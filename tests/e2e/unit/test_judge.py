"""Unit tests for the judge using ScriptedResponder."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ..harness.agent import (
    FakeMessage,
    FakeTextBlock,
    FakeToolUseBlock,
    FakeUsage,
    ScriptedResponder,
)
from ..harness.invariants import (
    Invariant,
    InvariantContext,
    InvariantResult,
    run_invariants,
)
from ..harness.judge import JUDGE_TOOL_NAME, call_judge, load_rubric
from ..harness.state_capture import FLPState


def _state(**overrides: Any) -> FLPState:
    return FLPState(
        path=Path("/tmp/x.flp"),
        describe={},
        channels=overrides.get("channels", []),
        mixer=overrides.get("mixer", []),
        patterns=overrides.get("patterns", []),
        tracks=overrides.get("tracks", []),
        clips=overrides.get("clips", []),
        arrangements=overrides.get("arrangements", []),
    )


def _trivial_invariant_report():
    inv = Invariant(
        "trivial",
        "hard",
        lambda _ctx: InvariantResult(passed=True, detail="ok"),
    )
    ctx = InvariantContext(
        before=_state(),
        after=_state(),
        before_path=Path("/tmp/a"),
        after_path=Path("/tmp/b"),
    )
    return run_invariants([inv], ctx)


@pytest.mark.anyio
async def test_call_judge_extracts_grade_from_tool_use() -> None:
    """A submit_grade tool_use → JudgeVerdict with parsed fields."""
    responder = ScriptedResponder(
        messages_to_emit=[
            FakeMessage(
                content=[
                    FakeToolUseBlock(
                        id="t1",
                        name=JUDGE_TOOL_NAME,
                        input={
                            "grade": 4,
                            "rationale": "solid run",
                            "strengths": ["clear plan"],
                            "weaknesses": ["one redundant call"],
                        },
                    )
                ],
                stop_reason="tool_use",
                usage=FakeUsage(input_tokens=500, output_tokens=80),
            )
        ]
    )
    from ..harness.agent import AgentRun

    verdict = await call_judge(
        before=_state(),
        after=_state(),
        run=AgentRun(),
        invariants=_trivial_invariant_report(),
        client=responder,
    )
    assert verdict.grade == 4
    assert verdict.rationale == "solid run"
    assert verdict.strengths == ["clear plan"]
    assert verdict.weaknesses == ["one redundant call"]


@pytest.mark.anyio
async def test_call_judge_raises_when_no_tool_call() -> None:
    """Defensive: judge response with no submit_grade tool_use → raises."""
    responder = ScriptedResponder(
        messages_to_emit=[
            FakeMessage(content=[FakeTextBlock("oops, just text")], stop_reason="end_turn"),
        ]
    )
    from ..harness.agent import AgentRun

    with pytest.raises(RuntimeError, match=JUDGE_TOOL_NAME):
        await call_judge(
            before=_state(),
            after=_state(),
            run=AgentRun(),
            invariants=_trivial_invariant_report(),
            client=responder,
        )


@pytest.mark.anyio
async def test_call_judge_passes_rubric_in_system() -> None:
    """The rubric loaded from disk is sent as system + cache_control."""
    responder = ScriptedResponder(
        messages_to_emit=[
            FakeMessage(
                content=[
                    FakeToolUseBlock(
                        id="t1",
                        name=JUDGE_TOOL_NAME,
                        input={"grade": 3, "rationale": "ok"},
                    )
                ],
                stop_reason="tool_use",
            )
        ]
    )
    from ..harness.agent import AgentRun

    await call_judge(
        before=_state(),
        after=_state(),
        run=AgentRun(),
        invariants=_trivial_invariant_report(),
        client=responder,
        rubric="custom rubric text",
    )
    [call] = responder.calls
    [sys_block] = call["system"]
    assert sys_block["text"] == "custom rubric text"
    assert sys_block["cache_control"] == {"type": "ephemeral"}
    # tool_choice forces submit_grade
    assert call["tool_choice"] == {"type": "tool", "name": JUDGE_TOOL_NAME}


def test_load_rubric_returns_text() -> None:
    """The bundled rubric file resolves and is non-empty."""
    text = load_rubric()
    assert "1" in text and "5" in text  # mentions the grading scale
    assert len(text) > 100
