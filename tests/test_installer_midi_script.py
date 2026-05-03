"""Tests for the MIDI script auto-installer."""

from __future__ import annotations

from pathlib import Path

import pytest

from flstudio_mcp.installer import midi_script


def test_bundled_script_path_exists() -> None:
    path = midi_script.bundled_script_path()
    assert path.exists()
    assert path.name == midi_script.SCRIPT_FILENAME
    assert "# name=flstudio-mcp" in path.read_text(encoding="utf-8").splitlines()[0]


def test_install_when_target_missing(tmp_path: Path) -> None:
    hw_dir = tmp_path / "Hardware"
    hw_dir.mkdir()
    result = midi_script.install_midi_script(hardware_dir_path=hw_dir)
    assert result.action == midi_script.InstallAction.INSTALLED
    assert result.target_path.exists()
    assert result.installed_hash is None
    assert result.bundled_hash == midi_script.file_sha256(midi_script.bundled_script_path())


def test_noop_when_already_up_to_date(tmp_path: Path) -> None:
    hw_dir = tmp_path / "Hardware"
    hw_dir.mkdir()
    midi_script.install_midi_script(hardware_dir_path=hw_dir)
    second = midi_script.install_midi_script(hardware_dir_path=hw_dir)
    assert second.action == midi_script.InstallAction.NOOP


def test_update_when_hash_differs(tmp_path: Path) -> None:
    hw_dir = tmp_path / "Hardware"
    hw_dir.mkdir()
    target = hw_dir / midi_script.SCRIPT_FILENAME
    target.write_text("# stale", encoding="utf-8")
    result = midi_script.install_midi_script(hardware_dir_path=hw_dir)
    assert result.action == midi_script.InstallAction.UPDATED
    assert result.installed_hash is not None
    assert result.installed_hash != result.bundled_hash


def test_hardware_dir_missing(tmp_path: Path) -> None:
    hw_dir = tmp_path / "nope"
    result = midi_script.install_midi_script(hardware_dir_path=hw_dir)
    assert result.action == midi_script.InstallAction.HARDWARE_DIR_MISSING
    assert not result.target_path.exists()


def test_hardware_dir_created_with_flag(tmp_path: Path) -> None:
    hw_dir = tmp_path / "fresh" / "Hardware"
    result = midi_script.install_midi_script(hardware_dir_path=hw_dir, create_hardware_dir=True)
    assert result.action == midi_script.InstallAction.INSTALLED
    assert hw_dir.exists()


def test_symlink_preferred(tmp_path: Path) -> None:
    hw_dir = tmp_path / "Hardware"
    hw_dir.mkdir()
    result = midi_script.install_midi_script(hardware_dir_path=hw_dir, prefer_symlink=True)
    assert result.used_symlink is True
    assert result.target_path.is_symlink()


def test_symlink_fallback_to_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hw_dir = tmp_path / "Hardware"
    hw_dir.mkdir()

    real_symlink_to = Path.symlink_to

    def boom(self, target):
        raise OSError("symlink not permitted")

    monkeypatch.setattr(Path, "symlink_to", boom)
    try:
        result = midi_script.install_midi_script(hardware_dir_path=hw_dir, prefer_symlink=True)
    finally:
        monkeypatch.setattr(Path, "symlink_to", real_symlink_to)
    assert result.used_symlink is False
    assert result.target_path.exists()
    assert not result.target_path.is_symlink()


def test_version_stamp_written(tmp_path: Path) -> None:
    hw_dir = tmp_path / "Hardware"
    hw_dir.mkdir()
    result = midi_script.install_midi_script(hardware_dir_path=hw_dir)
    stamp = midi_script.installed_version_stamp(result.target_path)
    assert stamp is not None
    assert stamp["script_sha256"] == result.bundled_hash
    assert "package_version" in stamp


def test_env_var_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLSTUDIO_MCP_HARDWARE_DIR", str(tmp_path))
    assert midi_script.hardware_dir() == tmp_path


def test_install_creates_runtime_dirs(tmp_path: Path) -> None:
    """FL sandbox can't makedirs under ~/Documents — installer must do it."""
    hw_dir = tmp_path / "Hardware"
    hw_dir.mkdir()
    result = midi_script.install_midi_script(hardware_dir_path=hw_dir)

    runtime_root = hw_dir / midi_script.RUNTIME_SUBDIR
    for leaf in midi_script.RUNTIME_LEAF_DIRS:
        assert (runtime_root / leaf).is_dir(), f"missing {leaf}"
    assert len(result.runtime_dirs) == len(midi_script.RUNTIME_LEAF_DIRS)


def test_install_runtime_dirs_are_idempotent(tmp_path: Path) -> None:
    hw_dir = tmp_path / "Hardware"
    hw_dir.mkdir()
    midi_script.install_midi_script(hardware_dir_path=hw_dir)
    # Drop a sentinel inside inbox/ — ensure second install doesn't wipe it
    sentinel = hw_dir / midi_script.RUNTIME_SUBDIR / "inbox" / ".sentinel"
    sentinel.write_text("keep me", encoding="utf-8")

    midi_script.install_midi_script(hardware_dir_path=hw_dir)
    assert sentinel.read_text(encoding="utf-8") == "keep me"


def test_runtime_dirs_reported_on_noop(tmp_path: Path) -> None:
    hw_dir = tmp_path / "Hardware"
    hw_dir.mkdir()
    midi_script.install_midi_script(hardware_dir_path=hw_dir)
    second = midi_script.install_midi_script(hardware_dir_path=hw_dir)
    assert second.action == midi_script.InstallAction.NOOP
    assert len(second.runtime_dirs) == len(midi_script.RUNTIME_LEAF_DIRS)


def test_ensure_runtime_dirs_returns_paths(tmp_path: Path) -> None:
    out = midi_script.ensure_runtime_dirs(tmp_path)
    assert all(p.is_dir() for p in out)
    assert {p.name for p in out} == set(midi_script.RUNTIME_LEAF_DIRS)
