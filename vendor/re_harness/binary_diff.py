"""Raw event-level binary diff for `.flp` files (SPEC 1.2.3).

This runs *below* PyFLP's abstraction layer. The point is to see the bytes
PyFLP doesn't (yet) interpret: unknown opcodes, new-in-FL25 fields, plugin
state chunks. The RE discovery loop feeds these diffs into the event
registry (1.2.5) and plugin parsers (1.2.8).

FLP binary format (short version)
---------------------------------

::

    0..3    b"FLhd"                (header magic)
    4..7    uint32 LE = 6           (header size, always 6)
    8..9    int16  LE               (format: 0=project, 1=pattern, 2=score)
    10..11  uint16 LE               (channel count — historical, not reliable)
    12..13  uint16 LE               (PPQ)
    14..17  b"FLdt"                (data magic)
    18..21  uint32 LE               (events_size: payload bytes following)
    22..    event stream

Every event is ``opcode (uint8) + payload`` where the opcode range dictates
the payload encoding:

+---------------+----------------------+-----------------------------+
| Opcode range  | Payload encoding     | Name                        |
+===============+======================+=============================+
| 0..63         | 1 byte               | BYTE                        |
+---------------+----------------------+-----------------------------+
| 64..127       | 2 bytes  (uint16 LE) | WORD                        |
+---------------+----------------------+-----------------------------+
| 128..191      | 4 bytes  (uint32 LE) | DWORD                       |
+---------------+----------------------+-----------------------------+
| 192..255      | VarInt size + bytes  | TEXT/DATA (variable-length) |
+---------------+----------------------+-----------------------------+

``VarInt`` follows protobuf's base-128 LEB128 encoding: each byte carries 7
bits of the integer, high-bit set = "more bytes follow", cleared = final.

What this module produces
-------------------------

A :class:`BinaryDiff` with four categories of change, each paired with the
exact byte offsets so a human can inspect the raw file:

* ``added`` — events present in *B* but not *A*, aligned by stream position.
* ``removed`` — events in *A* but not *B*.
* ``modified`` — same opcode at the same stream position with different
  payload bytes.
* ``unchanged`` — count only (for ratio reporting).

Event alignment is position-based: iterate both streams in lockstep and
compare opcode + payload. When streams diverge in length, everything beyond
the shorter one is ``added`` or ``removed``.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal

# --------------------------------------------------------------------------- #
# Opcode range boundaries (matches PyFLP's _events.py).
# --------------------------------------------------------------------------- #

BYTE_MAX = 64
WORD_MAX = 128
DWORD_MAX = 192
# DATA_MAX is 256 (opcodes are uint8)

HEADER_MAGIC = b"FLhd"
DATA_MAGIC = b"FLdt"
HEADER_BYTES = 14  # FLhd..ppq
PREFIX_BYTES = 22  # header + FLdt + events_size field


class BinaryDiffError(Exception):
    """Base class for binary-diff-specific failures."""


class MalformedFLPError(BinaryDiffError):
    """File is not a recognizable FLP at the byte level."""


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FLPHeader:
    """Fixed-size preamble."""

    format: int
    channel_count: int
    ppq: int
    events_size: int  # size in bytes of the events stream


@dataclass(frozen=True, slots=True)
class RawEvent:
    """One event pulled from an FLP stream.

    ``offset`` is the byte position of the opcode byte inside the full file.
    ``payload`` is just the data bytes — the opcode byte and any length
    prefix are not included.
    """

    offset: int
    opcode: int
    payload: bytes

    @property
    def category(self) -> Literal["BYTE", "WORD", "DWORD", "DATA"]:
        if self.opcode < BYTE_MAX:
            return "BYTE"
        if self.opcode < WORD_MAX:
            return "WORD"
        if self.opcode < DWORD_MAX:
            return "DWORD"
        return "DATA"

    @property
    def size(self) -> int:
        return len(self.payload)


ChangeKind = Literal["added", "removed", "modified"]


@dataclass(frozen=True, slots=True)
class EventChange:
    kind: ChangeKind
    position: int  # logical event index (0..N) in the stream
    opcode: int
    opcode_hex: str
    a_offset: int | None  # byte offset in file A (None for added)
    b_offset: int | None  # byte offset in file B (None for removed)
    a_payload: bytes | None
    b_payload: bytes | None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "position": self.position,
            "opcode": self.opcode,
            "opcode_hex": self.opcode_hex,
            "a_offset": self.a_offset,
            "b_offset": self.b_offset,
            "a_size": None if self.a_payload is None else len(self.a_payload),
            "b_size": None if self.b_payload is None else len(self.b_payload),
            "a_payload_hex": None if self.a_payload is None else self.a_payload.hex(),
            "b_payload_hex": None if self.b_payload is None else self.b_payload.hex(),
        }


@dataclass(frozen=True, slots=True)
class HeaderChange:
    """Header-level difference (format, channel_count, ppq, events_size)."""

    field: str
    a_value: int
    b_value: int


@dataclass(frozen=True, slots=True)
class BinaryDiff:
    """Full diff report between two FLP files."""

    a_path: str
    b_path: str
    a_header: FLPHeader
    b_header: FLPHeader
    header_changes: tuple[HeaderChange, ...]
    event_changes: tuple[EventChange, ...]
    unchanged_count: int

    @property
    def summary_counts(self) -> dict[str, int]:
        counts = {"added": 0, "removed": 0, "modified": 0, "unchanged": self.unchanged_count}
        for c in self.event_changes:
            counts[c.kind] += 1
        return counts

    def to_json(self, *, indent: int | None = 2) -> str:
        obj = {
            "a_path": self.a_path,
            "b_path": self.b_path,
            "a_header": dataclasses.asdict(self.a_header),
            "b_header": dataclasses.asdict(self.b_header),
            "header_changes": [dataclasses.asdict(c) for c in self.header_changes],
            "event_changes": [c.to_dict() for c in self.event_changes],
            "summary": self.summary_counts,
        }
        return json.dumps(obj, indent=indent, sort_keys=True)


# --------------------------------------------------------------------------- #
# Low-level stream primitives
# --------------------------------------------------------------------------- #


def _read_varint(stream: BinaryIO) -> int:
    """Decode a protobuf-style base-128 VarInt from ``stream``.

    Raises ``MalformedFLPError`` on truncation.
    """
    shift = 0
    result = 0
    for _ in range(10):  # 64 bits / 7 = ~10 bytes max
        chunk = stream.read(1)
        if not chunk:
            raise MalformedFLPError("truncated VarInt")
        byte = chunk[0]
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result
        shift += 7
    raise MalformedFLPError("VarInt too long")


def _read_exact(stream: BinaryIO, n: int, *, what: str) -> bytes:
    data = stream.read(n)
    if len(data) != n:
        raise MalformedFLPError(f"truncated while reading {what}: got {len(data)}/{n}")
    return data


def parse_header(stream: BinaryIO) -> FLPHeader:
    """Parse the 22-byte FLhd+FLdt preamble and leave the stream at offset 22."""
    if _read_exact(stream, 4, what="FLhd magic") != HEADER_MAGIC:
        raise MalformedFLPError("bad FLhd magic")

    header_size = int.from_bytes(_read_exact(stream, 4, what="header size"), "little")
    if header_size != 6:
        raise MalformedFLPError(f"unexpected FLhd size {header_size}, want 6")

    fmt = int.from_bytes(_read_exact(stream, 2, what="format"), "little", signed=True)
    channel_count = int.from_bytes(_read_exact(stream, 2, what="channel count"), "little")
    ppq = int.from_bytes(_read_exact(stream, 2, what="ppq"), "little")

    if _read_exact(stream, 4, what="FLdt magic") != DATA_MAGIC:
        raise MalformedFLPError("bad FLdt magic")

    events_size = int.from_bytes(_read_exact(stream, 4, what="events size"), "little")
    return FLPHeader(
        format=fmt,
        channel_count=channel_count,
        ppq=ppq,
        events_size=events_size,
    )


def iter_events(stream: BinaryIO, *, start_offset: int = PREFIX_BYTES) -> list[RawEvent]:
    """Consume the event stream and return every event.

    ``start_offset`` is the byte offset where events begin in the source
    file (default 22, i.e., right after the FLdt header). Stops when the
    stream reaches EOF — does not validate ``events_size`` here, that's a
    higher-level check.
    """
    events: list[RawEvent] = []
    while True:
        at = start_offset + sum(1 + len(e.payload) + _length_prefix_size(e) for e in events)
        opcode_byte = stream.read(1)
        if not opcode_byte:
            return events
        opcode = opcode_byte[0]
        if opcode < BYTE_MAX:
            payload = _read_exact(stream, 1, what=f"BYTE payload @ 0x{at:x}")
        elif opcode < WORD_MAX:
            payload = _read_exact(stream, 2, what=f"WORD payload @ 0x{at:x}")
        elif opcode < DWORD_MAX:
            payload = _read_exact(stream, 4, what=f"DWORD payload @ 0x{at:x}")
        else:
            size = _read_varint(stream)
            payload = _read_exact(stream, size, what=f"DATA payload ({size}B) @ 0x{at:x}")
        events.append(RawEvent(offset=at, opcode=opcode, payload=payload))


def _length_prefix_size(event: RawEvent) -> int:
    """For DATA events, the size-VarInt occupies some bytes between opcode
    and payload. We need to know that when re-computing offsets."""
    if event.category != "DATA":
        return 0
    size = len(event.payload)
    if size == 0:
        return 1
    # Number of base-128 digits = ceil(bits/7); minimum 1.
    bits = size.bit_length()
    return max(1, (bits + 6) // 7)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def read_flp(path: str | Path) -> tuple[FLPHeader, list[RawEvent]]:
    """Read an FLP's header and event stream."""
    path = Path(path)
    with path.open("rb") as f:
        header = parse_header(f)
        events = iter_events(f)
    return header, events


