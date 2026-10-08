"""Music theory primitives shared by beat checks and (from Q2) the generators.

Key numbers are FL Studio note keys as stored in the .flp (0..131). They
follow MIDI numbering: 60 is middle C (~261.63 Hz). FL's piano roll
*labels* key 60 as "C5" — one octave above scientific/MIDI "C4". Whether a
note actually sounds at that pitch depends on the channel: plugin
generators follow the key, samplers play the sample's own pitch with
root key 60. The F11.0.2 render probe confirms both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MIDDLE_C_KEY = 60
A4_KEY = 69
A4_HZ = 440.0

_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
PITCH_CLASS: dict[str, int] = {
    "C": 0, "B#": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "Fb": 4,
    "F": 5, "E#": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
    "A#": 10, "Bb": 10, "B": 11, "Cb": 11,
}  # fmt: skip
MODES: dict[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "harmonic minor": (0, 2, 3, 5, 7, 8, 11),
}
_MODE_ALIASES = {"": "major", "maj": "major", "m": "minor", "min": "minor"}
_KEY_RE = re.compile(r"^\s*([A-Ga-g])([#b]?)\s*(.*?)\s*$")


@dataclass(frozen=True)
class Key:
    """A tonic pitch class (0..11) plus a mode name from ``MODES``."""

    tonic: int
    mode: str

    def pitch_classes(self) -> frozenset[int]:
        """Pitch classes of the scale."""
        return frozenset((self.tonic + step) % 12 for step in MODES[self.mode])

    def __str__(self) -> str:
        return f"{_NOTE_NAMES[self.tonic]} {self.mode}"


def parse_key(text: str) -> Key:
    """Parse "F#m", "C", "Am", "Bb minor", "D dorian", "E major" into a Key."""
    m = _KEY_RE.match(text or "")
    if not m:
        raise ValueError(f"unrecognized key {text!r}")
    letter, accidental, rest = m.groups()
    mode_raw = rest.strip().lower().replace("-", " ").replace("_", " ")
    mode = _MODE_ALIASES.get(mode_raw, mode_raw)
    if mode not in MODES:
        raise ValueError(f"unknown mode {rest!r} in key {text!r}; supported: {sorted(MODES)}")
    return Key(tonic=PITCH_CLASS[letter.upper() + accidental], mode=mode)


def key_name(key: int) -> str:
    """FL Studio's piano-roll label for a key number: 60 -> "C5"."""
    return f"{_NOTE_NAMES[key % 12]}{key // 12}"


def key_to_hz(key: int) -> float:
    """Equal-tempered frequency of a key number (69 = A 440 Hz)."""
    return A4_HZ * 2.0 ** ((key - A4_KEY) / 12.0)
