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


def test_auto_install_returns_results(_isolate_hardware_dir: Path) -> None:
    results = server.auto_install_midi_script()
    assert results is not None
    assert len(results) == 1  # env override → single dir
    assert results[0].action == midi.InstallAction.INSTALLED
    assert results[0].target_path.parent == _isolate_hardware_dir / midi.SCRIPT_SUBDIR


def test_auto_install_noop_after_first_run(_isolate_hardware_dir: Path) -> None:
    server.auto_install_midi_script()
    second = server.auto_install_midi_script()
    assert second is not None
    assert all(r.action == midi.InstallAction.NOOP for r in second)


def test_auto_install_disabled_via_env(
    monkeypatch: pytest.MonkeyPatch, _isolate_hardware_dir: Path
) -> None:
    monkeypatch.setenv(server.NO_AUTO_INSTALL_ENV, "1")
    assert server.auto_install_midi_script() is None
    assert not (_isolate_hardware_dir / midi.SCRIPT_FILENAME).exists()


def test_install_result_extends_instructions(_isolate_hardware_dir: Path) -> None:
    results = server.auto_install_midi_script()
    instance = server.build_server(install_result=results)
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
    results = server.auto_install_midi_script()
    assert results is not None
    assert results[0].action == midi.InstallAction.HARDWARE_DIR_MISSING
    instance = server.build_server(install_result=results)
    assert "Hardware dir was not found" in (instance.instructions or "")


def test_auto_install_to_all_fl_versions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When no env override is set, installer covers every detected FL version."""
    monkeypatch.delenv("FLSTUDIO_MCP_HARDWARE_DIR", raising=False)

    parent = tmp_path / "Image-Line"
    for ver in ("FL Studio", "FL Studio 21", "FL Studio 2024"):
        (parent / ver / "Settings" / "Hardware").mkdir(parents=True)
    monkeypatch.setattr(midi, "FL_USERDATA_PARENT", parent)

    results = server.auto_install_midi_script()
    assert results is not None
    assert len(results) == 3
    assert all(r.action == midi.InstallAction.INSTALLED for r in results)
    for r in results:
        assert r.target_path.exists()


def test_install_failure_swallowed(
    monkeypatch: pytest.MonkeyPatch, _isolate_hardware_dir: Path, capsys
) -> None:
    def boom(**_kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(midi, "install_midi_script", boom)
    assert server.auto_install_midi_script() is None
    captured = capsys.readouterr()
    assert "MIDI script install failed" in captured.err
