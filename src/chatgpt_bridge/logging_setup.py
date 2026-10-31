from __future__ import annotations

import contextlib
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from chatgpt_bridge import paths
from chatgpt_bridge.config import Settings

LOGGER_NAME = "chatgpt_bridge"
LOG_FILE_NAME = "chatgpt-bridge.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3


def log_file() -> Path:
    return paths.log_dir() / LOG_FILE_NAME


class _PrivateRotatingFileHandler(RotatingFileHandler):
    """Rotating handler that keeps the new file private after every rollover."""

    def doRollover(self) -> None:
        super().doRollover()
        with contextlib.suppress(OSError):
            Path(self.baseFilename).chmod(0o600)


def setup_logging(settings: Settings) -> Path:
    """Attach a rotating file handler. Never logs to stdout, so CLI output stays clean."""
    path = log_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    for existing in list(logger.handlers):
        existing.close()
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if settings.log_level == "debug" else logging.INFO)
    logger.propagate = False

    handler = _PrivateRotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    # Logs can name chats and paths, so keep them user-only like the database.
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
