"""Brief e2e runner (F11.1.4): empty project + free-text brief -> beat -> Tier 0/1 judge.

Gated on FLSTUDIO_MCP_E2E=1 + ANTHROPIC_API_KEY (conftest). Rendering needs
FL Studio installed and closed: FLSTUDIO_RENDER_E2E=1. Quality gates only
assert with FLSTUDIO_BRIEF_STRICT=1 (Q4 exit); the baseline run records data.
Cost: ~$1.5 per brief with history caching (the first uncached run cost ~$7).
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest
from anthropic import AsyncAnthropic

from flstudio_mcp.audio_checks import analyze
from flstudio_mcp.beat_check import build_view, check_beat
from flstudio_mcp.render import RenderError, render_to_wav
from flstudio_mcp.tools.render import sections_for_audio

from .cases.briefs import (
    AGENT_EFFORT,
    AGENT_MODEL,
    BASE_FLP,
    MAX_INPUT_TOKENS,
    MAX_ITERATIONS,
    MAX_OUTPUT_TOKENS,
    USER_PROMPT_TEMPLATE,
    Brief,
    load_briefs,
)
from .harness.agent import AgentConfig, run_agent
from .harness.beat_judge import judge_beat
from .harness.client import open_session
from .harness.invariants import (
    Invariant,
    InvariantContext,
    InvariantResult,
    run_invariants,
)
from .harness.reporting import write_artifacts
from .harness.state_capture import capture_state
from .test_full_song_e2e import _flpdiff_cmd

RENDER_ENV = "FLSTUDIO_RENDER_E2E"
STRICT_ENV = "FLSTUDIO_BRIEF_STRICT"
# Baseline uses today's prompt; F11.3.2 switches brief runs to the shipped make_beat prompt.
PROMPT_PATH = Path(__file__).parent / "harness" / "prompts" / "full_song.md"


def _parses(ctx: InvariantContext) -> InvariantResult:
    ok = ctx.after.describe.get("_type") == "FLPProject"
    return InvariantResult(ok, "after-FLP parses" if ok else "after-FLP did not parse")


def _arranged_32_bars(ctx: InvariantContext) -> InvariantResult:
    bars = build_view(ctx.after.describe).total_bars
    return InvariantResult(bars >= 32, f"{bars:g} bars arranged")


BRIEF_INVARIANTS = [
    Invariant("parses", "hard", _parses),
    Invariant("arranged_32_bars", "soft", _arranged_32_bars),
]


@pytest.fixture(scope="session")
def anthropic_client() -> AsyncAnthropic:
    return AsyncAnthropic()


@pytest.mark.e2e
@pytest.mark.anyio
@pytest.mark.parametrize("brief", load_briefs(), ids=lambda b: b.id)
async def test_brief(brief: Brief, tmp_path: Path, anthropic_client: AsyncAnthropic) -> None:
    scratch = tmp_path / "beat.flp"
    shutil.copy2(BASE_FLP, scratch)
    flpdiff_cmd = _flpdiff_cmd()

    async with open_session(tmp_path / "sess") as ses:
        before = await capture_state(ses, scratch)
        run = await run_agent(
            prompt=USER_PROMPT_TEMPLATE.format(brief=brief.text, path=scratch),
            session=ses,
            cfg=AgentConfig(
                model=AGENT_MODEL,
                effort=AGENT_EFFORT,
                system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
                max_iterations=MAX_ITERATIONS,
                max_input_tokens=MAX_INPUT_TOKENS,
                max_output_tokens_per_turn=MAX_OUTPUT_TOKENS,
            ),
            client=anthropic_client,
            tool_filter=lambda tools: [t for t in tools if t["name"] == "offline_execute"],
        )
        after = await capture_state(ses, scratch)

    ctx = InvariantContext(
        before=before,
        after=after,
        before_path=BASE_FLP,
        after_path=scratch,
        flpdiff_cmd=flpdiff_cmd,
    )
    invariants = run_invariants(BRIEF_INVARIANTS, ctx)
    out_dir = write_artifacts(
        test_id=f"brief_{brief.id}",
        session=ses,  # type: ignore[has-type]
        run=run,
        invariants=invariants,
        judge=None,
        before_path=BASE_FLP,
        after_path=scratch,
        before_state=before,
        after_state=after,
        flpdiff_cmd=flpdiff_cmd,
    )
    meta = asdict(brief) | {
        "agent_model": AGENT_MODEL,
        "effort": AGENT_EFFORT,
        "prompt": PROMPT_PATH.name,
    }
    (out_dir / "brief.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    shutil.copy2(scratch, out_dir / "beat.flp")

    check = check_beat(after.describe, brief.genre).to_dict()
    (out_dir / "check_beat.json").write_text(json.dumps(check, indent=2), encoding="utf-8")

    wav: Path | None = None
    audio: dict | None = None
    if os.environ.get(RENDER_ENV) == "1":
        try:
            rendered = render_to_wav(scratch)
            wav = out_dir / "render.wav"
            shutil.copy2(rendered.wav_path, wav)
            audio = analyze(wav, sections_for_audio(after.describe)).to_dict()
            (out_dir / "audio.json").write_text(json.dumps(audio, indent=2), encoding="utf-8")
        except RenderError as exc:
            print(f"\nrender failed: {exc.code}: {exc.message}")

    report = await judge_beat(
        client=anthropic_client,
        brief=brief.text,
        genre=brief.genre,
        check=check,
        wav=wav,
        audio=audio,
        agent_summary=run.final_text,
    )
    (out_dir / "beat_judge.json").write_text(json.dumps(report.to_dict(), indent=2, default=str))

    print(f"\nartifacts -> {out_dir}")
    print(f"agent: terminated={run.terminated} iterations={run.iterations}")
    print(invariants.summary())
    print(f"judge: passed={report.passed} reasons={report.reasons}")

    assert run.terminated not in ("exception",), f"infra failure: {run.final_text[:300]}"
    assert invariants.passed_hard, invariants.summary()
    if os.environ.get(STRICT_ENV) == "1":
        assert all(res.passed for _inv, res in invariants.results), invariants.summary()
        assert report.passed, report.reasons
