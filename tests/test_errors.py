"""Tests for the error code taxonomy + ToolError envelope."""

from __future__ import annotations

import pytest

from flstudio_mcp.errors import ErrorCode, ToolError, hint_for


def test_error_codes_are_strings() -> None:
    assert ErrorCode.FL_NOT_RUNNING.value == "FL_NOT_RUNNING"
    assert str(ErrorCode.IPC_TIMEOUT) == "IPC_TIMEOUT"


def test_default_hints_exist_for_every_code() -> None:
    for code in ErrorCode:
        # Hint may be empty string but must be defined (no KeyError)
        _ = hint_for(code)


@pytest.mark.parametrize(
    "code",
    [
        ErrorCode.FL_NOT_RUNNING,
        ErrorCode.MIDI_SCRIPT_NOT_LOADED,
        ErrorCode.IAC_DRIVER_OFFLINE,
        ErrorCode.IPC_TIMEOUT,
        ErrorCode.UNSUPPORTED_KIND,
        ErrorCode.FL_DIALOG_BLOCKING,
        ErrorCode.SNAPSHOT_FAILED,
        ErrorCode.PREFLIGHT_FAILED,
    ],
)
def test_actionable_hints_non_empty_for_user_facing_codes(code: ErrorCode) -> None:
    """Codes the LLM is expected to act on must have a default hint."""
    assert hint_for(code), f"missing hint for {code}"


def test_tool_error_to_result_uses_default_hint() -> None:
    err = ToolError(ErrorCode.FL_NOT_RUNNING, "FL closed")
    result = err.to_result()
    assert result["error"] == "FL_NOT_RUNNING"
    assert result["message"] == "FL closed"
    assert "Launch FL Studio" in result["hint"]


def test_tool_error_to_result_overrides_hint() -> None:
    err = ToolError(ErrorCode.FL_NOT_RUNNING, "FL closed", hint="custom hint")
    assert err.to_result()["hint"] == "custom hint"


def test_tool_error_to_result_explicit_empty_hint() -> None:
    err = ToolError(ErrorCode.UNKNOWN, "weird", hint="")
    assert err.to_result()["hint"] == ""


def test_tool_error_to_result_includes_extra() -> None:
    err = ToolError(
        ErrorCode.UNSUPPORTED_KIND,
        "unknown kind",
        extra={"supported": ["a", "b"]},
    )
    assert err.to_result()["extra"] == {"supported": ["a", "b"]}


def test_tool_error_is_an_exception() -> None:
    err = ToolError(ErrorCode.FL_NOT_RUNNING, "boom")
    assert isinstance(err, Exception)
    assert "FL_NOT_RUNNING: boom" in str(err)


def test_tool_error_can_be_raised_and_caught() -> None:
    with pytest.raises(ToolError) as exc_info:
        raise ToolError(ErrorCode.IPC_TIMEOUT, "no response")
    assert exc_info.value.code == ErrorCode.IPC_TIMEOUT
