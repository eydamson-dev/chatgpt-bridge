from __future__ import annotations

from pathlib import Path

import pytest

from chatgpt_bridge import store
from chatgpt_bridge.store import Attachment, Turn


@pytest.fixture
def conn(tmp_path: Path):
    connection = store.connect(tmp_path / "bridge.db")
    yield connection
    connection.close()


def seed(
    conn, chat_id: str = "c1", title: str = "First chat", section: str | None = "recents"
) -> None:
    turns = [Turn(role="user", markdown="hi", text="hi")]
    store.save_chat(
        conn,
        chat_id=chat_id,
        title=title,
        url=f"https://chatgpt.com/c/{chat_id}",
        section=section,
        turns=turns,
        hash_value=store.content_hash(turns),
    )


def test_connect_creates_current_schema(tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "bridge.db")
    assert store.schema_version(conn) == store.SCHEMA_VERSION == 4
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"chats", "turns", "attachments", "fetch_log", "send_log", "meta"} <= tables
    assert "sync_runs" not in tables
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(chats)")}
    assert {"project", "project_id"} <= columns
    conn.close()


def test_db_file_is_user_only(tmp_path: Path) -> None:
    path = tmp_path / "bridge.db"
    store.connect(path).close()
    assert path.stat().st_mode & 0o077 == 0


def test_unusable_database_path_reports_store_error(tmp_path: Path) -> None:
    with pytest.raises(store.StoreError, match="cannot open database"):
        store.connect(tmp_path)  # a directory, not a database file


def test_newer_schema_refused(tmp_path: Path) -> None:
    path = tmp_path / "bridge.db"
    conn = store.connect(path)
    conn.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(store.NewerSchemaError, match="newer"):
        store.connect(path)


def test_v1_database_is_migrated(tmp_path: Path) -> None:
    """A schema-1 database (the old backfill model) is rebuilt as the current schema."""
    path = tmp_path / "bridge.db"
    conn = store.connect(path)
    conn.execute("PRAGMA user_version = 1")
    conn.close()

    conn = store.connect(path)
    assert store.schema_version(conn) == store.SCHEMA_VERSION
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "sync_runs" not in tables
    assert "send_log" in tables
    conn.close()
    assert (tmp_path / "bridge.db.bak-1").exists()


def test_v2_database_is_migrated_to_current(tmp_path: Path) -> None:
    path = tmp_path / "bridge.db"
    conn = store.connect(path)
    # Wind the schema back to v2: no send_log and no project columns.
    conn.execute("DROP TABLE send_log")
    conn.execute("DROP INDEX IF EXISTS idx_chats_project")
    conn.execute("ALTER TABLE chats DROP COLUMN project")
    conn.execute("ALTER TABLE chats DROP COLUMN project_id")
    conn.execute("PRAGMA user_version = 2")
    conn.close()

    conn = store.connect(path)
    assert store.schema_version(conn) == store.SCHEMA_VERSION == 4
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "send_log" in tables
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(chats)")}
    assert {"project", "project_id"} <= columns
    conn.close()
    assert (tmp_path / "bridge.db.bak-2").exists()


def test_drop_migration_backup(tmp_path: Path) -> None:
    backup = tmp_path / "bridge.db.bak-1"
    backup.write_text("x", encoding="utf-8")
    store.drop_migration_backup(tmp_path / "bridge.db")
    assert not backup.exists()


def test_save_chat_stores_metadata_and_turns(conn) -> None:
    seed(conn)
    chat = store.get_chat(conn, "c1")
    assert chat is not None
    assert chat.title == "First chat"
    assert chat.url == "https://chatgpt.com/c/c1"
    assert chat.fetched_at
    assert chat.turn_count == 1
    assert chat.turns[0].text == "hi"


def test_save_chat_replaces_previous_copy(conn) -> None:
    seed(conn)
    turns = [
        Turn(role="user", markdown="a", text="a"),
        Turn(role="assistant", markdown="b", text="b"),
    ]
    store.save_chat(
        conn,
        chat_id="c1",
        title="Renamed",
        url="https://chatgpt.com/c/c1",
        turns=turns,
        hash_value=store.content_hash(turns),
    )
    chat = store.get_chat(conn, "c1")
    assert chat is not None
    assert chat.title == "Renamed"
    assert len(chat.turns) == 2
    assert store.counts(conn).turns == 2


