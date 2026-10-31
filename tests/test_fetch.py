from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from chatgpt_bridge import store
from chatgpt_bridge.adapters.chatgpt import ExtractedChat
from chatgpt_bridge.fetch import fetch_chat
from chatgpt_bridge.parse import ParsedTurn
from chatgpt_bridge.resolve import AmbiguousTarget, TargetNotFound
from chatgpt_bridge.store import ChatRef

UUID = "6ae5de7d-e0f8-83ec-9a71-cd2780e3bf72"


def extracted(chat_id: str, text: str = "hi", title: str = "Chat") -> ExtractedChat:
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
    def __init__(self, refs: list[ChatRef], body: str = "hi") -> None:
        self.refs = refs
        self.body = body
        self.fetched: list[str] = []
        self.list_calls = 0

    def list_chats(self, scroll: bool = False, limit: int | None = None) -> list[ChatRef]:
        self.list_calls += 1
        return self.refs[:limit] if limit else self.refs

    def fetch_chat(self, url_or_id: str) -> ExtractedChat:
        self.fetched.append(url_or_id)
        return extracted(url_or_id, self.body)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "bridge.db"


def session_for(source: FakeSource):
    @contextmanager
    def _session():
        yield source

    return _session


def test_fetch_by_title_uses_sidebar_then_stores(db: Path) -> None:
    source = FakeSource([ChatRef(id=UUID, title="Lenovo ThinkPad Review", url="u")])
    result = fetch_chat("Lenovo ThinkPad Review", db_path=db, session=session_for(source))

    assert result.outcome == "new"
    assert result.chat.id == UUID
    assert source.fetched == [UUID]
    assert source.list_calls == 1

    conn = store.connect(db)
    assert store.counts(conn).chats == 1
    conn.close()


def test_fetch_by_url_skips_sidebar(db: Path) -> None:
    source = FakeSource([])
    result = fetch_chat(f"https://chatgpt.com/c/{UUID}", db_path=db, session=session_for(source))
    assert result.chat.id == UUID
    assert source.list_calls == 0


def test_cached_chat_is_served_without_browser(db: Path) -> None:
    first = FakeSource([ChatRef(id=UUID, title="Chat", url="u")])
    fetch_chat(UUID, db_path=db, session=session_for(first))

    result = fetch_chat(UUID, db_path=db, session=None)
    assert result.outcome == "cached"
    assert result.fetched is False
    assert first.fetched == [UUID]


def test_refresh_refetches_and_reports_unchanged(db: Path) -> None:
    source = FakeSource([ChatRef(id=UUID, title="Chat", url="u")])
    fetch_chat(UUID, db_path=db, session=session_for(source))

    refreshed = FakeSource([ChatRef(id=UUID, title="Chat", url="u")])
    result = fetch_chat(UUID, refresh=True, db_path=db, session=session_for(refreshed))
    assert result.outcome == "unchanged"
    assert result.changed is False
    assert refreshed.fetched == [UUID]


def test_refresh_detects_changed_content(db: Path) -> None:
    fetch_chat(UUID, db_path=db, session=session_for(FakeSource([], body="before")))
    changed = FakeSource([], body="after")
    result = fetch_chat(UUID, refresh=True, db_path=db, session=session_for(changed))
    assert result.outcome == "refreshed"
    assert result.changed is True
    assert result.chat.turns[0].text == "after"


def test_uncached_target_without_session_raises(db: Path) -> None:
    with pytest.raises(TargetNotFound, match="no browser session"):
        fetch_chat("Some title", db_path=db, session=None)


def test_ambiguous_title_raises_with_candidates(db: Path) -> None:
    source = FakeSource(
        [
            ChatRef(id="c1", title="Lenovo T14", url="u"),
            ChatRef(id="c2", title="Lenovo T14s", url="u"),
        ]
    )
    with pytest.raises(AmbiguousTarget) as excinfo:
        fetch_chat("Lenovo", db_path=db, session=session_for(source))
    assert len(excinfo.value.candidates) == 2
    assert source.fetched == []


def test_lookup_limit_is_passed(db: Path) -> None:
    refs = [ChatRef(id=f"c{i}", title=f"Chat {i}", url="u") for i in range(5)]
    source = FakeSource(refs)
    with pytest.raises(TargetNotFound):
        fetch_chat("Chat 4", db_path=db, session=session_for(source), lookup_limit=2)
    assert source.fetched == []


def test_fetch_log_records_outcome(db: Path) -> None:
    fetch_chat(UUID, db_path=db, session=session_for(FakeSource([], body="x")))
    conn = store.connect(db)
    entry = store.last_fetch(conn)
    assert entry is not None
    assert entry.chat_id == UUID
    assert entry.status == "ok"
    conn.close()


def test_failure_stores_nothing(db: Path) -> None:
    class Boom(FakeSource):
        def fetch_chat(self, url_or_id: str) -> ExtractedChat:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        fetch_chat(UUID, db_path=db, session=session_for(Boom([])))

    conn = store.connect(db)
    assert store.counts(conn).chats == 0
    conn.close()
