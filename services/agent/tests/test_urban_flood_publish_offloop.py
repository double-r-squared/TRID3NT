"""BREAK A (layer publish/persist) + BREAK B (event-loop off-loading) coverage
for the PySWMM urban-flood composer (model_urban_flood_swmm.py).

These tests are DELIBERATELY pyswmm-free: the heavy in-process solve
(build_and_stage_swmm_deck -> run_swmm_local -> postprocess_swmm) is stubbed at
the composer's module namespace so the publish/emit path + the off-loop wrap are
exercised end to end WITHOUT requiring pyswmm/swmm-api (absent in CI/some venvs).
They run everywhere rasterio is unneeded too - the stubs return plain objects.

BREAK A: the SWMM peak + frame COGs come out of postprocess_swmm as RAW s3://
URIs; the job-0254 emit guardrail (layer_uri_emit) DROPS a renderable raster
carrying s3://, so without publishing they silently vanish from the map and
persist no renderable loaded_layer. The fix routes the peak + each frame through
publish_layer (the render chokepoint) so each carries a published http(s) URL
before it is returned/emitted (mirrors SFINCS model_flood_scenario Step-9/9b).

BREAK B: run_swmm_local is a SYNCHRONOUS ~16-min blocking solve; calling it
inline on the asyncio loop starves the loop (WS keepalive dies). The fix runs it
via asyncio.to_thread so the loop stays responsive. The test proves the loop is
NOT blocked by ticking a concurrent keepalive coroutine during a slow synthetic
solve.
"""

from __future__ import annotations

import asyncio
import time

from grace2_contracts.swmm_contracts import SWMMDepthLayerURI, SWMMRunArgs
from grace2_agent.tools.publish_layer import PublishLayerError
from grace2_agent.workflows import model_urban_flood_swmm as M


# --------------------------------------------------------------------------- #
# Helpers / fakes
# --------------------------------------------------------------------------- #
def _depth_layer(layer_id: str, name: str, uri: str, role: str) -> SWMMDepthLayerURI:
    return SWMMDepthLayerURI(
        layer_id=layer_id,
        name=name,
        layer_type="raster",
        uri=uri,
        style_preset="continuous_flood_depth",
        role=role,
        units="meters",
        bbox=[-88.0, 36.0, -87.99, 36.01],
        max_depth_m=1.25,
        flooded_area_km2=0.04,
        n_buildings_affected=2,
        barriers={"type": "FeatureCollection", "features": []},
    )


def _titiler(uri: str) -> str:
    from urllib.parse import quote

    return f"https://tiles.example/cog/tiles/{{z}}/{{x}}/{{y}}.png?url={quote(uri, safe='')}"


class _FakeEmitter:
    def __init__(self) -> None:
        self.loaded_layers: list = []
        self.map_commands: list = []

    async def add_loaded_layer(self, layer) -> None:  # noqa: ANN001
        self.loaded_layers.append(layer)

    async def emit_map_command(self, kind, payload) -> None:  # noqa: ANN001
        self.map_commands.append((kind, payload))


# --------------------------------------------------------------------------- #
# BREAK A - _publish_peak_layer
# --------------------------------------------------------------------------- #
def test_publish_peak_substitutes_http_url(monkeypatch):
    """A raw s3:// peak is published and the returned layer carries the http URL
    while preserving the narration scalars + barriers."""
    calls: list = []

    def _pub(layer_uri, layer_id, style_preset=None, **kw):  # noqa: ANN001
        calls.append((layer_uri, layer_id, style_preset))
        return _titiler(layer_uri)

    monkeypatch.setattr(M, "publish_layer", _pub)

    raw = _depth_layer(
        "swmm-depth-peak-RID", "Peak flood depth",
        "s3://runs/RID/swmm_depth_peak.tif", "primary",
    )
    out = M._publish_peak_layer(raw, "RID")

    assert isinstance(out, SWMMDepthLayerURI)
    assert out.uri.startswith("http")
    assert not out.uri.startswith("s3://")
    assert out.layer_id == "swmm-depth-peak-RID"
    # narration scalars preserved (Invariant 1).
    assert out.max_depth_m == raw.max_depth_m
    assert out.flooded_area_km2 == raw.flooded_area_km2
    assert out.n_buildings_affected == raw.n_buildings_affected
    assert out.barriers == raw.barriers
    assert out.role == "primary"
    assert out.style_preset == "continuous_flood_depth"
    # publish_layer called once with the canonical layer_id + preset.
    assert len(calls) == 1
    assert calls[0][1] == "swmm-depth-peak-RID"
    assert calls[0][2] == "continuous_flood_depth"


