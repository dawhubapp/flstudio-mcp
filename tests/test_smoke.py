"""Smoke tests verifying package metadata + entrypoint stub."""

from __future__ import annotations

import subprocess
import sys

import flstudio_mcp
from flstudio_mcp import server


def test_version_attribute() -> None:
    assert flstudio_mcp.__version__ == "0.1.0.dev0"


def test_main_version_flag(capsys) -> None:
    rc = server.main(["--version"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "flstudio-mcp" in captured.out
    assert flstudio_mcp.__version__ in captured.out


def test_main_help_flag(capsys) -> None:
    rc = server.main(["--help"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Usage:" in captured.out
    assert "stdio" in captured.out


def test_console_script_version() -> None:
    """`python -m flstudio_mcp.server --version` runs via the entry point."""
    result = subprocess.run(
        [sys.executable, "-m", "flstudio_mcp.server", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert flstudio_mcp.__version__ in result.stdout


def test_build_server_returns_fastmcp_instance() -> None:
    instance = server.build_server()
    assert instance.name == server.SERVER_NAME
    assert instance.instructions == server.SERVER_INSTRUCTIONS
