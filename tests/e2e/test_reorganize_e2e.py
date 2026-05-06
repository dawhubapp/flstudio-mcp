"""End-to-end reorganize-flow tests.

Runs the full pipeline: capture before-state → drive a Claude tool-use
loop against the MCP server → capture after-state → run hard
invariants → grade with Claude → write artifacts.

Gated on ``FLSTUDIO_MCP_E2E=1`` + ``ANTHROPIC_API_KEY`` (handled by
``conftest.py`` — these tests skip gracefully without both). Cost
budget per case is in ``REORGANIZE_CASES``.

Local-only cases (e.g. ``bass_sketch_real``) skip themselves if their
gitignored fixture isn't present.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from anthropic import AsyncAnthropic

from .cases.reorganize import REORGANIZE_CASES, ReorganizeCase
from .harness.agent import AgentConfig, run_agent
from .harness.client import open_session
from .harness.invariants import REORGANIZE_INVARIANTS, InvariantContext, run_invariants
from .harness.judge import call_judge
from .harness.reporting import write_artifacts
from .harness.state_capture import capture_state

REORGANIZE_PROMPT_PATH = Path(__file__).parent / "harness" / "prompts" / "reorganize.md"


def _flpdiff_cmd() -> list[str]:
    """Return the flpdiff CLI argv used by reporting + invariants.

    Prefer the sibling dev workspace (`flpdiff/src/cli.ts` via bun)
    so we exercise the same code the bridge uses; fall back to
    `flpdiff` on PATH.
    """
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
@pytest.mark.parametrize("case", REORGANIZE_CASES, ids=lambda c: c.id)
async def test_reorganize(
    case: ReorganizeCase,
    tmp_path: Path,
    anthropic_client: AsyncAnthropic,
) -> None:
    if not case.input_flp.exists():
        pytest.skip(f"fixture missing (likely gitignored): {case.input_flp}")

    scratch = tmp_path / case.input_flp.name
    shutil.copy2(case.input_flp, scratch)

    flpdiff_cmd = _flpdiff_cmd()
    system_prompt = REORGANIZE_PROMPT_PATH.read_text(encoding="utf-8")
    user_prompt = (
        f"Reorganize this FL Studio project. Path: {scratch}\n\n"
        "Inspect, plan, then execute. Use offline_execute exclusively."
    )

    async with open_session(tmp_path / "sess") as ses:
        before = await capture_state(ses, scratch)
        run = await run_agent(
            prompt=user_prompt,
            session=ses,
            cfg=AgentConfig(
                model=case.model,
                system_prompt=system_prompt,
                max_iterations=case.max_iterations,
            ),
            client=anthropic_client,
            tool_filter=lambda tools: [t for t in tools if t["name"] == "offline_execute"],
        )
        after = await capture_state(ses, scratch)

    ctx = InvariantContext(
        before=before,
        after=after,
        before_path=case.input_flp,
        after_path=scratch,
        flpdiff_cmd=flpdiff_cmd,
    )
    invariants = run_invariants(REORGANIZE_INVARIANTS, ctx)

    judge = None
    judge_error: str | None = None
    try:
        judge = await call_judge(
            before=before,
            after=after,
            run=run,
            invariants=invariants,
            client=anthropic_client,
        )
    except Exception as exc:
        judge_error = repr(exc)

    # Always write artifacts, regardless of pass/fail — they're the
    # forensic trail for whatever went wrong.
    out_dir = write_artifacts(
        test_id=case.id,
        session=ses,  # type: ignore[has-type] — `ses` is bound by the async with above
        run=run,
        invariants=invariants,
        judge=judge,
        before_path=case.input_flp,
        after_path=scratch,
        before_state=before,
        after_state=after,
        flpdiff_cmd=flpdiff_cmd,
    )
    print(f"\nartifacts -> {out_dir}")
    print(
        f"agent: terminated={run.terminated} iterations={run.iterations} tokens={run.total_input_tokens}"
    )
    print(invariants.summary())

    assert run.terminated in {
        "end_turn",
        "iter_cap",
    }, f"agent terminated={run.terminated} (likely infra failure)"
    assert invariants.passed_hard, f"hard invariants failed:\n{invariants.summary()}"
    assert judge is not None, f"judge call failed: {judge_error}"
    assert judge.grade >= case.min_grade, (
        f"judge grade {judge.grade} < {case.min_grade}\n"
        f"rationale: {judge.rationale}\n"
        f"weaknesses: {judge.weaknesses}"
    )
