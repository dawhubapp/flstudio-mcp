"""Unit tests for per-run artifact dumping."""

from __future__ import annotations

import json
from pathlib import Path

from ..harness.agent import AgentRun, AgentTurn
from ..harness.client import HarnessSession, TranscriptEntry
from ..harness.invariants import (
    Invariant,
    InvariantContext,
    InvariantResult,
    run_invariants,
)
from ..harness.judge import JudgeVerdict
from ..harness.reporting import _prune_runs, write_artifacts
from ..harness.state_capture import FLPState


def _fake_session(transcript: list[TranscriptEntry] | None = None) -> HarnessSession:
    """Build a HarnessSession without spawning a real subprocess.

    Only `.transcript` is used by reporting, so we skip the
    ClientSession constructor entirely.
    """
    obj = object.__new__(HarnessSession)
    obj.session = None  # type: ignore[assignment]
    obj.scratch_dir = Path("/tmp")
    obj.transcript = transcript or []
    obj.tool_schemas = []
    return obj


def _state() -> FLPState:
    return FLPState(path=Path("/tmp/x.flp"), describe={})


def _trivial_report():
    inv = Invariant("trivial", "hard", lambda _c: InvariantResult(passed=True, detail="ok"))
    ctx = InvariantContext(
        before=_state(),
        after=_state(),
        before_path=Path("/tmp/a"),
        after_path=Path("/tmp/b"),
    )
    return run_invariants([inv], ctx)


def test_write_artifacts_creates_all_expected_files(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    session = _fake_session(
        transcript=[
            TranscriptEntry(
                name="offline_execute",
                args={"kind": "describe"},
                structured={"ok": True, "kind": "describe", "result": {}},
                is_error=False,
                latency_ms=12.5,
            ),
        ]
    )
    run = AgentRun(
        turns=[AgentTurn(iteration=1, stop_reason="end_turn", text_blocks=["ok"])],
        terminated="end_turn",
        elapsed_s=1.23,
        final_text="ok",
    )
    judge = JudgeVerdict(grade=4, rationale="solid", strengths=["s1"], weaknesses=[])

    flp = tmp_path / "x.flp"
    flp.write_bytes(b"FAKE")  # flpdiff will fail on this; reporting must still write meta

    out_dir = write_artifacts(
        test_id="smoke",
        runs_dir=runs_dir,
        session=session,
        run=run,
        invariants=_trivial_report(),
        judge=judge,
        before_path=flp,
        after_path=flp,
        flpdiff_cmd=["/nonexistent/flpdiff"],  # forces graceful fallback
    )

    assert out_dir.exists()
    assert (out_dir / "transcript.jsonl").exists()
    assert (out_dir / "agent_run.json").exists()
    assert (out_dir / "before.flp.info.json").exists()
    assert (out_dir / "after.flp.info.json").exists()
    assert (out_dir / "diff.txt").exists()
    assert (out_dir / "invariants.json").exists()
    assert (out_dir / "judge.json").exists()
    assert (out_dir / "meta.json").exists()

    meta = json.loads((out_dir / "meta.json").read_text())
    assert meta["test_id"] == "smoke"
    assert meta["iterations"] == 1
    assert meta["terminated"] == "end_turn"
    assert meta["judge_grade"] == 4

    # Transcript: one JSON line per entry.
    lines = (out_dir / "transcript.jsonl").read_text().strip().split("\n")
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["name"] == "offline_execute"


def test_write_artifacts_handles_missing_judge(tmp_path: Path) -> None:
    """A run that hit iter_cap before the judge ran still produces a bundle."""
    runs_dir = tmp_path / "runs"
    flp = tmp_path / "x.flp"
    flp.write_bytes(b"")
    out_dir = write_artifacts(
        test_id="iter_cap_run",
        runs_dir=runs_dir,
        session=_fake_session(),
        run=AgentRun(terminated="iter_cap"),
        invariants=_trivial_report(),
        judge=None,
        before_path=flp,
        after_path=flp,
        flpdiff_cmd=["/nonexistent/flpdiff"],
    )
    assert (out_dir / "meta.json").exists()
    assert not (out_dir / "judge.json").exists()
    meta = json.loads((out_dir / "meta.json").read_text())
    assert meta["judge_grade"] is None
    assert meta["terminated"] == "iter_cap"


def test_prune_runs_keeps_newest_n(tmp_path: Path) -> None:
    base = tmp_path / "runs"
    base.mkdir()
    # Create 25 dirs with sortable names.
    for i in range(25):
        (base / f"2026-01-01-{i:03d}").mkdir()
    _prune_runs(base, retention=20)
    remaining = sorted(p.name for p in base.iterdir())
    assert len(remaining) == 20
    # The newest 20 names (sorted desc) survive.
    assert remaining[0].endswith("005")
    assert remaining[-1].endswith("024")


def test_prune_runs_no_op_when_under_retention(tmp_path: Path) -> None:
    base = tmp_path / "runs"
    base.mkdir()
    for i in range(5):
        (base / f"2026-01-01-{i:03d}").mkdir()
    _prune_runs(base, retention=20)
    assert len(list(base.iterdir())) == 5