def diff(a_path: str | Path, b_path: str | Path) -> BinaryDiff:
    """Return a :class:`BinaryDiff` comparing two FLP files at event level."""
    a_header, a_events = read_flp(a_path)
    b_header, b_events = read_flp(b_path)

    header_changes = _diff_headers(a_header, b_header)
    event_changes, unchanged = _diff_events(a_events, b_events)

    return BinaryDiff(
        a_path=str(a_path),
        b_path=str(b_path),
        a_header=a_header,
        b_header=b_header,
        header_changes=tuple(header_changes),
        event_changes=tuple(event_changes),
        unchanged_count=unchanged,
    )


def _diff_headers(a: FLPHeader, b: FLPHeader) -> list[HeaderChange]:
    out: list[HeaderChange] = []
    for f in dataclasses.fields(FLPHeader):
        av = getattr(a, f.name)
        bv = getattr(b, f.name)
        if av != bv:
            out.append(HeaderChange(field=f.name, a_value=av, b_value=bv))
    return out


def _diff_events(
    a: list[RawEvent], b: list[RawEvent]
) -> tuple[list[EventChange], int]:
    """Position-aligned diff.

    We compare events at equal stream positions (index 0 of A vs index 0 of
    B, etc.). When both files share a common prefix of opcodes but differ on
    payload, those are ``modified``. When opcodes disagree at a given
    position we emit ``removed`` (from A) and ``added`` (from B) at that
    position. When one stream ends early, the tail of the other becomes
    ``added`` / ``removed``.

    This alignment is intentionally naive — it works well when the
    modification only changed field values without reordering events (the
    common case for the RE harness). A more sophisticated Myers-style diff
    can be layered on top later if we find real cases where event order
    shifts.
    """
    changes: list[EventChange] = []
    unchanged = 0
    n = min(len(a), len(b))
    for i in range(n):
        ea, eb = a[i], b[i]
        if ea.opcode == eb.opcode and ea.payload == eb.payload:
            unchanged += 1
            continue
        if ea.opcode == eb.opcode:
            changes.append(
                EventChange(
                    kind="modified",
                    position=i,
                    opcode=ea.opcode,
                    opcode_hex=f"0x{ea.opcode:02x}",
                    a_offset=ea.offset,
                    b_offset=eb.offset,
                    a_payload=ea.payload,
                    b_payload=eb.payload,
                )
            )
        else:
            changes.append(
                EventChange(
                    kind="removed",
                    position=i,
                    opcode=ea.opcode,
                    opcode_hex=f"0x{ea.opcode:02x}",
                    a_offset=ea.offset,
                    b_offset=None,
                    a_payload=ea.payload,
                    b_payload=None,
                )
            )
            changes.append(
                EventChange(
                    kind="added",
                    position=i,
                    opcode=eb.opcode,
                    opcode_hex=f"0x{eb.opcode:02x}",
                    a_offset=None,
                    b_offset=eb.offset,
                    a_payload=None,
                    b_payload=eb.payload,
                )
            )
    # Tail of whichever stream is longer.
    for i in range(n, len(a)):
        ea = a[i]
        changes.append(
            EventChange(
                kind="removed",
                position=i,
                opcode=ea.opcode,
                opcode_hex=f"0x{ea.opcode:02x}",
                a_offset=ea.offset,
                b_offset=None,
                a_payload=ea.payload,
                b_payload=None,
            )
        )
    for i in range(n, len(b)):
        eb = b[i]
        changes.append(
            EventChange(
                kind="added",
                position=i,
                opcode=eb.opcode,
                opcode_hex=f"0x{eb.opcode:02x}",
                a_offset=None,
                b_offset=eb.offset,
                a_payload=None,
                b_payload=eb.payload,
            )
        )
    return changes, unchanged


