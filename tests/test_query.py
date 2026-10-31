from __future__ import annotations

import json
from pathlib import Path

import pytest

from chatgpt_bridge import maintenance, query, store
from chatgpt_bridge.store import Turn


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "bridge.db"
    conn = store.connect(path)
    store.upsert_listed_chats(
        conn,
        [
            store.ChatRef(
                id="c1", title="Fedora notes", url="u1", section="recents", sidebar_rank=0
            ),
            store.ChatRef(id="c2", title="Other", url="u2", section="pinned", sidebar_rank=1),
        ],
        seen_at=store.utc_now(),
    )
    turns = [
        Turn(
            role="user",
            markdown="how do I **install** pkg-config?",
            text="how do I install pkg-config?",
        ),
        Turn(
            role="assistant",
            markdown="`dnf install pkgconf`",
            text="dnf install pkgconf",
            alternatives=2,
        ),
    ]
    store.save_chat(conn, "c1", turns, store.content_hash(turns))
    conn.close()
    return path


def test_list_chats_filters(db: Path) -> None:
    assert len(query.list_chats(db_path=db)) == 2
    assert [c.id for c in query.list_chats(db_path=db, section="pinned")] == ["c2"]
    assert [c.id for c in query.list_chats(db_path=db, unfetched=True)] == ["c2"]
    assert query.list_chats(db_path=db, limit=1)


def test_get_chat_returns_turns(db: Path) -> None:
    chat = query.get_chat("c1", db_path=db)
    assert chat.title == "Fedora notes"
    assert len(chat.turns) == 2


def test_get_chat_missing(db: Path) -> None:
    with pytest.raises(query.NotFound, match="not in the cache"):
        query.get_chat("nope", db_path=db)


def test_get_chat_unfetched(db: Path) -> None:
    with pytest.raises(query.NotFound, match="not fetched"):
        query.get_chat("c2", db_path=db)


def test_search_returns_hits(db: Path) -> None:
    hits = query.search("pkgconf", db_path=db)
    assert len(hits) == 1
    assert hits[0].chat_id == "c1"


def test_search_limit_is_capped(db: Path) -> None:
    hits = query.search("do", db_path=db, limit=10_000)
    assert isinstance(hits, list)


def test_counts_and_last_run(db: Path) -> None:
    counts = query.counts(db_path=db)
    assert counts.chats == 2
    assert query.last_run(db_path=db) is None


def test_render_text_and_markdown(db: Path) -> None:
    chat = query.get_chat("c1", db_path=db)
    text = query.render_text(chat)
    assert "Fedora notes" in text and "[1] You" in text and "[2] ChatGPT" in text
    markdown = query.render_markdown(chat)
    assert markdown.startswith("# Fedora notes")
    assert "## You" in markdown and "## ChatGPT" in markdown


def test_envelope_shape(db: Path) -> None:
    chat = query.get_chat("c1", db_path=db)
    payload = query.envelope(chat).as_dict()
    assert payload["id"] == "c1"
    turns = payload["turns"]
    assert isinstance(turns, list)
    assert turns[1]["alternatives"] == 2
    assert turns[0]["markdown"].startswith("how do I")


def test_envelope_can_omit_markdown(db: Path) -> None:
    payload = query.envelope(query.get_chat("c1", db_path=db), include_markdown=False).as_dict()
    assert "markdown" not in payload["turns"][0]


def test_export_raw_writes_jsonl(db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHATGPT_BRIDGE_DATA_DIR", str(tmp_path))
    out = tmp_path / "dump.jsonl"
    target = maintenance.export_raw(out)
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert set(first) >= {"id", "title", "turns"}


def test_reset_clears_cache(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHATGPT_BRIDGE_DATA_DIR", str(db.parent))
    maintenance.reset()
    assert query.counts(db_path=db).chats == 0
