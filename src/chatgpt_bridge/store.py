from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from chatgpt_bridge import paths

SCHEMA_VERSION = 1
DEFAULT_PARSE_VERSION = 1
Role = Literal["user", "assistant"]
RunKind = Literal["full", "incremental", "single"]
RunStatus = Literal["running", "ok", "partial", "failed", "aborted"]


class StoreError(RuntimeError):
    """Raised when the database cannot be opened or written."""


class NewerSchemaError(StoreError):
    """The database was written by a newer version of the app."""


@dataclass(frozen=True)
class ChatRef:
    """A chat as it appears in a listing, before its content is fetched."""

    id: str
    title: str
    url: str
    section: str | None = None
    sidebar_rank: int | None = None


@dataclass(frozen=True)
class Attachment:
    name: str | None
    mime: str | None
    kind: str | None


@dataclass(frozen=True)
class Turn:
    role: Role
    markdown: str
    text: str
    alternatives: int = 1
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True)
class StoredChat:
    id: str
    title: str
    url: str
    section: str | None
    sidebar_rank: int | None
    content_hash: str | None
    turn_count: int | None
    fetched_at: str | None
    last_seen_at: str
    stale: bool
    parse_version: int
    turns: tuple[Turn, ...]


@dataclass(frozen=True)
class ChatSummary:
    id: str
    title: str
    url: str
    section: str | None
    sidebar_rank: int | None
    turn_count: int | None
    fetched_at: str | None
    stale: bool


@dataclass(frozen=True)
class SearchHit:
    chat_id: str
    title: str
    position: int
    role: str
    snippet: str


