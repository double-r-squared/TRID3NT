"""Atomic tool ``run_deepforest_tree_crown`` -- individual tree-crown detection.

A compute-heavy ML-inference tool that detects INDIVIDUAL tree crowns (one
bounding box per tree) in sub-metre RGB aerial imagery using DeepForest (the
``deepforest`` pip package -- a RetinaNet trained on NEON airborne RGB,
``predict_tile``), MIT-licensed. The outer agent picks the AOI; the inner model
runs ``predict_tile`` and the tool returns the per-tree crown bounding-box
polygons (with a detection ``score``) as a FlatGeobuf vector layer.

WHY THIS IS A BATCH-WORKER TOOL (NOT inline on the agent box)
=============================================================

This mirrors ``compute_canopy_height`` EXACTLY. Canopy-height runs Meta's ViT+DPT
in a ``services/workers/canopy`` AWS Batch worker dispatched via
``run_solver('canopy', ...)`` -- it does NOT run the model inline on the agent
box. DeepForest is the same shape: a PyTorch model (RetinaNet) whose dependency
closure (``torch`` + ``torchvision`` + ``pytorch-lightning`` + the full
``nvidia-*`` / ``cuda-toolkit`` stack + a HuggingFace weights download) is GBs and
would pollute / conflict the shared agent venv and stall the WebSocket heartbeat
if run on the loop. So the inference belongs in an ephemeral scale-to-zero CPU
Batch worker, EXACTLY like the canopy ViT.

LANE STATUS (honest): the ``services/workers/deepforest`` Batch worker + its
``"deepforest"`` entry in ``solver.SOLVER_WORKFLOW_REGISTRY`` + its
``GRACE2_AWS_BATCH_JOB_DEF_DEEPFOREST`` job-def are an ORCHESTRATOR seam (services/
workers) and are NOT YET PROVISIONED. Until they land, ``run_solver('deepforest',
...)`` raises the honest ``SolverNotRegisteredError`` (unknown solver) -- this tool
catches it and returns a typed ``DEEPFOREST_WORKER_UNAVAILABLE`` error so the LLM
narrates "the tree-crown worker is not deployed yet" rather than fabricating a
layer. This is the SAME inert-until-provisioned posture SWMM / OpenQuake / the
SFINCS deck-builder carried before their job-defs were flipped.

Flow (mirrors compute_canopy_height's stage -> dispatch -> wait -> publish chain):

  1. Resolve / stage a sub-metre RGB COG for the AOI. A caller-supplied
     ``imagery_uri`` (an existing fetcher's COG handle, PREFERRED) wins; else we
     fetch NAIP (the CONUS sub-metre RGB source) via ``fetch_naip``. Either way
     the model input is an ``s3://`` COG the ephemeral Batch worker can download
     (the worker has NO access to the agent box FS -- the same honesty guard
     ``_run_solver_aws_batch`` enforces on ``model_setup_uri``).
  2. Write a build_spec JSON ({imagery_uri, patch_size, patch_overlap,
     iou_threshold, output_glob}) to the cache bucket.
  3. Dispatch through the generic ``run_solver('deepforest', model_setup_uri=<build
     spec>, compute_class=select_compute_class(tiles))`` seam. ``"deepforest"`` is
     registered in ``SOLVER_WORKFLOW_REGISTRY`` once the worker is provisioned;
     until then the dispatch raises the honest inert error.
  4. ``wait_for_completion`` polls the SAME ``completion.json`` schema the worker
     writes; the worker uploads ``tree_crowns.fgb`` under the Batch run_id prefix.
  5. ``publish_layer`` the crown vector and return a ``LayerURI`` (vector) so the
     map paints the per-tree bounding boxes.

AOI CAP (load-bearing): DeepForest ``predict_tile`` over a large sub-metre AOI is
many model passes, so the bbox is capped (the granularity gate's spirit) and
``select_compute_class`` grabs a bigger box for a denser AOI. A too-large bbox
returns an honest typed error BEFORE any Spot spend.

Truthfulness floor: a detection is a MODEL ESTIMATE (DeepForest misses suppressed/
overlapping crowns and false-positives on non-tree texture). The layer name says
"Detected" and a non-complete Batch solve / an empty crown set NEVER reads as
success.

Determinism boundary (Invariant 1): the tool stages + dispatches + publishes; no
LLM call anywhere. FR-DC-6: ``cacheable=False`` + ``ttl_class="live-no-cache"`` +
``source_class="workflow_dispatch"`` -- the cache shim is NOT invoked (it spends
SPOT, like the other solver dispatchers).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from typing import Any

from grace2_contracts import new_ulid
from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from ..tool_arg_normalizer import coerce_bbox_value

logger = logging.getLogger("grace2_agent.tools.run_deepforest_tree_crown")

__all__ = [
    "run_deepforest_tree_crown",
    "DeepForestError",
    "DEEPFOREST_SOLVER_NAME",
    "assemble_deepforest_build_spec",
    "estimate_deepforest_tiles",
    "resolve_crown_vector_uri",
]


#: The registry key + handle ``solver`` tag for the DeepForest worker. Must match
#: the (Orchestrator-owned) ``"deepforest"`` entry the worker provisioning adds to
#: ``tools.solver.SOLVER_WORKFLOW_REGISTRY``. Until that lands, ``run_solver``
#: raises ``SolverNotRegisteredError`` -> the honest worker-unavailable error.
DEEPFOREST_SOLVER_NAME: str = "deepforest"

#: The worker writes the crown bounding-box vector under this fixed name in the
#: run_id prefix (mirrored by ``services/workers/deepforest``).
CROWN_OUTPUT_NAME: str = "tree_crowns.fgb"

#: The worker's output globs (crown FlatGeobuf + stdout/stderr for the honesty
#: gate), mirroring the canopy worker's output-glob list.
DEEPFOREST_OUTPUT_GLOBS: list[str] = [
    CROWN_OUTPUT_NAME,
    "deepforest.stdout",
    "deepforest.stderr",
]

#: DeepForest predict_tile defaults (the published NEON-tree-crown recipe).
#: ``patch_size`` is the crop the RetinaNet runs over (px); ``patch_overlap`` the
#: fractional overlap between crops (reduces edge-truncated crowns);
#: ``iou_threshold`` the NMS overlap above which two boxes merge.
DEFAULT_PATCH_SIZE: int = 400
DEFAULT_PATCH_OVERLAP: float = 0.25
DEFAULT_IOU_THRESHOLD: float = 0.15
#: Detection-confidence floor; crowns scoring below this are dropped by the worker
#: (DeepForest emits a continuous ``score`` per box).
DEFAULT_SCORE_THRESHOLD: float = 0.30


#: bbox area cap (deg^2). DeepForest predict_tile over a sub-metre AOI is many
#: model passes; cap the AOI to a neighborhood / small stand so a single SPOT box
#: finishes in a sane window. Matches the canopy + fetch_naip 0.06 deg^2 guardrail
#: (NAIP is the RGB source). Env-overridable so the cap re-tunes without a code
#: change.
def _max_bbox_deg2() -> float:
    raw = (os.environ.get("GRACE2_DEEPFOREST_MAX_BBOX_DEG2") or "").strip()
    try:
        v = float(raw)
        return v if v > 0 else 0.06
    except ValueError:
        return 0.06


#: NAIP native resolution (~1 m); used to estimate the patch count the model will
#: run (the cost proxy fed to ``select_compute_class``).
_NAIP_RES_M: float = 1.0
_DEG_TO_M: float = 111_320.0


class DeepForestError(RuntimeError):
    """Raised on any staging / dispatch / publish failure before a layer.

    Carries an open-set A.6 ``error_code`` so the agent emitter renders a typed
    error frame. Codes:

    - ``DEEPFOREST_PARAMS_INVALID`` -- the bbox / params could not be coerced.
    - ``DEEPFOREST_PARAMS_INCOMPLETE`` -- neither a bbox nor an imagery_uri.
    - ``DEEPFOREST_AOI_TOO_LARGE`` -- the AOI exceeds the CPU-runtime cap.
    - ``DEEPFOREST_IMAGERY_FAILED`` -- the RGB COG could not be staged/fetched.
    - ``DEEPFOREST_STAGING_FAILED`` -- the build_spec upload failed.
    - ``DEEPFOREST_WORKER_UNAVAILABLE`` -- the Batch worker is not provisioned
      yet (``run_solver('deepforest')`` is inert) -- honest typed error.
    - ``DEEPFOREST_SOLVE_FAILED`` -- the Batch solve did not complete.
    - ``DEEPFOREST_OUTPUT_MISSING`` -- a 'complete' solve produced no crown vector.
    - ``DEEPFOREST_PUBLISH_FAILED`` -- the crown vector could not be published.
    """

    error_code: str = "DEEPFOREST_WORKFLOW_FAILED"

    def __init__(
        self,
        error_code: str,
        *,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or error_code)
        self.error_code = error_code
        self.details: dict[str, Any] = dict(details or {})


# --------------------------------------------------------------------------- #
# Patch-count estimate -> compute-class (the predict_tile cost proxy).
# --------------------------------------------------------------------------- #
def estimate_deepforest_tiles(
    bbox: tuple[float, float, float, float],
    *,
    res_m: float = _NAIP_RES_M,
    patch_size: int = DEFAULT_PATCH_SIZE,
) -> int:
    """Estimate the number of predict_tile patches the model will run.

    DeepForest crops the tile into ``patch_size``-px windows and runs the
    RetinaNet per window, so the patch count is the natural cost proxy for
    ``select_compute_class``. Pure arithmetic (no I/O) -- unit-testable.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    width_m = max(0.0, (max_lon - min_lon)) * _DEG_TO_M * math.cos(
        math.radians((min_lat + max_lat) / 2.0)
    )
    height_m = max(0.0, (max_lat - min_lat)) * _DEG_TO_M
    if res_m <= 0:
        res_m = _NAIP_RES_M
    px_w = width_m / res_m
    px_h = height_m / res_m
    psz = patch_size if patch_size > 0 else DEFAULT_PATCH_SIZE
    nx = max(1, math.ceil(px_w / psz))
    ny = max(1, math.ceil(px_h / psz))
    return int(nx * ny)


