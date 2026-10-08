"""Tier 1 dev judge for brief runs (F11.1.3).

Tier 0 output + audio metrics + Audiobox Aesthetics + CLAP + a Claude
rubric call, combined into one pass/fail. Heavy packages (torch,
transformers, audiobox_aesthetics) are imported lazily: a missing model
is reported as unavailable and never fails the run. Thresholds are
initial values, to be calibrated against Roman's ratings (Q4).
"""

from __future__ import annotations

import functools
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from flstudio_mcp.audio_checks import load_mono

from .agent import AnthropicClient, _block_kind, _block_text

JUDGE_MODEL = "claude-opus-5-5"
JUDGE_EFFORT = "high"
BEAT_RUBRIC_PATH = Path(__file__).parent / "prompts" / "judge_rubric_beat.md"
AUDIOBOX_MIN_PQ = 6.0
RUBRIC_MIN_GRADE = 4
# laion/larger_clap_music loads under transformers 5.x but returns ~constant
# similarities (cosine ~0.01 for every pair); the base checkpoint discriminates.
CLAP_MODEL_ID = "laion/clap-htsat-unfused"
CLAP_SR = 48000
CLAP_CONTRASTS = (
    "a quiet ambient soundscape with no drums",
    "an acoustic folk song with guitar and vocals",
    "an orchestral film score",
    "a heavy metal song with distorted guitars",
)
GENRE_PROMPTS = {
    "house": "a house music track with a four-on-the-floor kick drum",
    "trap": "a trap beat with booming 808 bass and fast hi-hat rolls",
    "lofi": "a lofi hip hop beat",
    "techno": "a techno track",
    "pop": "a pop song with vocals",
}
RUBRIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "grade": {"type": "integer", "description": "Overall 1-5."},
        "musicality": {
            "type": "integer",
            "description": "1-5: groove, voicing, register, dynamics.",
        },
        "sound_fit": {
            "type": "integer",
            "description": "1-5: sounds fit their roles and the brief.",
        },
        "brief_fit": {"type": "integer", "description": "1-5: matches the brief."},
        "play_for_friend": {
            "type": "boolean",
            "description": "Would a producer play it for a friend?",
        },
        "rationale": {"type": "string"},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "grade",
        "musicality",
        "sound_fit",
        "brief_fit",
        "play_for_friend",
        "rationale",
        "weaknesses",
    ],
    "additionalProperties": False,
}


@dataclass
class ModelScore:
    name: str
    available: bool
    scores: dict[str, float] = field(default_factory=dict)
    note: str = ""
    top_genre: str | None = None


@dataclass
class RubricVerdict:
    grade: int
    musicality: int
    sound_fit: int
    brief_fit: int
    play_for_friend: bool
    rationale: str
    weaknesses: list[str]


@dataclass
class BeatJudgeReport:
    check: dict[str, Any]
    audio: dict[str, Any] | None
    models: list[ModelScore]
    rubric: RubricVerdict | None
    rubric_error: str | None
    passed: bool
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- audiobox #


@functools.lru_cache(maxsize=1)
def _audiobox_predictor() -> Any:
    from audiobox_aesthetics.infer import initialize_predictor

    return initialize_predictor()


def score_audiobox(wav: Path) -> ModelScore:
    """Audiobox Aesthetics PQ/PC/CE/CU (1-10) for one WAV."""
    name = "audiobox_aesthetics"
    try:
        import audiobox_aesthetics.infer  # noqa: F401
    except ImportError as exc:
        return ModelScore(name, False, note=f"not installed ({exc}); uv sync --extra eval")
    try:
        import torch

        # Pass a tensor, not a path: torchaudio >= 2.9 needs torchcodec + FFmpeg to load files.
        x, sr = load_mono(wav)
        tensor = torch.from_numpy(x).unsqueeze(0)
        row = _audiobox_predictor().forward([{"path": tensor, "sample_rate": sr}])[0]
        if isinstance(row, str):
            row = json.loads(row)
        scores = {k: float(row[k]) for k in ("PQ", "PC", "CE", "CU") if k in row}
        return ModelScore(name, True, scores)
    except Exception as exc:  # model download / inference failure: report, don't fail
        return ModelScore(name, False, note=f"failed: {exc!r}")


# -------------------------------------------------------------------- clap #


