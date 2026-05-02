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


def test_main_no_args_returns_nonzero_until_phase_1_2_1(capsys) -> None:
    rc = server.main([])
    captured = capsys.readouterr()
    assert rc == 1
    assert "Phase 1.2.1" in captured.err


def test_console_script_installed() -> None:
    """`flstudio-mcp --version` runs via the entry point."""
    result = subprocess.run(
        [sys.executable, "-m", "flstudio_mcp.server", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert flstudio_mcp.__version__ in result.stdout
