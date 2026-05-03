"""Tests for the live_execute pre-flight check."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from flstudio_mcp.errors import ErrorCode
from flstudio_mcp.installer import verify as verify_installer
from flstudio_mcp.preflight import Preflight, PreflightResult


@dataclass
class _FakeResult:
    status: str = "ok"
    detail: str = ""


@dataclass
class _FakeRuntime:
    raise_timeout: bool = False
    raise_other: Exception | None = None
    status: str = "ok"

    def noop(self, *, timeout_s: float = 1.0) -> _FakeResult:
        if self.raise_timeout:
            raise TimeoutError("noop deadline exceeded")
        if self.raise_other is not None:
            raise self.raise_other
        return _FakeResult(status=self.status)


def _stub_fl(monkeypatch: pytest.MonkeyPatch, *, running: bool = True) -> None:
    procs = [("OsxFL", 1234)] if running else []
    monkeypatch.setattr(verify_installer, "detect_fl_processes", lambda: procs)


def test_preflight_ok_when_fl_running_and_noop_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_fl(monkeypatch, running=True)
    pf = Preflight(runtime_factory=_FakeRuntime)
    res = pf.check()
    assert res.ok is True
    assert res.code is None
    assert res.cached is False


def test_preflight_fl_not_running_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fl(monkeypatch, running=False)
    calls = {"n": 0}

    class CountingRuntime:
        def noop(self, *, timeout_s=1.0):
            calls["n"] += 1
            return _FakeResult()

    pf = Preflight(runtime_factory=CountingRuntime)
    res = pf.check()
    assert res.ok is False
    assert res.code == ErrorCode.FL_NOT_RUNNING
    # noop must not be called when FL isn't running
    assert calls["n"] == 0


def test_preflight_noop_timeout_returns_script_not_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_fl(monkeypatch, running=True)
    pf = Preflight(runtime_factory=lambda: _FakeRuntime(raise_timeout=True))
    res = pf.check()
    assert res.ok is False
    assert res.code == ErrorCode.MIDI_SCRIPT_NOT_LOADED


def test_preflight_noop_unexpected_exception_returns_preflight_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_fl(monkeypatch, running=True)
    pf = Preflight(runtime_factory=lambda: _FakeRuntime(raise_other=RuntimeError("ipc broke")))
    res = pf.check()
    assert res.ok is False
    assert res.code == ErrorCode.PREFLIGHT_FAILED
    assert "RuntimeError" in res.detail


def test_preflight_noop_error_status_returns_script_not_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_fl(monkeypatch, running=True)
    pf = Preflight(runtime_factory=lambda: _FakeRuntime(status="error"))
    res = pf.check()
    assert res.ok is False
    assert res.code == ErrorCode.MIDI_SCRIPT_NOT_LOADED


def test_preflight_caches_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fl(monkeypatch, running=True)
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return _FakeRuntime()

    pf = Preflight(runtime_factory=factory, cache_ttl_s=60.0)
    pf.check()
    pf.check()
    pf.check()
    assert calls["n"] == 1
    assert pf.check().cached is True


def test_preflight_force_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fl(monkeypatch, running=True)
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return _FakeRuntime()

    pf = Preflight(runtime_factory=factory, cache_ttl_s=60.0)
    pf.check()
    pf.check(force=True)
    assert calls["n"] == 2


def test_preflight_invalidate_clears_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fl(monkeypatch, running=True)
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return _FakeRuntime()

    pf = Preflight(runtime_factory=factory, cache_ttl_s=60.0)
    pf.check()
    pf.invalidate()
    pf.check()
    assert calls["n"] == 2


def test_preflight_result_to_dict() -> None:
    res = PreflightResult(ok=True, code=None, detail="ok", cached=False)
    assert res.to_dict() == {"ok": True, "code": None, "detail": "ok", "cached": False}
    err = PreflightResult(ok=False, code=ErrorCode.IPC_TIMEOUT, detail="boom", cached=True)
    assert err.to_dict() == {
        "ok": False,
        "code": "IPC_TIMEOUT",
        "detail": "boom",
        "cached": True,
    }
