"""One-time builder for the factory plugin-preset manifest.

Walks the FL install's `Patches/Plugin presets/Generators/`,
`Plugin presets/Effects/`, and `Channel presets/` trees, collects every
`.fst`, emits JSON to `mcp/data/factory_presets/fl-<version>.json`.
Run as needed (FL minor updates, new presets installed). Commit the
result.

Usage:
    cd mcp && uv run python scripts/build_factory_preset_manifest.py
    cd mcp && uv run python scripts/build_factory_preset_manifest.py \\
        --fl-app "/Applications/FL Studio 2026.app" \\
        --output data/factory_presets/fl-25.4.4.json

Per D-63c (size_bytes only — no last_modified). Per F9.3.1.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from flstudio_mcp.fl_app import resolve_fl_app
from flstudio_mcp.preset_browser import (
    DEFAULT_PRESET_MANIFEST_DIR,
    _classify,
)
from flstudio_mcp.sample_browser import (
    DEFAULT_FACTORY_TOKEN,
    detect_fl_version,
)


def build_manifest(fl_app: Path) -> dict:
    patches_root = fl_app / "Contents" / "Resources" / "FL" / "Data" / "Patches"
    if not patches_root.is_dir():
        raise SystemExit(f"FL Patches root not found: {patches_root}")

    presets: list[dict] = []
    skipped = 0
    for fst in sorted(patches_root.rglob("*.fst")):
        rel = fst.relative_to(patches_root)
        classified = _classify(rel.parts)
        if classified is None:
            skipped += 1
            continue
        category, plugin, kind = classified
        path_token = f"{DEFAULT_FACTORY_TOKEN}/Data/Patches/{rel.as_posix()}"
        try:
            size = fst.stat().st_size
        except OSError:
            size = 0
        presets.append(
            {
                "path": path_token,
                "plugin": plugin,
                "preset_name": fst.stem,
                "category": category,
                "kind": kind,
                "size_bytes": size,
            }
        )

    fl_version = detect_fl_version(fl_app) or "unknown"
    payload = {
        "fl_version": fl_version,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "install_root_at_build": str(fl_app),
        "count": len(presets),
        "presets": presets,
    }
    if skipped:
        print(
            f"[builder] skipped {skipped} unclassified .fst files (outside the known preset roots)",
            file=sys.stderr,
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fl-app",
        type=Path,
        default=None,
        help="FL Studio .app bundle path (default: newest /Applications/FL Studio N.app)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path (default: data/factory_presets/fl-<version>.json)",
    )
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        default=DEFAULT_PRESET_MANIFEST_DIR,
        help="Default output dir when --output is omitted (default: %(default)s)",
    )
    args = parser.parse_args()

    payload = build_manifest(resolve_fl_app(args.fl_app))
    print(
        f"[builder] fl_version={payload['fl_version']!r} presets={payload['count']}",
        file=sys.stderr,
    )

    output = args.output
    if output is None:
        # Default: gzipped JSON so the 7k-entry / ~2 MB manifest fits
        # under the repo's 500 KB large-file threshold (post-gzip ~150 KB).
        # Pass --output explicitly with a `.json` suffix to keep plain JSON.
        version = payload["fl_version"]
        output = args.manifest_dir / f"fl-{version}.json.gz"
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=False).encode("utf-8")
    if output.suffix == ".gz":
        with gzip.open(output, "wb") as fp:
            fp.write(encoded)
    else:
        output.write_bytes(encoded)
    size_kb = output.stat().st_size / 1024
    print(f"[builder] wrote {output} ({size_kb:.1f} KB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