def test_attachments_round_trip(conn) -> None:
    turns = [
        Turn(
            role="user",
            markdown="see file",
            text="see file",
            attachments=(Attachment(name="a.pdf", mime="application/pdf", kind="file"),),
        )
    ]
    store.save_chat(
        conn,
        chat_id="c1",
        title="t",
        url="u",
        turns=turns,
        hash_value=store.content_hash(turns),
    )
    chat = store.get_chat(conn, "c1")
    assert chat is not None
    assert chat.turns[0].attachments == (
        Attachment(name="a.pdf", mime="application/pdf", kind="file"),
    )


def test_alternatives_preserved(conn) -> None:
    turns = [Turn(role="user", markdown="x", text="x", alternatives=3)]
    store.save_chat(conn, "c1", "t", "u", turns, store.content_hash(turns))
    chat = store.get_chat(conn, "c1")
    assert chat is not None
    assert chat.turns[0].alternatives == 3


def test_content_hash_tracks_markdown_not_plain_text() -> None:
    a = [Turn(role="user", markdown="hello", text="hello")]
    b = [Turn(role="user", markdown="hello", text="different")]
    c = [Turn(role="user", markdown="hello **world**", text="hello world")]
    assert store.content_hash(a) == store.content_hash(b)
    assert store.content_hash(a) != store.content_hash(c)


def test_resolve_chat_id_exact_and_prefix(conn) -> None:
    seed(conn, "aaaa1111-2222", "one")
    assert store.resolve_chat_id(conn, "aaaa1111-2222") == "aaaa1111-2222"
    assert store.resolve_chat_id(conn, "aaaa") == "aaaa1111-2222"
    assert store.resolve_chat_id(conn, "zzz") is None


def test_resolve_chat_id_ambiguous(conn) -> None:
    seed(conn, "aaaa1111", "one")
    seed(conn, "aaaa2222", "two")
    with pytest.raises(store.AmbiguousChatId):
        store.resolve_chat_id(conn, "aaaa")


def test_find_by_title(conn) -> None:
    seed(conn, "c1", "Sample hardware review")
    seed(conn, "c2", "Fedora notes")
    assert [c.id for c in store.find_by_title(conn, "fedora notes")] == ["c2"]
    assert [c.id for c in store.find_by_title(conn, "Sample")] == ["c1"]
    assert store.find_by_title(conn, "nothing") == []


def test_find_by_title_treats_wildcards_literally(conn) -> None:
    seed(conn, "c1", "100% Fedora")
    seed(conn, "c2", "other")
    assert [c.id for c in store.find_by_title(conn, "%")] == ["c1"]
    assert store.find_by_title(conn, "_") == []


def test_forget_chat(conn) -> None:
    seed(conn, "c1")
    assert store.forget_chat(conn, "c1") is True
    assert store.forget_chat(conn, "c1") is False
    assert store.counts(conn).chats == 0


def test_send_log_records_and_clears(conn) -> None:
    store.record_send(conn, "c1", "sent", sent_at="2026-01-01T00:00:00+00:00")
    entry = store.last_send(conn)
    assert entry is not None
    assert (entry.chat_id, entry.status, entry.detail) == ("c1", "sent", None)
    store.delete_all(conn)
    assert store.last_send(conn) is None


def test_fetch_log_records_and_reports_last(conn) -> None:
    store.record_fetch(conn, "c1", "ok", fetched_at="2026-01-01T00:00:00+00:00")
    store.record_fetch(conn, "c2", "failed", "boom", fetched_at="2026-01-02T00:00:00+00:00")
    entry = store.last_fetch(conn)
    assert entry is not None
    assert (entry.chat_id, entry.status, entry.detail) == ("c2", "failed", "boom")
    assert store.counts(conn).last_fetch == "2026-01-02T00:00:00+00:00"


def test_search_finds_text_case_insensitively(conn) -> None:
    turns = [Turn(role="user", markdown="pkg-config", text="install pkg-config")]
    store.save_chat(conn, "c1", "Fedora notes", "u", turns, store.content_hash(turns))
    hits = store.search(conn, "PKG-CONFIG")
    assert len(hits) == 1
    assert hits[0].title == "Fedora notes"
    assert "pkg-config" in hits[0].snippet


def test_search_short_query_uses_like(conn) -> None:
    turns = [Turn(role="user", markdown="an ox", text="an ox")]
    store.save_chat(conn, "c1", "t", "u", turns, store.content_hash(turns))
    assert len(store.search(conn, "ox")) == 1


def test_search_mixed_short_terms_uses_like(conn) -> None:
    turns = [Turn(role="user", markdown="go to the store", text="go to the store")]
    store.save_chat(conn, "c1", "t", "u", turns, store.content_hash(turns))
    assert len(store.search(conn, "go store")) == 1
    assert len(store.search(conn, "100%")) == 0


