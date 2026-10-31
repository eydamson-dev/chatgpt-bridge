import logging
from pathlib import Path

import pytest

from chatgpt_bridge import logging_setup
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
    assert (isolated_dirs / "logs" / logging_setup.LOG_FILE_NAME).exists()
