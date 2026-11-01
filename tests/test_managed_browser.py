from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chatgpt_bridge import managed_browser
from chatgpt_bridge.browser import BrowserUnreachable, BrowserVersion
from chatgpt_bridge.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(managed_profile_dir=tmp_path / "browser")


def _completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    recorded: list[tuple[str, ...]] = []

    def fake_docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        recorded.append(args)
        if args[0] == "inspect":
            return _completed(returncode=1)  # container does not exist
        return _completed(stdout="abc123")

    monkeypatch.setattr(managed_browser, "_docker", fake_docker)
    monkeypatch.setattr(
        managed_browser,
        "probe",
        lambda url, timeout_s=2.0: BrowserVersion(browser="Chrome/155", websocket_url="ws://x"),
    )
    return recorded


def test_start_publishes_cdp_on_loopback_only(tmp_path: Path, calls: list[tuple[str, ...]]) -> None:
    managed_browser.start(_settings(tmp_path))
    run = next(call for call in calls if call[0] == "run")
    joined = " ".join(run)
    assert "127.0.0.1:9223:9222" in joined
    assert "127.0.0.1:5801:5800" in joined
    assert "0.0.0.0" not in joined
    assert "CHROMIUM_REMOTE_DEBUGGING=1" in run
    assert "--shm-size=1g" in run


def test_start_mounts_the_profile(tmp_path: Path, calls: list[tuple[str, ...]]) -> None:
    settings = _settings(tmp_path)
    managed_browser.start(settings)
    run = next(call for call in calls if call[0] == "run")
    assert f"{settings.managed_profile_dir}:/config" in run
    assert settings.managed_profile_dir.is_dir()
    assert (settings.managed_profile_dir.stat().st_mode & 0o777) == 0o700


def test_start_reuses_an_existing_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[tuple[str, ...]] = []

    def fake_docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        recorded.append(args)
        if args[0] == "inspect":
            return _completed(returncode=0, stdout="abc123")
        return _completed()

    monkeypatch.setattr(managed_browser, "_docker", fake_docker)
    monkeypatch.setattr(
        managed_browser, "probe", lambda url, timeout_s=2.0: BrowserVersion("Chrome/1", "ws")
    )
    managed_browser.start(_settings(tmp_path))
    assert recorded[1] == ("start", managed_browser.CONTAINER)
    assert not any(call[0] == "run" for call in recorded)


def test_start_reports_a_browser_that_never_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(managed_browser, "_docker", lambda *a, check=True: _completed())
    monkeypatch.setattr(managed_browser, "START_TIMEOUT_S", 0.2)
    monkeypatch.setattr(managed_browser, "READY_POLL_S", 0.05)

    def unreachable(url: str, timeout_s: float = 2.0) -> BrowserVersion:
        raise BrowserUnreachable("no answer")

    monkeypatch.setattr(managed_browser, "probe", unreachable)
    with pytest.raises(managed_browser.ManagedBrowserError, match="did not answer"):
        managed_browser.start(_settings(tmp_path))


def test_status_reports_a_stopped_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(managed_browser, "_docker", lambda *a, check=True: _completed(returncode=1))
    current = managed_browser.status(_settings(tmp_path))
    assert current.running is False
    assert current.browser is None


def test_status_reports_a_running_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return _completed(returncode=0, stdout="true\n")

    monkeypatch.setattr(managed_browser, "_docker", fake_docker)
    monkeypatch.setattr(
        managed_browser, "probe", lambda url, timeout_s=3.0: BrowserVersion("Chrome/155", "ws")
    )
    current = managed_browser.status(_settings(tmp_path))
    assert current.running is True
    assert current.browser == "Chrome/155"


def test_stop_does_nothing_when_the_container_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[str, ...]] = []

    def fake_docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        recorded.append(args)
        return _completed(returncode=1)  # inspect says the container is absent

    monkeypatch.setattr(managed_browser, "_docker", fake_docker)
    managed_browser.stop()
    assert not any(call[0] == "stop" for call in recorded)


def test_docker_missing_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(managed_browser.shutil, "which", lambda name: None)
    with pytest.raises(managed_browser.ManagedBrowserError, match="docker is not installed"):
        managed_browser.start(_settings(tmp_path))
