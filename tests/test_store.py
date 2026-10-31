from pathlib import Path

import pytest

from chatgpt_bridge import store
from chatgpt_bridge.store import (
    Attachment,
    ChatRef,
    Turn,
)


@pytest.fixture
def conn(tmp_path: Path):
    connection = store.connect(tmp_path / "bridge.db")
    yield connection
    connection.close()


def make_chat(conn, chat_id="c1", title="First chat", section="recent", rank=0) -> None:
    store.upsert_listed_chats(
        conn,
        [
            ChatRef(
                id=chat_id,
                title=title,
                url=f"https://chatgpt.com/c/{chat_id}",
                section=section,
                sidebar_rank=rank,
            )
        ],
        seen_at=store.utc_now(),
    )


def test_connect_creates_schema(tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "bridge.db")
    assert store.schema_version(conn) == store.SCHEMA_VERSION
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"chats", "turns", "attachments", "sync_runs", "meta"} <= tables
    conn.close()


def test_db_file_is_user_only(tmp_path: Path) -> None:
    path = tmp_path / "bridge.db"
    conn = store.connect(path)
    conn.close()
    assert path.stat().st_mode & 0o077 == 0


def test_reopen_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "bridge.db"
    store.connect(path).close()
    conn = store.connect(path)
    assert store.schema_version(conn) == store.SCHEMA_VERSION
    conn.close()


def test_newer_schema_refused(tmp_path: Path) -> None:
    path = tmp_path / "bridge.db"
    conn = store.connect(path)
    conn.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(store.NewerSchemaError, match="newer"):
        store.connect(path)


def test_backup_created_when_migrating(tmp_path: Path) -> None:
    path = tmp_path / "bridge.db"
    conn = store.connect(path)
    target = store._backup(conn, path, store.SCHEMA_VERSION)
    conn.close()
    assert target == tmp_path / f"bridge.db.bak-{store.SCHEMA_VERSION}"
    assert target.exists()


def test_migration_runs_once_from_empty(tmp_path: Path) -> None:
    path = tmp_path / "bridge.db"
    store.connect(path).close()
    assert not list(tmp_path.glob("bridge.db.bak-*"))


def test_drop_migration_backup(tmp_path: Path) -> None:
    path = tmp_path / "bridge.db"
    backup = tmp_path / "bridge.db.bak-1"
    backup.write_text("x", encoding="utf-8")
    store.drop_migration_backup(path)
    assert not backup.exists()


def test_upsert_listed_chats_is_idempotent(conn) -> None:
    make_chat(conn)
    make_chat(conn, title="Renamed")
    rows = store.chat_summaries(conn)
    assert len(rows) == 1
    assert rows[0].title == "Renamed"


def test_upsert_does_not_clear_fetched_content(conn) -> None:
    make_chat(conn)
    turns = [Turn(role="user", markdown="hi", text="hi")]
    store.save_chat(conn, "c1", turns, store.content_hash(turns))
    make_chat(conn, title="Renamed after fetch")
    chat = store.get_chat(conn, "c1")
    assert chat is not None
    assert chat.turns and chat.turns[0].text == "hi"
    assert chat.fetched_at is not None


def test_stale_marking_and_recovery(conn) -> None:
    make_chat(conn, "c1")
    make_chat(conn, "c2")
    later = "2099-01-01T00:00:00+00:00"
    stale = store.mark_missing_stale(conn, later)
    assert set(stale) == {"c1", "c2"}
    assert store.get_chat(conn, "c1").stale  # type: ignore[union-attr]

    store.upsert_listed_chats(
        conn,
        [ChatRef(id="c1", title="First chat", url="https://chatgpt.com/c/c1", section="recent")],
        seen_at="2099-01-02T00:00:00+00:00",
    )
    assert not store.get_chat(conn, "c1").stale  # type: ignore[union-attr]
    assert store.get_chat(conn, "c2").stale  # type: ignore[union-attr]
    assert len(store.chat_summaries(conn)) == 2


def test_stale_filter(conn) -> None:
    make_chat(conn, "c1")
    make_chat(conn, "c2")
    store.mark_missing_stale(conn, "2099-01-01T00:00:00+00:00")
    assert [c.id for c in store.chat_summaries(conn, stale=True)] == ["c1", "c2"]
    assert store.chat_summaries(conn, stale=False) == []


