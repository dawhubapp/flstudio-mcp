"""Shared issue record for Tier 0 beat checks (symbolic + audio)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class Issue:
    """One finding. ``error`` fails a check; ``warning``/``info`` don't."""

    severity: Severity
    message: str
    hint: str = ""
    role: str | None = None
    section: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict without empty optional fields."""
        return {k: v for k, v in asdict(self).items() if v not in (None, "")}
