"""Auto-sweep tool for reverse-engineering native FL plugin parameter layouts.

Takes a baseline FLP that has the target plugin instantiated at a known
mixer slot or channel, drives FL via the existing IPC harness to set
each parameter to a known value, saves after each set, parses the
resulting FLP, diffs the plugin's ``0xD5`` state blob byte-by-byte to
discover which bytes correspond to which parameter index.

Output is a TS layout snippet ready to paste into
``flpdiff/src/mutations/index.ts::PLUGIN_PARAM_LAYOUTS``.

Usage::

    python -m tools.re_harness.sweep_plugin_layout \\
        --baseline tests/corpus/local/proj_with_reeverb.flp \\
        --scope mixer --insert 5 --slot 0 \\
        --plugin-name "Fruity Reeverb 2"

The orchestrator script:

1. Calls ``open_flp`` to launch FL with the baseline.
2. Calls ``get_plugin_info`` over IPC to discover the plugin's parameter
   count + names.
3. For each ``paramIdx`` in ``[0, param_count)``:

   a. Sets ``paramIdx`` to a known low value (default 0.0) via
      ``plugins.setParamValue`` over IPC.
   b. Triggers ``save_via_menu`` and waits for FL to flush.
   c. Re-parses the FLP, extracts the plugin's ``0xD5`` blob, captures
      a fingerprint (byte hash + which bytes differ from baseline).
   d. Sets ``paramIdx`` to a known high value (default 1.0) and repeats.
   e. Compares low/high blobs: bytes that differ are the parameter's
      slot. Width determines field type (1 byte = u8, 2 consecutive
      bytes = u16 LE, 4 = u32 / float32 — check by extracting raw value
      and comparing to ``round(v * 0xFFFF)`` etc.).

4. Emits the discovered layout as a TS source snippet.

Run cost: ~0.5 seconds per parameter (set + save + parse), so a 36-param
plugin like EQ 2 takes ~30 seconds; a 10-param plugin like Fruity
Limiter takes ~10 seconds.

This tool drives FL and is interactive — Mac must be unlocked, FL must
have Accessibility perms, and the IPC bridge must be live (run
``verify_setup`` first).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .autodrive import open_flp, save_via_menu, wait_for_clean_fl
from .ipc import Command, default_inbox

NOTES_OPCODE = 0xD5  # plugin state event


@dataclass(frozen=True)
class ParamSweepResult:
    param_idx: int
    param_name: str | None
    low_blob: bytes
    high_blob: bytes
    differing_offsets: tuple[int, ...]


def _ipc_call(kind: str, args: dict, *, timeout: float = 10.0) -> dict:
    inbox = default_inbox()
    cmd = Command(id=f"{kind}-{int(time.time() * 1000)}", kind=kind, args=args)
    inbox.write_command(cmd)
    result = inbox.wait_for_result(cmd.id, timeout=timeout)
    if result is None:
        raise RuntimeError(f"IPC timeout for {kind}({args})")
    if result.status != "ok":
        raise RuntimeError(f"IPC {kind} returned {result.status}: {result.detail}")
    return result


def _read_plugin_state_blob(
    flp_path: Path, scope: str, insert_idx: int, slot_idx: int, channel_iid: int
) -> bytes:
    """Re-parse the FLP and extract the 0xD5 blob at the requested scope.

    Walks events directly to keep this script free of TS dependencies.
    Mirrors `findPluginStateEvent` from
    ``flpdiff/src/mutations/index.ts`` but in Python.
    """
    data = flp_path.read_bytes()
    # Skip FLhd (14 bytes) + FLdt header (8 bytes) = 22 bytes preamble
    # before the event stream.
    p = 22

    cur_insert = 0
    # FL doesn't emit a 0x62 NEW_SLOT for the FIRST plugin in an
    # insert — it's implicitly at slot 0. Default cur_slot to 0
    # and reset to 0 (not -1) on each 0x93 INSERT_END.
    cur_slot = 0
    in_mixer = False
    cur_channel = -1
    in_channel_scope = False
    OP_NEW_CHANNEL = 0x40
    OP_NEW_SLOT = 0x62
    OP_INSERT_END = 0x93
    OP_INSERT_FLAGS = 0xEC
    OP_PLUGIN_STATE = 0xD5

    while p < len(data):
        opcode = data[p]
        if opcode < 64:
            payload_size = 1
            payload_start = p + 1
            value = data[p + 1]
        elif opcode < 128:
            payload_size = 2
            payload_start = p + 1
            value = int.from_bytes(data[p + 1 : p + 3], "little")
        elif opcode < 192:
            payload_size = 4
            payload_start = p + 1
            value = int.from_bytes(data[p + 1 : p + 5], "little")
        else:
            # varint length
            q = p + 1
            length = 0
            shift = 0
            while True:
                byte = data[q]
                q += 1
                length |= (byte & 0x7F) << shift
                if byte & 0x80 == 0:
                    break
                shift += 7
            payload_start = q
            payload_size = length
            value = None
        event_end = payload_start + payload_size
        payload = data[payload_start:event_end]

        if scope == "channel":
            if opcode == OP_NEW_CHANNEL:
                cur_channel = value
                in_channel_scope = cur_channel == channel_iid
            elif opcode == OP_INSERT_END or opcode == OP_INSERT_FLAGS or opcode == OP_NEW_SLOT:
                in_channel_scope = False
            elif opcode == OP_PLUGIN_STATE and in_channel_scope:
                return bytes(payload)
        else:  # mixer
            if opcode == OP_INSERT_FLAGS:
                in_mixer = True
            elif opcode == OP_NEW_SLOT:
                in_mixer = True
                cur_slot = value
            elif in_mixer and opcode == OP_INSERT_END:
                cur_insert += 1
                cur_slot = 0
            elif (
                in_mixer
                and opcode == OP_PLUGIN_STATE
                and cur_insert == insert_idx
                and cur_slot == slot_idx
            ):
                return bytes(payload)

        p = event_end

    raise RuntimeError(
        f"no 0xD5 plugin state found at scope={scope} "
        f"(insert={insert_idx} slot={slot_idx} channel={channel_iid})"
    )


def _set_param_and_save(
    *,
    scope: str,
    index: int,
    slot: int,
    param: int,
    value: float,
    flp_path: Path,
) -> bytes:
    """Set one param via IPC, save FL, return the post-save 0xD5 blob.

    `slot` here is the FL IPC slot index. Walker uses file-side
    indexing (FL slot N → file slot N-1 for mixer scope).
    """
    _ipc_call(
        "set_plugin_param",
        {
            "scope": "mixer" if scope == "mixer" else "channel",
            "index": index,
            "slot": slot,
            "param": param,
            "value": value,
        },
    )
    # Give FL a beat to apply, then save.
    time.sleep(0.15)
    save_via_menu()
    # Wait for save to flush — empirically ~1s.
    time.sleep(1.2)

    file_slot = max(0, slot - 1) if scope == "mixer" else slot
    return _read_plugin_state_blob(
        flp_path,
        scope,
        insert_idx=index if scope == "mixer" else -1,
        slot_idx=file_slot,
        channel_iid=index if scope == "channel" else -1,
    )


def _classify_diff(low_blob: bytes, high_blob: bytes) -> ParamSweepResult | None:
    """Return offsets where the two blobs differ + suggested field type."""
    if len(low_blob) != len(high_blob):
        return None
    diffs = tuple(i for i in range(len(low_blob)) if low_blob[i] != high_blob[i])
    return ParamSweepResult(
        param_idx=-1,
        param_name=None,
        low_blob=low_blob,
        high_blob=high_blob,
        differing_offsets=diffs,
    )


def _emit_layout_ts(
    *,
    plugin_name: str,
    blob_size: int,
    sweeps: list[ParamSweepResult],
) -> str:
    """Emit a TS layout snippet for `PLUGIN_PARAM_LAYOUTS`."""
    lines: list[str] = []
    lines.append(f"// Auto-generated by sweep_plugin_layout.py — review before commit.")
    lines.append(f"const {_layout_const_name(plugin_name)}: PluginLayout = {{")
    lines.append(f"  minSize: {blob_size},")
    lines.append(f"  maxSize: {blob_size + 50},")
    lines.append(f"  paramRefToOffset: (ref) => {{")
    lines.append(f'    if (ref.kind !== "param") return null;')
    for s in sweeps:
        if not s.differing_offsets:
            continue
        offsets = list(s.differing_offsets)
        if len(offsets) == 1:
            field = "u8"
            offset = offsets[0]
        elif len(offsets) == 2 and offsets[1] == offsets[0] + 1:
            field = "u16"
            offset = offsets[0]
        elif len(offsets) == 4 and all(offsets[i] == offsets[0] + i for i in range(4)):
            # could be u32 or float32 — emit as u32 for now; caller may
            # adjust fieldType to float32 if value-scaling check confirms.
            field = "u32-or-f32"
            offset = offsets[0]
        else:
            field = "complex"
            offset = offsets[0]
        comment = f" // {s.param_name}" if s.param_name else ""
        lines.append(
            f'    if (ref.index === {s.param_idx}) return '
            f'{{ offset: 0x{offset:02x}, fieldType: "{field}" }};{comment}'
        )
    lines.append(f"    return null;")
    lines.append(f"  }},")
    lines.append(f"}};")
    lines.append("")
    lines.append(f"PLUGIN_PARAM_LAYOUTS[\"{plugin_name}\"] = {_layout_const_name(plugin_name)};")
    return "\n".join(lines)


def _layout_const_name(plugin_name: str) -> str:
    """Generate a TS const name from a plugin name."""
    cleaned = "".join(c if c.isalnum() else "_" for c in plugin_name).strip("_")
    return cleaned.upper() + "_LAYOUT"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--scope", choices=["mixer", "channel"], required=True)
    ap.add_argument("--insert", type=int, default=-1, help="for scope=mixer")
    ap.add_argument("--slot", type=int, default=-1, help="for scope=mixer")
    ap.add_argument("--channel-iid", type=int, default=-1, help="for scope=channel")
    ap.add_argument("--plugin-name", required=True)
    ap.add_argument("--low", type=float, default=0.0)
    ap.add_argument("--high", type=float, default=1.0)
    ap.add_argument("--max-params", type=int, default=80, help="cap to avoid runaways")
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help="write TS snippet here; otherwise stdout",
    )
    args = ap.parse_args(argv)

    if args.scope == "mixer" and (args.insert < 0 or args.slot < 0):
        ap.error("--insert and --slot required for scope=mixer")
    if args.scope == "channel" and args.channel_iid < 0:
        ap.error("--channel-iid required for scope=channel")

    print(f"[sweep] launching FL with {args.baseline}", file=sys.stderr)
    open_flp(args.baseline, wait_seconds=15.0)
    time.sleep(2.0)

    print("[sweep] waiting for FL to reach clean state (dismiss modals)…", file=sys.stderr)
    # After 5 dismiss-only attempts, force-restart FL with the baseline
    # FLP — most reliable IPC recovery (D-32). Total budget: 10 attempts.
    if not wait_for_clean_fl(
        max_attempts=10,
        dismiss_per_attempt=3,
        retry_delay=2.5,
        restart_after=5,
        restart_flp=args.baseline,
    ):
        print(
            "[sweep] FATAL: IPC never came up after 10 dismiss attempts. "
            "FL likely has a PACE plugin-license fatal error or the MIDI "
            "script isn't running. Check FL → Options → MIDI Settings → "
            "IAC input controller type = 'flstudio-mcp'.",
            file=sys.stderr,
        )
        return 3

    # Discover param count + names.
    plug_index = args.insert if args.scope == "mixer" else args.channel_iid
    info_args = {
        "scope": args.scope if args.scope == "channel" else "mixer",
        "index": plug_index,
        "slot": args.slot,
    }
    print(f"[sweep] querying plugin info: {info_args}", file=sys.stderr)
    info_result = _ipc_call("get_plugin_info", info_args)
    info = json.loads(info_result.detail)
    print(
        f"[sweep] plugin: {info.get('name')!r} param_count={info.get('param_count')}",
        file=sys.stderr,
    )
    param_count = info.get("param_count")
    if not isinstance(param_count, int):
        print(f"[sweep] FATAL: cannot read param_count: {param_count}", file=sys.stderr)
        return 2
    param_names = info.get("param_names") or []
    param_count = min(param_count, args.max_params)

    # File slot index is FL IPC slot index minus 1: events between
    # `0x62 N` and `0x62 N+1` in the file appear at FL slot N+1 via
    # `plugins.getPluginName`. Walker uses file-side numbering, so
    # subtract 1 from the user-supplied (FL-side) slot index.
    file_slot = max(0, args.slot - 1) if args.scope == "mixer" else args.slot

    # Capture baseline blob (whatever the file currently has).
    print(
        f"[sweep] capturing baseline blob (FL slot {args.slot} → file slot {file_slot})…",
        file=sys.stderr,
    )
    baseline_blob = _read_plugin_state_blob(
        args.baseline,
        args.scope,
        args.insert,
        file_slot,
        args.channel_iid,
    )
    blob_size = len(baseline_blob)
    print(f"[sweep] baseline blob size: {blob_size} bytes", file=sys.stderr)

    sweeps: list[ParamSweepResult] = []
    for idx in range(param_count):
        name = param_names[idx] if idx < len(param_names) else None
        print(f"[sweep] param {idx} ({name})…", file=sys.stderr, flush=True)
        try:
            low = _set_param_and_save(
                scope=args.scope,
                index=plug_index,
                slot=args.slot,
                param=idx,
                value=args.low,
                flp_path=args.baseline,
            )
            high = _set_param_and_save(
                scope=args.scope,
                index=plug_index,
                slot=args.slot,
                param=idx,
                value=args.high,
                flp_path=args.baseline,
            )
        except Exception as exc:
            print(f"[sweep]   ERROR: {exc}", file=sys.stderr)
            continue
        result = _classify_diff(low, high)
        if result is None:
            print(
                f"[sweep]   WARN: blob size changed for param {idx}, skipping",
                file=sys.stderr,
            )
            continue
        result = ParamSweepResult(
            param_idx=idx,
            param_name=name,
            low_blob=result.low_blob,
            high_blob=result.high_blob,
            differing_offsets=result.differing_offsets,
        )
        sweeps.append(result)
        if result.differing_offsets:
            print(
                f"[sweep]   diff at offsets {[hex(o) for o in result.differing_offsets]}",
                file=sys.stderr,
            )
        else:
            print(f"[sweep]   no diff (param may be no-op or saturated)", file=sys.stderr)

    snippet = _emit_layout_ts(
        plugin_name=args.plugin_name, blob_size=blob_size, sweeps=sweeps
    )
    if args.output:
        args.output.write_text(snippet)
        print(f"[sweep] wrote {args.output}", file=sys.stderr)
    else:
        print(snippet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