def test_search_scoped_to_chat(conn) -> None:
    turns = [Turn(role="user", markdown="docker compose", text="docker compose")]
    store.save_chat(conn, "c1", "a", "u", turns, store.content_hash(turns))
    store.save_chat(conn, "c2", "b", "u", turns, store.content_hash(turns))
    assert len(store.search(conn, "docker")) == 2
    assert len(store.search(conn, "docker", chat_id="c2")) == 1


def test_search_filters_fts_syntax(conn) -> None:
    turns = [Turn(role="user", markdown="what is a*b?", text="what is a*b?")]
    store.save_chat(conn, "c1", "t", "u", turns, store.content_hash(turns))
    assert store.search(conn, "a*b")
    assert store.search(conn, "NOT OR AND") == []


def test_fts_tracks_updates(conn) -> None:
    first = [Turn(role="user", markdown="alpha", text="alpha")]
    store.save_chat(conn, "c1", "t", "u", first, store.content_hash(first))
    assert store.search(conn, "alpha")
    second = [Turn(role="user", markdown="beta", text="beta")]
    store.save_chat(conn, "c1", "t", "u", second, store.content_hash(second))
    assert store.search(conn, "alpha") == []
    assert store.search(conn, "beta")


def test_snippet_includes_context(conn) -> None:
    text = "x" * 100 + " needle " + "y" * 100
    turns = [Turn(role="user", markdown=text, text=text)]
    store.save_chat(conn, "c1", "t", "u", turns, store.content_hash(turns))
    hit = store.search(conn, "needle")[0]
    assert "needle" in hit.snippet
    assert hit.snippet.startswith("…")
    assert len(hit.snippet) < len(text)


def test_delete_all(conn) -> None:
    seed(conn)
    store.record_fetch(conn, "c1", "ok")
    store.delete_all(conn)
    counts = store.counts(conn)
    assert (counts.chats, counts.turns, counts.last_fetch) == (0, 0, None)


def test_missing_chat_returns_none(conn) -> None:
    assert store.get_chat(conn, "nope") is None


# ---------------------------------------------------------------- projects


def test_save_chat_records_project(tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "bridge.db")
    turns = [Turn(role="user", markdown="hi", text="hi")]
    store.save_chat(
        conn,
        chat_id="c1",
        title="Plan",
        url="https://chatgpt.com/g/g-p-abc/c/c1",
        turns=turns,
        hash_value=store.content_hash(turns),
        project="Test project",
        project_id="g-p-abc",
    )
    summary = store.chat_summaries(conn)[0]
    assert (summary.project, summary.project_id) == ("Test project", "g-p-abc")
    chat = store.get_chat(conn, "c1")
    assert chat is not None
    assert (chat.project, chat.project_id) == ("Test project", "g-p-abc")
    conn.close()


def test_resave_without_project_keeps_the_membership(tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "bridge.db")
    turns = [Turn(role="user", markdown="hi", text="hi")]
    store.save_chat(
        conn,
        chat_id="c1",
        title="Plan",
        url="u",
        turns=turns,
        hash_value="h1",
        project="Test project",
        project_id="g-p-abc",
    )
    store.save_chat(conn, chat_id="c1", title="Plan", url="u", turns=turns, hash_value="h2")
    summary = store.chat_summaries(conn)[0]
    assert summary.project == "Test project"
    assert summary.project_id == "g-p-abc"
    conn.close()


def test_chat_summaries_filter_by_project(conn) -> None:
    turns = [Turn(role="user", markdown="hi", text="hi")]
    store.save_chat(
        conn,
        chat_id="c1",
        title="In project",
        url="u",
        turns=turns,
        hash_value="h",
        project="Test project",
        project_id="g-p-abc",
    )
    seed(conn, chat_id="c2", title="Elsewhere")
    assert [s.id for s in store.chat_summaries_in_project(conn, "Test project")] == ["c1"]
    assert [s.id for s in store.chat_summaries(conn, project_id="g-p-abc")] == ["c1"]
    assert len(store.chat_summaries(conn)) == 2


def test_search_can_be_scoped_to_a_project(conn) -> None:
    turns = [Turn(role="user", markdown="kubernetes notes", text="kubernetes notes")]
    store.save_chat(
        conn,
        chat_id="c1",
        title="In project",
        url="u",
        turns=turns,
        hash_value="h",
        project="Test project",
        project_id="g-p-abc",
    )
    seed(conn, chat_id="c2", title="Elsewhere")
    assert store.search(conn, "kubernetes", project="Test project")
    assert store.search(conn, "kubernetes", project="Other project") == []
