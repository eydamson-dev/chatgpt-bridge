from __future__ import annotations

import random
from pathlib import Path

import pytest

from chatgpt_bridge import store
from chatgpt_bridge.adapters.chatgpt import ExtractedChat
from chatgpt_bridge.parse import ParseDriftError, ParsedTurn
from chatgpt_bridge.store import ChatRef
from chatgpt_bridge.sync import SyncAborted, SyncOptions, run_sync


def ref(chat_id: str, title: str = "Chat", rank: int = 0, section: str = "recents") -> ChatRef:
    return ChatRef(
        id=chat_id,
        title=title,
        url=f"https://chatgpt.com/c/{chat_id}",
        section=section,
        sidebar_rank=rank,
    )


def chat(chat_id: str, text: str = "hi", title: str = "Chat") -> ExtractedChat:
    return ExtractedChat(
        id=chat_id,
        title=title,
        url=f"https://chatgpt.com/c/{chat_id}",
        turns=(
            ParsedTurn(role="user", markdown=text, text=text),
            ParsedTurn(role="assistant", markdown=f"re: {text}", text=f"re: {text}"),
        ),
    )


class FakeSource:
    def __init__(self, chats: list[ChatRef], bodies: dict[str, str] | None = None) -> None:
        self.chats = chats
        self.bodies = bodies or {}
        self.fetched: list[str] = []
        self.fail: dict[str, Exception] = {}

    def list_chats(self, scroll: bool = True) -> list[ChatRef]:
        return list(self.chats)

    def fetch_chat(self, url_or_id: str) -> ExtractedChat:
        chat_id = url_or_id.rsplit("/", 1)[-1]
        self.fetched.append(chat_id)
        if chat_id in self.fail:
            raise self.fail[chat_id]
        return chat(chat_id, self.bodies.get(chat_id, "hi"), title="Chat")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "bridge.db"


def run(source: FakeSource, db: Path, **kwargs) -> object:
    options = SyncOptions(min_delay_s=0, max_delay_s=0, **kwargs)
    return run_sync(source, options, db_path=db, sleep=lambda _s: None, rng=random.Random(0))


def test_full_sync_fetches_listed_chats(db: Path) -> None:
    source = FakeSource([ref("a", rank=0), ref("b", rank=1)])
    result = run(source, db, full=True)
    assert (result.status, result.listed, result.fetched, result.new) == ("ok", 2, 2, 2)  # type: ignore[attr-defined]
    assert source.fetched == ["a", "b"]

    conn = store.connect(db)
    assert store.counts(conn).fetched == 2
    assert store.counts(conn).last_full_sync is not None
    conn.close()


def test_full_sync_skips_already_fetched(db: Path) -> None:
    source = FakeSource([ref("a")])
    run(source, db, full=True)
    source.fetched.clear()
    result = run(source, db, full=True)
    assert result.fetched == 0  # type: ignore[attr-defined]
    assert source.fetched == []


def test_full_sync_retries_unfetched(db: Path) -> None:
    conn = store.connect(db)
    store.upsert_listed_chats(conn, [ref("a")], seen_at=store.utc_now())
    conn.close()

    source = FakeSource([ref("a")])
    result = run(source, db, full=True)
    assert result.fetched == 1  # type: ignore[attr-defined]


def test_incremental_fetches_new_chats(db: Path) -> None:
    run(FakeSource([ref("a")]), db, full=True)
    source = FakeSource([ref("b", rank=0), ref("a", rank=1)])
    result = run(source, db)
    assert "b" in source.fetched
    assert result.new == 1  # type: ignore[attr-defined]


def test_incremental_catches_recent_updates(db: Path) -> None:
    run(FakeSource([ref("a", rank=0), ref("b", rank=1), ref("c", rank=2)]), db, full=True)
    source = FakeSource(
        [ref("c", rank=0), ref("a", rank=1), ref("b", rank=2)],
        bodies={"c": "updated text"},
    )
    result = run(source, db, recent_window=1)
    assert "c" in source.fetched
    assert result.updated == 1  # type: ignore[attr-defined]

    conn = store.connect(db)
    stored = store.get_chat(conn, "c")
    assert stored is not None and stored.turns[0].text == "updated text"
    conn.close()


