from __future__ import annotations

import pytest

from chatgpt_bridge.adapters import chatgpt
from chatgpt_bridge.adapters.chatgpt import (
    ChatNotFound,
    ChatGPTAdapter,
    LoginRequired,
    chat_id_from_url,
    chat_url,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("6ae5de7d-e0f8-83ec-9a71-cd2780e3bf72", "6ae5de7d-e0f8-83ec-9a71-cd2780e3bf72"),
        ("/c/abc123", "abc123"),
        ("https://chatgpt.com/c/abc123", "abc123"),
        ("https://chatgpt.com/c/abc123?model=gpt-5", "abc123"),
        ("https://chatgpt.com/c/abc123#frag", "abc123"),
        ("https://chatgpt.com/c/abc123/", "abc123"),
        ("https://chatgpt.com/g/xyz/c/def456", "def456"),
    ],
)
def test_chat_id_from_url(value: str, expected: str) -> None:
    assert chat_id_from_url(value) == expected


def test_chat_id_from_url_rejects_other_sites() -> None:
    with pytest.raises(ValueError, match="not a ChatGPT chat URL"):
        chat_id_from_url("https://example.com/c/abc")


def test_chat_url() -> None:
    assert chat_url("abc") == "https://chatgpt.com/c/abc"


def test_parse_sidebar_builds_refs() -> None:
    payload = {
        "ok": True,
        "loginRequired": False,
        "chats": [
            {
                "id": "a",
                "title": "Pinned chat",
                "url": "https://chatgpt.com/c/a",
                "section": "pinned",
                "rank": 0,
            },
            {
                "id": "b",
                "title": "",
                "url": "https://chatgpt.com/c/b",
                "section": "recents",
                "rank": 1,
            },
        ],
    }
    refs = chatgpt._parse_sidebar(payload)
    assert [r.id for r in refs] == ["a", "b"]
    assert refs[0].section == "pinned"
    assert refs[1].title == "b"


def test_parse_sidebar_requires_login() -> None:
    with pytest.raises(LoginRequired, match="not logged in"):
        chatgpt._parse_sidebar({"ok": False, "loginRequired": True, "chats": []})


def test_parse_sidebar_rejects_empty_list() -> None:
    with pytest.raises(LoginRequired, match="empty"):
        chatgpt._parse_sidebar({"ok": True, "loginRequired": False, "chats": []})


def test_parse_chat_builds_turns() -> None:
    payload = {
        "title": "Some chat - ChatGPT",
        "url": "https://chatgpt.com/c/abc",
        "turns": [
            {"role": "user", "html": "<p>hi</p>", "alternatives": 1},
            {"role": "assistant", "html": "<p><strong>hello</strong></p>", "alternatives": 2},
        ],
    }
    chat = chatgpt._parse_chat("abc", payload)
    assert chat.title == "Some chat"
    assert [t.role for t in chat.turns] == ["user", "assistant"]
    assert chat.turns[1].markdown == "**hello**"
    assert chat.turns[1].alternatives == 2


def test_parse_chat_cleans_pipe_suffix() -> None:
    chat = chatgpt._parse_chat(
        "abc", {"title": "T | ChatGPT", "turns": [{"role": "user", "html": "<p>x</p>"}]}
    )
    assert chat.title == "T"


def test_parse_chat_rejects_unknown_role() -> None:
    with pytest.raises(ValueError, match="unexpected message role"):
        chatgpt._parse_chat("abc", {"title": "t", "turns": [{"role": "system", "html": "x"}]})


def test_parse_chat_drift_propagates() -> None:
    with pytest.raises(Exception, match="no messages"):
        chatgpt._parse_chat("abc", {"title": "t", "turns": []})


class _FakePage:
    def __init__(self, payload: object, selector_ok: bool = True) -> None:
        self.payload = payload
        self.selector_ok = selector_ok
        self.evaluated: list[str] = []

    def wait_for_selector(self, selector: str, timeout: int | None = None) -> None:
        if not self.selector_ok:
            raise RuntimeError("timeout")

    def evaluate(self, script: str) -> object:
        self.evaluated.append(script)
        if script == chatgpt.EXTRACT_SIDEBAR_JS or script == chatgpt.EXTRACT_CHAT_JS:
            return self.payload
        if script == chatgpt.SCROLL_SIDEBAR_JS:
            return {"found": True, "count": 3, "atBottom": True}
        if script == chatgpt.LOAD_EARLIER_JS:
            return {"clicked": False, "atTop": True}
        return 2  # role count probe


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.opened: list[str | None] = []

    def open_tab(self, url: str | None = None):
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            self.opened.append(url)
            yield self.page

        return _ctx()


def test_list_chats_opens_listing_and_closes_it() -> None:
    page = _FakePage(
        {
            "ok": True,
            "loginRequired": False,
            "chats": [
                {
                    "id": "a",
                    "title": "A",
                    "url": "https://chatgpt.com/c/a",
                    "section": "recents",
                    "rank": 0,
                }
            ],
        }
    )
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    refs = adapter.list_chats()
    assert [r.id for r in refs] == ["a"]


def test_fetch_chat_returns_turns() -> None:
    page = _FakePage(
        {
            "title": "T",
            "url": "https://chatgpt.com/c/abc",
            "turns": [
                {"role": "user", "html": "<p>q</p>", "alternatives": 1},
                {"role": "assistant", "html": "<p>a</p>", "alternatives": 1},
            ],
        }
    )
    fake = _FakeBrowser(page)
    adapter = ChatGPTAdapter(fake, page_delay_s=0)  # type: ignore[arg-type]
    chat = adapter.fetch_chat("abc")
    assert chat.id == "abc"
    assert [t.text for t in chat.turns] == ["q", "a"]
    assert fake.opened == ["https://chatgpt.com/c/abc"]


def test_fetch_chat_reports_missing_chat() -> None:
    page = _FakePage({}, selector_ok=False)
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    with pytest.raises(ChatNotFound, match="no messages appeared"):
        adapter.fetch_chat("abc")
