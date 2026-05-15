"""Unit tests for the factory preset browser (F9.3.3)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from flstudio_mcp.preset_browser import (
    PresetEntry,
    _classify,
    _walk_install,
    enumerate_factory_presets,
    load_manifest,
    manifest_path_for_version,
    to_dict,
)

# --------------------------------------------------------------------------- #
# Synthetic manifest helpers                                                  #
# --------------------------------------------------------------------------- #


def _write_synthetic_manifest(dir_path: Path, fl_version: str, presets: list[dict]) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    payload = {
        "fl_version": fl_version,
        "generated_at": "2026-05-15T00:00:00+00:00",
        "install_root_at_build": "/fake/FL.app",
        "count": len(presets),
        "presets": presets,
    }
    out = dir_path / f"fl-{fl_version}.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out


def _preset(
    plugin: str,
    name: str,
    *,
    category: str = "Generators",
    kind: str = "generator",
    size: int = 192,
) -> dict:
    return {
        "path": f"%FLStudioFactoryData%/Data/Patches/Plugin presets/{category}/{plugin}/{name}.fst",
        "plugin": plugin,
        "preset_name": name,
        "category": category,
        "kind": kind,
        "size_bytes": size,
    }


def _seed_manifest(tmp_path: Path) -> Path:
    return _write_synthetic_manifest(
        tmp_path,
        "25.2.5",
        [
            _preset("Fruity DX10", "Steel Guitar"),
            _preset("Fruity DX10", "Gill Sweep"),
            _preset("Fruity DX10", "Chunky Bass"),
            _preset("FL Keys", "Concert Piano"),
            _preset("Sytrus", "Default subtractive", size=1193),
            _preset("Drumaxx", "Bite Me FG", size=21795),
            _preset(
                "Fruity Reeverb 2",
                "Cathedral",
                category="Effects",
                kind="effect",
                size=158,
            ),
            _preset(
                "Fruity Reeverb 2",
                "Hall",
                category="Effects",
                kind="effect",
                size=160,
            ),
            _preset(
                "3x Osc",
                "Default subtractive",
                category="Channel",
                kind="channel_state",
                size=400,
            ),
        ],
    )


# --------------------------------------------------------------------------- #
# Default load                                                                #
# --------------------------------------------------------------------------- #


def test_default_load_returns_all_under_limit(tmp_path: Path) -> None:
    _seed_manifest(tmp_path)
    entries = enumerate_factory_presets(fl_version="25.2.5", manifest_dir=tmp_path, limit=100)
    assert len(entries) == 9
    assert all(isinstance(e, PresetEntry) for e in entries)


def test_plugin_filter_is_case_insensitive_substring(tmp_path: Path) -> None:
    _seed_manifest(tmp_path)
    entries = enumerate_factory_presets(fl_version="25.2.5", manifest_dir=tmp_path, plugin="dx10")
    assert len(entries) == 3
    assert {e.preset_name for e in entries} == {"Steel Guitar", "Gill Sweep", "Chunky Bass"}


def test_kind_filter_exact_match(tmp_path: Path) -> None:
    _seed_manifest(tmp_path)
    effects = enumerate_factory_presets(fl_version="25.2.5", manifest_dir=tmp_path, kind="effect")
    assert len(effects) == 2
    assert {e.plugin for e in effects} == {"Fruity Reeverb 2"}

    channels = enumerate_factory_presets(
        fl_version="25.2.5", manifest_dir=tmp_path, kind="channel_state"
    )
    assert len(channels) == 1
    assert channels[0].plugin == "3x Osc"


def test_query_filter_matches_preset_name(tmp_path: Path) -> None:
    _seed_manifest(tmp_path)
    entries = enumerate_factory_presets(fl_version="25.2.5", manifest_dir=tmp_path, query="bass")
    assert len(entries) == 1
    assert entries[0].preset_name == "Chunky Bass"


def test_combined_plugin_and_query(tmp_path: Path) -> None:
    _seed_manifest(tmp_path)
    entries = enumerate_factory_presets(
        fl_version="25.2.5",
        manifest_dir=tmp_path,
        plugin="Reeverb",
        query="cath",
    )
    assert len(entries) == 1
    assert entries[0].preset_name == "Cathedral"
    assert entries[0].kind == "effect"


def test_limit_clamped(tmp_path: Path) -> None:
    _seed_manifest(tmp_path)
    entries = enumerate_factory_presets(fl_version="25.2.5", manifest_dir=tmp_path, limit=3)
    assert len(entries) == 3


def test_limit_lower_bound(tmp_path: Path) -> None:
    _seed_manifest(tmp_path)
    entries = enumerate_factory_presets(fl_version="25.2.5", manifest_dir=tmp_path, limit=0)
    # Implementation clamps to >=1.
    assert len(entries) == 1


# --------------------------------------------------------------------------- #
# Manifest selection                                                          #
# --------------------------------------------------------------------------- #


def test_manifest_path_exact_version_match(tmp_path: Path) -> None:
    _write_synthetic_manifest(tmp_path, "25.2.5", [])
    _write_synthetic_manifest(tmp_path, "25.4.4", [])
    m = manifest_path_for_version("25.2.5", manifest_dir=tmp_path)
    assert m is not None
    assert m.name == "fl-25.2.5.json"


def test_manifest_path_major_version_fallback(tmp_path: Path) -> None:
    _write_synthetic_manifest(tmp_path, "25.4.4", [])
    _write_synthetic_manifest(tmp_path, "24.0.0", [])
    # User has 25.9.9 installed; no exact match. Should pick 25.4.4
    # (same major), not 24.0.0.
    m = manifest_path_for_version("25.9.9", manifest_dir=tmp_path)
    assert m is not None
    assert m.name == "fl-25.4.4.json"


def test_manifest_path_returns_none_when_dir_empty(tmp_path: Path) -> None:
    (tmp_path / "factory_presets").mkdir()
    m = manifest_path_for_version("25.2.5", manifest_dir=tmp_path / "factory_presets")
    assert m is None


def test_manifest_path_returns_none_when_dir_missing(tmp_path: Path) -> None:
    m = manifest_path_for_version("25.2.5", manifest_dir=tmp_path / "definitely-does-not-exist")
    assert m is None


# --------------------------------------------------------------------------- #
# Missing-manifest fallback walks the filesystem                              #
# --------------------------------------------------------------------------- #


def test_missing_manifest_falls_back_to_fs_walk(tmp_path: Path) -> None:
    # Empty manifest dir → fs walk fallback.
    empty = tmp_path / "empty_manifest"
    empty.mkdir()
    # Build a tiny synthetic install tree under tmp_path with one .fst
    # in each known root.
    install = tmp_path / "FL.app" / "Contents" / "Resources" / "FL" / "Data"
    gen = install / "Patches" / "Plugin presets" / "Generators" / "Fruity DX10"
    eff = install / "Patches" / "Plugin presets" / "Effects" / "Fruity Reeverb 2"
    chan = install / "Patches" / "Channel presets" / "3x Osc"
    for d in (gen, eff, chan):
        d.mkdir(parents=True)
    (gen / "Steel Guitar.fst").write_bytes(b"\0" * 192)
    (eff / "Cathedral.fst").write_bytes(b"\0" * 158)
    (chan / "Default subtractive.fst").write_bytes(b"\0" * 400)

    entries = enumerate_factory_presets(
        fl_version="25.2.5",
        manifest_dir=empty,
        install_root=install,
    )
    by_kind = {e.kind: e for e in entries}
    assert {"generator", "effect", "channel_state"}.issubset(by_kind)
    assert by_kind["generator"].plugin == "Fruity DX10"
    assert by_kind["effect"].plugin == "Fruity Reeverb 2"
    assert by_kind["channel_state"].plugin == "3x Osc"


def test_walk_skips_unclassified_fst(tmp_path: Path) -> None:
    install = tmp_path / "FL.app" / "Contents" / "Resources" / "FL" / "Data"
    # A `.fst` outside the known roots (e.g. accidentally under Misc/).
    misc = install / "Patches" / "Misc" / "Stray"
    misc.mkdir(parents=True)
    (misc / "orphan.fst").write_bytes(b"\0" * 100)
    entries = _walk_install(install)
    assert entries == []


# --------------------------------------------------------------------------- #
# _classify edge cases                                                        #
# --------------------------------------------------------------------------- #


def test_classify_generator_path() -> None:
    parts = ("Plugin presets", "Generators", "Fruity DX10", "Steel Guitar.fst")
    assert _classify(parts) == ("Generators", "Fruity DX10", "generator")


def test_classify_effect_path() -> None:
    parts = ("Plugin presets", "Effects", "Fruity Reeverb 2", "Cathedral.fst")
    assert _classify(parts) == ("Effects", "Fruity Reeverb 2", "effect")


def test_classify_channel_path() -> None:
    parts = ("Channel presets", "3x Osc", "Default subtractive.fst")
    assert _classify(parts) == ("Channel", "3x Osc", "channel_state")


def test_classify_unknown_root_returns_none() -> None:
    parts = ("Misc", "Stray", "orphan.fst")
    assert _classify(parts) is None


def test_classify_too_shallow_returns_none() -> None:
    assert _classify(("Plugin presets",)) is None
    assert _classify(("Plugin presets", "Generators")) is None


# --------------------------------------------------------------------------- #
# to_dict                                                                     #
# --------------------------------------------------------------------------- #


def test_to_dict_round_trip() -> None:
    e = PresetEntry(
        path="%FLStudioFactoryData%/x.fst",
        plugin="Fruity DX10",
        preset_name="Steel Guitar",
        category="Generators",
        kind="generator",
        size_bytes=192,
    )
    d = to_dict(e)
    assert d == {
        "path": "%FLStudioFactoryData%/x.fst",
        "plugin": "Fruity DX10",
        "preset_name": "Steel Guitar",
        "category": "Generators",
        "kind": "generator",
        "size_bytes": 192,
    }


# --------------------------------------------------------------------------- #
# Gzip manifest support — production ships .json.gz to fit under the 500 KB   #
# repo large-file threshold.                                                  #
# --------------------------------------------------------------------------- #


def test_load_manifest_autodetects_gzip(tmp_path: Path) -> None:
    payload = {
        "fl_version": "25.2.5",
        "generated_at": "2026-05-15T00:00:00+00:00",
        "install_root_at_build": "/fake",
        "count": 1,
        "presets": [_preset("Fruity DX10", "Bass Pluck")],
    }
    gz_path = tmp_path / "fl-25.2.5.json.gz"
    with gzip.open(gz_path, "wb") as fp:
        fp.write(json.dumps(payload).encode("utf-8"))
    loaded = load_manifest(gz_path)
    assert loaded["fl_version"] == "25.2.5"
    assert loaded["count"] == 1


def test_manifest_path_picks_gz_when_only_gz_present(tmp_path: Path) -> None:
    gz_path = tmp_path / "fl-25.2.5.json.gz"
    with gzip.open(gz_path, "wb") as fp:
        fp.write(b'{"presets": []}')
    m = manifest_path_for_version("25.2.5", manifest_dir=tmp_path)
    assert m == gz_path


def test_manifest_path_prefers_plain_json_over_gz_when_both_exist(tmp_path: Path) -> None:
    plain = _write_synthetic_manifest(tmp_path, "25.2.5", [])
    gz = tmp_path / "fl-25.2.5.json.gz"
    with gzip.open(gz, "wb") as fp:
        fp.write(b'{"presets": []}')
    m = manifest_path_for_version("25.2.5", manifest_dir=tmp_path)
    # Exact-suffix-match prefers .json before .json.gz.
    assert m == plain


def test_enumerate_loads_gz_manifest(tmp_path: Path) -> None:
    payload = {
        "fl_version": "25.2.5",
        "generated_at": "2026-05-15T00:00:00+00:00",
        "install_root_at_build": "/fake",
        "count": 2,
        "presets": [
            _preset("Fruity DX10", "Bass Pluck"),
            _preset("Sytrus", "Lead 1"),
        ],
    }
    gz_path = tmp_path / "fl-25.2.5.json.gz"
    with gzip.open(gz_path, "wb") as fp:
        fp.write(json.dumps(payload).encode("utf-8"))
    entries = enumerate_factory_presets(fl_version="25.2.5", manifest_dir=tmp_path)
    assert len(entries) == 2
    assert {e.plugin for e in entries} == {"Fruity DX10", "Sytrus"}