def test_save_chat_replaces_turns_and_sets_metadata(conn) -> None:
    make_chat(conn)
    first = [
        Turn(role="user", markdown="a", text="a"),
        Turn(role="assistant", markdown="b", text="b"),
    ]
    store.save_chat(conn, "c1", first, store.content_hash(first))
    second = [Turn(role="user", markdown="a", text="a")]
    store.save_chat(conn, "c1", second, store.content_hash(second))

    chat = store.get_chat(conn, "c1")
    assert chat is not None
    assert chat.turn_count == 1
    assert len(chat.turns) == 1
    assert chat.content_hash == store.content_hash(second)
    assert store.counts(conn).turns == 1


def test_attachments_round_trip(conn) -> None:
    make_chat(conn)
    turns = [
        Turn(
            role="user",
            markdown="see file",
            text="see file",
            attachments=(Attachment(name="a.pdf", mime="application/pdf", kind="file"),),
        )
    ]
    store.save_chat(conn, "c1", turns, store.content_hash(turns))
    chat = store.get_chat(conn, "c1")
    assert chat is not None
    assert chat.turns[0].attachments == (
        Attachment(name="a.pdf", mime="application/pdf", kind="file"),
    )


def test_alternatives_preserved(conn) -> None:
    make_chat(conn)
    turns = [Turn(role="assistant", markdown="x", text="x", alternatives=3)]
    store.save_chat(conn, "c1", turns, store.content_hash(turns))
    chat = store.get_chat(conn, "c1")
    assert chat is not None
    assert chat.turns[0].alternatives == 3


def test_content_hash_ignores_positions_but_tracks_formatting() -> None:
    a = [Turn(role="user", markdown="hello", text="hello")]
    b = [Turn(role="user", markdown="hello", text="different plain text")]
    c = [Turn(role="user", markdown="hello **world**", text="hello world")]
    assert store.content_hash(a) == store.content_hash(b)
    assert store.content_hash(a) != store.content_hash(c)


def test_chats_needing_fetch_order_and_parse_version(conn) -> None:
    make_chat(conn, "c1", rank=1)
    make_chat(conn, "c2", rank=0)
    assert store.chats_needing_fetch(conn) == ["c2", "c1"]

    turns = [Turn(role="user", markdown="x", text="x")]
    store.save_chat(conn, "c2", turns, store.content_hash(turns))
    assert store.chats_needing_fetch(conn) == ["c1"]

    conn.execute("UPDATE chats SET parse_version = 0 WHERE id = 'c2'")
    assert store.chats_needing_fetch(conn) == ["c2", "c1"]
    assert store.chats_needing_fetch(conn, limit=1) == ["c2"]


def test_search_finds_text_and_returns_snippet(conn) -> None:
    make_chat(conn, "c1", title="Fedora notes")
    turns = [
        Turn(
            role="user",
            markdown="How do I install pkg-config?",
            text="How do I install pkg-config?",
        ),
        Turn(
            role="assistant",
            markdown="Use dnf install pkgconf-pkg-config.",
            text="Use dnf install pkgconf-pkg-config.",
        ),
    ]
    store.save_chat(conn, "c1", turns, store.content_hash(turns))

    hits = store.search(conn, "pkg-config")
    assert len(hits) == 2
    assert hits[0].chat_id == "c1"
    assert hits[0].title == "Fedora notes"
    assert "pkg" in hits[0].snippet


def test_search_is_case_insensitive(conn) -> None:
    make_chat(conn)
    turns = [Turn(role="user", markdown="Wayland compositor", text="Wayland compositor")]
    store.save_chat(conn, "c1", turns, store.content_hash(turns))
    assert store.search(conn, "WAYLAND")


def test_search_handles_substrings(conn) -> None:
    make_chat(conn)
    turns = [Turn(role="user", markdown="useEffect cleanup", text="useEffect cleanup")]
    store.save_chat(conn, "c1", turns, store.content_hash(turns))
    assert store.search(conn, "Effect")  # trigram substring match


