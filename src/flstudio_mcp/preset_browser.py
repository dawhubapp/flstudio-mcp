"""Factory plugin-preset inventory for FL Studio — manifest-first lookup.

Per D-63d (Phase F9.0): factory plugin presets (`.fst` files under
`<FL>/Contents/Resources/FL/Data/Patches/Plugin presets/` plus
`Channel presets/`) are static per FL version. Ship a pre-built JSON
manifest at `mcp/data/factory_presets/fl-<version>.json`; load it at
runtime instead of walking the filesystem on every query. Live fs
walk is the fallback when no matching manifest exists OR
`force_walk=True`.

Manifest schema (D-63c — `size_bytes` only, no `last_modified` since
install timestamps carry no signal):

```
{
  "fl_version": "25.4.4",
  "generated_at": "2026-05-15T...",
  "install_root_at_build": "/Applications/FL Studio 2025.app/...",
  "count": 7250,
  "presets": [
    {
      "path": "%FLStudioFactoryData%/Data/Patches/Plugin presets/Generators/Fruity DX10/Steel Guitar.fst",
      "plugin": "Fruity DX10",
      "preset_name": "Steel Guitar",
      "category": "Generators",
      "kind": "generator",
      "size_bytes": 192
    },
    ...
  ]
}
```

`path` mirrors the FL library-token form (same as F7.1 sample
manifest). `kind` is one of `generator` / `effect` / `channel_state`;
splice helpers in flpdiff route on it (`loadFactoryGeneratorPreset`
vs `loadFactoryEffectPreset`).

Source-directory → `category` / `kind` mapping:

| Source under `Patches/`              | `category`   | `kind`           |
|--------------------------------------|--------------|------------------|
| `Plugin presets/Generators/<plugin>` | `Generators` | `generator`      |
| `Plugin presets/Effects/<plugin>`    | `Effects`    | `effect`         |
| `Channel presets/<plugin>`           | `Channel`    | `channel_state`  |

This module is intentionally read-only; the splice helpers live on
the flpdiff side and operate on parsed `.fst` donors.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .sample_browser import (
    DEFAULT_FACTORY_TOKEN,
    DEFAULT_FL_APP,
    detect_fl_version,
)

# Resolve the bundled manifest dir relative to the package:
#   mcp/src/flstudio_mcp/preset_browser.py
#   mcp/data/factory_presets/fl-<version>.json
_PKG_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PRESET_MANIFEST_DIR = _PKG_ROOT / "data" / "factory_presets"

PresetKind = Literal["generator", "effect", "channel_state"]


@dataclass
class PresetEntry:
    """One factory plugin preset. `path` is the FL library-token form;
    `plugin` is the parent directory name (`Fruity DX10`, `Sytrus`, ...).
    `kind` distinguishes splice target — `generator` and `channel_state`
    go to a new channel, `effect` goes to a mixer slot."""

    path: str
    plugin: str
    preset_name: str
    category: str  # "Generators" | "Effects" | "Channel"
    kind: PresetKind
    size_bytes: int


def manifest_path_for_version(
    fl_version: str | None,
    *,
    manifest_dir: Path = DEFAULT_PRESET_MANIFEST_DIR,
) -> Path | None:
    """Pick the manifest matching ``fl_version`` (exact match first,
    then highest-major-version match, then any). Returns None if no
    manifest exists in `manifest_dir`.

    Accepts both plain `.json` and gzipped `.json.gz` manifest files —
    the preset manifest is ~2 MB uncompressed and ships gzipped to
    stay under the repo's large-file threshold. Loader auto-detects
    via the file extension.
    """
    if not manifest_dir.is_dir():
        return None
    candidates = sorted(
        list(manifest_dir.glob("fl-*.json")) + list(manifest_dir.glob("fl-*.json.gz")),
        reverse=True,
    )
    if not candidates:
        return None
    if fl_version is None:
        return candidates[0]
    for suffix in (".json", ".json.gz"):
        exact = manifest_dir / f"fl-{fl_version}{suffix}"
        if exact.is_file():
            return exact
    major = fl_version.split(".", 1)[0]
    for c in candidates:
        stem = c.name.removeprefix("fl-").removesuffix(".gz").removesuffix(".json")
        if stem.split(".", 1)[0] == major:
            return c
    return candidates[0]


def load_manifest(path: Path) -> dict:
    """Load a manifest from JSON or gzipped JSON, autodetected by suffix."""
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as fp:
            return json.load(fp)
    return json.loads(path.read_text(encoding="utf-8"))


def _entries_from_manifest(payload: dict) -> list[PresetEntry]:
    out: list[PresetEntry] = []
    for s in payload.get("presets", []):
        kind = s.get("kind", "generator")
        if kind not in ("generator", "effect", "channel_state"):
            kind = "generator"
        out.append(
            PresetEntry(
                path=s.get("path", ""),
                plugin=s.get("plugin", ""),
                preset_name=s.get("preset_name", ""),
                category=s.get("category", ""),
                kind=kind,
                size_bytes=int(s.get("size_bytes", 0)),
            )
        )
    return out


def _classify(rel_parts: tuple[str, ...]) -> tuple[str, str, PresetKind] | None:
    """Map a path relative to ``Patches/`` to ``(category, plugin, kind)``.
    Returns None for `.fst` files outside the three known roots."""
    if len(rel_parts) < 2:
        return None
    head = rel_parts[0]
    if head == "Plugin presets" and len(rel_parts) >= 3:
        sub = rel_parts[1]
        plugin = rel_parts[2]
        if sub == "Generators":
            return "Generators", plugin, "generator"
        if sub == "Effects":
            return "Effects", plugin, "effect"
        return None
    if head == "Channel presets" and len(rel_parts) >= 2:
        plugin = rel_parts[1]
        return "Channel", plugin, "channel_state"
    return None


def _walk_install(install_root: Path) -> list[PresetEntry]:
    """Live filesystem walk for `.fst` files under the three preset
    roots: `Plugin presets/Generators/<plugin>/`,
    `Plugin presets/Effects/<plugin>/`, `Channel presets/<plugin>/`.

    Skips files that don't classify (avoids leaking unrelated `.fst`
    blobs the builder script should have filtered out).
    """
    patches_root = install_root / "Patches"
    if not patches_root.is_dir():
        return []
    out: list[PresetEntry] = []
    for fst in patches_root.rglob("*.fst"):
        rel = fst.relative_to(patches_root)
        classified = _classify(rel.parts)
        if classified is None:
            continue
        category, plugin, kind = classified
        path_token = f"{DEFAULT_FACTORY_TOKEN}/Data/Patches/{rel.as_posix()}"
        try:
            size = fst.stat().st_size
        except OSError:
            size = 0
        out.append(
            PresetEntry(
                path=path_token,
                plugin=plugin,
                preset_name=fst.stem,
                category=category,
                kind=kind,
                size_bytes=size,
            )
        )
    return out


def _filter_entries(
    entries: Iterable[PresetEntry],
    *,
    plugin: str | None,
    kind: PresetKind | None,
    query: str | None,
    limit: int,
) -> list[PresetEntry]:
    plugin_lc = plugin.lower() if plugin else None
    q_lc = query.lower() if query else None
    out: list[PresetEntry] = []
    for e in entries:
        if plugin_lc and plugin_lc not in e.plugin.lower():
            continue
        if kind and e.kind != kind:
            continue
        if q_lc and q_lc not in e.preset_name.lower():
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def enumerate_factory_presets(
    *,
    fl_version: str | None = None,
    install_root: Path | None = None,
    manifest_dir: Path = DEFAULT_PRESET_MANIFEST_DIR,
    limit: int = 100,
    plugin: str | None = None,
    kind: PresetKind | None = None,
    query: str | None = None,
    force_walk: bool = False,
    fl_app: Path = DEFAULT_FL_APP,
) -> list[PresetEntry]:
    """Return factory plugin presets filtered by plugin name + kind +
    preset-name query.

    Lookup order:
    1. If `force_walk=True`: live walk of `install_root` (or fl_app).
    2. Otherwise: load manifest matching `fl_version` (auto-detect
       from fl_app if omitted).
    3. If no manifest matches: live walk fallback.

    `plugin` is a case-insensitive substring match. `query` matches
    against the preset name (filename stem). `kind` is an exact match.
    Defaults: limit=100, max=500 (caller clamps further if needed).
    """
    limit = max(1, min(limit, 500))

    if force_walk:
        root = install_root or (fl_app / "Contents" / "Resources" / "FL" / "Data")
        entries = _walk_install(root)
        return _filter_entries(entries, plugin=plugin, kind=kind, query=query, limit=limit)

    version = fl_version or detect_fl_version(fl_app)
    mpath = manifest_path_for_version(version, manifest_dir=manifest_dir)
    if mpath is not None:
        try:
            payload = load_manifest(mpath)
            entries = _entries_from_manifest(payload)
            return _filter_entries(entries, plugin=plugin, kind=kind, query=query, limit=limit)
        except Exception:
            pass  # fall through to fs walk

    root = install_root or (fl_app / "Contents" / "Resources" / "FL" / "Data")
    entries = _walk_install(root)
    return _filter_entries(entries, plugin=plugin, kind=kind, query=query, limit=limit)


def to_dict(entry: PresetEntry) -> dict:
    return asdict(entry)
