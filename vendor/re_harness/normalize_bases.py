"""Normalize RE base FLPs through FL Studio's own save pipeline.

Problem we're solving
---------------------

When you hand-create ``base_empty.flp`` in FL Studio, FL saves it in one
event layout. Later, when the cycle helper re-saves the same project after
``set_tempo``, FL uses a slightly different layout: some events get split
into smaller ones, some get reordered, some metadata (undo history, save
counter) gets appended. Our naive position-based binary diff then reports
hundreds of "added/removed" events when only tempo really changed.

Fix: pass every base through the same save pipeline *once* before using it
as a reference. After that, ``base`` and ``scratch`` come from the identical
FL save path, so the only remaining byte differences reflect the actual
modification.

What this script does
---------------------

For each base FLP path you give it:

1. Open the file in FL Studio via ``open -a`` (autodrive).
2. Wait for the project to load.
3. Trigger **File → Save** via AppleScript — FL writes over the same path
   in its canonical layout.
4. Report the before / after byte size.

**This is destructive** — it rewrites the committed base FLP in place. That
is the point: the normalized version *is* the new canonical base. Commit
the result. If you want a pre-normalize snapshot, pass ``--backup`` and the
script writes ``<name>.pre_normalize.flp`` alongside the base first.

Typical one-shot use:

::

    python -m tools.re_harness.normalize_bases \\
        tests/corpus/re_base/fl25/base_empty.flp \\
        tests/corpus/re_base/fl25/base_one_channel.flp \\
        tests/corpus/re_base/fl25/base_one_pattern.flp \\
        tests/corpus/re_base/fl25/base_one_insert.flp \\
        tests/corpus/re_base/fl25/base_one_serum.flp \\
        --backup

Then ``git diff tests/corpus/re_base/fl25/`` shows what FL's canonical save
layout looks like, and a subsequent cycle run produces a clean tempo diff.
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from .autodrive import (
    AutodriveError,
    activate_fl_studio,
    close_current_project,
    open_flp,
    save_via_menu,
)
from .ipc import Command, default_inbox


def _mtime(p: Path) -> float:
    return p.stat().st_mtime


def normalize_one(base: Path, *, backup: bool, wait_seconds: float) -> bool:
    """Return True if FL's save actually rewrote ``base``."""
    if not base.exists():
        print(f"  SKIP: {base} does not exist")
        return False
    before_mtime = _mtime(base)
    before_size = base.stat().st_size

    if backup:
        backup_path = base.with_suffix(".pre_normalize.flp")
        shutil.copy2(base, backup_path)
        print(f"  backup: {backup_path}")

    print("  closing any active project")
    activate_fl_studio()
    try:
        close_current_project()
    except AutodriveError as exc:
        print(f"  WARN close: {exc}")
    time.sleep(0.8)

    print(f"  opening in FL ({base})")
    open_flp(base, wait_seconds=wait_seconds)

    # Optional sanity check: round-trip a get_tempo so we know the script
    # is seeing this project (not a stale one). If FL didn't switch, the
    # tempo read could still succeed but reflect the wrong project — no
    # cheap way to guard that without a title or path lookup the API
    # doesn't expose. Best-effort only.
    inbox = default_inbox()
    inbox.write_command(Command(id=f"norm-probe-{base.stem}", kind="get_tempo"))
    try:
        res = inbox.wait_for_result(f"norm-probe-{base.stem}", timeout=20)
        if res.status == "ok":
            print(f"  tempo (as seen by FL): {res.detail} BPM")
        else:
            print(f"  tempo probe failed: {res}")
    except TimeoutError:
        print("  WARN: tempo probe timed out (FL may not have finished loading)")

    print("  saving via File → Save menu click")
    try:
        status = save_via_menu()
    except AutodriveError as exc:
        print(f"  FAIL: {exc}")
        return False
    print(f"  {status}")

    time.sleep(1.5)  # give FL a beat to flush to disk

    after_mtime = _mtime(base)
    after_size = base.stat().st_size
    changed = after_mtime != before_mtime or after_size != before_size
    print(f"  before: mtime={before_mtime:.0f} size={before_size}")
    print(f"  after:  mtime={after_mtime:.0f} size={after_size}  changed={changed}")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bases", nargs="+", type=Path, help="FLP files to normalize in place")
    parser.add_argument(
        "--backup",
        action="store_true",
        help="write <name>.pre_normalize.flp alongside each base before overwriting",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=5.0,
        help="seconds to wait between `open -a` and first command (default 5.0)",
    )
    args = parser.parse_args(argv)

    failures: list[Path] = []
    for base in args.bases:
        print(f"\n=== {base} ===")
        try:
            ok = normalize_one(base, backup=args.backup, wait_seconds=args.wait_seconds)
        except Exception as exc:
            print(f"  EXCEPTION: {type(exc).__name__}: {exc}")
            ok = False
        if not ok:
            failures.append(base)

    print()
    print(f"normalized: {len(args.bases) - len(failures)}/{len(args.bases)}")
    if failures:
        print("failures:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
