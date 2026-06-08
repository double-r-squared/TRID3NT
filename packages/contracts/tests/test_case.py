"""Round-trip + invariant tests for Case persistence envelopes (FR-MP-6).

Every Case persistence type defined in ``grace2_contracts.case`` is exercised:
- A real instance is built, dumped via ``model_dump(mode="json")``, JSON-text
  round-tripped, parsed back, and re-dumped. Both passes must be byte-identical.
- ULID format validation refuses malformed ids.
- ISO-8601 datetime validation produces ``...Z`` suffixes.
- envelope_type Literal validation refuses wrong discriminator values.
- Invariant 9: no cost field anywhere (self-checked).
- Closed-enum boundaries (``CaseStatus`` / ``CaseCommand``) are enforced.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from grace2_contracts.case import (
    CaseChatMessage,
    CaseCommandEnvelopePayload,
    CaseListEnvelopePayload,
    CaseOpenEnvelopePayload,
    CaseSessionState,
    CaseSummary,
)
from grace2_contracts.common import new_ulid


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _roundtrip(model: Any) -> dict[str, Any]:
    """Real JSON serialize -> text -> dict -> re-validate -> dump. Idempotent."""
    dumped_a = model.model_dump(mode="json")
    text_a = json.dumps(dumped_a, sort_keys=True)
    loaded = json.loads(text_a)
    rebuilt = type(model).model_validate(loaded)
    dumped_b = rebuilt.model_dump(mode="json")
    text_b = json.dumps(dumped_b, sort_keys=True)
    assert text_a == text_b, "JSON round-trip not idempotent"
    return dumped_a


def _fresh_case_summary() -> CaseSummary:
    return CaseSummary(
        case_id=new_ulid(),
        title="Hurricane Ian — Fort Myers",
        created_at="2026-06-05T12:00:00Z",
        updated_at="2026-06-05T12:30:00Z",
        bbox=(-82.5, 26.4, -81.7, 26.9),
        primary_hazard="flood",
        layer_summary=["run-01HX-flood-depth", "run-01HX-pop"],
        qgs_project_uri="gs://grace-2/cases/01HX/01HX.qgs",
    )


# --------------------------------------------------------------------------- #
# CaseSummary
# --------------------------------------------------------------------------- #


def test_case_summary_roundtrip() -> None:
    summary = _fresh_case_summary()
    dumped = _roundtrip(summary)
    assert dumped["status"] == "active"  # default
    assert dumped["schema_version"] == "v1"
    assert dumped["created_at"].endswith("Z")
    assert dumped["updated_at"].endswith("Z")
    assert dumped["bbox"] == [-82.5, 26.4, -81.7, 26.9]


def test_case_summary_minimal_required_fields() -> None:
    """All optional fields default cleanly."""
    summary = CaseSummary(
        case_id=new_ulid(),
        title="untitled",
        created_at="2026-06-05T12:00:00Z",
        updated_at="2026-06-05T12:00:00Z",
    )
    dumped = _roundtrip(summary)
    assert dumped["bbox"] is None
    assert dumped["primary_hazard"] is None
    assert dumped["layer_summary"] == []
    assert dumped["qgs_project_uri"] is None
    assert dumped["status"] == "active"


def test_case_summary_rejects_malformed_ulid() -> None:
    with pytest.raises(ValidationError):
        CaseSummary(
            case_id="not-a-ulid",
            title="x",
            created_at="2026-06-05T12:00:00Z",
            updated_at="2026-06-05T12:00:00Z",
        )


def test_case_summary_rejects_invalid_status() -> None:
    """CaseStatus is a closed Literal."""
    with pytest.raises(ValidationError):
        CaseSummary(
            case_id=new_ulid(),
            title="x",
            created_at="2026-06-05T12:00:00Z",
            updated_at="2026-06-05T12:00:00Z",
            status="paused",  # type: ignore[arg-type]
        )


def test_case_summary_rejects_bad_bbox_ordering() -> None:
    """BBox validator inherited from common.py: minLon > maxLon must fail."""
    with pytest.raises(ValidationError):
        CaseSummary(
            case_id=new_ulid(),
            title="x",
            created_at="2026-06-05T12:00:00Z",
            updated_at="2026-06-05T12:00:00Z",
            bbox=(10.0, 10.0, 5.0, 20.0),
        )


def test_case_summary_no_cost_field_invariant_9() -> None:
    """Invariant 9: no cost field anywhere on Case envelopes."""
    summary = _fresh_case_summary()
    dumped = summary.model_dump(mode="json")
    forbidden = {"cost", "estimated_cost", "spend", "spent", "budget", "quota"}
    assert forbidden.isdisjoint(dumped.keys()), f"cost-like field leaked: {dumped.keys() & forbidden}"


def test_case_summary_extra_forbid() -> None:
    """GraceModel extra='forbid' — unknown fields fail validation, not silently dropped."""
    with pytest.raises(ValidationError):
        CaseSummary.model_validate({
            "case_id": new_ulid(),
            "title": "x",
            "created_at": "2026-06-05T12:00:00Z",
            "updated_at": "2026-06-05T12:00:00Z",
            "estimated_cost": 42.0,  # invariant 9 + extra=forbid
        })


# --------------------------------------------------------------------------- #
# CaseChatMessage
# --------------------------------------------------------------------------- #


def test_case_chat_message_roundtrip() -> None:
    msg = CaseChatMessage(
        message_id=new_ulid(),
        case_id=new_ulid(),
        role="agent",
        content="Generating flood depth for Fort Myers...",
        pipeline_id=new_ulid(),
        layer_emissions=["run-01HX-flood-depth"],
        map_command_emissions=[
            {
                "command": "load-layer",
                "args": {
                    "layer_id": "run-01HX-flood-depth",
                    "wms_url": "https://qgis.example.com/wms?MAP=01HX.qgs",
                    "style_preset": "flood_depth_blue",
                },
            },
            {
                "command": "zoom-to",
                "args": {"bbox": [-82.5, 26.4, -81.7, 26.9]},
            },
        ],
        created_at="2026-06-05T12:01:00Z",
    )
    dumped = _roundtrip(msg)
    assert dumped["role"] == "agent"
    assert len(dumped["map_command_emissions"]) == 2
    assert dumped["created_at"].endswith("Z")


def test_case_chat_message_minimal_user_turn() -> None:
    msg = CaseChatMessage(
        message_id=new_ulid(),
        case_id=new_ulid(),
        role="user",
        content="Model the flood",
        created_at="2026-06-05T12:00:00Z",
    )
    dumped = _roundtrip(msg)
    assert dumped["pipeline_id"] is None
    assert dumped["layer_emissions"] == []
    assert dumped["map_command_emissions"] == []


def test_case_chat_message_rejects_invalid_role() -> None:
    with pytest.raises(ValidationError):
        CaseChatMessage(
            message_id=new_ulid(),
            case_id=new_ulid(),
            role="assistant",  # type: ignore[arg-type]
            content="...",
            created_at="2026-06-05T12:00:00Z",
        )


# --------------------------------------------------------------------------- #
# CaseSessionState
# --------------------------------------------------------------------------- #


def test_case_session_state_roundtrip() -> None:
    case = _fresh_case_summary()
    state = CaseSessionState(
        case=case,
        chat_history=[
            CaseChatMessage(
                message_id=new_ulid(),
                case_id=case.case_id,
                role="user",
                content="Run Ian",
                created_at="2026-06-05T12:00:00Z",
            ),
            CaseChatMessage(
                message_id=new_ulid(),
                case_id=case.case_id,
                role="agent",
                content="Running...",
                pipeline_id=new_ulid(),
                created_at="2026-06-05T12:00:30Z",
            ),
        ],
        loaded_layers=[
            {
                "layer_id": "run-01HX-flood-depth",
                "name": "Flood depth",
                "layer_type": "raster",
                "uri": "gs://grace-2/runs/01HX/depth.cog.tif",
                "style_preset": "flood_depth_blue",
                "visible": True,
                "role": "primary",
                "temporal": False,
            }
        ],
        pipeline_history=[],
        current_pipeline=None,
    )
    dumped = _roundtrip(state)
    assert dumped["case"]["case_id"] == case.case_id
    assert len(dumped["chat_history"]) == 2
    assert dumped["current_pipeline"] is None


def test_case_session_state_requires_case() -> None:
    with pytest.raises(ValidationError):
        CaseSessionState.model_validate({"chat_history": []})


# --------------------------------------------------------------------------- #
# CaseListEnvelopePayload
# --------------------------------------------------------------------------- #


def test_case_list_envelope_roundtrip_with_cases() -> None:
    payload = CaseListEnvelopePayload(
        cases=[_fresh_case_summary(), _fresh_case_summary()]
    )
    dumped = _roundtrip(payload)
    assert dumped["envelope_type"] == "case-list"
    assert len(dumped["cases"]) == 2


def test_case_list_envelope_empty_default() -> None:
    payload = CaseListEnvelopePayload()
    dumped = _roundtrip(payload)
    assert dumped["envelope_type"] == "case-list"
    assert dumped["cases"] == []


def test_case_list_envelope_rejects_wrong_envelope_type() -> None:
    """envelope_type Literal is locked — assigning a wrong value fails."""
    with pytest.raises(ValidationError):
        CaseListEnvelopePayload.model_validate({
            "envelope_type": "case-open",  # wrong discriminator
            "cases": [],
        })


def test_case_list_envelope_message_type_classvar() -> None:
    """The MESSAGE_TYPE ClassVar matches the envelope_type literal (A.1 discipline)."""
    assert CaseListEnvelopePayload.MESSAGE_TYPE == "case-list"


# --------------------------------------------------------------------------- #
# CaseOpenEnvelopePayload
# --------------------------------------------------------------------------- #


def test_case_open_envelope_roundtrip_with_state() -> None:
    case = _fresh_case_summary()
    state = CaseSessionState(case=case)
    payload = CaseOpenEnvelopePayload(session_state=state)
    dumped = _roundtrip(payload)
    assert dumped["envelope_type"] == "case-open"
    assert dumped["session_state"] is not None
    assert dumped["session_state"]["case"]["case_id"] == case.case_id


def test_case_open_envelope_null_session_state() -> None:
    """When the server can't rehydrate (e.g. Case deleted mid-select), state is None."""
    payload = CaseOpenEnvelopePayload()
    dumped = _roundtrip(payload)
    assert dumped["envelope_type"] == "case-open"
    assert dumped["session_state"] is None