def test_incremental_skips_unchanged_content(db: Path) -> None:
    run(FakeSource([ref("a")]), db, full=True)
    source = FakeSource([ref("a")])
    result = run(source, db)
    assert result.fetched == 1  # type: ignore[attr-defined]
    assert result.unchanged == 1  # type: ignore[attr-defined]


def test_incremental_refetches_retitled_chats(db: Path) -> None:
    run(FakeSource([ref("a", title="Old")]), db, full=True)
    source = FakeSource([ref("a", title="New")])
    run(source, db, recent_window=0)
    assert source.fetched == ["a"]

    conn = store.connect(db)
    summary = store.chat_summaries(conn)[0]
    assert summary.title == "New"
    conn.close()


def test_incremental_respects_recent_window(db: Path) -> None:
    refs = [ref(letter, rank=i) for i, letter in enumerate("abcdef")]
    run(FakeSource(refs), db, full=True)
    source = FakeSource(refs)
    run(source, db, recent_window=2)
    assert source.fetched == ["a", "b"]


def test_limit_caps_fetches(db: Path) -> None:
    source = FakeSource([ref("a", rank=0), ref("b", rank=1), ref("c", rank=2)])
    result = run(source, db, full=True, limit=2)
    assert result.fetched == 2  # type: ignore[attr-defined]
    assert source.fetched == ["a", "b"]


def test_partial_failure_continues(db: Path) -> None:
    source = FakeSource([ref("a", rank=0), ref("b", rank=1), ref("c", rank=2)])
    source.fail["b"] = RuntimeError("boom")
    result = run(source, db, full=True)
    assert result.status == "partial"  # type: ignore[attr-defined]
    assert result.fetched == 2  # type: ignore[attr-defined]
    assert any("b:" in e for e in result.errors)  # type: ignore[attr-defined]


def test_repeated_drift_aborts(db: Path) -> None:
    source = FakeSource([ref(x, rank=i) for i, x in enumerate("abcd")])
    for key in "abcd":
        source.fail[key] = ParseDriftError("layout changed")
    with pytest.raises(SyncAborted, match="consecutive parse failures"):
        run(source, db, full=True)

    conn = store.connect(db)
    row = conn.execute("SELECT status FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "failed"
    conn.close()


def test_stale_marking_on_full_sync(db: Path) -> None:
    run(FakeSource([ref("a"), ref("b", rank=1)]), db, full=True)
    run(FakeSource([ref("a")]), db, full=True)

    conn = store.connect(db)
    assert store.get_chat(conn, "b").stale  # type: ignore[union-attr]
    assert not store.get_chat(conn, "a").stale  # type: ignore[union-attr]
    conn.close()


def test_dry_run_does_not_write_turns(db: Path) -> None:
    source = FakeSource([ref("a")])
    result = run(source, db, full=True, dry_run=True)
    assert result.fetched == 1  # type: ignore[attr-defined]

    conn = store.connect(db)
    assert store.counts(conn).turns == 0
    conn.close()


def test_sync_run_recorded(db: Path) -> None:
    run(FakeSource([ref("a")]), db, full=True)
    conn = store.connect(db)
    row = conn.execute(
        "SELECT kind, status, summary FROM sync_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["kind"] == "full"
    assert row["status"] == "ok"
    assert '"fetched": 1' in row["summary"]
    conn.close()


def test_parse_version_bump_refetches(db: Path) -> None:
    run(FakeSource([ref("a")]), db, full=True)
    conn = store.connect(db)
    conn.execute("UPDATE chats SET parse_version = 0")
    conn.close()

    source = FakeSource([ref("a")])
    run(source, db, full=True)
    assert source.fetched == ["a"]


def test_progress_messages(db: Path) -> None:
    messages: list[str] = []
    run_sync(
        FakeSource([ref("a")]),
        SyncOptions(min_delay_s=0, max_delay_s=0, full=True),
        db_path=db,
        progress=messages.append,
        sleep=lambda _s: None,
    )
    assert any("listed 1 chats" in m for m in messages)
    assert any("done: ok" in m for m in messages)


def test_pacing_delay_is_used(db: Path) -> None:
    delays: list[float] = []
    run_sync(
        FakeSource([ref("a", rank=0), ref("b", rank=1)]),
        SyncOptions(min_delay_s=1.0, max_delay_s=2.0, full=True),
        db_path=db,
        sleep=delays.append,
        rng=random.Random(1),
    )
    assert len(delays) == 2
    assert all(1.0 <= d <= 2.0 for d in delays)
