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


def test_search_reports_no_hint_when_it_finds_hits(seeded: Path) -> None:
    assert mcp_server.search_chats("pkg-config")["data"]["hint"] is None


def test_search_hints_that_a_title_matched_instead(seeded: Path) -> None:
    payload = mcp_server.search_chats("fedora")
    assert payload["data"]["hits"] == []
    assert "a stored chat is titled" in payload["data"]["hint"]


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
        "create_project",
        "projects",
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
    project = (tools["create_project"].description or "").lower()
    assert "confirm=true" in create
    assert "confirm=true" in project
    assert "untrusted" in send


# ---------------------------------------------------------------- projects


class _StubBrowser:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def __enter__(self) -> _StubBrowser:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def connect(self) -> None:
        pass


def test_projects_tool_lists_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from chatgpt_bridge.adapters.chatgpt import ProjectRef

    class _Adapter:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def list_projects(self):
            return [ProjectRef("Test project"), ProjectRef("Empty", has_chats=False)]

    monkeypatch.setattr(mcp_server, "Browser", _StubBrowser)
    monkeypatch.setattr(mcp_server, "ChatGPTAdapter", _Adapter)
    payload = mcp_server.projects()
    assert payload["ok"] is True
    assert payload["projects"][0]["name"] == "Test project"
    assert payload["projects"][1]["has_chats"] is False


def test_projects_tool_reports_browser_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("no browser")

    monkeypatch.setattr(mcp_server, "Browser", _boom)
    payload = mcp_server.projects()
    assert payload["ok"] is False
    assert payload["error"] == "RuntimeError"


def test_chats_stored_scoped_to_project(seeded: Path) -> None:
    payload = mcp_server.chats(project="Other project")
    assert payload["chats"] == []
    assert payload["project"] == "Other project"


def test_search_scoped_to_project_returns_hint(seeded: Path) -> None:
    payload = mcp_server.search_chats("pkg-config", project="Other project")
    assert payload["data"]["hits"] == []
    assert "in project 'Other project'" in payload["data"]["hint"]


def test_send_dry_run_reports_the_project(seeded: Path) -> None:
    from chatgpt_bridge import store

    conn = store.connect()
    conn.execute("UPDATE chats SET project = 'Test project' WHERE id = ?", (UUID,))
    conn.close()

    payload = mcp_server.send_message("fedora", "hello", project="Test project")
    assert payload["ok"] is True
    assert payload["project"] == "Test project"


def test_send_dry_run_project_scope_excludes_other_projects(seeded: Path) -> None:
    payload = mcp_server.send_message("fedora", "hello", project="Other project")
    assert payload["ok"] is False


def test_create_dry_run_reports_the_project() -> None:
    payload = mcp_server.create_chat("hello", project="Test project")
    assert payload["dry_run"] is True
    assert payload["project"] == "Test project"


def test_create_project_dry_run_creates_nothing() -> None:
    payload = mcp_server.create_project("Copenhagen Trip")
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["name"] == "Copenhagen Trip"
    assert payload["requires"] == "confirm=true"


def test_create_project_rejects_an_empty_name() -> None:
    payload = mcp_server.create_project("   ", confirm=True)
    assert payload["ok"] is False
    assert "empty" in payload["error"]


def test_create_project_confirm_creates(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chatgpt_bridge.adapters.chatgpt import ProjectRef

    calls: list[str] = []

    def fake_create(name: str, *, session: object = None) -> ProjectRef:
        calls.append(name)
        return ProjectRef(name=name, project_id="g-p-abc")

    monkeypatch.setattr(mcp_server.project_service, "create_project", fake_create)
    payload = mcp_server.create_project("  Trip  ", confirm=True)
    assert payload == {"ok": True, "name": "Trip", "project_id": "g-p-abc"}
    assert calls == ["Trip"]


def test_create_project_reports_a_page_failure(
    seeded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chatgpt_bridge.adapters.chatgpt import ProjectCreateFailed

    def fake_create(name: str, *, session: object = None) -> object:
        raise ProjectCreateFailed("the create-project dialog did not open")

    monkeypatch.setattr(mcp_server.project_service, "create_project", fake_create)
    payload = mcp_server.create_project("Trip", confirm=True)
    assert payload["ok"] is False
    assert "dialog" in payload["error"]
