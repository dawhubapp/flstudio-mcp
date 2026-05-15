"""End-to-end full-song-edit tests (Flow 2).

Drives a Claude tool-use loop against the MCP server with the
``full_song.md`` system prompt; verifies the post-state has the
expected musical delta (note count growth, optional new pattern /
channel) and grades with Claude.

Gated on ``FLSTUDIO_MCP_E2E=1`` + ``ANTHROPIC_API_KEY`` (handled by
``conftest.py``). Cost budget per case is in ``FULL_SONG_CASES``.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from anthropic import AsyncAnthropic

from .cases.full_song import FULL_SONG_CASES, FullSongCase
from .harness.agent import AgentConfig, run_agent
from .harness.client import open_session
from .harness.invariants import InvariantContext, run_invariants
from .harness.invariants_song import build_invariants
from .harness.judge import call_judge
from .harness.reporting import write_artifacts
from .harness.state_capture import capture_state
from .harness.visual_gate import VisualGateRunResult, run_visual_gates

VISUAL_GATE_ENV = "FLSTUDIO_VISUAL_GATE"

FULL_SONG_PROMPT_PATH = Path(__file__).parent / "harness" / "prompts" / "full_song.md"
JUDGE_RUBRIC_SONG_PATH = Path(__file__).parent / "harness" / "prompts" / "judge_rubric_song.md"


def _flpdiff_cmd() -> list[str]:
    here = Path(__file__).resolve()
    for parent in here.parents:
        cli = parent / "flpdiff" / "src" / "cli.ts"
        if cli.is_file():
            bun = shutil.which("bun")
            if bun:
                return [bun, "run", str(cli)]
    return ["flpdiff"]


@pytest.fixture(scope="session")
def anthropic_client() -> AsyncAnthropic:
    return AsyncAnthropic()


@pytest.mark.e2e
@pytest.mark.anyio
@pytest.mark.parametrize("case", FULL_SONG_CASES, ids=lambda c: c.id)
async def test_full_song(
    case: FullSongCase,
    tmp_path: Path,
    anthropic_client: AsyncAnthropic,
) -> None:
    if not case.input_flp.exists():
        pytest.skip(f"fixture missing (likely gitignored): {case.input_flp}")

    scratch = tmp_path / case.input_flp.name
    shutil.copy2(case.input_flp, scratch)

    flpdiff_cmd = _flpdiff_cmd()
    system_prompt = FULL_SONG_PROMPT_PATH.read_text(encoding="utf-8")
    user_prompt = case.user_prompt + str(scratch)

    async with open_session(tmp_path / "sess") as ses:
        before = await capture_state(ses, scratch)
        run = await run_agent(
            prompt=user_prompt,
            session=ses,
            cfg=AgentConfig(
                model=case.model,
                system_prompt=system_prompt,
                max_iterations=case.max_iterations,
                max_input_tokens=case.max_input_tokens,
                max_output_tokens_per_turn=case.max_output_tokens_per_turn,
            ),
            client=anthropic_client,
            tool_filter=lambda tools: [t for t in tools if t["name"] == "offline_execute"],
        )
        after = await capture_state(ses, scratch)

    invariants_list = build_invariants(
        min_notes_added=case.min_notes_added,
        min_controllers_added=case.min_controllers_added,
        expect_new_pattern=case.expect_new_pattern,
        expect_new_channel=case.expect_new_channel,
        expect_plugin_params=case.expect_plugin_params,
        expect_channel_levels=case.expect_channel_levels,
        expect_channel_sample_paths=case.expect_channel_sample_paths,
    )
    ctx = InvariantContext(
        before=before,
        after=after,
        before_path=case.input_flp,
        after_path=scratch,
        flpdiff_cmd=flpdiff_cmd,
    )
    invariants = run_invariants(invariants_list, ctx)

    judge = None
    judge_error: str | None = None
    try:
        judge = await call_judge(
            before=before,
            after=after,
            run=run,
            invariants=invariants,
            client=anthropic_client,
            model=case.judge_model,
            rubric_path=JUDGE_RUBRIC_SONG_PATH,
            task_label="full-song-edit",
        )
    except Exception as exc:
        judge_error = repr(exc)

    out_dir = write_artifacts(
        test_id=case.id,
        session=ses,  # type: ignore[has-type]
        run=run,
        invariants=invariants,
        judge=judge,
        before_path=case.input_flp,
        after_path=scratch,
        before_state=before,
        after_state=after,
        flpdiff_cmd=flpdiff_cmd,
    )

    gate_results: list[VisualGateRunResult] = []
    gate_failures: list[str] = []
    if case.visual_gates and os.environ.get(VISUAL_GATE_ENV) == "1":
        try:
            gate_results = await run_visual_gates(
                expectations=case.visual_gates,
                flp_path=scratch,
                artifact_dir=out_dir,
                client=anthropic_client,
            )
        except Exception as exc:
            print(f"\nvisual_gate orchestration failed: {exc!r}")
        else:
            (out_dir / "visual_gates.json").write_text(
                json.dumps(
                    [
                        {
                            "name": r.name,
                            "question": r.question,
                            "passed": r.verdict.passed,
                            "rationale": r.verdict.rationale,
                            "evidence": r.verdict.evidence,
                            "must_pass": r.must_pass,
                            "image_path": str(r.image_path),
                        }
                        for r in gate_results
                    ],
                    indent=2,
                ),
                encoding="utf-8",
            )
            print("\nvisual gates:")
            for r in gate_results:
                mark = "PASS" if r.verdict.passed else "FAIL"
                hard = " (HARD)" if r.must_pass else ""
                print(f"  [{mark}]{hard} {r.name}: {r.verdict.rationale[:120]}")
                if r.must_pass and not r.verdict.passed:
                    gate_failures.append(f"{r.name}: {r.verdict.rationale}")
    elif case.visual_gates:
        print(f"\nvisual gates skipped ({VISUAL_GATE_ENV}!=1)")

    print(f"\nartifacts -> {out_dir}")
    print(
        f"agent: terminated={run.terminated} iterations={run.iterations} tokens={run.total_input_tokens}"
    )
    print(invariants.summary())

    assert (
        run.terminated != "exception"
    ), f"agent terminated={run.terminated}: {run.final_text[:300]} (infra failure)"
    assert invariants.passed_hard, f"hard invariants failed:\n{invariants.summary()}"
    assert judge is not None, f"judge call failed: {judge_error}"
    assert judge.grade >= case.min_grade, (
        f"judge grade {judge.grade} < {case.min_grade}\n"
        f"rationale: {judge.rationale}\n"
        f"weaknesses: {judge.weaknesses}"
    )
    assert not gate_failures, "hard visual gates failed:\n  " + "\n  ".join(gate_failures)
