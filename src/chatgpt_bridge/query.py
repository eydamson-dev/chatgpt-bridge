"""Query services shared by the CLI and the MCP server."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from chatgpt_bridge import store
from chatgpt_bridge.store import ChatSummary, SearchHit, StoredChat

MAX_LIMIT = 200
DEFAULT_LIMIT = 20


class NotFound(LookupError):
    """The requested chat is not in the cache."""


def list_chats(
    db_path: Path | None = None,
    section: str | None = None,
    stale: bool | None = None,
    unfetched: bool = False,
    limit: int | None = None,
) -> list[ChatSummary]:
    conn = store.connect(db_path)
    try:
        return store.chat_summaries(
            conn, section=section, stale=stale, unfetched=unfetched, limit=_cap(limit)
        )
    finally:
        conn.close()


def get_chat(chat_id: str, db_path: Path | None = None) -> StoredChat:
    conn = store.connect(db_path)
    try:
        chat = store.get_chat(conn, chat_id)
    finally:
        conn.close()
    if chat is None:
        raise NotFound(f"chat {chat_id} is not in the cache; run a sync first")
    if chat.fetched_at is None:
        raise NotFound(f"chat {chat_id} is listed but its messages were not fetched yet")
    return chat


def search(
    query: str,
    db_path: Path | None = None,
    chat_id: str | None = None,
    section: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> list[SearchHit]:
    conn = store.connect(db_path)
    try:
        return store.search(
            conn,
            query,
            chat_id=chat_id,
            section=section,
            since=since,
            limit=_cap(limit),
        )
    finally:
        conn.close()


def counts(db_path: Path | None = None) -> store.Counts:
    conn = store.connect(db_path)
    try:
        return store.counts(conn)
    finally:
        conn.close()


def last_run(db_path: Path | None = None) -> dict[str, object] | None:
    conn = store.connect(db_path)
    try:
        row = conn.execute(
            "SELECT kind, status, started_at, finished_at, summary FROM sync_runs "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "kind": row["kind"],
        "status": row["status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "summary": row["summary"],
    }


def _cap(limit: int | None) -> int:
    if limit is None or limit <= 0:
        return DEFAULT_LIMIT
    return min(limit, MAX_LIMIT)


def render_text(chat: StoredChat) -> str:
    """Human-readable transcript."""
    lines = [chat.title, chat.url, ""]
    for index, turn in enumerate(chat.turns, start=1):
        who = "You" if turn.role == "user" else "ChatGPT"
        lines.append(f"--- [{index}] {who} ---")
        lines.append(turn.markdown)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(chat: StoredChat) -> str:
    """Markdown document with frontmatter-ish header, suitable for exporting."""
    header = [f"# {chat.title}", "", f"<{chat.url}>", ""]
    body: list[str] = []
    for turn in chat.turns:
        who = "You" if turn.role == "user" else "ChatGPT"
        body.extend([f"## {who}", "", turn.markdown, ""])
    return "\n".join(header + body).rstrip() + "\n"


@dataclass(frozen=True)
class ChatEnvelope:
    """JSON-safe shape for one chat."""

    id: str
    title: str
    url: str
    section: str | None
    stale: bool
    fetched_at: str | None
    turns: list[dict[str, object]]

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "section": self.section,
            "stale": self.stale,
            "fetched_at": self.fetched_at,
            "turns": self.turns,
        }


def envelope(chat: StoredChat, include_markdown: bool = True) -> ChatEnvelope:
    turns: list[dict[str, object]] = []
    for index, turn in enumerate(chat.turns):
        item: dict[str, object] = {
            "position": index,
            "role": turn.role,
            "text": turn.text,
            "alternatives": turn.alternatives,
        }
        if include_markdown:
            item["markdown"] = turn.markdown
        if turn.attachments:
            item["attachments"] = [
                {"name": a.name, "mime": a.mime, "kind": a.kind} for a in turn.attachments
            ]
        turns.append(item)
    return ChatEnvelope(
        id=chat.id,
        title=chat.title,
        url=chat.url,
        section=chat.section,
        stale=chat.stale,
        fetched_at=chat.fetched_at,
        turns=turns,
    )
