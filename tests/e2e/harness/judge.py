"""Claude-as-judge grading for the e2e harness.

After the agent run + invariant validator finish, ``call_judge()``
makes one Anthropic call asking Claude to grade the result on a 1-5
scale per a static rubric. The judge sees:

- Pre/post state summaries (compact diff, not the full describe)
- Invariant report (which passed/failed, with details)
- The agent's transcript (last N turns if over budget)

The judge submits its verdict via a single ``submit_grade`` tool — we
extract the structured payload from that call rather than parsing free
text. ~$0.05 per judgment with prompt caching on the rubric.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent import AgentRun, AnthropicClient, _block_kind, _tool_use_fields
from .invariants import InvariantReport
from .state_capture import FLPState

JUDGE_TOOL_NAME = "submit_grade"
JUDGE_RUBRIC_PATH = Path(__file__).parent / "prompts" / "judge_rubric.md"
DEFAULT_JUDGE_MODEL = "claude-opus-4-7"


@dataclass
class JudgeVerdict:
    grade: int  # 1..5
    rationale: str
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    raw_text: str = ""


JUDGE_TOOL_DEF: dict[str, Any] = {
    "name": JUDGE_TOOL_NAME,
    "description": "Submit your grade for the agent's reorganize attempt.",
    "input_schema": {
        "type": "object",
        "required": ["grade", "rationale"],
        "properties": {
            "grade": {"type": "integer", "minimum": 1, "maximum": 5},
            "rationale": {"type": "string"},
            "strengths": {"type": "array", "items": {"type": "string"}},
            "weaknesses": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def load_rubric() -> str:
    if JUDGE_RUBRIC_PATH.exists():
        return JUDGE_RUBRIC_PATH.read_text(encoding="utf-8")
    return "Grade the agent's reorganize attempt 1-5."


def _state_summary(state: FLPState) -> dict[str, Any]:
    """Compact dict — drops verbose describe payload."""
    return {
        "channels": [
            {
                "iid": c.get("iid"),
                "name": c.get("name"),
                "color": c.get("color"),
                "routing": c.get("routing"),
            }
            for c in state.channels[:50]
        ],
        "mixer": [
            {"index": m.get("index"), "name": m.get("name"), "color": m.get("color")}
            for m in state.mixer[:30]
        ],
        "patterns": [
            {"iid": p.get("iid"), "name": p.get("name"), "color": p.get("color")}
            for p in state.patterns[:30]
        ],
        "tracks": [
            {"index": t.get("index"), "name": t.get("name"), "color": t.get("color")}
            for t in state.tracks[:30]
            if t.get("name") or t.get("color")
        ],
        "clip_count": len(state.clips),
    }


def _transcript_summary(run: AgentRun, max_turns: int = 20) -> list[dict[str, Any]]:
    turns = run.turns[-max_turns:] if len(run.turns) > max_turns else run.turns
    return [
        {
            "iteration": t.iteration,
            "stop_reason": t.stop_reason,
            "text": " ".join(t.text_blocks)[:400],
            "tool_calls": [
                {"name": tc["name"], "args": tc["args"], "ok": tc["ok"]} for tc in t.tool_calls
            ],
        }
        for t in turns
    ]


def _invariant_summary(report: InvariantReport) -> list[dict[str, Any]]:
    return [
        {
            "name": inv.name,
            "kind": inv.kind,
            "passed": res.passed,
            "detail": res.detail,
        }
        for inv, res in report.results
    ]


async def call_judge(
    *,
    before: FLPState,
    after: FLPState,
    run: AgentRun,
    invariants: InvariantReport,
    client: AnthropicClient,
    model: str = DEFAULT_JUDGE_MODEL,
    rubric: str | None = None,
) -> JudgeVerdict:
    """Call Claude to grade the agent's run. One API call.

    The model is forced to use ``submit_grade`` via ``tool_choice``
    so we don't have to parse free text.
    """
    rubric_text = rubric if rubric is not None else load_rubric()
    payload = {
        "before": _state_summary(before),
        "after": _state_summary(after),
        "invariants": _invariant_summary(invariants),
        "transcript": _transcript_summary(run),
        "agent_terminated": run.terminated,
        "iterations": run.iterations,
    }
    user_text = (
        "Grade this reorganize-project attempt against the rubric. "
        "Submit your verdict via the submit_grade tool.\n\n"
        f"Data:\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}\n```"
    )

    message = await client.messages.create(
        model=model,
        max_tokens=2048,
        system=[{"type": "text", "text": rubric_text, "cache_control": {"type": "ephemeral"}}],
        tools=[JUDGE_TOOL_DEF],
        tool_choice={"type": "tool", "name": JUDGE_TOOL_NAME},
        messages=[{"role": "user", "content": user_text}],
    )

    raw_blocks: list[str] = []
    grade_input: dict[str, Any] | None = None
    for block in message.content:
        kind = _block_kind(block)
        if kind == "text":
            raw_blocks.append(
                getattr(block, "text", "") if not isinstance(block, dict) else block.get("text", "")
            )
        elif kind == "tool_use":
            _, name, args = _tool_use_fields(block)
            if name == JUDGE_TOOL_NAME:
                grade_input = args

    if grade_input is None:
        raise RuntimeError(
            f"judge did not call {JUDGE_TOOL_NAME!r}; raw text={' '.join(raw_blocks)[:400]!r}"
        )

    return JudgeVerdict(
        grade=int(grade_input.get("grade", 0)),
        rationale=str(grade_input.get("rationale", "")),
        strengths=[str(s) for s in (grade_input.get("strengths") or [])],
        weaknesses=[str(w) for w in (grade_input.get("weaknesses") or [])],
        raw_text="\n".join(raw_blocks),
    )