@dataclass(frozen=True)
class Counts:
    chats: int
    fetched: int
    stale: int
    turns: int
    last_full_sync: str | None
    last_incremental_sync: str | None


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def content_hash(turns: Iterable[Turn]) -> str:
    """Hash normalized turn content, so formatting or text changes count as updates."""
    digest = hashlib.sha256()
    for turn in turns:
        digest.update(turn.role.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(turn.markdown.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(turn.alternatives).encode("utf-8"))
        digest.update(b"\x00")
        for att in turn.attachments:
            digest.update(repr((att.name, att.mime, att.kind)).encode("utf-8"))
            digest.update(b"\x00")
    return digest.hexdigest()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _restrict_permissions(path)
    migrate(conn, path)
    return conn


def _restrict_permissions(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def migrations_dir() -> Path:
    return Path(__file__).parent / "migrations"


def migrate(conn: sqlite3.Connection, db_path: Path) -> None:
    """Apply numbered migrations in order, backing up the database first."""
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current > SCHEMA_VERSION:
        raise NewerSchemaError(
            f"database schema version {current} is newer than this app supports "
            f"({SCHEMA_VERSION}); upgrade chatgpt-bridge"
        )
    if current == SCHEMA_VERSION:
        return

    files = sorted(migrations_dir().glob("*.sql"))
    if current > 0:
        _backup(conn, db_path, current)

    for file in files:
        version = int(file.name.split("_", 1)[0])
        if version <= current:
            continue
        script = file.read_text(encoding="utf-8")
        try:
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            raise StoreError(f"migration {file.name} failed: {exc}") from exc
        current = version

    final = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if final != SCHEMA_VERSION:
        raise StoreError(f"migration ended at version {final}, expected {SCHEMA_VERSION}")


def _backup(conn: sqlite3.Connection, db_path: Path, version: int) -> Path:
    target = db_path.with_name(f"{db_path.name}.bak-{version}")
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    shutil.copy2(db_path, target)
    return target


def drop_migration_backup(db_path: Path) -> None:
    """Called after a successful sync; keeps one fallback until then."""
    for backup in db_path.parent.glob(f"{db_path.name}.bak-*"):
        with contextlib.suppress(OSError):
            backup.unlink()


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


# ---------------------------------------------------------------- meta


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# ---------------------------------------------------------------- writes


def upsert_listed_chats(
    conn: sqlite3.Connection,
    refs: Sequence[ChatRef],
    seen_at: str,
    parse_version: int = DEFAULT_PARSE_VERSION,
) -> None:
    """Record chats seen in a listing. Never touches fetched content."""
    with _transaction(conn):
        for ref in refs:
            conn.execute(
                """
                INSERT INTO chats(id, title, url, section, sidebar_rank, last_seen_at,
                                  stale, parse_version)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(id) DO UPDATE SET
                  title = excluded.title,
                  url = excluded.url,
                  section = excluded.section,
                  sidebar_rank = excluded.sidebar_rank,
                  last_seen_at = excluded.last_seen_at,
                  stale = 0
                """,
                (
                    ref.id,
                    ref.title,
                    ref.url,
                    ref.section,
                    ref.sidebar_rank,
                    seen_at,
                    parse_version,
                ),
            )


def mark_missing_stale(conn: sqlite3.Connection, seen_at: str) -> list[str]:
    """Mark chats not seen in this listing as stale. Returns their IDs."""
    rows = conn.execute(
        "SELECT id FROM chats WHERE last_seen_at < ? AND stale = 0", (seen_at,)
    ).fetchall()
    ids = [str(row["id"]) for row in rows]
    if ids:
        with _transaction(conn):
            conn.executemany("UPDATE chats SET stale = 1 WHERE id = ?", [(i,) for i in ids])
    return ids


def save_chat(
    conn: sqlite3.Connection,
    chat_id: str,
    turns: Sequence[Turn],
    hash_value: str,
    parse_version: int = DEFAULT_PARSE_VERSION,
    fetched_at: str | None = None,
) -> None:
    """Replace a chat's turns in one transaction."""
    fetched = fetched_at or utc_now()
    with _transaction(conn):
        conn.execute("DELETE FROM turns WHERE chat_id = ?", (chat_id,))
        for position, turn in enumerate(turns):
            conn.execute(
                "INSERT INTO turns(chat_id, position, role, markdown, text, alternatives) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, position, turn.role, turn.markdown, turn.text, turn.alternatives),
            )
            for idx, att in enumerate(turn.attachments):
                conn.execute(
                    "INSERT INTO attachments(chat_id, position, idx, name, mime, kind) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (chat_id, position, idx, att.name, att.mime, att.kind),
                )
        conn.execute(
            "UPDATE chats SET content_hash = ?, turn_count = ?, fetched_at = ?, "
            "parse_version = ?, stale = 0 WHERE id = ?",
            (hash_value, len(turns), fetched, parse_version, chat_id),
        )


def save_sync_run(
    conn: sqlite3.Connection,
    kind: RunKind,
    status: RunStatus,
    summary: dict[str, Any],
    started_at: str,
    finished_at: str | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO sync_runs(kind, started_at, finished_at, status, summary) "
        "VALUES (?, ?, ?, ?, ?)",
        (kind, started_at, finished_at, status, json.dumps(summary)),
    )
    return int(cursor.lastrowid or 0)


def update_sync_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: RunStatus,
    summary: dict[str, Any],
    finished_at: str | None = None,
) -> None:
    conn.execute(
        "UPDATE sync_runs SET status = ?, summary = ?, finished_at = ? WHERE id = ?",
        (status, json.dumps(summary), finished_at or utc_now(), run_id),
    )


# ---------------------------------------------------------------- reads


def chat_summaries(
    conn: sqlite3.Connection,
    section: str | None = None,
    stale: bool | None = None,
    unfetched: bool = False,
    limit: int | None = None,
) -> list[ChatSummary]:
    sql = "SELECT * FROM chats WHERE 1 = 1"
    params: list[Any] = []
    if section is not None:
        sql += " AND section = ?"
        params.append(section)
    if stale is not None:
        sql += " AND stale = ?"
        params.append(1 if stale else 0)
    if unfetched:
        sql += " AND fetched_at IS NULL"
    sql += (
        " ORDER BY (fetched_at IS NULL), sidebar_rank IS NULL, sidebar_rank, title COLLATE NOCASE"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [
        ChatSummary(
            id=str(row["id"]),
            title=str(row["title"]),
            url=str(row["url"]),
            section=row["section"],
            sidebar_rank=row["sidebar_rank"],
            turn_count=row["turn_count"],
            fetched_at=row["fetched_at"],
            stale=bool(row["stale"]),
        )
        for row in conn.execute(sql, params).fetchall()
    ]


def chats_needing_fetch(conn: sqlite3.Connection, limit: int | None = None) -> list[str]:
    sql = (
        "SELECT id FROM chats WHERE fetched_at IS NULL OR parse_version < ? "
        "ORDER BY sidebar_rank IS NULL, sidebar_rank, id"
    )
    rows = conn.execute(sql, (DEFAULT_PARSE_VERSION,)).fetchall()
    ids = [str(row["id"]) for row in rows]
    return ids[:limit] if limit is not None else ids


def chat_exists(conn: sqlite3.Connection, chat_id: str) -> bool:
    return conn.execute("SELECT 1 FROM chats WHERE id = ?", (chat_id,)).fetchone() is not None


def get_chat(conn: sqlite3.Connection, chat_id: str) -> StoredChat | None:
    row = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    if row is None:
        return None
    turns = _load_turns(conn, chat_id)
    return StoredChat(
        id=str(row["id"]),
        title=str(row["title"]),
        url=str(row["url"]),
        section=row["section"],
        sidebar_rank=row["sidebar_rank"],
        content_hash=row["content_hash"],
        turn_count=row["turn_count"],
        fetched_at=row["fetched_at"],
        last_seen_at=str(row["last_seen_at"]),
        stale=bool(row["stale"]),
        parse_version=int(row["parse_version"]),
        turns=turns,
    )


def _load_turns(conn: sqlite3.Connection, chat_id: str) -> tuple[Turn, ...]:
    rows = conn.execute(
        "SELECT position, role, markdown, text, alternatives FROM turns "
        "WHERE chat_id = ? ORDER BY position",
        (chat_id,),
    ).fetchall()
    attachments: dict[int, list[Attachment]] = {}
    for att in conn.execute(
        "SELECT position, name, mime, kind FROM attachments WHERE chat_id = ? "
        "ORDER BY position, idx",
        (chat_id,),
    ).fetchall():
        attachments.setdefault(int(att["position"]), []).append(
            Attachment(name=att["name"], mime=att["mime"], kind=att["kind"])
        )
    return tuple(
        Turn(
            role=str(row["role"]),  # type: ignore[arg-type]
            markdown=str(row["markdown"]),
            text=str(row["text"]),
            alternatives=int(row["alternatives"]),
            attachments=tuple(attachments.get(int(row["position"]), [])),
        )
        for row in rows
    )


def search(
    conn: sqlite3.Connection,
    query: str,
    chat_id: str | None = None,
    section: str | None = None,
    since: str | None = None,
    limit: int = 20,
) -> list[SearchHit]:
    """Full-text search. Short queries (<3 chars) fall back to a LIKE scan."""
    query = query.strip()
    if not query:
        return []

    filters: list[str] = []
    params: list[Any] = []
    if chat_id:
        filters.append("t.chat_id = ?")
        params.append(chat_id)
    if section:
        filters.append("c.section = ?")
        params.append(section)
    if since:
        filters.append("c.fetched_at >= ?")
        params.append(since)
    where = (" AND " + " AND ".join(filters)) if filters else ""

    if len(query) >= 3:
        match = _fts_query(query)
        sql = (
            "SELECT t.chat_id, c.title, t.position, t.role, "
            "snippet(turns_fts, 0, '[', ']', '…', 12) AS snippet "
            "FROM turns_fts JOIN turns t ON t.rowid = turns_fts.rowid "
            "JOIN chats c ON c.id = t.chat_id "
            f"WHERE turns_fts MATCH ?{where} "
            "ORDER BY rank LIMIT ?"
        )
        rows = conn.execute(sql, [match, *params, limit]).fetchall()
    else:
        sql = (
            "SELECT t.chat_id, c.title, t.position, t.role, "
            "substr(t.text, max(1, instr(lower(t.text), lower(?)) - 40), 120) AS snippet "
            "FROM turns t JOIN chats c ON c.id = t.chat_id "
            f"WHERE lower(t.text) LIKE lower(?){where} "
            "ORDER BY t.chat_id, t.position LIMIT ?"
        )
        like = f"%{query}%"
        rows = conn.execute(sql, [query, like, *params, limit]).fetchall()

    return [
        SearchHit(
            chat_id=str(row["chat_id"]),
            title=str(row["title"]),
            position=int(row["position"]),
            role=str(row["role"]),
            snippet=str(row["snippet"] or ""),
        )
        for row in rows
    ]


def _fts_query(query: str) -> str:
    """Quote each term so FTS5 syntax characters are treated as text."""
    terms = [t.replace('"', '""') for t in query.split() if t]
    return " AND ".join(f'"{term}"' for term in terms)


def counts(conn: sqlite3.Connection) -> Counts:
    one = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(fetched_at IS NOT NULL) AS fetched, "
        "SUM(stale) AS stale FROM chats"
    ).fetchone()
    turns = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    return Counts(
        chats=int(one["total"] or 0),
        fetched=int(one["fetched"] or 0),
        stale=int(one["stale"] or 0),
        turns=int(turns or 0),
        last_full_sync=get_meta(conn, "last_full_sync"),
        last_incremental_sync=get_meta(conn, "last_incremental_sync"),
    )


def delete_all(conn: sqlite3.Connection) -> None:
    with _transaction(conn):
        conn.execute("DELETE FROM attachments")
        conn.execute("DELETE FROM turns")
        conn.execute("DELETE FROM chats")
        conn.execute("DELETE FROM sync_runs")
        conn.execute("DELETE FROM meta")


class _transaction:
    """Explicit transaction, since the connection runs in autocommit mode."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
