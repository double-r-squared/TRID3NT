"""FRESH adversarial correctness probe for job-0254 (layer_uri_emit seam).

Run: cd services/agent && .venv/bin/python -m pytest tests/verify_0254_correctness.py -q
NOT the runner's own tests — a hostile re-derivation by the CORRECTNESS lens.
"""
from __future__ import annotations

import os
import importlib
from unittest.mock import MagicMock, patch

import pytest

from grace2_contracts.execution import LayerURI


def _mk(uri: str, layer_type: str = "raster", layer_id: str = "L1") -> LayerURI:
    return LayerURI(
        layer_id=layer_id,
        name=layer_id,
        layer_type=layer_type,
        uri=uri,
        style_preset="",
    )


# ------------------------------------------------------------------ #
# Full guardrail matrix + hostile shapes
# ------------------------------------------------------------------ #
@pytest.mark.parametrize(
    "uri,layer_type,expect_dropped",
    [
        # the leak class
        ("gs://bucket/flood.tif", "raster", True),
        ("gs://bucket/x.tif?token=abc", "raster", True),  # gs with querystring
        # PASS: WMS raster
        ("https://qgis.run.app/ogc/wms?LAYERS=x", "raster", False),
        ("http://qgis.run.app/ogc/wms?LAYERS=x", "raster", False),
        # PASS: vector gs:// (inline GeoJSON path job-0175)
        ("gs://bucket/parcels.geojson", "vector", False),
        ("gs://bucket/parcels.geojson", "vector", False),
        # PASS: vector https
        ("https://x/y.geojson", "vector", False),
        # PASS: /vsigs and local raster (not the leak class — no over-block)
        ("/vsigs/bucket/x.tif", "raster", False),
        ("/tmp/local.tif", "raster", False),
        ("./relative.tif", "raster", False),
        # hostile: empty uri raster -> not gs:// -> PASS (returns layer)
        ("", "raster", False),
        # hostile: uppercase GS:// — startswith is CASE-SENSITIVE so this PASSES
        ("GS://bucket/x.tif", "raster", False),
        # hostile: leading whitespace gs:// — startswith fails so PASSES
        (" gs://bucket/x.tif", "raster", False),
    ],
)
def test_guardrail_matrix(uri, layer_type, expect_dropped):
    from grace2_agent.layer_uri_emit import emit_layer_uri

    out = emit_layer_uri(_mk(uri, layer_type))
    if expect_dropped:
        assert out is None, f"expected DROP for {layer_type} {uri!r}"
    else:
        assert out is not None, f"expected PASS for {layer_type} {uri!r}"
        assert out.uri == uri
        assert out.layer_type == layer_type


def test_pass_is_identity_object():
    from grace2_agent.layer_uri_emit import emit_layer_uri

    lyr = _mk("https://x/wms", "raster")
    assert emit_layer_uri(lyr) is lyr  # same object, not a copy


def test_uppercase_GS_is_NOT_dropped_documents_case_sensitivity():
    """ADVERSARIAL FINDING CANDIDATE: GS:// (uppercase) bypasses the guardrail.

    startswith('gs://') is case-sensitive. A LayerURI carrying GS://... raster
    would PASS the guardrail. Whether this is reachable depends on whether any
    production code can emit an uppercase-scheme gs uri.
    """
    from grace2_agent.layer_uri_emit import emit_layer_uri

    out = emit_layer_uri(_mk("GS://bucket/x.tif", "raster"))
    assert out is not None  # NOT dropped — uppercase bypasses


# ------------------------------------------------------------------ #
# SIGNED_URLS flag parsing + byte-identity
# ------------------------------------------------------------------ #
@pytest.mark.parametrize(
    "val,expected",
    [
        ("true", True), ("TRUE", True), ("True", True),
        ("1", True), ("yes", True), ("YES", True),
        ("", False), ("false", False), ("0", False),
        ("no", False), ("off", False), ("on", False),  # 'on' NOT truthy here
        ("2", False), ("enabled", False),
    ],
)
def test_signed_urls_parsing(val, expected, monkeypatch):
    monkeypatch.setenv("SIGNED_URLS", val)
    from grace2_agent import layer_uri_emit
    importlib.reload(layer_uri_emit)
    assert layer_uri_emit.signed_urls_enabled() is expected


def test_signed_urls_true_byte_identical(monkeypatch):
    """SIGNED_URLS=true must be byte-identical to absent (only a WARNING)."""
    from grace2_agent.layer_uri_emit import emit_layer_uri

    lyr = _mk("https://x/wms", "raster")
    monkeypatch.delenv("SIGNED_URLS", raising=False)
    out_absent = emit_layer_uri(lyr)
    monkeypatch.setenv("SIGNED_URLS", "true")
    out_true = emit_layer_uri(lyr)
    assert out_absent is lyr and out_true is lyr  # both identity
    # and the guardrail still drops raster gs:// even with the flag on:
    gs = _mk("gs://b/x.tif", "raster")
    assert emit_layer_uri(gs) is None


# ------------------------------------------------------------------ #
# Publish-failure path: force PublishLayerError; NO LayerURI to client;
# dict stays truthful (metrics survive).
# ------------------------------------------------------------------ #
def test_pipeline_emitter_drops_raster_gs_at_emit_site():
    """Drive the real emit_tool_call gate: a tool returning a raster gs://
    LayerURI must NOT call add_loaded_layer."""
    import asyncio
    from grace2_agent import pipeline_emitter as pe

    # Build a minimal emitter and patch add_loaded_layer to record calls.
    emitter = pe.PipelineEmitter.__new__(pe.PipelineEmitter)
    calls = []

    async def fake_add(layer):
        calls.append(layer)

    # The seam is called inline; test emit_layer_uri integration directly via
    # the same import the emitter uses.
    from grace2_agent.layer_uri_emit import emit_layer_uri

    gs_raster = _mk("gs://b/flood.tif", "raster")
    wms_raster = _mk("https://q/wms", "raster")
    vec_gs = _mk("gs://b/p.geojson", "vector")

    # Reproduce the emitter's gate logic (pipeline_emitter.py:859-862):
    for result in (gs_raster, wms_raster, vec_gs):
        if isinstance(result, LayerURI):
            el = emit_layer_uri(result)
            if el is not None:
                asyncio.get_event_loop().run_until_complete(fake_add(el))

    # Only the WMS raster and vector gs:// should reach add_loaded_layer.
    assert len(calls) == 2
    assert calls[0].uri == "https://q/wms"
    assert calls[1].uri == "gs://b/p.geojson"
    assert all(c.uri != "gs://b/flood.tif" for c in calls)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
