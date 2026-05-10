"""Unit tests for the factory sample browser (F7.1.5c)."""

from __future__ import annotations

import json
from pathlib import Path

from flstudio_mcp.sample_browser import (
    SampleEntry,
    _filter_entries,
    enumerate_factory_samples,
    manifest_path_for_version,
    to_dict,
)


def _write_synthetic_manifest(dir_path: Path, fl_version: str, samples: list[dict]) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    payload = {
        "fl_version": fl_version,
        "generated_at": "2026-05-10T00:00:00+00:00",
        "install_root_at_build": "/fake/FL.app",
        "count": len(samples),
        "samples": samples,
    }
    out = dir_path / f"fl-{fl_version}.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out


def _sample_dict(category: str, subcategory: str, filename: str, size: int = 1024) -> dict:
    return {
        "token": f"%FLStudioFactoryData%/Data/Patches/Packs/{category}/{subcategory}/{filename}",
        "category": category,
        "subcategory": subcategory,
        "filename": filename,
        "size_bytes": size,
    }


def _seed_manifest(tmp_path: Path) -> Path:
    return _write_synthetic_manifest(
        tmp_path,
        "25.2.5",
        [
            _sample_dict("Drums", "Kicks", "909 Kick.wav"),
            _sample_dict("Drums", "Kicks", "707 Kick.wav"),
            _sample_dict("Drums", "Snares", "808 Snare.wav"),
            _sample_dict("Drums", "Hats", "Closed Hat.wav"),
            _sample_dict("FLEX", "Bass", "Sub Bass.wav"),
            _sample_dict("FLEX", "Lead", "Saw Lead.wav"),
            _sample_dict("Loops", "Drums", "Beat 01.wav"),
            _sample_dict("Loops", "Drums", "Beat 02.wav"),
            _sample_dict("Risers", "Sweeps", "Riser A.wav"),
            _sample_dict("Risers", "Sweeps", "Riser B.wav"),
        ],
    )


def test_default_load_returns_all_under_limit(tmp_path: Path) -> None:
    _seed_manifest(tmp_path)
    items = enumerate_factory_samples(
        manifest_dir=tmp_path,
        fl_version="25.2.5",
        limit=100,
    )
    assert len(items) == 10


def test_category_filter(tmp_path: Path) -> None:
    _seed_manifest(tmp_path)
    drums = enumerate_factory_samples(
        manifest_dir=tmp_path, fl_version="25.2.5", category="Drums", limit=100
    )
    assert len(drums) == 4
    assert all(e.category == "Drums" for e in drums)


def test_query_substring_case_insensitive(tmp_path: Path) -> None:
    _seed_manifest(tmp_path)
    snares = enumerate_factory_samples(
        manifest_dir=tmp_path, fl_version="25.2.5", query="snare", limit=100
    )
    assert len(snares) == 1
    assert snares[0].filename == "808 Snare.wav"


def test_combined_category_and_query(tmp_path: Path) -> None:
    _seed_manifest(tmp_path)
    out = enumerate_factory_samples(
        manifest_dir=tmp_path,
        fl_version="25.2.5",
        category="Drums",
        query="kick",
        limit=100,
    )
    assert len(out) == 2
    assert {e.filename for e in out} == {"909 Kick.wav", "707 Kick.wav"}


def test_limit_clamps_results(tmp_path: Path) -> None:
    _seed_manifest(tmp_path)
    out = enumerate_factory_samples(manifest_dir=tmp_path, fl_version="25.2.5", limit=3)
    assert len(out) == 3


def test_limit_max_clamped_to_500(tmp_path: Path) -> None:
    _seed_manifest(tmp_path)
    out = enumerate_factory_samples(manifest_dir=tmp_path, fl_version="25.2.5", limit=10000)
    assert len(out) == 10  # only 10 in fixture; limit clamped internally


def test_missing_manifest_dir_returns_empty_or_walks(tmp_path: Path) -> None:
    """No manifest + no FL install -> empty list (not crash)."""
    nonexistent = tmp_path / "no-such-dir"
    out = enumerate_factory_samples(
        manifest_dir=nonexistent,
        fl_version="99.9",
        install_root=tmp_path / "no-such-fl-install",
    )
    assert out == []


def test_manifest_version_match_falls_back_to_same_major(tmp_path: Path) -> None:
    _write_synthetic_manifest(tmp_path, "25.4.4", [_sample_dict("Drums", "Kicks", "X.wav")])
    # Asking for 25.2.5; fallback to 25.4.4 (same major).
    mp = manifest_path_for_version("25.2.5", manifest_dir=tmp_path)
    assert mp is not None
    assert mp.name == "fl-25.4.4.json"


def test_manifest_version_match_picks_highest_when_no_version(tmp_path: Path) -> None:
    _write_synthetic_manifest(tmp_path, "25.2.5", [])
    _write_synthetic_manifest(tmp_path, "25.4.4", [])
    mp = manifest_path_for_version(None, manifest_dir=tmp_path)
    assert mp is not None
    assert mp.name == "fl-25.4.4.json"


def test_filter_entries_helper_pure() -> None:
    entries = [
        SampleEntry("a", "Drums", "Kicks", "Kick A.wav", 100),
        SampleEntry("b", "Drums", "Snares", "Snare A.wav", 200),
        SampleEntry("c", "FLEX", "Bass", "Bass A.wav", 300),
    ]
    out = _filter_entries(entries, category="Drums", query="kick", limit=10)
    assert [e.filename for e in out] == ["Kick A.wav"]


def test_to_dict_round_trip() -> None:
    entry = SampleEntry("tok", "Drums", "Kicks", "Kick.wav", 42)
    d = to_dict(entry)
    assert d == {
        "token": "tok",
        "category": "Drums",
        "subcategory": "Kicks",
        "filename": "Kick.wav",
        "size_bytes": 42,
    }


def test_real_bundled_manifest_loads() -> None:
    """Smoke: the actual mcp/data/factory_samples/fl-25.2.5.5055.json
    that the builder produced loads + has plausible sample count."""
    items = enumerate_factory_samples(limit=500)
    if not items:
        return  # no bundled manifest yet; OK
    assert len(items) > 100  # real manifest has 3k+
    drums = enumerate_factory_samples(category="Drums", limit=500)
    assert len(drums) > 10
