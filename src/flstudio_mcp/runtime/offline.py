"""Offline runtime — spawns the flpdiff TS bridge over a child process.

Per MCP-SPEC.md decision #21, the canonical FL-format implementation
lives in TypeScript (the `flpdiff` repo). This module does NOT
re-parse FL format in Python; it spawns `flpdiff bridge` (or the
configured equivalent) and forwards JSON in/out.

Bridge resolution order (each candidate is tried until one works):
  1. ``FLSTUDIO_MCP_BRIDGE_CMD`` env var — exact command, e.g.
     ``"bun run /path/to/flpdiff/src/cli.ts bridge"``. Split on
     whitespace.
  2. Sibling dev workspace at ``../flpdiff/src/cli.ts`` invoked via
     ``bun``. Found by walking up from this file.
  3. ``flpdiff`` binary on PATH (when the published npm CLI is
     installed via ``npm i -g flpdiff``).

Spawn cost ~80-200 ms cold per call (decision #22) — acceptable for
offline ops which aren't latency-critical. A long-lived bridge
worker is a v1.x optimization.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..logging_setup import get_logger

DEFAULT_TIMEOUT_S = 8.0  # tightened from 15s — bridge cold-start is ~25ms, real work <2s
BRIDGE_ENV_VAR = "FLSTUDIO_MCP_BRIDGE_CMD"

_LOG = get_logger("runtime.offline")


class OfflineRuntimeError(RuntimeError):
    """Raised when the bridge subprocess fails before producing JSON."""


class NodeNotFoundError(OfflineRuntimeError):
    """No usable bridge command discovered (no env override, no bun, no flpdiff)."""


@dataclass(frozen=True)
class BridgeResponse:
    ok: bool
    kind: str
    result: Any | None = None
    error: str | None = None
    message: str | None = None
    raw: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BridgeResponse:
        return cls(
            ok=bool(data.get("ok")),
            kind=str(data.get("kind", "?")),
            result=data.get("result"),
            error=data.get("error"),
            message=data.get("message"),
            raw=data,
        )


def _walk_up_for_flpdiff(start: Path) -> Path | None:
    """Find the flpdiff/src/cli.ts dev checkout by walking parents."""
    for parent in start.parents:
        candidate = parent / "flpdiff" / "src" / "cli.ts"
        if candidate.is_file():
            return candidate
    return None


def discover_bridge_cmd() -> Sequence[str]:
    """Return the argv to invoke the bridge with `<argv> bridge` semantics.

    Note: returned argv ALREADY ends with the appropriate "bridge"
    token where applicable, so callers just spawn it as-is.
    """
    env_override = os.environ.get(BRIDGE_ENV_VAR)
    if env_override and env_override.strip():
        return env_override.split()

    here = Path(__file__).resolve()
    dev_cli = _walk_up_for_flpdiff(here)
    if dev_cli is not None:
        bun = shutil.which("bun")
        if bun:
            return [bun, "run", str(dev_cli), "bridge"]

    flpdiff_on_path = shutil.which("flpdiff")
    if flpdiff_on_path:
        return [flpdiff_on_path, "bridge"]

    raise NodeNotFoundError(
        "no flpdiff bridge available. Set "
        f"{BRIDGE_ENV_VAR}=<command...> OR install bun + sibling "
        "flpdiff workspace OR `npm i -g flpdiff`."
    )


@dataclass
class OfflineRuntime:
    cmd: Sequence[str]
    timeout_s: float = DEFAULT_TIMEOUT_S

    def call(self, kind: str, args: dict[str, Any] | None = None) -> BridgeResponse:
        """Send {kind, args} to the bridge subprocess, parse stdout JSON."""
        request = json.dumps({"kind": kind, "args": args or {}})
        # Diagnostic: track open-fd count + spawn latency so a future
        # bridge hang has forensics. Reproducible-after-N-calls bugs
        # almost always look like a leak somewhere on the path.
        try:
            import resource

            soft_fd_limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        except Exception:
            soft_fd_limit = -1
        _LOG.info(
            "offline call",
            extra={
                "kind": kind,
                "cmd": list(self.cmd),
                "args_keys": sorted((args or {}).keys()),
                "request_bytes": len(request),
                "soft_fd_limit": soft_fd_limit,
            },
        )
        spawn_t0 = time.monotonic()
        try:
            proc = subprocess.run(
                self.cmd,
                input=request,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OfflineRuntimeError(
                f"bridge timed out after {self.timeout_s}s for kind={kind!r} "
                f"(spawn-to-timeout took {time.monotonic() - spawn_t0:.2f}s; "
                f"check `lsof -p $(pgrep -f flstudio-mcp)` for fd accumulation)"
            ) from exc
        except FileNotFoundError as exc:
            raise NodeNotFoundError(f"bridge command not executable: {' '.join(self.cmd)}") from exc

        spawn_ms = round((time.monotonic() - spawn_t0) * 1000.0, 1)
        _LOG.info(
            "offline call done",
            extra={
                "kind": kind,
                "spawn_ms": spawn_ms,
                "stdout_bytes": len(proc.stdout),
                "stderr_bytes": len(proc.stderr),
                "returncode": proc.returncode,
            },
        )
        if proc.returncode != 0:
            raise OfflineRuntimeError(
                f"bridge exit={proc.returncode} stderr={proc.stderr.strip()[:300]}"
            )

        out = proc.stdout.strip()
        if not out:
            raise OfflineRuntimeError("bridge returned empty stdout")

        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise OfflineRuntimeError(f"bridge stdout not JSON: {out[:200]!r}") from exc
        if not isinstance(data, dict):
            raise OfflineRuntimeError(f"bridge stdout not a JSON object: {type(data).__name__}")
        return BridgeResponse.from_dict(data)


def default_runtime(timeout_s: float = DEFAULT_TIMEOUT_S) -> OfflineRuntime:
    """Build an OfflineRuntime using the discovered bridge command."""
    return OfflineRuntime(cmd=discover_bridge_cmd(), timeout_s=timeout_s)


def check_bridge_available() -> tuple[bool, str]:
    """Quick pre-flight: is a bridge command available? Returns (ok, detail)."""
    try:
        cmd = discover_bridge_cmd()
    except NodeNotFoundError as exc:
        return False, str(exc)
    return True, f"bridge cmd: {' '.join(cmd)}"
