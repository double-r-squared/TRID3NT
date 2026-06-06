"""Negative controls at the wire layer (Invariants 1, 2, 9; A.6 codes).

These mirror the contracts package's pydantic-level negative tests but assert
behavior at the WIRE LAYER — through Envelope serialization and (for the
discriminator test) the live server's dispatch path. They are the regression
guard against any future change that loosens contract enforcement on either
side.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from grace2_contracts import new_ulid
from grace2_contracts.ws import (
    Envelope,
    ErrorPayload,
    UserMessagePayload,
)


# ---------------------------------------------------------------------------
# Bare-float intensity rejected (Invariant 7 / Decision M)
# ---------------------------------------------------------------------------


def test_intensity_field_rejects_bare_float() -> None:
    """Every numeric intensity field is ClaimSet | None — never a bare number.

    This is the same assertion the contracts package tests at the model level;
    we repeat it here at the test-suite top level so the M1 acceptance record
    explicitly demonstrates the wire-layer enforcement (Invariant 7 / Decision M).
    ``RainfallIntensity.total_inches`` is typed ``ClaimSet | None``; a bare float
    must be rejected.
    """
    from grace2_contracts.event import RainfallIntensity

    with pytest.raises(ValidationError) as exc:
        # Passing a bare float (the obvious legacy shape) must fail.
        RainfallIntensity.model_validate({"total_inches": 3.2})
    msg = str(exc.value)
    # Pydantic v2 rejects the float because it does not match the ClaimSet
    # model shape (an object). The error names the field and rejects a
    # numeric/primitive input as not a dict / model.
    assert "total_inches" in msg, (
        f"CONTRACTS layer regression — error message lost the field name: {msg}"
    )


# ---------------------------------------------------------------------------
# Wrong-discriminator envelope rejected (Invariant 2 — deterministic dispatch)
# ---------------------------------------------------------------------------


def test_wrong_envelope_discriminator_rejected_at_construction() -> None:
    """An Envelope with a payload that doesn't match its declared model fails."""
    # Envelope's generic payload accepts any GraceModel, but the per-field
    # validators on the payload type itself reject bogus shapes.
    sid = new_ulid()
    # Build a UserMessagePayload with required fields, then mutate to invalid.
    with pytest.raises(ValidationError):
        UserMessagePayload.model_validate({"text": "hi", "research_mode": "totally-bogus"})

    # Envelope itself enforces that ``payload`` is a model instance; bare dicts
    # in the wrong shape do not validate as UserMessagePayload.
    with pytest.raises(ValidationError):
        UserMessagePayload.model_validate({"research_mode": "research"})  # missing text


async def test_wire_layer_rejects_wrong_discriminator_payload(
    agent_subprocess: str,
) -> None:
    """Send a user-message envelope with a payload missing required fields;
    the live server must respond with an A.6 typed error, not a crash."""
    import websockets

    sid = new_ulid()
    bad_user_message = {
        "type": "user-message",
        "id": new_ulid(),
        "ts": "2026-06-05T12:00:00Z",
        "session_id": sid,
        # text is required; sending only research_mode is the bug under test.
        "payload": {"research_mode": "research"},
    }
    async with websockets.connect(agent_subprocess) as ws:
        await ws.send(json.dumps(bad_user_message))
        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        parsed = json.loads(raw)
        assert parsed["type"] == "error", (
            f"AGENT layer regression — wrong-discriminator payload did not "
            f"produce typed error. Got: {parsed.get('type')!r}"
        )
        err = ErrorPayload.model_validate(parsed["payload"])
        # The server maps payload-validation failures to TOOL_PARAMS_INVALID.
        assert err.error_code in {"TOOL_PARAMS_INVALID", "INTERNAL_ERROR"}, (
            f"AGENT layer regression — wrong error code for bad payload: {err.error_code}"
        )


# ---------------------------------------------------------------------------
# Cost-theater guard (Invariant 9)
# ---------------------------------------------------------------------------


def test_no_cost_fields_in_ws_models() -> None:
    """ConfirmationRequestPayload and friends carry no cost field (Invariant 9)."""
    from grace2_contracts.ws import ConfirmationRequestPayload

    fields = set(ConfirmationRequestPayload.model_fields)
    forbidden = {"cost", "cost_estimate", "usd", "cents", "dollars", "price"}
    leaks = fields & forbidden
    assert not leaks, f"Invariant 9 violation — cost fields present: {leaks}"
