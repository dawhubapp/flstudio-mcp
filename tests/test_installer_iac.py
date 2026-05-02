"""Tests for IAC Driver detection + auto-enable."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from flstudio_mcp.installer import iac


def _profiler_payload(*, online: bool = True, present: bool = True) -> str:
    if not present:
        return json.dumps({iac.SYSTEM_PROFILER_DATA_TYPE: []})
    state_value = "midi_state_online" if online else "midi_state_offline"
    return json.dumps(
        {
            iac.SYSTEM_PROFILER_DATA_TYPE: [
                {
                    "_name": "MIDI Studio",
                    "_items": [
                        {
                            "_name": "IAC Driver",
                            "online_state": state_value,
                        }
                    ],
                }
            ]
        }
    )


def _patch_profiler(monkeypatch: pytest.MonkeyPatch, payload: str) -> None:
    monkeypatch.setattr(iac, "_run_system_profiler", lambda: payload)


def test_check_iac_status_online(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_profiler(monkeypatch, _profiler_payload(online=True))
    status = iac.check_iac_status()
    assert status.state == iac.IacState.ONLINE
    assert status.ok is True


def test_check_iac_status_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_profiler(monkeypatch, _profiler_payload(online=False))
    status = iac.check_iac_status()
    assert status.state == iac.IacState.OFFLINE
    assert status.ok is False
    assert "disabled" in status.detail


def test_check_iac_status_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_profiler(monkeypatch, _profiler_payload(present=False))
    status = iac.check_iac_status()
    assert status.state == iac.IacState.NOT_INSTALLED


def test_check_iac_status_unknown_when_profiler_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom():
        raise FileNotFoundError("system_profiler")

    monkeypatch.setattr(iac, "_run_system_profiler", boom)
    status = iac.check_iac_status()
    assert status.state == iac.IacState.UNKNOWN
    assert "system_profiler" in status.detail


def test_check_iac_status_unknown_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_profiler(monkeypatch, "not json")
    status = iac.check_iac_status()
    assert status.state == iac.IacState.UNKNOWN
    assert "non-JSON" in status.detail


def test_legacy_midi_state_field(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        {iac.SYSTEM_PROFILER_DATA_TYPE: [{"_name": "IAC Driver", "midi_state": "online"}]}
    )
    _patch_profiler(monkeypatch, payload)
    assert iac.check_iac_status().state == iac.IacState.ONLINE


def test_enable_iac_returns_post_attempt_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    def fake_osascript(script: str) -> tuple[int, str, str]:
        calls.append(script)
        return 0, "", ""

    monkeypatch.setattr(iac, "_run_osascript", fake_osascript)
    _patch_profiler(monkeypatch, _profiler_payload(online=True))
    status = iac.enable_iac_via_ui_scripting()
    assert status.state == iac.IacState.ONLINE
    assert calls, "osascript was not invoked"


def test_enable_iac_handles_osascript_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_script: str):
        raise FileNotFoundError("osascript")

    monkeypatch.setattr(iac, "_run_osascript", boom)
    status = iac.enable_iac_via_ui_scripting()
    assert status.state == iac.IacState.UNKNOWN


def test_enable_iac_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_script: str):
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=1)

    monkeypatch.setattr(iac, "_run_osascript", boom)
    status = iac.enable_iac_via_ui_scripting()
    assert status.state == iac.IacState.UNKNOWN


def test_ensure_iac_online_skips_enable_when_already_online(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_called = False

    def fake_enable():
        nonlocal enable_called
        enable_called = True
        return iac.IacStatus(iac.IacState.ONLINE)

    monkeypatch.setattr(iac, "enable_iac_via_ui_scripting", fake_enable)
    _patch_profiler(monkeypatch, _profiler_payload(online=True))
    status = iac.ensure_iac_online()
    assert status.ok
    assert enable_called is False


def test_ensure_iac_online_attempts_enable_when_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        iac,
        "enable_iac_via_ui_scripting",
        lambda: iac.IacStatus(iac.IacState.ONLINE, "auto-enabled"),
    )
    _patch_profiler(monkeypatch, _profiler_payload(online=False))
    status = iac.ensure_iac_online()
    assert status.ok
    assert "auto-enabled" in status.detail


def test_ensure_iac_online_skips_when_disabled_via_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        iac, "enable_iac_via_ui_scripting", lambda: pytest.fail("should not be called")
    )
    _patch_profiler(monkeypatch, _profiler_payload(online=False))
    status = iac.ensure_iac_online(attempt_enable=False)
    assert status.state == iac.IacState.OFFLINE
