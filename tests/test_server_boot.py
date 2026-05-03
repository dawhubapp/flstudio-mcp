"""Tests for server boot hooks (MIDI script auto-install)."""

from __future__ import annotations

from pathlib import Path

import pytest

from flstudio_mcp import server
from flstudio_mcp.installer import midi_script as midi


@pytest.fixture(autouse=True)
def _isolate_hardware_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    hw = tmp_path / "Hardware"
    hw.mkdir()
    monkeypatch.setenv("FLSTUDIO_MCP_HARDWARE_DIR", str(hw))
    monkeypatch.delenv(server.NO_AUTO_INSTALL_ENV, raising=False)
    return hw


def test_auto_install_returns_result(_isolate_hardware_dir: Path) -> None:
    result = server.auto_install_midi_script()
    assert result is not None
    assert result.action == midi.InstallAction.INSTALLED
    assert result.target_path.parent == _isolate_hardware_dir / midi.SCRIPT_SUBDIR


def test_auto_install_noop_after_first_run(_isolate_hardware_dir: Path) -> None:
    server.auto_install_midi_script()
    second = server.auto_install_midi_script()
    assert second is not None
    assert second.action == midi.InstallAction.NOOP


def test_auto_install_disabled_via_env(
    monkeypatch: pytest.MonkeyPatch, _isolate_hardware_dir: Path
) -> None:
    monkeypatch.setenv(server.NO_AUTO_INSTALL_ENV, "1")
    assert server.auto_install_midi_script() is None
    assert not (_isolate_hardware_dir / midi.SCRIPT_FILENAME).exists()


def test_install_result_extends_instructions(_isolate_hardware_dir: Path) -> None:
    result = server.auto_install_midi_script()
    instance = server.build_server(install_result=result)
    text = instance.instructions or ""
    assert "Update MIDI scripts" in text
    assert "flstudio-mcp" in text


def test_noop_does_not_extend_instructions(_isolate_hardware_dir: Path) -> None:
    server.auto_install_midi_script()
    second = server.auto_install_midi_script()
    instance = server.build_server(install_result=second)
    assert (instance.instructions or "").endswith("macOS only in v1.")


def test_missing_hardware_dir_warns_in_instructions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FLSTUDIO_MCP_HARDWARE_DIR", str(tmp_path / "absent"))
    result = server.auto_install_midi_script()
    assert result is not None
    assert result.action == midi.InstallAction.HARDWARE_DIR_MISSING
    instance = server.build_server(install_result=result)
    assert "Hardware dir was not found" in (instance.instructions or "")


def test_install_failure_swallowed(
    monkeypatch: pytest.MonkeyPatch, _isolate_hardware_dir: Path, capsys
) -> None:
    def boom(**_kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(midi, "install_midi_script", boom)
    assert server.auto_install_midi_script() is None
    captured = capsys.readouterr()
    assert "MIDI script install failed" in captured.err
