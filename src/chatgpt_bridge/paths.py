from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_log_dir

APP_NAME = "chatgpt-bridge"
DATA_DIR_ENV = "CHATGPT_BRIDGE_DATA_DIR"
CONFIG_FILE_ENV = "CHATGPT_BRIDGE_CONFIG"


def data_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    override = env.get(DATA_DIR_ENV)
    return Path(override).expanduser() if override else Path(user_data_dir(APP_NAME))


def config_file(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    override = env.get(CONFIG_FILE_ENV)
    if override:
        return Path(override).expanduser()
    return Path(user_config_dir(APP_NAME)) / "config.toml"


def log_dir() -> Path:
    return Path(user_log_dir(APP_NAME))


def db_path() -> Path:
    return data_dir() / "bridge.db"


def lock_path() -> Path:
    return data_dir() / "sync.lock"