def test_case_open_envelope_rejects_wrong_envelope_type() -> None:
    with pytest.raises(ValidationError):
        CaseOpenEnvelopePayload.model_validate({
            "envelope_type": "case-list",
            "session_state": None,
        })


def test_case_open_envelope_message_type_classvar() -> None:
    assert CaseOpenEnvelopePayload.MESSAGE_TYPE == "case-open"


# --------------------------------------------------------------------------- #
# CaseCommandEnvelopePayload
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "command,case_id,args",
    [
        ("create", None, {}),
        ("create", None, {"title": "Hurricane Ian — Fort Myers"}),
        ("select", "fixed", {}),
        ("rename", "fixed", {"title": "Renamed Case"}),
        ("archive", "fixed", {}),
        ("delete", "fixed", {}),
    ],
)
def test_case_command_envelope_roundtrip_every_command(
    command: str, case_id: str | None, args: dict
) -> None:
    cid = new_ulid() if case_id == "fixed" else None
    payload = CaseCommandEnvelopePayload(
        command=command,  # type: ignore[arg-type]
        case_id=cid,
        args=args,
    )
    dumped = _roundtrip(payload)
    assert dumped["envelope_type"] == "case-command"
    assert dumped["command"] == command
    assert dumped["case_id"] == cid
    assert dumped["args"] == args


