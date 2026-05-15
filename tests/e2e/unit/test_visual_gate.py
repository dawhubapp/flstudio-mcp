"""Unit tests for the visual-gate harness module (no FL, no API)."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

from ..harness.agent import (
    FakeMessage,
    FakeToolUseBlock,
    ScriptedResponder,
)
from ..harness.visual_gate import (
    GATE_TOOL_NAME,
    VisualGateVerdict,
    _slugify,
    visual_gate,
)

# Smallest valid PNG (1x1, transparent) - saved on disk so tests can
# point visual_gate at a real file without requiring screencapture.
_PNG_1x1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)


def _png(tmp_path: Path) -> Path:
    p = tmp_path / "tiny.png"
    p.write_bytes(base64.b64decode(_PNG_1x1_BASE64))
    return p


def test_slugify_strips_punctuation_and_caps():
    assert _slugify("Playlist has clips!") == "playlist_has_clips"
    assert _slugify("   spaces   ") == "spaces"
    assert _slugify("...") == "gate"


def test_visual_gate_returns_verdict_from_tool_call(tmp_path: Path):
    image = _png(tmp_path)
    responder = ScriptedResponder(
        messages_to_emit=[
            FakeMessage(
                content=[
                    FakeToolUseBlock(
                        id="tu1",
                        name=GATE_TOOL_NAME,
                        input={
                            "passed": True,
                            "rationale": "Clips visible at bars 1-5.",
                            "evidence": ["8 clip blocks", "playlist tab open"],
                        },
                    )
                ],
                stop_reason="tool_use",
            )
        ]
    )
    verdict = asyncio.run(
        visual_gate(
            client=responder,
            image_path=image,
            question="Are clips visible on the playlist?",
            model="test-model",
        )
    )
    assert isinstance(verdict, VisualGateVerdict)
    assert verdict.passed is True
    assert "Clips visible" in verdict.rationale
    assert verdict.evidence == ["8 clip blocks", "playlist tab open"]


def test_visual_gate_passes_image_block_and_forces_tool(tmp_path: Path):
    image = _png(tmp_path)
    responder = ScriptedResponder(
        messages_to_emit=[
            FakeMessage(
                content=[
                    FakeToolUseBlock(
                        id="tu1",
                        name=GATE_TOOL_NAME,
                        input={"passed": False, "rationale": "Empty playlist."},
                    )
                ]
            )
        ]
    )
    asyncio.run(
        visual_gate(
            client=responder,
            image_path=image,
            question="Are clips visible?",
        )
    )
    assert len(responder.calls) == 1
    call = responder.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": GATE_TOOL_NAME}
    msg = call["messages"][0]
    assert msg["role"] == "user"
    block_types = [b.get("type") for b in msg["content"]]
    assert block_types == ["image", "text"]
    image_block = msg["content"][0]
    assert image_block["source"]["media_type"] == "image/png"
    # Base64 round-trips back to original PNG bytes.
    decoded = base64.b64decode(image_block["source"]["data"])
    assert decoded == image.read_bytes()


def test_visual_gate_raises_when_model_skips_tool(tmp_path: Path):
    image = _png(tmp_path)
    responder = ScriptedResponder(
        messages_to_emit=[
            FakeMessage(
                content=[
                    # Only a text block, no tool_use — should raise.
                    type(
                        "TextOnly",
                        (),
                        {"type": "text", "text": "I refuse to use the tool"},
                    )()
                ]
            )
        ]
    )
    with pytest.raises(RuntimeError, match="did not call submit_verdict"):
        asyncio.run(
            visual_gate(
                client=responder,
                image_path=image,
                question="?",
            )
        )


def test_visual_gate_raises_on_missing_image(tmp_path: Path):
    responder = ScriptedResponder(messages_to_emit=[])
    with pytest.raises(FileNotFoundError):
        asyncio.run(
            visual_gate(
                client=responder,
                image_path=tmp_path / "does_not_exist.png",
                question="?",
            )
        )


def test_visual_gate_coerces_missing_evidence_to_empty_list(tmp_path: Path):
    image = _png(tmp_path)
    responder = ScriptedResponder(
        messages_to_emit=[
            FakeMessage(
                content=[
                    FakeToolUseBlock(
                        id="tu1",
                        name=GATE_TOOL_NAME,
                        # No "evidence" key, no "passed" key.
                        input={"rationale": "no answer"},
                    )
                ]
            )
        ]
    )
    verdict = asyncio.run(visual_gate(client=responder, image_path=image, question="?"))
    assert verdict.passed is False
    assert verdict.evidence == []
