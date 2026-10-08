"""Hand-built ``describe`` payloads (bridge shape) for beat-check tests."""

from __future__ import annotations

from typing import Any

PPQ = 96
BAR = PPQ * 4

# channel spec: name only (→ plugin synth) or (name, kind, plugin_name | None, sample_path | None)
ChannelSpec = str | tuple[str, str, str | None, str | None]


def note(
    position: int, key: int, channel: int, *, length: int = 48, velocity: int = 100
) -> dict[str, Any]:
    return {
        "_type": "Note",
        "position": position,
        "length": length,
        "key": key,
        "channel_iid": channel,
        "velocity": velocity,
    }


def make_describe(
    *,
    channels: dict[int, ChannelSpec],
    patterns: dict[int, tuple[str, list[dict[str, Any]]]],
    clips: list[tuple[int, ...]],
    tempo: float = 124.0,
    ppq: int = PPQ,
    numerator: int = 4,
) -> dict[str, Any]:
    """clips: (pattern_iid, start_bar, bars) or (pattern_iid, start_bar, bars, track_index)."""
    chans = []
    for iid, spec in channels.items():
        name, kind, plugin, sample = (
            (spec, "instrument", "3x Osc", None) if isinstance(spec, str) else spec
        )
        chans.append(
            {
                "_type": "Channel",
                "iid": iid,
                "kind": kind,
                "name": name,
                "sample_path": sample,
                "plugin": {"internalName": plugin} if plugin else None,
            }
        )
    bar = ppq * numerator
    tracks: dict[int, list[dict[str, Any]]] = {}
    for clip in clips:
        pattern_iid, start_bar, bars, *rest = clip
        track = rest[0] if rest else 1
        tracks.setdefault(track, []).append(
            {
                "_type": "PlaylistItem",
                "position": start_bar * bar,
                "length": bars * bar,
                "pattern_iid": pattern_iid,
                "channel_iid": None,
                "muted": False,
            }
        )
    return {
        "_type": "FLPProject",
        "metadata": {
            "ppq": ppq,
            "tempo": tempo,
            "time_signature": {"numerator": numerator, "denominator": 4},
        },
        "channels": chans,
        "patterns": [
            {"_type": "Pattern", "iid": iid, "name": name, "notes": notes}
            for iid, (name, notes) in patterns.items()
        ],
        "arrangements": [
            {
                "_type": "Arrangement",
                "index": 0,
                "tracks": [
                    {"_type": "Track", "index": t, "items": items}
                    for t, items in sorted(tracks.items())
                ],
            }
        ],
    }


KICK, CLAP, HAT, BASS, CHORDS = 0, 1, 2, 3, 4
_BASS_KEYS = (33, 36, 40, 38)  # A1 C2 E2 D2 (MIDI names) — A minor
_CHORD_SHAPES = ((57, 60, 64), (60, 64, 67), (57, 60, 65), (55, 59, 62))  # Am, C, F/A, G


def _kicks(bars: int) -> list[dict[str, Any]]:
    return [
        note(b * BAR + beat * PPQ, 60, KICK, velocity=118 if beat == 0 else 108)
        for b in range(bars)
        for beat in range(4)
    ]


def _hats(bars: int) -> list[dict[str, Any]]:
    return [
        note(b * BAR + i * 48, 60, HAT, length=24, velocity=100 if i % 2 else 72)
        for b in range(bars)
        for i in range(8)
    ]


def _claps(bars: int) -> list[dict[str, Any]]:
    return [
        note(b * BAR + beat * PPQ, 60, CLAP, velocity=110 if beat == 1 else 100)
        for b in range(bars)
        for beat in (1, 3)
    ]


def _bass(bars: int) -> list[dict[str, Any]]:
    return [
        note(
            b * BAR + beat * PPQ + 48,
            _BASS_KEYS[b % 4],
            BASS,
            length=40,
            velocity=96 if beat % 2 else 108,
        )
        for b in range(bars)
        for beat in range(4)
    ]


def _chords(bars: int) -> list[dict[str, Any]]:
    return [
        note(b * BAR + offset, key, CHORDS, length=PPQ, velocity=velocity)
        for b in range(bars)
        for offset, velocity in ((0, 92), (2 * PPQ, 80))
        for key in _CHORD_SHAPES[b % 4]
    ]


def good_house() -> dict[str, Any]:
    """A 32-bar A-minor house sketch that passes every Tier 0 check."""
    sample = "%FLStudioFactoryData%/Data/Patches/Packs/Drums/x.wav"
    channels: dict[int, ChannelSpec] = {
        KICK: ("Kick", "sampler", None, sample),
        CLAP: ("Clap", "sampler", None, sample),
        HAT: ("Hat", "sampler", None, sample),
        BASS: ("Bass · Rolling", "instrument", "Fruity DX10", None),
        CHORDS: ("Chords · Keys", "instrument", "3x Osc", None),
    }
    patterns = {
        1: ("Intro", _kicks(8) + _hats(8)),
        2: ("Build", _kicks(8) + _hats(8) + _claps(8) + _bass(8)),
        3: ("Drop", _kicks(16) + _hats(16) + _claps(16) + _bass(16) + _chords(16)),
    }
    return make_describe(
        channels=channels, patterns=patterns, clips=[(1, 0, 8), (2, 8, 8), (3, 16, 16)]
    )
