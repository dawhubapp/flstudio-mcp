"""Factory sample inventory for FL Studio — manifest-first lookup.

Per D-61d: factory samples are static per FL version. Ship a versioned
JSON manifest in `mcp/data/factory_samples/fl-<version>.json`; load it
at runtime instead of walking the filesystem on every query. Live
walk is the fallback when the manifest is missing OR `force_walk=True`.

Manifest schema:
{
  "fl_version": "25.4.4",
  "generated_at": "2026-05-10T...",
  "install_root_at_build": "/Applications/FL Studio 2025.app/...",
  "count": 3062,
  "samples": [
    {
      "token": "%FLStudioFactoryData%/Data/Patches/Packs/Drums/Kicks/909 Kick.wav",
      "category": "Drums",
      "subcategory": "Kicks",
      "filename": "909 Kick.wav",
      "size_bytes": 12345
    },
    ...
  ]
}

Token form mirrors what FL itself emits in 0xC4 channel sample-path
events (see flpdiff/src/model/channel.ts). The agent passes tokens
to `set_channel_sample_path` verbatim — FL resolves at load time.
"""

from __future__ import annotations

import json
import plistlib
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

# Resolve the bundled manifest dir relative to the package:
#   mcp/src/flstudio_mcp/sample_browser.py
#   mcp/data/factory_samples/fl-<version>.json
_PKG_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_MANIFEST_DIR = _PKG_ROOT / "data" / "factory_samples"

DEFAULT_FL_APP = Path("/Applications/FL Studio 2025.app")
DEFAULT_FACTORY_TOKEN = "%FLStudioFactoryData%"


@dataclass
class SampleEntry:
    """One factory sample. `token` is the FL library-token form;
    `category`/`subcategory` are derived from path structure under
    `Patches/Packs/`."""

    token: str
    category: str
    subcategory: str
    filename: str
    size_bytes: int


def detect_fl_version(fl_app: Path = DEFAULT_FL_APP) -> str | None:
    """Read FL's bundle Info.plist to extract `CFBundleShortVersionString`.

    Returns None if the app isn't installed or plist is unreadable.
    """
    plist_path = fl_app / "Contents" / "Info.plist"
    if not plist_path.is_file():
        return None
    try:
        with plist_path.open("rb") as fp:
            plist = plistlib.load(fp)
    except Exception:
        return None
    version = plist.get("CFBundleShortVersionString")
    return str(version) if version else None


def manifest_path_for_version(
    fl_version: str | None,
    *,
    manifest_dir: Path = DEFAULT_MANIFEST_DIR,
) -> Path | None:
    """Pick the manifest matching ``fl_version`` (exact match first,
    then highest-major-version match, then any). Returns None if no
    manifest exists in `manifest_dir`."""
    if not manifest_dir.is_dir():
        return None
    candidates = sorted(manifest_dir.glob("fl-*.json"), reverse=True)
    if not candidates:
        return None
    if fl_version is None:
        return candidates[0]
    # Exact: fl-25.4.4.json
    exact = manifest_dir / f"fl-{fl_version}.json"
    if exact.is_file():
        return exact
    # Same major: fl-25.* matches user's FL 25.4.4
    major = fl_version.split(".", 1)[0]
    for c in candidates:
        stem = c.stem.removeprefix("fl-")
        if stem.split(".", 1)[0] == major:
            return c
    return candidates[0]


def load_manifest(path: Path) -> dict:
    """Load + parse a manifest JSON. Caller checks `samples` key."""
    return json.loads(path.read_text(encoding="utf-8"))


def _entries_from_manifest(payload: dict) -> list[SampleEntry]:
    out: list[SampleEntry] = []
    for s in payload.get("samples", []):
        out.append(
            SampleEntry(
                token=s.get("token", ""),
                category=s.get("category", ""),
                subcategory=s.get("subcategory", ""),
                filename=s.get("filename", ""),
                size_bytes=int(s.get("size_bytes", 0)),
            )
        )
    return out


def _walk_install(install_root: Path) -> list[SampleEntry]:
    """Live filesystem walk under `<install_root>/Patches/Packs/`.
    Same shape the builder script emits."""
    packs_root = install_root / "Patches" / "Packs"
    if not packs_root.is_dir():
        return []
    out: list[SampleEntry] = []
    for wav in packs_root.rglob("*.wav"):
        rel = wav.relative_to(packs_root)
        parts = rel.parts
        category = parts[0] if len(parts) > 0 else ""
        subcategory = parts[1] if len(parts) > 2 else ""
        token = f"{DEFAULT_FACTORY_TOKEN}/Data/Patches/Packs/{rel.as_posix()}"
        try:
            size = wav.stat().st_size
        except OSError:
            size = 0
        out.append(
            SampleEntry(
                token=token,
                category=category,
                subcategory=subcategory,
                filename=wav.name,
                size_bytes=size,
            )
        )
    return out


def _filter_entries(
    entries: Iterable[SampleEntry],
    *,
    category: str | None,
    query: str | None,
    limit: int,
) -> list[SampleEntry]:
    cat_lc = category.lower() if category else None
    q_lc = query.lower() if query else None
    out: list[SampleEntry] = []
    for e in entries:
        if cat_lc and e.category.lower() != cat_lc:
            continue
        if q_lc and q_lc not in e.filename.lower():
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def enumerate_factory_samples(
    *,
    fl_version: str | None = None,
    install_root: Path | None = None,
    manifest_dir: Path = DEFAULT_MANIFEST_DIR,
    limit: int = 100,
    category: str | None = None,
    query: str | None = None,
    force_walk: bool = False,
    fl_app: Path = DEFAULT_FL_APP,
) -> list[SampleEntry]:
    """Return factory samples filtered by category + filename query.

    Lookup order:
    1. If `force_walk=True`: live walk of `install_root` (or fl_app's bundled Packs).
    2. Otherwise: load manifest matching `fl_version` (or auto-detect from fl_app).
    3. If no manifest matches: live walk fallback.

    Defaults: limit=100, max=500 (caller's responsibility to clamp
    further).
    """
    limit = max(1, min(limit, 500))

    if force_walk:
        root = install_root or (fl_app / "Contents" / "Resources" / "FL" / "Data")
        entries = _walk_install(root)
        return _filter_entries(entries, category=category, query=query, limit=limit)

    version = fl_version or detect_fl_version(fl_app)
    mpath = manifest_path_for_version(version, manifest_dir=manifest_dir)
    if mpath is not None:
        try:
            payload = load_manifest(mpath)
            entries = _entries_from_manifest(payload)
            return _filter_entries(entries, category=category, query=query, limit=limit)
        except Exception:
            pass  # fall through to fs walk

    # Fallback: fs walk
    root = install_root or (fl_app / "Contents" / "Resources" / "FL" / "Data")
    entries = _walk_install(root)
    return _filter_entries(entries, category=category, query=query, limit=limit)


def to_dict(entry: SampleEntry) -> dict:
    return asdict(entry)
