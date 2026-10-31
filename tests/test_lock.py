from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

import pytest

from chatgpt_bridge import lock


def test_lock_is_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "sync.lock"

    def _nested() -> None:
        with lock.sync_lock(path):
            pass

    with lock.sync_lock(path), pytest.raises(lock.LockBusy, match="already running"):
        _nested()


def test_lock_released_after_use(tmp_path: Path) -> None:
    path = tmp_path / "sync.lock"
    with lock.sync_lock(path):
        assert lock.is_locked(path)
    assert not lock.is_locked(path)


def test_lock_released_on_exception(tmp_path: Path) -> None:
    path = tmp_path / "sync.lock"
    with pytest.raises(RuntimeError), lock.sync_lock(path):
        raise RuntimeError("boom")
    assert not lock.is_locked(path)


def test_is_locked_without_file(tmp_path: Path) -> None:
    assert not lock.is_locked(tmp_path / "missing.lock")


def test_owner_pid_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sync.lock"
    path.write_text("12345", encoding="utf-8")
    assert lock.owner_pid(path) == 12345
    path.write_text("not a pid", encoding="utf-8")
    assert lock.owner_pid(path) is None


def _hold(path: str, seconds: float, started: object) -> None:
    with lock.sync_lock(Path(path)):
        started.set()  # type: ignore[attr-defined]
        time.sleep(seconds)


def test_second_process_is_blocked(tmp_path: Path) -> None:
    path = tmp_path / "sync.lock"
    ctx = multiprocessing.get_context("spawn")
    started = ctx.Event()
    process = ctx.Process(target=_hold, args=(str(path), 1.5, started))
    process.start()
    try:
        assert started.wait(timeout=10)
        with pytest.raises(lock.LockBusy), lock.sync_lock(path):
            pass
    finally:
        process.join(timeout=10)
    with lock.sync_lock(path):
        assert True
