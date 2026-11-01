from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Literal

from chatgpt_bridge import paths

Source = Literal["default", "file", "env", "flag", "managed"]
LOG_LEVELS = ("info", "debug")
BROWSER_MODES = ("external", "managed")
CDP_URL_ENV = "CHATGPT_BRIDGE_CDP_URL"


class ConfigError(ValueError):
    """Raised when a configuration value is missing, malformed, or out of range."""


@dataclass(frozen=True)
class Settings:
    cdp_url: str = "http://127.0.0.1:9222"
    browser_mode: str = "external"
    managed_cdp_port: int = 9223
    managed_gui_port: int = 5801
    managed_image: str = "jlesage/chromium:latest"
    managed_profile_dir: Path = field(default_factory=paths.browser_dir)
    min_delay_s: float = 1.0
    max_delay_s: float = 2.0
    lookup_limit: int = 50
    log_level: str = "info"
    data_dir: Path = field(default_factory=paths.data_dir)
    config_file: Path = field(default_factory=paths.config_file)


# (section, key) in the TOML file -> Settings field name
_FILE_KEYS: dict[tuple[str, str], str] = {
    ("browser", "cdp_url"): "cdp_url",
    ("browser", "mode"): "browser_mode",
    ("browser", "managed_cdp_port"): "managed_cdp_port",
    ("browser", "managed_gui_port"): "managed_gui_port",
    ("browser", "managed_image"): "managed_image",
    ("browser", "managed_profile_dir"): "managed_profile_dir",
    ("pacing", "min_delay_s"): "min_delay_s",
    ("pacing", "max_delay_s"): "max_delay_s",
    ("lookup", "limit"): "lookup_limit",
    ("logging", "level"): "log_level",
}

_ENV_KEYS: dict[str, str] = {
    CDP_URL_ENV: "cdp_url",
    paths.DATA_DIR_ENV: "data_dir",
}


@dataclass(frozen=True)
class Resolved:
    settings: Settings
    sources: dict[str, Source]


def load(
    flags: dict[str, Any] | None = None,
    config_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> Resolved:
    """Resolve settings. Precedence: flags > environment > config file > defaults."""
    env = dict(os.environ) if env is None else dict(env)
    flags = {k: v for k, v in (flags or {}).items() if v is not None}

    path_source: Source
    if config_path is not None:
        path, path_source = config_path, "flag"
    elif env.get(paths.CONFIG_FILE_ENV):
        path, path_source = paths.config_file(env), "env"
    else:
        path, path_source = paths.config_file(env), "default"

    values: dict[str, Any] = {}
    sources: dict[str, Source] = {f.name: "default" for f in fields(Settings)}

    for name, value in _read_file(path).items():
        if name == "managed_profile_dir" and isinstance(value, str):
            value = Path(value).expanduser()
        values[name] = value
        sources[name] = "file"

    for env_name, field_name in _ENV_KEYS.items():
        if env.get(env_name):
            raw = env[env_name]
            values[field_name] = Path(raw).expanduser() if field_name == "data_dir" else raw
            sources[field_name] = "env"

    for name, value in flags.items():
        values[name] = value
        sources[name] = "flag"

    values["config_file"] = path
    sources["config_file"] = path_source
    if "data_dir" not in values:
        values["data_dir"] = paths.data_dir(env)

    settings = replace(Settings(), **values)
    # A managed browser owns its own endpoint. An explicit cdp_url still wins.
    if settings.browser_mode == "managed" and sources["cdp_url"] == "default":
        settings = replace(settings, cdp_url=f"http://127.0.0.1:{settings.managed_cdp_port}")
        sources["cdp_url"] = "managed"
    _validate(settings)
    return Resolved(settings=settings, sources=sources)


def _read_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config file {path} is not valid TOML: {exc}") from exc

    out: dict[str, Any] = {}
    for (section, key), field_name in _FILE_KEYS.items():
        table = raw.get(section, {})
        if isinstance(table, dict) and key in table:
            out[field_name] = table[key]
    unknown = _unknown_keys(raw)
    if unknown:
        raise ConfigError(f"unknown settings in {path}: {', '.join(unknown)}")
    return out


def _unknown_keys(raw: dict[str, Any]) -> list[str]:
    known_sections = {section for section, _ in _FILE_KEYS}
    found: list[str] = []
    for section, table in raw.items():
        if section not in known_sections:
            found.append(section)
            continue
        if isinstance(table, dict):
            for key in table:
                if (section, key) not in _FILE_KEYS:
                    found.append(f"{section}.{key}")
    return sorted(found)


def _validate(s: Settings) -> None:
    if not (s.cdp_url.startswith("http://") or s.cdp_url.startswith("https://")):
        raise ConfigError(f"cdp_url must start with http:// or https:// (got {s.cdp_url!r})")
    if s.browser_mode not in BROWSER_MODES:
        raise ConfigError(
            f"browser.mode must be one of {', '.join(BROWSER_MODES)} (got {s.browser_mode!r})"
        )
    for name, port in (
        ("managed_cdp_port", s.managed_cdp_port),
        ("managed_gui_port", s.managed_gui_port),
    ):
        if not 1 <= port <= 65535:
            raise ConfigError(f"browser.{name} must be a TCP port (got {port})")
    if s.managed_cdp_port == s.managed_gui_port:
        raise ConfigError("browser.managed_cdp_port and managed_gui_port must differ")
    if s.min_delay_s < 0 or s.max_delay_s < s.min_delay_s:
        raise ConfigError("pacing requires 0 <= min_delay_s <= max_delay_s")
    if s.lookup_limit < 1:
        raise ConfigError("lookup.limit must be at least 1")
    if s.log_level not in LOG_LEVELS:
        raise ConfigError(f"logging.level must be one of {', '.join(LOG_LEVELS)}")
