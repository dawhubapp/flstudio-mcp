"""Capture pre/post FLP state via offline_execute reads.

A test calls ``capture_state(session, flp_path)`` before and after the
agent runs. The result is a self-contained dict the invariant validator
can consume without re-spawning the bridge.

We deliberately use fresh ``offline_execute`` calls (not the agent's
transcript) so the agent cannot game the validator by lying about state
in earlier tool returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .client import HarnessSession

OFFLINE_TOOL = "offline_execute"


@dataclass
class FLPState:
    """Snapshot of an FLP's externally-visible state."""

    path: Path
    describe: dict[str, Any]
    channels: list[dict[str, Any]] = field(default_factory=list)
    mixer: list[dict[str, Any]] = field(default_factory=list)
    patterns: list[dict[str, Any]] = field(default_factory=list)
    arrangements: list[dict[str, Any]] = field(default_factory=list)
    tracks: list[dict[str, Any]] = field(default_factory=list)
    clips: list[dict[str, Any]] = field(default_factory=list)
    plugins: list[dict[str, Any]] = field(default_factory=list)


async def capture_state(session: HarnessSession, flp_path: Path) -> FLPState:
    """Read every introspection kind off the bridge for ``flp_path``."""
    args = {"path": str(flp_path)}

    state = FLPState(
        path=flp_path,
        describe=await _read(session, "describe", args),
    )
    state.channels = _list(await _read(session, "list_channels", args))
    state.mixer = _list(await _read(session, "list_mixer", args))
    state.patterns = _list(await _read(session, "list_patterns", args))
    state.plugins = _list(await _read(session, "list_plugins", args))
    arr_payload = await _read(session, "list_arrangements", args)
    state.arrangements = _list(arr_payload)

    # Tracks/clips are arrangement-scoped — pull for each arrangement.
    for arr in state.arrangements:
        arr_id = arr.get("id", arr.get("arrangement_id", 0))
        scoped = {**args, "arrangement_id": arr_id}
        tracks = _list(await _read(session, "list_tracks", scoped))
        clips = _list(await _read(session, "list_clips", scoped))
        for t in tracks:
            t.setdefault("_arrangement_id", arr_id)
        for c in clips:
            c.setdefault("_arrangement_id", arr_id)
        state.tracks.extend(tracks)
        state.clips.extend(clips)
    return state


async def _read(session: HarnessSession, kind: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call offline_execute(kind, args), assert ok, return result dict."""
    envelope = await session.call_tool(OFFLINE_TOOL, {"kind": kind, "args": args})
    if not envelope.get("ok"):
        result = envelope.get("result") or {}
        msg = result.get("message") or result.get("error") or "<no message>"
        raise RuntimeError(f"offline_execute({kind}) failed: {msg}")
    payload = envelope.get("result")
    return payload if isinstance(payload, dict) else {"_raw": payload}


def _list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Coerce a list-shaped offline result into a list of dicts.

    The bridge returns shapes like ``{"channels": [...]}`` or
    ``{"items": [...]}``; we accept any single-list payload.
    """
    for key in (
        "channels",
        "mixer",
        "inserts",
        "patterns",
        "arrangements",
        "tracks",
        "clips",
        "plugins",
        "items",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict)]
    # Fallback: first list-valued field wins.
    for value in payload.values():
        if isinstance(value, list) and all(isinstance(v, dict) for v in value):
            return value
    return []
