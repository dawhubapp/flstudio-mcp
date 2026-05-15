"""Visual acceptance gate — open mutated FLP in FL, screenshot, ask Claude.

After an offline mutation has shipped an FLP, the "did this look right
in FL?" question has been a manual step (open in FL, eyeball the
playlist / channel rack, save, byte-diff). This module automates that
loop:

1. ``autodrive.open_flp(path)`` cold-loads the FLP in FL Studio.
2. ``autodrive.screenshot_fl_window(png)`` captures FL's frontmost
   window via macOS ``screencapture`` (silent).
3. ``visual_gate(client, png, question)`` POSTs the PNG to Claude
   with a structured-output tool, returns ``{passed, rationale}``.

The intent is a soft invariant — visual gates fire only when the env
var ``FLSTUDIO_VISUAL_GATE`` is set (FL must be running, costs ~$0.01
per gate, blocks ~30 s while FL boots + Claude responds). Hard
invariants stay byte-level.

Why a tool-choice call instead of a free-form prompt: forces Claude to
return ``{passed: bool, rationale: str}`` even when the answer is
ambiguous, so the harness can grade without parsing prose.
"""

from __future__ import annotations

import base64
import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent import AnthropicClient, _block_kind, _tool_use_fields

DEFAULT_GATE_MODEL = "claude-sonnet-4-6"
GATE_TOOL_NAME = "submit_verdict"
GATE_TOOL_DEF: dict[str, Any] = {
    "name": GATE_TOOL_NAME,
    "description": (
        "Submit a pass/fail verdict on whether the FL Studio screenshot "
        "matches the question. Be conservative — only PASS if the visible "
        "evidence clearly answers YES."
    ),
    "input_schema": {
        "type": "object",
        "required": ["passed", "rationale"],
        "properties": {
            "passed": {"type": "boolean"},
            "rationale": {
                "type": "string",
                "description": "One short paragraph citing what's visible.",
            },
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of specific UI elements you saw.",
            },
        },
    },
}


@dataclass
class VisualGateVerdict:
    passed: bool
    rationale: str
    evidence: list[str]
    raw_text: str = ""


def _encode_image_b64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("ascii")


async def visual_gate(
    *,
    client: AnthropicClient,
    image_path: Path,
    question: str,
    model: str = DEFAULT_GATE_MODEL,
    max_tokens: int = 512,
) -> VisualGateVerdict:
    """Ask Claude to verify the screenshot answers ``question`` with YES.

    Forces tool use so the response is always
    ``{passed: bool, rationale: str, evidence?: list[str]}``.
    """
    if not image_path.exists():
        raise FileNotFoundError(f"visual_gate: image not found at {image_path}")
    image_b64 = _encode_image_b64(image_path)
    user_blocks: list[dict[str, Any]] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": image_b64,
            },
        },
        {
            "type": "text",
            "text": (
                "You are an FL Studio acceptance reviewer. Given the "
                "screenshot above, decide whether the answer to the "
                "following question is YES. Submit your verdict via the "
                f"submit_verdict tool.\n\nQuestion: {question}"
            ),
        },
    ]
    message = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        tools=[GATE_TOOL_DEF],
        tool_choice={"type": "tool", "name": GATE_TOOL_NAME},
        messages=[{"role": "user", "content": user_blocks}],
    )

    verdict_args: dict[str, Any] | None = None
    raw_blocks: list[str] = []
    for block in message.content:
        kind = _block_kind(block)
        if kind == "text":
            text = (
                getattr(block, "text", "") if not isinstance(block, dict) else block.get("text", "")
            )
            raw_blocks.append(text)
        elif kind == "tool_use":
            _, name, args = _tool_use_fields(block)
            if name == GATE_TOOL_NAME:
                verdict_args = args

    if verdict_args is None:
        raise RuntimeError(
            "visual_gate: model did not call submit_verdict — "
            f"raw text was: {' '.join(raw_blocks)!r}"
        )

    return VisualGateVerdict(
        passed=bool(verdict_args.get("passed", False)),
        rationale=str(verdict_args.get("rationale", "")),
        evidence=list(verdict_args.get("evidence", []) or []),
        raw_text="\n".join(raw_blocks),
    )


@dataclass
class VisualGateRunResult:
    """Per-expectation verdict produced by :func:`run_visual_gates`."""

    name: str
    question: str
    verdict: VisualGateVerdict
    image_path: Path
    must_pass: bool


async def run_visual_gates(
    *,
    expectations: list[Any],  # list[VisualGateExpectation] — imported lazily
    flp_path: Path,
    artifact_dir: Path,
    client: AnthropicClient,
    model: str = DEFAULT_GATE_MODEL,
    boot_wait_seconds: float = 18.0,
    close_after: bool = True,
) -> list[VisualGateRunResult]:
    """Open ``flp_path`` in FL, screenshot, ask Claude each question.

    Returns one ``VisualGateRunResult`` per expectation. Side effects:
    drives FL via macOS automation, writes one PNG per gate into
    ``artifact_dir/visual_gates/`` for later inspection.

    Cleans up by closing the project in FL (unless ``close_after=False``).
    Does NOT quit FL — same convention as the rest of autodrive.

    Caller is responsible for env-gating; this function assumes the
    caller already decided that gates should run.
    """
    if not expectations:
        return []

    # Lazy imports — autodrive lives in the `re_harness` package which
    # depends on a macOS-only osascript path; importing at module load
    # would break harness unit tests that mock the autodrive surface.
    from re_harness import autodrive  # type: ignore[import-not-found]

    gate_dir = artifact_dir / "visual_gates"
    gate_dir.mkdir(parents=True, exist_ok=True)

    autodrive.open_flp(flp_path, wait_seconds=boot_wait_seconds)

    results: list[VisualGateRunResult] = []
    try:
        for i, exp in enumerate(expectations):
            label = exp.name or exp.question[:48].strip()
            image_path = gate_dir / f"gate_{i:02d}_{_slugify(label)}.png"
            autodrive.screenshot_fl_window(image_path, full_screen=exp.full_screen)
            verdict = await visual_gate(
                client=client,
                image_path=image_path,
                question=exp.question,
                model=model,
            )
            results.append(
                VisualGateRunResult(
                    name=label,
                    question=exp.question,
                    verdict=verdict,
                    image_path=image_path,
                    must_pass=exp.must_pass,
                )
            )
    finally:
        if close_after:
            with contextlib.suppress(autodrive.AutodriveError):
                autodrive.close_current_project()

    return results


def _slugify(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")[:48] or "gate"


__all__ = [
    "DEFAULT_GATE_MODEL",
    "GATE_TOOL_DEF",
    "GATE_TOOL_NAME",
    "VisualGateRunResult",
    "VisualGateVerdict",
    "run_visual_gates",
    "visual_gate",
]
