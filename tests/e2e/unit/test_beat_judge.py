"""Unit tests for the Tier 1 beat judge (no models, no API)."""

from __future__ import annotations

import json
import sys

import numpy as np
import pytest

from ..harness.agent import FakeMessage, FakeTextBlock, ScriptedResponder
from ..harness.beat_judge import (
    JUDGE_MODEL,
    RUBRIC_SCHEMA,
    ModelScore,
    RubricVerdict,
    call_beat_rubric,
    composite,
    judge_beat,
    resample_to_48k,
    score_audiobox,
    score_clap,
)

GOOD_CHECK = {"ok": True, "issues": [], "metrics": {}}
GOOD_AUDIO = {"issues": []}
GOOD_RUBRIC = RubricVerdict(5, 5, 4, 5, True, "nice", [])
MODELS = [
    ModelScore("audiobox_aesthetics", True, {"PQ": 7.2}),
    ModelScore("clap", True, {"brief_match": 0.6}, top_genre="house"),
]


def test_composite_passes_when_everything_agrees() -> None:
    ok, reasons = composite(
        check=GOOD_CHECK, audio=GOOD_AUDIO, models=MODELS, rubric=GOOD_RUBRIC, genre="house"
    )
    assert ok and reasons == []


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"check": {"issues": [{"severity": "error", "message": "x"}]}}, "Tier 0"),
        ({"audio": None}, "not rendered"),
        ({"audio": {"issues": [{"severity": "error", "message": "silent"}]}}, "audio:"),
        ({"rubric": None}, "rubric judge failed"),
        ({"rubric": RubricVerdict(3, 3, 3, 3, True, "", [])}, "rubric grade 3"),
        ({"rubric": RubricVerdict(5, 5, 5, 5, False, "", [])}, "wouldn't play"),
        (
            {"models": [ModelScore("audiobox_aesthetics", True, {"PQ": 4.0}), MODELS[1]]},
            "PQ 4.0",
        ),
        (
            {"models": [MODELS[0], ModelScore("clap", True, {}, top_genre="trap")]},
            "CLAP hears trap",
        ),
    ],
)
def test_composite_reasons(kwargs: dict, reason: str) -> None:
    args = {
        "check": GOOD_CHECK,
        "audio": GOOD_AUDIO,
        "models": MODELS,
        "rubric": GOOD_RUBRIC,
        "genre": "house",
    }
    args.update(kwargs)
    ok, reasons = composite(**args)
    assert not ok and any(reason in r for r in reasons), reasons


def test_unavailable_models_are_ignored_by_composite() -> None:
    models = [
        ModelScore("audiobox_aesthetics", False, {}, "not installed"),
        ModelScore("clap", False, {}, "x"),
    ]
    ok, _ = composite(
        check=GOOD_CHECK, audio=GOOD_AUDIO, models=models, rubric=GOOD_RUBRIC, genre="house"
    )
    assert ok


def test_rubric_schema_is_strict() -> None:
    assert RUBRIC_SCHEMA["additionalProperties"] is False
    assert set(RUBRIC_SCHEMA["required"]) == set(RUBRIC_SCHEMA["properties"])


@pytest.mark.anyio
async def test_call_beat_rubric_uses_structured_output() -> None:
    verdict = {
        "grade": 7, "musicality": 2, "sound_fit": 3, "brief_fit": 4,
        "play_for_friend": False, "rationale": "flat hats", "weaknesses": ["no chords"],
    }  # fmt: skip
    client = ScriptedResponder(
        messages_to_emit=[FakeMessage(content=[FakeTextBlock(text=json.dumps(verdict))])]
    )
    result = await call_beat_rubric(
        client=client, brief="dark trap", genre="trap", check=GOOD_CHECK, audio=None,
        models=[], agent_summary="plan: 140 bpm", rubric_text="RUBRIC",
    )  # fmt: skip
    assert result.grade == 5  # clamped
    assert result.weaknesses == ["no chords"] and result.play_for_friend is False
    call = client.calls[0]
    assert call["model"] == JUDGE_MODEL
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == "high"
    assert "tool_choice" not in call and "tools" not in call


@pytest.mark.anyio
async def test_judge_beat_without_render() -> None:
    verdict = {
        "grade": 4, "musicality": 4, "sound_fit": 4, "brief_fit": 4,
        "play_for_friend": True, "rationale": "", "weaknesses": [],
    }  # fmt: skip
    client = ScriptedResponder(
        messages_to_emit=[FakeMessage(content=[FakeTextBlock(text=json.dumps(verdict))])]
    )
    report = await judge_beat(
        client=client, brief="b", genre="house", check=GOOD_CHECK, wav=None, audio=None,
        agent_summary="",
    )  # fmt: skip
    assert report.models == [] and not report.passed
    assert any("not rendered" in r for r in report.reasons)
    json.dumps(report.to_dict())


def test_missing_packages_degrade(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setitem(sys.modules, "audiobox_aesthetics", None)
    monkeypatch.setitem(sys.modules, "audiobox_aesthetics.infer", None)
    monkeypatch.setitem(sys.modules, "torch", None)
    wav = tmp_path / "x.wav"
    assert score_audiobox(wav).available is False
    clap = score_clap(wav, "b", "house")
    assert clap.available is False and "not installed" in clap.note


def test_resample_to_48k() -> None:
    pytest.importorskip("scipy")
    x = np.sin(2 * np.pi * 440 * np.arange(44100) / 44100).astype(np.float32)
    y = resample_to_48k(x, 44100)
    assert y.size == 48000 and y.dtype == np.float32
    assert resample_to_48k(y, 48000) is y
