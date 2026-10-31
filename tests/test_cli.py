from __future__ import annotations

import json
from pathlib import Path

import pytest

from chatgpt_bridge import store
from chatgpt_bridge.cli import (
    EXIT_BROWSER,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_USAGE,
    main,
)

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
        title="Lenovo ThinkPad Review",
        url="https://chatgpt.com/c/aaaa1111-2222-3333-4444-555566667777",
        section="recents",
        turns=[store.Turn(role="user", markdown="T14", text="T14")],
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
    assert "cached chats 2" in capsys.readouterr().out


def test_chats_cached(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "chats", "--cached"]) == EXIT_OK
    rows = json.loads(capsys.readouterr().out)
    assert {r["title"] for r in rows} == {"Fedora notes", "Lenovo ThinkPad Review"}


def test_chats_cached_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CHATGPT_BRIDGE_DATA_DIR", str(tmp_path / "data"))
    assert main(["chats", "--cached"]) == EXIT_OK
    assert "no cached chats" in capsys.readouterr().out


def test_show_text_by_prefix(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "6ae5de7d"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Fedora notes" in out
    assert "[1] You" in out


def test_show_by_title(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "lenovo"]) == EXIT_OK
    assert "Lenovo ThinkPad Review" in capsys.readouterr().out


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
    assert "not in the cache" in err
    assert "chatgpt-bridge fetch" in err


def test_search_text_and_json(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["search", "pkg-config"]) == EXIT_OK
    assert "pkg-config" in capsys.readouterr().out

    assert main(["--json", "search", "dnf"]) == EXIT_OK
    hits = json.loads(capsys.readouterr().out)
    assert hits[0]["chat_id"] == UUID


def test_search_no_matches_mentions_cache_size(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["search", "zzzzzz"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "no matches in cached chats (2 cached)" in out


def test_search_scoped_to_chat(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "search", "pkg-config", "--chat", "lenovo"]) == EXIT_OK
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


def test_reset_with_yes(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["reset", "--yes"]) == EXIT_OK
    assert "cache deleted" in capsys.readouterr().out


def test_config_show(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "--show"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "lookup_limit" in out and "cdp_url" in out