def test_publish_peak_honest_drop_on_failure(monkeypatch):
    """On publish failure the RAW peak is returned (scalars intact) - the dispatch
    guardrail drops the dead raster but narration stays honest."""
    def _boom(layer_uri, layer_id, style_preset=None, **kw):  # noqa: ANN001
        raise PublishLayerError("JOBS_CLIENT_UNAVAILABLE", "no qgis in test")

    monkeypatch.setattr(M, "publish_layer", _boom)

    raw = _depth_layer(
        "swmm-depth-peak-RID", "Peak flood depth",
        "s3://runs/RID/swmm_depth_peak.tif", "primary",
    )
    out = M._publish_peak_layer(raw, "RID")
    # raw returned (still an s3:// uri) - narration intact, guardrail will drop.
    assert out is raw
    assert out.uri.startswith("s3://")
    assert out.max_depth_m == 1.25


def test_publish_peak_passthrough_when_already_http(monkeypatch):
    """A peak already carrying an http URL is returned untouched (no re-publish)."""
    def _should_not_call(*a, **k):  # noqa: ANN001
        raise AssertionError("publish_layer must not be called for an http peak")

    monkeypatch.setattr(M, "publish_layer", _should_not_call)
    raw = _depth_layer(
        "swmm-depth-peak-RID", "Peak flood depth",
        "https://tiles/x/{z}/{x}/{y}.png", "primary",
    )
    out = M._publish_peak_layer(raw, "RID")
    assert out is raw


# --------------------------------------------------------------------------- #
# BREAK A - _emit_frame_layers
# --------------------------------------------------------------------------- #
def test_emit_frames_publishes_each_and_preserves_name_token(monkeypatch):
    """Each frame COG is published to an http URL, emitted via add_loaded_layer,
    and keeps the 'Flood depth step N' grouping token (distinct urls)."""
    calls: list = []

    def _pub(layer_uri, layer_id, style_preset=None, **kw):  # noqa: ANN001
        calls.append(layer_id)
        return _titiler(layer_uri)

    monkeypatch.setattr(M, "publish_layer", _pub)

    frames = [
        _depth_layer(
            f"swmm-depth-frame-{i:02d}-RID", f"Flood depth step {i}",
            f"s3://runs/RID/swmm_depth_frame_{i:02d}.tif", "context",
        )
        for i in range(1, 4)
    ]
    emitter = _FakeEmitter()
    n = asyncio.run(M._emit_frame_layers(emitter, frames, "RID"))

    assert n == 3
    assert len(emitter.loaded_layers) == 3
    assert len(calls) == 3
    out_names = [f.name for f in emitter.loaded_layers]
    assert out_names == [f"Flood depth step {i}" for i in range(1, 4)]
    out_uris = [f.uri for f in emitter.loaded_layers]
    assert all(u.startswith("http") for u in out_uris)
    assert len(set(out_uris)) == 3  # distinct -> no dedup collapse
    assert all(f.role == "context" for f in emitter.loaded_layers)


def test_emit_frames_drops_a_failed_publish(monkeypatch):
    """A frame that fails to publish is honestly dropped; the rest stay."""
    def _pub(layer_uri, layer_id, style_preset=None, **kw):  # noqa: ANN001
        if layer_id.endswith("02-RID"):
            raise PublishLayerError("PUBLISH_FAILED", "boom")
        return _titiler(layer_uri)

    monkeypatch.setattr(M, "publish_layer", _pub)
    frames = [
        _depth_layer(
            f"swmm-depth-frame-{i:02d}-RID", f"Flood depth step {i}",
            f"s3://runs/RID/swmm_depth_frame_{i:02d}.tif", "context",
        )
        for i in range(1, 4)
    ]
    emitter = _FakeEmitter()
    n = asyncio.run(M._emit_frame_layers(emitter, frames, "RID"))
    assert n == 2  # frame 02 dropped
    emitted_ids = [f.layer_id for f in emitter.loaded_layers]
    assert "swmm-depth-frame-02-RID" not in emitted_ids


