# name=flstudio-mcp
"""FL Studio MIDI Scripting entry-point for flstudio-mcp.

Forked from ``device_flpdiff_harness.py`` (flpdiff RE harness) — same
handler dispatch, same IPC contract. The runtime path intentionally
still points at ``flpdiff-harness/runtime`` so the orchestrator-side
``re_harness.ipc.DEFAULT_RUNTIME_ROOT`` keeps working unchanged.
v1.0 vendoring will rebase both sides onto ``flstudio-mcp/runtime``.

----

Original docstring follows.

This file is **designed to be installed into FL Studio**, not imported from
the rest of the codebase. See ``README.md`` in this directory for
installation. Under FL Studio it runs inside FL's embedded Python
interpreter and has access to FL-specific modules (``channels``, ``mixer``,
``patterns``, ``transport``, ``ui``, ``general``).

Contract
--------

Every ``OnIdle`` tick:

1. Scan the configured inbox directory for oldest ``cmd_*.json`` file.
2. Parse it. If malformed, write an error result; archive.
3. Dispatch to a handler based on ``kind``.
4. Write ``result_<id>.json`` atomically.

**Status when committed: STUB.** The handlers are intentionally minimal —
most raise ``unsupported`` because they require live verification against
FL Studio's actual API surface. Expand incrementally: implement one
command, verify it in FL, commit, repeat.

Configuration
-------------

The harness inbox path is read from the environment variable
``FLPDIFF_HARNESS_INBOX``. Default is
``~/Library/Application Support/flpdiff/harness/runtime/`` on macOS.

Because FL's Python is sandboxed (no ``os.environ`` modifications from
outside), the orchestrator exports that env var before launching FL
Studio via ``open``. Keep this module parameter-free otherwise.

Python version
--------------

FL Studio 25 ships a Python 3-compatible scripting interpreter. Avoid
Python 3.10+ syntax (``match``, PEP 604 unions in function signatures) so
the file loads cleanly in FL's embedded version.
"""

import json
import os
import sys
import time
import traceback

# FL Studio modules. Guard imports so this file is at least import-able
# under CPython — useful for syntax checks.
try:
    import channels  # type: ignore[import-not-found]
    import general  # type: ignore[import-not-found]
    import mixer  # type: ignore[import-not-found]
    import patterns  # type: ignore[import-not-found]
    import plugins  # type: ignore[import-not-found]
    import transport  # type: ignore[import-not-found]
    import ui  # type: ignore[import-not-found]

    INSIDE_FL = True
except ImportError:
    INSIDE_FL = False

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

# FL's embedded Python doesn't set ``__file__`` (confirmed against FL
# MIDI scripting v40), so we can't locate the script dir dynamically.
# Fall back to the canonical install path — both this file and the
# orchestrator-side ``tools.re_harness.ipc.DEFAULT_RUNTIME_ROOT`` hardcode
# it to the same location to stay in sync.
#
# FL's sandboxed Python cannot create fresh subdirs under ``~/Documents/``
# (every mkdir returns NULL with no exception). Sitting inside FL's own
# Hardware tree works because FL already writes its settings there.
DEFAULT_INBOX_ROOT = os.path.expanduser(
    "~/Documents/Image-Line/FL Studio/Settings/Hardware/flstudio-mcp/runtime"
)
INBOX_ROOT = os.environ.get("FLPDIFF_HARNESS_INBOX", DEFAULT_INBOX_ROOT)

INBOX_DIR = os.path.join(INBOX_ROOT, "inbox")
OUTBOX_DIR = os.path.join(INBOX_ROOT, "outbox")
PROCESSED_DIR = os.path.join(INBOX_ROOT, "processed")


def _ensure_dirs():
    """Create the three IPC subdirs and verify we can write in each.

    FL's sandbox on this build allows create + write but denies
    ``os.remove`` (confirmed empirically — returns NULL with no
    exception). That's fine for our purposes: the IPC loop only uses
    ``open(..., 'w')`` and ``os.replace`` (rename), never unlink. So we
    intentionally leave the probe file in place rather than trying to
    delete it.
    """
    for d in (INBOX_DIR, OUTBOX_DIR, PROCESSED_DIR):
        try:
            if not os.path.isdir(d):
                os.makedirs(d)
            probe = os.path.join(d, ".write_probe")
            with open(probe, "w") as f:
                f.write("ok")
        except Exception as exc:
            print("[flstudio-mcp] cannot use directory {0!r}: {1}".format(d, exc))
            raise


