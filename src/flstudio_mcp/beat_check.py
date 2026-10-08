"""Tier 0 symbolic beat checks (F11.1.1).

Pure functions over the bridge's ``describe`` payload — no FL, no bridge
calls — so the ``check_beat`` offline kind, the ``render_to_wav`` section
analysis and the e2e judge share one implementation.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field, replace
from typing import Any

from .issues import Issue
from .music.theory import Key, key_name, parse_key

GENRES: tuple[str, ...] = ("house", "trap")
ROLES: tuple[str, ...] = ("kick", "clap", "snare", "hat", "perc", "bass", "808", "chords", "lead")
DRUM_ROLES = frozenset({"kick", "clap", "snare", "hat", "perc"})

# First match wins. Keywords of <= 2 chars must match a whole token.
_ROLE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("kick", ("kick", "bd")),
    ("808", ("808",)),
    ("clap", ("clap",)),
    ("snare", ("snare", "sd")),
    ("hat", ("hat", "hh", "cymbal", "ride", "crash")),
    ("perc", ("perc", "shaker", "tom", "rim", "conga", "bongo", "tamb", "cowbell")),
    ("bass", ("bass", "sub")),
    ("chords", ("chord", "stab", "pad", "keys", "piano", "organ", "rhodes")),
    ("lead", ("lead", "melody", "bell", "pluck", "arp", "synth")),
)

DEFAULT_SECTION_ENERGY: dict[str, float] = {
    "intro": 0.3,
    "outro": 0.3,
    "break": 0.4,
    "verse": 0.5,
    "bridge": 0.5,
    "build": 0.6,
    "hook": 0.9,
    "chorus": 0.9,
    "drop": 1.0,
}

# Does FL repeat a pattern that is shorter than its playlist clip?
# Decided by the F11.0.1 render spike; flip (and its tests) if FL loops.
PATTERN_CLIPS_LOOP = False

ONSET_TOLERANCE_TICKS = 6  # at PPQ 96; scaled for other resolutions


@dataclass(frozen=True)
class NoteEvent:
    position: int
    length: int
    key: int
    velocity: int
    channel_iid: int


@dataclass(frozen=True)
class Clip:
    pattern_iid: int
    start: int
    length: int


@dataclass(frozen=True)
class Section:
    """A stretch of the playlist: one clip, or several clips that overlap in time."""

    name: str
    start: int
    length: int
    clips: tuple[Clip, ...]
    energy: float | None


@dataclass
class BeatView:
    """What the arrangement plays, in a shape the checks can reason about."""

    ppq: int
    tempo: float
    beats_per_bar: int
    channel_names: dict[int, str]
    roles: dict[int, str | None]
    pitch_follows_key: dict[int, bool]
    pattern_names: dict[int, str]
    pattern_notes: dict[int, list[NoteEvent]]
    sections: list[Section]

    @property
    def bar_ticks(self) -> int:
        return self.ppq * self.beats_per_bar

    @property
    def total_bars(self) -> float:
        end = max((s.start + s.length for s in self.sections), default=0)
        return end / self.bar_ticks if self.bar_ticks else 0.0

    def tolerance(self) -> int:
        """Onset-matching tolerance in ticks."""
        return max(1, round(ONSET_TOLERANCE_TICKS * self.ppq / 96))

    def seconds(self, ticks: int) -> float:
        """Convert ticks to seconds at the project tempo."""
        return ticks / self.ppq * 60.0 / self.tempo

    def notes_in(self, section: Section) -> list[NoteEvent]:
        """Notes that sound in ``section``; positions relative to its start."""
        out: list[NoteEvent] = []
        for clip in section.clips:
            notes = self.pattern_notes.get(clip.pattern_iid, [])
            offsets = [0]
            if PATTERN_CLIPS_LOOP and notes:
                span = max(n.position + max(n.length, 1) for n in notes)
                loop = -(-span // self.bar_ticks) * self.bar_ticks
                offsets = list(range(0, clip.length, loop))
            base = clip.start - section.start
            for offset in offsets:
                for n in notes:
                    pos = n.position + offset
                    if pos < clip.length:
                        out.append(replace(n, position=base + pos))
        out.sort(key=lambda n: (n.position, n.key))
        return out


def infer_role(name: str | None) -> str | None:
    """Role from a channel name: ``"<Role> · <sound>"`` prefix first, then keywords."""
    if not name:
        return None
    if "·" in name:
        prefix = name.split("·", 1)[0].strip().lower()
        if prefix in ROLES:
            return prefix
    low = name.lower()
    tokens = set(re.findall(r"[a-z0-9]+", low))
    for role, words in _ROLE_KEYWORDS:
        for word in words:
            if (len(word) <= 2 and word in tokens) or (len(word) > 2 and word in low):
                return role
    return None


def section_energy(name: str) -> float | None:
    """Expected energy 0..1 from section-name keywords (max over matches)."""
    low = name.lower()
    hits = [energy for word, energy in DEFAULT_SECTION_ENERGY.items() if word in low]
    return max(hits) if hits else None


def _merge_into_sections(clips: list[Clip], pattern_names: dict[int, str]) -> list[Section]:
    groups: list[list[Clip]] = []
    end = -1
    for clip in sorted(clips, key=lambda c: (c.start, c.pattern_iid)):
        if groups and clip.start < end:
            groups[-1].append(clip)
            end = max(end, clip.start + clip.length)
        else:
            groups.append([clip])
            end = clip.start + clip.length
    sections = []
    for group in groups:
        start = min(c.start for c in group)
        stop = max(c.start + c.length for c in group)
        names: list[str] = []
        for c in group:
            label = pattern_names.get(c.pattern_iid, f"Pattern {c.pattern_iid}")
            if label not in names:
                names.append(label)
        name = "+".join(names)
        sections.append(
            Section(
                name=name,
                start=start,
                length=stop - start,
                clips=tuple(group),
                energy=section_energy(name),
            )
        )
    return sections


def build_view(describe: dict[str, Any], roles: dict[int, str] | None = None) -> BeatView:
    """Build a BeatView from a bridge ``describe`` payload (arrangement 0)."""
    meta = describe.get("metadata") or {}
    ppq = int(meta.get("ppq") or 96)
    tempo = float(meta.get("tempo") or 120.0)
    beats_per_bar = int((meta.get("time_signature") or {}).get("numerator") or 4)

    channel_names: dict[int, str] = {}
    pitch_follows_key: dict[int, bool] = {}
    for c in describe.get("channels") or []:
        iid = int(c["iid"])
        channel_names[iid] = c.get("name") or ""
        pitch_follows_key[iid] = bool(c.get("plugin")) and not c.get("sample_path")
    overrides = roles or {}
    resolved = {iid: overrides.get(iid) or infer_role(n) for iid, n in channel_names.items()}

    pattern_names: dict[int, str] = {}
    pattern_notes: dict[int, list[NoteEvent]] = {}
    for p in describe.get("patterns") or []:
        iid = int(p["iid"])
        pattern_names[iid] = p.get("name") or f"Pattern {iid}"
        pattern_notes[iid] = [
            NoteEvent(
                position=int(n["position"]),
                length=int(n.get("length") or 0),
                key=int(n["key"]),
                velocity=int(n.get("velocity", 100)),
                channel_iid=int(n["channel_iid"]),
            )
            for n in p.get("notes") or []
        ]

    clips: list[Clip] = []
    arrangements = describe.get("arrangements") or []
    if arrangements:
        for track in arrangements[0].get("tracks") or []:
            for item in track.get("items") or []:
                if item.get("pattern_iid") is None or item.get("muted"):
                    continue
                clips.append(
                    Clip(int(item["pattern_iid"]), int(item["position"]), int(item["length"]))
                )

    return BeatView(
        ppq=ppq,
        tempo=tempo,
        beats_per_bar=beats_per_bar,
        channel_names=channel_names,
        roles=resolved,
        pitch_follows_key=pitch_follows_key,
        pattern_names=pattern_names,
        pattern_notes=pattern_notes,
        sections=_merge_into_sections(clips, pattern_names),
    )


# ---------------------------------------------------------------- checks #

REGISTERS: dict[str, tuple[int, int]] = {
    "bass": (24, 55),
    "808": (24, 55),
    "chords": (48, 84),
    "lead": (55, 96),
}
# Minimum velocity range (max - min) per role; kick and 808 are often flat by design.
MIN_VELOCITY_RANGE: dict[str, int] = {
    "hat": 20,
    "perc": 15,
    "clap": 8,
    "snare": 8,
    "bass": 10,
    "chords": 8,
    "lead": 12,
}
CORE_ROLES: dict[str, tuple[tuple[str, ...], ...]] = {
    "house": (("kick",), ("hat",), ("bass", "808")),
    "trap": (("kick",), ("hat",), ("808", "bass"), ("snare", "clap")),
}
HIGH_ENERGY = 0.8


@dataclass
class CheckResult:
    ok: bool
    issues: list[Issue]
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [i.to_dict() for i in self.issues],
            "metrics": self.metrics,
        }


def check_beat(
    describe: dict[str, Any],
    genre: str,
    *,
    key: str | None = None,
    roles: dict[int, str] | None = None,
) -> CheckResult:
    """Run every Tier 0 symbolic check on the arrangement in ``describe``."""
    if genre not in GENRES:
        raise ValueError(f"genre must be one of {list(GENRES)}, got {genre!r}")
    parsed_key = parse_key(key) if key else None
    view = build_view(describe, roles)
    issues = _check_structure(view)
    if view.sections:
        arranged = _arranged_notes(view)
        by_role = _notes_by_role(view, arranged)
        issues += _check_roles(view, genre, by_role, arranged)
        issues += _check_registers(view, by_role)
        issues += _check_velocity(by_role)
        issues += _check_chords(view, by_role)
        if parsed_key is not None:
            issues += _check_key(view, by_role, parsed_key)
        issues += _check_groove(view, genre)
        issues += _check_sections(view)
    return CheckResult(
        ok=not any(i.severity == "error" for i in issues),
        issues=issues,
        metrics=_metrics(view),
    )


def _arranged_notes(view: BeatView) -> list[NoteEvent]:
    """Every sounding note, positions absolute in the playlist."""
    return [
        replace(n, position=s.start + n.position) for s in view.sections for n in view.notes_in(s)
    ]


def _notes_by_role(view: BeatView, arranged: list[NoteEvent]) -> dict[str, list[NoteEvent]]:
    out: dict[str, list[NoteEvent]] = {}
    for n in arranged:
        role = view.roles.get(n.channel_iid)
        if role:
            out.setdefault(role, []).append(n)
    return out


def _active_roles(view: BeatView, section: Section) -> set[str]:
    return {r for n in view.notes_in(section) if (r := view.roles.get(n.channel_iid))}


def _check_structure(view: BeatView) -> list[Issue]:
    if not view.sections:
        return [
            Issue(
                "error",
                "nothing is arranged in the playlist",
                hint="Place pattern clips with arrange_song or add_clip.",
            )
        ]
    issues: list[Issue] = []
    bar = view.bar_ticks
    for s in view.sections:
        for clip in s.clips:
            if not view.pattern_notes.get(clip.pattern_iid):
                name = view.pattern_names.get(clip.pattern_iid, f"Pattern {clip.pattern_iid}")
                issues.append(
                    Issue(
                        "error",
                        f"clip of pattern '{name}' at bar {clip.start // bar + 1} has no notes",
                        hint="Fill the pattern or remove the clip.",
                        section=s.name,
                    )
                )
        bars = -(-s.length // bar)
        occupied: set[int] = set()
        for n in view.notes_in(s):
            last = (n.position + max(n.length, 1) - 1) // bar
            occupied.update(range(n.position // bar, last + 1))
        silent = [b for b in range(bars) if b not in occupied]
        if silent:
            issues.append(
                Issue(
                    "warning",
                    f"section '{s.name}' has {len(silent)} silent bar(s) out of {bars}",
                    hint="The pattern is shorter than its clip; extend the pattern or shorten "
                    "the clip.",
                    section=s.name,
                )
            )
    return issues


def _check_roles(
    view: BeatView, genre: str, by_role: dict[str, list[NoteEvent]], arranged: list[NoteEvent]
) -> list[Issue]:
    issues: list[Issue] = []
    present = {r for r, notes in by_role.items() if notes}
    for alternatives in CORE_ROLES[genre]:
        if not present & set(alternatives):
            issues.append(
                Issue(
                    "error",
                    f"no {' or '.join(alternatives)} notes in the arrangement",
                    hint=f"A {genre} beat needs {' or '.join(alternatives)}.",
                    role=alternatives[0],
                )
            )
    for iid in sorted({n.channel_iid for n in arranged if view.roles.get(n.channel_iid) is None}):
        issues.append(
            Issue(
                "info",
                f"channel '{view.channel_names.get(iid, iid)}' has notes but no recognizable role",
                hint="Name channels '<Role> · <sound>' (e.g. 'Bass · Rolling') or pass roles.",
            )
        )
    return issues


def _check_registers(view: BeatView, by_role: dict[str, list[NoteEvent]]) -> list[Issue]:
    issues: list[Issue] = []
    for role, (lo, hi) in REGISTERS.items():
        notes = by_role.get(role) or []
        tracked = [n for n in notes if view.pitch_follows_key.get(n.channel_iid)]
        for iid in sorted({n.channel_iid for n in notes} - {n.channel_iid for n in tracked}):
            issues.append(
                Issue(
                    "info",
                    f"{role} register not checked on sample-based channel "
                    f"'{view.channel_names.get(iid, iid)}' (a sample plays at its own pitch)",
                    hint="The render's audio band checks cover the low end.",
                    role=role,
                )
            )
        if not tracked:
            continue
        outside = [n for n in tracked if not lo <= n.key <= hi]
        if not outside:
            continue
        keys = sorted({n.key for n in tracked})
        issues.append(
            Issue(
                "error" if len(outside) / len(tracked) > 0.10 else "warning",
                f"{role} notes span keys {keys[0]}-{keys[-1]} "
                f"({key_name(keys[0])}-{key_name(keys[-1])} in FL); "
                f"expected {lo}-{hi} ({key_name(lo)}-{key_name(hi)})",
                hint=f"Move the {role} part by octaves into its register "
                "(transpose_pattern_notes).",
                role=role,
            )
        )
    return issues


def _check_velocity(by_role: dict[str, list[NoteEvent]]) -> list[Issue]:
    issues: list[Issue] = []
    for role, floor in MIN_VELOCITY_RANGE.items():
        notes = by_role.get(role) or []
        if len(notes) < 8:
            continue
        velocities = [n.velocity for n in notes]
        if max(velocities) - min(velocities) < floor:
            issues.append(
                Issue(
                    "warning",
                    f"{role} velocities are flat ({min(velocities)}-{max(velocities)})",
                    hint="Add accents and humanize (humanize_velocities).",
                    role=role,
                )
            )
    return issues


def _group_onsets(notes: list[NoteEvent], tolerance: int) -> list[list[NoteEvent]]:
    groups: list[list[NoteEvent]] = []
    for n in sorted(notes, key=lambda n: n.position):
        if groups and n.position - groups[-1][0].position <= tolerance:
            groups[-1].append(n)
        else:
            groups.append([n])
    return groups


def _check_chords(view: BeatView, by_role: dict[str, list[NoteEvent]]) -> list[Issue]:
    notes = by_role.get("chords") or []
    if not notes:
        return []
    onsets = _group_onsets(notes, view.tolerance())
    voiced = sum(1 for g in onsets if len({n.key for n in g}) >= 3)
    if voiced / len(onsets) >= 0.5:
        return []
    return [
        Issue(
            "error",
            f"chords role plays {voiced}/{len(onsets)} onsets as 3+ note voicings",
            hint="Write real chords: 3-4 notes per hit.",
            role="chords",
        )
    ]


def _check_key(view: BeatView, by_role: dict[str, list[NoteEvent]], key: Key) -> list[Issue]:
    pcs = key.pitch_classes()
    pitched = [
        n
        for role, notes in by_role.items()
        if role not in DRUM_ROLES
        for n in notes
        if view.pitch_follows_key.get(n.channel_iid)
    ]
    if not pitched:
        return []
    outside = [n for n in pitched if n.key % 12 not in pcs]
    if len(outside) / len(pitched) <= 0.15:
        return []
    return [
        Issue(
            "warning",
            f"{len(outside)} of {len(pitched)} pitched notes are outside {key}",
            hint="Keep bass, chords and lead in the stated key.",
        )
    ]


def _has_onset(positions: list[int], target: int, tolerance: int) -> bool:
    i = bisect.bisect_left(positions, target - tolerance)
    return i < len(positions) and positions[i] <= target + tolerance


def _check_groove(view: BeatView, genre: str) -> list[Issue]:
    hot = [s for s in view.sections if s.energy is not None and s.energy >= HIGH_ENERGY]
    if not hot:
        return [
            Issue(
                "info",
                "no high-energy section (drop/hook/chorus) recognized by pattern name; "
                "groove idioms not checked",
                hint="Name sections like Intro/Build/Drop (house) or Intro/Hook/Verse (trap).",
            )
        ]
    issues: list[Issue] = []
    tol = view.tolerance()
    for s in hot:
        notes = view.notes_in(s)
        if genre == "house":
            kicks = sorted(n.position for n in notes if view.roles.get(n.channel_iid) == "kick")
            beats = list(range(0, s.length, view.ppq))
            hit = sum(1 for b in beats if _has_onset(kicks, b, tol))
            if not beats or hit / len(beats) < 0.9:
                issues.append(
                    Issue(
                        "error",
                        f"house {s.name}: kick on {hit}/{len(beats)} beats",
                        hint="A house drop needs a four-on-the-floor kick.",
                        role="kick",
                        section=s.name,
                    )
                )
        else:
            backbeat = sorted(
                n.position for n in notes if view.roles.get(n.channel_iid) in ("snare", "clap")
            )
            bars = list(range(0, s.length, view.bar_ticks))
            offset = view.ppq * (view.beats_per_bar // 2)
            hit = sum(1 for b in bars if _has_onset(backbeat, b + offset, tol))
            if not bars or hit / len(bars) < 0.75:
                issues.append(
                    Issue(
                        "error",
                        f"trap {s.name}: snare/clap on beat 3 in {hit}/{len(bars)} bars",
                        hint="Trap hooks use a half-time backbeat (snare/clap on 3).",
                        role="snare",
                        section=s.name,
                    )
                )
    return issues


def _same_feel(view: BeatView, a: Section, b: Section) -> bool:
    if {c.pattern_iid for c in a.clips} == {c.pattern_iid for c in b.clips}:
        return True
    if _active_roles(view, a) != _active_roles(view, b):
        return False
    da = len(view.notes_in(a)) / max(a.length, 1)
    db = len(view.notes_in(b)) / max(b.length, 1)
    return max(da, db) == 0 or abs(da - db) / max(da, db) < 0.2


def _check_sections(view: BeatView) -> list[Issue]:
    issues: list[Issue] = []
    for s in view.sections:
        if s.energy is not None and s.energy <= 0.3:
            continue
        active = _active_roles(view, s)
        if len(active) < 2:
            issues.append(
                Issue(
                    "warning",
                    f"section '{s.name}' has only {len(active)} active role(s)",
                    hint="Layer at least drums + one more role outside intros/outros.",
                    section=s.name,
                )
            )
    for a, b in zip(view.sections, view.sections[1:], strict=False):
        if _same_feel(view, a, b):
            issues.append(
                Issue(
                    "warning",
                    f"sections '{a.name}' and '{b.name}' sound the same",
                    hint="Vary density or roles between adjacent sections.",
                    section=b.name,
                )
            )
    return issues


def _metrics(view: BeatView) -> dict[str, Any]:
    bar = view.bar_ticks
    arranged = _arranged_notes(view)
    roles: dict[str, dict[str, Any]] = {}
    for role, notes in _notes_by_role(view, arranged).items():
        keys = [n.key for n in notes]
        velocities = [n.velocity for n in notes]
        roles[role] = {
            "notes": len(notes),
            "key_range": [min(keys), max(keys)],
            "velocity_range": [min(velocities), max(velocities)],
        }
    return {
        "total_bars": round(view.total_bars, 2),
        "tempo": view.tempo,
        "ppq": view.ppq,
        "sections": [
            {
                "name": s.name,
                "start_bar": s.start // bar + 1,
                "bars": round(s.length / bar, 2),
                "energy": s.energy,
                "roles": sorted(_active_roles(view, s)),
            }
            for s in view.sections
        ],
        "roles": roles,
    }
