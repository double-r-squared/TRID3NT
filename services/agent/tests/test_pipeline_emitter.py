"""Unit tests for ``PipelineEmitter`` (job-0035, M4 real envelope emission).

Coverage maps to the kickoff's acceptance criteria #5:

1. ``test_happy_path_state_transitions`` — pending → running → complete emits
   3 ``pipeline-state`` envelopes carrying the full snapshot each time
   (replace-not-reconcile per Appendix A.7).
2. ``test_replace_not_reconcile_full_snapshot`` — multi-step pipeline emits
   the FULL list of steps on every transition; there is NO merge / delta
   helper on the emitter (structurally enforced).
3. ``test_error_path_failed_step_carries_code_and_message`` — ``mark_failed``
   populates ``error_code`` (SCREAMING_SNAKE_CASE) + ``error_message``
   (truncated to 512 chars) per D.6 / job-0030.
4. ``test_loaded_layers_accumulation_via_layer_uri_return`` — when a tool
   returns a ``LayerURI``, ``loaded_layers`` grows in the next ``session-state``
   emission (FR-AS-7 / A.4 ``session-state``).
5. ``test_current_pipeline_set_and_cleared`` — ``current_pipeline`` is non-null
   in ``session-state`` while a pipeline is running and ``None`` after close
   (cross-envelope visibility predicate from job-0026).
6. ``test_cancel_propagation_emits_cancelled_state`` — the M1 cancel chain
   (``asyncio.CancelledError`` inside ``emit_tool_call``) flips the step to
   ``cancelled`` (yellow chip, distinct from ``failed`` per Invariant 8).
7. ``test_loaded_layers_dedup_by_uri`` — re-fetching the same layer replaces
   in place (TENTATIVE policy per kickoff Open Questions).
8. ``test_no_merge_helper_exists`` — defensive: scans the class for any
   ``merge``/``apply_delta``/``update_partial`` method that would break A.7.

These are async tests; the sink is a sync capture closure wrapped in an
``async def`` so the emitter can ``await`` it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from grace2_contracts import new_ulid
from grace2_contracts.execution import LayerURI

from grace2_agent.pipeline_emitter import (
    EMITTER_ERROR_CODES,
    PipelineEmitter,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


class _CapturingSink:
    """Captures every envelope JSON the emitter pushes. Tests assert on the
    parsed dicts."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def __call__(self, text: str) -> None:
        self.frames.append(json.loads(text))


@pytest.fixture()
def session_id() -> str:
    # Crockford-base32 ULID per grace2_contracts.ULIDStr.
    return new_ulid()


@pytest.fixture()
def sink() -> _CapturingSink:
    return _CapturingSink()


@pytest.fixture()
def emitter(session_id: str, sink: _CapturingSink) -> PipelineEmitter:
    return PipelineEmitter(session_id=session_id, sink=sink)


def _pipeline_frames(sink: _CapturingSink) -> list[dict[str, Any]]:
    return [f for f in sink.frames if f["type"] == "pipeline-state"]


def _session_frames(sink: _CapturingSink) -> list[dict[str, Any]]:
    return [f for f in sink.frames if f["type"] == "session-state"]


# --------------------------------------------------------------------------- #
# 1. Happy-path state transitions
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_happy_path_state_transitions(
    emitter: PipelineEmitter, sink: _CapturingSink
) -> None:
    """A single step's pending → running → complete cycle emits exactly 3
    ``pipeline-state`` envelopes, each carrying the FULL snapshot."""
    step_id = await emitter.add_step(name="Geocode", tool_name="geocode_location")
    await emitter.mark_running(step_id)
    await emitter.mark_complete(step_id)

    frames = _pipeline_frames(sink)
    assert len(frames) == 3, frames

    states = [f["payload"]["steps"][0]["state"] for f in frames]
    assert states == ["pending", "running", "complete"]

    # Pipeline id is stable across the three frames.
    pids = {f["payload"]["pipeline_id"] for f in frames}
    assert len(pids) == 1


# --------------------------------------------------------------------------- #
# 2. Replace-not-reconcile (Appendix A.7)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_replace_not_reconcile_full_snapshot(
    emitter: PipelineEmitter, sink: _CapturingSink
) -> None:
    """Every emission carries the complete steps list. Adding a second step
    to an already-running pipeline must emit a frame with BOTH steps; the
    M1 client replaces its view wholesale."""
    s1 = await emitter.add_step(name="Geocode", tool_name="geocode_location")
    await emitter.mark_running(s1)
    await emitter.mark_complete(s1)

    s2 = await emitter.add_step(name="Fetch DEM", tool_name="fetch_dem")

    frames = _pipeline_frames(sink)
    last = frames[-1]
    assert [step["step_id"] for step in last["payload"]["steps"]] == [s1, s2], last
    # First step still carries its `complete` terminal state in the
    # last frame — replace-not-reconcile.
    assert last["payload"]["steps"][0]["state"] == "complete"
    assert last["payload"]["steps"][1]["state"] == "pending"


