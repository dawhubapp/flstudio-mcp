"""Locate and add ``re_harness`` to ``sys.path``.

v0.1 imports the re_harness package from the sibling ``python/tools/``
directory in the dawhub workspace. v1.0 will vendor it under
``mcp/vendor/`` (per MCP-SPEC.md line 151), at which point this module
can be deleted.

Resolution order:

1. ``FLSTUDIO_MCP_RE_HARNESS`` env var (path to dir containing
   ``re_harness/``).
2. Dev layout: walk up from this file until a sibling ``python/tools``
   exists with ``re_harness/ipc.py`` inside.

Importers should call :func:`ensure_re_harness_on_path` before importing
``re_harness``. Callers that legitimately can't reach re_harness (e.g.
running unit tests on CI without the dawhub workspace) should catch
:class:`ReHarnessNotAvailable`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


class ReHarnessNotAvailable(RuntimeError):
    """Raised when re_harness cannot be located on disk."""


def _candidate_paths() -> list[Path]:
    candidates: list[Path] = []
    env = os.environ.get("FLSTUDIO_MCP_RE_HARNESS")
    if env:
        candidates.append(Path(env).expanduser().resolve())

    here = Path(__file__).resolve()
    for parent in here.parents:
        guess = parent / "python" / "tools"
        if (guess / "re_harness" / "ipc.py").exists():
            candidates.append(guess)
            break

    return candidates


def ensure_re_harness_on_path() -> Path:
    """Add the directory containing ``re_harness`` to ``sys.path``.

    Returns the resolved directory. Raises :class:`ReHarnessNotAvailable`
    if no candidate worked.
    """
    for candidate in _candidate_paths():
        ipc_file = candidate / "re_harness" / "ipc.py"
        if not ipc_file.exists():
            continue
        candidate_str = str(candidate)
        if candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)
        return candidate

    raise ReHarnessNotAvailable(
        "re_harness package not found. Set FLSTUDIO_MCP_RE_HARNESS to the "
        "directory containing re_harness/, or run from the dawhub workspace "
        "where ../python/tools/re_harness/ exists."
    )
