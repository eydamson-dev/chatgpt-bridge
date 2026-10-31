"""ChatGPT page adapter: the only module that knows ChatGPT's DOM.

Selectors and labels live here so a page change is a one-file fix.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from chatgpt_bridge.browser import Browser
from chatgpt_bridge.logging_setup import get_logger
from chatgpt_bridge.parse import ParsedTurn, RawTurn, build_turns
from chatgpt_bridge.store import ChatRef

log = get_logger("adapter.chatgpt")

BASE_URL = "https://chatgpt.com"
CHAT_PATH_PREFIX = "/c/"
SECTIONS = {"pinned", "projects", "recents"}

# --- selectors -------------------------------------------------------
SIDEBAR_NAV = 'nav[aria-label="Chat history"]'
CHAT_LINK = 'a[href^="/c/"]'
SIDEBAR_CHAT_LINK = 'nav[aria-label="Chat history"] a[href^="/c/"]'
SIDEBAR_LINK_TIMEOUT_MS = 10_000
ROLE_HEADING = "h4[data-conversation-role]"
TURN_WRAPPER = "[data-turn-key]"
SIDEBAR_SCROLLABLE_MAX_STEPS = 40
SIDEBAR_STABLE_ROUNDS = 2

LOAD_TIMEOUT_MS = 30_000
REPLY_TIMEOUT_S = 120.0
REPLY_POLL_S = 0.5
REPLY_STABLE_POLLS = 3

COMPOSER = 'div[contenteditable="true"][role="textbox"]'
SEND_BUTTON = 'button[aria-label="Send"]'
CHAT_URL_RE = re.compile(
    r"^https?://(?:www\.)?chatgpt\.com/c/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:$|[?#])"
)

# --- in-page scripts -------------------------------------------------
EXTRACT_SIDEBAR_JS = """
() => {
  const nav = document.querySelector('nav[aria-label="Chat history"]');
  if (!nav) return { ok: false, chats: [], loginRequired: true };
  const sections = new Set(['Pinned', 'Projects', 'Recents']);
  const chats = [];
  const seen = new Set();
  let section = null;
  let rank = 0;
  for (const el of nav.querySelectorAll('button, a[href^="/c/"]')) {
    if (el.tagName === 'BUTTON') {
      const text = (el.innerText || '').trim();
      if (sections.has(text)) section = text.toLowerCase();
      continue;
    }
    const href = el.getAttribute('href') || '';
    const id = href.split('/c/')[1]?.split(/[?#]/)[0];
    if (!id || seen.has(id)) continue;
    seen.add(id);
    chats.push({
      id,
      title: (el.innerText || '').trim() || id,
      url: 'https://chatgpt.com' + href,
      section,
      rank: rank++,
    });
  }
  return { ok: true, chats, loginRequired: false };
}
"""

SCROLL_SIDEBAR_JS = """
() => {
  const nav = document.querySelector('nav[aria-label="Chat history"]');
  if (!nav) return { found: false, count: 0, atBottom: true };
  let scroller = nav;
  while (scroller && scroller.scrollHeight <= scroller.clientHeight + 4) {
    scroller = scroller.parentElement;
  }
  const target = scroller || nav;
  const before = target.scrollTop;
  target.scrollTop = target.scrollHeight;
  target.dispatchEvent(new Event('scroll', { bubbles: true }));
  const count = nav.querySelectorAll('a[href^="/c/"]').length;
  return { found: true, count, atBottom: target.scrollTop === before };
}
"""

EXTRACT_CHAT_JS = """
() => {
  const heads = Array.from(document.querySelectorAll('h4[data-conversation-role]'));
  const turns = heads.map((h) => {
    const el = h.nextElementSibling;
    let alternatives = 1;
    const wrapper = h.closest('[data-turn-key]') || document;
    for (const node of wrapper.querySelectorAll('span, div')) {
      if (node.children.length !== 0) continue;
      const text = (node.textContent || '').trim();
      if (/^\\d+\\s*\\/\\s*\\d+$/.test(text)) {
        const total = parseInt(text.split('/')[1], 10);
        if (Number.isFinite(total) && total > alternatives) alternatives = total;
      }
    }
    return {
      role: h.getAttribute('data-conversation-role'),
      html: el ? el.innerHTML : '',
      alternatives,
    };
  });
  return {
    title: document.title,
    url: location.href,
    turns,
    loggedOut: !document.querySelector('nav[aria-label="Chat history"]'),
  };
}
"""

LOAD_EARLIER_JS = """
() => {
  const main = document.querySelector('main');
  if (!main) return { clicked: false, atTop: true };
  main.scrollTop = 0;
  const candidates = Array.from(document.querySelectorAll('button, [role="button"], div'));
  const button = candidates.find(
    (el) =>
      el.children.length === 0 &&
      /loading older messages|load earlier/i.test(el.textContent || '')
  );
  if (button) {
    button.click();
    return { clicked: true, atTop: true };
  }
  return { clicked: false, atTop: main.scrollTop <= 0 };
}
"""


COMPOSER_TEXT_JS = """
() => {
  const composer = document.querySelector('div[contenteditable="true"][role="textbox"]');
  return composer ? (composer.innerText || '') : null;
}
"""

REPLY_STATE_JS = """
() => {
  const heads = Array.from(document.querySelectorAll('h4[data-conversation-role]'));
  const assistants = heads.filter(
    (h) => h.getAttribute('data-conversation-role') === 'assistant'
  );
  const last = assistants.length ? assistants[assistants.length - 1] : null;
  const body = last ? last.nextElementSibling : null;
  const stop = Array.from(document.querySelectorAll('button')).some(
    (b) =>
      /^stop$/i.test((b.getAttribute('aria-label') || '').trim()) ||
      b.getAttribute('data-testid') === 'stop-button'
  );
  return {
    assistant: assistants.length,
    lastLen: body ? (body.innerText || '').length : 0,
    stop,
  };
}
"""

LOGIN_STATE_JS = """
() => {
  const nav = document.querySelector('nav[aria-label="Chat history"]');
  const login = document.querySelector('a[href*="login"], button[data-testid*="login"]');
  return { sidebar: !!nav, login: !!login };
}
"""


class LoginRequired(RuntimeError):
    """The browser is not logged in to ChatGPT."""


class ChatNotFound(RuntimeError):
    """The chat URL did not load a conversation."""


class SendFailed(RuntimeError):
    """The message could not be submitted; nothing was sent."""


class ComposerDraftPresent(SendFailed):
    """The composer already contains text, so sending would overwrite a draft."""


class ReplyTimeout(RuntimeError):
    """The message may have been sent, but no complete reply arrived."""

    def __init__(self, chat_id: str, url: str, message: str | None = None) -> None:
        super().__init__(
            message
            or "the message was submitted but no complete reply appeared in time; "
            "check the chat before retrying"
        )
        self.chat_id = chat_id
        self.url = url


@dataclass(frozen=True)
class ExtractedChat:
    id: str
    title: str
    url: str
    turns: tuple[ParsedTurn, ...] = field(default_factory=tuple)


CHAT_HOSTS = {"chatgpt.com", "www.chatgpt.com", "chat.openai.com"}


def chat_id_from_url(url_or_id: str) -> str:
    """Accept a chat id, a /c/<id> path, or a full ChatGPT URL."""
    candidate = url_or_id.strip()
    parsed = urlparse(candidate)
    if parsed.scheme:
        if parsed.hostname not in CHAT_HOSTS or CHAT_PATH_PREFIX not in parsed.path + "/":
            raise ValueError(f"not a ChatGPT chat URL: {url_or_id!r}")
        candidate = parsed.path
    if CHAT_PATH_PREFIX in candidate:
        tail = candidate.split(CHAT_PATH_PREFIX, 1)[1]
        return tail.split("?")[0].split("#")[0].split("/")[0]
    return candidate


def chat_url(chat_id: str) -> str:
    return f"{BASE_URL}{CHAT_PATH_PREFIX}{chat_id}"


class ChatGPTAdapter:
    def __init__(self, browser: Browser, *, page_delay_s: float = 1.0) -> None:
        self.browser = browser
        self.page_delay_s = page_delay_s

    # -- discovery --------------------------------------------------
    def list_chats(self, scroll: bool = False, limit: int | None = None) -> list[ChatRef]:
        """Read the visible sidebar.

        Scrolling is off by default: this is a title lookup, not a history backfill.
        """
        with self.browser.open_tab(BASE_URL) as page:
            page.wait_for_selector(SIDEBAR_NAV, timeout=LOAD_TIMEOUT_MS)
            # The nav renders before its chat links hydrate.
            with contextlib.suppress(Exception):
                page.wait_for_selector(SIDEBAR_CHAT_LINK, timeout=SIDEBAR_LINK_TIMEOUT_MS)
            if scroll:
                self._scroll_sidebar(page)
            payload = page.evaluate(EXTRACT_SIDEBAR_JS)
        refs = _parse_sidebar(payload)
        return refs[:limit] if limit is not None else refs

    def _scroll_sidebar(self, page: Any) -> None:
        stable = 0
        previous = -1
        for _ in range(SIDEBAR_SCROLLABLE_MAX_STEPS):
            state = page.evaluate(SCROLL_SIDEBAR_JS)
            count = int(state.get("count", 0))
            if count == previous and state.get("atBottom"):
                stable += 1
                if stable >= SIDEBAR_STABLE_ROUNDS:
                    break
            else:
                stable = 0
            previous = count
            time.sleep(max(self.page_delay_s, 0.2))

    # -- content ----------------------------------------------------
    def fetch_chat(self, url_or_id: str) -> ExtractedChat:
        chat_id = chat_id_from_url(url_or_id)
        with self.browser.open_tab(chat_url(chat_id)) as page:
            try:
                page.wait_for_selector(ROLE_HEADING, timeout=LOAD_TIMEOUT_MS)
            except Exception as exc:
                raise ChatNotFound(
                    f"no messages appeared for chat {chat_id}; it may be deleted, "
                    "unavailable, or the page layout changed"
                ) from exc
            self._load_full_chat(page)
            payload = page.evaluate(EXTRACT_CHAT_JS)
        return _parse_chat(chat_id, payload)

    # -- writes (explicit, confirmed by the caller) ------------------
    def create_chat(self, message: str) -> ExtractedChat:
        """Send one message from the new-chat page and return the created chat."""
        if not message.strip():
            raise SendFailed("message is empty")
        with self.browser.open_tab(BASE_URL) as page:
            self._open_composer(page)
            before = self._reply_state(page)
            self._type_message(page, message)
            self._click_send(page)
            try:
                page.wait_for_url(
                    lambda url: CHAT_URL_RE.match(url) is not None,
                    timeout=LOAD_TIMEOUT_MS,
                )
            except Exception as exc:
                raise ReplyTimeout("", page.url) from exc
            chat_id = chat_id_from_url(page.url)
            self._wait_for_reply(page, chat_id, before)
            payload = page.evaluate(EXTRACT_CHAT_JS)
        return _parse_chat(chat_id, payload)

    def send_message(self, chat_id: str, message: str) -> ExtractedChat:
        """Append one message to an existing chat and return the refreshed chat."""
        if not message.strip():
            raise SendFailed("message is empty")
        with self.browser.open_tab(chat_url(chat_id)) as page:
            self._open_composer(page)
            loaded = chat_id_from_url(page.url) if "/c/" in page.url else ""
            if loaded != chat_id:
                raise SendFailed(
                    f"loaded chat {loaded or 'unknown'} instead of {chat_id}; nothing was sent"
                )
            before = self._reply_state(page)
            self._type_message(page, message)
            self._click_send(page)
            self._wait_for_reply(page, chat_id, before)
            payload = page.evaluate(EXTRACT_CHAT_JS)
        return _parse_chat(chat_id, payload)

    def _open_composer(self, page: Any) -> None:
        try:
            page.wait_for_selector(COMPOSER, timeout=LOAD_TIMEOUT_MS)
        except Exception as exc:
            try:
                state = page.evaluate(LOGIN_STATE_JS)
            except Exception:
                state = {}
            if not state.get("sidebar") or state.get("login"):
                raise LoginRequired(
                    "ChatGPT is not logged in in this browser, or the sidebar is missing. "
                    "Open https://chatgpt.com in that browser, log in, then retry."
                ) from exc
            raise ChatNotFound(
                "the message composer did not load; the chat may be unavailable, "
                "or the page layout changed"
            ) from exc

    def _reply_state(self, page: Any) -> dict[str, int | bool]:
        state = page.evaluate(REPLY_STATE_JS) or {}
        return {
            "assistant": int(state.get("assistant", 0)),
            "lastLen": int(state.get("lastLen", 0)),
            "stop": bool(state.get("stop")),
        }

    def _type_message(self, page: Any, message: str) -> None:
        existing = page.evaluate(COMPOSER_TEXT_JS)
        if isinstance(existing, str) and existing.strip():
            raise ComposerDraftPresent(
                "the composer already contains a draft; clear it in the browser first"
            )
        page.click(COMPOSER)
        page.keyboard.insert_text(message)
        typed = page.evaluate(COMPOSER_TEXT_JS) or ""
        if not isinstance(typed, str) or _normalise(typed) != _normalise(message):
            self._clear_composer(page)
            raise SendFailed("the composer did not accept the message exactly; nothing was sent")

    def _click_send(self, page: Any) -> None:
        try:
            page.wait_for_selector(SEND_BUTTON, timeout=5_000)
        except Exception as exc:
            self._clear_composer(page)
            raise SendFailed("the send button did not appear; nothing was sent") from exc
        page.click(SEND_BUTTON)

    def _clear_composer(self, page: Any) -> None:
        with contextlib.suppress(Exception):
            page.click(COMPOSER)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")

    def _wait_for_reply(self, page: Any, chat_id: str, before: dict[str, int | bool]) -> None:
        deadline = time.monotonic() + REPLY_TIMEOUT_S
        stable = 0
        signature: tuple[int, int] | None = None
        while time.monotonic() < deadline:
            state = self._reply_state(page)
            if (
                int(state["assistant"]) > int(before["assistant"])
                and int(state["lastLen"]) > 0
                and not state["stop"]
            ):
                current = (int(state["assistant"]), int(state["lastLen"]))
                if current == signature:
                    stable += 1
                else:
                    stable = 1
                    signature = current
                if stable >= REPLY_STABLE_POLLS:
                    return
            else:
                stable = 0
                signature = None
            time.sleep(REPLY_POLL_S)
        raise ReplyTimeout(chat_id, page.url)

    def _load_full_chat(self, page: Any) -> None:
        """Scroll to the top and click any 'load earlier' control until stable."""
        previous = -1
        for _ in range(60):
            state = page.evaluate(LOAD_EARLIER_JS)
            count = int(page.evaluate(f"() => document.querySelectorAll('{ROLE_HEADING}').length"))
            if count == previous and not state.get("clicked"):
                break
            previous = count
            time.sleep(max(self.page_delay_s, 0.2))


def _parse_sidebar(payload: dict[str, Any]) -> list[ChatRef]:
    if payload.get("loginRequired") or not payload.get("ok"):
        raise LoginRequired(
            "ChatGPT is not logged in in this browser, or the sidebar is missing. "
            "Open https://chatgpt.com in that browser, log in, then retry."
        )
    refs: list[ChatRef] = []
    for item in payload.get("chats", []):
        section = item.get("section")
        refs.append(
            ChatRef(
                id=str(item["id"]),
                title=str(item.get("title") or item["id"]),
                url=str(item.get("url") or chat_url(str(item["id"]))),
                section=str(section) if section else None,
                rank=int(item.get("rank", 0)),
            )
        )
    if not refs:
        raise LoginRequired(
            "the chat sidebar is empty. If you have chats, the page layout may have changed."
        )
    return refs


def _parse_chat(chat_id: str, payload: dict[str, Any]) -> ExtractedChat:
    raw: list[RawTurn] = []
    for item in payload.get("turns", []):
        role = item.get("role")
        if role not in ("user", "assistant"):
            raise ValueError(f"unexpected message role {role!r}")
        raw.append(
            RawTurn(
                role=role,
                html=str(item.get("html") or ""),
                alternatives=int(item.get("alternatives") or 1),
            )
        )
    turns = build_turns(raw)
    title = _clean_title(str(payload.get("title") or chat_id))
    return ExtractedChat(
        id=chat_id,
        title=title,
        url=str(payload.get("url") or chat_url(chat_id)),
        turns=tuple(turns),
    )


def _normalise(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def _clean_title(title: str) -> str:
    for suffix in (" - ChatGPT", " | ChatGPT"):
        if title.endswith(suffix):
            title = title[: -len(suffix)]
    return title.strip() or "Untitled"


def dump_extraction(chat: ExtractedChat) -> str:
    """Stable JSON shape used by tests and debugging."""
    return json.dumps(
        {
            "id": chat.id,
            "title": chat.title,
            "url": chat.url,
            "turns": [
                {"role": t.role, "markdown": t.markdown, "alternatives": t.alternatives}
                for t in chat.turns
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
