"""Landlab run-output postprocessing (sprint-17 — NEW engine).

``postprocess_landlab(field_cog_path, *, run_id, analysis, result, ...) ->
(layers, metrics)`` takes the worker-produced field COG (the LandslideProbability
``probability_of_failure`` field, or the OverlandFlow peak ``surface_water__depth``
field — a single-band GeoTIFF in the grid's projected-metres CRS), reprojects it
to EPSG:4326 with the CRS round-trip guard (the TiTiler-wedge / mistagged-raster
guard, identical to ``postprocess_swmm._write_depth_cog_4326`` /
``postprocess_modflow._write_reprojected_cog``), uploads it to the runs bucket,
and emits a :class:`~grace2_contracts.landlab_contracts.LandlabSusceptibilityLayerURI`
carrying the typed narration scalars.

Reuse (do NOT reinvent): the COG reproject-to-4326 + CRS round-trip guard pattern
from ``postprocess_swmm`` (the MapLibre basemap is EPSG:4326/web-mercator, so the
metric-CRS worker field must be warped). The honesty floor (Invariant 1 /
FR-AS-7): the narration scalars are the worker's deterministically-computed
``result`` block (unstable-area fraction / min FoS / mean PoF) — no LLM anywhere;
the agent narrates the typed fields, never invents them. The scalars are
recomputed from the field as a fallback when the worker result block is absent
(e.g. an older completion schema), so a missing result never produces invented
numbers.

Tier separation (Invariant 5): the COG lands in the runs bucket (scheme-aware
via ``cache.storage_scheme()``); the agent does not re-render — ``publish_layer``
/ TiTiler serves the tiles from the URI on the envelope.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from grace2_contracts.landlab_contracts import LandlabSusceptibilityLayerURI

__all__ = [
    "PostprocessLandlabError",
    "postprocess_landlab",
    "compute_landlab_metrics",
    "LANDSLIDE_STYLE_PRESET",
    "OVERLAND_STYLE_PRESET",
    "UNSTABLE_PROBABILITY_THRESHOLD",
]

logger = logging.getLogger("grace2_agent.workflows.postprocess_landlab")

#: The TiTiler style preset key the orchestrator registers in
#: ``_TITILER_STYLE_REGISTRY`` (the shared-append snippet). Susceptibility =
#: probability of failure in [0, 1], rendered with a reversed red->green diverging
#: ramp (rdylgn_r) so HIGH susceptibility = RED, LOW = GREEN.
LANDSLIDE_STYLE_PRESET: str = "continuous_landslide_susceptibility"

#: The overland-flow chain reuses the existing flood-depth preset (a depth field,
#: same physical quantity as SFINCS/SWMM depth — additive reuse, no new preset).
OVERLAND_STYLE_PRESET: str = "continuous_flood_depth"

#: Mirror of the worker threshold for recomputing the unstable fraction when the
#: completion result block is absent (kept in sync with
#: ``services/workers/landlab/component_chain.UNSTABLE_PROBABILITY_THRESHOLD``).
UNSTABLE_PROBABILITY_THRESHOLD: float = 0.75

#: Wet-depth floor for the overland-flow unstable/wet fraction fallback (mirrors
#: the flood NODATA_DEPTH_M).
OVERLAND_WET_DEPTH_M: float = 0.05

#: Runs-bucket default (the gs:// fallback only; AWS uses GRACE2_RUNS_BUCKET).
RUNS_BUCKET_DEFAULT: str = "grace-2-hazard-prod-runs"


class PostprocessLandlabError(RuntimeError):
    """Raised on read / reproject / COG-write / upload failures.

    ``error_code`` matches the open-set A.6 surface so the agent emitter renders
    a typed error frame. Codes used here:

    - ``LANDLAB_OUTPUT_READ_FAILED`` — the field COG is missing / unreadable.
    - ``LANDLAB_DEPENDENCY_MISSING`` — rasterio / numpy not importable.
    - ``LANDLAB_COG_REPROJECT_FAILED`` — the projected-metres -> 4326 warp failed.
    - ``LANDLAB_CRS_TAG_MISMATCH`` — the COG CRS tag did not round-trip (the
      TiTiler-wedge / mistagged-raster guard).
    - ``LANDLAB_COG_UPLOAD_FAILED`` — the runs-bucket upload of the COG failed.
    """

    error_code: str = "POSTPROCESS_LANDLAB_FAILED"

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
# Pure metric math (unit-testable on a synthetic field grid).
# --------------------------------------------------------------------------- #
def compute_landlab_metrics(field: Any, *, analysis: str) -> dict[str, Any]:
    """Compute the three narration scalars from the output field grid.

    Pure arithmetic over the masked field (NaN = inactive/no-data):

      - landslide chain: ``unstable_area_fraction`` = fraction of active cells
        with probability >= ``UNSTABLE_PROBABILITY_THRESHOLD``;
        ``mean_probability_of_failure`` = mean probability over active cells;
        ``min_factor_of_safety`` is NOT derivable from the probability field
        alone, so it is left at 0.0 here (the authoritative value comes from the
        worker's deterministic FoS field via the completion ``result`` block).
      - overland chain: ``unstable_area_fraction`` = wet-cell fraction
        (depth >= ``OVERLAND_WET_DEPTH_M``); ``min_factor_of_safety`` carries the
        PEAK depth (m); ``mean_probability_of_failure`` = 0.0.

    Used as the FALLBACK when the worker ``result`` block is absent (a missing
    result yields an HONEST recomputed value, never an invented number).
    """
    import numpy as np

    arr = np.asarray(field, dtype="float64")
    active = np.isfinite(arr)
    vals = arr[active]
    n_active = int(vals.size)

    if n_active == 0:
        return {
            "unstable_area_fraction": 0.0,
            "min_factor_of_safety": 0.0,
            "mean_probability_of_failure": 0.0,
            "active_cell_count": 0,
        }

    if analysis == "overland_flow":
        wet_frac = float(np.count_nonzero(vals >= OVERLAND_WET_DEPTH_M) / n_active)
        max_depth = float(np.max(vals))
        return {
            "unstable_area_fraction": wet_frac,
            "min_factor_of_safety": max_depth,  # peak depth (units disambiguate)
            "mean_probability_of_failure": 0.0,
            "active_cell_count": n_active,
        }

    # landslide_probability (default): the field IS probability of failure.
    unstable_frac = float(
        np.count_nonzero(vals >= UNSTABLE_PROBABILITY_THRESHOLD) / n_active
    )
    mean_pof = float(np.mean(vals))
    return {
        "unstable_area_fraction": unstable_frac,
        "min_factor_of_safety": 0.0,  # authoritative FoS comes from worker result
        "mean_probability_of_failure": mean_pof,
        "active_cell_count": n_active,
    }


def _resolve_scalars(
    field: Any,
    *,
    analysis: str,
    result: dict[str, Any] | None,
) -> dict[str, float]:
    """Prefer the worker's deterministic ``result`` block; fall back to recompute.

    The worker computed the scalars with the FULL component output (incl. the
    deterministic FoS field the probability raster does not carry), so its
    ``result`` block is authoritative. When it is absent / incomplete we recompute
    from the field (honest under-report, never invented). Returns the three
    contract scalars clamped to their valid ranges.
    """
    recomputed = compute_landlab_metrics(field, analysis=analysis)

    def _pick(key: str) -> float:
        if isinstance(result, dict) and result.get(key) is not None:
            try:
                return float(result[key])
            except (TypeError, ValueError):
                pass
        return float(recomputed[key])

    unstable = max(0.0, min(1.0, _pick("unstable_area_fraction")))
    min_fos = max(0.0, _pick("min_factor_of_safety"))
    mean_pof = max(0.0, min(1.0, _pick("mean_probability_of_failure")))
    return {
        "unstable_area_fraction": unstable,
        "min_factor_of_safety": min_fos,
        "mean_probability_of_failure": mean_pof,
    }


# --------------------------------------------------------------------------- #
# COG reproject (projected-metres field -> EPSG:4326) + CRS round-trip guard.
# --------------------------------------------------------------------------- #
def _reproject_field_cog_4326(src_cog: Path) -> tuple[Path, tuple[float, float, float, float] | None]:
    """Reproject a metric-CRS field COG to EPSG:4326 (the MapLibre basemap CRS).

    Mirrors ``postprocess_swmm._write_depth_cog_4326``'s warp + CRS round-trip
    guard (``Resampling.nearest`` preserves the NaN no-data without smearing).
    Returns ``(dst_cog_path, bbox_4326)``.
    """
    try:
        import rasterio
        from rasterio.warp import (
            Resampling,
            calculate_default_transform,
            reproject,
        )
    except Exception as exc:  # noqa: BLE001
        raise PostprocessLandlabError(
            "LANDLAB_DEPENDENCY_MISSING",
            message=f"rasterio unavailable for COG reproject: {exc}",
        ) from exc

    if not src_cog.exists():
        raise PostprocessLandlabError(
            "LANDLAB_OUTPUT_READ_FAILED",
            message=f"Landlab field COG not found at {src_cog}",
            details={"src_cog": str(src_cog)},
        )

    dst_cog = Path(
        tempfile.NamedTemporaryFile(suffix="_landlab_4326.tif", delete=False).name
    )
    dst_crs = "EPSG:4326"
    try:
        with rasterio.open(src_cog) as src:
            if src.crs is None:
                raise PostprocessLandlabError(
                    "LANDLAB_OUTPUT_READ_FAILED",
                    message=f"Landlab field COG {src_cog} carries no CRS tag",
                    details={"src_cog": str(src_cog)},
                )
            transform, width, height = calculate_default_transform(
                src.crs, dst_crs, src.width, src.height, *src.bounds
            )
            profile = {
                "driver": "COG",
                "crs": dst_crs,
                "transform": transform,
                "width": width,
                "height": height,
                "count": 1,
                "dtype": "float32",
                "nodata": float("nan"),
                "compress": "LZW",
            }
            with rasterio.open(dst_cog, "w", **profile) as dst:
                reproject(
                    source=rasterio.band(src, 1),
                    destination=rasterio.band(dst, 1),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.nearest,
                )
    except PostprocessLandlabError:
        _safe_unlink(dst_cog)
        raise
    except Exception as exc:  # noqa: BLE001
        _safe_unlink(dst_cog)
        raise PostprocessLandlabError(
            "LANDLAB_COG_REPROJECT_FAILED",
            message=f"projected-metres -> EPSG:4326 reprojection failed: {exc}",
            details={"src_cog": str(src_cog)},
        ) from exc

    # --- CRS round-trip guard (TiTiler-wedge / mistagged-raster) ---
    import rasterio

    try:
        with rasterio.open(dst_cog, "r") as verify:
            if str(verify.crs) != dst_crs:
                raise PostprocessLandlabError(
                    "LANDLAB_CRS_TAG_MISMATCH",
                    message=(
                        f"COG written with crs={dst_crs!r} but rasterio read back "
                        f"{verify.crs!r}"
                    ),
                )
            bounds_max = max(abs(verify.bounds.left), abs(verify.bounds.right))
            if bounds_max > 360:
                raise PostprocessLandlabError(
                    "LANDLAB_CRS_TAG_MISMATCH",
                    message=(
                        f"COG tagged EPSG:4326 (geographic) but bounds.left="
                        f"{verify.bounds.left} implies projected coords (|x|>360)"
                    ),
                )
            b = verify.bounds
            bbox = (float(b.left), float(b.bottom), float(b.right), float(b.top))
    except PostprocessLandlabError:
        _safe_unlink(dst_cog)
        raise

    return dst_cog, bbox


def _safe_unlink(p: Path) -> None:
    try:
        p.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _read_field_array(cog_path: Path) -> Any:
    """Read the field COG band 1 as a numpy array (NaN no-data preserved)."""
    try:
        import numpy as np
        import rasterio
    except Exception as exc:  # noqa: BLE001
        raise PostprocessLandlabError(
            "LANDLAB_DEPENDENCY_MISSING",
            message=f"rasterio/numpy unavailable for field read: {exc}",
        ) from exc
    if not cog_path.exists():
        raise PostprocessLandlabError(
            "LANDLAB_OUTPUT_READ_FAILED",
            message=f"Landlab field COG not found at {cog_path}",
            details={"cog_path": str(cog_path)},
        )
    with rasterio.open(cog_path) as ds:
        arr = ds.read(1).astype("float64")
        nodata = ds.nodata
    if nodata is not None and np.isfinite(nodata):
        arr = np.where(arr == nodata, np.nan, arr)
    return arr


# --------------------------------------------------------------------------- #
# Upload (scheme-aware: s3 via boto3 / gs via fsspec) — mirrors postprocess_swmm.
# --------------------------------------------------------------------------- #
def _upload_cog_to_runs_bucket(
    local_cog: Path,
    run_id: str,
    runs_bucket: str | None = None,
    *,
    dest_filename: str = "landlab_susceptibility.tif",
) -> str:
    """Upload the staged COG to ``{scheme}://<runs_bucket>/<run_id>/<dest_filename>``.

    Scheme-aware via ``cache.storage_scheme()``: under ``s3`` the upload goes via
    boto3 + the runs bucket MUST come from ``GRACE2_RUNS_BUCKET`` / the explicit
    arg; the ``gs`` branch uses fsspec. Mirrors
    ``postprocess_swmm._upload_cog_to_runs_bucket`` exactly.
    """
    from ..tools.cache import storage_scheme

    scheme = storage_scheme()
    if scheme == "s3":
        bucket = runs_bucket or (os.environ.get("GRACE2_RUNS_BUCKET") or "").strip()
        if not bucket:
            raise PostprocessLandlabError(
                "LANDLAB_COG_UPLOAD_FAILED",
                message=(
                    "GRACE2_RUNS_BUCKET must be set under "
                    "GRACE2_STORAGE_BACKEND=s3 (no GCP-named default on AWS)"
                ),
                details={"local_cog": str(local_cog)},
            )
        dest = f"s3://{bucket}/{run_id}/{dest_filename}"
        try:
            from ..tools.solver import _get_s3_client

            with local_cog.open("rb") as fh:
                _get_s3_client().put_object(
                    Bucket=bucket,
                    Key=f"{run_id}/{dest_filename}",
                    Body=fh,
                    ContentType="image/tiff",
                )
        except Exception as exc:  # noqa: BLE001
            raise PostprocessLandlabError(
                "LANDLAB_COG_UPLOAD_FAILED",
                message=f"upload of {local_cog} to {dest} failed: {exc}",
                details={"local_cog": str(local_cog), "dest": dest},
            ) from exc
        logger.info("uploaded Landlab field COG to %s (boto3)", dest)
        return dest

    bucket = runs_bucket or os.environ.get("GRACE2_RUNS_BUCKET", RUNS_BUCKET_DEFAULT)
    dest = f"gs://{bucket}/{run_id}/{dest_filename}"
    try:
        import fsspec  # type: ignore[import-not-found]

        fs = fsspec.filesystem("gcs")
        fs.put(str(local_cog), dest)
    except Exception as exc:  # noqa: BLE001
        raise PostprocessLandlabError(
            "LANDLAB_COG_UPLOAD_FAILED",
            message=f"upload of {local_cog} to {dest} failed: {exc}",
            details={"local_cog": str(local_cog), "dest": dest},
        ) from exc
    logger.info("uploaded Landlab field COG to %s", dest)
    return dest


# --------------------------------------------------------------------------- #
# Top-level postprocess.
# --------------------------------------------------------------------------- #
def postprocess_landlab(
    field_cog_path: str | Path,
    *,
    run_id: str,
    analysis: str = "landslide_probability",
    result: dict[str, Any] | None = None,
    runs_bucket: str | None = None,
) -> tuple[list[LandlabSusceptibilityLayerURI], dict[str, Any]]:
    """Reproject a Landlab field COG to 4326 + emit a susceptibility layer.

    Reads the worker-produced field COG (probability of failure for the landslide
    chain; peak depth for the overland chain), reprojects it to EPSG:4326 (with
    the CRS round-trip guard), uploads it, and returns the ``(layers, metrics)``
    shape the composer consumes.

    Args:
        field_cog_path: the LOCAL on-disk path to the worker's field COG (the
            composer downloads it from the Batch output before calling this).
        run_id: the run identifier the output COG is keyed under.
        analysis: the component chain that produced the field ("landslide_
            probability" | "overland_flow") — selects the style preset + the
            metric interpretation.
        result: the worker's deterministic ``result`` block from completion.json
            (the authoritative narration scalars); recomputed from the field when
            absent.
        runs_bucket: optional override for the runs bucket name.

    Returns:
        ``(layers, metrics)``:
        - ``layers[0]`` = the susceptibility ``LandlabSusceptibilityLayerURI``
          (role ``"primary"``) carrying the three narration scalars; style preset
          is ``continuous_landslide_susceptibility`` (landslide) or
          ``continuous_flood_depth`` (overland).
        - ``metrics`` = the scalar dict + ``crs`` + ``analysis``.

    Raises:
        PostprocessLandlabError: any read / reproject / upload step failed;
            ``error_code`` identifies the stage.
    """
    src = Path(field_cog_path)

    field = _read_field_array(src)
    scalars = _resolve_scalars(field, analysis=analysis, result=result)

    dst_cog, bbox = _reproject_field_cog_4326(src)
    try:
        uri = _upload_cog_to_runs_bucket(
            dst_cog, run_id, runs_bucket, dest_filename="landlab_susceptibility.tif"
        )
    finally:
        _safe_unlink(dst_cog)

    is_landslide = analysis != "overland_flow"
    style = LANDSLIDE_STYLE_PRESET if is_landslide else OVERLAND_STYLE_PRESET
    if is_landslide:
        name = "Landslide susceptibility"
        units = "probability"
    else:
        name = "Peak overland depth"
        units = "meters"

    layer = LandlabSusceptibilityLayerURI(
        layer_id=f"landlab-susceptibility-{run_id}",
        name=name,
        layer_type="raster",
        uri=uri,
        style_preset=style,
        role="primary",
        units=units,
        bbox=bbox,
        unstable_area_fraction=float(scalars["unstable_area_fraction"]),
        min_factor_of_safety=float(scalars["min_factor_of_safety"]),
        mean_probability_of_failure=float(scalars["mean_probability_of_failure"]),
    )

    metrics = {
        "analysis": analysis,
        "crs": "EPSG:4326",
        "unstable_area_fraction": float(scalars["unstable_area_fraction"]),
        "min_factor_of_safety": float(scalars["min_factor_of_safety"]),
        "mean_probability_of_failure": float(scalars["mean_probability_of_failure"]),
    }
    logger.info(
        "postprocess_landlab run_id=%s analysis=%s unstable_frac=%.4f "
        "min_fos=%.4f mean_pof=%.4f uri=%s",
        run_id,
        analysis,
        metrics["unstable_area_fraction"],
        metrics["min_factor_of_safety"],
        metrics["mean_probability_of_failure"],
        uri,
    )
    return [layer], metrics
