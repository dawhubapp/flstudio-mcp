"""Tests for flstudio_mcp.render with a fake FL launcher (no FL needed)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from flstudio_mcp import render
from flstudio_mcp.errors import ErrorCode
from flstudio_mcp.render import (
    RenderCache,
    RenderError,
    build_render_argv,
    render_to_wav,
    resolve_fl_app,
)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class FakeProc:
    def __init__(self, exits_after_polls: int | None) -> None:
        self.polls = 0
        self.exits_after = exits_after_polls
        self.terminated = False

    def poll(self) -> int | None:
        self.polls += 1
        if self.exits_after is not None and self.polls > self.exits_after:
            return 0
        return None

    def terminate(self) -> None:
        self.terminated = True


class FakeLauncher:
    """Writes a 1 s WAV next to the FLP FL was asked to render (like FL's -R)."""

    def __init__(self, *, write: bool = True, exits_after_polls: int | None = 0) -> None:
        self.write = write
        self.exits_after = exits_after_polls
        self.argvs: list[list[str]] = []
        self.proc: FakeProc | None = None

    def __call__(self, argv: list[str]) -> FakeProc:
        self.argvs.append(argv)
        if self.write:
            wav = Path(argv[-1]).with_suffix(".wav")
            sf.write(str(wav), np.zeros(44100, dtype=np.float32) + 0.1, 44100)
        self.proc = FakeProc(self.exits_after)
        return self.proc


@pytest.fixture
def setup(tmp_path: Path):
    app = tmp_path / "FL Studio 2026.app"
    app.mkdir()
    flp = tmp_path / "song.flp"
    flp.write_bytes(b"FLhd-fake-project")
    cache = RenderCache(root=tmp_path / "cache")
    return app, flp, cache


def _render(setup, launcher, **kw):
    app, flp, cache = setup
    clock = FakeClock()
    kills: list[int] = []
    result = render_to_wav(
        flp,
        fl_app=app,
        cache=cache,
        launcher=launcher,
        is_fl_running=kw.pop("is_fl_running", lambda: False),
        kill_fl=lambda: kills.append(1),
        clock=clock,
        sleep=clock.sleep,
        **({"mode": "pattern"} | kw),
    )
    return result, kills


def test_build_render_argv(tmp_path: Path) -> None:
    assert build_render_argv(Path("/A/FL.app"), Path("/t/render.flp")) == [
        "open", "-W", "-a", "/A/FL.app", "--args", "-R", "-Ewav", "/t/render.flp",
    ]  # fmt: skip


def test_resolve_fl_app_picks_newest_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("FL Studio 21.app", "FL Studio 2025.app", "FL Studio 2026.app", "FL Cloud.app"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(render, "APPLICATIONS", tmp_path)
    monkeypatch.delenv(render.FL_APP_ENV, raising=False)
    assert resolve_fl_app() == tmp_path / "FL Studio 2026.app"
    assert resolve_fl_app(Path("/x.app")) == Path("/x.app")


def test_resolve_fl_app_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render, "APPLICATIONS", tmp_path)  # empty
    monkeypatch.setenv(render.FL_APP_ENV, "/Volumes/X/FL Studio 2026.app")
    assert resolve_fl_app() == Path("/Volumes/X/FL Studio 2026.app")
    monkeypatch.delenv(render.FL_APP_ENV)
    assert not resolve_fl_app().exists()  # nothing installed -> FL_APP_NOT_FOUND later


def test_render_then_cache_hit(setup) -> None:
    _app, flp, cache = setup
    launcher = FakeLauncher()
    first, kills = _render(setup, launcher)
    assert first.cached is False and first.outcome == "exited"
    assert first.wav_path.parent == cache.root
    assert first.duration_s == pytest.approx(1.0, abs=0.01)
    assert Path(launcher.argvs[0][-1]).name == "render.flp"  # temp copy, never the user's file
    assert Path(launcher.argvs[0][-1]).parent != flp.parent
    assert not flp.with_suffix(".wav").exists()
    assert flp.read_bytes() == b"FLhd-fake-project"
    assert kills == []
    second, _ = _render(setup, launcher)
    assert second.cached is True and second.outcome == "cached"
    assert len(launcher.argvs) == 1


def test_force_and_changed_bytes_rerender(setup) -> None:
    _app, flp, _cache = setup
    launcher = FakeLauncher()
    _render(setup, launcher)
    _render(setup, launcher, force=True)
    flp.write_bytes(b"FLhd-changed")
    _render(setup, launcher)
    assert len(launcher.argvs) == 3


def test_fl_busy_never_launches_or_kills(setup) -> None:
    launcher = FakeLauncher()
    with pytest.raises(RenderError) as exc:
        _render(setup, launcher, is_fl_running=lambda: True)
    assert exc.value.code == "FL_BUSY"
    assert launcher.argvs == []


def test_while_running_safe_never_kills(setup, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render, "RENDER_WHILE_RUNNING_SAFE", True)
    launcher = FakeLauncher(exits_after_polls=None)  # FL stays open after writing
    result, kills = _render(setup, launcher, is_fl_running=lambda: True)
    assert result.outcome == "stable"
    assert kills == []  # FL was already running: not ours to kill


def test_cached_result_even_without_fl(setup) -> None:
    app, _flp, _cache = setup
    _render(setup, FakeLauncher())
    app.rmdir()
    result, _ = _render(setup, FakeLauncher())
    assert result.cached is True


def test_missing_app(setup) -> None:
    app, _flp, _cache = setup
    app.rmdir()
    with pytest.raises(RenderError) as exc:
        _render(setup, FakeLauncher())
    assert exc.value.code == "FL_APP_NOT_FOUND"


def test_timeout_kills_our_fl(setup) -> None:
    launcher = FakeLauncher(write=False, exits_after_polls=None)
    with pytest.raises(RenderError) as exc:
        _render(setup, launcher, timeout_s=5.0)
    assert exc.value.code == "RENDER_TIMEOUT"


def test_timeout_calls_kill(setup) -> None:
    app, flp, cache = setup
    clock = FakeClock()
    kills: list[int] = []
    launcher = FakeLauncher(write=False, exits_after_polls=None)
    with pytest.raises(RenderError):
        render_to_wav(
            flp, fl_app=app, cache=cache, launcher=launcher, is_fl_running=lambda: False,
            kill_fl=lambda: kills.append(1), clock=clock, sleep=clock.sleep, timeout_s=5.0,
            mode="pattern",
        )  # fmt: skip
    assert kills == [1] and launcher.proc is not None and launcher.proc.terminated


def test_stable_output_while_fl_stays_open(setup) -> None:
    launcher = FakeLauncher(exits_after_polls=None)
    result, kills = _render(setup, launcher)
    assert result.outcome == "stable" and kills == [1]


def test_exit_without_wav_is_render_failed(setup) -> None:
    with pytest.raises(RenderError) as exc:
        _render(setup, FakeLauncher(write=False, exits_after_polls=0))
    assert exc.value.code == "RENDER_FAILED"


def test_cache_prune_keeps_newest(tmp_path: Path) -> None:
    cache = RenderCache(root=tmp_path / "c", cap_bytes=250)
    for i in range(4):
        src = tmp_path / f"{i}.wav"
        src.write_bytes(b"x" * 100)
        cache.put(f"k{i}", src)
    names = sorted(p.name for p in cache.root.iterdir())
    assert names == ["k2.wav", "k3.wav"]


def test_error_codes_exist() -> None:
    for code in ("FL_APP_NOT_FOUND", "FL_BUSY", "RENDER_TIMEOUT", "RENDER_FAILED"):
        assert ErrorCode(code).value == code


# ------------------------------------------------------------- song mode #


class FakeExportUi:
    """Stands in for FL's File > Export flow: writes <flp>.wav like FL does."""

    def __init__(self, *, write: bool = True, error: RenderError | None = None) -> None:
        self.write = write
        self.error = error
        self.calls: list[tuple[Path, float]] = []

    def render_song(self, flp: Path, *, timeout_s: float) -> None:
        self.calls.append((flp, timeout_s))
        if self.error is not None:
            raise self.error
        if self.write:
            sf.write(str(flp.with_suffix(".wav")), np.zeros(88200, dtype=np.float32) + 0.1, 44100)


def _song(setup, ui, **kw):
    app, flp, cache = setup
    kills: list[int] = []
    result = render_to_wav(
        flp,
        fl_app=app,
        cache=cache,
        export_ui=ui,
        is_fl_running=kw.pop("is_fl_running", lambda: False),
        kill_fl=lambda: kills.append(1),
        **kw,
    )
    return result, kills


def test_song_mode_is_the_default_and_closes_our_fl(setup) -> None:
    _app, flp, cache = setup
    ui = FakeExportUi()
    result, kills = _song(setup, ui)
    assert result.outcome == "exported" and result.cached is False
    assert result.duration_s == pytest.approx(2.0, abs=0.01)
    assert result.wav_path.parent == cache.root
    rendered_flp, _timeout = ui.calls[0]
    assert rendered_flp.name == "render.flp" and rendered_flp.parent != flp.parent
    assert kills == [1]
    again, _ = _song(setup, ui)
    assert again.cached is True and len(ui.calls) == 1


def test_song_and_pattern_renders_are_cached_separately(setup) -> None:
    ui = FakeExportUi()
    launcher = FakeLauncher()
    _song(setup, ui)
    _render(setup, launcher)  # mode="pattern"
    assert len(ui.calls) == 1 and len(launcher.argvs) == 1


def test_song_mode_fl_busy_never_touches_ui(setup) -> None:
    ui = FakeExportUi()
    with pytest.raises(RenderError) as exc:
        _song(setup, ui, is_fl_running=lambda: True)
    assert exc.value.code == "FL_BUSY" and ui.calls == []


def test_song_mode_ui_failure_still_closes_fl(setup) -> None:
    ui = FakeExportUi(error=RenderError("RENDER_TIMEOUT", "no render window"))
    with pytest.raises(RenderError) as exc:
        _song(setup, ui)
    assert exc.value.code == "RENDER_TIMEOUT"
    assert len(ui.calls) == 1


def test_song_mode_without_wav_is_render_failed(setup) -> None:
    with pytest.raises(RenderError) as exc:
        _song(setup, FakeExportUi(write=False))
    assert exc.value.code == "RENDER_FAILED"
