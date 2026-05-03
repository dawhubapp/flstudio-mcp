"""Tests for end-to-end FL Studio setup verifier."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def _stub_run(monkeypatch: pytest.MonkeyPatch, *, fl_running: bool = True) -> None:
    """Patch verify._run to fake pgrep + osascript outputs."""

    def fake_run(cmd: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess:
        if cmd[:1] == [verify.PGREP]:
            rc = 0 if fl_running else 1
            stdout = "1234\n" if fl_running else ""
            return subprocess.CompletedProcess(cmd, rc, stdout, "")
        raise AssertionError(f"unexpected cmd: {cmd}")

    monkeypatch.setattr(verify, "_run", fake_run)


def _stub_script_output(monkeypatch: pytest.MonkeyPatch, text: str, *, ok: bool = True) -> None:
    monkeypatch.setattr(verify, "read_fl_script_output", lambda timeout=10.0: (ok, text))


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
    _stub_run(monkeypatch, fl_running=True)
    step = verify.check_fl_running()
    assert step.ok is True
    assert step.data == {"pids": [1234]}


def test_check_fl_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_run(monkeypatch, fl_running=False)
    step = verify.check_fl_running()
    assert step.ok is False
    assert "not found" in step.detail


def test_check_script_output_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_script_output(monkeypatch, "[flstudio-mcp] started, polling /tmp/runtime/inbox\n")
    step = verify.check_script_output_loaded()
    assert step.ok is True


def test_check_script_output_missing_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_script_output(monkeypatch, "FL Studio Midi scripting version: 40\n")
    step = verify.check_script_output_loaded()
    assert step.ok is False
    assert "does not contain" in step.detail


def test_check_script_output_read_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_script_output(monkeypatch, "AS_ERROR: window not found", ok=False)
    step = verify.check_script_output_loaded()
    assert step.ok is False
    assert "could not read" in step.detail


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
    _stub_script_output(monkeypatch, "[flstudio-mcp] started\n")
    rt = FakeRuntime()

    result = verify.verify_setup(rt)
    assert result.ok is True
    assert [s.name for s in result.steps] == [
        "iac_driver_online",
        "script_installed",
        "fl_studio_running",
        "script_output_loaded",
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


def test_verify_setup_skip_ui_skips_script_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_iac(monkeypatch, ok=True)
    _stub_install(monkeypatch, tmp_path, install=True)
    _stub_run(monkeypatch, fl_running=True)

    def fail_if_called(*_a: Any, **_k: Any) -> None:
        pytest.fail("read_fl_script_output should not run when skip_ui=True")

    monkeypatch.setattr(verify, "read_fl_script_output", fail_if_called)
    rt = FakeRuntime()
    result = verify.verify_setup(rt, skip_ui=True)
    assert result.ok is True
    assert "script_output_loaded" not in [s.name for s in result.steps]


def test_verify_setup_ipc_failure_after_ui_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All UI checks pass but IPC noop times out — likely Controller type wrong."""
    _stub_iac(monkeypatch, ok=True)
    _stub_install(monkeypatch, tmp_path, install=True)
    _stub_run(monkeypatch, fl_running=True)
    _stub_script_output(monkeypatch, "[flstudio-mcp] started\n")
    rt = FakeRuntime(raise_timeout=True)
    result = verify.verify_setup(rt)
    assert result.ok is False
    assert result.steps[-1].name == "ipc_handshake"
    assert "handshake" in result.summary
