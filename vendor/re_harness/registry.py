"""Event registry: discovered FLP-event semantics (SPEC 1.2.5).

The registry is the durable output of the RE discovery loop. When the
runner applies a catalog modification, binary-diffs base vs modified, and
the diff points at opcode ``0xA3`` changing by 4 bytes, we record:

.. code-block:: json

    {
      "opcode": 163,
      "name": "ChannelVolume",
      "category": "channel",
      "data_type": "uint8",
      "value_range": [0, 128],
      "discovered_by": "channel_0_volume_50_to_80",
      "confidence": "confirmed",
      "pyflp_status": "supported",
      "fl_versions": ["20", "21", "24", "25"],
      "notes": ""
    }

Once an opcode hits ``confirmed`` confidence (at least N independent
modifications agree on its meaning), the Phase 1.2.6 step promotes it
from ``OpaqueBlob`` to a typed field in the canonical model.

Storage is JSON (``tools/re_harness/registry.json``) — readable by
humans, diffable in git, and loadable by downstream code without a YAML
dep.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

Confidence = Literal["confirmed", "probable", "uncertain"]
PyflpStatus = Literal["supported", "partial", "missing"]
DataType = Literal[
    "uint8",
    "int8",
    "uint16",
    "int16",
    "uint32",
    "int32",
    "float32",
    "utf16le",
    "ascii",
    "blob",
]
EventCategory = Literal[
    "metadata",
    "channel",
    "mixer",
    "pattern",
    "automation",
    "arrangement",
    "plugin",
    "unknown",
]

CONFIDENCES: tuple[Confidence, ...] = get_args(Confidence)
PYFLP_STATUSES: tuple[PyflpStatus, ...] = get_args(PyflpStatus)
DATA_TYPES: tuple[DataType, ...] = get_args(DataType)
EVENT_CATEGORIES: tuple[EventCategory, ...] = get_args(EventCategory)


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EventMapping:
    """Knowledge about one FLP opcode."""

    opcode: int
    name: str  # human name: ChannelVolume, InsertPan, ...
    category: EventCategory
    data_type: DataType
    value_range: tuple[float, float] | None = None
    discovered_by: tuple[str, ...] = ()  # modification ids that led to this mapping
    confidence: Confidence = "uncertain"
    pyflp_status: PyflpStatus = "missing"
    fl_versions: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if not 0 <= self.opcode <= 255:
            raise ValueError(f"opcode {self.opcode} out of range 0..255")


@dataclass(frozen=True, slots=True)
class Registry:
    """Collection of :class:`EventMapping`, keyed by opcode."""

    mappings: tuple[EventMapping, ...] = ()

    def __post_init__(self) -> None:
        seen: set[int] = set()
        for m in self.mappings:
            if m.opcode in seen:
                raise ValueError(f"duplicate opcode in registry: 0x{m.opcode:02x}")
            seen.add(m.opcode)

    def get(self, opcode: int) -> EventMapping | None:
        for m in self.mappings:
            if m.opcode == opcode:
                return m
        return None

    def by_category(self, category: EventCategory) -> tuple[EventMapping, ...]:
        return tuple(m for m in self.mappings if m.category == category)

    def by_confidence(self, confidence: Confidence) -> tuple[EventMapping, ...]:
        return tuple(m for m in self.mappings if m.confidence == confidence)

    def with_mapping(self, mapping: EventMapping) -> Registry:
        """Return a new registry with ``mapping`` inserted (or replacing the
        existing one with the same opcode).

        Registry is frozen; callers treat it as immutable. The discovery
        loop builds a new registry as it learns, then persists it.
        """
        replaced = False
        new_mappings: list[EventMapping] = []
        for existing in self.mappings:
            if existing.opcode == mapping.opcode:
                new_mappings.append(mapping)
                replaced = True
            else:
                new_mappings.append(existing)
        if not replaced:
            new_mappings.append(mapping)
        # Keep opcode-sorted for stable JSON output.
        new_mappings.sort(key=lambda m: m.opcode)
        return Registry(mappings=tuple(new_mappings))

    def stats(self) -> dict[str, int]:
        """Summary counts — useful in the phase-exit guardrail check."""
        return {
            "total": len(self.mappings),
            "confirmed": len(self.by_confidence("confirmed")),
            "probable": len(self.by_confidence("probable")),
            "uncertain": len(self.by_confidence("uncertain")),
        }


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class RegistryError(Exception):
    """Base for registry problems."""


class RegistryValidationError(RegistryError):
    """Raised when a registry JSON payload fails schema validation."""


# --------------------------------------------------------------------------- #
# (De)serialization
# --------------------------------------------------------------------------- #


def _validate_mapping(raw: object, index: int) -> EventMapping:
    if not isinstance(raw, dict):
        raise RegistryValidationError(
            f"mapping #{index}: expected object, got {type(raw).__name__}"
        )
    required = ("opcode", "name", "category", "data_type")
    for req in required:
        if req not in raw:
            raise RegistryValidationError(f"mapping #{index}: missing field {req!r}")

    opcode = raw["opcode"]
    if not isinstance(opcode, int):
        raise RegistryValidationError(f"mapping #{index}: opcode must be int")

    for key, allowed in (
        ("category", EVENT_CATEGORIES),
        ("data_type", DATA_TYPES),
    ):
        if raw[key] not in allowed:
            raise RegistryValidationError(
                f"mapping #{index} (opcode=0x{opcode:02x}): {key} {raw[key]!r} "
                f"not in {list(allowed)}"
            )

    confidence = raw.get("confidence", "uncertain")
    if confidence not in CONFIDENCES:
        raise RegistryValidationError(
            f"mapping 0x{opcode:02x}: confidence {confidence!r} not in {list(CONFIDENCES)}"
        )
    pyflp_status = raw.get("pyflp_status", "missing")
    if pyflp_status not in PYFLP_STATUSES:
        raise RegistryValidationError(
            f"mapping 0x{opcode:02x}: pyflp_status {pyflp_status!r} "
            f"not in {list(PYFLP_STATUSES)}"
        )

    value_range = raw.get("value_range")
    if value_range is not None:
        if not (isinstance(value_range, list) and len(value_range) == 2):
            raise RegistryValidationError(
                f"mapping 0x{opcode:02x}: value_range must be [lo, hi] or null"
            )
        lo, hi = value_range
        if not (isinstance(lo, (int, float)) and isinstance(hi, (int, float))):
            raise RegistryValidationError(
                f"mapping 0x{opcode:02x}: value_range elements must be numeric"
            )
        value_range = (float(lo), float(hi))

    try:
        return EventMapping(
            opcode=opcode,
            name=str(raw["name"]),
            category=raw["category"],
            data_type=raw["data_type"],
            value_range=value_range,
            discovered_by=tuple(str(x) for x in raw.get("discovered_by", ()) or ()),
            confidence=confidence,
            pyflp_status=pyflp_status,
            fl_versions=tuple(str(x) for x in raw.get("fl_versions", ()) or ()),
            notes=str(raw.get("notes", "")),
        )
    except ValueError as exc:
        raise RegistryValidationError(f"mapping #{index}: {exc}") from exc


def from_dict(raw: object) -> Registry:
    if not isinstance(raw, dict):
        raise RegistryValidationError(
            f"registry root must be an object, got {type(raw).__name__}"
        )
    mappings_raw = raw.get("mappings")
    if mappings_raw is None:
        mappings_raw = []
    if not isinstance(mappings_raw, list):
        raise RegistryValidationError("'mappings' must be a list")

    parsed = [_validate_mapping(m, i) for i, m in enumerate(mappings_raw)]
    try:
        return Registry(mappings=tuple(parsed))
    except ValueError as exc:  # duplicate opcode
        raise RegistryValidationError(str(exc)) from exc


def from_json(text: str) -> Registry:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RegistryValidationError(f"invalid JSON: {exc}") from exc
    return from_dict(raw)


def load(path: str | Path) -> Registry:
    """Load a registry JSON file. Missing file yields an empty registry —
    the discovery loop always starts fresh the first time."""
    path = Path(path)
    if not path.exists():
        return Registry()
    return from_json(path.read_text(encoding="utf-8"))


def to_dict(registry: Registry) -> dict[str, object]:
    return {
        "mappings": [
            {
                "opcode": m.opcode,
                "name": m.name,
                "category": m.category,
                "data_type": m.data_type,
                "value_range": None if m.value_range is None else list(m.value_range),
                "discovered_by": list(m.discovered_by),
                "confidence": m.confidence,
                "pyflp_status": m.pyflp_status,
                "fl_versions": list(m.fl_versions),
                "notes": m.notes,
            }
            for m in registry.mappings
        ]
    }


def to_json(registry: Registry, *, indent: int | None = 2) -> str:
    """Serialize to deterministic JSON (sorted opcodes + sorted keys)."""
    return json.dumps(to_dict(registry), indent=indent, sort_keys=True)


def save(registry: Registry, path: str | Path) -> None:
    Path(path).write_text(to_json(registry) + "\n", encoding="utf-8")


__all__ = [
    "CONFIDENCES",
    "DATA_TYPES",
    "EVENT_CATEGORIES",
    "PYFLP_STATUSES",
    "Confidence",
    "DataType",
    "EventCategory",
    "EventMapping",
    "PyflpStatus",
    "Registry",
    "RegistryError",
    "RegistryValidationError",
    "from_dict",
    "from_json",
    "load",
    "save",
    "to_dict",
    "to_json",
]
