"""Tests for end-to-end FL Studio setup verifier."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from flstudio_mcp.installer import iac, midi_script, verify


@dataclass
class _FakeResult:
    status: str = "ok"
    detail: str = ""


@dataclass
class FakeRuntime:
    raise_timeout: bool = False
    raise_other: Exception | None = None
    status: str = "ok"
    detail: str = ""

    def noop(self, *, timeout_s: float = 3.0) -> _FakeResult:
        if self.raise_timeout:
            raise TimeoutError("no result for command")
        if self.raise_other is not None:
            raise self.raise_other
        return _FakeResult(status=self.status, detail=self.detail)


def _stub_iac(monkeypatch: pytest.MonkeyPatch, ok: bool = True) -> None:
    state = iac.IacState.ONLINE if ok else iac.IacState.OFFLINE
    monkeypatch.setattr(iac, "check_iac_status", lambda: iac.IacStatus(state, "stub"))


def _stub_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, install: bool = True) -> None:
    parent = tmp_path / "Image-Line"
    hw = parent / "FL Studio" / "Settings" / "Hardware"
    hw.mkdir(parents=True)
    if install:
        script_dir = hw / midi_script.SCRIPT_SUBDIR
        script_dir.mkdir()
        (script_dir / midi_script.SCRIPT_FILENAME).write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(midi_script, "FL_USERDATA_PARENT", parent)


def _stub_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fl_running: bool = True,
    process_name: str = "FL Studio 2025",
) -> None:
    """Patch verify.detect_fl_processes to a fake list."""
    procs: list[tuple[str, int]] = [(process_name, 1234)] if fl_running else []
    monkeypatch.setattr(verify, "detect_fl_processes", lambda: procs)


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #


def test_check_iac_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_iac(monkeypatch, ok=True)
    step = verify.check_iac()
    assert step.ok is True
    assert step.name == "iac_driver_online"


def test_check_iac_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_iac(monkeypatch, ok=False)
    step = verify.check_iac()
    assert step.ok is False


def test_check_script_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_install(monkeypatch, tmp_path, install=True)
    step = verify.check_script_installed()
    assert step.ok is True
    assert step.data is not None
    assert any(midi_script.SCRIPT_FILENAME in p for p in step.data["present"])


def test_check_script_not_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_install(monkeypatch, tmp_path, install=False)
    step = verify.check_script_installed()
    assert step.ok is False


def test_check_script_no_fl_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(midi_script, "FL_USERDATA_PARENT", tmp_path / "absent")
    step = verify.check_script_installed()
    assert step.ok is False
    assert "no FL Studio user-data dirs" in step.detail


def test_check_fl_running(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_run(monkeypatch, fl_running=True, process_name="FL Studio 2025")
    step = verify.check_fl_running()
    assert step.ok is True
    assert "FL Studio 2025" in step.detail
    assert step.data == {"processes": [{"name": "FL Studio 2025", "pid": 1234}]}


def test_check_fl_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_run(monkeypatch, fl_running=False)
    step = verify.check_fl_running()
    assert step.ok is False
    assert "FL Studio" in step.detail


def test_check_ipc_handshake_ok() -> None:
    rt = FakeRuntime()
    step = verify.check_ipc_handshake(rt)
    assert step.ok is True
    assert "noop ok" in step.detail


def test_check_ipc_handshake_timeout() -> None:
    rt = FakeRuntime(raise_timeout=True)
    step = verify.check_ipc_handshake(rt)
    assert step.ok is False
    assert "timed out" in step.detail


def test_check_ipc_handshake_error_status() -> None:
    rt = FakeRuntime(status="error", detail="boom")
    step = verify.check_ipc_handshake(rt)
    assert step.ok is False
    assert "status=" in step.detail


def test_check_ipc_handshake_unexpected_exception() -> None:
    rt = FakeRuntime(raise_other=RuntimeError("boom"))
    step = verify.check_ipc_handshake(rt)
    assert step.ok is False
    assert "RuntimeError" in step.detail


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def test_verify_setup_full_chain_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_iac(monkeypatch, ok=True)
    _stub_install(monkeypatch, tmp_path, install=True)
    _stub_run(monkeypatch, fl_running=True)
    rt = FakeRuntime()

    result = verify.verify_setup(rt)
    assert result.ok is True
    assert [s.name for s in result.steps] == [
        "iac_driver_online",
        "script_installed",
        "fl_studio_running",
        "ipc_handshake",
    ]


def test_verify_setup_iac_offline_short_circuits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_iac(monkeypatch, ok=False)
    rt = FakeRuntime()
    result = verify.verify_setup(rt)
    assert result.ok is False
    assert len(result.steps) == 1
    assert result.steps[0].name == "iac_driver_online"
    assert "IAC Driver" in result.summary


def test_verify_setup_script_missing_short_circuits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_iac(monkeypatch, ok=True)
    _stub_install(monkeypatch, tmp_path, install=False)
    rt = FakeRuntime()
    result = verify.verify_setup(rt)
    assert result.ok is False
    assert result.steps[-1].name == "script_installed"
    assert "install_script" in result.summary


def test_verify_setup_fl_not_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_iac(monkeypatch, ok=True)
    _stub_install(monkeypatch, tmp_path, install=True)
    _stub_run(monkeypatch, fl_running=False)
    rt = FakeRuntime()
    result = verify.verify_setup(rt)
    assert result.ok is False
    assert result.steps[-1].name == "fl_studio_running"


def test_verify_setup_skip_ui_arg_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """skip_ui is reserved but must not break callers that pass it."""
    _stub_iac(monkeypatch, ok=True)
    _stub_install(monkeypatch, tmp_path, install=True)
    _stub_run(monkeypatch, fl_running=True)
    rt = FakeRuntime()
    result = verify.verify_setup(rt, skip_ui=True)
    assert result.ok is True


def test_verify_setup_ipc_failure_with_actionable_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IPC noop times out — likely Controller type wrong / script not loaded."""
    _stub_iac(monkeypatch, ok=True)
    _stub_install(monkeypatch, tmp_path, install=True)
    _stub_run(monkeypatch, fl_running=True)
    rt = FakeRuntime(raise_timeout=True)
    result = verify.verify_setup(rt)
    assert result.ok is False
    assert result.steps[-1].name == "ipc_handshake"
    assert "Update MIDI scripts" in result.summary
