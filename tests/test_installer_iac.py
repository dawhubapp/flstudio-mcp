"""Tests for IAC Driver detection + auto-enable."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from flstudio_mcp.installer import iac


def _patch_signals(
    monkeypatch: pytest.MonkeyPatch,
    *,
    coremidi: tuple[int, int] | None,
    plugin: bool,
    profiler: str | None = None,
) -> None:
    """Patch all three detection signals to known values.

    ``coremidi=None`` simulates ctypes load failure. ``profiler=None``
    simulates system_profiler returning nothing useful (empty list or
    error).
    """
    monkeypatch.setattr(iac, "_coremidi_endpoint_counts", lambda: coremidi)
    monkeypatch.setattr(iac, "_iac_plugin_installed", lambda *_a, **_k: plugin)
    if profiler is None:
        monkeypatch.setattr(iac, "_system_profiler_status", lambda: None)
    else:
        monkeypatch.setattr(
            iac, "_system_profiler_status", lambda: iac.IacStatus(iac.IacState.ONLINE, profiler)
        )


def _profiler_payload(*, online: bool = True, present: bool = True) -> str:
    if not present:
        return json.dumps({iac.SYSTEM_PROFILER_DATA_TYPE: []})
    state_value = "midi_state_online" if online else "midi_state_offline"
    return json.dumps(
        {
            iac.SYSTEM_PROFILER_DATA_TYPE: [
                {
                    "_name": "MIDI Studio",
                    "_items": [{"_name": "IAC Driver", "online_state": state_value}],
                }
            ]
        }
    )


# --------------------------------------------------------------------------- #
# CoreMIDI primary path
# --------------------------------------------------------------------------- #


def test_online_when_coremidi_has_endpoints_and_plugin_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_signals(monkeypatch, coremidi=(1, 1), plugin=True)
    status = iac.check_iac_status()
    assert status.state == iac.IacState.ONLINE
    assert status.ok is True
    assert status.raw == {"dest_count": 1, "src_count": 1, "plugin_installed": True}


def test_offline_when_no_endpoints_but_plugin_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_signals(monkeypatch, coremidi=(0, 0), plugin=True)
    status = iac.check_iac_status()
    assert status.state == iac.IacState.OFFLINE
    assert "no MIDI endpoints active" in status.detail


def test_not_installed_when_no_endpoints_and_plugin_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_signals(monkeypatch, coremidi=(0, 0), plugin=False)
    status = iac.check_iac_status()
    assert status.state == iac.IacState.NOT_INSTALLED


def test_not_installed_when_endpoints_exist_but_plugin_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hardware MIDI without IAC plugin — rare but reported as not installed."""
    _patch_signals(monkeypatch, coremidi=(1, 1), plugin=False)
    status = iac.check_iac_status()
    assert status.state == iac.IacState.NOT_INSTALLED


def test_only_destinations(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_signals(monkeypatch, coremidi=(2, 0), plugin=True)
    assert iac.check_iac_status().state == iac.IacState.ONLINE


def test_only_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_signals(monkeypatch, coremidi=(0, 3), plugin=True)
    assert iac.check_iac_status().state == iac.IacState.ONLINE


# --------------------------------------------------------------------------- #
# Fallback path: CoreMIDI unreachable
# --------------------------------------------------------------------------- #


def test_falls_back_to_system_profiler_when_coremidi_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_signals(
        monkeypatch, coremidi=None, plugin=True, profiler="system_profiler reports IAC online"
    )
    status = iac.check_iac_status()
    assert status.state == iac.IacState.ONLINE


def test_unknown_when_coremidi_unreachable_and_profiler_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_signals(monkeypatch, coremidi=None, plugin=True)
    status = iac.check_iac_status()
    assert status.state == iac.IacState.UNKNOWN
    assert "CoreMIDI not reachable" in status.detail


def test_not_installed_when_coremidi_unreachable_and_no_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_signals(monkeypatch, coremidi=None, plugin=False)
    assert iac.check_iac_status().state == iac.IacState.NOT_INSTALLED


# --------------------------------------------------------------------------- #
# Legacy system_profiler parsing (still exercised on older macOS)
# --------------------------------------------------------------------------- #


def test_system_profiler_extracts_iac_entries() -> None:
    payload = json.loads(_profiler_payload(online=True))
    entries = iac._extract_iac_entries(payload)
    assert len(entries) == 1
    assert iac._entry_is_online(entries[0]) is True


def test_system_profiler_offline() -> None:
    payload = json.loads(_profiler_payload(online=False))
    entries = iac._extract_iac_entries(payload)
    assert iac._entry_is_online(entries[0]) is False


def test_system_profiler_empty_returns_no_entries() -> None:
    payload = json.loads(_profiler_payload(present=False))
    assert iac._extract_iac_entries(payload) == []


def test_legacy_midi_state_field() -> None:
    entry = {"midi_state": "online"}
    assert iac._entry_is_online(entry) is True


# --------------------------------------------------------------------------- #
# Auto-enable
# --------------------------------------------------------------------------- #


def test_enable_iac_returns_post_attempt_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    def fake_osascript(script: str) -> tuple[int, str, str]:
        calls.append(script)
        return 0, "", ""

    monkeypatch.setattr(iac, "_run_osascript", fake_osascript)
    _patch_signals(monkeypatch, coremidi=(1, 1), plugin=True)
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


# --------------------------------------------------------------------------- #
# ensure_iac_online dispatcher
# --------------------------------------------------------------------------- #


def test_ensure_iac_online_skips_enable_when_already_online(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_called = False

    def fake_enable():
        nonlocal enable_called
        enable_called = True
        return iac.IacStatus(iac.IacState.ONLINE)

    monkeypatch.setattr(iac, "enable_iac_via_ui_scripting", fake_enable)
    _patch_signals(monkeypatch, coremidi=(1, 1), plugin=True)
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
    _patch_signals(monkeypatch, coremidi=(0, 0), plugin=True)
    status = iac.ensure_iac_online()
    assert status.ok
    assert "auto-enabled" in status.detail


def test_ensure_iac_online_skips_when_disabled_via_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        iac, "enable_iac_via_ui_scripting", lambda: pytest.fail("should not be called")
    )
    _patch_signals(monkeypatch, coremidi=(0, 0), plugin=True)
    status = iac.ensure_iac_online(attempt_enable=False)
    assert status.state == iac.IacState.OFFLINE
