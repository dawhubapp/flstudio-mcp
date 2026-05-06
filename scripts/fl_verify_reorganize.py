"""Live-FL acceptance test for reorganize_project v3.

Pipeline:
  1. Copy a fixture to a scratch path.
  2. Run `offline_execute(reorganize_project)` against the copy.
  3. Open the result in FL Studio via re_harness.autodrive.
  4. Activate FL, press F5 to open the Playlist.
  5. Screencapture the full screen.
  6. Optionally zoom to fit (cmd-F via menu) and recapture.
  7. Save artifacts to runs/v3-fl-verify-<timestamp>/.

Run:
    uv run python -m scripts.fl_verify_reorganize \\
        --flp ../flpdiff/tests/corpus/local/h3_ys_64.flp

The script does NOT verify pixel-level — Roman eyeballs the artifacts
and confirms whether automation tracks visibly nest under their
instruments. Disruptive to whatever's on-screen, so warn before
running.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FLPDIFF_CLI = REPO_ROOT / "flpdiff" / "src" / "cli.ts"

sys.path.insert(0, str(REPO_ROOT / "mcp"))
sys.path.insert(0, str(REPO_ROOT / "python" / "tools"))

from re_harness.autodrive import open_flp  # noqa: E402
from tests.e2e.harness.client import open_session  # noqa: E402


def _runs_dir() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
    out = REPO_ROOT / "mcp" / "tests" / "e2e" / "runs" / f"v3-fl-verify-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def screencapture(out: Path, label: str) -> Path:
    """Grab the full screen via macOS screencapture."""
    target = out / f"{label}.png"
    subprocess.run(["screencapture", "-x", str(target)], check=True)
    return target


def applescript(code: str) -> subprocess.CompletedProcess:
    """Run AppleScript and return the result."""
    return subprocess.run(["osascript", "-e", code], capture_output=True, text=True, check=False)


def press_key(key: str) -> None:
    """Send a single key keystroke to the frontmost app."""
    code = f'tell application "System Events" to key code {key}'
    applescript(code)


def press_f5() -> None:
    """Send F5 — FL Studio's Playlist toggle. Key code 96 on macOS.

    Often unreliable on FL because it's a Wine-bridged Windows app and
    the keystroke routing depends on which native macOS app currently
    holds focus. Prefer ``open_view_playlist()`` which clicks the
    actual menu item via accessibility.
    """
    press_key("96")


def force_osxfl_frontmost() -> None:
    """Bring FL to the foreground via System Events.

    FL Studio on macOS runs as a Wine-bridged process named ``OsxFL``,
    NOT ``FL Studio`` — `tell application "FL Studio" to activate`
    times out because the bundle's display name doesn't match the
    process. Address it as ``OsxFL`` instead.
    """
    applescript('tell application "System Events" to tell process "OsxFL" to set frontmost to true')


def open_view_playlist() -> None:
    """Click View → Playlist via accessibility (more reliable than F5)."""
    applescript(
        'tell application "System Events" to tell process "OsxFL" to '
        'click menu item "Playlist" of menu "View" of menu bar 1'
    )


def menu_zoom_to_fit() -> None:
    """Try View → Zoom to fit (Shift+W). Best-effort."""
    code = 'tell application "System Events" to ' 'keystroke "W" using shift down'
    applescript(code)


async def reorganize_via_mcp(flp: Path) -> dict:
    """Spawn the MCP server, call offline_execute(reorganize_project)."""
    sess_dir = REPO_ROOT / "mcp" / ".tmp_v3_verify"
    sess_dir.mkdir(exist_ok=True)
    async with open_session(sess_dir / "sess") as ses:
        env = await ses.call_tool(
            "offline_execute",
            {"kind": "reorganize_project", "args": {"path": str(flp)}},
        )
    return env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--flp",
        type=Path,
        default=REPO_ROOT / "flpdiff" / "tests" / "corpus" / "local" / "h3_ys_64.flp",
        help="FLP fixture (default: h3_ys_64.flp — has 50 automation channels)",
    )
    parser.add_argument("--load-wait", type=float, default=15.0)
    args = parser.parse_args()

    if not args.flp.exists():
        print(f"missing: {args.flp}", file=sys.stderr)
        return 2

    out = _runs_dir()
    print(f"artifacts: {out}")

    # 1. Copy fixture to scratch
    scratch = out / args.flp.name
    shutil.copy2(args.flp, scratch)
    print(f"scratch: {scratch}")

    # 2. Reorganize via MCP
    print("running reorganize_project via MCP …")
    env = asyncio.run(reorganize_via_mcp(scratch))
    if not env.get("ok"):
        print(f"reorganize failed: {env}")
        return 1
    result = env["result"]
    n = result.get("mutations_applied")
    plan_tracks = len(result.get("plan", {}).get("tracks") or [])
    plan_moves = len(result.get("plan", {}).get("clipMoves") or [])
    print(f"  ok: {n} mutations, {plan_tracks} tracks, {plan_moves} clip moves")

    # 3. Open in FL Studio
    print(f"opening in FL Studio (waiting {args.load_wait}s for load) …")
    open_flp(scratch, wait_seconds=args.load_wait)

    # 4. Bring FL to the foreground. The standard
    # `tell application "FL Studio"` AppleScript address times out
    # because FL is Wine-bridged and registers as `OsxFL` instead.
    force_osxfl_frontmost()
    time.sleep(1.0)
    p1 = screencapture(out, "01-on-load")
    print(f"  {p1.name}")

    # 4b. Dismiss any blocking modal dialog (missing-plugins/samples
    # dialog is common on cross-machine FLPs). Enter clicks OK; do
    # this twice in case there are stacked prompts.
    for _ in range(3):
        applescript('tell application "System Events" to keystroke return')
        time.sleep(0.5)
    force_osxfl_frontmost()
    time.sleep(0.5)

    # 5. Open Playlist via View menu (F5 keystroke unreliable through
    # the macOS→Wine bridge — see open_view_playlist docstring).
    open_view_playlist()
    time.sleep(2.0)
    p2 = screencapture(out, "02-playlist")
    print(f"  {p2.name}")

    # 6. Try zoom-to-fit (Shift+W is "Set time range to all")
    menu_zoom_to_fit()
    time.sleep(1.0)
    p3 = screencapture(out, "03-after-zoom-to-fit")
    print(f"  {p3.name}")

    # 7. Plan dump as a forensic adjunct
    import json as _json

    (out / "plan.json").write_text(
        _json.dumps(result.get("plan", {}), indent=2, default=str), encoding="utf-8"
    )
    (out / "meta.txt").write_text(
        f"fixture: {args.flp}\n"
        f"scratch: {scratch}\n"
        f"mutations_applied: {n}\n"
        f"plan_tracks: {plan_tracks}\n"
        f"plan_clip_moves: {plan_moves}\n"
        f"flpdiff_cli: {FLPDIFF_CLI}\n",
        encoding="utf-8",
    )

    print(f"\nopen the dir to review: open {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