# --------------------------------------------------------------------------- #
# 3. Error path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_error_path_failed_step_carries_code_and_message(
    emitter: PipelineEmitter, sink: _CapturingSink
) -> None:
    """``mark_failed`` populates the D.6 failure fields. Truncation to 512
    chars is enforced. SCREAMING_SNAKE_CASE shape is checked by the
    PipelineStepSummary validator (job-0030)."""
    step_id = await emitter.add_step(name="Fetch DEM", tool_name="fetch_dem")
    await emitter.mark_running(step_id)

    long_message = "x" * 1000  # > 512 cap
    await emitter.mark_failed(
        step_id,
        error_code="UPSTREAM_API_ERROR",
        error_message=long_message,
    )

    last = _pipeline_frames(sink)[-1]
    step = last["payload"]["steps"][0]
    assert step["state"] == "failed"
    # The wire ``pipeline-state`` payload (Appendix A.4) carries
    # ``progress_percent`` but NOT ``error_code``/``error_message`` — those
    # live on the persisted ``PipelineStepSummary`` (D.6) which surfaces via
    # session-state.current_pipeline. Verify both shapes:
    snap = emitter.current_snapshot()
    assert snap is not None
    failed = snap.steps[0]
    assert failed.error_code == "UPSTREAM_API_ERROR"
    assert failed.error_message is not None
    assert len(failed.error_message) == 512  # truncated
    assert EMITTER_ERROR_CODES.known("UPSTREAM_API_ERROR")


@pytest.mark.asyncio
async def test_mark_failed_rejects_malformed_error_code(
    emitter: PipelineEmitter,
) -> None:
    """The D.6 ``_validate_error_code_shape`` regex (job-0030) rejects
    lowercase / kebab-case codes at serialization time. ``mark_failed``
    flows through ``current_snapshot``/``PipelineStepSummary`` so the
    rejection lands eventually — but at the wire envelope shape
    (``PipelineStep``) the regex is NOT enforced (open-set on the wire).
    Verify the persistence-snapshot raises while the wire emission proceeds.
    """
    step_id = await emitter.add_step(name="X", tool_name="x")
    await emitter.mark_running(step_id)
    # Use lowercase code — would fail PipelineStepSummary regex.
    await emitter.mark_failed(
        step_id, error_code="upstream_api_error", error_message="oops"
    )
    with pytest.raises(Exception):
        emitter.current_snapshot()  # PipelineStepSummary regex fires here


# --------------------------------------------------------------------------- #
# 4. loaded_layers accumulation
# --------------------------------------------------------------------------- #


def _make_layer(uri: str, layer_id: str = "L1") -> LayerURI:
    return LayerURI(
        layer_id=layer_id,
        name="Demo DEM",
        layer_type="raster",
        uri=uri,
        style_preset="dem-default",
    )


@pytest.mark.asyncio
async def test_loaded_layers_accumulation_via_layer_uri_return(
    emitter: PipelineEmitter, sink: _CapturingSink
) -> None:
    """Calling ``add_loaded_layer`` directly OR returning a ``LayerURI`` from
    ``emit_tool_call`` should both grow ``loaded_layers`` and emit a fresh
    session-state envelope (A.7)."""
    layer = _make_layer("gs://b/dem.tif", layer_id="dem_1")
    await emitter.add_loaded_layer(layer)

    sess_frames = _session_frames(sink)
    assert len(sess_frames) == 1
    layers = sess_frames[-1]["payload"]["loaded_layers"]
    assert len(layers) == 1
    assert layers[0]["uri"] == "gs://b/dem.tif"
    assert layers[0]["layer_type"] == "raster"

    # Second distinct layer → both present in next emission.
    layer2 = _make_layer("gs://b/pop.fgb", layer_id="pop_1")
    await emitter.add_loaded_layer(layer2)
    sess_frames = _session_frames(sink)
    assert len(sess_frames) == 2
    uris = [layer["uri"] for layer in sess_frames[-1]["payload"]["loaded_layers"]]
    assert uris == ["gs://b/dem.tif", "gs://b/pop.fgb"]


