"""Turn a user-facing target (URL, ID, prefix, or title) into a chat ID.

Resolution is cache-first so a command never opens a page unless it has to.
Title resolution needs a listing provider, which the caller supplies only when
a browser is available.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from chatgpt_bridge.store import AmbiguousChatId, ChatRef, ChatSummary

CHAT_HOSTS = {"chatgpt.com", "www.chatgpt.com", "chat.openai.com", "chatgpt.ai"}
CHAT_PATH = "/c/"
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
ID_PREFIX_RE = re.compile(r"^[0-9a-fA-F]{6,}$")

ListingProvider = Callable[[], list[ChatRef]]


class TargetNotFound(LookupError):
    """Nothing matched the target."""


class AmbiguousTarget(LookupError):
    """Several chats matched; the caller must be more specific."""

    def __init__(self, message: str, candidates: list[tuple[str, str]]) -> None:
        super().__init__(message)
        self.candidates = candidates


@dataclass(frozen=True)
class Resolved:
    id: str
    title: str
    url: str
    section: str | None = None
    from_cache: bool = False


def target_id(target: str) -> str | None:
    """Extract a chat ID when the target is a URL or an ID/prefix, else None."""
    candidate = target.strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme:
        if parsed.hostname not in CHAT_HOSTS or CHAT_PATH not in f"{parsed.path}/":
            raise TargetNotFound(
                f"not a ChatGPT chat URL: {target!r} (expected https://chatgpt.com/c/<id>)"
            )
        return parsed.path.split(CHAT_PATH, 1)[1].split("/")[0] or None
    if candidate.startswith(CHAT_PATH):
        return candidate[len(CHAT_PATH) :].split("/")[0] or None
    if UUID_RE.match(candidate) or ID_PREFIX_RE.match(candidate):
        return candidate
    return None


def direct_resolved(target: str) -> Resolved | None:
    """Build a target from a URL or a full chat ID, without needing a listing."""
    direct = target_id(target)
    if direct is None or not UUID_RE.match(direct):
        return None
    return Resolved(id=direct, title=direct, url=f"https://chatgpt.com/c/{direct}")


def looks_like_id(target: str) -> bool:
    return target_id(target) is not None and not target.strip().startswith("http")


def resolve_from_cache(target: str, summaries: list[ChatSummary]) -> Resolved | None:
    """Resolve against cached chats only: exact ID, unique prefix, or title."""
    candidate = target.strip()
    if not candidate:
        return None
    by_id = {summary.id: summary for summary in summaries}

    direct = target_id(candidate)
    if direct is not None:
        matches = [summary for summary in summaries if summary.id == direct]
        if matches:
            return _resolved(matches[0])
        prefixed = [summary for summary in summaries if summary.id.startswith(direct)]
        if len(prefixed) == 1:
            return _resolved(prefixed[0])
        if len(prefixed) > 1:
            raise AmbiguousTarget(
                f"{candidate!r} matches several stored chats",
                [(s.id, s.title) for s in prefixed[:5]],
            )
        # An all-hex word such as "decade" is still a title when no ID matches.

    exact = [summary for summary in summaries if summary.title.lower() == candidate.lower()]
    if len(exact) == 1:
        return _resolved(exact[0])
    if len(exact) > 1:
        raise AmbiguousTarget(
            f"several stored chats are titled {candidate!r}",
            [(s.id, s.title) for s in exact[:5]],
        )

    partial = [summary for summary in summaries if candidate.lower() in summary.title.lower()]
    if len(partial) == 1:
        return _resolved(partial[0])
    if len(partial) > 1:
        raise AmbiguousTarget(
            f"{candidate!r} matches several stored chats",
            [(s.id, s.title) for s in partial[:5]],
        )
    fallback = by_id.get(candidate)
    return _resolved(fallback) if fallback is not None else None


def resolve_with_listing(target: str, refs: list[ChatRef]) -> Resolved:
    """Resolve against the visible sidebar list. Raises TargetNotFound/Ambiguous."""
    candidate = target.strip()
    direct = target_id(candidate)
    id_like_miss = False
    if direct is not None:
        matches = [ref for ref in refs if ref.id == direct]
        if matches:
            return _resolved_from_ref(matches[0])
        prefixed = [ref for ref in refs if ref.id.startswith(direct)]
        if len(prefixed) == 1:
            return _resolved_from_ref(prefixed[0])
        if len(prefixed) > 1:
            raise AmbiguousTarget(
                f"{candidate!r} matches several visible chats",
                [(r.id, r.title) for r in prefixed[:5]],
            )
        # A full ID that is not in the visible list can still be fetched directly.
        if UUID_RE.match(direct):
            return Resolved(id=direct, title=direct, url=f"https://chatgpt.com/c/{direct}")
        # An all-hex word such as "decade" may be a title, not an ID prefix.
        id_like_miss = True

    exact = [ref for ref in refs if ref.title.lower() == candidate.lower()]
    if len(exact) == 1:
        return _resolved_from_ref(exact[0])
    if len(exact) > 1:
        raise AmbiguousTarget(
            f"several visible chats are titled {candidate!r}",
            [(r.id, r.title) for r in exact[:5]],
        )

    partial = [ref for ref in refs if candidate.lower() in ref.title.lower()]
    if len(partial) == 1:
        return _resolved_from_ref(partial[0])
    if len(partial) > 1:
        raise AmbiguousTarget(
            f"{candidate!r} matches several visible chats",
            [(r.id, r.title) for r in partial[:5]],
        )
    if id_like_miss:
        raise TargetNotFound(f"chat {candidate!r} is not in the visible chat list")
    raise TargetNotFound(
        f"nothing matched {candidate!r}. Run `chatgpt-bridge chats` to see visible "
        "chats, or pass a chat URL."
    )


def _resolved(summary: ChatSummary) -> Resolved:
    return Resolved(
        id=summary.id,
        title=summary.title,
        url=summary.url,
        section=summary.section,
        from_cache=True,
    )


def _resolved_from_ref(ref: ChatRef) -> Resolved:
    return Resolved(id=ref.id, title=ref.title, url=ref.url, section=ref.section)


__all__ = [
    "AmbiguousChatId",
    "direct_resolved",
    "AmbiguousTarget",
    "Resolved",
    "TargetNotFound",
    "looks_like_id",
    "resolve_from_cache",
    "resolve_with_listing",
    "target_id",
]