def test_emit_frames_no_emitter_returns_zero():
    """No emitter bound (direct/smoke path) -> nothing emitted, returns 0."""
    frames = [
        _depth_layer("f1", "Flood depth step 1", "s3://r/1.tif", "context"),
    ]
    assert asyncio.run(M._emit_frame_layers(None, frames, "RID")) == 0


# --------------------------------------------------------------------------- #
# Full composer (pyswmm-free) - BREAK A end to end + BREAK B off-loop
# --------------------------------------------------------------------------- #
class _FakeStaging:
    def __init__(self) -> None:
        self.run_id = "RID"
        self.inp_path = "/tmp/does-not-exist/mesh.inp"
        self.build = type("B", (), {"n_active_cells": 0})()


def _install_pyswmm_free_chain(monkeypatch, *, solve_fn=None):
    """Stub the heavy solve chain so the composer runs without pyswmm."""
    staging = _FakeStaging()

    monkeypatch.setattr(
        M, "build_and_stage_swmm_deck",
        lambda *a, **k: staging,
    )
    # is_local_mode True so the run_solver out-of-process branch is skipped.
    monkeypatch.setattr(M, "is_local_mode", lambda: True)

    def _default_solve(stg):  # noqa: ANN001
        return type("R", (), {"continuity_error_pct": 0.5})()

    monkeypatch.setattr(M, "run_swmm_local", solve_fn or _default_solve)

    peak = _depth_layer(
        "swmm-depth-peak-RID", "Peak flood depth",
        "s3://runs/RID/swmm_depth_peak.tif", "primary",
    )
    frames = [
        _depth_layer(
            f"swmm-depth-frame-{i:02d}-RID", f"Flood depth step {i}",
            f"s3://runs/RID/swmm_depth_frame_{i:02d}.tif", "context",
        )
        for i in range(1, 4)
    ]
    monkeypatch.setattr(
        M, "postprocess_swmm",
        lambda *a, **k: ([peak] + frames, {"max_depth_m": 1.25}),
    )
    # No scratch dir to clean (stubbed inp path); make cleanup a no-op.
    monkeypatch.setattr(M, "_cleanup_deck_dir", lambda d: None)

    pub_calls: list = []

    def _pub(layer_uri, layer_id, style_preset=None, **kw):  # noqa: ANN001
        pub_calls.append(layer_id)
        return _titiler(layer_uri)

    monkeypatch.setattr(M, "publish_layer", _pub)
    return staging, pub_calls


def test_full_composer_publishes_peak_and_frames(monkeypatch):
    """End to end (pyswmm-free): the returned peak + all emitted frames carry
    published http URLs; publish_layer fired peak + per-frame."""
    from grace2_agent import pipeline_emitter as pe

    _staging, pub_calls = _install_pyswmm_free_chain(monkeypatch)

    fake = _FakeEmitter()
    token = pe._CURRENT_EMITTER.set(fake)
    try:
        run_args = SWMMRunArgs(bbox=(-88.0, 36.0, -87.99, 36.01))
        peak = asyncio.run(
            M.model_urban_flood_swmm(
                run_args,
                dem_path="/tmp/synthetic.tif",  # skip the DEM fetch
                building_footprints=None,
                run_id="RID",
            )
        )
    finally:
        pe._CURRENT_EMITTER.reset(token)

    # Peak returned, renderable http URL, scalars intact.
    assert isinstance(peak, SWMMDepthLayerURI)
    assert peak.role == "primary"
    assert peak.uri.startswith("http")
    assert peak.max_depth_m == 1.25

    # 3 frames emitted out-of-band, all published http, name token preserved.
    frames = fake.loaded_layers
    assert len(frames) == 3
    assert all(f.uri.startswith("http") for f in frames)
    assert [f.name for f in frames] == [f"Flood depth step {i}" for i in range(1, 4)]

    # publish_layer fired for peak + 3 frames = 4.
    assert len(pub_calls) == 4
    assert pub_calls[0] == "swmm-depth-peak-RID"

    # zoom-to issued before the solve.
    assert any(k == "zoom-to" for k, _ in fake.map_commands)


