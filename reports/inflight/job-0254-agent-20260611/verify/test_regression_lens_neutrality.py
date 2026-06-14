"""REGRESSION-LENS independent probe (panel job-0253/Stage-2).

Proves SIGNED_URLS-unset byte-identical emission + guardrail matrix, written
from scratch (not the job's own tests) to avoid trusting their snapshots.
"""
import json
import os

import pytest

from grace2_contracts.execution import LayerURI
from grace2_agent.layer_uri_emit import emit_layer_uri, signed_urls_enabled


def _raster_http() -> LayerURI:
    return LayerURI(
        layer_id="r1", name="flood", layer_type="raster",
        uri="https://qgis.example.run.app/wms?LAYERS=flood", style_preset="flood",
        role="primary",
    )


def _raster_gs() -> LayerURI:
    return LayerURI(
        layer_id="r2", name="flood", layer_type="raster",
        uri="gs://bkt/flood_depth_peak.tif", style_preset="flood", role="primary",
    )


def _vector_gs() -> LayerURI:
    return LayerURI(
        layer_id="v1", name="roads", layer_type="vector",
        uri="gs://bkt/roads.fgb", style_preset="roads", role="context",
    )


def test_signed_urls_default_false_when_unset(monkeypatch):
    monkeypatch.delenv("SIGNED_URLS", raising=False)
    assert signed_urls_enabled() is False


def test_raster_http_byte_identical_flag_absent_vs_true(monkeypatch):
    # absent
    monkeypatch.delenv("SIGNED_URLS", raising=False)
    out_absent = emit_layer_uri(_raster_http())
    # =true
    monkeypatch.setenv("SIGNED_URLS", "true")
    out_true = emit_layer_uri(_raster_http())
    assert out_absent is not None and out_true is not None
    # identity pass-through (same object returned) + byte-identical JSON
    src = _raster_http()
    assert emit_layer_uri(src) is src  # returns the SAME object, no copy/mutation
    assert json.dumps(out_absent.model_dump(), sort_keys=True) == \
        json.dumps(out_true.model_dump(), sort_keys=True)


def test_vector_gs_passes_untouched_both_flag_states(monkeypatch):
    src = _vector_gs()
    monkeypatch.delenv("SIGNED_URLS", raising=False)
    assert emit_layer_uri(src) is src  # job-0175 inline-GeoJSON path: must pass
    monkeypatch.setenv("SIGNED_URLS", "true")
    assert emit_layer_uri(_vector_gs()).uri == "gs://bkt/roads.fgb"


def test_raster_gs_dropped_regardless_of_flag(monkeypatch):
    monkeypatch.delenv("SIGNED_URLS", raising=False)
    assert emit_layer_uri(_raster_gs()) is None
    monkeypatch.setenv("SIGNED_URLS", "true")
    assert emit_layer_uri(_raster_gs()) is None  # dormant flag does NOT relax drop
