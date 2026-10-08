"""Tier 0 symbolic beat checks (F11.1.1).

Pure functions over the bridge's ``describe`` payload — no FL, no bridge
calls — so the ``check_beat`` offline kind, the ``render_to_wav`` section
analysis and the e2e judge share one implementation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

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
