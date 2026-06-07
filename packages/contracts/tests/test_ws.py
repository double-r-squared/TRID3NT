"""Round-trip + negative tests for the Appendix A WebSocket protocol (ws.py).

Every message type listed in ``ws.ALL_PAYLOADS`` (Appendix A.3, A.4, A.4b) is
exercised: a real instance is built, dumped to JSON via the Envelope, parsed
back, and re-dumped — both passes must be byte-identical (idempotent).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from grace2_contracts import ws
from grace2_contracts.common import GraceModel, new_ulid


def _wrap(payload: GraceModel, session_id: str) -> ws.Envelope:
    msg_type = getattr(payload, "MESSAGE_TYPE")
    return ws.Envelope[type(payload)](
        type=msg_type,
        session_id=session_id,
        payload=payload,
    )


def _roundtrip_idempotent(envelope: ws.Envelope) -> dict[str, Any]:
    """Serialize -> JSON text -> dict -> re-validate -> serialize. Both passes
    must match byte-for-byte.
    """
    dumped_a = envelope.model_dump(mode="json")
    text_a = json.dumps(dumped_a, sort_keys=True)
    # Real JSON round-trip via text
    loaded = json.loads(text_a)
    envelope_b = type(envelope).model_validate(loaded)
    dumped_b = envelope_b.model_dump(mode="json")
    text_b = json.dumps(dumped_b, sort_keys=True)
    assert text_a == text_b, "JSON round-trip not idempotent"
    return dumped_a


# --------------------------------------------------------------------------- #
# Client -> Agent (A.3)
# --------------------------------------------------------------------------- #


def test_user_message_default_research_mode(session_id: str) -> None:
    """A.3 user-message with the FR-WC-15 research_mode amendment, default value."""
    payload = ws.UserMessagePayload(text="Model the flooding from Hurricane Ian in Fort Myers")
    dumped = _roundtrip_idempotent(_wrap(payload, session_id))
    assert dumped["type"] == "user-message"
    assert dumped["payload"]["research_mode"] == "research"


def test_user_message_deep_research_mode(session_id: str) -> None:
    payload = ws.UserMessagePayload(
        text="Run a deep sweep on the 2024 atmospheric river sequence",
        research_mode="deep_research",
    )
    dumped = _roundtrip_idempotent(_wrap(payload, session_id))
    assert dumped["payload"]["research_mode"] == "deep_research"


def test_user_message_unknown_research_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        ws.UserMessagePayload(text="hi", research_mode="extra_deep")  # type: ignore[arg-type]


def test_cancel_message(session_id: str) -> None:
    payload = ws.CancelPayload(reason="user-requested")
    dumped = _roundtrip_idempotent(_wrap(payload, session_id))
    assert dumped["type"] == "cancel"


def test_confirm_response(session_id: str) -> None:
    payload = ws.ConfirmResponsePayload(request_id=new_ulid(), approved=True)
    _roundtrip_idempotent(_wrap(payload, session_id))


def test_session_resume_empty_payload(session_id: str) -> None:
    payload = ws.SessionResumePayload()
    dumped = _roundtrip_idempotent(_wrap(payload, session_id))
    # payload object is always present, never null
    assert dumped["payload"] == {}


# --------------------------------------------------------------------------- #
# Client -> Agent user-input responses (A.4b)
# --------------------------------------------------------------------------- #


def test_spatial_input_response_point(session_id: str) -> None:
    payload = ws.SpatialInputResponsePayload(
        request_id=new_ulid(),
        geometry_type="point",
        coordinates=[-82.0, 26.5],
    )
    _roundtrip_idempotent(_wrap(payload, session_id))


def test_spatial_input_response_cancelled(session_id: str) -> None:
    payload = ws.SpatialInputResponsePayload(
        request_id=new_ulid(),
        cancelled=True,
    )
    dumped = _roundtrip_idempotent(_wrap(payload, session_id))
    assert dumped["payload"]["cancelled"] is True


def test_disambiguation_response(session_id: str) -> None:
    payload = ws.DisambiguationResponsePayload(request_id=new_ulid(), candidate_id="cand-1")
    _roundtrip_idempotent(_wrap(payload, session_id))


def test_clarification_response(session_id: str) -> None:
    payload = ws.ClarificationResponsePayload(request_id=new_ulid(), option_id="opt-a")
    _roundtrip_idempotent(_wrap(payload, session_id))


# --------------------------------------------------------------------------- #
# Agent -> Client (A.4)
# --------------------------------------------------------------------------- #


def test_agent_message_chunk(session_id: str) -> None:
    payload = ws.AgentMessageChunkPayload(message_id=new_ulid(), delta="The peak depth is ", done=False)
    _roundtrip_idempotent(_wrap(payload, session_id))


def test_tool_call_start(session_id: str) -> None:
    payload = ws.ToolCallStartPayload(
        call_id=new_ulid(),
        step_id=new_ulid(),
        tool_name="run_storm_surge_flood",
        tool_category="workflow",
        params={"location": "Fort Myers, FL"},
    )
    _roundtrip_idempotent(_wrap(payload, session_id))


def test_tool_call_progress(session_id: str) -> None:
    payload = ws.ToolCallProgressPayload(call_id=new_ulid(), percent=42, status="running solver")
    _roundtrip_idempotent(_wrap(payload, session_id))


def test_tool_call_progress_percent_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        ws.ToolCallProgressPayload(call_id=new_ulid(), percent=200)


def test_tool_call_complete_metrics_carried_as_dict(session_id: str) -> None:
    payload = ws.ToolCallCompletePayload(
        call_id=new_ulid(),
        result_summary="Peak depth 3.2 m over 18 km^2",
        result_uri="gs://grace-2/runs/01HX/result.cog.tif",
        metrics={"flooded_area_km2": 18.4, "max_depth_m": 3.2},
    )
    dumped = _roundtrip_idempotent(_wrap(payload, session_id))
    # Invariant 1: numbers cited by the narrative live in `metrics`, not free text
    assert "flooded_area_km2" in dumped["payload"]["metrics"]


def test_tool_call_failed(session_id: str) -> None:
    payload = ws.ToolCallFailedPayload(
        call_id=new_ulid(),
        error_code="DEM_SOURCE_UNAVAILABLE",
        message="USGS 3DEP timed out",
        retryable=True,
    )
    _roundtrip_idempotent(_wrap(payload, session_id))


def test_pipeline_state_cancelled_is_distinct_terminal(session_id: str) -> None:
    """Invariant 8: cancelled must be a distinct PipelineStepState, not failed."""
    payload = ws.PipelineStatePayload(
        pipeline_id=new_ulid(),
        steps=[
            ws.PipelineStep(step_id=new_ulid(), name="fetch DEM", tool_name="fetch_dem", state="complete"),
            ws.PipelineStep(step_id=new_ulid(), name="run solver", tool_name="run_solver", state="cancelled"),
        ],
    )
    dumped = _roundtrip_idempotent(_wrap(payload, session_id))
    states = [s["state"] for s in dumped["payload"]["steps"]]
    assert "cancelled" in states and "failed" not in states


def test_pipeline_state_invalid_step_state_rejected() -> None:
    with pytest.raises(ValidationError):
        ws.PipelineStep(step_id=new_ulid(), name="x", tool_name="x", state="aborted")  # type: ignore[arg-type]


# --- map-command and the per-command args models ---------------------------- #


def test_map_command_load_layer_args_roundtrip(session_id: str) -> None:
    args = ws.LoadLayerArgs(
        layer_id="run-01HX-flood-depth",
        wms_url="https://qgis.example.com/wms?MAP=01HX.qgs",
        style_preset="flood_depth_blue",
        temporal=ws.MapTemporal(
            start="2026-06-05T00:00:00Z", end="2026-06-05T06:00:00Z", step_seconds=300
        ),
    )
    payload = ws.MapCommandPayload(command="load-layer", args=args.model_dump(mode="json"))
    dumped = _roundtrip_idempotent(_wrap(payload, session_id))
    # The internal command discriminator survives the round-trip
    assert dumped["payload"]["command"] == "load-layer"
    # The args dict re-validates as LoadLayerArgs (the consumer's contract)
    re_parsed = ws.LoadLayerArgs.model_validate(dumped["payload"]["args"])
    assert re_parsed.layer_id == "run-01HX-flood-depth"


def test_map_command_zoom_to_bbox_args(session_id: str) -> None:
    args = ws.ZoomToArgs(bbox=(-82.5, 26.4, -81.7, 26.9))
    payload = ws.MapCommandPayload(command="zoom-to", args=args.model_dump(mode="json"))
    _roundtrip_idempotent(_wrap(payload, session_id))


def test_map_command_set_layer_opacity_clamped() -> None:
    with pytest.raises(ValidationError):
        ws.SetLayerOpacityArgs(layer_id="x", opacity=1.5)


def test_map_command_args_registry_covers_every_command() -> None:
    """The internal command vocabulary must match the registered args models."""
    from typing import get_args as _get_args
    declared = set(_get_args(ws.MapCommand))
    registered = set(ws.MAP_COMMAND_ARGS.keys())
    assert declared == registered, (declared, registered)


# --- the rest of A.4 messages ---------------------------------------------- #


def test_confirmation_request_has_no_cost_field(session_id: str) -> None:
    """Invariant 9: no cost field anywhere on confirmation messages."""
    payload = ws.ConfirmationRequestPayload(
        request_id=new_ulid(),
        title="Run SFINCS for Hurricane Ian",
        description="This will execute the storm-surge solver.",
        estimated_duration_seconds=600,
    )
    dumped = _roundtrip_idempotent(_wrap(payload, session_id))
    payload_keys = set(dumped["payload"].keys())
    assert not any("cost" in k.lower() for k in payload_keys)
    # And the model itself rejects an attempt to add one
    with pytest.raises(ValidationError):
        ws.ConfirmationRequestPayload.model_validate(
            {
                "request_id": new_ulid(),
                "title": "x",
                "description": "x",
                "estimated_cost_usd": 4.20,
            }
        )


def test_session_state_payload(session_id: str) -> None:
    payload = ws.SessionStatePayload(
        chat_history=[{"role": "user", "content": "hi"}],
        loaded_layers=[],
        pipeline_history=[],
        map_view={"center": [-82.0, 26.5], "zoom": 8.0, "bbox": [-82.5, 26.4, -81.7, 26.9]},
    )
    _roundtrip_idempotent(_wrap(payload, session_id))


def test_error_payload_uses_a6_codes(session_id: str) -> None:
    payload = ws.ErrorPayload(error_code="RATE_LIMITED", message="slow down", retry_after_seconds=30)
    _roundtrip_idempotent(_wrap(payload, session_id))


def test_error_payload_unknown_code_rejected() -> None:
    with pytest.raises(ValidationError):
        ws.ErrorPayload(error_code="totally_made_up", message="x")  # type: ignore[arg-type]


def test_location_resolved(session_id: str) -> None:
    payload = ws.LocationResolvedPayload(
        resolved_id=new_ulid(),
        label="Fort Myers, FL",
        bbox=(-82.0, 26.5, -81.8, 26.7),
        granularity="city",
        source="geocoding",
    )
    _roundtrip_idempotent(_wrap(payload, session_id))


def test_spatial_input_request(session_id: str) -> None:
    payload = ws.SpatialInputRequestPayload(
        request_id=new_ulid(),
        mode="point",
        title="Pick a location",
        description="Where should the model be centered?",
        suggested_view=ws.SuggestedView(bbox=(-82.5, 26.4, -81.7, 26.9), zoom=10.0),
    )
    _roundtrip_idempotent(_wrap(payload, session_id))


def test_disambiguation_request(session_id: str) -> None:
    payload = ws.DisambiguationRequestPayload(
        request_id=new_ulid(),
        title="Which Springfield?",
        description="Multiple matches found.",
        candidates=[
            ws.DisambiguationCandidate(id="a", label="Springfield, IL", bbox=(-89.7, 39.7, -89.6, 39.9)),
            ws.DisambiguationCandidate(id="b", label="Springfield, MA", bbox=(-72.7, 42.0, -72.4, 42.2)),
        ],
    )
    _roundtrip_idempotent(_wrap(payload, session_id))


def test_clarification_request_requires_2_to_4_options(session_id: str) -> None:
    with pytest.raises(ValidationError):
        ws.ClarificationRequestPayload(
            request_id=new_ulid(),
            question="x?",
            options=[ws.ClarificationOption(id="a", label="A", description="A path")],  # only one
        )


def test_clarification_request_ok(session_id: str) -> None:
    payload = ws.ClarificationRequestPayload(
        request_id=new_ulid(),
        question="Model the storm surge or the pluvial flooding?",
        options=[
            ws.ClarificationOption(id="surge", label="Storm surge", description="SFINCS with surge BC"),
            ws.ClarificationOption(id="pluvial", label="Pluvial", description="SFINCS with rainfall BC"),
        ],
    )
    _roundtrip_idempotent(_wrap(payload, session_id))


# --------------------------------------------------------------------------- #
# Envelope-level + registry coverage
# --------------------------------------------------------------------------- #


def test_envelope_payload_always_an_object(session_id: str) -> None:
    """A.1: payload is always an object, never null/string/list."""
    payload = ws.SessionResumePayload()
    env = _wrap(payload, session_id)
    dumped = env.model_dump(mode="json")
    assert isinstance(dumped["payload"], dict)


def test_every_a3_a4_a4b_payload_round_trips(session_id: str) -> None:
    """Smoke: every payload class in ws.ALL_PAYLOADS must construct & round-trip
    via its minimal arguments. This catches an accidental drop from the registry.
    """
    minimal_factories = {
        "user-message": lambda: ws.UserMessagePayload(text="hi"),
        "cancel": lambda: ws.CancelPayload(),
        "confirm-response": lambda: ws.ConfirmResponsePayload(request_id=new_ulid(), approved=True),
        "session-resume": lambda: ws.SessionResumePayload(),
        "spatial-input-response": lambda: ws.SpatialInputResponsePayload(request_id=new_ulid(), cancelled=True),
        "disambiguation-response": lambda: ws.DisambiguationResponsePayload(request_id=new_ulid(), cancelled=True),
        "clarification-response": lambda: ws.ClarificationResponsePayload(request_id=new_ulid(), cancelled=True),
        "agent-message-chunk": lambda: ws.AgentMessageChunkPayload(message_id=new_ulid(), delta="x"),
        "tool-call-start": lambda: ws.ToolCallStartPayload(
            call_id=new_ulid(), step_id=new_ulid(), tool_name="t", tool_category="workflow"
        ),
        "tool-call-progress": lambda: ws.ToolCallProgressPayload(call_id=new_ulid()),
        "tool-call-complete": lambda: ws.ToolCallCompletePayload(call_id=new_ulid(), result_summary="ok"),
        "tool-call-failed": lambda: ws.ToolCallFailedPayload(
            call_id=new_ulid(), error_code="GENERIC", message="x"
        ),
        "pipeline-state": lambda: ws.PipelineStatePayload(pipeline_id=new_ulid()),
        "map-command": lambda: ws.MapCommandPayload(command="invalidate-tiles", args={}),
        "confirmation-request": lambda: ws.ConfirmationRequestPayload(
            request_id=new_ulid(), title="x", description="x"
        ),
        "session-state": lambda: ws.SessionStatePayload(),
        "error": lambda: ws.ErrorPayload(error_code="INTERNAL_ERROR", message="x"),
        "location-resolved": lambda: ws.LocationResolvedPayload(
            resolved_id=new_ulid(),
            label="x",
            bbox=(-1.0, -1.0, 1.0, 1.0),
            granularity="city",
            source="geocoding",
        ),
        "spatial-input-request": lambda: ws.SpatialInputRequestPayload(
            request_id=new_ulid(), mode="point", title="t", description="d"
        ),
        "disambiguation-request": lambda: ws.DisambiguationRequestPayload(
            request_id=new_ulid(),
            title="t",
            description="d",
            candidates=[ws.DisambiguationCandidate(id="a", label="A", bbox=(-1.0, -1.0, 1.0, 1.0))],
        ),
        "clarification-request": lambda: ws.ClarificationRequestPayload(
            request_id=new_ulid(),
            question="q?",
            options=[
                ws.ClarificationOption(id="a", label="A", description="a"),
                ws.ClarificationOption(id="b", label="B", description="b"),
            ],
        ),
        # sprint-08 — FR-FR-1 + §F.1.2 Mode 2
        "recovery-choice": lambda: ws.RecoveryChoicePayload(
            request_id=new_ulid(),
            failed_step_id=new_ulid(),
            error_code="UPSTREAM_API_ERROR",
            error_message="x",
            context="x",
            options=["deny", "retry", "chat"],
        ),
        "recovery-choice-response": lambda: ws.RecoveryChoiceResponsePayload(
            request_id=new_ulid(), choice="retry"
        ),
        "offer-catalog-addition": lambda: ws.OfferCatalogAdditionPayload(
            request_id=new_ulid(),
            url="https://example.gov/data/foo",
            discovered_via="user-query",
            probe_findings=ws.ProbeFindings(),
            suggested_catalog_entry=ws.SuggestedCatalogEntry(),
        ),
        "catalog-addition-response": lambda: ws.CatalogAdditionResponsePayload(
            request_id=new_ulid(), decision="reject"
        ),
    }
    # Every payload registered in ws.ALL_PAYLOADS must have a minimal factory
    # (i.e., the test covers the full inventory).
    assert set(minimal_factories.keys()) == set(ws.ALL_PAYLOADS.keys())
    for msg_type, factory in minimal_factories.items():
        payload = factory()
        env = _wrap(payload, session_id)
        _roundtrip_idempotent(env)
