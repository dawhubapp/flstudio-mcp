"""Rate rendered brief runs for judge calibration (F11.1.5).

Usage (from mcp/):  uv run python scripts/rate_renders.py [--no-play]

Lists run dirs under tests/e2e/runs that have a render.wav and no rating
yet, plays each render (afplay), asks for a 1-5 rating + "would you play
it for a producer friend?" + a note, appends a row to
tests/e2e/calibration/ratings.jsonl, and drops a KEEP marker so the run
survives the harness's run-dir pruning.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

MCP_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = MCP_ROOT / "tests" / "e2e" / "runs"
RATINGS_PATH = MCP_ROOT / "tests" / "e2e" / "calibration" / "ratings.jsonl"
KEEP_MARKER = "KEEP"  # must match tests/e2e/harness/reporting.py::KEEP_MARKER


def rated_ids(ratings: Path) -> set[str]:
    if not ratings.is_file():
        return set()
    lines = ratings.read_text().splitlines()
    return {json.loads(line)["run_id"] for line in lines if line.strip()}


def pending_runs(runs_dir: Path, ratings: Path) -> list[Path]:
    done = rated_ids(ratings)
    if not runs_dir.is_dir():
        return []
    return sorted(
        d
        for d in runs_dir.iterdir()
        if d.is_dir()
        and (d / "render.wav").is_file()
        and (d / "brief.json").is_file()
        and d.name not in done
    )


def judge_snapshot(run_dir: Path) -> dict[str, Any]:
    """The judge's view at rating time, kept with the rating (run dirs can be pruned)."""
    path = run_dir / "beat_judge.json"
    if not path.is_file():
        return {}
    report = json.loads(path.read_text())
    models = {m["name"]: m for m in report.get("models") or []}
    return {
        "passed": report.get("passed"),
        "reasons": report.get("reasons"),
        "rubric_grade": (report.get("rubric") or {}).get("grade"),
        "audiobox_pq": (models.get("audiobox_aesthetics") or {}).get("scores", {}).get("PQ"),
        "clap_top_genre": (models.get("clap") or {}).get("top_genre"),
    }


@click.command()
@click.option("--runs-dir", type=click.Path(path_type=Path, file_okay=False), default=RUNS_DIR)
@click.option("--ratings", type=click.Path(path_type=Path, dir_okay=False), default=RATINGS_PATH)
@click.option("--play/--no-play", default=True, help="Play each render with afplay first.")
def main(runs_dir: Path, ratings: Path, play: bool) -> None:
    """Rate every rendered, unrated brief run."""
    runs = pending_runs(runs_dir, ratings)
    if not runs:
        click.echo("Nothing to rate.")
        return
    for run_dir in runs:
        brief = json.loads((run_dir / "brief.json").read_text())
        snap = judge_snapshot(run_dir)
        click.echo(f"\n== {run_dir.name}: {brief['id']} ({brief['genre']})\n   {brief['text']}")
        click.echo(f"   judge: {snap}")
        wav = run_dir / "render.wav"
        if play:
            subprocess.run(["afplay", str(wav)], check=False)
        else:
            click.echo(f"   wav: {wav}")
        rating = click.prompt("   rating 1-5", type=click.IntRange(1, 5))
        friend = click.confirm("   would you play it for a producer friend?", default=False)
        note = click.prompt("   note", default="", show_default=False)
        row = {
            "run_id": run_dir.name,
            "brief_id": brief["id"],
            "rating_1_5": rating,
            "play_for_friend": friend,
            "note": note,
            "judge": snap,
            "rated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        ratings.parent.mkdir(parents=True, exist_ok=True)
        with ratings.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        (run_dir / KEEP_MARKER).write_text("rated\n")


if __name__ == "__main__":
    main()