# --------------------------------------------------------------------------- #
# IPC helpers (minimal mirror of tools.re_harness.ipc)
# --------------------------------------------------------------------------- #


def _read_command(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return raw


def _write_result(cmd_id, status, detail="", artifact_path=None):
    """Write the result file directly to its final path.

    FL's sandbox denies both ``os.remove`` and ``os.replace``, so the
    typical tmp+rename atomicity dance isn't available. Instead we
    serialize the full payload to a string first and write it in one
    ``f.write`` call — APFS treats small single-write operations as
    atomic at the syscall level, and the orchestrator tolerates the
    occasional torn read by retrying the JSON parse.
    """
    payload = {"id": cmd_id, "status": status, "detail": detail}
    if artifact_path is not None:
        payload["artifact_path"] = artifact_path
    out_path = os.path.join(OUTBOX_DIR, f"result_{cmd_id}.json")
    text = json.dumps(payload, indent=2, sort_keys=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)


def _mark_processed(cmd_id):
    """Stand-in for file moves, since FL's sandbox denies rename.

    Drops a tiny marker file into ``processed/`` so audit output still
    shows what the harness has run; the original ``cmd_*.json`` stays
    in ``inbox/``. ``_pending_commands`` already filters out commands
    with matching results, so the inbox growth is cosmetic.
    """
    marker = os.path.join(PROCESSED_DIR, f"cmd_{cmd_id}.done")
    try:
        with open(marker, "w") as f:
            f.write("ok")
    except Exception:
        pass  # non-fatal — processing succeeded either way


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #


def _handle_noop(args):
    # Handshake only — always OK.
    return "ok", "", None


def _handle_describe(args):
    """Read-only diagnostic: report what FL thinks the current project is.

    Helps us discover which API calls actually work in FL 25 before we
    commit to write-handlers. Every field is wrapped defensively because
    the scripting API raises for missing elements (e.g., no active project).
    The JSON returned in ``detail`` is the ground truth we use to design
    subsequent commands.
    """
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    report = {}

    def _try(label, fn):
        try:
            report[label] = fn()
        except Exception as exc:
            report[label] = "ERR:{0}".format(exc)

    _try("tempo", lambda: mixer.getCurrentTempo())  # type: ignore[name-defined]
    _try("project_title", lambda: general.getProjectTitle())  # type: ignore[name-defined]
    # FL's MIDI-scripting API has no current-file accessor:
    #   * general.getCurrentFilename — doesn't exist (verified via docs +
    #     runtime probe on FL 25.2.4 / 2025).
    #   * ui.getProgTitle — returns just "FL Studio 2025" (program name),
    #     not the window title with filename.
    # The orchestrator falls back to AppleScript reading the macOS-level
    # window title + Spotlight (mdfind) for full path resolution.
    _try("flp_path", lambda: general.getCurrentFilename())  # type: ignore[name-defined]
    _try("channel_count", lambda: channels.channelCount())  # type: ignore[name-defined]
    _try("pattern_count", lambda: patterns.patternCount())  # type: ignore[name-defined]
    _try("insert_count", lambda: mixer.trackCount())  # type: ignore[name-defined]
    _try(
        "api_version",
        lambda: ui.getVersion() if hasattr(ui, "getVersion") else "unknown",  # type: ignore[name-defined]
    )

    return "ok", json.dumps(report, sort_keys=True), None


def _handle_get_tempo(args):
    """Return current tempo in BPM. Confirmed live: mixer.getCurrentTempo()
    returns bpm*1000 on FL 25.2.4."""
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    try:
        raw = mixer.getCurrentTempo()  # type: ignore[name-defined]
        return "ok", "{0}".format(raw / 1000.0), None
    except Exception as exc:
        return "error", f"get_tempo failed: {exc}", None


def _handle_set_tempo(args):
    """Set tempo via ``mixer.setCurrentTempo(bpm * 1000)``.

    Confirmed live against FL 25.2.4: tempo display updates and
    get_tempo readback reflects the change. ``mixer.setTempo`` and the
    ``general.processRECEvent(REC_MainTempo)`` fallback were dropped
    once this one was verified.
    """
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    try:
        bpm = float(args.get("bpm"))
    except (TypeError, ValueError):
        return "error", "bpm arg missing or not a number", None
    raw = int(round(bpm * 1000))
    try:
        mixer.setCurrentTempo(raw)  # type: ignore[name-defined]
    except Exception as exc:
        return "error", "mixer.setCurrentTempo({0}): {1}".format(raw, exc), None
    return "ok", "tempo set to {0} bpm".format(bpm), None


def _handle_list_apis(args):
    """Return ``dir()`` listings for FL's scripting modules.

    Read-only discovery. Handy when adding a new write handler so we
    know what methods actually exist in this FL version before committing
    to a specific API. Filters to public callables by default; pass
    ``{"include_private": true}`` to dump everything.
    """
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    include_private = bool(args.get("include_private", False))
    report = {}
    for mod_name, mod in (
        ("channels", channels),  # type: ignore[name-defined]
        ("general", general),  # type: ignore[name-defined]
        ("mixer", mixer),  # type: ignore[name-defined]
        ("patterns", patterns),  # type: ignore[name-defined]
        ("plugins", plugins),  # type: ignore[name-defined]
        ("transport", transport),  # type: ignore[name-defined]
        ("ui", ui),  # type: ignore[name-defined]
    ):
        names = dir(mod)
        if not include_private:
            names = [n for n in names if not n.startswith("_")]
        # Keep only names we can identify as callables; skip constants
        # (integers etc.) for a cleaner writeup.
        callables = []
        for n in names:
            try:
                attr = getattr(mod, n)
                if callable(attr):
                    callables.append(n)
            except Exception:
                pass
        report[mod_name] = sorted(callables)
    return "ok", json.dumps(report, sort_keys=True), None


def _handle_set_time_signature(args):
    """Set global time sig via ``general.setNumerator`` + ``setDenominator``.

    FL 25's API splits the signature into two separate setter calls —
    no combined ``setTimeSig``. Confirmed via the ``list_apis`` handler.
    """
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    try:
        num = int(args.get("num"))
        beat = int(args.get("beat"))
    except (TypeError, ValueError):
        return "error", "num/beat missing or not integers", None
    try:
        general.setNumerator(num)  # type: ignore[name-defined]
        general.setDenominator(beat)  # type: ignore[name-defined]
    except Exception as exc:
        return "error", "setNumerator/setDenominator({0},{1}): {2}".format(
            num, beat, exc), None
    return "ok", "time sig set to {0}/{1}".format(num, beat), None


def _handle_set_channel_volume(args):
    """Set channel-rack volume. FL expects ``v`` in the range 0.0-1.0."""
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    try:
        iid = int(args.get("iid"))
        v = float(args.get("value"))
    except (TypeError, ValueError):
        return "error", "iid/value missing or wrong type", None
    try:
        channels.setChannelVolume(iid, v)  # type: ignore[name-defined]
    except Exception as exc:
        return "error", "setChannelVolume({0},{1}): {2}".format(iid, v, exc), None
    return "ok", "channel {0} volume set to {1}".format(iid, v), None


def _handle_set_channel_name(args):
    """Set channel-rack name."""
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    try:
        iid = int(args.get("iid"))
    except (TypeError, ValueError):
        return "error", "iid missing or not int", None
    name = args.get("name")
    if not isinstance(name, str):
        return "error", "name arg missing or not a string", None
    try:
        channels.setChannelName(iid, name)  # type: ignore[name-defined]
    except Exception as exc:
        return "error", "setChannelName({0},{1!r}): {2}".format(iid, name, exc), None
    return "ok", "channel {0} renamed to {1!r}".format(iid, name), None


def _handle_set_insert_volume(args):
    """Set mixer-insert volume via ``mixer.setTrackVolume``."""
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    try:
        idx = int(args.get("idx"))
        v = float(args.get("value"))
    except (TypeError, ValueError):
        return "error", "idx/value missing or wrong type", None
    try:
        mixer.setTrackVolume(idx, v)  # type: ignore[name-defined]
    except Exception as exc:
        return "error", "setTrackVolume({0},{1}): {2}".format(idx, v, exc), None
    return "ok", "insert {0} volume set to {1}".format(idx, v), None


def _handle_set_insert_name(args):
    """Set mixer-insert name via ``mixer.setTrackName``."""
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    try:
        idx = int(args.get("idx"))
    except (TypeError, ValueError):
        return "error", "idx missing or not int", None
    name = args.get("name")
    if not isinstance(name, str):
        return "error", "name arg missing or not a string", None
    try:
        mixer.setTrackName(idx, name)  # type: ignore[name-defined]
    except Exception as exc:
        return "error", "setTrackName({0},{1!r}): {2}".format(idx, name, exc), None
    return "ok", "insert {0} renamed to {1!r}".format(idx, name), None


def _handle_set_plugin_param(args):
    """Set a plugin's internal parameter via ``plugins.setParamValue``.

    Args:
        index:       channel-rack index OR mixer insert index (depending on ``scope``).
        scope:       "channel" (default) or "mixer".
        slot:        mixer effect slot index (0..9); ignored when scope="channel".
        param:       parameter index inside the plugin (0-based).
        value:       normalized float 0.0 .. 1.0.

    The signature is the one documented in FL's MIDI scripting manual:
    ``plugins.setParamValue(value, paramIndex, index, slotIndex=-1, ...)``.
    We pass ``slotIndex=-1`` for channel-rack plugins and a real slot
    index for mixer-insert plugins.
    """
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    try:
        index = int(args.get("index"))
        param = int(args.get("param"))
        value = float(args.get("value"))
    except (TypeError, ValueError):
        return "error", "index/param/value missing or wrong type", None
    scope = args.get("scope", "channel")
    if scope not in ("channel", "mixer"):
        return "error", "scope must be 'channel' or 'mixer'", None
    slot = int(args.get("slot", -1))
    if scope == "mixer" and slot < 0:
        return "error", "mixer scope requires slot>=0", None
    try:
        plugins.setParamValue(value, param, index, slot)  # type: ignore[name-defined]
    except Exception as exc:
        return "error", "plugins.setParamValue({0},{1},{2},{3}): {4}".format(
            value, param, index, slot, exc), None
    return "ok", "plugin param set: scope={0} index={1} slot={2} param={3} value={4}".format(
        scope, index, slot, param, value), None


def _handle_get_plugin_info(args):
    """Read-only diagnostic: report what plugin lives at a given
    channel-or-insert/slot, and its parameter count + names.

    Feeds the RE workflow — before sweeping parameters, we need to
    know how many there are and what they're called.
    """
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    try:
        index = int(args.get("index"))
    except (TypeError, ValueError):
        return "error", "index missing or not int", None
    scope = args.get("scope", "channel")
    slot = int(args.get("slot", -1))
    info = {"scope": scope, "index": index, "slot": slot}

    def _try(label, fn):
        try:
            info[label] = fn()
        except Exception as exc:
            info[label] = "ERR:{0}".format(exc)

    # Plugin name
    _try("name", lambda: plugins.getPluginName(index, slot))  # type: ignore[name-defined]
    # Parameter count + names (if available)
    _try("param_count", lambda: plugins.getParamCount(index, slot))  # type: ignore[name-defined]
    # Sample a few parameter names if count worked
    if isinstance(info.get("param_count"), int):
        names = []
        for i in range(min(info["param_count"], 80)):
            try:
                names.append(plugins.getParamName(i, index, slot))  # type: ignore[name-defined]
            except Exception as exc:
                names.append("ERR:{0}".format(exc))
                break
        info["param_names"] = names
    return "ok", json.dumps(info, sort_keys=True), None


def _handle_set_mixer_eq(args):
    """Set one band of an insert's built-in 3-band EQ.

    Targets ``mixer.setEqFrequency / setEqGain / setEqBandwidth``. Band
    indexing is 0-2 (low/mid/high) per FL's scripting convention.
    """
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    try:
        idx = int(args.get("idx"))
        band = int(args.get("band"))
        freq = args.get("frequency")
        gain = args.get("gain")
        bw = args.get("bandwidth")
    except (TypeError, ValueError):
        return "error", "idx/band missing or non-int", None
    try:
        if freq is not None:
            mixer.setEqFrequency(idx, band, float(freq))  # type: ignore[name-defined]
        if gain is not None:
            mixer.setEqGain(idx, band, float(gain))  # type: ignore[name-defined]
        if bw is not None:
            mixer.setEqBandwidth(idx, band, float(bw))  # type: ignore[name-defined]
    except Exception as exc:
        return "error", "mixer.setEq*({0},{1}): {2}".format(idx, band, exc), None
    return "ok", "insert {0} band {1} updated".format(idx, band), None


def _handle_set_pattern_name(args):
    """Set pattern name via ``patterns.setPatternName``."""
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    try:
        iid = int(args.get("iid"))
    except (TypeError, ValueError):
        return "error", "iid missing or not int", None
    name = args.get("name")
    if not isinstance(name, str):
        return "error", "name arg missing or not a string", None
    try:
        patterns.setPatternName(iid, name)  # type: ignore[name-defined]
    except Exception as exc:
        return "error", "setPatternName({0},{1!r}): {2}".format(iid, name, exc), None
    return "ok", "pattern {0} renamed to {1!r}".format(iid, name), None


def _handle_set_step(args):
    """Toggle a step-sequencer step on/off via ``channels.setGridBit``.

    Args:
        channel:    channel-rack index (0-based).
        step:       step index inside the current pattern (0-based, typically 0..15).
        on:         bool; True = lit step, False = empty.
    """
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    try:
        channel = int(args.get("channel"))
        step = int(args.get("step"))
        on = bool(args.get("on"))
    except (TypeError, ValueError):
        return "error", "channel/step/on missing or wrong type", None
    try:
        channels.setGridBit(channel, step, 1 if on else 0)  # type: ignore[name-defined]
    except Exception as exc:
        return "error", "setGridBit({0},{1}): {2}".format(channel, step, exc), None
    return "ok", "channel {0} step {1} {2}".format(channel, step, "on" if on else "off"), None


def _handle_get_pattern_steps(args):
    """Read all step-sequencer bits + per-step velocities for one channel.

    Args:
        channel:    channel-rack index (0-based).
        count:      number of steps to read (default 16).

    Returns: JSON ``{"channel": int, "steps": [{"step": i, "on": bool}, ...]}``.
    """
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    try:
        channel = int(args.get("channel"))
        count = int(args.get("count", 16))
    except (TypeError, ValueError):
        return "error", "channel/count missing or wrong type", None
    if count <= 0 or count > 256:
        return "error", "count must be 1..256", None
    out_steps = []
    for i in range(count):
        try:
            bit = channels.getGridBit(channel, i)  # type: ignore[name-defined]
        except Exception as exc:
            return "error", "getGridBit({0},{1}): {2}".format(channel, i, exc), None
        out_steps.append({"step": i, "on": bool(bit)})
    payload = json.dumps({"channel": channel, "count": count, "steps": out_steps},
                         sort_keys=True)
    return "ok", payload, None


def _handle_clear_pattern_steps(args):
    """Set every step in [0..count) to OFF for the given channel.

    Args:
        channel:    channel-rack index (0-based).
        count:      number of steps to clear (default 16).
    """
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    try:
        channel = int(args.get("channel"))
        count = int(args.get("count", 16))
    except (TypeError, ValueError):
        return "error", "channel/count missing or wrong type", None
    if count <= 0 or count > 256:
        return "error", "count must be 1..256", None
    cleared = 0
    for i in range(count):
        try:
            channels.setGridBit(channel, i, 0)  # type: ignore[name-defined]
            cleared += 1
        except Exception as exc:
            return "error", "setGridBit({0},{1}): {2}".format(channel, i, exc), None
    return "ok", "cleared {0} steps on channel {1}".format(cleared, channel), None


def _handle_save(args):
    """Trigger FL's 'Save' action. Saves to the currently-open file path.

    Path-aware save-as isn't exposed in FL's scripting API (we checked:
    general.getCurrentFilename doesn't exist either), so the orchestrator
    drives this by opening a specific FLP before calling save, and
    reading back from that same path afterwards.

    FPT_Save == 50 per Image-Line's transport constants.
    """
    if not INSIDE_FL:
        return "unsupported", "not running inside FL Studio", None
    try:
        transport.globalTransport(50, 1)  # type: ignore[name-defined]
        return "ok", "save triggered via globalTransport(FPT_Save)", None
    except Exception as exc:
        return "error", f"save failed: {exc}", None


_HANDLERS = {
    "noop": _handle_noop,
    "describe": _handle_describe,
    "list_apis": _handle_list_apis,
    "get_tempo": _handle_get_tempo,
    "set_tempo": _handle_set_tempo,
    "set_time_signature": _handle_set_time_signature,
    "set_channel_volume": _handle_set_channel_volume,
    "set_channel_name": _handle_set_channel_name,
    "set_insert_volume": _handle_set_insert_volume,
    "set_insert_name": _handle_set_insert_name,
    "set_pattern_name": _handle_set_pattern_name,
    "set_mixer_eq": _handle_set_mixer_eq,
    "set_plugin_param": _handle_set_plugin_param,
    "get_plugin_info": _handle_get_plugin_info,
    "save": _handle_save,
    # Phase 2.3 — step-sequencer ops (channels.setGridBit/getGridBit)
    "set_step": _handle_set_step,
    "get_pattern_steps": _handle_get_pattern_steps,
    "clear_pattern_steps": _handle_clear_pattern_steps,
}


def _handle(cmd):
    kind = cmd.get("kind")
    handler = _HANDLERS.get(kind)
    if handler is None:
        return "unsupported", f"no handler for kind={kind}", None
    return handler(cmd.get("args") or {})


# --------------------------------------------------------------------------- #
# FL Studio callbacks
# --------------------------------------------------------------------------- #

# Throttle polling so we don't hammer the filesystem.
_LAST_POLL = {"t": 0.0}
_POLL_INTERVAL = 0.05  # seconds


def _pending_commands():
    """List inbox entries that don't yet have a matching result.

    Because we can't rename inbox → processed inside FL's sandbox,
    processed commands pile up in inbox/. This filter is what keeps the
    poll loop from re-running them.
    """
    if not os.path.exists(INBOX_DIR):
        return []
    out = []
    for name in sorted(os.listdir(INBOX_DIR)):
        if not (name.startswith("cmd_") and name.endswith(".json")):
            continue
        cmd_id = name[len("cmd_"):-len(".json")]
        if os.path.exists(os.path.join(OUTBOX_DIR, f"result_{cmd_id}.json")):
            continue
        out.append(os.path.join(INBOX_DIR, name))
    return out


def _process_once():
    for cmd_path in _pending_commands():
        try:
            raw = _read_command(cmd_path)
        except Exception as exc:
            cmd_id = os.path.basename(cmd_path)[4:-5]
            _write_result(cmd_id, "error", f"malformed command: {exc}")
            _mark_processed(cmd_id)
            continue
        cmd_id = raw.get("id", "?")
        status, detail, artifact_path = _handle(raw)
        _write_result(cmd_id, status, detail, artifact_path)
        _mark_processed(cmd_id)


def OnInit():
    try:
        _ensure_dirs()
    except Exception:
        # Printing a real traceback beats FL's C-level "FileIO returned
        # NULL" message. If this fires, the problem is filesystem
        # permissions — read the trace to see which path.
        try:
            print("[flstudio-mcp] startup failure:\n" + traceback.format_exc())
        except Exception:
            pass
        return 0
    try:
        print(f"[flstudio-mcp] started, polling {INBOX_DIR}")
    except Exception:
        pass
    return 0


def OnDeInit():
    try:
        print("[flstudio-mcp] stopped")
    except Exception:
        pass
    return 0


def OnIdle():
    now = time.time()
    if now - _LAST_POLL["t"] < _POLL_INTERVAL:
        return
    _LAST_POLL["t"] = now
    try:
        _process_once()
    except Exception:
        try:
            print("[flstudio-mcp] processing error:\n" + traceback.format_exc())
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Standalone syntax check
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # Allow `python device_flpdiff_harness.py` to be a dumb syntax-check.
    print("INSIDE_FL=", INSIDE_FL)
    print("INBOX_ROOT=", INBOX_ROOT)
    sys.exit(0)
