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

SCHEMA_VERSION = 4
DEFAULT_PARSE_VERSION = 1
Role = Literal["user", "assistant"]
FetchStatus = Literal["ok", "unchanged", "failed"]


class StoreError(RuntimeError):
    """Raised when the database cannot be opened or written."""


class NewerSchemaError(StoreError):
    """The database was written by a newer version of the app."""


class AmbiguousChatId(LookupError):
    """A chat ID prefix matched several cached chats."""


@dataclass(frozen=True)
class ChatRef:
    """A chat as it appears in a listing (sidebar or project page)."""

    id: str
    title: str
    url: str
    section: str | None = None
    rank: int | None = None
    project: str | None = None
    project_id: str | None = None


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
    content_hash: str | None
    turn_count: int | None
    fetched_at: str
    parse_version: int
    turns: tuple[Turn, ...]
    project: str | None = None
    project_id: str | None = None


@dataclass(frozen=True)
class ChatSummary:
    id: str
    title: str
    url: str
    section: str | None
    turn_count: int | None
    fetched_at: str
    parse_version: int
    project: str | None = None
    project_id: str | None = None


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
    turns: int
    last_fetch: str | None
    last_send: str | None = None


@dataclass(frozen=True)
class FetchLogEntry:
    chat_id: str
    fetched_at: str
    status: str
    detail: str | None


@dataclass(frozen=True)
class SendLogEntry:
    chat_id: str
    sent_at: str
    status: str
    detail: str | None


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def content_hash(turns: Iterable[Turn]) -> str:
    """Hash normalized turn content, so formatting or text changes count as changes."""
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
    conn: sqlite3.Connection | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        _restrict_permissions(path)
        migrate(conn, path)
    except (OSError, sqlite3.Error) as exc:
        _close_quietly(conn)
        raise StoreError(f"cannot open database {path}: {exc}") from exc
    except BaseException:
        _close_quietly(conn)
        raise
    return conn


def _close_quietly(conn: sqlite3.Connection | None) -> None:
    if conn is not None:
        with contextlib.suppress(sqlite3.Error):
            conn.close()


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

    if current > 0:
        _backup(conn, db_path, current)

    for file in sorted(migrations_dir().glob("*.sql")):
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


def save_chat(
    conn: sqlite3.Connection,
    chat_id: str,
    title: str,
    url: str,
    turns: Sequence[Turn],
    hash_value: str,
    section: str | None = None,
    parse_version: int = DEFAULT_PARSE_VERSION,
    fetched_at: str | None = None,
    project: str | None = None,
    project_id: str | None = None,
) -> None:
    """Store a fetched chat, replacing any previous copy, in one transaction."""
    fetched = fetched_at or utc_now()
    with _transaction(conn):
        conn.execute(
            """
            INSERT INTO chats(id, title, url, section, content_hash, turn_count,
                              fetched_at, parse_version, project, project_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              title = excluded.title,
              url = excluded.url,
              section = excluded.section,
              content_hash = excluded.content_hash,
              turn_count = excluded.turn_count,
              fetched_at = excluded.fetched_at,
              parse_version = excluded.parse_version,
              project = COALESCE(excluded.project, chats.project),
              project_id = COALESCE(excluded.project_id, chats.project_id)
            """,
            (
                chat_id,
                title,
                url,
                section,
                hash_value,
                len(turns),
                fetched,
                parse_version,
                project,
                project_id,
            ),
        )
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


def forget_chat(conn: sqlite3.Connection, chat_id: str) -> bool:
    """Remove one chat from the cache. Returns False when it was not cached."""
    with _transaction(conn):
        cursor = conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    return cursor.rowcount > 0


def record_fetch(
    conn: sqlite3.Connection,
    chat_id: str,
    status: FetchStatus,
    detail: str | None = None,
    fetched_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO fetch_log(chat_id, fetched_at, status, detail) VALUES (?, ?, ?, ?)",
        (chat_id, fetched_at or utc_now(), status, detail),
    )


