"""Explicit write path: one confirmed message, one chat.

Nothing here decides whether sending is allowed. The CLI prompts and the MCP
tool requires `confirm=true` plus `expected_chat_id`; this module performs the
send after that decision and stores the result for later reading and search.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from chatgpt_bridge import store
from chatgpt_bridge.adapters.chatgpt import ExtractedChat, ReplyTimeout
from chatgpt_bridge.resolve import (
    Resolved,
    TargetNotFound,
    direct_resolved,
    resolve_from_cache,
    resolve_with_listing,
)
from chatgpt_bridge.store import ChatRef, Turn

SendOutcome = Literal["sent", "unknown"]


class SendError(RuntimeError):
    """The message was not submitted (validation, draft, or layout failure)."""


class SendRefused(SendError):
    """The caller's confirmation did not match the resolved chat."""


class SendSource(Protocol):
    def list_chats(self, scroll: bool = False, limit: int | None = None) -> list[ChatRef]: ...

    def list_project_chats(self, project: str, limit: int = 50) -> list[ChatRef]: ...

    def send_message(self, chat_id: str, message: str) -> ExtractedChat: ...

    def create_chat(self, message: str) -> ExtractedChat: ...

    def create_chat_in_project(self, project: str, message: str) -> ExtractedChat: ...


SessionFactory = Callable[[], AbstractContextManager[SendSource]]


@dataclass(frozen=True)
class SendResult:
    outcome: SendOutcome
    chat_id: str
    url: str
    chat: store.StoredChat | None = None
    reply: str | None = None


def preview(
    target: str, *, db_path: Path | None = None, project: str | None = None
) -> Resolved | None:
    """Resolve a target without opening a page. Used by dry runs and prompts."""
    direct = direct_resolved(target)
    if direct is not None:
        return direct
    conn = store.connect(db_path)
    try:
        summaries = (
            store.chat_summaries_in_project(conn, project)
            if project
            else store.chat_summaries(conn)
        )
        return resolve_from_cache(target, summaries)
    finally:
        conn.close()


def send_message(
    target: str,
    message: str,
    *,
    expected_chat_id: str | None = None,
    db_path: Path | None = None,
    session: SessionFactory | None = None,
    lookup_limit: int = 50,
    project: str | None = None,
) -> SendResult:
    """Send one message to one existing chat, then refresh the stored copy."""
    if not target.strip():
        raise SendError("target is empty")
    _validate(message)
    conn = store.connect(db_path)
    try:
        cached = _cached_match(conn, target, project=project)
        if cached is not None:
            if session is None:
                raise TargetNotFound(
                    f"chat {target!r} is stored but sending requires a browser session"
                )
            with session() as source:
                return _send_with_source(conn, source, cached, message, expected_chat_id, project)

        if session is None:
            raise TargetNotFound(
                f"chat {target!r} is not stored and no browser session is available"
            )
        with session() as source:
            resolved = direct_resolved(target)
            if resolved is None:
                refs = (
                    source.list_project_chats(project, limit=lookup_limit)
                    if project
                    else source.list_chats(scroll=False, limit=lookup_limit)
                )
                resolved = resolve_with_listing(target, refs)
            return _send_with_source(conn, source, resolved, message, expected_chat_id, project)
    finally:
        conn.close()


def create_chat(
    message: str,
    *,
    db_path: Path | None = None,
    session: SessionFactory | None = None,
    project: str | None = None,
) -> SendResult:
    """Create a new chat with one message, then store the conversation."""
    _validate(message)
    if project is not None and not project.strip():
        raise SendError("project is empty")
    if session is None:
        raise SendError("creating a chat requires a browser session")
    conn = store.connect(db_path)
    try:
        with session() as source:
            try:
                extracted = (
                    source.create_chat_in_project(project, message)
                    if project
                    else source.create_chat(message)
                )
            except ReplyTimeout as exc:
                if exc.chat_id:
                    store.record_send(conn, exc.chat_id, "unknown", "ReplyTimeout")
                return SendResult(outcome="unknown", chat_id=exc.chat_id, url=exc.url)
        return _save_and_result(conn, extracted, section=None, project=project)
    finally:
        conn.close()


def _send_with_source(
    conn: object,
    source: SendSource,
    resolved: Resolved,
    message: str,
    expected_chat_id: str | None,
    project: str | None = None,
) -> SendResult:
    if expected_chat_id is not None and resolved.id != expected_chat_id:
        raise SendRefused(
            f"refusing to send: expected chat {expected_chat_id}, resolved {resolved.id}"
        )
    try:
        # A project chat only loads on its /g/<project>/c/<chat> URL.
        target = resolved.url if resolved.project_id else resolved.id
        extracted = source.send_message(target, message)
    except ReplyTimeout as exc:
        store.record_send(conn, exc.chat_id or resolved.id, "unknown", "ReplyTimeout")  # type: ignore[arg-type]
        return SendResult(
            outcome="unknown",
            chat_id=exc.chat_id or resolved.id,
            url=exc.url or resolved.url,
        )
    return _save_and_result(
        conn,
        extracted,
        section=resolved.section,
        project=project or resolved.project,
        project_id=resolved.project_id,
    )


def _save_and_result(
    conn: object,
    extracted: ExtractedChat,
    *,
    section: str | None,
    project: str | None = None,
    project_id: str | None = None,
) -> SendResult:
    turns = [
        Turn(
            role=turn.role,
            markdown=turn.markdown,
            text=turn.text,
            alternatives=turn.alternatives,
        )
        for turn in extracted.turns
    ]
    store.save_chat(
        conn,  # type: ignore[arg-type]
        chat_id=extracted.id,
        title=extracted.title or extracted.id,
        url=extracted.url,
        section=section,
        turns=turns,
        hash_value=store.content_hash(turns),
        project=project,
        project_id=project_id or extracted.project_id,
    )
    store.record_fetch(conn, extracted.id, "ok")  # type: ignore[arg-type]
    store.record_send(conn, extracted.id, "sent")  # type: ignore[arg-type]
    chat = store.get_chat(conn, extracted.id)  # type: ignore[arg-type]
    if chat is None:  # pragma: no cover - just written
        raise store.StoreError(f"chat {extracted.id} vanished after sending")
    return SendResult(
        outcome="sent",
        chat_id=chat.id,
        url=chat.url,
        chat=chat,
        reply=_last_assistant(chat),
    )


def _last_assistant(chat: store.StoredChat) -> str | None:
    for turn in reversed(chat.turns):
        if turn.role == "assistant":
            return turn.markdown
    return None


def _cached_match(conn: object, target: str, project: str | None = None) -> Resolved | None:
    summaries = (
        store.chat_summaries_in_project(conn, project)  # type: ignore[arg-type]
        if project
        else store.chat_summaries(conn)  # type: ignore[arg-type]
    )
    return resolve_from_cache(target, summaries)


def _validate(message: str) -> None:
    if not message.strip():
        raise SendError("message is empty")
