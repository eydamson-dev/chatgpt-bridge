"""Fetch one named chat and cache it. Nothing here scans the chat history."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from chatgpt_bridge import store
from chatgpt_bridge.adapters.chatgpt import ExtractedChat
from chatgpt_bridge.logging_setup import get_logger
from chatgpt_bridge.resolve import (
    AmbiguousTarget,
    Resolved,
    TargetNotFound,
    direct_resolved,
    resolve_from_cache,
    resolve_with_listing,
)
from chatgpt_bridge.store import ChatRef, Turn

log = get_logger("fetch")

FetchOutcome = Literal["cached", "new", "refreshed", "unchanged"]


class ChatSource(Protocol):
    """What fetching needs from a chat source. The adapter implements this."""

    def list_chats(self, scroll: bool = True, limit: int | None = None) -> list[ChatRef]: ...

    def list_project_chats(self, project: str, limit: int = 50) -> list[ChatRef]: ...

    def fetch_chat(self, url_or_id: str) -> ExtractedChat: ...


SessionFactory = Callable[[], AbstractContextManager[ChatSource]]


@dataclass(frozen=True)
class FetchResult:
    chat: store.StoredChat
    outcome: FetchOutcome
    changed: bool

    @property
    def fetched(self) -> bool:
        return self.outcome != "cached"


def fetch_chat(
    target: str,
    *,
    refresh: bool = False,
    db_path: Path | None = None,
    session: SessionFactory | None = None,
    lookup_limit: int = 50,
    project: str | None = None,
) -> FetchResult:
    """Return a chat, fetching it from the browser unless a fresh cached copy is enough.

    The browser session is only opened when a page load is actually required.
    With `project`, resolution is limited to that project and the membership is
    recorded on save.
    """
    conn = store.connect(db_path)
    try:
        cached = _cached_match(conn, target, project=project)

        if cached is not None and not refresh:
            chat = store.get_chat(conn, cached.id)
            if chat is not None:
                log.info("served cached chat %s", chat.id)
                return FetchResult(chat=chat, outcome="cached", changed=False)

        resolved = cached
        if resolved is None or refresh:
            if session is None:
                if cached is not None:
                    raise TargetNotFound(
                        f"chat {target!r} is stored but cannot be refreshed "
                        "without a browser session"
                    )
                raise TargetNotFound(
                    f"chat {target!r} is not stored and no browser session is available"
                )
            with session() as source:
                if resolved is None:
                    resolved = direct_resolved(target)
                if resolved is None:
                    refs = (
                        source.list_project_chats(project, limit=lookup_limit)
                        if project
                        else source.list_chats(scroll=False, limit=lookup_limit)
                    )
                    resolved = resolve_with_listing(target, refs)
                return _fetch_and_store(conn, source, resolved, refresh=refresh, project=project)
        raise TargetNotFound(f"chat {target!r} could not be resolved")
    finally:
        conn.close()


def _cached_match(conn: object, target: str, project: str | None = None) -> Resolved | None:
    if project:
        summaries = store.chat_summaries_in_project(conn, project)  # type: ignore[arg-type]
    else:
        summaries = store.chat_summaries(conn)  # type: ignore[arg-type]
    try:
        return resolve_from_cache(target, summaries)
    except AmbiguousTarget:
        raise


def _fetch_and_store(
    conn: object,
    source: ChatSource,
    resolved: Resolved,
    *,
    refresh: bool,
    project: str | None = None,
) -> FetchResult:
    try:
        # A project chat only loads on its /g/<project>/c/<chat> URL.
        target = resolved.url if resolved.project_id else resolved.id
        extracted = source.fetch_chat(target)
    except Exception as exc:
        # Record the failure without storing content or the exception message,
        # which may contain a URL or a title.
        store.record_fetch(
            conn,  # type: ignore[arg-type]
            resolved.id,
            "failed",
            type(exc).__name__,
        )
        raise
    turns = [
        Turn(
            role=turn.role,
            markdown=turn.markdown,
            text=turn.text,
            alternatives=turn.alternatives,
        )
        for turn in extracted.turns
    ]
    new_hash = store.content_hash(turns)
    existing = store.get_chat(conn, extracted.id)  # type: ignore[arg-type]
    changed = existing is None or existing.content_hash != new_hash

    store.save_chat(
        conn,  # type: ignore[arg-type]
        chat_id=extracted.id,
        title=extracted.title or resolved.title,
        url=extracted.url or resolved.url,
        section=resolved.section,
        turns=turns,
        hash_value=new_hash,
        project=project or resolved.project,
        project_id=extracted.project_id or resolved.project_id,
    )
    status = "ok"
    if not changed:
        status = "unchanged"
    store.record_fetch(conn, extracted.id, status)  # type: ignore[arg-type]

    chat = store.get_chat(conn, extracted.id)  # type: ignore[arg-type]
    if chat is None:  # pragma: no cover - just written
        raise store.StoreError(f"chat {extracted.id} vanished after saving")

    if existing is None:
        outcome: FetchOutcome = "new"
    elif changed:
        outcome = "refreshed"
    else:
        outcome = "unchanged"
    log.info("fetched chat %s (%s, %d turns)", chat.id, outcome, len(chat.turns))
    return FetchResult(chat=chat, outcome=outcome, changed=changed)
