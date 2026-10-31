from __future__ import annotations

import pytest

from chatgpt_bridge.adapters import chatgpt
from chatgpt_bridge.adapters.chatgpt import (
    ChatGPTAdapter,
    ChatNotFound,
    LoginRequired,
    chat_id_from_url,
    chat_url,
)

UUID = "6ae5de7d-e0f8-83ec-9a71-cd2780e3bf72"


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


class _Keyboard:
    def __init__(self, page: _SendPage) -> None:
        self.page = page

    def insert_text(self, text: str) -> None:
        self.page.typed = self.page.typed_override or text

    def press(self, key: str) -> None:
        if key == "Backspace":
            self.page.typed = ""


class _SendPage:
    def __init__(
        self,
        url: str,
        *,
        final_url: str | None = None,
        typed_override: str | None = None,
        draft: str = "",
        login: bool = False,
        post: dict[str, object] | None = None,
    ) -> None:
        self.url = url
        self.final_url = final_url
        self.typed_override = typed_override
        self.typed = draft
        self.login = login
        self.sent = False
        self.clicks: list[str] = []
        self.keyboard = _Keyboard(self)
        self.post = post

    def wait_for_selector(self, selector: str, timeout: int | None = None) -> None:
        if selector == chatgpt.COMPOSER and self.login:
            raise RuntimeError("timeout")
        if selector == chatgpt.SEND_BUTTON and not self.typed:
            raise RuntimeError("timeout")

    def evaluate(self, script: str) -> object:
        if script == chatgpt.COMPOSER_TEXT_JS:
            return self.typed
        if script == chatgpt.REPLY_STATE_JS:
            if self.sent:
                return self.post or {"assistant": 2, "lastLen": 5, "stop": False}
            return {"assistant": 1, "lastLen": 4, "stop": False}
        if script == chatgpt.LOGIN_STATE_JS:
            return {"sidebar": not self.login, "login": self.login}
        if script == chatgpt.EXTRACT_CHAT_JS:
            return {
                "title": "T",
                "url": self.url,
                "turns": [
                    {"role": "user", "html": "<p>q</p>"},
                    {"role": "assistant", "html": "<p>a</p>"},
                ],
            }
        raise AssertionError(f"unexpected script: {script[:40]}")

    def click(self, selector: str) -> None:
        self.clicks.append(selector)
        if selector == chatgpt.SEND_BUTTON:
            self.sent = True

    def wait_for_url(self, predicate, timeout: int | None = None) -> None:
        assert self.final_url is not None
        self.url = self.final_url
        assert predicate(self.url)


def test_create_chat_sends_and_returns_the_new_chat() -> None:
    page = _SendPage("https://chatgpt.com/", final_url=f"https://chatgpt.com/c/{UUID}")
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    chat = adapter.create_chat("hello")
    assert chat.id == UUID
    assert [turn.role for turn in chat.turns] == ["user", "assistant"]
    assert chatgpt.SEND_BUTTON in page.clicks


def test_send_message_appends_to_an_existing_chat() -> None:
    page = _SendPage(chat_url(UUID))
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    chat = adapter.send_message(UUID, "hello")
    assert chat.id == UUID
    assert chat.turns[-1].text == "a"


def test_send_refuses_when_the_composer_has_a_draft() -> None:
    page = _SendPage(chat_url(UUID), draft="unsaved work")
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    with pytest.raises(chatgpt.ComposerDraftPresent, match="draft"):
        adapter.send_message(UUID, "hello")
    assert chatgpt.SEND_BUTTON not in page.clicks


def test_send_refuses_when_the_composer_changes_the_message() -> None:
    page = _SendPage(chat_url(UUID), typed_override="something else")
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    with pytest.raises(chatgpt.SendFailed, match="did not accept"):
        adapter.send_message(UUID, "hello")
    assert chatgpt.SEND_BUTTON not in page.clicks


def test_send_refuses_when_the_loaded_chat_is_wrong() -> None:
    page = _SendPage("https://chatgpt.com/c/other", final_url=None)
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    with pytest.raises(chatgpt.SendFailed, match="instead of"):
        adapter.send_message(UUID, "hello")
    assert page.clicks == []


def test_send_times_out_when_no_reply_appears(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chatgpt, "REPLY_TIMEOUT_S", 0.2)
    monkeypatch.setattr(chatgpt, "REPLY_POLL_S", 0.01)
    monkeypatch.setattr(chatgpt, "REPLY_STABLE_POLLS", 100)
    page = _SendPage(chat_url(UUID), post={"assistant": 2, "lastLen": 0, "stop": False})
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    with pytest.raises(chatgpt.ReplyTimeout):
        adapter.send_message(UUID, "hello")
