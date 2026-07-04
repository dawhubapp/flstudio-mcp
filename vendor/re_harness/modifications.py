"""Modification catalog schema + loader (SPEC 1.2.2).

Each entry describes an atomic change the RE harness will make to a base
FL Studio project: "raise channel 0 volume from 50% to 80%", "rename the
master insert to 'X'", "add a C5 note at beat 1". The runner (1.2.4)
consumes this catalog and drives FL Studio to produce the modified FLP,
then binary-diffs base vs modified to isolate which bytes encode which
semantics.

The catalog lives in ``tools/re_harness/modifications.yaml`` so it's
human-editable and diffable. This module defines the schema and provides
a validating loader.

Categories
----------

Declared in :data:`CATEGORIES`, ordered by musical priority so the
discovery loop naturally covers high-value territory first. The runner
may filter by category to run a targeted sweep (e.g., "just plugin
params for the Serum preset change work").

Methods
-------

* ``midi_script`` — FL Studio's built-in MIDI-scripting API drives the
  change. Preferred for anything the scripting API can reach.
* ``ui_automation`` — :mod:`pyautogui` fallback for UI-only knobs. More
  brittle; used only when MIDI scripting can't hit the target.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

import yaml

# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #

Category = Literal[
    "metadata",
    "channel",
    "mixer",
    "pattern",
    "automation",
    "arrangement",
    "plugin",
    "fl_version_specific",
]

Method = Literal["midi_script", "ui_automation"]

CATEGORIES: tuple[Category, ...] = get_args(Category)
METHODS: tuple[Method, ...] = get_args(Method)


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Modification:
    """One atomic modification in the RE catalog.

    Attributes
    ----------
    id
        Stable, snake_case identifier. Used as the output filename
        suffix (``mod_<id>.flp``) and as the primary key of discovery
        results in the registry.
    category
        High-level grouping from :data:`CATEGORIES`.
    description
        Human-readable sentence. Shows up in reports.
    method
        How the harness executes this change (``midi_script`` or
        ``ui_automation``).
    base_flp
        Path (relative to the repo root) to the base FLP on top of
        which the modification is applied. Usually one of the
        ``tests/corpus/re_base/fl25/*.flp`` fixtures.
    script_commands
        Ordered list of commands sent to the MIDI script / UI driver.
        Shape is method-specific; validation only enforces that the list
        is non-empty for ``midi_script`` method.
    expected_change
        Free-text description of what bytes we *expect* to see move in
        the binary diff. Useful for cross-checking surprising results.
    fl_versions
        Empty tuple means "all versions"; otherwise a list like
        ``("25",)`` for FL25-specific events.
    tags
        Free-form labels for grouping (``"priority:p1"``, ``"vst:serum"``).
    """

    id: str
    category: Category
    description: str
    method: Method
    base_flp: str
    script_commands: tuple[str, ...] = ()
    expected_change: str = ""
    fl_versions: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Catalog:
    """The whole modification catalog."""

    modifications: tuple[Modification, ...]

    def by_category(self, category: Category) -> tuple[Modification, ...]:
        return tuple(m for m in self.modifications if m.category == category)

    def by_id(self, mod_id: str) -> Modification:
        for m in self.modifications:
            if m.id == mod_id:
                return m
        raise KeyError(mod_id)

    def ids(self) -> tuple[str, ...]:
        return tuple(m.id for m in self.modifications)


# --------------------------------------------------------------------------- #
# Validation errors
# --------------------------------------------------------------------------- #


class CatalogError(Exception):
    """Base for catalog-loading problems."""


class CatalogValidationError(CatalogError):
    """Raised when the on-disk YAML fails schema validation."""


# --------------------------------------------------------------------------- #
# Loader
# --------------------------------------------------------------------------- #


_REQUIRED_FIELDS = ("id", "category", "description", "method", "base_flp")
# fields that must be strings if provided
_STRING_FIELDS = ("id", "description", "method", "base_flp", "expected_change")


def _validate_entry(raw: dict, index: int) -> Modification:
    if not isinstance(raw, dict):
        raise CatalogValidationError(
            f"modification #{index}: expected mapping, got {type(raw).__name__}"
        )
    for req in _REQUIRED_FIELDS:
        if req not in raw:
            raise CatalogValidationError(f"modification #{index}: missing field '{req}'")
    for f in _STRING_FIELDS:
        if f in raw and not isinstance(raw[f], str):
            raise CatalogValidationError(
                f"modification #{index} ({raw.get('id', '?')}): "
                f"'{f}' must be a string, got {type(raw[f]).__name__}"
            )

    category = raw["category"]
    if category not in CATEGORIES:
        raise CatalogValidationError(
            f"modification {raw['id']!r}: unknown category {category!r}. "
            f"Must be one of {list(CATEGORIES)}"
        )
    method = raw["method"]
    if method not in METHODS:
        raise CatalogValidationError(
            f"modification {raw['id']!r}: unknown method {method!r}. "
            f"Must be one of {list(METHODS)}"
        )

    script_commands = tuple(raw.get("script_commands", ()) or ())
    for i, cmd in enumerate(script_commands):
        if not isinstance(cmd, str):
            raise CatalogValidationError(
                f"modification {raw['id']!r} script_commands[{i}] must be a string"
            )
    if method == "midi_script" and not script_commands:
        raise CatalogValidationError(
            f"modification {raw['id']!r}: midi_script method requires at least one "
            "script_commands entry"
        )

    fl_versions = tuple(str(v) for v in (raw.get("fl_versions") or ()))
    tags = tuple(str(t) for t in (raw.get("tags") or ()))

    return Modification(
        id=raw["id"],
        category=category,
        description=raw["description"],
        method=method,
        base_flp=raw["base_flp"],
        script_commands=script_commands,
        expected_change=raw.get("expected_change", ""),
        fl_versions=fl_versions,
        tags=tags,
    )


def _validate_catalog(raw: object) -> Catalog:
    if not isinstance(raw, dict):
        raise CatalogValidationError(
            f"catalog root must be a mapping, got {type(raw).__name__}"
        )
    mods_raw = raw.get("modifications")
    if not isinstance(mods_raw, list):
        raise CatalogValidationError("catalog must contain a 'modifications:' list")
    if not mods_raw:
        raise CatalogValidationError("'modifications:' list must be non-empty")

    seen: set[str] = set()
    parsed: list[Modification] = []
    for i, entry in enumerate(mods_raw):
        m = _validate_entry(entry, i)
        if m.id in seen:
            raise CatalogValidationError(f"duplicate modification id: {m.id!r}")
        seen.add(m.id)
        parsed.append(m)
    return Catalog(modifications=tuple(parsed))


def load(path: str | Path) -> Catalog:
    """Load and validate a catalog YAML file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CatalogError(f"catalog not found: {path}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogValidationError(f"{path}: invalid YAML: {exc}") from exc
    return _validate_catalog(raw)


def loads(text: str) -> Catalog:
    """Load and validate a catalog from an in-memory YAML string."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogValidationError(f"invalid YAML: {exc}") from exc
    return _validate_catalog(raw)


# --------------------------------------------------------------------------- #
# Seed catalog generation
# --------------------------------------------------------------------------- #


def seed_catalog() -> Catalog:
    """Return a small seed catalog showing how each category looks.

    Not the 100+ full catalog (that needs FL Studio to validate) — just a
    well-formed example covering every category so:

    1. The YAML schema is exercised end-to-end in tests.
    2. ``tools/re_harness/modifications.yaml`` can be regenerated from
       code if someone clobbers it.
    """
    seeds: list[Modification] = [
        Modification(
            id="tempo_140_to_145",
            category="metadata",
            description="Change project tempo from 140 BPM to 145 BPM.",
            method="midi_script",
            base_flp="tests/corpus/re_base/fl25/base_empty.flp",
            script_commands=("set_tempo 145.0",),
            expected_change="Single WORD event payload changes value 140*100 -> 145*100",
            tags=("priority:p1",),
        ),
        Modification(
            id="channel_0_volume_50_to_80",
            category="channel",
            description="Raise channel 0 volume from 50% to 80%.",
            method="midi_script",
            base_flp="tests/corpus/re_base/fl25/base_one_channel.flp",
            script_commands=("select_channel 0", "set_volume 0.8"),
            expected_change="One DWORD event near channel-0 block",
            tags=("priority:p1",),
        ),
        Modification(
            id="insert_1_rename_to_drums",
            category="mixer",
            description="Rename mixer insert 1 to 'Drums'.",
            method="midi_script",
            base_flp="tests/corpus/re_base/fl25/base_one_insert.flp",
            script_commands=("select_insert 1", "set_name 'Drums'"),
            expected_change="One DATA (TEXT) event carrying utf-16le 'Drums'",
            tags=("priority:p1",),
        ),
        Modification(
            id="pattern_1_add_note_c5_b1",
            category="pattern",
            description="Add a C5 note at beat 1 in pattern 1.",
            method="midi_script",
            base_flp="tests/corpus/re_base/fl25/base_one_pattern.flp",
            script_commands=("select_pattern 1", "add_note C5 0 96 100"),
            expected_change="Pattern notes DATA event grows by one note struct",
            tags=("priority:p1",),
        ),
        Modification(
            id="automation_add_keyframe",
            category="automation",
            description="Add an automation keyframe on channel 0 volume.",
            method="midi_script",
            base_flp="tests/corpus/re_base/fl25/base_one_channel.flp",
            script_commands=("select_channel 0", "add_automation_point volume 96 0.7"),
            expected_change="Automation DATA event grows",
            tags=("priority:p2",),
        ),
        Modification(
            id="playlist_add_clip",
            category="arrangement",
            description="Place pattern 1 clip on track 1 at position 0.",
            method="midi_script",
            base_flp="tests/corpus/re_base/fl25/base_one_pattern.flp",
            script_commands=("select_track 1", "add_pattern_clip 1 0 192"),
            expected_change="Playlist DATA event grows by one clip struct",
            tags=("priority:p1",),
        ),
        Modification(
            id="eq2_band_3_freq_2400_to_3100",
            category="plugin",
            description=(
                "Fruity Parametric EQ 2: change band 3 frequency from 2400Hz to 3100Hz."
            ),
            method="ui_automation",
            base_flp="tests/corpus/re_base/fl25/base_one_insert.flp",
            script_commands=("ui.open_slot 1 1", "ui.eq2_set_band_freq 3 3100"),
            expected_change=(
                "Inside the EQ2 plugin state DATA blob, 4 bytes at band-3 freq offset change"
            ),
            tags=("priority:p1", "plugin:eq2"),
        ),
        Modification(
            id="fl25_new_channel_event",
            category="fl_version_specific",
            description="Capture any FL25-only channel events emitted when a Sampler is created.",
            method="midi_script",
            base_flp="tests/corpus/re_base/fl25/base_empty.flp",
            script_commands=("add_channel Sampler",),
            expected_change="Net new opcode(s) unseen in FL 24 catalog runs",
            fl_versions=("25",),
            tags=("priority:p1",),
        ),
    ]
    return Catalog(modifications=tuple(seeds))


def dump(catalog: Catalog, *, indent: int = 2) -> str:
    """Serialize a :class:`Catalog` to YAML (stable key order)."""
    obj = {
        "modifications": [
            {
                "id": m.id,
                "category": m.category,
                "description": m.description,
                "method": m.method,
                "base_flp": m.base_flp,
                "script_commands": list(m.script_commands),
                "expected_change": m.expected_change,
                "fl_versions": list(m.fl_versions),
                "tags": list(m.tags),
            }
            for m in catalog.modifications
        ]
    }
    return yaml.safe_dump(obj, sort_keys=False, indent=indent, allow_unicode=True)


__all__ = [
    "CATEGORIES",
    "METHODS",
    "Catalog",
    "CatalogError",
    "CatalogValidationError",
    "Category",
    "Method",
    "Modification",
    "dump",
    "load",
    "loads",
    "seed_catalog",
]


# --------------------------------------------------------------------------- #
# CLI entrypoint: `python -m tools.re_harness.modifications`
# --------------------------------------------------------------------------- #


def _main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Validate or regenerate the RE modification catalog.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub_validate = sub.add_parser("validate", help="validate a catalog YAML")
    sub_validate.add_argument("path")

    sub_seed = sub.add_parser(
        "seed",
        help="print the seed catalog as YAML (safe to redirect into modifications.yaml)",
    )
    _ = sub_seed  # no extra args

    args = p.parse_args(argv)

    if args.cmd == "validate":
        cat = load(args.path)
        print(f"{args.path}: OK, {len(cat.modifications)} modification(s)")
        for m in cat.modifications:
            print(f"  - {m.id:<40} [{m.category}] {m.method}")
        return 0
    if args.cmd == "seed":
        print(dump(seed_catalog()))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
