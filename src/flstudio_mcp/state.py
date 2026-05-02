"""Active-project resolution + on-disk state cache.

Calls ``describe`` on the live runtime to read FL Studio's current
project state, parses the JSON result, and caches it to
``~/Library/Application Support/flstudio-mcp/state.json``. Future tool
calls can read the cache without round-tripping to FL.

When an explicit ``path`` is supplied, it overrides the cached value
and is recorded as the resolved project (per decision #13 — auto-detect
with explicit override).
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .logging_setup import get_logger

DEFAULT_STATE_DIR = Path.home() / "Library" / "Application Support" / "flstudio-mcp"
DEFAULT_STATE_FILENAME = "state.json"

_LOG = get_logger("state")


@dataclass
class ProjectState:
    """A snapshot of FL Studio's current project, as reported by ``describe``."""

    flp_path: str | None = None
    project_title: str | None = None
    tempo_bpm: float | None = None
    channel_count: int | None = None
    pattern_count: int | None = None
    insert_count: int | None = None
    api_version: str | None = None
    cached_at: float = field(default_factory=time.time)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_describe_payload(detail: str) -> ProjectState:
    """Parse the JSON ``detail`` of a ``describe`` result into a ProjectState.

    Defensive against ``ERR:...`` placeholders that the FL script writes
    when an introspection call fails — those become ``None`` on the
    typed fields, but the raw payload is preserved.
    """
    if not detail:
        return ProjectState()
    try:
        raw = json.loads(detail)
    except json.JSONDecodeError:
        _LOG.warning("describe payload not JSON", extra={"detail_preview": detail[:120]})
        return ProjectState(raw={"_unparsed": detail})

    if not isinstance(raw, dict):
        return ProjectState(raw={"_root_not_dict": raw})

    tempo_raw = raw.get("tempo")
    tempo_bpm: float | None
    if isinstance(tempo_raw, int | float) and tempo_raw:
        # FL reports tempo as bpm*1000 from describe (mixer.getCurrentTempo).
        tempo_bpm = round(float(tempo_raw) / 1000.0, 6)
    else:
        tempo_bpm = None

    return ProjectState(
        flp_path=_clean_str(raw.get("flp_path")),
        project_title=_clean_str(raw.get("project_title")),
        tempo_bpm=tempo_bpm,
        channel_count=_coerce_int(raw.get("channel_count")),
        pattern_count=_coerce_int(raw.get("pattern_count")),
        insert_count=_coerce_int(raw.get("insert_count")),
        api_version=_clean_str(raw.get("api_version")),
        raw=raw,
    )


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.startswith("ERR:"):
        return None
    return text


def state_path(state_dir: Path | None = None, filename: str = DEFAULT_STATE_FILENAME) -> Path:
    return (Path(state_dir) if state_dir else DEFAULT_STATE_DIR) / filename


def cache_state(state: ProjectState, *, state_dir: Path | None = None) -> Path:
    """Persist a ProjectState to ``state.json`` (creates dir if needed)."""
    target = state_path(state_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return target


def load_cached_state(*, state_dir: Path | None = None) -> ProjectState | None:
    """Load the cached ProjectState, or ``None`` if absent/corrupt."""
    target = state_path(state_dir)
    if not target.exists():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _LOG.warning("state.json corrupt", extra={"path": str(target)})
        return None
    if not isinstance(raw, Mapping):
        return None
    return ProjectState(
        flp_path=raw.get("flp_path"),
        project_title=raw.get("project_title"),
        tempo_bpm=raw.get("tempo_bpm"),
        channel_count=raw.get("channel_count"),
        pattern_count=raw.get("pattern_count"),
        insert_count=raw.get("insert_count"),
        api_version=raw.get("api_version"),
        cached_at=float(raw.get("cached_at") or 0.0),
        raw=raw.get("raw") or {},
    )


def describe_active_project(runtime, *, state_dir: Path | None = None) -> ProjectState:
    """Send ``describe`` to FL, parse, cache, return."""
    result = runtime.send("describe")
    if getattr(result, "status", None) != "ok":
        raise RuntimeError(
            f"describe failed: status={getattr(result, 'status', '?')!r} "
            f"detail={getattr(result, 'detail', '')[:200]!r}"
        )
    state = parse_describe_payload(getattr(result, "detail", "") or "")
    cache_state(state, state_dir=state_dir)
    return state


def resolve_active_project(
    runtime,
    *,
    path: str | None = None,
    state_dir: Path | None = None,
    use_cache: bool = True,
) -> ProjectState:
    """Return the current project state.

    Resolution:
      * explicit ``path`` → return cached state with that path overridden
        (still calls describe so live data is up to date).
      * ``use_cache=True`` and a cached state exists → return it.
      * otherwise → call describe.
    """
    if path is not None:
        live = describe_active_project(runtime, state_dir=state_dir)
        live.flp_path = path
        cache_state(live, state_dir=state_dir)
        return live

    if use_cache:
        cached = load_cached_state(state_dir=state_dir)
        if cached is not None:
            return cached

    return describe_active_project(runtime, state_dir=state_dir)