# --------------------------------------------------------------------------- #
# Opcode-population diff — order-independent.
# --------------------------------------------------------------------------- #
#
# Problem the position-based diff hits on FL-saved files: each save can
# re-layout events, making the position-aligned comparison cascade after the
# first realignment. For the RE harness we actually care about "which opcodes'
# content shifted", not where they live in the stream. So: bucket events by
# (opcode, payload) and compute the multiset difference. Order falls out.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class OpcodeDelta:
    """A single opcode's net change between two files.

    Reports the count of (opcode, payload) tuples in each file. Payloads
    are summarized, not listed exhaustively, so the output stays readable
    even when a single opcode has many instances.
    """

    opcode: int
    a_count: int  # total events with this opcode in file A
    b_count: int
    payloads_only_in_a: tuple[bytes, ...]  # distinct payloads present in A but not B
    payloads_only_in_b: tuple[bytes, ...]

    @property
    def opcode_hex(self) -> str:
        return f"0x{self.opcode:02x}"


def diff_by_opcode(
    a_events: list[RawEvent],
    b_events: list[RawEvent],
) -> list[OpcodeDelta]:
    """Multiset diff grouped by opcode.

    Returns an :class:`OpcodeDelta` for every opcode whose population of
    ``(opcode, payload)`` tuples differs between the two files. Order in
    the output is ascending opcode.
    """
    from collections import Counter

    a_keys: Counter[tuple[int, bytes]] = Counter((e.opcode, e.payload) for e in a_events)
    b_keys: Counter[tuple[int, bytes]] = Counter((e.opcode, e.payload) for e in b_events)

    a_opcodes: Counter[int] = Counter(e.opcode for e in a_events)
    b_opcodes: Counter[int] = Counter(e.opcode for e in b_events)

    opcodes = sorted(set(a_opcodes) | set(b_opcodes))
    deltas: list[OpcodeDelta] = []
    for op in opcodes:
        # Events under this opcode: payloads present in A not B, and vice versa.
        a_payloads = Counter(
            pl for (o, pl), c in a_keys.items() if o == op for _ in range(c)
        )
        b_payloads = Counter(
            pl for (o, pl), c in b_keys.items() if o == op for _ in range(c)
        )
        only_a = a_payloads - b_payloads
        only_b = b_payloads - a_payloads
        if a_opcodes[op] == b_opcodes[op] and not only_a and not only_b:
            continue
        deltas.append(
            OpcodeDelta(
                opcode=op,
                a_count=a_opcodes[op],
                b_count=b_opcodes[op],
                payloads_only_in_a=tuple(only_a.elements()),
                payloads_only_in_b=tuple(only_b.elements()),
            )
        )
    return deltas