def last_fetch(conn: sqlite3.Connection) -> FetchLogEntry | None:
    row = conn.execute(
        "SELECT chat_id, fetched_at, status, detail FROM fetch_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return FetchLogEntry(
        chat_id=str(row["chat_id"]),
        fetched_at=str(row["fetched_at"]),
        status=str(row["status"]),
        detail=row["detail"],
    )


def record_send(
    conn: sqlite3.Connection,
    chat_id: str,
    status: Literal["sent", "unknown"],
    detail: str | None = None,
    sent_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO send_log(chat_id, sent_at, status, detail) VALUES (?, ?, ?, ?)",
        (chat_id, sent_at or utc_now(), status, detail),
    )


def last_send(conn: sqlite3.Connection) -> SendLogEntry | None:
    row = conn.execute(
        "SELECT chat_id, sent_at, status, detail FROM send_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return SendLogEntry(
        chat_id=str(row["chat_id"]),
        sent_at=str(row["sent_at"]),
        status=str(row["status"]),
        detail=row["detail"],
    )


# ---------------------------------------------------------------- reads


def chat_summaries(
    conn: sqlite3.Connection,
    limit: int | None = None,
    project_id: str | None = None,
) -> list[ChatSummary]:
    sql = "SELECT * FROM chats"
    params: list[Any] = []
    if project_id is not None:
        sql += " WHERE project_id = ?"
        params.append(project_id)
    sql += " ORDER BY fetched_at DESC, title COLLATE NOCASE"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [
        ChatSummary(
            id=str(row["id"]),
            title=str(row["title"]),
            url=str(row["url"]),
            section=row["section"],
            turn_count=row["turn_count"],
            fetched_at=str(row["fetched_at"]),
            parse_version=int(row["parse_version"]),
            project=row["project"],
            project_id=row["project_id"],
        )
        for row in conn.execute(sql, params).fetchall()
    ]


def chat_summaries_in_project(
    conn: sqlite3.Connection, project: str, limit: int | None = None
) -> list[ChatSummary]:
    """Stored chats belonging to a project, matched by name."""
    rows = conn.execute(
        "SELECT * FROM chats WHERE project = ? COLLATE NOCASE "
        "ORDER BY fetched_at DESC, title COLLATE NOCASE",
        (project,),
    ).fetchall()
    summaries = [
        ChatSummary(
            id=str(row["id"]),
            title=str(row["title"]),
            url=str(row["url"]),
            section=row["section"],
            turn_count=row["turn_count"],
            fetched_at=str(row["fetched_at"]),
            parse_version=int(row["parse_version"]),
            project=row["project"],
            project_id=row["project_id"],
        )
        for row in rows
    ]
    return summaries[:limit] if limit is not None else summaries


def chat_exists(conn: sqlite3.Connection, chat_id: str) -> bool:
    return conn.execute("SELECT 1 FROM chats WHERE id = ?", (chat_id,)).fetchone() is not None


def resolve_chat_id(conn: sqlite3.Connection, candidate: str) -> str | None:
    """Resolve an exact chat ID or a unique ID prefix (as printed by list commands)."""
    if chat_exists(conn, candidate):
        return candidate
    rows = conn.execute(
        "SELECT id FROM chats WHERE id LIKE ? LIMIT 2", (f"{candidate}%",)
    ).fetchall()
    if len(rows) == 1:
        return str(rows[0]["id"])
    if len(rows) > 1:
        ids = ", ".join(str(row["id"])[:8] for row in rows)
        raise AmbiguousChatId(f"{candidate!r} matches several cached chats: {ids}")
    return None


def find_by_title(conn: sqlite3.Connection, title: str) -> list[ChatSummary]:
    """Cached chats whose title matches exactly, else by literal substring."""
    exact = conn.execute("SELECT * FROM chats WHERE title = ? COLLATE NOCASE", (title,)).fetchall()
    rows = (
        exact
        or conn.execute(
            "SELECT * FROM chats WHERE title LIKE ? ESCAPE '\\'",
            (f"%{_like_escape(title)}%",),
        ).fetchall()
    )
    return [
        ChatSummary(
            id=str(row["id"]),
            title=str(row["title"]),
            url=str(row["url"]),
            section=row["section"],
            turn_count=row["turn_count"],
            fetched_at=str(row["fetched_at"]),
            parse_version=int(row["parse_version"]),
            project=row["project"],
            project_id=row["project_id"],
        )
        for row in rows
    ]


