"""Tests for FL Settings dialog driver."""

from __future__ import annotations

import subprocess

import pytest

from flstudio_mcp.installer import wire_fl


@pytest.fixture
def fake_pyautogui(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Replace _import_pyautogui with a recording stub."""
    calls: list[tuple] = []

    class FakePG:
        FAILSAFE = False

        @staticmethod
        def click(x: int, y: int) -> None:
            calls.append(("click", x, y))

        @staticmethod
        def write(s: str, *, interval: float = 0.0) -> None:
            calls.append(("write", s))

        @staticmethod
        def press(key: str) -> None:
            calls.append(("press", key))

    monkeypatch.setattr(wire_fl, "_import_pyautogui", lambda: FakePG)
    return calls


def _stub_open(monkeypatch: pytest.MonkeyPatch, bounds: wire_fl.WindowBounds | str) -> None:
    monkeypatch.setattr(
        wire_fl,
        "open_midi_settings",
        lambda process_name: (isinstance(bounds, wire_fl.WindowBounds), bounds),
    )


def _stub_fl_process(monkeypatch: pytest.MonkeyPatch, name: str | None = "OsxFL") -> None:
    monkeypatch.setattr(wire_fl, "_detect_fl_process_name", lambda: name)


def test_wire_input_full_sequence(
    monkeypatch: pytest.MonkeyPatch, fake_pyautogui: list[tuple]
) -> None:
    _stub_fl_process(monkeypatch, "OsxFL")
    bounds = wire_fl.WindowBounds(x=100, y=200, w=760, h=800)
    _stub_open(monkeypatch, bounds)
    monkeypatch.setattr(wire_fl.time, "sleep", lambda s: None)

    result = wire_fl.wire_flstudio_mcp_input()
    assert result.ok is True
    assert result.window == bounds
    coord = wire_fl.WireCoords()

    # All 4 clicks happen at window-origin + relative coords
    click_calls = [c for c in fake_pyautogui if c[0] == "click"]
    assert click_calls == [
        ("click", 100 + coord.update_scripts_btn[0], 200 + coord.update_scripts_btn[1]),
        ("click", 100 + coord.iac_input_row[0], 200 + coord.iac_input_row[1]),
        ("click", 100 + coord.enable_radio[0], 200 + coord.enable_radio[1]),
        ("click", 100 + coord.controller_dropdown[0], 200 + coord.controller_dropdown[1]),
    ]
    # Type-to-search + Enter
    assert ("write", "flstudio-mcp") in fake_pyautogui
    assert ("press", "enter") in fake_pyautogui


def test_wire_input_dry_run_skips_clicks(
    monkeypatch: pytest.MonkeyPatch, fake_pyautogui: list[tuple]
) -> None:
    _stub_fl_process(monkeypatch, "OsxFL")
    _stub_open(monkeypatch, wire_fl.WindowBounds(0, 0, 760, 800))

    result = wire_fl.wire_flstudio_mcp_input(dry_run=True)
    assert result.ok is True
    assert "dry run" in result.summary
    assert fake_pyautogui == []  # no clicks


def test_wire_input_no_fl_running(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fl_process(monkeypatch, None)
    result = wire_fl.wire_flstudio_mcp_input()
    assert result.ok is False
    assert "not running" in result.summary


def test_wire_input_dialog_open_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fl_process(monkeypatch, "OsxFL")
    _stub_open(monkeypatch, "AS_ERROR:menu missing")
    result = wire_fl.wire_flstudio_mcp_input()
    assert result.ok is False
    assert "could not open" in result.summary


def test_wire_input_custom_coords_used(
    monkeypatch: pytest.MonkeyPatch, fake_pyautogui: list[tuple]
) -> None:
    _stub_fl_process(monkeypatch, "OsxFL")
    _stub_open(monkeypatch, wire_fl.WindowBounds(0, 0, 760, 800))
    monkeypatch.setattr(wire_fl.time, "sleep", lambda s: None)

    custom = wire_fl.WireCoords(
        update_scripts_btn=(1, 2),
        iac_input_row=(3, 4),
        enable_radio=(5, 6),
        controller_dropdown=(7, 8),
    )
    result = wire_fl.wire_flstudio_mcp_input(coords=custom)
    assert result.ok is True
    click_calls = [c for c in fake_pyautogui if c[0] == "click"]
    assert click_calls == [
        ("click", 1, 2),
        ("click", 3, 4),
        ("click", 5, 6),
        ("click", 7, 8),
    ]


def test_open_midi_settings_parses_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, *, timeout=10.0):
        return subprocess.CompletedProcess(cmd, 0, "100,200,760,401", "")

    monkeypatch.setattr(wire_fl, "_run", fake_run)
    ok, bounds = wire_fl.open_midi_settings("OsxFL")
    assert ok is True
    assert bounds == wire_fl.WindowBounds(100, 200, 760, 401)


def test_open_midi_settings_handles_as_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, *, timeout=10.0):
        return subprocess.CompletedProcess(cmd, 0, "AS_ERROR:dialog not found", "")

    monkeypatch.setattr(wire_fl, "_run", fake_run)
    ok, err = wire_fl.open_midi_settings("OsxFL")
    assert ok is False
    assert "AS_ERROR" in err


def test_open_midi_settings_handles_osascript_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd, *, timeout=10.0):
        return subprocess.CompletedProcess(cmd, 1, "", "boom")

    monkeypatch.setattr(wire_fl, "_run", fake_run)
    ok, err = wire_fl.open_midi_settings("OsxFL")
    assert ok is False
    assert "exit=1" in err


def test_window_bounds_offset() -> None:
    b = wire_fl.WindowBounds(10, 20, 760, 401)
    assert b.offset(5, 3) == (15, 23)
