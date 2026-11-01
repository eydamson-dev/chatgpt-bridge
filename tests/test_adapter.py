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


# ---------------------------------------------------------------- projects

PROJECT_URL = "https://chatgpt.com/g/g-p-6ae70048e0988191babe7d3b4bd2619f/project"
PROJECT_CHAT_FULL = "https://chatgpt.com/g/g-p-6ae70048e0988191babe7d3b4bd2619f/c/" + UUID
PROJECT_CHAT_SLUGGED = (
    "https://chatgpt.com/g/g-p-6ae70048e0988191babe7d3b4bd2619f-test-project/c/" + UUID
)


def test_project_chat_url_regex_accepts_both_forms() -> None:
    plain = chatgpt.PROJECT_CHAT_URL_RE.match(PROJECT_CHAT_FULL)
    slugged = chatgpt.PROJECT_CHAT_URL_RE.match(PROJECT_CHAT_SLUGGED)
    assert plain is not None and slugged is not None
    assert plain.group(1) == slugged.group(1) == "g-p-6ae70048e0988191babe7d3b4bd2619f"
    assert plain.group(2) == slugged.group(2) == UUID


def test_project_chat_url_regex_rejects_plain_chat() -> None:
    assert chatgpt.PROJECT_CHAT_URL_RE.match(f"https://chatgpt.com/c/{UUID}") is None


def test_chat_url_re_does_not_match_project_urls() -> None:
    """Trap from milestone 1: create_chat_in_project must not wait on this."""
    assert chatgpt.CHAT_URL_RE.match(PROJECT_CHAT_FULL) is None
    assert chatgpt.CHAT_URL_RE.match(PROJECT_CHAT_SLUGGED) is None


def test_parse_project_links_keeps_project_id() -> None:
    refs = chatgpt._parse_project_links(
        [{"href": PROJECT_CHAT_FULL, "title": "A chat"}, {"href": "/not/a/chat"}],
        "Test project",
    )
    assert len(refs) == 1
    assert refs[0].id == UUID
    assert refs[0].title == "A chat"
    assert refs[0].project == "Test project"
    assert refs[0].project_id == "g-p-6ae70048e0988191babe7d3b4bd2619f"


def test_parse_project_links_dedupes() -> None:
    payload = [{"href": PROJECT_CHAT_FULL, "title": "A"}, {"href": PROJECT_CHAT_FULL, "title": "A"}]
    refs = chatgpt._dedupe_refs(chatgpt._parse_project_links(payload, "P"))
    assert len(refs) == 1


class _ProjectPage:
    """A page that reveals one chat when a project is expanded."""

    def __init__(self) -> None:
        self.url = "https://chatgpt.com/projects"
        self.expanded = False
        self.clicked: list[str] = []
        self.typed = ""

    def wait_for_selector(self, selector: str, timeout: int | None = None) -> None:
        return None

    def evaluate(self, script: str) -> object:
        if script == chatgpt.EXTRACT_PROJECTS_JS:
            return {
                "projects": [
                    {"name": "Test project", "hasChats": True},
                    {"name": "Empty project", "hasChats": False},
                ]
            }
        if script == chatgpt.PROJECT_LINKS_JS:
            if not self.expanded:
                return []
            return [{"href": PROJECT_CHAT_FULL, "title": "Reply Exactly OK"}]
        raise AssertionError(f"unexpected script: {script[:40]}")

    def click(self, selector: str, timeout: int | None = None) -> None:
        self.clicked.append(selector)
        if "Toggle project" in selector:
            self.expanded = True


def test_list_projects_reads_names() -> None:
    page = _ProjectPage()
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    projects = adapter.list_projects()
    assert [p.name for p in projects] == ["Test project", "Empty project"]
    assert projects[1].has_chats is False


def test_list_project_chats_returns_only_revealed_chats() -> None:
    page = _ProjectPage()
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    refs = adapter.list_project_chats("Test project")
    assert [ref.id for ref in refs] == [UUID]
    assert refs[0].project == "Test project"
    assert any("Toggle project" in selector for selector in page.clicked)


def test_list_project_chats_rejects_an_unknown_project() -> None:
    class _NoClick(_ProjectPage):
        def click(self, selector: str, timeout: int | None = None) -> None:
            raise RuntimeError("no such element")

    adapter = ChatGPTAdapter(_FakeBrowser(_NoClick()), page_delay_s=0)  # type: ignore[arg-type]
    with pytest.raises(ChatNotFound, match="not on the projects page"):
        adapter.list_project_chats("Nope")


class _ProjectSendPage:
    """Walks the real sequence: projects page -> project page -> project chat."""

    def __init__(self) -> None:
        self.url = chatgpt.PROJECTS_URL
        self.typed = ""
        self.typed_override: str | None = None
        self.sent = False
        self.clicks: list[str] = []
        self.keyboard = _Keyboard(self)

    def wait_for_selector(self, selector: str, timeout: int | None = None) -> None:
        return None

    def evaluate(self, script: str) -> object:
        if script == chatgpt.COMPOSER_TEXT_JS:
            return self.typed
        if script == chatgpt.REPLY_STATE_JS:
            if self.sent:
                return {"assistant": 2, "lastLen": 5, "stop": False}
            return {"assistant": 1, "lastLen": 4, "stop": False}
        if script == chatgpt.EXTRACT_CHAT_JS:
            return {
                "title": "Project chat",
                "url": PROJECT_CHAT_FULL,
                "turns": [
                    {"role": "user", "html": "<p>q</p>"},
                    {"role": "assistant", "html": "<p>a</p>"},
                ],
            }
        raise AssertionError(f"unexpected script: {script[:40]}")

    def click(self, selector: str, timeout: int | None = None) -> None:
        self.clicks.append(selector)
        if "Start new chat in project" in selector:
            self.url = PROJECT_URL
        if selector == chatgpt.SEND_BUTTON:
            self.sent = True
            self.url = PROJECT_CHAT_FULL

    def wait_for_url(self, predicate, timeout: int | None = None) -> None:
        if not predicate(self.url):
            raise RuntimeError(f"url did not match: {self.url}")


