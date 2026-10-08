"""Tier 0 audio checks on a rendered WAV (F7.3, slim).

Ships in the MCP, so only numpy + soundfile — no torch. Every level is in
dBFS; silence is floored at EPS so reports never contain -inf/NaN.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from .issues import Issue

EPS = 1e-10
BANDS: dict[str, tuple[float, float]] = {
    "sub": (20.0, 60.0),
    "low": (60.0, 250.0),
    "mid": (250.0, 4000.0),
    "high": (4000.0, 20000.0),
}
SILENCE_DBFS = -60.0
CLIP_LEVEL = 0.999
CLIP_RATIO_ERROR = 0.001
QUIET_RMS_DBFS = -30.0
THIN_LOW_END_DB = -12.0  # sub+low share of total energy in a high-energy section
ENERGY_GAP = 0.3  # compare sections whose expected energies differ by at least this
LEVEL_SLACK_DB = 1.0


@dataclass(frozen=True)
class SectionSpan:
    name: str
    start_s: float
    end_s: float
    energy: float | None = None


@dataclass
class AudioReport:
    duration_s: float
    sample_rate: int
    peak_dbfs: float
    rms_dbfs: float
    clipping_ratio: float
    silence_spans: list[tuple[float, float]]
    bands_db: dict[str, float]
    sections: list[dict[str, Any]]
    issues: list[Issue]

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration_s": round(self.duration_s, 3),
            "sample_rate": self.sample_rate,
            "peak_dbfs": round(self.peak_dbfs, 2),
            "rms_dbfs": round(self.rms_dbfs, 2),
            "clipping_ratio": round(self.clipping_ratio, 6),
            "silence_spans": [[round(a, 3), round(b, 3)] for a, b in self.silence_spans],
            "bands_db": {k: round(v, 2) for k, v in self.bands_db.items()},
            "sections": self.sections,
            "issues": [i.to_dict() for i in self.issues],
        }


def load_mono(path: Path) -> tuple[np.ndarray, int]:
    """Read any WAV soundfile supports; average channels to mono float32."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return data.mean(axis=1), int(sr)


def _amp_db(value: float) -> float:
    return float(20.0 * np.log10(max(value, EPS)))


def _peak_dbfs(x: np.ndarray) -> float:
    return _amp_db(float(np.max(np.abs(x)))) if x.size else _amp_db(0.0)


def _rms_dbfs(x: np.ndarray) -> float:
    if not x.size:
        return _amp_db(0.0)
    return _amp_db(float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))))


def band_shares_db(x: np.ndarray, sr: int) -> dict[str, float]:
    """Each band's share of total spectral energy (>= 20 Hz), in dB (0 = all of it)."""
    if x.size == 0:
        return {name: _amp_db(0.0) / 2 for name in BANDS}
    power = np.abs(np.fft.rfft(x.astype(np.float64))) ** 2
    freqs = np.fft.rfftfreq(x.size, 1.0 / sr)
    total = float(power[freqs >= 20.0].sum()) + EPS
    shares: dict[str, float] = {}
    for name, (lo, hi) in BANDS.items():
        band = float(power[(freqs >= lo) & (freqs < hi)].sum())
        shares[name] = float(10.0 * np.log10(max(band, EPS) / total))
    return shares


def silence_spans(
    x: np.ndarray,
    sr: int,
    *,
    window_s: float = 0.05,
    min_len_s: float = 0.5,
    threshold_dbfs: float = SILENCE_DBFS,
) -> list[tuple[float, float]]:
    """Stretches quieter than ``threshold_dbfs`` lasting at least ``min_len_s``."""
    win = max(1, int(sr * window_s))
    frames = x.size // win
    if frames == 0:
        return []
    framed = x[: frames * win].reshape(frames, win)
    rms = np.sqrt(np.mean(np.square(framed, dtype=np.float64), axis=1))
    silent = 20.0 * np.log10(np.maximum(rms, EPS)) < threshold_dbfs
    spans: list[tuple[float, float]] = []
    start: int | None = None
    for i, is_silent in enumerate(silent):
        if is_silent and start is None:
            start = i
        elif not is_silent and start is not None:
            spans.append((start * win / sr, i * win / sr))
            start = None
    if start is not None:
        spans.append((start * win / sr, frames * win / sr))
    return [(a, b) for a, b in spans if b - a >= min_len_s]