__all__ = [
    "BinaryDiff",
    "BinaryDiffError",
    "EventChange",
    "FLPHeader",
    "HeaderChange",
    "MalformedFLPError",
    "OpcodeDelta",
    "RawEvent",
    "diff",
    "diff_by_opcode",
    "iter_events",
    "parse_header",
    "read_flp",
]


# --------------------------------------------------------------------------- #
# CLI entry-point: `python -m tools.re_harness.binary_diff a.flp b.flp`
# --------------------------------------------------------------------------- #


def _main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Raw event-level diff for two FLP files.")
    p.add_argument("a", help="first FLP file")
    p.add_argument("b", help="second FLP file")
    p.add_argument("--json", action="store_true", help="emit JSON report")
    p.add_argument("--limit", type=int, default=20, help="max events to list in text mode")
    args = p.parse_args(argv)

    result = diff(args.a, args.b)
    if args.json:
        print(result.to_json())
        return 0

    summary = result.summary_counts
    print(f"A: {args.a}")
    print(f"B: {args.b}")
    print(f"events: {summary}")
    if result.header_changes:
        print("header:")
        for h in result.header_changes:
            print(f"  {h.field}: {h.a_value} -> {h.b_value}")
    for c in result.event_changes[: args.limit]:
        a_size = "-" if c.a_payload is None else str(len(c.a_payload))
        b_size = "-" if c.b_payload is None else str(len(c.b_payload))
        print(f"  #{c.position:>4} {c.kind:<8} {c.opcode_hex}  sizes {a_size}->{b_size}")
    if len(result.event_changes) > args.limit:
        print(f"  ... {len(result.event_changes) - args.limit} more (use --json for full)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