@pytest.mark.asyncio
async def test_emit_tool_call_layer_uri_return_funnels_to_loaded_layers(
    emitter: PipelineEmitter, sink: _CapturingSink
) -> None:
    """End-to-end: a tool that returns a ``LayerURI`` from inside
    ``emit_tool_call`` causes a ``session-state`` envelope to be emitted
    BEFORE the final ``pipeline-state(complete)``."""
    layer = _make_layer("gs://b/dem.tif", layer_id="dem_1")

    def fake_tool() -> LayerURI:
        return layer

    result = await emitter.emit_tool_call(
        name="Fetch DEM", tool_name="fetch_dem", invoke=fake_tool
    )
    assert result is layer
    assert any(f["type"] == "session-state" for f in sink.frames)
    # Frame ordering: pending, running, session-state (after add_loaded_layer),
    # complete.
    types = [f["type"] for f in sink.frames]
    assert types == [
        "pipeline-state",  # pending
        "pipeline-state",  # running
        "session-state",  # add_loaded_layer side-effect
        "pipeline-state",  # complete
    ]


# --------------------------------------------------------------------------- #
# 5. current_pipeline set + cleared
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_current_pipeline_set_and_cleared(
    emitter: PipelineEmitter, sink: _CapturingSink
) -> None:
    """``session-state.current_pipeline`` is non-null while a pipeline is
    running (cross-envelope predicate (b) from job-0026), and is ``None``
    after ``close_pipeline``."""
    step_id = await emitter.add_step(name="Geocode", tool_name="geocode_location")
    await emitter.mark_running(step_id)
    await emitter.emit_session_state()

    last = _session_frames(sink)[-1]
    assert last["payload"]["current_pipeline"] is not None
    assert last["payload"]["current_pipeline"]["pipeline_id"] == emitter.pipeline_id

    await emitter.mark_complete(step_id)
    emitter.close_pipeline()
    await emitter.emit_session_state()

    last = _session_frames(sink)[-1]
    assert last["payload"]["current_pipeline"] is None
    assert emitter.pipeline_id is None


# --------------------------------------------------------------------------- #
# 6. Cancel propagation (Invariant 8)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cancel_propagation_emits_cancelled_state(
    emitter: PipelineEmitter, sink: _CapturingSink
) -> None:
    """``asyncio.CancelledError`` inside the wrapped tool flips the step to
    ``cancelled`` (distinct from failed) and re-raises. Honors Invariant 8."""

    async def cancelling_tool() -> Any:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await emitter.emit_tool_call(
            name="Long fetch", tool_name="fetch_dem", invoke=cancelling_tool
        )
    frames = _pipeline_frames(sink)
    last_state = frames[-1]["payload"]["steps"][-1]["state"]
    assert last_state == "cancelled"


@pytest.mark.asyncio
async def test_error_classifier_buckets_known_exception_types(
    emitter: PipelineEmitter, sink: _CapturingSink
) -> None:
    """Exception classification feeds the open-set A.6 error-code registry.
    ConnectionError → UPSTREAM_API_ERROR (covers job-0033 fetcher failures)."""

    def boom() -> None:
        raise ConnectionError("upstream 503")

    with pytest.raises(ConnectionError):
        await emitter.emit_tool_call(
            name="Fetch", tool_name="fetch_dem", invoke=boom
        )
    snap = emitter.current_snapshot()
    assert snap is not None
    failed = snap.steps[-1]
    assert failed.state == "failed"
    assert failed.error_code == "UPSTREAM_API_ERROR"


# --------------------------------------------------------------------------- #
# 7. loaded_layers dedup
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_loaded_layers_dedup_by_uri(
    emitter: PipelineEmitter, sink: _CapturingSink
) -> None:
    """Dedup policy: by ``uri`` (TENTATIVE per kickoff). Re-fetching the same
    layer with refreshed metadata replaces in place rather than duplicating."""
    layer = _make_layer("gs://b/dem.tif", layer_id="dem_1")
    await emitter.add_loaded_layer(layer)

    # Same uri, different style_preset on a re-fetch
    refreshed = LayerURI(
        layer_id="dem_1",
        name="Demo DEM (refreshed)",
        layer_type="raster",
        uri="gs://b/dem.tif",
        style_preset="dem-bluescale",
    )
    await emitter.add_loaded_layer(refreshed)

    layers = emitter.loaded_layers
    assert len(layers) == 1
    assert layers[0].style_preset == "dem-bluescale"
    assert layers[0].name == "Demo DEM (refreshed)"


# --------------------------------------------------------------------------- #
# 8. No merge helper (structural A.7 enforcement)
# --------------------------------------------------------------------------- #


def test_no_merge_helper_exists() -> None:
    """A.7 replace-not-reconcile is structurally enforced: the emitter must
    expose no merge / apply_delta / update_partial method. A future PR that
    accidentally adds one will fail this test."""
    forbidden = {"merge", "apply_delta", "update_partial", "reconcile"}
    methods = {name for name in dir(PipelineEmitter) if not name.startswith("_")}
    overlap = methods & forbidden
    assert not overlap, (
        f"PipelineEmitter exposes forbidden helper(s) {sorted(overlap)} — "
        "Appendix A.7 mandates replace-not-reconcile, structurally enforced "
        "by NOT shipping a merge-style API. Remove or rename."
    )
