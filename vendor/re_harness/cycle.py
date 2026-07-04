"""End-to-end RE cycle: open → modify → save → binary-diff (SPEC 1.2.4 seed).

This is the smallest working version of the Phase 1.2 discovery loop. It
takes one base FLP and one modification, runs them against FL Studio, and
tells you which raw-event opcodes moved in the binary. The loop is what
produces the evidence the event registry (1.2.5) then records.

Safety
------

Base FLPs in ``tests/corpus/re_base/fl25/`` are committed fixtures. This
script **never** overwrites them. Every run copies the base into a scratch
directory (default: ``tests/corpus/re_base/scratch/``, gitignored) and FL
Studio opens that copy. Save therefore only touches the scratch file.

You drive FL manually between steps because the harness can only script
inside a running FL instance; it can't currently ``File > Open`` from the
scripting API (``general.getCurrentFilename`` doesn't exist in FL 25, and
neither does a path-aware open). So: the script tells you which file to
open, waits for your confirmation, then runs the modification.

Example
-------

::

    python -m tools.re_harness.cycle \\
        --base tests/corpus/re_base/fl25/base_empty.flp \\
        --set-tempo 145.0
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

from .autodrive import (
    AutodriveError,
    activate_fl_studio,
    close_current_project,
    open_flp,
    reload_midi_script,
    save_via_menu,
)
from .binary_diff import diff, diff_by_opcode, read_flp
from .ipc import Command, default_inbox

logger = logging.getLogger("cycle")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SCRATCH = REPO_ROOT / "tests" / "corpus" / "re_base" / "scratch"


def snapshot(base: Path, scratch_dir: Path) -> Path:
    """Copy ``base`` into ``scratch_dir`` and return the new path.

    Scratch dir is gitignored; output paths are reused deterministically
    per base-file name so subsequent runs overwrite the last scratch.
    """
    scratch_dir.mkdir(parents=True, exist_ok=True)
    target = scratch_dir / base.name
    shutil.copy2(base, target)
    return target


def _confirm(prompt: str) -> None:
    """Block until the user presses Enter. Flushed so Ctrl-C during wait
    is visible."""
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        input()
    except (KeyboardInterrupt, EOFError):
        sys.exit(130)


def run_cycle(
    base: Path,
    bpm: float,
    scratch_dir: Path,
    *,
    auto: bool = False,
    reload_script: bool = False,
) -> None:
    scratch = snapshot(base, scratch_dir)
    inbox = default_inbox()

    # Per-run id suffix so previous runs' result files don't shadow new
    # commands with the same logical role (cycle-describe, cycle-set, etc.).
    # FL's script-side filter says "skip any cmd_* whose result_* already
    # exists", which would otherwise make the second run of the day a no-op.
    run_id = f"{int(time.time())}"

    print(f"\n=== Cycle: {base.name} → set_tempo({bpm}) ===")
    print(f"base     : {base}")
    print(f"scratch  : {scratch}")
    print(f"run_id   : {run_id}")
    print()

    if auto:
        if reload_script:
            print("Step 1a. Reloading MIDI script...")
            try:
                result = reload_midi_script()
                print(f"         {result}")
            except AutodriveError as exc:
                print(f"         WARN: reload failed: {exc}")
                print("         (Continuing — script may still be running from earlier.)")
            time.sleep(1.0)  # let OnInit settle
        print("Step 1b. Closing any currently-open project in FL...")
        activate_fl_studio()
        try:
            close_current_project()
        except AutodriveError as exc:
            print(f"         WARN: {exc}")
        time.sleep(0.8)
        print(f"Step 1c. Opening {scratch} in FL...")
        open_flp(scratch, wait_seconds=4.0)
    else:
        print("Step 1. In FL Studio, File → Open the scratch file above")
        print("        (NOT the committed base — the script won't guard that for you).")
        print(f"        Exact path:\n        {scratch}")
        _confirm("        Press Enter when FL has finished loading it ⏎ ")

    print("Step 2. Verifying handshake + reading current tempo...")
    describe_id = f"cycle-describe-{run_id}"
    inbox.write_command(Command(id=describe_id, kind="get_tempo"))
    before = inbox.wait_for_result(describe_id, timeout=15)
    if before.status != "ok":
        print(f"  FAIL: get_tempo returned {before}")
        sys.exit(1)
    print(f"  current tempo: {before.detail} BPM")

    print(f"Step 3. Sending set_tempo({bpm})...")
    inbox.write_command(
        Command(id=f"cycle-set-{run_id}", kind="set_tempo", args={"bpm": bpm}),
    )
    set_result = inbox.wait_for_result(f"cycle-set-{run_id}", timeout=15)
    if set_result.status != "ok":
        print(f"  FAIL: {set_result}")
        sys.exit(1)
    print(f"  {set_result.detail}")

    print("Step 4. Saving (File → Save menu click — script-side save is a no-op in FL 25)...")
    try:
        status = save_via_menu()
    except AutodriveError as exc:
        print(f"  FAIL: {exc}")
        sys.exit(1)
    print(f"  {status}")
    time.sleep(1.5)  # let FL flush to disk before we diff

    print("Step 5. Diffing base vs modified scratch...")
    d = diff(base, scratch)
    counts = d.summary_counts
    print(f"  [position diff] {counts}")
    if d.header_changes:
        print("  header changes:")
        for h in d.header_changes:
            print(f"    {h.field}: {h.a_value} → {h.b_value}")

    # Opcode-population diff — order-independent. FL's save re-layouts events
    # nondeterministically on each save, so the position diff cascades into
    # noise. The population diff groups by (opcode, payload) so only real
    # content changes surface.
    print("\n  [opcode-population diff] (order-independent)")
    _, a_ev = read_flp(base)
    _, b_ev = read_flp(scratch)
    deltas = diff_by_opcode(a_ev, b_ev)
    if not deltas:
        print("    no opcode-level changes — files are content-identical modulo event ordering.")
    else:
        print(f"    {len(deltas)} opcode(s) with content changes:")
        for od in deltas:
            print(
                f"      {od.opcode_hex}  count {od.a_count} → {od.b_count}  "
                f"uniq-only-in-base={len(set(od.payloads_only_in_a))} "
                f"uniq-only-in-scratch={len(set(od.payloads_only_in_b))}"
            )
            # Print a preview when the single-sided payloads are small.
            for label, blobs in (
                ("  removed", od.payloads_only_in_a),
                ("  added  ", od.payloads_only_in_b),
            ):
                seen: set[bytes] = set()
                for pl in blobs:
                    if pl in seen:
                        continue
                    seen.add(pl)
                    if len(seen) > 3:
                        print(f"      {label}: ... ({len(set(blobs)) - 3} more distinct)")
                        break
                    if len(pl) <= 32:
                        print(f"      {label}: {pl.hex()}")
                    else:
                        print(f"      {label}: [{len(pl)}B blob, starts {pl[:16].hex()}...]")

    print()
    print("Step 6. Close the scratch file in FL (File → Close) when done.")
    print("        The scratch file is disposable — next run will overwrite it.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True, help="base FLP path")
    parser.add_argument("--set-tempo", type=float, required=True, help="target BPM")
    parser.add_argument(
        "--scratch-dir",
        type=Path,
        default=DEFAULT_SCRATCH,
        help=f"scratch directory (default: {DEFAULT_SCRATCH.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="drive FL Studio automatically (open file via AppleScript) — skips manual prompt",
    )
    parser.add_argument(
        "--reload-script",
        action="store_true",
        help="(implies --auto) click FL's 'Reload script' button before the run",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not args.base.exists():
        print(f"base file missing: {args.base}", file=sys.stderr)
        return 2
    auto = args.auto or args.reload_script
    run_cycle(
        args.base,
        args.set_tempo,
        args.scratch_dir,
        auto=auto,
        reload_script=args.reload_script,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
