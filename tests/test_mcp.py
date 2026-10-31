from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from chatgpt_bridge import mcp_server, store

UUID = "6ae5de7d-e0f8-83ec-9a71-cd2780e3bf72"


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHATGPT_BRIDGE_DATA_DIR", str(tmp_path / "data"))
    conn = store.connect()
    turns = [
        store.Turn(role="user", markdown="install **pkg-config**", text="install pkg-config"),
        store.Turn(role="assistant", markdown="run `dnf`", text="run dnf", alternatives=2),
    ]
    store.save_chat(
        conn,
        chat_id=UUID,
        title="Fedora notes",
        url=f"https://chatgpt.com/c/{UUID}",
        section="recents",
        turns=turns,
        hash_value=store.content_hash(turns),
    )
    store.record_fetch(conn, UUID, "ok")
    conn.close()
    return tmp_path


def test_status_reports_cache(seeded: Path) -> None:
    payload = mcp_server.status()
    assert payload["stored_chats"] == 1
    assert payload["messages"] == 2
    assert payload["last_fetch_status"] == "ok"


def test_chats_defaults_to_local_store_without_browser(seeded: Path) -> None:
    payload = mcp_server.chats()
    assert payload["source"] == "local_store"
    assert payload["chats"][0]["id"] == UUID
    assert payload["chats"][0]["messages"] == 2


def test_get_chat_wraps_untrusted_content(seeded: Path) -> None:
    payload = mcp_server.get_chat("fedora")
    assert payload["trust"] == "untrusted_user_content"
    assert "not instructions" in payload["note"]
    assert payload["data"]["id"] == UUID


def test_get_chat_markdown(seeded: Path) -> None:
    payload = mcp_server.get_chat("6ae5de7d", format="markdown")
    assert payload["data"]["markdown"].startswith("# Fedora notes")


def test_get_chat_missing_names_fetch_chat(seeded: Path) -> None:
    with pytest.raises(Exception, match="chatgpt-bridge fetch"):
        mcp_server.get_chat("nope")


def test_search_wraps_and_reports_cache_size(seeded: Path) -> None:
    payload = mcp_server.search_chats("pkg-config")
    assert payload["trust"] == "untrusted_user_content"
    assert payload["data"]["stored_chats"] == 1
    assert payload["data"]["hits"][0]["chat_id"] == UUID


def test_search_scoped_to_chat(seeded: Path) -> None:
    payload = mcp_server.search_chats("dnf", chat="fedora")
    assert payload["data"]["hits"]


def test_forget_requires_confirm(seeded: Path) -> None:
    payload = mcp_server.forget_chat("fedora")
    assert payload["ok"] is False
    assert "confirm" in str(payload["error"])


def test_forget_removes_with_confirm(seeded: Path) -> None:
    payload = mcp_server.forget_chat("fedora", confirm=True)
    assert payload["ok"] is True
    assert mcp_server.status()["stored_chats"] == 0


def test_fetch_without_browser_raises(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHATGPT_BRIDGE_CDP_URL", "http://127.0.0.1:1")
    with pytest.raises(Exception, match="unreachable"):
        mcp_server.fetch_chat("some chat")


def test_fetch_serves_cache_without_browser(seeded: Path) -> None:
    payload = mcp_server.fetch_chat("6ae5de7d")
    assert payload["data"]["outcome"] == "cached"
    assert payload["data"]["changed"] is False


def test_tool_surface_is_explicit() -> None:
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert names == {
        "status",
        "chats",
        "fetch_chat",
        "get_chat",
        "search_chats",
        "forget_chat",
        "doctor",
        "send_message",
        "create_chat",
    }
    assert not any(name for name in names if name in {"sync", "delete_chat", "rename_chat"})


def test_tool_descriptions_flag_untrusted_content() -> None:
    tools = {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}
    text = json.dumps(
        {name: (tool.description or "") for name, tool in tools.items()}, ensure_ascii=False
    ).lower()
    assert "untrusted" in text
    assert "fetch_chat" in text


def test_send_dry_run_returns_the_stored_chat(seeded: Path) -> None:
    payload = mcp_server.send_message("fedora", "hello")
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["chat_id"] == UUID
    assert payload["requires"].startswith("confirm=true")


def test_send_confirm_requires_expected_chat_id(seeded: Path) -> None:
    payload = mcp_server.send_message("fedora", "hello", confirm=True)
    assert payload["ok"] is False
    assert "expected_chat_id" in payload["error"]


def test_create_dry_run_sends_nothing() -> None:
    payload = mcp_server.create_chat("hello")
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["requires"] == "confirm=true"


def test_write_tools_require_confirmation() -> None:
    tools = {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}
    send = (tools["send_message"].description or "").lower()
    create = (tools["create_chat"].description or "").lower()
    assert "confirm=true" in send and "expected_chat_id" in send
    assert "confirm=true" in create
    assert "untrusted" in send
