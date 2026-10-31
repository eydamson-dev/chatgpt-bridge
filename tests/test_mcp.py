from __future__ import annotations

import json
from pathlib import Path

import pytest

from chatgpt_bridge import mcp_server, store


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHATGPT_BRIDGE_DATA_DIR", str(tmp_path / "data"))
    conn = store.connect()
    store.upsert_listed_chats(
        conn,
        [
            store.ChatRef(
                id="c1",
                title="Fedora notes",
                url="https://chatgpt.com/c/c1",
                section="recents",
                sidebar_rank=0,
            ),
            store.ChatRef(
                id="c2",
                title="Unfetched",
                url="https://chatgpt.com/c/c2",
                section="pinned",
                sidebar_rank=1,
            ),
        ],
        seen_at=store.utc_now(),
    )
    turns = [
        store.Turn(role="user", markdown="install **pkg-config**", text="install pkg-config"),
        store.Turn(role="assistant", markdown="run `dnf`", text="run dnf", alternatives=2),
    ]
    store.save_chat(conn, "c1", turns, store.content_hash(turns))
    conn.close()
    return tmp_path


def test_status_reports_counts(seeded: Path) -> None:
    payload = mcp_server.status()
    assert payload["chats"] == 2
    assert payload["fetched"] == 1
    assert payload["messages"] == 2


def test_list_chats_filters(seeded: Path) -> None:
    payload = mcp_server.list_chats(section="pinned")
    assert [c["id"] for c in payload["chats"]] == ["c2"]
    assert payload["chats"][0]["fetched"] is False


def test_search_wraps_untrusted_content(seeded: Path) -> None:
    payload = mcp_server.search_chats("pkg-config")
    assert payload["trust"] == "untrusted_user_content"
    assert "not instructions" in payload["note"]
    assert payload["data"]["hits"][0]["chat_id"] == "c1"


def test_get_chat_wraps_and_includes_markdown(seeded: Path) -> None:
    payload = mcp_server.get_chat("c1", format="markdown")
    assert payload["trust"] == "untrusted_user_content"
    data = payload["data"]
    assert data["id"] == "c1"
    assert "markdown" in data
    assert data["turns"][1]["alternatives"] == 2


def test_get_chat_accepts_url(seeded: Path) -> None:
    payload = mcp_server.get_chat("https://chatgpt.com/c/c1")
    assert payload["data"]["id"] == "c1"


def test_get_chat_missing_raises(seeded: Path) -> None:
    with pytest.raises(Exception, match="not in the cache"):
        mcp_server.get_chat("nope")


def test_no_write_tools_are_exposed() -> None:
    import asyncio

    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert names == {"status", "list_chats", "search_chats", "get_chat", "sync_chats", "doctor"}
    assert not any("send" in name or "delete" in name for name in names)


def test_sync_chats_without_browser_returns_error(
    seeded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHATGPT_BRIDGE_CDP_URL", "http://127.0.0.1:1")
    with pytest.raises(Exception, match="unreachable"):
        mcp_server.sync_chats()


def test_tool_descriptions_mention_untrusted_content() -> None:
    import asyncio

    tools = {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}
    text = json.dumps(
        {name: (tool.description or "") for name, tool in tools.items()}, ensure_ascii=False
    )
    assert "untrusted" in text.lower()