def resample_to_48k(x: np.ndarray, sr: int) -> np.ndarray:
    """Polyphase resample to CLAP's 48 kHz (no-op when already 48 kHz)."""
    if sr == CLAP_SR:
        return x
    from scipy.signal import resample_poly

    g = math.gcd(CLAP_SR, sr)
    return resample_poly(x, CLAP_SR // g, sr // g).astype(np.float32)


@functools.lru_cache(maxsize=1)
def _clap() -> tuple[Any, Any]:
    from transformers import ClapModel, ClapProcessor

    model = ClapModel.from_pretrained(CLAP_MODEL_ID).eval()
    return model, ClapProcessor.from_pretrained(CLAP_MODEL_ID)


def _clap_probs(audio: np.ndarray, texts: list[str]) -> list[float]:
    import torch

    model, processor = _clap()
    inputs = processor(
        text=texts, audio=[audio], sampling_rate=CLAP_SR, return_tensors="pt", padding=True
    )
    with torch.no_grad():
        logits = model(**inputs).logits_per_audio[0]
    return [float(p) for p in logits.softmax(dim=-1).tolist()]


def score_clap(wav: Path, brief: str, genre: str) -> ModelScore:
    """CLAP: P(brief) against contrasting descriptions + zero-shot genre."""
    name = "clap"
    try:
        # torch first: scipy's array-API shim probes sys.modules["torch"] on import.
        import torch  # noqa: F401, I001
        import transformers  # noqa: F401
        import scipy.signal  # noqa: F401
    except ImportError as exc:
        return ModelScore(name, False, note=f"not installed ({exc}); uv sync --extra eval")
    try:
        x, sr = load_mono(wav)
        audio = resample_to_48k(x, sr)
        brief_probs = _clap_probs(audio, [brief, *CLAP_CONTRASTS])
        labels = list(GENRE_PROMPTS)
        genre_probs = _clap_probs(audio, [GENRE_PROMPTS[g] for g in labels])
        top = labels[int(np.argmax(genre_probs))]
        scores = {"brief_match": brief_probs[0]}
        scores.update({f"genre_{g}": p for g, p in zip(labels, genre_probs, strict=True)})
        return ModelScore(name, True, scores, note=f"top genre: {top}", top_genre=top)
    except Exception as exc:
        return ModelScore(name, False, note=f"failed: {exc!r}")


# ------------------------------------------------------------------ rubric #


def _clamp(value: Any) -> int:
    return max(1, min(5, int(value)))


async def call_beat_rubric(
    *,
    client: AnthropicClient,
    brief: str,
    genre: str,
    check: dict[str, Any],
    audio: dict[str, Any] | None,
    models: list[ModelScore],
    agent_summary: str,
    rubric_text: str,
    model: str = JUDGE_MODEL,
) -> RubricVerdict:
    """One Claude call with a JSON-schema structured output (no forced tool_choice)."""
    payload = {
        "brief": brief,
        "genre": genre,
        "check_beat": check,
        "audio": audio,
        "model_scores": [asdict(m) for m in models],
        "agent_final_message": agent_summary[:4000],
    }
    message = await client.messages.create(
        model=model,
        max_tokens=16000,
        system=[{"type": "text", "text": rubric_text, "cache_control": {"type": "ephemeral"}}],
        output_config={
            "effort": JUDGE_EFFORT,
            "format": {"type": "json_schema", "schema": RUBRIC_SCHEMA},
        },
        messages=[
            {
                "role": "user",
                "content": "Grade this beat against the rubric.\n\n```json\n"
                + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
                + "\n```",
            }
        ],
    )
    if getattr(message, "stop_reason", None) == "refusal":
        raise RuntimeError("judge refused")
    text = next((_block_text(b) for b in message.content if _block_kind(b) == "text"), "")
    data = json.loads(text)
    return RubricVerdict(
        grade=_clamp(data["grade"]),
        musicality=_clamp(data["musicality"]),
        sound_fit=_clamp(data["sound_fit"]),
        brief_fit=_clamp(data["brief_fit"]),
        play_for_friend=bool(data["play_for_friend"]),
        rationale=str(data["rationale"]),
        weaknesses=[str(w) for w in data["weaknesses"]],
    )


# --------------------------------------------------------------- composite #


def _errors(block: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [i for i in (block or {}).get("issues", []) if i.get("severity") == "error"]


def composite(
    *,
    check: dict[str, Any],
    audio: dict[str, Any] | None,
    models: list[ModelScore],
    rubric: RubricVerdict | None,
    genre: str,
) -> tuple[bool, list[str]]:
    """Pass iff every available signal agrees. Returns (passed, reasons)."""
    reasons: list[str] = []
    if errors := _errors(check):
        reasons.append(f"Tier 0: {len(errors)} error issue(s)")
    if audio is None:
        reasons.append("not rendered (set FLSTUDIO_RENDER_E2E=1 with FL installed)")
    elif errors := _errors(audio):
        reasons.append(f"audio: {len(errors)} error issue(s)")
    by_name = {m.name: m for m in models if m.available}
    audiobox = by_name.get("audiobox_aesthetics")
    if audiobox and audiobox.scores.get("PQ", 0.0) < AUDIOBOX_MIN_PQ:
        reasons.append(f"audiobox PQ {audiobox.scores.get('PQ', 0.0):.1f} < {AUDIOBOX_MIN_PQ}")
    clap = by_name.get("clap")
    if clap and clap.top_genre != genre:
        reasons.append(f"CLAP hears {clap.top_genre}, not {genre}")
    if rubric is None:
        reasons.append("rubric judge failed")
    else:
        if rubric.grade < RUBRIC_MIN_GRADE:
            reasons.append(f"rubric grade {rubric.grade} < {RUBRIC_MIN_GRADE}")
        if not rubric.play_for_friend:
            reasons.append("rubric: wouldn't play it for a friend")
    return (not reasons, reasons)


async def judge_beat(
    *,
    client: AnthropicClient,
    brief: str,
    genre: str,
    check: dict[str, Any],
    wav: Path | None,
    audio: dict[str, Any] | None,
    agent_summary: str,
    rubric_path: Path = BEAT_RUBRIC_PATH,
) -> BeatJudgeReport:
    """Run every Tier 1 signal and combine them."""
    models = [score_audiobox(wav), score_clap(wav, brief, genre)] if wav is not None else []
    rubric: RubricVerdict | None = None
    rubric_error: str | None = None
    try:
        rubric = await call_beat_rubric(
            client=client,
            brief=brief,
            genre=genre,
            check=check,
            audio=audio,
            models=models,
            agent_summary=agent_summary,
            rubric_text=rubric_path.read_text(encoding="utf-8"),
        )
    except Exception as exc:
        rubric_error = repr(exc)
    passed, reasons = composite(check=check, audio=audio, models=models, rubric=rubric, genre=genre)
    return BeatJudgeReport(check, audio, models, rubric, rubric_error, passed, reasons)
