from __future__ import annotations

import json
from pathlib import Path

import pytest

from chatgpt_bridge import maintenance, query, store

UUID = "6ae5de7d-e0f8-83ec-9a71-cd2780e3bf72"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "bridge.db"
    conn = store.connect(path)
    turns = [
        store.Turn(
            role="user",
            markdown="how do I **install** pkg-config?",
            text="how do I install pkg-config?",
        ),
        store.Turn(
            role="assistant",
            markdown="`dnf install pkgconf`",
            text="dnf install pkgconf",
            alternatives=2,
        ),
    ]
    store.save_chat(
        conn,
        chat_id=UUID,
        title="Fedora notes",
        url=f"https://chatgpt.com/c/{UUID}",
        section="recents",
        turns=turns,
        hash_value=store.content_hash(turns),
    )
    store.save_chat(
        conn,
        chat_id="aaaa1111-2222-3333-4444-555566667777",
        title="Lenovo ThinkPad Review",
        url="https://chatgpt.com/c/aaaa",
        section="recents",
        turns=[store.Turn(role="user", markdown="T14", text="T14")],
        hash_value="h",
    )
    store.record_fetch(conn, UUID, "ok")
    conn.close()
    return path


def test_list_cached(db: Path) -> None:
    assert len(query.list_cached(db_path=db)) == 2
    assert len(query.list_cached(db_path=db, limit=1)) == 1


def test_get_chat_by_id_prefix_and_title(db: Path) -> None:
    assert query.get_chat(UUID, db_path=db).title == "Fedora notes"
    assert query.get_chat("6ae5de7d", db_path=db).title == "Fedora notes"
    assert query.get_chat("fedora", db_path=db).id == UUID


def test_get_chat_missing_hints_at_fetch(db: Path) -> None:
    with pytest.raises(query.NotFound) as excinfo:
        query.get_chat("nothing", db_path=db)
    assert "chatgpt-bridge fetch" in str(excinfo.value)


def test_get_chat_ambiguous(db: Path) -> None:
    with pytest.raises(query.NotFound, match="matches several"):
        query.get_chat("a", db_path=db)


def test_search_hits(db: Path) -> None:
    hits = query.search("pkgconf", db_path=db)
    assert len(hits) == 1
    assert hits[0].chat_id == UUID


def test_search_scoped_by_title(db: Path) -> None:
    assert query.search("pkgconf", db_path=db, chat="fedora")
    assert query.search("pkgconf", db_path=db, chat="lenovo") == []


def test_search_scope_missing_chat(db: Path) -> None:
    with pytest.raises(query.NotFound):
        query.search("x", db_path=db, chat="nothing")


def test_counts_and_last_fetch(db: Path) -> None:
    counts = query.counts(db_path=db)
    assert (counts.chats, counts.turns) == (2, 3)
    entry = query.last_fetch(db_path=db)
    assert entry is not None and entry.chat_id == UUID


def test_forget(db: Path) -> None:
    removed = query.forget("fedora", db_path=db)
    assert removed == UUID
    assert query.counts(db_path=db).chats == 1


def test_forget_missing(db: Path) -> None:
    with pytest.raises(query.NotFound):
        query.forget("nothing", db_path=db)


def test_render_text_and_markdown(db: Path) -> None:
    chat = query.get_chat("fedora", db_path=db)
    text = query.render_text(chat)
    assert "Fedora notes" in text and "[1] You" in text and "[2] ChatGPT" in text
    markdown = query.render_markdown(chat)
    assert markdown.startswith("# Fedora notes")
    assert "## You" in markdown and "## ChatGPT" in markdown


def test_envelope_shape(db: Path) -> None:
    payload = query.envelope(query.get_chat("fedora", db_path=db)).as_dict()
    assert payload["id"] == UUID
    turns = payload["turns"]
    assert isinstance(turns, list)
    assert turns[1]["alternatives"] == 2
    assert turns[0]["markdown"].startswith("how do I")


def test_envelope_can_omit_markdown(db: Path) -> None:
    payload = query.envelope(query.get_chat("fedora", db_path=db), include_markdown=False).as_dict()
    assert "markdown" not in payload["turns"][0]


def test_export_raw_writes_jsonl(db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHATGPT_BRIDGE_DATA_DIR", str(db.parent))
    out = tmp_path / "dump.jsonl"
    target = maintenance.export_raw(out)
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert set(json.loads(lines[0])) >= {"id", "title", "turns"}


def test_reset_clears_cache(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHATGPT_BRIDGE_DATA_DIR", str(db.parent))
    maintenance.reset()
    assert query.counts(db_path=db).chats == 0
