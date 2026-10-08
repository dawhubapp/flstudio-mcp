"""Tests for flstudio_mcp.audio_checks on synthesized WAVs."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from flstudio_mcp.audio_checks import (
    SectionSpan,
    analyze,
    band_shares_db,
    dominant_frequency,
    load_mono,
    silence_spans,
)

SR = 44100


def _tone(freq: float, seconds: float, amp: float = 0.5, sr: int = SR) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _write(path: Path, x: np.ndarray, sr: int = SR, subtype: str = "PCM_16") -> Path:
    sf.write(str(path), x, sr, subtype=subtype)
    return path


def test_sine_metrics(tmp_path: Path) -> None:
    wav = _write(tmp_path / "a.wav", _tone(440.0, 2.0, amp=0.5))
    report = analyze(wav)
    assert report.peak_dbfs == pytest.approx(-6.02, abs=0.1)
    assert report.rms_dbfs == pytest.approx(-9.03, abs=0.2)
    assert report.duration_s == pytest.approx(2.0, abs=0.01)
    assert not [i for i in report.issues if i.severity == "error"]
    x, sr = load_mono(wav)
    assert dominant_frequency(x, sr) == pytest.approx(440.0, abs=1.0)


def test_silent_render_is_an_error_and_json_safe(tmp_path: Path) -> None:
    for name, x in (
        ("zero.wav", np.zeros(SR, dtype=np.float32)),
        ("empty.wav", np.zeros(0, dtype=np.float32)),
    ):
        report = analyze(_write(tmp_path / name, x))
        assert any("render is silent" in i.message for i in report.issues if i.severity == "error")
        json.dumps(report.to_dict(), allow_nan=False)


def test_clipping_is_an_error(tmp_path: Path) -> None:
    square = np.sign(_tone(100.0, 1.0, amp=1.0)).astype(np.float32)
    report = analyze(_write(tmp_path / "clip.wav", square, subtype="FLOAT"))
    assert any("clipping" in i.message for i in report.issues if i.severity == "error")


def test_interior_gap_warns_unless_low_energy_section(tmp_path: Path) -> None:
    x = np.concatenate([_tone(220, 1.0), np.zeros(SR, dtype=np.float32), _tone(220, 1.0)])
    wav = _write(tmp_path / "gap.wav", x)
    assert any("silent gap" in i.message for i in analyze(wav).issues)
    sections = [
        SectionSpan("Intro", 0.0, 0.9, 0.3),
        SectionSpan("Break", 0.9, 2.1, 0.4),
        SectionSpan("Drop", 2.1, 3.0, 1.0),
    ]
    assert not any("silent gap" in i.message for i in analyze(wav, sections).issues)


def test_drop_quieter_than_intro_warns(tmp_path: Path) -> None:
    x = np.concatenate([_tone(60, 2.0, amp=0.6), _tone(60, 2.0, amp=0.1)])
    wav = _write(tmp_path / "e.wav", x)
    sections = [SectionSpan("Intro", 0.0, 2.0, 0.3), SectionSpan("Drop", 2.0, 4.0, 1.0)]
    report = analyze(wav, sections)
    assert any("'Drop' is quieter than 'Intro'" in i.message for i in report.issues)
    assert [s["name"] for s in report.sections] == ["Intro", "Drop"]


def test_thin_low_end_in_drop_warns(tmp_path: Path) -> None:
    hi = _write(tmp_path / "hi.wav", _tone(8000, 2.0))
    full = _write(tmp_path / "full.wav", _tone(50, 2.0) + _tone(8000, 2.0, amp=0.2))
    drop = [SectionSpan("Drop", 0.0, 2.0, 1.0)]
    assert any("thin low end" in i.message for i in analyze(hi, drop).issues)
    assert not any("thin low end" in i.message for i in analyze(full, drop).issues)


def test_band_shares_for_sub_sine() -> None:
    shares = band_shares_db(_tone(45.0, 2.0), SR)
    assert shares["sub"] > -1.0
    assert shares["mid"] < -20.0 and shares["high"] < -20.0


def test_silence_spans_boundaries() -> None:
    x = np.concatenate([_tone(220, 1.0), np.zeros(SR, dtype=np.float32), _tone(220, 1.0)])
    spans = silence_spans(x, SR)
    assert len(spans) == 1
    start, end = spans[0]
    assert start == pytest.approx(1.0, abs=0.06) and end == pytest.approx(2.0, abs=0.06)


def test_load_mono_stereo_float_48k(tmp_path: Path) -> None:
    left = _tone(440, 1.0, amp=0.5, sr=48000)
    stereo = np.stack([left, left * 0.0], axis=1)
    path = tmp_path / "st.wav"
    sf.write(str(path), stereo, 48000, subtype="FLOAT")
    x, sr = load_mono(path)
    assert sr == 48000 and x.ndim == 1 and x.size == 48000
    assert float(np.max(np.abs(x))) == pytest.approx(0.25, abs=0.01)
    pcm24 = tmp_path / "p24.wav"
    sf.write(str(pcm24), left, 48000, subtype="PCM_24")
    assert load_mono(pcm24)[1] == 48000
