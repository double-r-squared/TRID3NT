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
    BEFORE the final ``pipeline-state(complete)``.

    job-0254: ``emit_tool_call`` now routes the returned ``LayerURI`` through
    the ``layer_uri_emit`` seam before ``add_loaded_layer``. A renderable
    raster carries a WMS ``http(s)`` URL (post-publish, the realistic shape),
    which the seam passes through — so the funnel still fires. (The
    raster-with-raw-``gs://`` drop path is covered by
    ``test_emit_tool_call_drops_raster_gs_uri`` below and in
    ``test_layer_uri_emit.py``.)"""
    layer = LayerURI(
        layer_id="dem_1",
        name="Demo DEM",
        layer_type="raster",
        uri="https://qgis.run.app/wms?LAYERS=dem_1",
        style_preset="dem-default",
    )

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


@pytest.mark.asyncio
async def test_emit_tool_call_drops_raster_gs_uri(
    emitter: PipelineEmitter, sink: _CapturingSink
) -> None:
    """job-0254 §1+§2 integration: a tool that returns a renderable raster
    ``LayerURI`` with a raw ``gs://`` uri (the publish-failure degraded path)
    is DROPPED by the ``layer_uri_emit`` seam — NO ``session-state`` is
    emitted (no broken layer row), the step still completes, and the tool
    result is returned UNCHANGED so narration/retry can act on it."""
    leaked = _make_layer("gs://b/flood_depth_peak.tif", layer_id="flood_1")

    def fake_tool() -> LayerURI:
        return leaked

    result = await emitter.emit_tool_call(
        name="Flood scenario", tool_name="run_model_flood_scenario", invoke=fake_tool
    )
    # The LLM-visible tool result is unchanged (retry contract preserved).
    assert result is leaked
    # No layer reached the accumulator; no session-state was emitted.
    assert emitter.loaded_layers == []
    assert not any(f["type"] == "session-state" for f in sink.frames)
    # The step still completes cleanly (the drop is not a tool failure).
    types = [f["type"] for f in sink.frames]
    assert types == [
        "pipeline-state",  # pending
        "pipeline-state",  # running
        "pipeline-state",  # complete (no session-state in between)
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


# --------------------------------------------------------------------------- #
# 9. Vector inline-GeoJSON (job-0175)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_vector_layer_inlines_geojson_into_session_state(
    emitter: PipelineEmitter, sink: _CapturingSink, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a tool returns a vector LayerURI, the emitter reads bytes from
    GCS, parses, and embeds the result on the wire as ``inline_geojson``."""
    fake_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
                "properties": {"event": "Flood Warning"},
            }
        ],
    }

    async def fake_reader(uri: str):
        assert uri == "gs://b/alerts.fgb"
        return fake_fc

    monkeypatch.setattr(
        "grace2_agent.pipeline_emitter._read_vector_uri_as_geojson", fake_reader,
    )

    vector_layer = LayerURI(
        layer_id="nws-conus-all",
        name="NWS Alerts CONUS",
        layer_type="vector",
        uri="gs://b/alerts.fgb",
        style_preset="nws_alerts",
    )
    await emitter.add_loaded_layer(vector_layer)

    sess_frames = _session_frames(sink)
    assert len(sess_frames) == 1
    layers = sess_frames[-1]["payload"]["loaded_layers"]
    assert len(layers) == 1
    assert "inline_geojson" in layers[0]
    assert layers[0]["inline_geojson"]["type"] == "FeatureCollection"
    assert len(layers[0]["inline_geojson"]["features"]) == 1
    assert layers[0]["uri"] == "gs://b/alerts.fgb"


