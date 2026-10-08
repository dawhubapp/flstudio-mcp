"""Tests for the render_to_wav MCP tool (fake renderer + fake bridge)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from flstudio_mcp import logging_setup
from flstudio_mcp.render import RenderError, RenderResult
from flstudio_mcp.runtime.offline import BridgeResponse
from flstudio_mcp.server import build_server
from flstudio_mcp.tools import render as render_tool

from .beat_fixtures import good_house
from .test_offline_tool import FakeOfflineRuntime


@pytest.fixture(autouse=True)
def _logs(tmp_path):
    return logging_setup.configure_logging(log_dir=tmp_path / "logs")


@pytest.fixture
def flp(tmp_path: Path) -> Path:
    path = tmp_path / "beat.flp"
    path.write_bytes(b"FLhd")
    return path


def _renderer(tmp_path: Path, seconds: float = 62.0):
    wav = tmp_path / "out.wav"
    sr = 22050
    t = np.arange(int(seconds * sr)) / sr
    sf.write(str(wav), (0.3 * np.sin(2 * np.pi * 55 * t)).astype(np.float32), sr)

    def fake(path: Path, *, force: bool = False) -> RenderResult:
        return RenderResult(wav, seconds, wav.stat().st_size, 12.3, False, "exited")

    return fake


def test_sections_for_audio_good_house() -> None:
    spans = render_tool.sections_for_audio(good_house())
    assert [s.name for s in spans] == ["Intro", "Build", "Drop"]
    bar_s = 4 * 60 / 124
    assert spans[1].start_s == pytest.approx(8 * bar_s)
    assert spans[2].end_s == pytest.approx(32 * bar_s)
    assert spans[2].energy == 1.0


def test_render_and_analyze(tmp_path: Path, flp: Path) -> None:
    rt = FakeOfflineRuntime(response=BridgeResponse(ok=True, kind="describe", result=good_house()))
    env = render_tool.execute(
        {"path": str(flp)}, renderer=_renderer(tmp_path), runtime_factory=lambda: rt
    )
    assert env["ok"] is True and env["kind"] == "render_to_wav"
    result = env["result"]
    assert result["render_walltime_s"] == 12.3
    assert [s["name"] for s in result["metrics"]["sections"]] == ["Intro", "Build", "Drop"]
    assert isinstance(result["issues"], list)


def test_analyze_false_skips_bridge(tmp_path: Path, flp: Path) -> None:
    rt = FakeOfflineRuntime()
    env = render_tool.execute(
        {"path": str(flp), "analyze": False},
        renderer=_renderer(tmp_path),
        runtime_factory=lambda: rt,
    )
    assert env["ok"] is True and "metrics" not in env["result"]
    assert rt.last_call is None


def test_describe_failure_still_returns_audio_metrics(tmp_path: Path, flp: Path) -> None:
    rt = FakeOfflineRuntime(
        response=BridgeResponse(ok=False, kind="describe", error="PARSE_ERROR", message="bad")
    )
    env = render_tool.execute(
        {"path": str(flp)}, renderer=_renderer(tmp_path), runtime_factory=lambda: rt
    )
    assert env["ok"] is True
    assert "section checks skipped" in env["result"]["analysis_note"]
    assert env["result"]["metrics"]["sections"] == []


def test_render_error_maps_to_envelope(tmp_path: Path, flp: Path) -> None:
    def busy(path: Path, *, force: bool = False) -> RenderResult:
        raise RenderError("FL_BUSY", "FL Studio is already running")

    env = render_tool.execute({"path": str(flp)}, renderer=busy, runtime_factory=FakeOfflineRuntime)
    assert env["ok"] is False
    assert env["result"]["error"] == "FL_BUSY"
    assert "quit FL" in env["result"]["hint"]


@pytest.mark.parametrize("path", ["", "/nope/missing.flp", "/etc/hosts"])
def test_invalid_path(path: str) -> None:
    env = render_tool.execute(
        {"path": path},
        renderer=None,  # type: ignore[arg-type]
        runtime_factory=FakeOfflineRuntime,
    )
    assert env["ok"] is False and env["result"]["error"] == "INVALID_ARGS"


@pytest.mark.anyio
async def test_server_registers_render_tool() -> None:
    server = build_server(install_result=[])
    names = {t.name for t in await server.list_tools()}
    assert "render_to_wav" in names and "offline_execute" in names
