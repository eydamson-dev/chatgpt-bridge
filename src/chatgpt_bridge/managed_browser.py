"""The managed background browser: a Chromium in a Docker container.

The tool never owns the browser's lifetime. Docker does. This module starts,
stops, and inspects the container, and reports whether its CDP endpoint answers.
The profile lives on a bind mount, so the ChatGPT session survives restarts.

CDP is bound to loopback only. Anyone who can reach that port controls the
logged-in session, so it is never published on 0.0.0.0.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass

from chatgpt_bridge.browser import BrowserUnreachable, probe
from chatgpt_bridge.config import Settings

CONTAINER = "chatgpt-bridge-browser"
START_TIMEOUT_S = 60.0
READY_POLL_S = 1.0


class ManagedBrowserError(RuntimeError):
    """The managed browser could not be started, stopped, or inspected."""


@dataclass(frozen=True)
class ManagedStatus:
    running: bool
    cdp_url: str
    gui_url: str
    profile_dir: str
    image: str
    browser: str | None = None
    detail: str | None = None


def cdp_url(settings: Settings) -> str:
    return f"http://127.0.0.1:{settings.managed_cdp_port}"


def gui_url(settings: Settings) -> str:
    return f"http://127.0.0.1:{settings.managed_gui_port}"


def status(settings: Settings) -> ManagedStatus:
    running = _container_running()
    browser: str | None = None
    detail: str | None = None
    if running:
        try:
            browser = probe(cdp_url(settings), timeout_s=3.0).browser
        except BrowserUnreachable as exc:
            detail = str(exc).splitlines()[0]
    return ManagedStatus(
        running=running,
        cdp_url=cdp_url(settings),
        gui_url=gui_url(settings),
        profile_dir=str(settings.managed_profile_dir),
        image=settings.managed_image,
        browser=browser,
        detail=detail,
    )


def start(settings: Settings) -> ManagedStatus:
    """Start the container and wait for CDP. Idempotent."""
    profile = settings.managed_profile_dir
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    if _container_exists():
        _docker("start", CONTAINER)
    else:
        _docker(
            "run",
            "-d",
            "--name",
            CONTAINER,
            "--shm-size=1g",
            "-p",
            f"127.0.0.1:{settings.managed_cdp_port}:9222",
            "-p",
            f"127.0.0.1:{settings.managed_gui_port}:5800",
            "-e",
            "CHROMIUM_REMOTE_DEBUGGING=1",
            "-e",
            f"USER_ID={os.getuid()}",
            "-e",
            f"GROUP_ID={os.getgid()}",
            "-v",
            f"{profile}:/config",
            settings.managed_image,
        )
    _wait_ready(settings)
    return status(settings)


def stop() -> None:
    """Stop the container. The profile is untouched."""
    if not _container_exists():
        return
    _docker("stop", CONTAINER)


def restart(settings: Settings) -> ManagedStatus:
    stop()
    return start(settings)


def _wait_ready(settings: Settings) -> None:
    deadline = time.monotonic() + START_TIMEOUT_S
    last: str | None = None
    while time.monotonic() < deadline:
        try:
            probe(cdp_url(settings), timeout_s=2.0)
            return
        except BrowserUnreachable as exc:
            last = str(exc).splitlines()[0]
            time.sleep(READY_POLL_S)
    raise ManagedBrowserError(
        f"the browser did not answer at {cdp_url(settings)} within "
        f"{START_TIMEOUT_S:.0f}s: {last or 'no response'}"
    )


def _container_exists() -> bool:
    result = _docker("inspect", "-f", "{{.Id}}", CONTAINER, check=False)
    return result.returncode == 0


def _container_running() -> bool:
    result = _docker("inspect", "-f", "{{.State.Running}}", CONTAINER, check=False)
    if result.returncode != 0:
        return False
    return result.stdout.strip() == "true"


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    binary = shutil.which("docker")
    if binary is None:
        raise ManagedBrowserError(
            "docker is not installed or not on PATH; the managed browser needs it"
        )
    try:
        result = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ManagedBrowserError(f"docker {args[0]} failed: {exc}") from exc
    if check and result.returncode != 0:
        message = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
        raise ManagedBrowserError(f"docker {args[0]} failed: {message}")
    return result


def health(settings: Settings) -> dict[str, object]:
    """Machine-readable shape for `browser status --json`."""
    current = status(settings)
    return {
        "running": current.running,
        "container": CONTAINER,
        "cdp_url": current.cdp_url,
        "gui_url": current.gui_url,
        "profile_dir": current.profile_dir,
        "image": current.image,
        "browser": current.browser,
        "detail": current.detail,
    }
