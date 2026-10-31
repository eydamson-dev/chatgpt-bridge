"""ChatGPT page adapter: the only module that knows ChatGPT's DOM.

Selectors and labels live here so a page change is a one-file fix.
"""

from __future__ import annotations

import json
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
ROLE_HEADING = "h4[data-conversation-role]"
TURN_WRAPPER = "[data-turn-key]"
SIDEBAR_SCROLLABLE_MAX_STEPS = 40
SIDEBAR_STABLE_ROUNDS = 2

LOAD_TIMEOUT_MS = 30_000

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


class LoginRequired(RuntimeError):
    """The browser is not logged in to ChatGPT."""


class ChatNotFound(RuntimeError):
    """The chat URL did not load a conversation."""


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
    def list_chats(self, scroll: bool = True) -> list[ChatRef]:
        with self.browser.open_tab(BASE_URL) as page:
            page.wait_for_selector(SIDEBAR_NAV, timeout=LOAD_TIMEOUT_MS)
            if scroll:
                self._scroll_sidebar(page)
            payload = page.evaluate(EXTRACT_SIDEBAR_JS)
        return _parse_sidebar(payload)

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
                sidebar_rank=int(item.get("rank", 0)),
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
