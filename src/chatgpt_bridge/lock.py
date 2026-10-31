from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from chatgpt_bridge.logging_setup import get_logger

log = get_logger("lock")

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


class LockBusy(RuntimeError):
    """Another sync holds the lock."""


@contextmanager
def sync_lock(path: Path, timeout_s: float = 0.0) -> Iterator[Path]:
    """Exclusive lock so only one sync runs at a time across CLI and MCP.

    The OS releases the lock when the process exits, so a crash cannot leave a
    stale lock behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        _acquire(handle, path, timeout_s)
        try:
            yield path
        finally:
            _release(handle)
    finally:
        handle.close()


def _acquire(handle: Any, path: Path, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        if _try_acquire(handle):
            return
        if time.monotonic() >= deadline:
            raise LockBusy(f"a sync is already running (lock: {path})")
        time.sleep(0.2)


def _try_acquire(handle: Any) -> bool:
    if fcntl is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    if msvcrt is not None:  # pragma: no cover - Windows
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    return True  # pragma: no cover - no locking primitive available


def _release(handle: Any) -> None:
    with contextlib.suppress(Exception):
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_UN)
        elif msvcrt is not None:  # pragma: no cover - Windows
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def is_locked(path: Path) -> bool:
    """Best-effort check used by `status`; never creates the lock file."""
    if not path.exists():
        return False
    try:
        with path.open("a+") as handle:
            if _try_acquire(handle):
                _release(handle)
                return False
            return True
    except OSError:  # pragma: no cover - permission problems
        return False


def owner_pid(path: Path) -> int | None:
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(content) if content.isdigit() else None


def write_owner(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.write_text(str(os.getpid()), encoding="utf-8")