# --------------------------------------------------------------------------- #
# build_spec assembly (PURE -- unit-tested in isolation).
# --------------------------------------------------------------------------- #
def assemble_deepforest_build_spec(
    imagery_uri: str,
    *,
    patch_size: int,
    patch_overlap: float,
    iou_threshold: float,
    score_threshold: float,
    bbox: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    """Map the staged RGB COG + predict_tile params onto the worker build_spec.

    The single source of truth for the worker-side input. The worker reads
    ``inputs[]`` (the RGB COG, downloaded as ``rgb.tif``), runs
    ``deepforest.main.deepforest().predict_tile(rgb.tif, patch_size, patch_overlap,
    iou_threshold)``, filters by ``score_threshold``, georeferences the pixel-space
    boxes to lon/lat crown polygons, and uploads the FlatGeobuf named by
    ``CROWN_OUTPUT_NAME``. Pure dict assembly.
    """
    spec: dict[str, Any] = {
        "inputs": [{"gs_uri": imagery_uri, "dest": "rgb.tif"}],
        "build_spec": {
            "input_file": "rgb.tif",
            "output_file": CROWN_OUTPUT_NAME,
            "patch_size": int(patch_size),
            "patch_overlap": float(patch_overlap),
            "iou_threshold": float(iou_threshold),
            "score_threshold": float(score_threshold),
        },
        "outputs": list(DEEPFOREST_OUTPUT_GLOBS),
    }
    if bbox is not None:
        spec["build_spec"]["bbox"] = list(bbox)
    return spec


# --------------------------------------------------------------------------- #
# build_spec staging (S3) -- mirror of stage_canopy_build_spec.
# --------------------------------------------------------------------------- #
def stage_deepforest_build_spec(
    imagery_uri: str,
    *,
    patch_size: int,
    patch_overlap: float,
    iou_threshold: float,
    score_threshold: float,
    run_id: str,
    bbox: tuple[float, float, float, float] | None = None,
) -> str:
    """Upload the DeepForest build_spec JSON to the cache bucket; return its s3:// URI.

    Mirrors ``stage_canopy_build_spec`` EXACTLY (no new client): the same
    ``cache.storage_scheme()`` scheme + the same ``solver._get_s3_client()`` boto3
    client + the same ``GRACE2_CACHE_BUCKET`` staging bucket. The returned URI is
    fed STRAIGHT to ``run_solver('deepforest', model_setup_uri=<this>)``.

    Raises ``DeepForestError('DEEPFOREST_STAGING_FAILED')`` on upload failure (the
    Batch lane cannot dispatch without a reachable build_spec -- fail loudly).
    """
    from .cache import storage_scheme
    from .solver import _get_s3_client

    scheme = storage_scheme()  # "s3" on AWS
    cache_bucket = os.environ.get("GRACE2_CACHE_BUCKET", "grace-2-hazard-prod-cache")
    prefix = f"cache/static-30d/deepforest_setup/{run_id}/"
    spec_key = f"{prefix}build_spec.json"
    spec_uri = f"{scheme}://{cache_bucket}/{spec_key}"

    build_spec = assemble_deepforest_build_spec(
        imagery_uri,
        patch_size=patch_size,
        patch_overlap=patch_overlap,
        iou_threshold=iou_threshold,
        score_threshold=score_threshold,
        bbox=bbox,
    )
    try:
        s3 = _get_s3_client()
        s3.put_object(
            Bucket=cache_bucket,
            Key=spec_key,
            Body=json.dumps(build_spec, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception as exc:  # noqa: BLE001
        raise DeepForestError(
            "DEEPFOREST_STAGING_FAILED",
            message=f"failed to stage deepforest build_spec to {spec_uri}: {exc}",
            details={"run_id": run_id, "build_spec_uri": spec_uri},
        ) from exc

    logger.info("stage_deepforest_build_spec run_id=%s -> %s", run_id, spec_uri)
    return spec_uri


# --------------------------------------------------------------------------- #
# Crown-vector handle resolution from the worker's completion.json.
# --------------------------------------------------------------------------- #
def resolve_crown_vector_uri(output_uris: list[str]) -> str | None:
    """Pick the crown FlatGeobuf from the uploaded output URIs (pure helper).

    The worker writes exactly one ``tree_crowns.fgb`` alongside stdout/stderr.
    Prefer the ``tree_crowns``-named ``.fgb``, falling back to any ``.fgb``, else
    None. Pure (string-only) so it unit-tests in isolation.
    """
    fgbs = [u for u in output_uris if u.lower().endswith((".fgb", ".geojson"))]
    for u in fgbs:
        name = u.rsplit("/", 1)[-1].lower()
        if "crown" in name or "tree" in name:
            return u
    return fgbs[0] if fgbs else None


# --------------------------------------------------------------------------- #
# AtomicToolMetadata + registration.
# --------------------------------------------------------------------------- #
_METADATA = AtomicToolMetadata(
    name="run_deepforest_tree_crown",
    ttl_class="live-no-cache",
    source_class="workflow_dispatch",
    cacheable=False,
)


def estimate_payload_mb(*_args: Any, **_kwargs: Any) -> float:
    """Conservative client-payload estimate for the crown vector.

    The output is a FlatGeobuf of per-tree bounding boxes; even a dense small AOI
    is at most a few thousand small polygons (~tens to low-hundreds of KB). The
    actual layer is delivered as a styled WMS face + a durable browser-readable
    GeoJSON mirror, so the in-chat payload is tiny. Report a small constant so the
    >25MB warn / >250MB block gate (FR large-payload) never spuriously trips.
    """
    return 1.0


@register_tool(
    _METADATA,
    # Annotations mirror the solver dispatchers (compute_canopy_height /
    # run_swan_waves): readOnlyHint=False (dispatches a Batch job that writes a
    # crown vector artifact), openWorldHint=False (Batch worker + intra-cloud
    # object store -- no public external API from the agent), destructiveHint=False
    # (writes go to a new runs/ prefix), idempotentHint=False (each call mints a
    # new run_id + output keys).
    read_only_hint=False,
    open_world_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
)
async def run_deepforest_tree_crown(
    bbox: tuple[float, float, float, float] | list[float] | str | None = None,
    imagery_uri: str | None = None,
    patch_size: int = DEFAULT_PATCH_SIZE,
    patch_overlap: float = DEFAULT_PATCH_OVERLAP,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    compute_class: str | None = None,
    case_id: str | None = None,
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> LayerURI | dict[str, Any]:
    """Detect INDIVIDUAL tree crowns (one box per tree) over an AOI from RGB imagery.

    Runs DeepForest (a RetinaNet trained on NEON airborne RGB, MIT-licensed,
    ``predict_tile``) on sub-metre RGB aerial imagery and returns one bounding-box
    polygon per DETECTED tree crown, each with a detection ``score``, painted on
    the map as a vector layer. This is an "AI-using-AI" inference tool: the
    inference is heavy (a CNN over many image patches), so -- exactly like
    ``compute_canopy_height`` -- it runs on the SAME scale-to-zero CPU AWS Batch
    substrate the physics engines use, NOT inline on the agent box.

    Use this when:
        - The user wants to COUNT / LOCATE / DELINEATE individual trees over an
          area ("count the trees here", "outline each tree crown", "how many trees
          in this stand", "tree-crown map"); OR
        - A downstream needs per-tree polygons (e.g. to join with a canopy-height
          raster via ``compute_zonal_statistics`` for per-tree height).

    Do NOT use this for:
        - Tree / canopy HEIGHT in metres (use ``compute_canopy_height``).
        - Vegetation greenness / health (use ``compute_ndvi``).
        - Forest vs non-forest land-cover CLASSES (use ``fetch_landcover``).
        - Very large AOIs -- the CNN is slow over many patches, so the bbox is
          capped; narrow it to a neighborhood / small stand.

    Params:
        bbox: the AOI as ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326
            (lon-first). Required UNLESS ``imagery_uri`` is supplied. CONUS-only
            when relying on the NAIP RGB source.
        imagery_uri: OPTIONAL ``s3://`` URI of an existing sub-metre RGB COG (an
            imagery fetcher's output handle). PREFERRED when available -- skips
            the NAIP fetch. When absent, NAIP is fetched for ``bbox``.
        patch_size: ADVANCED. The crop size (px) DeepForest runs the RetinaNet
            over (default 400 -- the published NEON recipe).
        patch_overlap: ADVANCED. Fractional overlap between crops (default 0.25;
            higher recovers more edge-truncated crowns at more compute).
        iou_threshold: ADVANCED. NMS overlap above which two boxes merge (default
            0.15).
        score_threshold: ADVANCED. Detection-confidence floor; crowns below this
            are dropped (default 0.30).
        compute_class: OPTIONAL FR-CE-3 compute class override. When unset it is
            auto-selected from the estimated patch count (more patches -> a bigger
            CPU box).

    Returns:
        On success: a ``LayerURI`` (``layer_type="vector"``) -- the emitter
        appends it to ``session-state.loaded_layers`` and the map renders the
        per-tree crown bounding boxes. The layer name reads "Detected Tree Crowns"
        (truthfulness floor: it is a model DETECTION -- DeepForest misses
        suppressed/overlapping crowns and can false-positive, not a census).

        On failure: a dict ``{"status": "error", "error_code", "error_message"}``
        so the LLM narrates the failure honestly (no layer). A non-complete Batch
        solve or an empty crown set returns a typed error -- it NEVER reports a
        silently-empty layer as success (honesty floor). While the Batch worker is
        not yet provisioned the dispatch returns
        ``DEEPFOREST_WORKER_UNAVAILABLE`` (honest "the tree-crown worker is not
        deployed yet"), NOT a fabricated layer.

    FR-DC-6: ``cacheable=False`` + ``ttl_class="live-no-cache"`` +
    ``source_class="workflow_dispatch"`` -- the cache shim is NOT invoked.
    """
    # --- 1. Validate params + resolve the AOI / imagery handle --------------
    try:
        psz = int(patch_size)
        povlp = float(patch_overlap)
        iou = float(iou_threshold)
        score = float(score_threshold)
    except (TypeError, ValueError):
        return {
            "status": "error",
            "error_code": "DEEPFOREST_PARAMS_INVALID",
            "error_message": (
                "patch_size must be an int and patch_overlap/iou_threshold/"
                "score_threshold must be floats; got "
                f"patch_size={patch_size!r}, patch_overlap={patch_overlap!r}, "
                f"iou_threshold={iou_threshold!r}, score_threshold={score_threshold!r}."
            ),
        }
    if psz <= 0:
        return {
            "status": "error",
            "error_code": "DEEPFOREST_PARAMS_INVALID",
            "error_message": f"patch_size must be > 0; got {psz}.",
        }
    if not (0.0 <= povlp < 1.0):
        return {
            "status": "error",
            "error_code": "DEEPFOREST_PARAMS_INVALID",
            "error_message": f"patch_overlap must be in [0, 1); got {povlp}.",
        }
    if not (0.0 <= iou <= 1.0):
        return {
            "status": "error",
            "error_code": "DEEPFOREST_PARAMS_INVALID",
            "error_message": f"iou_threshold must be in [0, 1]; got {iou}.",
        }
    if not (0.0 <= score <= 1.0):
        return {
            "status": "error",
            "error_code": "DEEPFOREST_PARAMS_INVALID",
            "error_message": f"score_threshold must be in [0, 1]; got {score}.",
        }

    coerced_bbox: tuple[float, float, float, float] | None = None
    if bbox is not None:
        coerced = coerce_bbox_value(bbox)
        if coerced is None:
            return {
                "status": "error",
                "error_code": "DEEPFOREST_PARAMS_INVALID",
                "error_message": (
                    f"invalid bbox (expected 4 numbers "
                    f"min_lon,min_lat,max_lon,max_lat): {bbox!r}"
                ),
            }
        coerced_bbox = tuple(coerced)  # type: ignore[assignment]

    if imagery_uri is None and coerced_bbox is None:
        return {
            "status": "error",
            "error_code": "DEEPFOREST_PARAMS_INCOMPLETE",
            "error_message": (
                "run_deepforest_tree_crown requires a bbox "
                "(min_lon, min_lat, max_lon, max_lat) OR an imagery_uri."
            ),
        }

    # --- 2. AOI cap (CPU-runtime guard, BEFORE any Spot spend) --------------
    if coerced_bbox is not None:
        min_lon, min_lat, max_lon, max_lat = coerced_bbox
        if not all(math.isfinite(v) for v in coerced_bbox):
            return {
                "status": "error",
                "error_code": "DEEPFOREST_PARAMS_INVALID",
                "error_message": f"bbox contains non-finite values: {coerced_bbox!r}",
            }
        if min_lon >= max_lon or min_lat >= max_lat:
            return {
                "status": "error",
                "error_code": "DEEPFOREST_PARAMS_INVALID",
                "error_message": (
                    f"bbox is degenerate (min must be < max on both axes): "
                    f"{coerced_bbox!r}"
                ),
            }
        area = (max_lon - min_lon) * (max_lat - min_lat)
        cap = _max_bbox_deg2()
        if area > cap:
            return {
                "status": "error",
                "error_code": "DEEPFOREST_AOI_TOO_LARGE",
                "error_message": (
                    f"bbox area {area:.4f} deg^2 exceeds the {cap} deg^2 tree-crown "
                    "cap (DeepForest runs a CNN over many image patches; narrow the "
                    "AOI to a neighborhood / small stand, or split it)."
                ),
            }

    try:
        return await _run_deepforest_chain(
            bbox=coerced_bbox,
            imagery_uri=imagery_uri,
            patch_size=psz,
            patch_overlap=povlp,
            iou_threshold=iou,
            score_threshold=score,
            compute_class=compute_class,
            case_id=case_id,
        )
    except asyncio.CancelledError:
        raise
    except DeepForestError as exc:
        logger.warning(
            "run_deepforest_tree_crown failed: %s (%s)", exc.error_code, exc
        )
        return {
            "status": "error",
            "error_code": exc.error_code,
            "error_message": str(exc),
        }
    except Exception as exc:  # noqa: BLE001 -- defensive catch-all
        logger.exception("run_deepforest_tree_crown unexpected failure")
        return {
            "status": "error",
            "error_code": "DEEPFOREST_INTERNAL_ERROR",
            "error_message": str(exc),
        }


async def _run_deepforest_chain(
    *,
    bbox: tuple[float, float, float, float] | None,
    imagery_uri: str | None,
    patch_size: int,
    patch_overlap: float,
    iou_threshold: float,
    score_threshold: float,
    compute_class: str | None,
    case_id: str | None,
) -> LayerURI:
    """The stage -> dispatch -> wait -> publish chain (cancellable; raises
    ``DeepForestError`` on any failure before a layer)."""
    from .publish_layer import publish_layer
    from .solver import (
        SolverNotRegisteredError,
        run_solver,
        select_compute_class,
        wait_for_completion,
    )

    run_id = new_ulid()

    # --- Resolve the RGB COG handle (caller-supplied or NAIP fetch) ---------
    staged_imagery_uri = imagery_uri
    if staged_imagery_uri is None:
        assert bbox is not None  # guarded by the caller
        staged_imagery_uri = await _fetch_naip_rgb_uri(bbox)
    if not (
        staged_imagery_uri.startswith("s3://")
        or staged_imagery_uri.startswith("gs://")
    ):
        # The ephemeral Batch worker has no agent-box FS access -- a non-object-
        # store imagery handle cannot be read. Reject loudly BEFORE the Spot
        # submit (the same honesty guard _run_solver_aws_batch enforces).
        raise DeepForestError(
            "DEEPFOREST_IMAGERY_FAILED",
            message=(
                f"imagery_uri must be an s3:// / gs:// COG the Batch worker can "
                f"download (the worker has no access to the agent box FS); got "
                f"{staged_imagery_uri!r}"
            ),
            details={"run_id": run_id},
        )

    # --- Stage the build_spec to S3 (sync boto3 off the loop) ---------------
    build_spec_uri = await asyncio.to_thread(
        stage_deepforest_build_spec,
        staged_imagery_uri,
        patch_size=patch_size,
        patch_overlap=patch_overlap,
        iou_threshold=iou_threshold,
        score_threshold=score_threshold,
        run_id=run_id,
        bbox=bbox,
    )

    # --- Pick the compute class from the patch-count estimate ---------------
    chosen_class = compute_class
    if chosen_class is None:
        tiles = (
            estimate_deepforest_tiles(bbox, patch_size=patch_size)
            if bbox is not None
            else 0
        )
        chosen_class = select_compute_class(tiles)

    # --- Dispatch through the generic run_solver / wait_for_completion seam --
    # Until the services/workers/deepforest Batch worker + its
    # SOLVER_WORKFLOW_REGISTRY["deepforest"] entry + GRACE2_AWS_BATCH_JOB_DEF_
    # DEEPFOREST job-def are provisioned (an ORCHESTRATOR seam), run_solver
    # raises SolverNotRegisteredError -- surfaced as the honest typed
    # DEEPFOREST_WORKER_UNAVAILABLE (never a fabricated layer).
    try:
        handle = run_solver(
            solver=DEEPFOREST_SOLVER_NAME,
            model_setup_uri=build_spec_uri,
            compute_class=chosen_class,
        )
    except SolverNotRegisteredError as exc:
        raise DeepForestError(
            "DEEPFOREST_WORKER_UNAVAILABLE",
            message=(
                "the DeepForest tree-crown Batch worker is not deployed yet "
                "(services/workers/deepforest + the 'deepforest' solver-registry "
                "entry + GRACE2_AWS_BATCH_JOB_DEF_DEEPFOREST job-def are not "
                f"provisioned): {exc}"
            ),
            details={"run_id": run_id, "build_spec_uri": build_spec_uri},
        ) from exc

    run_result = await wait_for_completion(handle)

    # Honesty floor: a non-complete Batch result is a hard failure (no layer).
    if run_result.status != "complete":
        raise DeepForestError(
            "DEEPFOREST_SOLVE_FAILED",
            message=(
                "tree-crown Batch solve did not complete "
                f"(status={run_result.status}, error_code={run_result.error_code}): "
                f"{run_result.error_message or run_result.cancellation_reason or ''}"
            ),
            details={"run_id": run_id, "output_uri": run_result.output_uri},
        )

    # --- Resolve the crown vector handle from the worker's completion -------
    batch_run_id = getattr(run_result, "run_id", None) or run_id
    crown_uri = await asyncio.to_thread(
        _resolve_vector_from_result, run_result, batch_run_id
    )
    if not crown_uri:
        raise DeepForestError(
            "DEEPFOREST_OUTPUT_MISSING",
            message=(
                "tree-crown Batch solve completed but produced no "
                "tree_crowns.fgb (honesty floor: an empty output is not a "
                "successful layer -- no crowns detected, or the worker failed)."
            ),
            details={"run_id": batch_run_id, "output_uri": run_result.output_uri},
        )

    # --- Publish the crown vector -------------------------------------------
    layer_id = f"tree-crowns-{batch_run_id}"
    try:
        wms_url = await asyncio.to_thread(
            publish_layer,
            layer_uri=crown_uri,
            layer_id=layer_id,
            case_id=case_id,
        )
    except Exception as exc:  # noqa: BLE001 -- publish-failure path
        raise DeepForestError(
            "DEEPFOREST_PUBLISH_FAILED",
            message=f"failed to publish the tree-crown vector: {exc}",
            details={"run_id": batch_run_id, "crown_uri": crown_uri},
        ) from exc

    logger.info(
        "run_deepforest_tree_crown complete run_id=%s layer_id=%s uri=%s",
        batch_run_id,
        layer_id,
        crown_uri,
    )
    return LayerURI(
        layer_id=layer_id,
        name="Detected Tree Crowns",
        layer_type="vector",
        uri=wms_url,
        style_preset="tree_crowns",
        role="primary",
        bbox=bbox,
    )


def _resolve_vector_from_result(run_result: Any, batch_run_id: str) -> str | None:
    """Resolve the crown FlatGeobuf s3:// URI from the RunResult / completion.json.

    Prefers the completion's ``output_uris`` (read off S3 by run_id); falls back
    to composing the canonical ``<runs>/<run_id>/tree_crowns.fgb`` path. Sync
    (boto3) -- the caller runs it off the loop via ``asyncio.to_thread``.
    """
    from .cache import storage_scheme
    from .solver import _get_runs_bucket, _try_get_completion_s3  # type: ignore[attr-defined]

    runs_bucket = _get_runs_bucket()
    manifest = _try_get_completion_s3(runs_bucket, batch_run_id)
    if isinstance(manifest, dict):
        uris = [str(u) for u in (manifest.get("output_uris") or [])]
        hit = resolve_crown_vector_uri(uris)
        if hit:
            return hit
    # Fallback: the canonical output path under the runs prefix.
    scheme = storage_scheme()
    return f"{scheme}://{runs_bucket}/{batch_run_id}/{CROWN_OUTPUT_NAME}"


async def _fetch_naip_rgb_uri(bbox: tuple[float, float, float, float]) -> str:
    """Fetch a NAIP RGB COG for the AOI; return its s3:// URI.

    NAIP is the CONUS sub-metre RGB source (the data-source fallback norm:
    NAIP -> [future Maxar] -> honest typed error). Reuses the existing
    ``fetch_naip`` tool (its output is an s3:// cache COG handle). ``fetch_naip``
    is synchronous (a cached read-through) so it runs off the loop.
    """
    from .fetch_naip import fetch_naip

    try:
        layer = await asyncio.to_thread(fetch_naip, bbox)
    except Exception as exc:  # noqa: BLE001
        raise DeepForestError(
            "DEEPFOREST_IMAGERY_FAILED",
            message=(
                f"failed to fetch NAIP RGB imagery for the AOI (DeepForest needs a "
                f"sub-metre RGB COG; NAIP is CONUS-only): {exc}"
            ),
        ) from exc
    uri = getattr(layer, "uri", None)
    if not uri:
        raise DeepForestError(
            "DEEPFOREST_IMAGERY_FAILED",
            message=(
                "NAIP fetch returned no COG URI for the AOI (no NAIP coverage? "
                "DeepForest needs sub-metre RGB -- narrow to a CONUS AOI)."
            ),
        )
    return str(uri)
