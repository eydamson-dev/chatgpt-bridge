import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from chatgpt_bridge import logging_setup, paths


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Keep every test away from the real config, data, and log directories."""
    monkeypatch.setenv("CHATGPT_BRIDGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CHATGPT_BRIDGE_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("CHATGPT_BRIDGE_CDP_URL", raising=False)
    monkeypatch.setattr(paths, "log_dir", lambda: tmp_path / "logs")
    yield tmp_path
    logger = logging.getLogger(logging_setup.LOGGER_NAME)
    for handler in list(logger.handlers):
        handler.close()
    logger.handlers.clear()
