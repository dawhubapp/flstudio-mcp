"""F7.2.0 / F11.0.1-2 render spike. Run by hand with FL installed and closed.

    uv run python scripts/render_spike.py

Builds a probe FLP via the bridge (Kepler "Default" + a factory bass
sample), renders it once in pattern mode (FL CLI) and twice in song mode
(File > Export, drives FL's UI), and prints findings as JSON.

Kepler, not DX10: most factory DX10 / 3x Osc presets are FL 2.x-4.x saves
that FL 26 plays as 80 ms blips when spliced; Kepler's Default is an FL 21
save and sustains.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import click
import numpy as np

from flstudio_mcp.audio_checks import dominant_frequency, load_mono, silence_spans
from flstudio_mcp.music.theory import key_to_hz
from flstudio_mcp.render import RenderCache, RenderError, render_to_wav
from flstudio_mcp.runtime.offline import default_runtime

MCP_ROOT = Path(__file__).resolve().parents[1]
BASE = MCP_ROOT.parent / "flpdiff" / "tests" / "corpus" / "re_base" / "fl25" / "base_empty.flp"
SYNTH_PRESET = "%FLStudioFactoryData%/Data/Patches/Plugin presets/Generators/Kepler/Default.fst"
BASS_SAMPLE = (
    "%FLStudioFactoryData%/Data/Patches/Packs/Instruments/Bass/Acoustic/Acoustic Bass (1).wav"
)
PPQ = 96
TEMPO = 120.0  # 1 beat = 0.5 s, 1 bar = 2 s
SYNTH_KEYS = (45, 57)  # expect ~110 Hz and ~220 Hz if plugin keys follow MIDI numbering
SAMPLER_KEYS = (60, 72)  # expect a 2:1 ratio; key 60 = the sample's native pitch
CLIP_START_BAR = 1  # 2 s of song-mode lead-in silence


def build_probe(dst: Path) -> dict[str, Any]:
    """1-bar pattern in a 3-bar clip that starts at bar 2 (2 s at 120 bpm).

    Beat 1: Kepler key 45. Beat 3: sampler key 60. Beat 4: sampler key 72.
    Song mode -> 2 s of leading silence; pattern mode -> sound at 0 s.
    Clips loop -> the sampler hits repeat at +2 s and +4 s.
    """
    shutil.copy2(BASE, dst)
    rt = default_runtime()

    def call(op: str, **args: Any) -> Any:
        response = rt.call(op, {"path": str(dst), **args})
        if not response.ok:
            raise click.ClickException(f"{op} failed: {response.error} {response.message}")
        return response.result

    synth = call(
        "load_factory_preset", fst_path=SYNTH_PRESET, kind="generator", name="Probe · Kepler"
    )["channel_iid"]
    sampler = call("create_channel", name="Probe · Bass sample")["channel_iid"]
    call("set_channel_sample_path", iid=sampler, sample_path=BASS_SAMPLE)
    pattern = call("create_pattern", name="Probe")["pattern_id"]
    notes = [
        {"position": 0, "length": PPQ, "key": SYNTH_KEYS[0], "channel_iid": synth, "velocity": 110},
        {
            "position": 2 * PPQ,
            "length": PPQ // 2,
            "key": SAMPLER_KEYS[0],
            "channel_iid": sampler,
            "velocity": 110,
        },
        {
            "position": 3 * PPQ,
            "length": PPQ // 2,
            "key": SAMPLER_KEYS[1],
            "channel_iid": sampler,
            "velocity": 110,
        },
    ]
    call("set_pattern_notes", pattern_id=pattern, notes=notes)
    call("set_tempo", bpm=TEMPO)
    call(
        "arrange_song",
        arrangement=0,
        structure=[{"pattern_id": pattern, "bars": 3, "position_ticks": CLIP_START_BAR * 4 * PPQ}],
    )
    return {"synth_iid": synth, "sampler_iid": sampler, "pattern_id": pattern}


def _f(x: np.ndarray, sr: int, start_s: float, end_s: float) -> float:
    return round(dominant_frequency(x[int(start_s * sr) : int(end_s * sr)], sr), 1)


def _rms_db(x: np.ndarray, sr: int, start_s: float, end_s: float) -> float:
    seg = x[int(start_s * sr) : int(end_s * sr)].astype(np.float64)
    return round(float(20 * np.log10(max(np.sqrt(np.mean(seg**2)) if seg.size else 0.0, 1e-10))), 1)


def measure(wav: Path) -> dict[str, Any]:
    x, sr = load_mono(wav)
    song_mode = _rms_db(x, sr, 0.0, 1.9) < -60.0
    t0 = 2.0 if song_mode else 0.0  # clip start in the render
    # sampler hits at clip start + 1.0 s (key 60) and + 1.5 s (key 72), each 0.25 s long
    samp_lo, samp_hi = _f(x, sr, t0 + 1.02, t0 + 1.24), _f(x, sr, t0 + 1.52, t0 + 1.74)
    repeats_db = [_rms_db(x, sr, t0 + 1.0 + k * 2.0, t0 + 1.25 + k * 2.0) for k in (1, 2)]
    return {
        "duration_s": round(x.size / sr, 2),
        "song_mode": song_mode,
        "synth_hz": _f(x, sr, t0 + 0.02, t0 + 0.4),
        "synth_expected_hz": round(key_to_hz(SYNTH_KEYS[0]), 1),
        "sampler_hz": [samp_lo, samp_hi],
        "sampler_ratio": round(samp_hi / samp_lo, 3) if samp_lo else None,
        "repeat_hits_db": repeats_db,
        "pattern_clip_loops": all(db > -40.0 for db in repeats_db),
        "silence_spans": [
            [round(a, 2), round(b, 2)] for a, b in silence_spans(x, sr, min_len_s=0.3)
        ],
    }


@click.command()
def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="render-spike-"))
    probe = work / "probe.flp"
    results: dict[str, Any] = {"work_dir": str(work), "probe": build_probe(probe)}
    cache = RenderCache(root=work / "cache")
    for label, mode in (("pattern", "pattern"), ("song_cold", "song"), ("song_warm", "song")):
        t0 = time.monotonic()
        try:
            rendered = render_to_wav(probe, mode=mode, cache=cache, force=True)
        except RenderError as exc:
            results[label] = {"error": exc.code, "message": exc.message}
            continue
        results[label] = {
            "walltime_s": round(time.monotonic() - t0, 1),
            **rendered.to_dict(),
            **measure(rendered.wav_path),
        }
    results["cache_hit"] = render_to_wav(probe, cache=cache).cached
    click.echo(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
