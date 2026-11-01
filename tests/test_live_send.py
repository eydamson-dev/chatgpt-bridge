"""Live write-path checks. Disabled unless explicitly allowed.

These tests create a chat and send two messages to the user's real account.
They are gated twice: they need a reachable CDP browser and
`CHATGPT_BRIDGE_ALLOW_SEND=1`. Never enable this in CI.

Run with:

    CHATGPT_BRIDGE_ALLOW_SEND=1 uv run pytest -m live_send
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from chatgpt_bridge import query, store
from chatgpt_bridge.adapters.chatgpt import ChatGPTAdapter
from chatgpt_bridge.browser import Browser
from chatgpt_bridge.send import create_chat, send_message

CDP_URL = "http://127.0.0.1:9222"


def cdp_available() -> bool:
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2) as response:
            return bool(json.loads(response.read()))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return False


pytestmark = [
    pytest.mark.live_send,
    pytest.mark.skipif(not cdp_available(), reason=f"no CDP endpoint at {CDP_URL}"),
    pytest.mark.skipif(
        os.getenv("CHATGPT_BRIDGE_ALLOW_SEND") != "1",
        reason="set CHATGPT_BRIDGE_ALLOW_SEND=1 to run write-path live tests",
    ),
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


def test_create_send_and_read_back(tmp_path: Path) -> None:
    db = tmp_path / "live-send.db"
    created = create_chat(
        "chatgpt-bridge write-path test. Reply with exactly: OK",
        db_path=db,
        session=session_factory(),
    )
    assert created.outcome == "sent"
    assert created.chat is not None
    assert created.reply
    assert created.chat.turns[0].role == "user"

    sent = send_message(
        created.chat.id,
        "chatgpt-bridge follow-up. Reply with exactly: DONE",
        db_path=db,
        session=session_factory(),
    )
    assert sent.outcome == "sent"
    assert sent.chat is not None
    assert len(sent.chat.turns) > len(created.chat.turns)
    assert sent.reply

    stored = query.get_chat(created.chat.id, db_path=db)
    assert len(stored.turns) == len(sent.chat.turns)
    assert query.search("follow-up", db_path=db)

    conn = store.connect(db)
    assert store.last_send(conn) is not None
    conn.close()


PROJECT_NAME = "chatgpt-bridge test"


def test_create_and_send_inside_a_project(tmp_path: Path) -> None:
    db = tmp_path / "project-send.db"
    created = create_chat(
        "chatgpt-bridge project write test. Reply with exactly: OK",
        db_path=db,
        session=session_factory(),
        project=PROJECT_NAME,
    )
    assert created.outcome == "sent"
    assert created.chat is not None
    assert created.chat.project == PROJECT_NAME
    assert "/g/" in created.chat.url

    stored = store.connect(db)
    row = stored.execute(
        "SELECT project, project_id FROM chats WHERE id = ?", (created.chat.id,)
    ).fetchone()
    stored.close()
    assert row["project"] == PROJECT_NAME
    assert row["project_id"].startswith("g-p-")

    sent = send_message(
        created.chat.url,
        "chatgpt-bridge project follow-up. Reply with exactly: DONE",
        db_path=db,
        session=session_factory(),
        project=PROJECT_NAME,
    )
    assert sent.outcome == "sent"
    assert sent.chat is not None
    assert len(sent.chat.turns) > len(created.chat.turns)