def test_create_chat_in_project_walks_projects_to_chat() -> None:
    page = _ProjectSendPage()
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    chat = adapter.create_chat_in_project("Test project", "hello")
    assert chat.id == UUID
    assert chat.project_id == "g-p-6ae70048e0988191babe7d3b4bd2619f"
    assert chat.turns[-1].text == "a"
    assert any("Start new chat in project" in selector for selector in page.clicks)


def test_create_chat_in_project_requires_a_message() -> None:
    page = _ProjectSendPage()
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    with pytest.raises(chatgpt.SendFailed, match="empty"):
        adapter.create_chat_in_project("Test project", "  ")
    assert page.clicks == []


class _ProjectCreatePage:
    """Fake /projects page with the create dialog."""

    def __init__(
        self,
        *,
        button: bool = True,
        accepts_name: bool = True,
        appears: bool = True,
        final_url: str = "https://chatgpt.com/projects",
        existing: tuple[str, ...] = ("Test project",),
    ) -> None:
        self.url = chatgpt.PROJECTS_URL
        self.dialog_open = False
        self.name = ""
        self.names = list(existing)
        self.clicks: list[str] = []
        self._button = button
        self._accepts_name = accepts_name
        self._appears = appears
        self._final_url = final_url

    def wait_for_selector(
        self, selector: str, timeout: int | None = None, state: str = "attached"
    ) -> None:
        if selector == chatgpt.PROJECT_ROW:
            return
        if selector == chatgpt.PROJECT_CREATE_DIALOG:
            if state == "hidden":
                if self.dialog_open:
                    raise RuntimeError("dialog is still open")
                return
            if not self.dialog_open:
                raise RuntimeError("dialog did not open")
            return
        raise AssertionError(f"unexpected selector: {selector}")

    def evaluate(self, script: str) -> object:
        if script == chatgpt.OPEN_PROJECT_DIALOG_JS:
            if not self._button:
                return {"clicked": False}
            self.dialog_open = True
            return {"clicked": True}
        if script == chatgpt.EXTRACT_PROJECTS_JS:
            return {"projects": [{"name": name, "hasChats": False} for name in self.names]}
        raise AssertionError(f"unexpected script: {script[:40]}")

    def fill(self, selector: str, value: str) -> None:
        assert selector == chatgpt.PROJECT_NAME_INPUT
        self.name = value if self._accepts_name else value + "x"

    def input_value(self, selector: str) -> str:
        assert selector == chatgpt.PROJECT_NAME_INPUT
        return self.name

    def click(self, selector: str, timeout: int | None = None) -> None:
        self.clicks.append(selector)
        if selector == chatgpt.PROJECT_CREATE_SUBMIT:
            self.dialog_open = False
            self.url = self._final_url
            if self._appears:
                self.names.append(self.name)


def test_create_project_returns_the_new_project() -> None:
    project_id = "g-p-6ae70048e0988191babe7d3b4bd2619f"
    page = _ProjectCreatePage(final_url=f"https://chatgpt.com/g/{project_id}-copenhagen/project")
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    project = adapter.create_project("Copenhagen Trip")
    assert project.name == "Copenhagen Trip"
    assert project.project_id == project_id
    assert page.clicks == [chatgpt.PROJECT_CREATE_SUBMIT]


def test_create_project_trims_the_name() -> None:
    page = _ProjectCreatePage()
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    assert adapter.create_project("  Trip  ").name == "Trip"
    assert page.name == "Trip"


def test_create_project_requires_a_name() -> None:
    page = _ProjectCreatePage()
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    with pytest.raises(chatgpt.ProjectCreateFailed, match="empty"):
        adapter.create_project("   ")
    assert page.clicks == []


def test_create_project_rejects_a_long_name() -> None:
    page = _ProjectCreatePage()
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    with pytest.raises(chatgpt.ProjectCreateFailed, match="longer than"):
        adapter.create_project("x" * (chatgpt.PROJECT_NAME_MAX + 1))
    assert page.clicks == []


def test_create_project_refuses_when_the_field_changes_the_name() -> None:
    page = _ProjectCreatePage(accepts_name=False)
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    with pytest.raises(chatgpt.ProjectCreateFailed, match="did not accept"):
        adapter.create_project("Trip")
    assert chatgpt.PROJECT_CREATE_SUBMIT not in page.clicks


def test_create_project_reports_a_missing_control() -> None:
    page = _ProjectCreatePage(button=False)
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    with pytest.raises(chatgpt.ProjectCreateFailed, match="Add new project"):
        adapter.create_project("Trip")


def test_create_project_reports_a_project_that_never_appears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chatgpt, "PROJECT_SETTLE_TIMEOUT_S", 0.3)
    page = _ProjectCreatePage(appears=False)
    adapter = ChatGPTAdapter(_FakeBrowser(page), page_delay_s=0)  # type: ignore[arg-type]
    with pytest.raises(chatgpt.ProjectCreateFailed, match="before retrying"):
        adapter.create_project("Trip")
