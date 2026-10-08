"""Real FL render (gated: FLSTUDIO_RENDER_E2E=1, FL installed and closed; drives FL's UI)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from flstudio_mcp.render import RenderCache, render_to_wav

pytestmark = pytest.mark.skipif(
    os.environ.get("FLSTUDIO_RENDER_E2E") != "1", reason="set FLSTUDIO_RENDER_E2E=1 (drives FL)"
)
SPIKE = Path(__file__).resolve().parents[1] / "scripts" / "render_spike.py"


def _spike():
    spec = importlib.util.spec_from_file_location("render_spike", SPIKE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_real_song_render_probe(tmp_path: Path) -> None:
    spike = _spike()
    probe = tmp_path / "probe.flp"
    spike.build_probe(probe)
    cache = RenderCache(root=tmp_path / "cache")
    first = render_to_wav(probe, cache=cache, force=True)  # song mode (FL export UI)
    assert first.outcome == "exported" and first.file_size > 100_000
    found = spike.measure(first.wav_path)
    assert found["song_mode"], found  # 2 s lead-in: the clip starts at bar 2
    assert found["synth_hz"] == pytest.approx(110.0, rel=0.06)  # Kepler key 45
    assert found["sampler_ratio"] == pytest.approx(2.0, rel=0.05)  # sampler keys 60 -> 72
    assert found["pattern_clip_loops"] is False  # FL leaves stretched clips silent
    assert render_to_wav(probe, cache=cache).cached is True
