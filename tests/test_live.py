"""Live checks against a real, logged-in browser.

Skipped unless the CDP endpoint answers. Read-only: these tests fetch one chat
into a temporary database and never write to ChatGPT.

Run with: uv run pytest -m live
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from chatgpt_bridge.adapters.chatgpt import ChatGPTAdapter
from chatgpt_bridge.browser import Browser
from chatgpt_bridge.fetch import fetch_chat
from chatgpt_bridge.store import ChatRef

CDP_URL = "http://127.0.0.1:9222"


def cdp_available() -> bool:
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2) as response:
            return bool(json.loads(response.read()))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return False


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not cdp_available(), reason=f"no CDP endpoint at {CDP_URL}"),
]


def session_factory():
    def factory():
        browser = Browser(CDP_URL)
        browser.connect()
        adapter = ChatGPTAdapter(browser, page_delay_s=0.4)

        class _Session:
            def __enter__(self) -> ChatGPTAdapter:
                return adapter

            def __exit__(self, *_: object) -> None:
                browser.close()

        return _Session()

    return factory


def test_sidebar_lookup_returns_chats() -> None:
    with Browser(CDP_URL) as browser:
        refs = ChatGPTAdapter(browser, page_delay_s=0.2).list_chats(scroll=False, limit=10)
    assert refs, "sidebar returned no chats"
    assert all(isinstance(ref, ChatRef) and ref.id for ref in refs)


def test_fetch_first_visible_chat(tmp_path: Path) -> None:
    with Browser(CDP_URL) as browser:
        refs = ChatGPTAdapter(browser, page_delay_s=0.2).list_chats(scroll=False, limit=1)
    assert refs

    result = fetch_chat(refs[0].id, db_path=tmp_path / "live.db", session=session_factory())
    assert result.outcome == "new"
    assert result.chat.turns, "fetched chat has no messages"
    roles = [turn.role for turn in result.chat.turns]
    assert roles[0] == "user"
    assert all(a != b for a, b in zip(roles, roles[1:], strict=False))

    again = fetch_chat(refs[0].id, db_path=tmp_path / "live.db", session=None)
    assert again.outcome == "cached"


PROJECT_NAME = "chatgpt-bridge test"


def test_lists_projects_read_only() -> None:
    browser = Browser(CDP_URL)
    browser.connect()
    adapter = ChatGPTAdapter(browser, page_delay_s=0.4)
    try:
        projects = adapter.list_projects()
    finally:
        browser.close()
    names = [project.name for project in projects]
    assert names, "expected at least one ChatGPT project"
    assert PROJECT_NAME in names


def test_lists_project_chats_read_only() -> None:
    browser = Browser(CDP_URL)
    browser.connect()
    adapter = ChatGPTAdapter(browser, page_delay_s=0.4)
    try:
        refs = adapter.list_project_chats(PROJECT_NAME)
    finally:
        browser.close()
    for ref in refs:
        assert ref.project == PROJECT_NAME
        assert ref.project_id and ref.project_id.startswith("g-p-")
        assert "/g/" in ref.url and "/c/" in ref.url
