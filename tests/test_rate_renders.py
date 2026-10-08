"""Tests for scripts/rate_renders.py (no audio playback)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from click.testing import CliRunner

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rate_renders.py"


def _load():
    spec = importlib.util.spec_from_file_location("rate_renders", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run_dir(base: Path, name: str, *, wav: bool = True) -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "brief.json").write_text(
        json.dumps({"id": "house_deep", "genre": "house", "text": "deep"})
    )
    (d / "beat_judge.json").write_text(
        json.dumps({"passed": False, "reasons": ["x"], "rubric": {"grade": 2}, "models": []})
    )
    if wav:
        (d / "render.wav").write_bytes(b"RIFF")
    return d


def test_rates_pending_runs_and_marks_keep(tmp_path: Path) -> None:
    rr = _load()
    runs = tmp_path / "runs"
    first = _run_dir(runs, "20261010-a")
    _run_dir(runs, "20261010-b", wav=False)  # no render -> not rateable
    ratings = tmp_path / "ratings.jsonl"
    result = CliRunner().invoke(
        rr.main,
        ["--runs-dir", str(runs), "--ratings", str(ratings), "--no-play"],
        input="2\nn\nflat hats\n",
    )
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in ratings.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["run_id"] == "20261010-a" and rows[0]["rating_1_5"] == 2
    assert rows[0]["play_for_friend"] is False and rows[0]["note"] == "flat hats"
    assert rows[0]["judge"]["rubric_grade"] == 2
    assert (first / rr.KEEP_MARKER).exists()
    again = CliRunner().invoke(
        rr.main, ["--runs-dir", str(runs), "--ratings", str(ratings), "--no-play"]
    )
    assert "Nothing to rate" in again.output


def test_keep_marker_matches_reporting() -> None:
    from tests.e2e.harness.reporting import KEEP_MARKER

    assert _load().KEEP_MARKER == KEEP_MARKER
