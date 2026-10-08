"""One-time builder for the factory-sample manifest.

Walks the FL install's Patches/Packs/ tree, collects every .wav,
emits JSON to mcp/data/factory_samples/fl-<version>.json. Run as
needed (FL minor updates, new packs installed). Commit the result.

Usage:
    cd mcp && uv run python scripts/build_factory_sample_manifest.py
    cd mcp && uv run python scripts/build_factory_sample_manifest.py \
        --fl-app "/Applications/FL Studio 2026.app" \
        --output data/factory_samples/fl-25.4.4.json

Per D-61d. Per F7.1.5a.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from flstudio_mcp.fl_app import resolve_fl_app
from flstudio_mcp.sample_browser import (
    DEFAULT_FACTORY_TOKEN,
    DEFAULT_MANIFEST_DIR,
    detect_fl_version,
)


def build_manifest(fl_app: Path) -> dict:
    packs_root = fl_app / "Contents" / "Resources" / "FL" / "Data" / "Patches" / "Packs"
    if not packs_root.is_dir():
        raise SystemExit(f"FL packs root not found: {packs_root}")

    samples: list[dict] = []
    for wav in sorted(packs_root.rglob("*.wav")):
        rel = wav.relative_to(packs_root)
        parts = rel.parts
        category = parts[0] if len(parts) > 0 else ""
        subcategory = parts[1] if len(parts) > 2 else ""
        token = f"{DEFAULT_FACTORY_TOKEN}/Data/Patches/Packs/{rel.as_posix()}"
        try:
            size = wav.stat().st_size
        except OSError:
            size = 0
        samples.append(
            {
                "token": token,
                "category": category,
                "subcategory": subcategory,
                "filename": wav.name,
                "size_bytes": size,
            }
        )

    fl_version = detect_fl_version(fl_app) or "unknown"
    return {
        "fl_version": fl_version,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "install_root_at_build": str(fl_app),
        "count": len(samples),
        "samples": samples,
    }


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
        help="Output JSON path (default: data/factory_samples/fl-<version>.json)",
    )
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        default=DEFAULT_MANIFEST_DIR,
        help="Default output dir when --output is omitted (default: %(default)s)",
    )
    args = parser.parse_args()

    payload = build_manifest(resolve_fl_app(args.fl_app))
    print(
        f"[builder] fl_version={payload['fl_version']!r} samples={payload['count']}",
        file=sys.stderr,
    )

    output = args.output
    if output is None:
        version = payload["fl_version"]
        output = args.manifest_dir / f"fl-{version}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    size_kb = output.stat().st_size / 1024
    print(f"[builder] wrote {output} ({size_kb:.1f} KB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
