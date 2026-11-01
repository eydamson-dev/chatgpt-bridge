from __future__ import annotations

import json
from pathlib import Path

import pytest

from chatgpt_bridge import cli as cli_module
from chatgpt_bridge import store
from chatgpt_bridge.cli import (
    EXIT_BROWSER,
    EXIT_ERROR,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_UNKNOWN,
    EXIT_USAGE,
    main,
)
from chatgpt_bridge.send import SendResult

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
    store.save_chat(
        conn,
        chat_id="aaaa1111-2222-3333-4444-555566667777",
        title="Sample hardware review",
        url="https://chatgpt.com/c/aaaa1111-2222-3333-4444-555566667777",
        section="recents",
        turns=[store.Turn(role="user", markdown="Laptop A", text="Laptop A")],
        hash_value="h",
    )
    conn.close()
    return tmp_path


def test_version() -> None:
    assert main(["version"]) == EXIT_OK


def test_status_json(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "status"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["chats"] == 2
    assert payload["turns"] == 3


def test_status_text(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["status"]) == EXIT_OK
    assert "stored chats 2" in capsys.readouterr().out


def test_json_flag_can_follow_the_subcommand(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["status", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["chats"] == 2


def test_chats_cached(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "chats", "--stored"]) == EXIT_OK
    rows = json.loads(capsys.readouterr().out)
    assert {r["title"] for r in rows} == {"Fedora notes", "Sample hardware review"}


def test_chats_cached_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CHATGPT_BRIDGE_DATA_DIR", str(tmp_path / "data"))
    assert main(["chats", "--stored"]) == EXIT_OK
    assert "no stored chats" in capsys.readouterr().out


def test_show_text_by_prefix(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "6ae5de7d"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Fedora notes" in out
    assert "[1] You" in out


def test_show_by_title(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "sample"]) == EXIT_OK
    assert "Sample hardware review" in capsys.readouterr().out


def test_show_markdown(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "6ae5de7d", "--format", "md"]) == EXIT_OK
    assert capsys.readouterr().out.startswith("# Fedora notes")


def test_show_json(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "6ae5de7d", "--format", "json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == UUID
    assert len(payload["turns"]) == 2


def test_show_missing_tells_you_to_fetch(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "not cached anywhere"]) == EXIT_NOT_FOUND
    err = capsys.readouterr().err
    assert "not in the local store" in err
    assert "chatgpt-bridge fetch" in err


def test_search_text_and_json(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["search", "pkg-config"]) == EXIT_OK
    assert "pkg-config" in capsys.readouterr().out

    assert main(["--json", "search", "dnf"]) == EXIT_OK
    hits = json.loads(capsys.readouterr().out)
    assert hits[0]["chat_id"] == UUID


def test_search_no_matches_explains_titles_are_not_searched(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["search", "zzzzzz"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "no stored message contains 'zzzzzz'" in out
    assert "2 chats are stored" in out
    assert "titles are not searched" in out


def test_search_json_keeps_stdout_clean_and_hints_on_stderr(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--json", "search", "zzzzzz"]) == EXIT_OK
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []
    assert "no stored message contains 'zzzzzz'" in captured.err


def test_search_no_matches_points_at_a_matching_stored_title(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["search", "fedora"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "a stored chat is titled 'Fedora notes'" in out
    assert "chatgpt-bridge show 'Fedora notes'" in out


def test_search_scoped_to_chat(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "search", "pkg-config", "--chat", "sample"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == []


def test_forget_requires_yes(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["forget", "6ae5de7d"]) == EXIT_USAGE
    assert "refusing" in capsys.readouterr().err


def test_forget_removes(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["forget", "6ae5de7d", "--yes"]) == EXIT_OK
    assert "removed" in capsys.readouterr().out
    assert main(["show", "6ae5de7d"]) == EXIT_NOT_FOUND


def test_sync_is_gone_and_points_at_fetch(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["sync", "--full"]) == EXIT_USAGE
    assert "chatgpt-bridge fetch" in capsys.readouterr().err


def test_fetch_without_browser_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CHATGPT_BRIDGE_DATA_DIR", str(tmp_path / "data"))
    assert main(["--cdp-url", "http://127.0.0.1:1", "fetch", "some title"]) == EXIT_BROWSER
    assert "unreachable" in capsys.readouterr().err


def test_fetch_cached_chat_needs_no_browser(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--cdp-url", "http://127.0.0.1:1", "fetch", "6ae5de7d"]) == EXIT_OK
    assert "Fedora notes" in capsys.readouterr().out


def test_export_raw(seeded: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "dump.jsonl"
    assert main(["export-raw", "--out", str(out)]) == EXIT_OK
    assert len(out.read_text(encoding="utf-8").splitlines()) == 2
    assert out.stat().st_mode & 0o077 == 0


def test_reset_with_yes(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["reset", "--yes"]) == EXIT_OK
    assert "local store deleted" in capsys.readouterr().out


def test_reset_json(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "reset", "--yes"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == {"reset": True}


def test_send_dry_run_resolves_stored_chat(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["send", "6ae5de7d", "hello", "--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "would send to" in out
    assert "Fedora notes" in out


def test_send_requires_yes_without_a_terminal(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["send", "6ae5de7d", "hello"]) == EXIT_USAGE
    assert "refusing to send" in capsys.readouterr().err


def test_new_dry_run(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new", "hello", "--dry-run"]) == EXIT_OK
    assert "would create a new chat" in capsys.readouterr().out


def test_new_project_dry_run(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new-project", "  Copenhagen Trip  ", "--dry-run"]) == EXIT_OK
    assert "would create project 'Copenhagen Trip'" in capsys.readouterr().out


def test_new_project_requires_yes_without_a_terminal(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new-project", "Copenhagen Trip"]) == EXIT_USAGE
    assert "refusing to create a project" in capsys.readouterr().err


def test_new_project_creates(
    seeded: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from chatgpt_bridge.adapters.chatgpt import ProjectRef

    calls: list[str] = []

    def fake_create(name: str, *, session: object = None) -> ProjectRef:
        calls.append(name)
        return ProjectRef(name=name, project_id="g-p-abc")

    monkeypatch.setattr(cli_module.project_service, "create_project", fake_create)
    assert main(["new-project", "Copenhagen Trip", "--yes"]) == EXIT_OK
    out = capsys.readouterr()
    assert "Copenhagen Trip  (g-p-abc)" in out.out
    assert calls == ["Copenhagen Trip"]


def test_new_project_reports_a_failure(
    seeded: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from chatgpt_bridge.adapters.chatgpt import ProjectCreateFailed

    def fake_create(name: str, *, session: object = None) -> object:
        raise ProjectCreateFailed("the project did not appear after clicking Create")

    monkeypatch.setattr(cli_module.project_service, "create_project", fake_create)
    assert main(["new-project", "Trip", "--yes"]) == EXIT_ERROR
    assert "did not appear" in capsys.readouterr().err


def test_new_project_rejects_an_empty_name(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new-project", "   ", "--dry-run"]) == EXIT_USAGE
    assert "empty" in capsys.readouterr().err


def test_send_prints_the_reply(
    seeded: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_send(*_: object, **__: object) -> SendResult:
        return SendResult(outcome="sent", chat_id=UUID, url="u", reply="the answer")

    monkeypatch.setattr(cli_module.send_service, "send_message", fake_send)
    assert main(["send", "6ae5de7d", "hello", "--yes"]) == EXIT_OK
    assert "the answer" in capsys.readouterr().out


def test_send_unknown_returns_exit_10(
    seeded: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_send(*_: object, **__: object) -> SendResult:
        return SendResult(outcome="unknown", chat_id=UUID, url="u")

    monkeypatch.setattr(cli_module.send_service, "send_message", fake_send)
    assert main(["send", "6ae5de7d", "hello", "--yes"]) == EXIT_UNKNOWN
    assert "may have been sent" in capsys.readouterr().err


def test_config_show(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "--show"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "lookup_limit" in out and "cdp_url" in out


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


def _stub_adapter(monkeypatch: pytest.MonkeyPatch, projects=None, refs=None) -> None:
    from chatgpt_bridge.adapters.chatgpt import ProjectRef

    class _Adapter:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def list_projects(self):
            return projects or [
                ProjectRef("Test project"),
                ProjectRef("Empty project", has_chats=False),
            ]

        def list_project_chats(self, project: str, limit: int = 50):
            return refs or []

        def list_chats(self, scroll: bool = False, limit: int | None = None):
            return refs or []

    monkeypatch.setattr(cli_module, "Browser", _StubBrowser)
    monkeypatch.setattr(cli_module, "ChatGPTAdapter", _Adapter)


def test_projects_lists_names(
    seeded: Path, capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    _stub_adapter(monkeypatch)
    assert main(["projects"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Test project" in out
    assert "(no chats)" in out


def test_projects_json(seeded: Path, capsys: pytest.CaptureFixture[str], monkeypatch) -> None:
    _stub_adapter(monkeypatch)
    assert main(["--json", "projects"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["name"] == "Test project"
    assert payload[1]["has_chats"] is False


def test_chats_stored_scoped_to_project(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "chats", "--stored", "--project", "Other project"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == []


def test_chats_stored_project_empty_message(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["chats", "--stored", "--project", "Other project"]) == EXIT_OK
    assert "no stored chats in project 'Other project'" in capsys.readouterr().out


def test_search_scoped_to_project(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "search", "pkg-config", "--project", "Other project"]) == EXIT_OK
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []
    assert "in project 'Other project'" in captured.err


def test_new_dry_run_mentions_the_project(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new", "hello", "--dry-run", "--project", "Test project"]) == EXIT_OK
    assert "would create in project 'Test project'" in capsys.readouterr().out


def test_send_dry_run_scoped_to_project_shows_project_in_label(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["send", "6ae5de7d", "hello", "--dry-run", "--project", "Test project"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "would send to" in out
