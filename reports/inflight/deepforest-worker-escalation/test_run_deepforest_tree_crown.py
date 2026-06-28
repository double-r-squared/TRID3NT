"""run_deepforest_tree_crown -- staging / dispatch / publish glue tests.

Exercises the tree-crown ML-inference tool in ISOLATION (no AWS, no torch, no
deepforest, no network): the build_spec assembly + S3 staging (mocked S3
client), the patch-count -> compute-class estimate, the crown-vector URI
resolver, the typed-error guards (bad params, AOI too large, incomplete params),
the honest worker-unavailable path (run_solver inert), the non-complete solve +
empty-output honesty floors, and the happy path through to a vector LayerURI
(mocked run_solver / wait_for_completion / publish_layer / NAIP fetch).

Pattern mirrors test_compute_canopy_height.py (the sibling Batch-worker ML tool):
monkeypatch-at-source + a FakeS3 client + asyncio.run.

NOTE: ``test_run_deepforest_tree_crown_registered`` will pass once the main
session unions this module into tools/__init__.py centrally.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from grace2_agent.tools import run_deepforest_tree_crown as rdf
from grace2_agent.tools.run_deepforest_tree_crown import (
    DEFAULT_PATCH_SIZE,
    assemble_deepforest_build_spec,
    estimate_deepforest_tiles,
    resolve_crown_vector_uri,
    run_deepforest_tree_crown,
    stage_deepforest_build_spec,
)

# A small CONUS forested AOI (panhandle Florida -- well inside the 0.06 deg^2 cap).
_AOI = (-85.300, 29.940, -85.297, 29.943)


# --------------------------------------------------------------------------- #
# FakeS3 (records puts; mirrors the canopy test's fake)
# --------------------------------------------------------------------------- #
class _FakeS3:
    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.puts.append(kwargs)
        return {"ETag": "fake"}


# --------------------------------------------------------------------------- #
# Pure helpers: patch-count estimate
# --------------------------------------------------------------------------- #
def test_estimate_tiles_small_aoi_is_one_patch():
    # A ~330 m x 330 m AOI at 1 m / 400-px patches -> 1x1 = 1 patch.
    assert estimate_deepforest_tiles(_AOI) == 1


def test_estimate_tiles_grows_with_area():
    small = estimate_deepforest_tiles((-85.30, 29.94, -85.29, 29.95))
    big = estimate_deepforest_tiles((-85.30, 29.94, -85.20, 29.99))
    assert big > small


def test_estimate_tiles_degenerate_clamps_to_one():
    assert estimate_deepforest_tiles((-85.30, 29.94, -85.30, 29.94)) == 1


def test_estimate_tiles_smaller_patch_size_more_patches():
    coarse = estimate_deepforest_tiles(_AOI, patch_size=400)
    fine = estimate_deepforest_tiles(_AOI, patch_size=100)
    assert fine >= coarse


# --------------------------------------------------------------------------- #
# Pure helpers: build_spec assembly
# --------------------------------------------------------------------------- #
def test_assemble_build_spec_shape():
    spec = assemble_deepforest_build_spec(
        "s3://bucket/rgb.tif",
        patch_size=400,
        patch_overlap=0.25,
        iou_threshold=0.15,
        score_threshold=0.3,
        bbox=_AOI,
    )
    assert spec["inputs"] == [{"gs_uri": "s3://bucket/rgb.tif", "dest": "rgb.tif"}]
    bs = spec["build_spec"]
    assert bs["input_file"] == "rgb.tif"
    assert bs["output_file"] == "tree_crowns.fgb"
    assert bs["patch_size"] == 400
    assert bs["patch_overlap"] == 0.25
    assert bs["iou_threshold"] == 0.15
    assert bs["score_threshold"] == 0.3
    assert bs["bbox"] == list(_AOI)
    assert "tree_crowns.fgb" in spec["outputs"]


def test_assemble_build_spec_omits_bbox_when_none():
    spec = assemble_deepforest_build_spec(
        "s3://b/rgb.tif",
        patch_size=400,
        patch_overlap=0.25,
        iou_threshold=0.15,
        score_threshold=0.3,
        bbox=None,
    )
    assert "bbox" not in spec["build_spec"]


# --------------------------------------------------------------------------- #
# Pure helpers: crown-vector URI resolver
# --------------------------------------------------------------------------- #
def test_resolve_crown_vector_prefers_named_fgb():
    uris = [
        "s3://runs/abc/deepforest.stdout",
        "s3://runs/abc/tree_crowns.fgb",
        "s3://runs/abc/other.fgb",
    ]
    assert resolve_crown_vector_uri(uris) == "s3://runs/abc/tree_crowns.fgb"


def test_resolve_crown_vector_falls_back_to_any_fgb():
    uris = ["s3://runs/abc/other.fgb", "s3://runs/abc/deepforest.stderr"]
    assert resolve_crown_vector_uri(uris) == "s3://runs/abc/other.fgb"


def test_resolve_crown_vector_none_when_no_vector():
    assert resolve_crown_vector_uri(["s3://runs/abc/deepforest.stdout"]) is None


# --------------------------------------------------------------------------- #
# S3 staging (FakeS3)
# --------------------------------------------------------------------------- #
def test_stage_build_spec_puts_json(monkeypatch):
    fake = _FakeS3()
    monkeypatch.setattr(
        "grace2_agent.tools.solver._get_s3_client", lambda: fake, raising=True
    )
    monkeypatch.setattr(
        "grace2_agent.tools.cache.storage_scheme", lambda: "s3", raising=True
    )
    monkeypatch.setenv("GRACE2_CACHE_BUCKET", "test-cache")

    uri = stage_deepforest_build_spec(
        "s3://b/rgb.tif",
        patch_size=400,
        patch_overlap=0.25,
        iou_threshold=0.15,
        score_threshold=0.3,
        run_id="RID123",
        bbox=_AOI,
    )
    assert uri == "s3://test-cache/cache/static-30d/deepforest_setup/RID123/build_spec.json"
    assert len(fake.puts) == 1
    body = json.loads(fake.puts[0]["Body"].decode("utf-8"))
    assert body["build_spec"]["patch_size"] == 400


def test_stage_build_spec_raises_typed_on_put_failure(monkeypatch):
    class _BoomS3:
        def put_object(self, **_kw: Any):
            raise RuntimeError("s3 down")

    monkeypatch.setattr(
        "grace2_agent.tools.solver._get_s3_client", lambda: _BoomS3(), raising=True
    )
    monkeypatch.setattr(
        "grace2_agent.tools.cache.storage_scheme", lambda: "s3", raising=True
    )
    with pytest.raises(rdf.DeepForestError) as ei:
        stage_deepforest_build_spec(
            "s3://b/rgb.tif",
            patch_size=400,
            patch_overlap=0.25,
            iou_threshold=0.15,
            score_threshold=0.3,
            run_id="RID",
        )
    assert ei.value.error_code == "DEEPFOREST_STAGING_FAILED"


# --------------------------------------------------------------------------- #
# Input-validation guards (return typed-error dicts, no exception)
# --------------------------------------------------------------------------- #
def test_invalid_bbox_returns_typed_error():
    res = asyncio.run(run_deepforest_tree_crown(bbox="not-a-bbox"))
    assert res["status"] == "error"
    assert res["error_code"] == "DEEPFOREST_PARAMS_INVALID"


def test_missing_bbox_and_imagery_returns_incomplete():
    res = asyncio.run(run_deepforest_tree_crown())
    assert res["status"] == "error"
    assert res["error_code"] == "DEEPFOREST_PARAMS_INCOMPLETE"


def test_degenerate_bbox_returns_invalid():
    res = asyncio.run(
        run_deepforest_tree_crown(bbox=(-85.30, 29.94, -85.30, 29.94))
    )
    assert res["status"] == "error"
    assert res["error_code"] == "DEEPFOREST_PARAMS_INVALID"


def test_aoi_too_large_returns_typed_error():
    # ~1 deg x 1 deg = 1.0 deg^2 >> 0.06 cap.
    res = asyncio.run(
        run_deepforest_tree_crown(bbox=(-85.0, 29.0, -84.0, 30.0))
    )
    assert res["status"] == "error"
    assert res["error_code"] == "DEEPFOREST_AOI_TOO_LARGE"


def test_bad_patch_overlap_returns_invalid():
    res = asyncio.run(
        run_deepforest_tree_crown(bbox=_AOI, patch_overlap=1.5)
    )
    assert res["status"] == "error"
    assert res["error_code"] == "DEEPFOREST_PARAMS_INVALID"


def test_bad_score_threshold_returns_invalid():
    res = asyncio.run(
        run_deepforest_tree_crown(bbox=_AOI, score_threshold=2.0)
    )
    assert res["status"] == "error"
    assert res["error_code"] == "DEEPFOREST_PARAMS_INVALID"


# --------------------------------------------------------------------------- #
# Honest worker-unavailable path (run_solver inert -- the partial-lane proof)
# --------------------------------------------------------------------------- #
def test_worker_unavailable_when_solver_not_registered(monkeypatch):
    from grace2_agent.tools.solver import SolverNotRegisteredError

    # NAIP fetch -> staged s3 uri (off the network).
    async def _fake_naip(_bbox):
        return "s3://cache/rgb.tif"

    monkeypatch.setattr(rdf, "_fetch_naip_rgb_uri", _fake_naip, raising=True)
    # Stage succeeds (no real S3).
    monkeypatch.setattr(
        rdf,
        "stage_deepforest_build_spec",
        lambda *a, **k: "s3://cache/spec.json",
        raising=True,
    )

    # run_solver raises the inert "unknown solver" error (worker not provisioned).
    def _raise_unregistered(**_kw):
        raise SolverNotRegisteredError("solver 'deepforest' not registered")

    monkeypatch.setattr(
        "grace2_agent.tools.solver.run_solver", _raise_unregistered, raising=True
    )
    monkeypatch.setattr(
        "grace2_agent.tools.solver.select_compute_class", lambda _t: "cpu-small",
        raising=True,
    )

    res = asyncio.run(run_deepforest_tree_crown(bbox=_AOI))
    assert res["status"] == "error"
    assert res["error_code"] == "DEEPFOREST_WORKER_UNAVAILABLE"
    assert "not deployed yet" in res["error_message"]


# --------------------------------------------------------------------------- #
# Honesty floors: non-complete solve + empty output
# --------------------------------------------------------------------------- #
class _FakeResult:
    def __init__(self, status, *, run_id="RID", output_uri=None,
                 error_code=None, error_message=None):
        self.status = status
        self.run_id = run_id
        self.output_uri = output_uri
        self.error_code = error_code
        self.error_message = error_message
        self.cancellation_reason = None


def _wire_dispatch(monkeypatch, *, result):
    async def _fake_naip(_bbox):
        return "s3://cache/rgb.tif"

    monkeypatch.setattr(rdf, "_fetch_naip_rgb_uri", _fake_naip, raising=True)
    monkeypatch.setattr(
        rdf, "stage_deepforest_build_spec",
        lambda *a, **k: "s3://cache/spec.json", raising=True,
    )
    monkeypatch.setattr(
        "grace2_agent.tools.solver.run_solver",
        lambda **_kw: object(), raising=True,
    )
    monkeypatch.setattr(
        "grace2_agent.tools.solver.select_compute_class",
        lambda _t: "cpu-small", raising=True,
    )

    async def _fake_wait(_handle):
        return result

    monkeypatch.setattr(
        "grace2_agent.tools.solver.wait_for_completion", _fake_wait, raising=True
    )


def test_non_complete_solve_returns_solve_failed(monkeypatch):
    _wire_dispatch(monkeypatch, result=_FakeResult("failed", error_code="X"))
    res = asyncio.run(run_deepforest_tree_crown(bbox=_AOI))
    assert res["status"] == "error"
    assert res["error_code"] == "DEEPFOREST_SOLVE_FAILED"


def test_empty_output_returns_output_missing(monkeypatch):
    _wire_dispatch(monkeypatch, result=_FakeResult("complete", run_id="RID9"))
    # Resolver returns None -> honest empty-output floor.
    monkeypatch.setattr(
        rdf, "_resolve_vector_from_result", lambda *a, **k: None, raising=True
    )
    res = asyncio.run(run_deepforest_tree_crown(bbox=_AOI))
    assert res["status"] == "error"
    assert res["error_code"] == "DEEPFOREST_OUTPUT_MISSING"


# --------------------------------------------------------------------------- #
# Happy path -> vector LayerURI
# --------------------------------------------------------------------------- #
def test_happy_path_returns_vector_layer_uri(monkeypatch):
    _wire_dispatch(monkeypatch, result=_FakeResult("complete", run_id="RID7"))
    monkeypatch.setattr(
        rdf,
        "_resolve_vector_from_result",
        lambda *a, **k: "s3://runs/RID7/tree_crowns.fgb",
        raising=True,
    )
    monkeypatch.setattr(
        "grace2_agent.tools.publish_layer.publish_layer",
        lambda **_kw: "https://wms/tree-crowns",
        raising=True,
    )

    res = asyncio.run(run_deepforest_tree_crown(bbox=_AOI, case_id="case-1"))
    # A LayerURI (not an error dict).
    assert getattr(res, "layer_type", None) == "vector"
    assert res.layer_id == "tree-crowns-RID7"
    assert res.name == "Detected Tree Crowns"
    assert res.uri == "https://wms/tree-crowns"
    assert res.style_preset == "tree_crowns"


# --------------------------------------------------------------------------- #
# payload estimate
# --------------------------------------------------------------------------- #
def test_estimate_payload_mb_small_constant():
    assert rdf.estimate_payload_mb(bbox=_AOI) <= 5.0