def test_case_command_envelope_rejects_invalid_command() -> None:
    """CaseCommand is a closed Literal."""
    with pytest.raises(ValidationError):
        CaseCommandEnvelopePayload(
            command="duplicate",  # type: ignore[arg-type]
        )


def test_case_command_envelope_rejects_wrong_envelope_type() -> None:
    with pytest.raises(ValidationError):
        CaseCommandEnvelopePayload.model_validate({
            "envelope_type": "case-list",
            "command": "create",
        })


def test_case_command_envelope_no_cost_field_invariant_9() -> None:
    """Invariant 9: no cost field on the command envelope."""
    payload = CaseCommandEnvelopePayload(command="create")
    dumped = payload.model_dump(mode="json")
    forbidden = {"cost", "estimated_cost", "spend", "spent", "budget", "quota"}
    assert forbidden.isdisjoint(dumped.keys())


def test_case_command_envelope_no_cancellation_field_invariant_8() -> None:
    """Invariant 8: cancellation flows through A.3 cancel, not a case-command field."""
    payload = CaseCommandEnvelopePayload(command="create")
    dumped = payload.model_dump(mode="json")
    forbidden = {"cancel", "cancelled", "cancellation_reason"}
    assert forbidden.isdisjoint(dumped.keys())


def test_case_command_envelope_message_type_classvar() -> None:
    assert CaseCommandEnvelopePayload.MESSAGE_TYPE == "case-command"


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #


def test_module_exports_via_package_namespace() -> None:
    """Idempotent-append re-export from the package __init__ exposes case.*."""
    import grace2_contracts

    assert grace2_contracts.case is not None
    assert grace2_contracts.case.CaseSummary is CaseSummary
    assert grace2_contracts.case.CaseChatMessage is CaseChatMessage
    assert grace2_contracts.case.CaseSessionState is CaseSessionState
    assert grace2_contracts.case.CaseListEnvelopePayload is CaseListEnvelopePayload
    assert grace2_contracts.case.CaseOpenEnvelopePayload is CaseOpenEnvelopePayload
    assert grace2_contracts.case.CaseCommandEnvelopePayload is CaseCommandEnvelopePayload
