from pathlib import Path

import pytest

from chatgpt_bridge import config


def write_toml(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_when_no_file_or_env(tmp_path: Path) -> None:
    resolved = config.load(config_path=tmp_path / "missing.toml", env={})
    s = resolved.settings
    assert s.cdp_url == "http://127.0.0.1:9222"
    assert (s.min_delay_s, s.max_delay_s) == (2.0, 4.0)
    assert s.lookup_limit == 50
    assert s.log_level == "info"
    settings_sources = {k: v for k, v in resolved.sources.items() if k != "config_file"}
    assert set(settings_sources.values()) == {"default"}


def test_file_overrides_defaults(tmp_path: Path) -> None:
    cfg = write_toml(
        tmp_path / "config.toml",
        '[browser]\ncdp_url = "http://127.0.0.1:9333"\n\n'
        "[pacing]\nmin_delay_s = 1.0\nmax_delay_s = 1.5\n",
    )
    resolved = config.load(config_path=cfg, env={})
    assert resolved.settings.cdp_url == "http://127.0.0.1:9333"
    assert resolved.settings.min_delay_s == 1.0
    assert resolved.sources["cdp_url"] == "file"
    assert resolved.sources["lookup_limit"] == "default"


def test_env_overrides_file(tmp_path: Path) -> None:
    cfg = write_toml(tmp_path / "config.toml", '[browser]\ncdp_url = "http://a:1"\n')
    resolved = config.load(
        config_path=cfg,
        env={"CHATGPT_BRIDGE_CDP_URL": "http://b:2"},
    )
    assert resolved.settings.cdp_url == "http://b:2"
    assert resolved.sources["cdp_url"] == "env"


def test_flag_overrides_env(tmp_path: Path) -> None:
    resolved = config.load(
        flags={"cdp_url": "http://c:3"},
        config_path=tmp_path / "none.toml",
        env={"CHATGPT_BRIDGE_CDP_URL": "http://b:2"},
    )
    assert resolved.settings.cdp_url == "http://c:3"
    assert resolved.sources["cdp_url"] == "flag"


def test_none_flags_are_ignored(tmp_path: Path) -> None:
    resolved = config.load(flags={"cdp_url": None}, config_path=tmp_path / "none.toml", env={})
    assert resolved.settings.cdp_url == "http://127.0.0.1:9222"


def test_data_dir_env_is_expanded(tmp_path: Path) -> None:
    resolved = config.load(
        config_path=tmp_path / "none.toml",
        env={"CHATGPT_BRIDGE_DATA_DIR": str(tmp_path / "data")},
    )
    assert resolved.settings.data_dir == tmp_path / "data"
    assert resolved.sources["data_dir"] == "env"


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    cfg = write_toml(tmp_path / "config.toml", '[browser]\ncdp_url = "http://a:1"\nbogus = 1\n')
    with pytest.raises(config.ConfigError, match="browser.bogus"):
        config.load(config_path=cfg, env={})


def test_unknown_section_is_rejected(tmp_path: Path) -> None:
    cfg = write_toml(tmp_path / "config.toml", "[nope]\nx = 1\n")
    with pytest.raises(config.ConfigError, match="nope"):
        config.load(config_path=cfg, env={})


def test_invalid_toml_is_reported(tmp_path: Path) -> None:
    cfg = write_toml(tmp_path / "config.toml", "[browser\n")
    with pytest.raises(config.ConfigError, match="not valid TOML"):
        config.load(config_path=cfg, env={})


@pytest.mark.parametrize(
    "text, message",
    [
        ('[browser]\ncdp_url = "ws://x"\n', "cdp_url must start"),
        ("[pacing]\nmin_delay_s = 5\nmax_delay_s = 1\n", "pacing requires"),
        ("[pacing]\nmin_delay_s = -1\n", "pacing requires"),
        ("[lookup]\nlimit = 0\n", "lookup.limit"),
        ('[logging]\nlevel = "trace"\n', "logging.level"),
    ],
)
def test_out_of_range_values_are_rejected(tmp_path: Path, text: str, message: str) -> None:
    cfg = write_toml(tmp_path / "config.toml", text)
    with pytest.raises(config.ConfigError, match=message):
        config.load(config_path=cfg, env={})


def test_config_file_source_from_env(tmp_path: Path) -> None:
    cfg = write_toml(tmp_path / "alt.toml", "")
    resolved = config.load(env={"CHATGPT_BRIDGE_CONFIG": str(cfg)})
    assert resolved.settings.config_file == cfg
    assert resolved.sources["config_file"] == "env"
