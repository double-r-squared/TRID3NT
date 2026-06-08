"""Tests for the tool payload-warning envelopes (job-0127).

Covers:
- Round-trip serialization for both envelopes (JSON idempotence).
- ``options`` invariants (uniqueness, non-empty subset).
- ``decision``/``revised_args`` cross-field rule on the confirmation
  envelope.
- Hard-cap shape: a warning where ``proceed`` is omitted from ``options``.
- Registry wiring: both envelopes are reachable via
  ``CLIENT_TO_AGENT_PAYLOADS`` / ``AGENT_TO_CLIENT_PAYLOADS`` /
  ``ALL_PAYLOADS``.
- Invariant 9 (no cost theater): neither envelope carries cost / dollar
  / latency / quota fields.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from grace2_contracts import new_ulid
from grace2_contracts.payload_warning import (
    HARD_CAP_MB_DEFAULT,
    WARNING_THRESHOLD_MB_DEFAULT,
    PayloadConfirmationEnvelopePayload,
    PayloadWarningEnvelopePayload,
)
from grace2_contracts.ws import (
    AGENT_TO_CLIENT_PAYLOADS,
    ALL_PAYLOADS,
    CLIENT_TO_AGENT_PAYLOADS,
)


# --- Envelope round-trip ---------------------------------------------------- #


def test_warning_envelope_round_trips_through_json() -> None:
    """A populated warning envelope serializes and re-parses identically."""
    wid = new_ulid()
    env = PayloadWarningEnvelopePayload(
        warning_id=wid,
        tool_name="fetch_nexrad_reflectivity",
        tool_args={"bbox": [-82.5, 26.5, -82.0, 27.0], "bands": ["reflectivity"]},
        estimated_mb=87.3,
        threshold_mb=WARNING_THRESHOLD_MB_DEFAULT,
        recommendation="Consider narrowing the bbox to a single county.",
        alternative_args={"bbox": [-82.2, 26.7, -82.1, 26.8]},
        options=["proceed", "cancel", "narrow_scope"],
    )
    wire = env.model_dump(mode="json")
    blob = json.dumps(wire)
    rt = PayloadWarningEnvelopePayload.model_validate(json.loads(blob))
    assert rt.model_dump(mode="json") == wire
    # Defaults preserved
    assert rt.envelope_type == "tool-payload-warning"
    assert rt.ttl_seconds == 300


def test_confirmation_envelope_round_trips_through_json() -> None:
    """A populated confirmation envelope serializes and re-parses identically."""
    wid = new_ulid()
    env = PayloadConfirmationEnvelopePayload(
        warning_id=wid,
        decision="narrow_scope",
        revised_args={"bbox": [-82.2, 26.7, -82.1, 26.8]},
    )
    wire = env.model_dump(mode="json")
    blob = json.dumps(wire)
    rt = PayloadConfirmationEnvelopePayload.model_validate(json.loads(blob))
    assert rt.model_dump(mode="json") == wire
    assert rt.envelope_type == "tool-payload-confirmation"


# --- Cross-field rules ------------------------------------------------------ #


def test_confirmation_narrow_scope_requires_revised_args() -> None:
    """``decision='narrow_scope'`` without ``revised_args`` is rejected."""
    with pytest.raises(ValidationError):
        PayloadConfirmationEnvelopePayload(
            warning_id=new_ulid(),
            decision="narrow_scope",
            revised_args=None,
        )


def test_confirmation_proceed_forbids_revised_args() -> None:
    """``decision='proceed'`` with revised_args is a contract violation."""
    with pytest.raises(ValidationError):
        PayloadConfirmationEnvelopePayload(
            warning_id=new_ulid(),
            decision="proceed",
            revised_args={"bbox": [-82.2, 26.7, -82.1, 26.8]},
        )


def test_confirmation_cancel_forbids_revised_args() -> None:
    """``decision='cancel'`` with revised_args is a contract violation."""
    with pytest.raises(ValidationError):
        PayloadConfirmationEnvelopePayload(
            warning_id=new_ulid(),
            decision="cancel",
            revised_args={"anything": "here"},
        )


def test_confirmation_narrow_scope_with_empty_dict_is_legal() -> None:
    """``revised_args={}`` is dispatch-able (no narrowing parameter changed)."""
    env = PayloadConfirmationEnvelopePayload(
        warning_id=new_ulid(),
        decision="narrow_scope",
        revised_args={},
    )
    assert env.revised_args == {}


# --- Warning options invariants -------------------------------------------- #


def test_warning_rejects_duplicate_options() -> None:
    """Duplicates would render duplicate buttons — refused at the contract."""
    with pytest.raises(ValidationError):
        PayloadWarningEnvelopePayload(
            warning_id=new_ulid(),
            tool_name="fetch_dem",
            tool_args={"bbox": [0, 0, 1, 1]},
            estimated_mb=50.0,
            threshold_mb=25.0,
            recommendation="narrow bbox",
            options=["proceed", "proceed", "cancel"],
        )


def test_warning_rejects_empty_options() -> None:
    """``options`` must offer the user at least one action."""
    with pytest.raises(ValidationError):
        PayloadWarningEnvelopePayload(
            warning_id=new_ulid(),
            tool_name="fetch_dem",
            tool_args={"bbox": [0, 0, 1, 1]},
            estimated_mb=50.0,
            threshold_mb=25.0,
            recommendation="narrow bbox",
            options=[],
        )


def test_warning_hard_cap_shape_omits_proceed() -> None:
    """At the hard cap, ``proceed`` is removed; ``cancel`` and ``narrow_scope``
    remain."""
    env = PayloadWarningEnvelopePayload(
        warning_id=new_ulid(),
        tool_name="fetch_nexrad_reflectivity",
        tool_args={"bbox": [-100, 30, -90, 40]},
        estimated_mb=HARD_CAP_MB_DEFAULT + 1.0,
        threshold_mb=HARD_CAP_MB_DEFAULT,
        recommendation="Hard cap exceeded; narrow scope or cancel.",
        alternative_args={"bbox": [-95, 35, -94, 36]},
        options=["cancel", "narrow_scope"],
    )
    wire = env.model_dump(mode="json")
    assert "proceed" not in wire["options"]
    assert "cancel" in wire["options"]
    assert "narrow_scope" in wire["options"]


# --- Numeric bounds --------------------------------------------------------- #


def test_warning_rejects_negative_estimated_mb() -> None:
    """A negative payload estimate is non-sensical."""
    with pytest.raises(ValidationError):
        PayloadWarningEnvelopePayload(
            warning_id=new_ulid(),
            tool_name="fetch_dem",
            tool_args={},
            estimated_mb=-1.0,
            threshold_mb=25.0,
            recommendation="narrow",
        )


def test_warning_rejects_negative_threshold_mb() -> None:
    """A negative threshold would gate every call — refuse at the contract."""
    with pytest.raises(ValidationError):
        PayloadWarningEnvelopePayload(
            warning_id=new_ulid(),
            tool_name="fetch_dem",
            tool_args={},
            estimated_mb=10.0,
            threshold_mb=-1.0,
            recommendation="narrow",
        )


def test_warning_recommendation_is_length_capped() -> None:
    """Recommendation is capped at 512 chars (mirrors PipelineStepSummary)."""
    with pytest.raises(ValidationError):
        PayloadWarningEnvelopePayload(
            warning_id=new_ulid(),
            tool_name="fetch_dem",
            tool_args={},
            estimated_mb=50.0,
            threshold_mb=25.0,
            recommendation="x" * 513,
        )


# --- Registry wiring -------------------------------------------------------- #


def test_warning_envelope_registered_in_agent_to_client() -> None:
    """Warnings flow agent -> client; the registry must reflect that."""
    assert (
        AGENT_TO_CLIENT_PAYLOADS["tool-payload-warning"]
        is PayloadWarningEnvelopePayload
    )
    assert "tool-payload-warning" in ALL_PAYLOADS
    assert "tool-payload-warning" not in CLIENT_TO_AGENT_PAYLOADS


def test_confirmation_envelope_registered_in_client_to_agent() -> None:
    """Confirmations flow client -> agent; the registry must reflect that."""
    assert (
        CLIENT_TO_AGENT_PAYLOADS["tool-payload-confirmation"]
        is PayloadConfirmationEnvelopePayload
    )
    assert "tool-payload-confirmation" in ALL_PAYLOADS
    assert "tool-payload-confirmation" not in AGENT_TO_CLIENT_PAYLOADS


# --- Invariant 9 (no cost theater) ----------------------------------------- #


def test_warning_envelope_carries_no_cost_field() -> None:
    """Invariant 9: no cost / dollar / latency / quota field anywhere."""
    fields = PayloadWarningEnvelopePayload.model_fields
    banned = {"cost", "dollar", "dollars", "price", "quota", "latency_ms", "usd"}
    assert banned.isdisjoint(fields.keys())


def test_confirmation_envelope_carries_no_cost_field() -> None:
    """Invariant 9: no cost / dollar / latency / quota field anywhere."""
    fields = PayloadConfirmationEnvelopePayload.model_fields
    banned = {"cost", "dollar", "dollars", "price", "quota", "latency_ms", "usd"}
    assert banned.isdisjoint(fields.keys())


# --- Default thresholds ---------------------------------------------------- #


def test_default_thresholds_are_sane() -> None:
    """Sanity check: warning < hard cap; both positive."""
    assert 0.0 < WARNING_THRESHOLD_MB_DEFAULT < HARD_CAP_MB_DEFAULT
    assert WARNING_THRESHOLD_MB_DEFAULT == 25.0
    assert HARD_CAP_MB_DEFAULT == 250.0