@pytest.mark.asyncio
async def test_vector_layer_inline_geojson_failure_is_non_fatal(
    emitter: PipelineEmitter, sink: _CapturingSink, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the GCS read / parse fails, the layer still lands; the wire
    payload omits ``inline_geojson``."""

    async def boom(uri: str):
        raise RuntimeError("simulated GCS failure")

    monkeypatch.setattr(
        "grace2_agent.pipeline_emitter._read_vector_uri_as_geojson", boom,
    )

    vector_layer = LayerURI(
        layer_id="nws-fail",
        name="NWS Alerts (broken)",
        layer_type="vector",
        uri="gs://b/missing.fgb",
        style_preset="nws_alerts",
    )
    await emitter.add_loaded_layer(vector_layer)

    sess_frames = _session_frames(sink)
    assert len(sess_frames) == 1
    layers = sess_frames[-1]["payload"]["loaded_layers"]
    assert len(layers) == 1
    assert "inline_geojson" not in layers[0]


@pytest.mark.asyncio
async def test_raster_layer_does_not_trigger_inline_path(
    emitter: PipelineEmitter, sink: _CapturingSink, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raster layers don't pass through the inline path."""
    calls: list[str] = []

    async def fake_reader(uri: str):
        calls.append(uri)
        return None

    monkeypatch.setattr(
        "grace2_agent.pipeline_emitter._read_vector_uri_as_geojson", fake_reader,
    )

    raster_layer = LayerURI(
        layer_id="dem_1",
        name="Demo DEM",
        layer_type="raster",
        uri="gs://b/dem.tif",
        style_preset="dem-default",
    )
    await emitter.add_loaded_layer(raster_layer)

    assert calls == []
    sess_frames = _session_frames(sink)
    layers = sess_frames[-1]["payload"]["loaded_layers"]
    assert "inline_geojson" not in layers[0]


@pytest.mark.asyncio
async def test_reset_loaded_layers_clears_inline_table(
    emitter: PipelineEmitter, sink: _CapturingSink, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``reset_loaded_layers`` flushes the inline-GeoJSON side-table."""

    async def fake_reader(uri: str):
        return {"type": "FeatureCollection", "features": []}

    monkeypatch.setattr(
        "grace2_agent.pipeline_emitter._read_vector_uri_as_geojson", fake_reader,
    )

    vector_layer = LayerURI(
        layer_id="a",
        name="A",
        layer_type="vector",
        uri="gs://b/a.fgb",
        style_preset="nws_alerts",
    )
    await emitter.add_loaded_layer(vector_layer)
    emitter.reset_loaded_layers([])
    assert emitter.loaded_layers == []
    await emitter.emit_session_state()
    last = _session_frames(sink)[-1]
    assert last["payload"]["loaded_layers"] == []


# --------------------------------------------------------------------------- #
# duration_ms stamping (job-0264, ELEVATED tool-timer requirement)
# --------------------------------------------------------------------------- #


def _stub_clock(emitter: PipelineEmitter, instants: list) -> None:
    """Patch the emitter's ``_now_fn`` to return ``instants`` in order, then
    repeat the last instant forever (so any extra clock reads don't IndexError).
    Pass timezone-aware UTC ``datetime`` objects."""
    seq = list(instants)
    idx = {"i": 0}

    def _fn():
        i = idx["i"]
        if i < len(seq):
            idx["i"] = i + 1
            return seq[i]
        return seq[-1]

    emitter._now_fn = staticmethod(_fn)  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_complete_stamps_authoritative_duration_ms(
    session_id: str, sink: _CapturingSink
) -> None:
    """On the complete transition the step carries duration_ms = the
    wall-clock elapsed time between mark_running and mark_complete."""
    from datetime import datetime, timezone

    emitter = PipelineEmitter(session_id=session_id, sink=sink)
    # add_step (start_pipeline + step), mark_running (started_at),
    # mark_complete (completed_at). Clock reads: pipeline_started, started_at,
    # completed_at — give a 2m34s gap between running and complete.
    t0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
    t_run = datetime(2026, 6, 10, 12, 0, 1, tzinfo=timezone.utc)
    t_done = datetime(2026, 6, 10, 12, 2, 35, tzinfo=timezone.utc)  # +154s from t_run
    _stub_clock(emitter, [t0, t_run, t_done])

    step_id = await emitter.add_step(name="run_sfincs", tool_name="run_solver")
    await emitter.mark_running(step_id)
    await emitter.mark_complete(step_id)

    last = _pipeline_frames(sink)[-1]["payload"]["steps"][-1]
    assert last["state"] == "complete"
    assert last["duration_ms"] == 154_000


@pytest.mark.asyncio
async def test_failed_stamps_duration_ms(
    session_id: str, sink: _CapturingSink
) -> None:
    """A failed step also carries the elapsed-before-failure duration_ms."""
    from datetime import datetime, timezone

    emitter = PipelineEmitter(session_id=session_id, sink=sink)
    t0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
    t_run = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
    t_fail = datetime(2026, 6, 10, 12, 0, 5, 500_000, tzinfo=timezone.utc)  # +5.5s
    _stub_clock(emitter, [t0, t_run, t_fail])

    step_id = await emitter.add_step(name="fetch_dem", tool_name="fetch_dem")
    await emitter.mark_running(step_id)
    await emitter.mark_failed(step_id, error_code="UPSTREAM_API_ERROR", error_message="503")

    last = _pipeline_frames(sink)[-1]["payload"]["steps"][-1]
    assert last["state"] == "failed"
    assert last["duration_ms"] == 5_500


@pytest.mark.asyncio
async def test_cancelled_stamps_duration_ms(
    session_id: str, sink: _CapturingSink
) -> None:
    """A cancelled step carries the elapsed-before-cancel duration_ms so the
    yellow card locks rather than ticking forever."""
    from datetime import datetime, timezone

    emitter = PipelineEmitter(session_id=session_id, sink=sink)
    t0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
    t_run = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
    t_cancel = datetime(2026, 6, 10, 12, 0, 12, tzinfo=timezone.utc)  # +12s
    _stub_clock(emitter, [t0, t_run, t_cancel])

    step_id = await emitter.add_step(name="long_fetch", tool_name="fetch_dem")
    await emitter.mark_running(step_id)
    await emitter.mark_cancelled(step_id)

    last = _pipeline_frames(sink)[-1]["payload"]["steps"][-1]
    assert last["state"] == "cancelled"
    assert last["duration_ms"] == 12_000


@pytest.mark.asyncio
async def test_pending_and_running_have_no_duration_ms(
    emitter: PipelineEmitter, sink: _CapturingSink
) -> None:
    """duration_ms is None while pending and running — only the terminal
    transition stamps it. The cosmetic client ticker fills the gap."""
    step_id = await emitter.add_step(name="run_sfincs", tool_name="run_solver")
    pending = _pipeline_frames(sink)[-1]["payload"]["steps"][-1]
    assert pending["state"] == "pending"
    assert pending["duration_ms"] is None

    await emitter.mark_running(step_id)
    running = _pipeline_frames(sink)[-1]["payload"]["steps"][-1]
    assert running["state"] == "running"
    assert running["duration_ms"] is None


@pytest.mark.asyncio
async def test_zero_duration_for_subsecond_tool(
    session_id: str, sink: _CapturingSink
) -> None:
    """A tool that completes within the same instant reports duration_ms == 0
    (honest, not None) — the contract is ge=0."""
    from datetime import datetime, timezone

    emitter = PipelineEmitter(session_id=session_id, sink=sink)
    t = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
    _stub_clock(emitter, [t, t, t])

    step_id = await emitter.add_step(name="geocode", tool_name="geocode_location")
    await emitter.mark_running(step_id)
    await emitter.mark_complete(step_id)

    last = _pipeline_frames(sink)[-1]["payload"]["steps"][-1]
    assert last["duration_ms"] == 0


@pytest.mark.asyncio
async def test_emit_tool_call_stamps_duration_end_to_end(
    session_id: str, sink: _CapturingSink
) -> None:
    """The full emit_tool_call wrapper stamps a non-negative duration_ms on
    the terminal complete frame (integration of the seam server.py drives)."""
    emitter = PipelineEmitter(session_id=session_id, sink=sink)

    async def tool() -> str:
        return "ok"

    await emitter.emit_tool_call(name="fetch_dem", tool_name="fetch_dem", invoke=tool)
    last = _pipeline_frames(sink)[-1]["payload"]["steps"][-1]
    assert last["state"] == "complete"
    assert last["duration_ms"] is not None
    assert last["duration_ms"] >= 0


# --------------------------------------------------------------------------- #
# job-0254 §3 — byte-identical emission when SIGNED_URLS is absent
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_emit_byte_identical_with_seam_for_passing_layers(
    session_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For a PASSING layer (WMS raster), routing through the seam in
    ``emit_tool_call`` produces a ``session-state`` payload byte-identical to
    calling ``add_loaded_layer`` directly (pre-seam path). SIGNED_URLS absent
    must be a true no-op on the wire."""
    monkeypatch.delenv("SIGNED_URLS", raising=False)

    layer = LayerURI(
        layer_id="dem_1",
        name="Demo DEM",
        layer_type="raster",
        uri="https://qgis.run.app/wms?LAYERS=dem_1",
        style_preset="dem-default",
    )

    # Path A: seam-routed (through emit_tool_call's isinstance gate).
    sink_seam = _CapturingSink()
    em_seam = PipelineEmitter(session_id=session_id, sink=sink_seam)
    await em_seam.emit_tool_call(
        name="Fetch DEM", tool_name="fetch_dem", invoke=lambda: layer
    )
    seam_session = [f for f in sink_seam.frames if f["type"] == "session-state"]
    assert seam_session, "seam path emitted no session-state"
    seam_loaded = seam_session[-1]["payload"]["loaded_layers"]

    # Path B: direct add_loaded_layer (bypasses the seam entirely).
    sink_direct = _CapturingSink()
    em_direct = PipelineEmitter(session_id=session_id, sink=sink_direct)
    await em_direct.add_loaded_layer(layer)
    direct_session = [
        f for f in sink_direct.frames if f["type"] == "session-state"
    ]
    direct_loaded = direct_session[-1]["payload"]["loaded_layers"]

    # The loaded_layers wire dicts are byte-identical (seam is a no-op for a
    # passing layer when SIGNED_URLS is absent).
    assert seam_loaded == direct_loaded
    assert seam_loaded[0]["uri"] == "https://qgis.run.app/wms?LAYERS=dem_1"


@pytest.mark.asyncio
async def test_emit_byte_identical_under_signed_urls_true(
    session_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGNED_URLS=true is dormant: the emitted ``session-state.loaded_layers``
    payload is identical to SIGNED_URLS absent (only a WARNING is logged)."""
    layer = LayerURI(
        layer_id="dem_2",
        name="Demo DEM 2",
        layer_type="raster",
        uri="https://qgis.run.app/wms?LAYERS=dem_2",
        style_preset="dem-default",
    )

    monkeypatch.delenv("SIGNED_URLS", raising=False)
    sink_absent = _CapturingSink()
    em_absent = PipelineEmitter(session_id=session_id, sink=sink_absent)
    await em_absent.emit_tool_call(
        name="Fetch DEM", tool_name="fetch_dem", invoke=lambda: layer
    )
    absent_loaded = [
        f for f in sink_absent.frames if f["type"] == "session-state"
    ][-1]["payload"]["loaded_layers"]

    monkeypatch.setenv("SIGNED_URLS", "true")
    sink_true = _CapturingSink()
    em_true = PipelineEmitter(session_id=session_id, sink=sink_true)
    await em_true.emit_tool_call(
        name="Fetch DEM", tool_name="fetch_dem", invoke=lambda: layer
    )
    true_loaded = [
        f for f in sink_true.frames if f["type"] == "session-state"
    ][-1]["payload"]["loaded_layers"]

    assert absent_loaded == true_loaded
