import logging
from pathlib import Path

import pytest

from chatgpt_bridge import config, logging_setup
from chatgpt_bridge.cli import main


def test_config_show_lists_settings_and_sources(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "--show"]) == 0
    out = capsys.readouterr().out
    assert "cdp_url" in out and "[default]" in out
    assert "data_dir" in out and "[env]" in out
    assert "log_file" in out


def test_flag_source_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--cdp-url", "http://127.0.0.1:9444", "config", "--show"]) == 0
    line = next(x for x in capsys.readouterr().out.splitlines() if x.startswith("cdp_url"))
    assert "http://127.0.0.1:9444" in line and "[flag]" in line


def test_config_without_show_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config"]) == 2
    assert "usage" in capsys.readouterr().err


def test_bad_config_file_exits_1(isolated_dirs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (isolated_dirs / "config.toml").write_text('[browser]\ncdp_url = "ftp://x"\n', encoding="utf-8")
    assert main(["version"]) == 1
    assert "cdp_url must start" in capsys.readouterr().err


def test_logging_writes_to_file_not_stdout(
    isolated_dirs: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["version"])
    assert capsys.readouterr().out.strip().startswith("chatgpt-bridge ")
    logging.getLogger(logging_setup.LOGGER_NAME).info("probe message")
    log_path = isolated_dirs / "logs" / logging_setup.LOG_FILE_NAME
    assert log_path.exists()
    assert log_path.stat().st_mode & 0o077 == 0


def test_setup_logging_closes_the_previous_handler(isolated_dirs: Path) -> None:
    settings = config.load(env={}).settings
    logging_setup.setup_logging(settings)
    previous = logging.getLogger(logging_setup.LOGGER_NAME).handlers[0]
    logging_setup.setup_logging(settings)
    assert previous.stream is None


def test_log_rollover_keeps_every_file_private(
    isolated_dirs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(logging_setup, "MAX_BYTES", 100)
    settings = config.load(env={}).settings
    logging_setup.setup_logging(settings)
    logger = logging.getLogger(logging_setup.LOGGER_NAME)
    for index in range(30):
        logger.info("line %d %s", index, "x" * 20)

    log_dir = isolated_dirs / "logs"
    files = list(log_dir.iterdir())
    assert len(files) > 1
    assert all(path.stat().st_mode & 0o077 == 0 for path in files)
