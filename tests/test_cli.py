from __future__ import annotations

import json
from pathlib import Path

import pytest

from chatgpt_bridge import store
from chatgpt_bridge.cli import (
    EXIT_BROWSER,
    EXIT_ERROR,
    EXIT_LOCKED,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_USAGE,
    main,
)


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
                title="Other",
                url="https://chatgpt.com/c/c2",
                section="pinned",
                sidebar_rank=1,
            ),
        ],
        seen_at=store.utc_now(),
    )
    turns = [
        store.Turn(role="user", markdown="install **pkg-config**", text="install pkg-config"),
        store.Turn(role="assistant", markdown="run `dnf`", text="run dnf"),
    ]
    store.save_chat(conn, "c1", turns, store.content_hash(turns))
    conn.close()
    return tmp_path


def test_version() -> None:
    assert main(["version"]) == EXIT_OK


def test_status_json(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "status"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["chats"] == 2
    assert payload["fetched"] == 1


def test_status_text(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["status"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "chats         2 (1 fetched" in out


def test_list_json(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "list"]) == EXIT_OK
    rows = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in rows] == ["c1", "c2"]


def test_list_section_filter(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "list", "--section", "pinned"]) == EXIT_OK
    rows = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in rows] == ["c2"]


def test_list_empty_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CHATGPT_BRIDGE_DATA_DIR", str(tmp_path / "data"))
    assert main(["list"]) == EXIT_OK
    assert "no chats cached" in capsys.readouterr().out


def test_show_text(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "c1"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Fedora notes" in out
    assert "[1] You" in out


def test_show_markdown(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "c1", "--format", "md"]) == EXIT_OK
    assert capsys.readouterr().out.startswith("# Fedora notes")


def test_show_json(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "c1", "--format", "json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == "c1"
    assert len(payload["turns"]) == 2


def test_show_accepts_url(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "https://chatgpt.com/c/c1", "--format", "json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["id"] == "c1"


def test_show_missing_chat_exits_5(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "nope"]) == EXIT_NOT_FOUND
    assert "not in the cache" in capsys.readouterr().err


def test_search_text_and_json(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["search", "pkg-config"]) == EXIT_OK
    assert "pkg-config" in capsys.readouterr().out

    assert main(["--json", "search", "dnf"]) == EXIT_OK
    hits = json.loads(capsys.readouterr().out)
    assert hits[0]["chat_id"] == "c1"


def test_search_no_matches(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["search", "zzzzzz"]) == EXIT_OK
    assert "no matches" in capsys.readouterr().out


def test_export_raw_writes_file(
    seeded: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "dump.jsonl"
    assert main(["export-raw", "--out", str(out)]) == EXIT_OK
    assert out.exists()
    assert "wrote" in capsys.readouterr().out


def test_reset_requires_yes_when_not_tty(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["reset"]) == EXIT_USAGE
    assert "refusing" in capsys.readouterr().err


def test_reset_with_yes(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["reset", "--yes"]) == EXIT_OK
    assert "cache deleted" in capsys.readouterr().out


def test_sync_without_browser_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CHATGPT_BRIDGE_DATA_DIR", str(tmp_path / "data"))
    assert main(["--cdp-url", "http://127.0.0.1:1", "sync"]) == EXIT_BROWSER
    assert "unreachable" in capsys.readouterr().err


def test_bad_config_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = tmp_path / "bad.toml"
    cfg.write_text("[nope]\nx = 1\n", encoding="utf-8")
    assert main(["--config", str(cfg), "version"]) == EXIT_ERROR
    assert "unknown settings" in capsys.readouterr().err


def test_lock_busy_exits_7(
    seeded: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from chatgpt_bridge import lock, paths

    with lock.sync_lock(paths.lock_path()):
        assert main(["sync"]) == EXIT_LOCKED
    assert "already running" in capsys.readouterr().err
