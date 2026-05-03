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


def test_describe_uses_window_title_fallback_when_flp_path_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FL 2025 API gap: getCurrentFilename() doesn't exist; fallback resolves via window."""
    payload = _describe_detail(flp_path="ERR:no such attribute", project_title="ERR:also missing")
    rt = FakeRuntime(detail=payload)
    fallback_path = "/Users/me/Documents/Image-Line/FL Studio/Projects/track.flp"
    monkeypatch.setattr(state, "resolve_flp_path_from_fl_window", lambda: fallback_path)
    s = state.describe_active_project(rt, state_dir=tmp_path)
    assert s.flp_path == fallback_path
    # project_title was None (from ERR:); falls back to filename stem
    assert s.project_title == "track"


def test_describe_keeps_real_project_title_when_using_path_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _describe_detail(flp_path="ERR:no", project_title="My Track")
    rt = FakeRuntime(detail=payload)
    monkeypatch.setattr(state, "resolve_flp_path_from_fl_window", lambda: "/some/track.flp")
    s = state.describe_active_project(rt, state_dir=tmp_path)
    assert s.project_title == "My Track"  # don't overwrite real title


def test_describe_skips_fallback_when_flp_path_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt = FakeRuntime(detail=_describe_detail(flp_path="/from-fl.flp"))
    monkeypatch.setattr(
        state,
        "resolve_flp_path_from_fl_window",
        lambda: pytest.fail("fallback should not run"),
    )
    s = state.describe_active_project(rt, state_dir=tmp_path)
    assert s.flp_path == "/from-fl.flp"


def test_resolve_flp_path_from_fl_window_no_title(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state, "_read_fl_window_title", lambda: None)
    assert state.resolve_flp_path_from_fl_window() is None


def test_resolve_flp_path_from_fl_window_no_flp_in_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(state, "_read_fl_window_title", lambda: "FL Studio 2025")
    assert state.resolve_flp_path_from_fl_window() is None


def test_resolve_flp_path_from_fl_window_uses_mdfind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real = tmp_path / "track.flp"
    real.write_bytes(b"FLhd")
    monkeypatch.setattr(state, "_read_fl_window_title", lambda: "track.flp - FL Studio 2025")
    monkeypatch.setattr(state, "_resolve_flp_filename_via_mdfind", lambda fn: str(real))
    assert state.resolve_flp_path_from_fl_window() == str(real)


def test_resolve_flp_path_strips_modified_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def fake_mdfind(filename: str) -> str | None:
        captured.append(filename)
        return None

    monkeypatch.setattr(state, "_read_fl_window_title", lambda: "*track.flp - FL Studio 2025")
    monkeypatch.setattr(state, "_resolve_flp_filename_via_mdfind", fake_mdfind)
    state.resolve_flp_path_from_fl_window()
    assert captured == ["track.flp"]
