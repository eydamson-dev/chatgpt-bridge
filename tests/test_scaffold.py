import pytest

from chatgpt_bridge import __version__
from chatgpt_bridge.cli import main
from chatgpt_bridge.mcp_server import mcp


def test_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == f"chatgpt-bridge {__version__}"


def test_no_command_prints_help_and_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage: chatgpt-bridge" in capsys.readouterr().out


def test_mcp_server_is_named() -> None:
    assert mcp.name == "chatgpt-bridge"