def test_search_short_query_uses_like(conn) -> None:
    make_chat(conn)
    turns = [Turn(role="user", markdown="an ox", text="an ox")]
    store.save_chat(conn, "c1", turns, store.content_hash(turns))
    hits = store.search(conn, "ox")
    assert len(hits) == 1
    assert "ox" in hits[0].snippet


def test_search_scopes(conn) -> None:
    make_chat(conn, "c1", section="pinned")
    make_chat(conn, "c2", section="recent")
    turns = [Turn(role="user", markdown="docker compose", text="docker compose")]
    store.save_chat(conn, "c1", turns, store.content_hash(turns))
    store.save_chat(conn, "c2", turns, store.content_hash(turns))

    assert len(store.search(conn, "docker")) == 2
    assert len(store.search(conn, "docker", chat_id="c2")) == 1
    assert len(store.search(conn, "docker", section="pinned")) == 1
    assert store.search(conn, "docker", since="2099-01-01") == []


def test_search_filters_syntax_characters(conn) -> None:
    make_chat(conn)
    turns = [Turn(role="user", markdown="what is a*b?", text="what is a*b?")]
    store.save_chat(conn, "c1", turns, store.content_hash(turns))
    assert store.search(conn, "a*b")
    assert store.search(conn, "NOT OR AND") == []


def test_empty_query_returns_nothing(conn) -> None:
    assert store.search(conn, "   ") == []


def test_fts_tracks_updates_and_deletes(conn) -> None:
    make_chat(conn)
    first = [Turn(role="user", markdown="alpha", text="alpha")]
    store.save_chat(conn, "c1", first, store.content_hash(first))
    assert store.search(conn, "alpha")
    second = [Turn(role="user", markdown="beta", text="beta")]
    store.save_chat(conn, "c1", second, store.content_hash(second))
    assert store.search(conn, "alpha") == []
    assert store.search(conn, "beta")


def test_deleting_chat_cascades(conn) -> None:
    make_chat(conn)
    turns = [Turn(role="user", markdown="x", text="x")]
    store.save_chat(conn, "c1", turns, store.content_hash(turns))
    conn.execute("DELETE FROM chats WHERE id = 'c1'")
    assert store.counts(conn).turns == 0


def test_sync_runs_recorded(conn) -> None:
    run_id = store.save_sync_run(conn, "full", "running", {"seen": 3}, store.utc_now())
    store.update_sync_run(conn, run_id, "ok", {"seen": 3, "fetched": 3})
    row = conn.execute("SELECT * FROM sync_runs WHERE id = ?", (run_id,)).fetchone()
    assert row["status"] == "ok"
    assert row["finished_at"] is not None


def test_meta_round_trip(conn) -> None:
    store.set_meta(conn, "last_full_sync", "2026-01-01T00:00:00+00:00")
    store.set_meta(conn, "last_full_sync", "2026-01-02T00:00:00+00:00")
    assert store.get_meta(conn, "last_full_sync") == "2026-01-02T00:00:00+00:00"
    assert store.counts(conn).last_full_sync == "2026-01-02T00:00:00+00:00"


def test_counts(conn) -> None:
    make_chat(conn, "c1")
    make_chat(conn, "c2")
    turns = [Turn(role="user", markdown="x", text="x")]
    store.save_chat(conn, "c1", turns, store.content_hash(turns))
    c = store.counts(conn)
    assert (c.chats, c.fetched, c.turns, c.stale) == (2, 1, 1, 0)


def test_delete_all_clears_tables(conn) -> None:
    make_chat(conn)
    turns = [Turn(role="user", markdown="x", text="x")]
    store.save_chat(conn, "c1", turns, store.content_hash(turns))
    store.set_meta(conn, "k", "v")
    store.delete_all(conn)
    c = store.counts(conn)
    assert (c.chats, c.turns) == (0, 0)
    assert store.get_meta(conn, "k") is None


def test_chat_exists(conn) -> None:
    make_chat(conn, "c1")
    assert store.chat_exists(conn, "c1")
    assert not store.chat_exists(conn, "nope")


def test_get_chat_missing_returns_none(conn) -> None:
    assert store.get_chat(conn, "nope") is None
