"""Query services shared by the CLI and the MCP server.

Reads never touch the browser: everything here works on the local cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from chatgpt_bridge import store
from chatgpt_bridge.resolve import AmbiguousTarget, resolve_from_cache
from chatgpt_bridge.store import ChatSummary, SearchHit, StoredChat

MAX_LIMIT = 200
DEFAULT_LIMIT = 20


class NotFound(LookupError):
    """The requested chat is not in the cache."""


def list_cached(
    db_path: Path | None = None,
    limit: int | None = None,
    project: str | None = None,
) -> list[ChatSummary]:
    conn = store.connect(db_path)
    try:
        if project:
            return store.chat_summaries_in_project(conn, project, limit=_cap(limit))
        return store.chat_summaries(conn, limit=_cap(limit))
    finally:
        conn.close()


def get_chat(target: str, db_path: Path | None = None) -> StoredChat:
    conn = store.connect(db_path)
    try:
        summaries = store.chat_summaries(conn)
        try:
            resolved = resolve_from_cache(target, summaries)
        except AmbiguousTarget as exc:
            raise NotFound(_ambiguous_message(str(exc), exc.candidates)) from exc
        if resolved is None:
            raise NotFound(_missing_message(target))
        chat = store.get_chat(conn, resolved.id)
    finally:
        conn.close()
    if chat is None:  # pragma: no cover - resolved from the same snapshot
        raise NotFound(_missing_message(target))
    return chat


def search(
    query: str,
    db_path: Path | None = None,
    chat: str | None = None,
    limit: int | None = None,
    project: str | None = None,
) -> list[SearchHit]:
    conn = store.connect(db_path)
    try:
        chat_id: str | None = None
        if chat:
            summaries = (
                store.chat_summaries_in_project(conn, project)
                if project
                else store.chat_summaries(conn)
            )
            try:
                resolved = resolve_from_cache(chat, summaries)
            except AmbiguousTarget as exc:
                raise NotFound(_ambiguous_message(str(exc), exc.candidates)) from exc
            if resolved is None:
                raise NotFound(_missing_message(chat))
            chat_id = resolved.id
        return store.search(conn, query, chat_id=chat_id, limit=_cap(limit), project=project)
    finally:
        conn.close()


def search_hint(query: str, db_path: Path | None = None, project: str | None = None) -> str:
    """Explain a search that found nothing, using the local store only.

    Titles are not indexed, so a term that names a chat produces no hits. Saying
    that plainly is more useful than a bare "no matches".
    """
    term = query.strip()
    conn = store.connect(db_path)
    try:
        if project:
            summaries = store.chat_summaries_in_project(conn, project)
        else:
            summaries = store.chat_summaries(conn)
    finally:
        conn.close()
    total = len(summaries)
    folded = term.casefold()
    titled = [s for s in summaries if folded and folded in s.title.casefold()]
    if titled:
        best = titled[0]
        where = f" in project {best.project!r}" if best.project else ""
        return (
            f"no stored message contains {term!r}, but a stored chat is titled "
            f"{best.title!r}{where}. Read it with: chatgpt-bridge show {best.title!r}"
        )
    plural = "chat is" if total == 1 else "chats are"
    if project:
        return (
            f"no stored message contains {term!r}. Only {total} {plural} stored "
            f"in project {project!r}, and chat titles are not searched. Fetch the "
            f'chat first: chatgpt-bridge fetch "<title>" --project {project!r}'
        )
    return (
        f"no stored message contains {term!r}. Only {total} {plural} stored, and "
        "chat titles are not searched. If you meant a chat from the sidebar, "
        'fetch it first: chatgpt-bridge fetch "<title>"'
    )


def counts(db_path: Path | None = None) -> store.Counts:
    conn = store.connect(db_path)
    try:
        return store.counts(conn)
    finally:
        conn.close()


def last_fetch(db_path: Path | None = None) -> store.FetchLogEntry | None:
    conn = store.connect(db_path)
    try:
        return store.last_fetch(conn)
    finally:
        conn.close()


def forget(target: str, db_path: Path | None = None) -> str:
    """Remove one chat from the cache. Returns the removed chat ID."""
    conn = store.connect(db_path)
    try:
        summaries = store.chat_summaries(conn)
        try:
            resolved = resolve_from_cache(target, summaries)
        except AmbiguousTarget as exc:
            raise NotFound(_ambiguous_message(str(exc), exc.candidates)) from exc
        if resolved is None:
            raise NotFound(_missing_message(target))
        store.forget_chat(conn, resolved.id)
        return resolved.id
    finally:
        conn.close()


def _missing_message(target: str) -> str:
    return f'{target!r} is not in the local store. Fetch it first: chatgpt-bridge fetch "{target}"'


def _ambiguous_message(message: str, candidates: list[tuple[str, str]]) -> str:
    listed = "; ".join(f"{chat_id[:8]} {title}" for chat_id, title in candidates)
    return f"{message}: {listed}"


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
    """Markdown document, suitable for handing to another tool or a note."""
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
    fetched_at: str
    turns: list[dict[str, object]]
    project: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "section": self.section,
            "fetched_at": self.fetched_at,
            "project": self.project,
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
        fetched_at=chat.fetched_at,
        turns=turns,
        project=chat.project,
    )
