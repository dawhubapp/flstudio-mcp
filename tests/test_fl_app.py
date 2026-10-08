"""Tests for flstudio_mcp.fl_app (locating the installed FL Studio bundle)."""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from flstudio_mcp import fl_app, preset_browser, sample_browser
from flstudio_mcp.fl_app import resolve_fl_app


def _install(apps: Path, name: str, version: str | None = None) -> Path:
    bundle = apps / name
    (bundle / "Contents").mkdir(parents=True)
    if version:
        with (bundle / "Contents" / "Info.plist").open("wb") as fh:
            plistlib.dump({"CFBundleShortVersionString": version}, fh)
    return bundle


@pytest.fixture
def apps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(fl_app, "APPLICATIONS", tmp_path)
    monkeypatch.delenv(fl_app.FL_APP_ENV, raising=False)
    return tmp_path


def test_resolve_fl_app_picks_newest_install(apps: Path) -> None:
    for name in ("FL Studio 21.app", "FL Studio 2025.app", "FL Studio 2026.app", "FL Cloud.app"):
        (apps / name).mkdir()
    assert resolve_fl_app() == apps / "FL Studio 2026.app"
    assert resolve_fl_app(Path("/x.app")) == Path("/x.app")


def test_resolve_fl_app_env_override(apps: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(fl_app.FL_APP_ENV, "/Volumes/X/FL Studio 2026.app")
    assert resolve_fl_app() == Path("/Volumes/X/FL Studio 2026.app")
    monkeypatch.delenv(fl_app.FL_APP_ENV)
    assert not resolve_fl_app().exists()  # nothing installed -> callers report not found


def test_detect_fl_version_defaults_to_newest_install(apps: Path) -> None:
    _install(apps, "FL Studio 2025.app", "25.2.5.5055")
    _install(apps, "FL Studio 2026.app", "26.1.7.5419")
    assert sample_browser.detect_fl_version() == "26.1.7.5419"


def test_preset_rescan_defaults_to_newest_install(apps: Path) -> None:
    bundle = _install(apps, "FL Studio 2026.app")
    gen = bundle / "Contents/Resources/FL/Data/Patches/Plugin presets/Generators/Kepler"
    gen.mkdir(parents=True)
    (gen / "Default.fst").write_bytes(b"FLhd")
    entries = preset_browser.enumerate_factory_presets(force_walk=True)
    assert [(e.plugin, e.preset_name) for e in entries] == [("Kepler", "Default")]
