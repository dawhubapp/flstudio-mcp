"""Tests for active-project resolution + state cache."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from flstudio_mcp import state


@dataclass
class _FakeResult:
    status: str = "ok"
    detail: str = ""
    artifact_path: str | None = None
    id: str = "x"


@dataclass
class FakeRuntime:
    detail: str = ""
    status: str = "ok"
    sent: list[tuple[str, dict]] = field(default_factory=list)

    def send(self, kind: str, args: dict[str, Any] | None = None, **_kw) -> _FakeResult:
        self.sent.append((kind, args or {}))
        return _FakeResult(status=self.status, detail=self.detail)


def _describe_detail(**overrides: Any) -> str:
    payload = {
        "tempo": 145000.0,
        "project_title": "demo",
        "flp_path": "/tmp/demo.flp",
        "channel_count": 8,
        "pattern_count": 3,
        "insert_count": 105,
        "api_version": "FL Studio 25.2.4",
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_parse_describe_payload_happy() -> None:
    s = state.parse_describe_payload(_describe_detail())
    assert s.tempo_bpm == 145.0
    assert s.project_title == "demo"
    assert s.flp_path == "/tmp/demo.flp"
    assert s.channel_count == 8
    assert s.pattern_count == 3
    assert s.insert_count == 105
    assert "FL Studio" in (s.api_version or "")


def test_parse_describe_payload_handles_err_strings() -> None:
    s = state.parse_describe_payload(_describe_detail(project_title="ERR:no project"))
    assert s.project_title is None


def test_parse_describe_payload_invalid_json() -> None:
    s = state.parse_describe_payload("not json")
    assert s.tempo_bpm is None
    assert s.raw == {"_unparsed": "not json"}


def test_parse_describe_payload_empty() -> None:
    s = state.parse_describe_payload("")
    assert s == state.ProjectState(cached_at=s.cached_at)


def test_cache_and_load_round_trip(tmp_path: Path) -> None:
    s = state.parse_describe_payload(_describe_detail())
    target = state.cache_state(s, state_dir=tmp_path)
    assert target == tmp_path / "state.json"
    loaded = state.load_cached_state(state_dir=tmp_path)
    assert loaded is not None
    assert loaded.tempo_bpm == 145.0
    assert loaded.flp_path == "/tmp/demo.flp"


def test_load_cached_state_missing_file(tmp_path: Path) -> None:
    assert state.load_cached_state(state_dir=tmp_path) is None


def test_load_cached_state_corrupt(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("not json", encoding="utf-8")
    assert state.load_cached_state(state_dir=tmp_path) is None


def test_describe_active_project_caches(tmp_path: Path) -> None:
    rt = FakeRuntime(detail=_describe_detail())
    s = state.describe_active_project(rt, state_dir=tmp_path)
    assert s.tempo_bpm == 145.0
    assert rt.sent == [("describe", {})]
    assert (tmp_path / "state.json").exists()


def test_describe_active_project_raises_on_error_status(tmp_path: Path) -> None:
    rt = FakeRuntime(detail="x", status="error")
    with pytest.raises(RuntimeError, match="describe failed"):
        state.describe_active_project(rt, state_dir=tmp_path)


def test_resolve_active_project_uses_cache_when_present(tmp_path: Path) -> None:
    cached = state.parse_describe_payload(_describe_detail(project_title="cached"))
    state.cache_state(cached, state_dir=tmp_path)
    rt = FakeRuntime(detail=_describe_detail(project_title="fresh"))
    s = state.resolve_active_project(rt, state_dir=tmp_path)
    assert s.project_title == "cached"
    assert rt.sent == []  # didn't call describe


def test_resolve_active_project_skips_cache(tmp_path: Path) -> None:
    cached = state.parse_describe_payload(_describe_detail(project_title="cached"))
    state.cache_state(cached, state_dir=tmp_path)
    rt = FakeRuntime(detail=_describe_detail(project_title="fresh"))
    s = state.resolve_active_project(rt, state_dir=tmp_path, use_cache=False)
    assert s.project_title == "fresh"


def test_resolve_active_project_with_path_override(tmp_path: Path) -> None:
    rt = FakeRuntime(detail=_describe_detail(flp_path="/old.flp"))
    s = state.resolve_active_project(rt, path="/explicit.flp", state_dir=tmp_path)
    assert s.flp_path == "/explicit.flp"
    cached = state.load_cached_state(state_dir=tmp_path)
    assert cached is not None and cached.flp_path == "/explicit.flp"