def test_full_composer_peak_drop_keeps_metrics(monkeypatch):
    """If publish fails for the peak, the composer still RETURNS a peak with the
    narration scalars (raw s3://) - the dispatch guardrail handles the map drop."""
    from grace2_agent import pipeline_emitter as pe

    _staging, _ = _install_pyswmm_free_chain(monkeypatch)

    def _boom(layer_uri, layer_id, style_preset=None, **kw):  # noqa: ANN001
        raise PublishLayerError("JOBS_CLIENT_UNAVAILABLE", "no qgis in test")

    monkeypatch.setattr(M, "publish_layer", _boom)

    fake = _FakeEmitter()
    token = pe._CURRENT_EMITTER.set(fake)
    try:
        run_args = SWMMRunArgs(bbox=(-88.0, 36.0, -87.99, 36.01))
        peak = asyncio.run(
            M.model_urban_flood_swmm(
                run_args, dem_path="/tmp/synthetic.tif",
                building_footprints=None, run_id="RID",
            )
        )
    finally:
        pe._CURRENT_EMITTER.reset(token)

    assert isinstance(peak, SWMMDepthLayerURI)
    assert peak.max_depth_m == 1.25  # narration intact
    assert peak.uri.startswith("s3://")  # unpublished; guardrail drops at dispatch
    # every frame failed to publish too -> none emitted (honest, no fake rows).
    assert fake.loaded_layers == []


def test_solve_runs_off_the_event_loop(monkeypatch):
    """BREAK B: the synchronous solve runs OFF the loop (asyncio.to_thread), so a
    concurrent keepalive coroutine keeps ticking DURING the solve. If the solve
    ran inline on the loop, the keepalive would be starved (0 ticks)."""
    from grace2_agent import pipeline_emitter as pe

    SOLVE_SECONDS = 0.6
    TICK_INTERVAL = 0.02

    def _slow_blocking_solve(staging):  # noqa: ANN001 - a SYNCHRONOUS blocking call
        # Simulates run_swmm_local's ~16-min synchronous pyswmm churn. time.sleep
        # holds the GIL only while sleeping releases it, so a to_thread worker lets
        # the loop run; an inline call would block the loop for the whole sleep.
        time.sleep(SOLVE_SECONDS)
        return type("R", (), {"continuity_error_pct": 0.5})()

    _install_pyswmm_free_chain(monkeypatch, solve_fn=_slow_blocking_solve)

    ticks = {"n": 0}

    async def _keepalive(stop: asyncio.Event) -> None:
        # Stand-in for the WS ping coroutine: it MUST keep getting scheduled while
        # the solve runs. With the off-loop fix it ticks ~SOLVE/TICK times.
        while not stop.is_set():
            ticks["n"] += 1
            await asyncio.sleep(TICK_INTERVAL)

    async def _drive() -> SWMMDepthLayerURI:
        fake = _FakeEmitter()
        token = pe._CURRENT_EMITTER.set(fake)
        stop = asyncio.Event()
        ka = asyncio.create_task(_keepalive(stop))
        try:
            run_args = SWMMRunArgs(bbox=(-88.0, 36.0, -87.99, 36.01))
            peak = await M.model_urban_flood_swmm(
                run_args, dem_path="/tmp/synthetic.tif",
                building_footprints=None, run_id="RID",
            )
        finally:
            stop.set()
            await ka
            pe._CURRENT_EMITTER.reset(token)
        return peak

    peak = asyncio.run(_drive())
    assert isinstance(peak, SWMMDepthLayerURI)
    # The loop stayed responsive: the keepalive ticked many times DURING the
    # 0.6s solve. A regression that runs the solve inline on the loop would
    # yield far fewer (effectively 0-1) ticks. Use a conservative floor.
    expected_min = int((SOLVE_SECONDS / TICK_INTERVAL) * 0.5)
    assert ticks["n"] >= expected_min, (
        f"loop was starved during the solve: only {ticks['n']} keepalive ticks "
        f"(expected >= {expected_min}); the blocking solve is NOT off-loop"
    )
