"""Locate the installed FL Studio app bundle (macOS)."""

from __future__ import annotations

import os
import re
from pathlib import Path

APPLICATIONS = Path("/Applications")
FL_APP_ENV = "FLSTUDIO_MCP_FL_APP"
_FL_APP_RE = re.compile(r"^FL Studio (\d+)\.app$")


def resolve_fl_app(fl_app: Path | None = None) -> Path:
    """Explicit path, else ``FLSTUDIO_MCP_FL_APP``, else the newest /Applications/FL Studio N.app.

    With nothing installed the returned path doesn't exist, so callers
    report "not found" instead of silently using a stale version.
    """
    if fl_app is not None:
        return fl_app
    env = os.environ.get(FL_APP_ENV)
    if env:
        return Path(env)
    installed = sorted(
        (
            (int(m.group(1)), path)
            for path in APPLICATIONS.glob("FL Studio *.app")
            if (m := _FL_APP_RE.match(path.name))
        ),
        reverse=True,
    )
    return installed[0][1] if installed else APPLICATIONS / "FL Studio.app"
