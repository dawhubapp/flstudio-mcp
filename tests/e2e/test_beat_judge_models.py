"""Real-model smoke test for Tier 1 scorers (downloads weights on first run)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from .harness.beat_judge import score_audiobox, score_clap


@pytest.mark.e2e
def test_models_score_a_synthetic_beat(tmp_path: Path) -> None:
    sr = 44100
    t = np.arange(8 * sr) / sr
    kick = np.sin(2 * np.pi * 55 * t) * (np.mod(t, 0.5) < 0.12)
    hat = np.random.default_rng(0).normal(0, 0.05, t.size) * (np.mod(t + 0.25, 0.5) < 0.03)
    wav = tmp_path / "beat.wav"
    sf.write(str(wav), (0.6 * kick + hat).astype(np.float32), sr)

    audiobox = score_audiobox(wav)
    print("audiobox:", audiobox)
    assert audiobox.available, audiobox.note
    assert set(audiobox.scores) >= {"PQ", "CE"}

    clap = score_clap(wav, "a house beat with a four-on-the-floor kick", "house")
    print("clap:", clap)
    assert clap.available, clap.note
    genre_total = sum(v for k, v in clap.scores.items() if k.startswith("genre_"))
    assert genre_total == pytest.approx(1.0, abs=1e-3)
    # Must actually discriminate: uniform over brief + 4 contrasts would be 0.2.
    assert clap.scores["brief_match"] > 0.4, clap.scores