def dominant_frequency(
    x: np.ndarray, sr: int, *, fmin: float = 20.0, fmax: float = 5000.0
) -> float:
    """Strongest frequency in [fmin, fmax] (Hann window + parabolic interpolation)."""
    if x.size < 2:
        return 0.0
    spec = np.abs(np.fft.rfft(x.astype(np.float64) * np.hanning(x.size)))
    freqs = np.fft.rfftfreq(x.size, 1.0 / sr)
    masked = np.where((freqs >= fmin) & (freqs <= fmax), spec, 0.0)
    i = int(np.argmax(masked))
    if masked[i] == 0.0:
        return 0.0
    if 0 < i < spec.size - 1:
        a, b, c = spec[i - 1], spec[i], spec[i + 1]
        denom = a - 2 * b + c
        if denom != 0:
            return float(freqs[i] + 0.5 * (a - c) / denom * (freqs[1] - freqs[0]))
    return float(freqs[i])


def _section_at(sections: list[SectionSpan], t: float) -> SectionSpan | None:
    return next((s for s in sections if s.start_s <= t < s.end_s), None)


def analyze(
    path: Path, sections: list[SectionSpan] | None = None, *, min_silence_s: float = 0.5
) -> AudioReport:
    """Level, clipping, silence and per-section balance checks for one render."""
    sections = sections or []
    x, sr = load_mono(path)
    duration = x.size / sr if sr else 0.0
    peak = _peak_dbfs(x)
    rms = _rms_dbfs(x)
    clipping = float(np.mean(np.abs(x) >= CLIP_LEVEL)) if x.size else 0.0
    spans = silence_spans(x, sr, min_len_s=min_silence_s)
    issues: list[Issue] = []
    rows: list[dict[str, Any]] = []

    if x.size == 0 or peak <= SILENCE_DBFS:
        issues.append(
            Issue(
                "error",
                "render is silent",
                hint="Check that channels have sounds (sample or plugin) and notes are arranged.",
            )
        )
    else:
        if clipping > CLIP_RATIO_ERROR:
            issues.append(
                Issue(
                    "error",
                    f"clipping: {clipping:.2%} of samples at full scale",
                    hint="Lower channel volumes (set_channel_volume).",
                )
            )
        elif peak > -0.1:
            issues.append(Issue("warning", f"peak at {peak:.1f} dBFS — no headroom"))
        if rms < QUIET_RMS_DBFS:
            issues.append(Issue("warning", f"very quiet render ({rms:.1f} dBFS RMS)"))
        for a, b in spans:
            if a <= 0.05 or b >= duration - 0.05:
                continue  # leading/trailing silence is fine
            section = _section_at(sections, (a + b) / 2)
            if section is not None and section.energy is not None and section.energy <= 0.4:
                continue
            where = f" in '{section.name}'" if section else ""
            issues.append(
                Issue(
                    "warning",
                    f"silent gap {a:.1f}-{b:.1f}s{where}",
                    hint="Fill the gap or make it an intentional break.",
                    section=section.name if section else None,
                )
            )
        for s in sections:
            seg = x[int(s.start_s * sr) : int(s.end_s * sr)]
            shares = band_shares_db(seg, sr)
            rows.append(
                {
                    "name": s.name,
                    "start_s": round(s.start_s, 3),
                    "end_s": round(s.end_s, 3),
                    "energy": s.energy,
                    "rms_dbfs": round(_rms_dbfs(seg), 2),
                    "bands_db": {k: round(v, 2) for k, v in shares.items()},
                }
            )
            if s.energy is not None and s.energy >= 0.8 and seg.size:
                low = 10.0 * np.log10(10 ** (shares["sub"] / 10) + 10 ** (shares["low"] / 10))
                if low < THIN_LOW_END_DB:
                    issues.append(
                        Issue(
                            "warning",
                            f"thin low end in '{s.name}' (sub+low {low:.1f} dB of total)",
                            hint="Add or raise the kick/bass/808.",
                            section=s.name,
                        )
                    )
        rated = [r for r in rows if r["energy"] is not None]
        for i, a_row in enumerate(rated):
            for b_row in rated[i + 1 :]:
                lo, hi = (a_row, b_row) if a_row["energy"] < b_row["energy"] else (b_row, a_row)
                if (
                    hi["energy"] - lo["energy"] >= ENERGY_GAP
                    and hi["rms_dbfs"] < lo["rms_dbfs"] - LEVEL_SLACK_DB
                ):
                    issues.append(
                        Issue(
                            "warning",
                            f"'{hi['name']}' is quieter than '{lo['name']}' "
                            f"({hi['rms_dbfs']} vs {lo['rms_dbfs']} dBFS)",
                            hint="High-energy sections should be louder and denser.",
                            section=hi["name"],
                        )
                    )

    return AudioReport(
        duration_s=duration,
        sample_rate=sr,
        peak_dbfs=peak,
        rms_dbfs=rms,
        clipping_ratio=clipping,
        silence_spans=spans,
        bands_db=band_shares_db(x, sr) if x.size else {},
        sections=rows,
        issues=issues,
    )