def get_chat(conn: sqlite3.Connection, chat_id: str) -> StoredChat | None:
    row = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    if row is None:
        return None
    return StoredChat(
        id=str(row["id"]),
        title=str(row["title"]),
        url=str(row["url"]),
        section=row["section"],
        content_hash=row["content_hash"],
        turn_count=row["turn_count"],
        fetched_at=str(row["fetched_at"]),
        parse_version=int(row["parse_version"]),
        turns=_load_turns(conn, chat_id),
        project=row["project"],
        project_id=row["project_id"],
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
    limit: int = 20,
    project: str | None = None,
) -> list[SearchHit]:
    """Full-text search. Terms shorter than 3 chars cannot be trigram-indexed, so
    those queries fall back to a LIKE scan."""
    query = query.strip()
    if not query:
        return []
    terms = query.split()

    filters: list[str] = []
    params: list[Any] = []
    if chat_id:
        filters.append("t.chat_id = ?")
        params.append(chat_id)
    if project:
        filters.append("lower(c.project) = lower(?)")
        params.append(project)
    where = (" AND " + " AND ".join(filters)) if filters else ""

    if all(len(term) >= 3 for term in terms):
        sql = (
            "SELECT t.chat_id, c.title, t.position, t.role, t.text AS text "
            "FROM turns_fts JOIN turns t ON t.rowid = turns_fts.rowid "
            "JOIN chats c ON c.id = t.chat_id "
            f"WHERE turns_fts MATCH ?{where} "
            "ORDER BY rank LIMIT ?"
        )
        rows = conn.execute(sql, [_fts_query(query), *params, limit]).fetchall()
    else:
        likes = " AND ".join("lower(t.text) LIKE lower(?) ESCAPE '\\'" for _ in terms)
        sql = (
            "SELECT t.chat_id, c.title, t.position, t.role, t.text AS text "
            "FROM turns t JOIN chats c ON c.id = t.chat_id "
            f"WHERE {likes}{where} "
            "ORDER BY t.chat_id, t.position LIMIT ?"
        )
        patterns = [f"%{_like_escape(term)}%" for term in terms]
        rows = conn.execute(sql, [*patterns, *params, limit]).fetchall()

    return [
        SearchHit(
            chat_id=str(row["chat_id"]),
            title=str(row["title"]),
            position=int(row["position"]),
            role=str(row["role"]),
            snippet=_snippet(str(row["text"] or ""), query),
        )
        for row in rows
    ]


SNIPPET_WIDTH = 200


def _snippet(text: str, query: str, width: int = SNIPPET_WIDTH) -> str:
    """A window of text around the first match, with ellipses at the cut edges."""
    first_term = next((term for term in query.split() if term), query)
    index = text.lower().find(first_term.lower())
    if index < 0:
        window = text[:width]
        return window + "…" if len(text) > width else window
    start = max(0, index - width // 3)
    end = min(len(text), start + width)
    start = max(0, end - width)
    window = text[start:end].replace("\n", " ")
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{window}{suffix}"


def _fts_query(query: str) -> str:
    """Quote each term so FTS5 syntax characters are treated as text."""
    terms = [t.replace('"', '""') for t in query.split() if t]
    return " AND ".join(f'"{term}"' for term in terms)


def _like_escape(value: str) -> str:
    """Make %, _ and the escape character literal inside a LIKE pattern."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def counts(conn: sqlite3.Connection) -> Counts:
    chats = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
    turns = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    entry = last_fetch(conn)
    sent = last_send(conn)
    return Counts(
        chats=int(chats or 0),
        turns=int(turns or 0),
        last_fetch=entry.fetched_at if entry else None,
        last_send=sent.sent_at if sent else None,
    )


def delete_all(conn: sqlite3.Connection) -> None:
    with _transaction(conn):
        conn.execute("DELETE FROM attachments")
        conn.execute("DELETE FROM turns")
        conn.execute("DELETE FROM chats")
        conn.execute("DELETE FROM fetch_log")
        conn.execute("DELETE FROM send_log")
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


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
