"""Shared fixtures + gating for the e2e harness.

Two gates:

1. ``FLSTUDIO_MCP_E2E=1`` — opt-in. Without it, every test under this
   directory is skipped (so a regular ``pytest`` from the repo root
   doesn't burn API tokens).
2. ``ANTHROPIC_API_KEY`` — required for the agent + judge calls.
   Tests under ``unit/`` are exempt (they ship a scripted responder
   and never hit the API).

Self-tests in ``tests/e2e/unit/`` run unconditionally so any harness
regression is caught in normal CI.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

E2E_GATE_ENV = "FLSTUDIO_MCP_E2E"
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _is_unit_test(node: pytest.Item) -> bool:
    return "tests/e2e/unit/" in str(node.fspath)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip API-burning tests when gates are missing.

    Unit tests under ``tests/e2e/unit/`` are exempt — they have no
    external dependency.
    """
    e2e_enabled = os.environ.get(E2E_GATE_ENV) == "1"
    has_api_key = bool(os.environ.get(ANTHROPIC_KEY_ENV))
    skip_no_gate = pytest.mark.skip(reason=f"set {E2E_GATE_ENV}=1 to run e2e LLM tests")
    skip_no_key = pytest.mark.skip(reason=f"set {ANTHROPIC_KEY_ENV} to run e2e LLM tests")
    for item in items:
        if "tests/e2e/" not in str(item.fspath):
            continue
        if _is_unit_test(item):
            continue
        if not e2e_enabled:
            item.add_marker(skip_no_gate)
        elif not has_api_key:
            item.add_marker(skip_no_key)


@pytest.fixture
def scratch_flp(tmp_path: Path) -> Path:
    """A writable copy directory for FLP fixtures."""
    out = tmp_path / "flp"
    out.mkdir()
    return out
