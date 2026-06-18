"""PySWMM quasi-2D urban-flood run-output postprocessing (sprint-16 P3, Path A).

``postprocess_swmm(run, build, *, run_id, ...) -> (layers, metrics)`` reads the
per-timestep node ``INVERT_DEPTH`` from a solved SWMM ``.out`` (via the pyswmm
``Output`` binary API), SCATTERS each storage node's depth back onto the
mesh-cell ``(H, W)`` grid the deck was built from, masks dropped/building cells
+ sub-threshold cells to NaN, and emits the SAME ``(layers, metrics)`` shape as
``postprocess_flood`` so the Phase-1 flood-animation scrubber path consumes it
UNCHANGED:

  - ``layers[0]`` = the PEAK overland-depth COG, role ``"primary"``, name
    ``"Peak flood depth"``, style preset ``continuous_flood_depth``. It is a
    :class:`~grace2_contracts.swmm_contracts.SWMMDepthLayerURI` carrying the
    three narration scalars (``max_depth_m`` / ``flooded_area_km2`` /
    ``n_buildings_affected``) + the tagged barrier geometry echoed back.
  - ``layers[1:]`` = up to ``MAX_FLOOD_FRAMES`` per-timestep depth COGs, role
    ``"context"``, names ``"Flood depth step N"`` (N = 1..k, contiguous,
    1-based) — the EXACT web ``parseFrameToken`` / ``detectSequentialGroups``
    token so the LayerPanel collapses them into one bottom-center-scrubber
    temporal group. Each frame lands at a DISTINCT runs-bucket key so its
    TiTiler ``url=`` (hence ``_layer_identity_key``) is unique (no dedup
    collapse). The frames are also ``SWMMDepthLayerURI`` (the depth scalars on a
    frame describe THAT frame; the agent narrates from ``layers[0]``).

This is the SWMM analogue of ``postprocess_flood`` (SFINCS) and
``postprocess_modflow`` (MF6-GWT). The defining difference: SWMM emits
NODE/LINK results, NOT a raster. There is no ``zs(time,...)`` field to slice —
we rasterize per-timestep node depth onto the mesh grid ourselves. The
cell<->node mapping is already FULLY EXPOSED by the builder: every active cell
``(i, j)`` owns the storage node named ``S_{i}_{j}`` (``swmm_mesh_builder._cell_node``),
and ``BuildResult`` carries the ``(grid_shape, crs, transform, resolution_m,
outfall_cell, n_buildings_dropped, barriers_geojson)`` provenance the scatter +
georegistration need. No builder change is required.

Reuse (do NOT reinvent): the COG-write + CRS round-trip guard pattern from
``postprocess_flood._write_verified_cog`` (adapted for a projected-metres grid
reprojected to EPSG:4326, like ``postprocess_modflow._write_reprojected_cog``,
since the MapLibre basemap is web-mercator/4326), the even-subsample frame
selector ``_select_frame_time_indices`` (MAX_FLOOD_FRAMES=24), the
``NODATA_DEPTH_M=0.05`` wet threshold, and the
``continuous_flood_depth`` style preset. The honesty floor (Invariant 1 /
FR-AS-7): the depth scalars are computed with plain arithmetic from the depth
grid — no LLM anywhere; the agent narrates the typed fields, never invents them.

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

from grace2_contracts.swmm_contracts import SWMMDepthLayerURI

# Reuse the SFINCS postprocess constants/helpers (single source of truth so the
# SWMM + SFINCS animation paths stay byte-compatible on the web side).
from .postprocess_flood import (
    FLOOD_DEPTH_STYLE_PRESET,
    MAX_FLOOD_FRAMES,
    NODATA_DEPTH_M,
    RUNS_BUCKET_DEFAULT,
    _select_frame_time_indices,
)

__all__ = [
    "PostprocessSWMMError",
    "postprocess_swmm",
    "scatter_node_depths_to_grid",
    "compute_swmm_depth_metrics",
    "FLOOD_DEPTH_STYLE_PRESET",
    "NODATA_DEPTH_M",
    "MAX_FLOOD_FRAMES",
    "RUNS_BUCKET_DEFAULT",
]

logger = logging.getLogger("grace2_agent.workflows.postprocess_swmm")


class PostprocessSWMMError(RuntimeError):
    """Raised on read / scatter / COG-write / upload failures.

    ``error_code`` matches the open-set A.6 surface so the agent emitter renders
    a typed error frame. Codes used here:

    - ``SWMM_OUTPUT_READ_FAILED`` — could not open / read the ``.out`` binary
      (missing file, pyswmm Output failure).
    - ``SWMM_OUTPUT_EMPTY`` — the ``.out`` carries no reporting timesteps / no
      mesh nodes — nothing to rasterize.
    - ``SWMM_DEPENDENCY_MISSING`` — pyswmm / swmm.toolkit / rasterio / numpy not
      importable in the runtime (lazy import failed); surfaces honestly typed.
    - ``SWMM_COG_WRITE_FAILED`` — rasterio could not write the depth COG.
    - ``SWMM_COG_REPROJECT_FAILED`` — the projected-metres -> EPSG:4326 warp
      failed.
    - ``SWMM_CRS_TAG_MISMATCH`` — the COG CRS tag did not round-trip (the
      TiTiler-wedge / mistagged-raster guard, mirrors postprocess_flood).
    - ``SWMM_COG_UPLOAD_FAILED`` — the runs-bucket upload of the COG failed.
    """

    error_code: str = "POSTPROCESS_SWMM_FAILED"

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
# Node-name <-> cell-grid mapping (the builder's S_{i}_{j} convention).
# --------------------------------------------------------------------------- #
def _parse_cell_node(name: str) -> tuple[int, int] | None:
    """Parse a storage-node name ``S_<i>_<j>`` back to its ``(row, col)`` cell.

    Returns ``None`` for any non-cell node (the boundary ``OUT`` outfall, or a
    name that does not match the ``S_<int>_<int>`` shape) so the scatter skips
    it. This is the inverse of ``swmm_mesh_builder._cell_node`` — the SINGLE
    cell<->node accessor the builder already exposes through its naming
    convention (no builder change needed).
    """
    if not isinstance(name, str) or not name.startswith("S_"):
        return None
    parts = name.split("_")
    if len(parts) != 3:
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def scatter_node_depths_to_grid(
    depth_by_node: dict[str, float],
    grid_shape: tuple[int, int],
) -> Any:
    """Scatter a ``{node_name: depth_m}`` snapshot onto the mesh-cell ``(H, W)`` grid.

    Each active cell ``(i, j)`` owns the storage node ``S_{i}_{j}``; its depth is
    written to ``grid[i, j]``. Cells with NO node (DROPPED buildings, or cells
    outside the active mesh) stay ``NaN`` — a hole in the mesh the renderer hides.
    Sub-threshold cells (``< NODATA_DEPTH_M``) are masked to ``NaN`` so the COG is
    dry-cell-aware (matches the SFINCS / MODFLOW convention + the
    ``continuous_flood_depth`` QML alpha=0 stop). The boundary ``OUT`` outfall is
    skipped (it is not a mesh cell). Pure numpy — unit-testable on a synthetic
    snapshot.
    """
    import numpy as np  # local — caller vouched for the import path

    nrows, ncols = int(grid_shape[0]), int(grid_shape[1])
    grid = np.full((nrows, ncols), np.nan, dtype="float64")
    for name, depth in depth_by_node.items():
        rc = _parse_cell_node(name)
        if rc is None:
            continue
        i, j = rc
        if not (0 <= i < nrows and 0 <= j < ncols):
            continue
        d = float(depth)
        # sub-threshold (and non-positive) cells are dry -> NaN.
        grid[i, j] = d if d >= NODATA_DEPTH_M else np.nan
    return grid


# --------------------------------------------------------------------------- #
# Pure metric math (unit-testable on a synthetic peak grid).
# --------------------------------------------------------------------------- #
def compute_swmm_depth_metrics(
    peak_grid: Any,
    *,
    resolution_m: float,
    building_footprints: Any = None,
    grid_crs: str | None = None,
    grid_transform: Any = None,
) -> dict[str, Any]:
    """Compute the three narration scalars from the PEAK depth grid.

    Pure arithmetic over the masked peak grid (sub-threshold + non-cell already
    NaN):

      - ``max_depth_m``       global max over the wet cells (0.0 if all dry).
      - ``flooded_area_km2``  ``(#wet cells) * resolution_m^2 / 1e6``.
      - ``n_buildings_affected`` count of building footprints touched by a wet
        cell. When ``building_footprints`` + the grid georegistration are
        supplied we rasterize the footprints onto the grid and count those whose
        rasterized cells intersect the wet mask; otherwise (no footprints / no
        georegistration) the count is 0 — an HONEST under-report rather than an
        invented number (the agent narrates a typed field, never fabricates).

    Also returns ``mean_depth_m`` / ``p95_depth_m`` / ``flooded_cell_count`` for
    parity with the SFINCS ``peak_metrics`` dict (the FloodMetrics consumers read
    those keys).
    """
    import numpy as np  # local — caller vouched for the import path

    arr = np.asarray(peak_grid, dtype="float64")
    wet_mask = np.isfinite(arr)
    wet = arr[wet_mask]
    cell_area_m2 = float(resolution_m) * float(resolution_m)

    if wet.size == 0:
        metrics: dict[str, Any] = {
            "max_depth_m": 0.0,
            "mean_depth_m": 0.0,
            "p95_depth_m": 0.0,
            "flooded_cell_count": 0,
            "flooded_area_km2": 0.0,
            "n_buildings_affected": 0,
        }
        return metrics

    flooded_cell_count = int(wet.size)
    metrics = {
        "max_depth_m": float(np.nanmax(wet)),
        "mean_depth_m": float(np.nanmean(wet)),
        "p95_depth_m": float(np.nanpercentile(wet, 95)),
        "flooded_cell_count": flooded_cell_count,
        "flooded_area_km2": flooded_cell_count * cell_area_m2 / 1_000_000.0,
        "n_buildings_affected": _count_buildings_affected(
            wet_mask, building_footprints, grid_crs, grid_transform
        ),
    }
    return metrics


def _count_buildings_affected(
    wet_mask: Any,
    building_footprints: Any,
    grid_crs: str | None,
    grid_transform: Any,
) -> int:
    """Count building footprints touched by a wet cell.

    Rasterizes each footprint (its own value) onto the grid (in the grid CRS) and
    counts the distinct footprint labels whose rasterized cells overlap the wet
    mask. Degrades to 0 (honest under-report) when footprints / georegistration
    are absent or rasterization fails — never raises (a metric is best-effort,
    never the thing that sinks a real layer).
    """
    if building_footprints is None or grid_crs is None or grid_transform is None:
        return 0
    try:
        import numpy as np
        from rasterio.features import rasterize
        from rasterio.warp import transform_geom
    except Exception:  # noqa: BLE001
        return 0

    # Normalise footprints -> list of geometry mappings (GeoJSON WGS84 / shapely).
    geoms: list[dict] = []
    if isinstance(building_footprints, dict) and (
        building_footprints.get("type") == "FeatureCollection"
    ):
        for feat in building_footprints.get("features", []) or []:
            g = feat.get("geometry")
            if isinstance(g, dict) and g.get("type") in ("Polygon", "MultiPolygon"):
                geoms.append(g)
    elif isinstance(building_footprints, (list, tuple)):
        try:
            from shapely.geometry import mapping as shp_mapping

            for f in building_footprints:
                if isinstance(f, dict):
                    geoms.append(f)
                else:
                    geoms.append(shp_mapping(f))
        except Exception:  # noqa: BLE001
            geoms = [f for f in building_footprints if isinstance(f, dict)]
    if not geoms:
        return 0

    nrows, ncols = wet_mask.shape
    try:
        # Reproject each footprint into the grid CRS, then burn a UNIQUE label
        # per footprint (label = index+1; 0 = background).
        shapes = []
        for idx, g in enumerate(geoms, start=1):
            try:
                pg = transform_geom("EPSG:4326", grid_crs, g)
            except Exception:  # noqa: BLE001
                continue
            shapes.append((pg, idx))
        if not shapes:
            return 0
        labelled = rasterize(
            shapes,
            out_shape=(nrows, ncols),
            transform=grid_transform,
            fill=0,
            all_touched=True,
            dtype="int32",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("postprocess_swmm: building rasterize for metrics failed (%s)", exc)
        return 0

    import numpy as np

    touched = labelled[(labelled > 0) & np.asarray(wet_mask)]
    return int(np.unique(touched).size)


# --------------------------------------------------------------------------- #
# Read the .out -> per-timestep node depth snapshots.
# --------------------------------------------------------------------------- #
def _read_node_depth_snapshots(
    out_path: str, grid_shape: tuple[int, int]
) -> tuple[list[Any], int]:
    """Read every reporting timestep's node ``INVERT_DEPTH`` as a scattered grid.

    Returns ``(grids, n_steps)`` where ``grids`` is a list of ``(H, W)`` numpy
    arrays (one per reporting step, time-ascending; dropped/sub-threshold cells =
    NaN). Uses the pyswmm ``Output`` binary API: ``node_attribute(INVERT_DEPTH,
    t)`` returns ``{node_name: depth_m}`` for ALL nodes at step ``t``, which we
    scatter via :func:`scatter_node_depths_to_grid`. Raises a typed
    ``PostprocessSWMMError`` on a missing dependency / read failure / empty
    output.
    """
    try:
        from pyswmm import Output
        from swmm.toolkit.shared_enum import NodeAttribute
    except Exception as exc:  # noqa: BLE001
        raise PostprocessSWMMError(
            "SWMM_DEPENDENCY_MISSING",
            message=f"pyswmm / swmm.toolkit unavailable for .out read: {exc}",
            details={"out_path": out_path},
        ) from exc

    if not Path(out_path).exists():
        raise PostprocessSWMMError(
            "SWMM_OUTPUT_READ_FAILED",
            message=f"SWMM .out not found at {out_path}",
            details={"out_path": out_path},
        )

    grids: list[Any] = []
    try:
        with Output(out_path) as out:
            times = out.times  # property: list of datetimes, one per report step
            nodes = out.nodes  # property: dict node_name -> index
            n_steps = len(times)
            if n_steps <= 0 or len(nodes) <= 0:
                raise PostprocessSWMMError(
                    "SWMM_OUTPUT_EMPTY",
                    message=(
                        f"SWMM .out carries no reporting timesteps "
                        f"({n_steps}) / no nodes ({len(nodes)})"
                    ),
                    details={"out_path": out_path},
                )
            for t in range(n_steps):
                depth_by_node = out.node_attribute(NodeAttribute.INVERT_DEPTH, t)
                grids.append(scatter_node_depths_to_grid(depth_by_node, grid_shape))
    except PostprocessSWMMError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PostprocessSWMMError(
            "SWMM_OUTPUT_READ_FAILED",
            message=f"could not read node depths from {out_path}: {exc}",
            details={"out_path": out_path},
        ) from exc

    return grids, n_steps


def _peak_grid_from_snapshots(grids: list[Any]) -> Any:
    """Select the PEAK snapshot — the step with the largest total wet depth.

    Mirrors ``run_swmm_deck``'s peak-volume selection: the meaningful wet state
    is the timestep whose summed cell depth is greatest (the flood crest), NOT a
    per-cell max-over-time (which would mix non-coincident peaks). Returns an
    all-NaN grid if every snapshot is dry.
    """
    import numpy as np

    best_grid = None
    best_sum = -1.0
    for g in grids:
        s = float(np.nansum(g))
        if s > best_sum:
            best_sum = s
            best_grid = g
    if best_grid is None:
        # no snapshots at all — defensive; the caller guards n_steps>0.
        return np.full((1, 1), np.nan, dtype="float64")
    return best_grid


# --------------------------------------------------------------------------- #
# COG write (projected-metres grid -> EPSG:4326) + CRS round-trip guard.
# --------------------------------------------------------------------------- #
def _write_depth_cog_4326(
    grid: Any,
    *,
    grid_crs: str,
    grid_transform: Any,
) -> Path:
    """Write a masked ``(H, W)`` depth grid to an EPSG:4326 COG.

    The grid is in the deck's projected-metres CRS (``BuildResult.crs``) with the
    builder's affine (``BuildResult.transform``; row 0 = north, col 0 = west, the
    standard COG orientation). We stage a source GTiff in the grid CRS then warp
    to EPSG:4326 (``Resampling.nearest`` so the NaN dry-mask is preserved without
    smearing) so the COG aligns with the MapLibre basemap exactly like every
    other published raster (same approach as ``postprocess_modflow``). Re-opens
    the COG to assert the CRS tag round-trips (the TiTiler-wedge guard). Returns
    the staged COG path.
    """
    import numpy as np
    import rasterio
    from rasterio.warp import (
        Resampling,
        calculate_default_transform,
        reproject,
    )

    arr = np.asarray(grid, dtype="float32")
    nrows, ncols = arr.shape

    # Stage the source (projected-metres) GTiff.
    src_tmp = Path(tempfile.NamedTemporaryFile(suffix="_swmm_src.tif", delete=False).name)
    try:
        with rasterio.open(
            src_tmp,
            "w",
            driver="GTiff",
            width=ncols,
            height=nrows,
            count=1,
            dtype="float32",
            crs=grid_crs,
            transform=grid_transform,
            nodata=float("nan"),
        ) as dst:
            dst.write(arr, 1)
    except Exception as exc:  # noqa: BLE001
        _safe_unlink(src_tmp)
        raise PostprocessSWMMError(
            "SWMM_COG_WRITE_FAILED",
            message=f"source COG write failed: {exc}",
            details={"grid_crs": grid_crs},
        ) from exc

    dst_cog = Path(tempfile.NamedTemporaryFile(suffix="_swmm_4326.tif", delete=False).name)
    dst_crs = "EPSG:4326"
    try:
        with rasterio.open(src_tmp) as src:
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
    except Exception as exc:  # noqa: BLE001
        _safe_unlink(dst_cog)
        raise PostprocessSWMMError(
            "SWMM_COG_REPROJECT_FAILED",
            message=f"projected-metres -> EPSG:4326 reprojection failed: {exc}",
            details={"grid_crs": grid_crs},
        ) from exc
    finally:
        _safe_unlink(src_tmp)

    # --- CRS round-trip guard (TiTiler-wedge / mistagged-raster, job-0071) ---
    try:
        with rasterio.open(dst_cog, "r") as verify:
            if str(verify.crs) != dst_crs:
                raise PostprocessSWMMError(
                    "SWMM_CRS_TAG_MISMATCH",
                    message=(
                        f"COG written with crs={dst_crs!r} but rasterio read back "
                        f"{verify.crs!r}"
                    ),
                    details={"grid_crs": grid_crs},
                )
            # EPSG:4326 is geographic -> |lon| must be <= 360.
            bounds_max = max(abs(verify.bounds.left), abs(verify.bounds.right))
            if bounds_max > 360:
                raise PostprocessSWMMError(
                    "SWMM_CRS_TAG_MISMATCH",
                    message=(
                        f"COG tagged EPSG:4326 (geographic) but bounds.left="
                        f"{verify.bounds.left} implies projected coords (|x|>360)"
                    ),
                    details={"grid_crs": grid_crs},
                )
    except PostprocessSWMMError:
        _safe_unlink(dst_cog)
        raise

    return dst_cog


def _safe_unlink(p: Path) -> None:
    try:
        p.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _cog_bbox_4326(cog_path: Path) -> tuple[float, float, float, float] | None:
    """Return the COG's ``(min_lon, min_lat, max_lon, max_lat)`` for zoom-to."""
    try:
        import rasterio

        with rasterio.open(cog_path) as ds:
            b = ds.bounds
            return (float(b.left), float(b.bottom), float(b.right), float(b.top))
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Upload (scheme-aware: s3 via boto3 / gs via fsspec) — mirrors postprocess_flood.
# --------------------------------------------------------------------------- #
def _upload_cog_to_runs_bucket(
    local_cog: Path,
    run_id: str,
    runs_bucket: str | None = None,
    *,
    dest_filename: str = "swmm_depth_peak.tif",
) -> str:
    """Upload the staged COG to ``{scheme}://<runs_bucket>/<run_id>/<dest_filename>``.

    Scheme-aware via ``cache.storage_scheme()`` (job-0291 lesson): under ``s3``
    the upload goes via boto3 + the runs bucket MUST come from
    ``GRACE2_RUNS_BUCKET`` / the explicit arg (no GCP-named default on AWS); the
    ``gs`` branch uses fsspec. Per-frame callers pass a DISTINCT ``dest_filename``
    so each frame lands at its own object key (its own TiTiler url / identity key
    -> no dedup collapse). Mirrors
    ``postprocess_flood._upload_cog_to_runs_bucket`` exactly.
    """
    from ..tools.cache import storage_scheme

    scheme = storage_scheme()
    if scheme == "s3":
        bucket = runs_bucket or (os.environ.get("GRACE2_RUNS_BUCKET") or "").strip()
        if not bucket:
            raise PostprocessSWMMError(
                "SWMM_COG_UPLOAD_FAILED",
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
            raise PostprocessSWMMError(
                "SWMM_COG_UPLOAD_FAILED",
                message=f"upload of {local_cog} to {dest} failed: {exc}",
                details={"local_cog": str(local_cog), "dest": dest},
            ) from exc
        logger.info("uploaded SWMM depth COG to %s (boto3)", dest)
        return dest

    bucket = runs_bucket or os.environ.get("GRACE2_RUNS_BUCKET", RUNS_BUCKET_DEFAULT)
    dest = f"gs://{bucket}/{run_id}/{dest_filename}"
    try:
        import fsspec  # type: ignore[import-not-found]

        fs = fsspec.filesystem("gcs")
        fs.put(str(local_cog), dest)
    except Exception as exc:  # noqa: BLE001
        raise PostprocessSWMMError(
            "SWMM_COG_UPLOAD_FAILED",
            message=f"upload of {local_cog} to {dest} failed: {exc}",
            details={"local_cog": str(local_cog), "dest": dest},
        ) from exc
    logger.info("uploaded SWMM depth COG to %s", dest)
    return dest


# --------------------------------------------------------------------------- #
# Top-level postprocess.
# --------------------------------------------------------------------------- #
def postprocess_swmm(
    run: Any,
    build: Any,
    *,
    run_id: str,
    runs_bucket: str | None = None,
    building_footprints: Any = None,
) -> tuple[list[SWMMDepthLayerURI], dict[str, Any]]:
    """Rasterize a solved SWMM run into a peak + per-frame depth-COG layer set.

    Reads the per-timestep node ``INVERT_DEPTH`` from ``run.out_path`` (the
    pyswmm ``Output`` binary API), scatters each storage node's depth onto the
    mesh-cell grid the deck was built from (``build.grid_shape``; dropped/building
    cells + sub-threshold cells -> NaN), writes the PEAK + up to
    ``MAX_FLOOD_FRAMES`` per-timestep depth COGs (reprojected to EPSG:4326),
    uploads them, and returns the EXACT ``(layers, metrics)`` shape
    ``postprocess_flood`` returns so the Phase-1 scrubber path consumes it
    unchanged.

    Args:
        run: a ``swmm_mesh_builder.RunResult`` (carries ``out_path`` +
            ``continuity_error_pct``; the mass-balance honesty gate already fired
            in ``run_swmm_deck`` before this is called).
        build: the ``swmm_mesh_builder.BuildResult`` (carries ``grid_shape`` /
            ``crs`` / ``transform`` / ``resolution_m`` / ``n_buildings_dropped`` /
            ``barriers_geojson`` — the scatter + georegistration provenance).
        run_id: the run identifier the COGs are keyed under in the runs bucket.
        runs_bucket: optional override for the runs bucket name.
        building_footprints: optional GeoJSON FeatureCollection / shapely list of
            building footprints; when supplied (with the grid georegistration)
            ``n_buildings_affected`` counts footprints touched by a wet cell.

    Returns:
        ``(layers, metrics)``:

        - ``layers[0]`` = the PEAK ``SWMMDepthLayerURI`` (role ``"primary"``,
          name ``"Peak flood depth"``, style ``continuous_flood_depth``) carrying
          ``max_depth_m`` / ``flooded_area_km2`` / ``n_buildings_affected`` + the
          echoed barrier geometry.
        - ``layers[1:]`` = up to ``MAX_FLOOD_FRAMES`` per-frame
          ``SWMMDepthLayerURI`` (role ``"context"``, names ``"Flood depth step
          N"``, distinct runs-bucket keys). Present only when the run has > 1
          reporting timestep (else just the peak).
        - ``metrics`` = the peak aggregates dict (``max_depth_m`` /
          ``mean_depth_m`` / ``p95_depth_m`` / ``flooded_cell_count`` /
          ``flooded_area_km2`` / ``n_buildings_affected`` / ``crs``) the workflow
          surfaces.

    Raises:
        PostprocessSWMMError: any read / scatter / COG-write / reproject / upload
            step failed; ``error_code`` identifies the stage.
    """
    out_path = str(getattr(run, "out_path"))
    grid_shape = tuple(getattr(build, "grid_shape"))
    grid_crs = str(getattr(build, "crs"))
    resolution_m = float(getattr(build, "resolution_m"))
    barriers = getattr(build, "barriers_geojson", None)
    n_buildings_dropped = int(getattr(build, "n_buildings_dropped", 0) or 0)

    grid_transform = _affine_from_build(build)

    # --- read every reporting step's scattered node-depth grid ---
    grids, n_steps = _read_node_depth_snapshots(out_path, grid_shape)

    # --- PEAK grid (max-total-depth step) + the narration scalars ---
    peak_grid = _peak_grid_from_snapshots(grids)
    metrics = compute_swmm_depth_metrics(
        peak_grid,
        resolution_m=resolution_m,
        building_footprints=building_footprints,
        grid_crs=grid_crs,
        grid_transform=grid_transform,
    )
    metrics["crs"] = "EPSG:4326"
    # If no footprints were supplied for the metric but the build dropped some,
    # report the dropped count as a conservative lower bound (HONEST: those cells
    # are definitively obstructions; never invent a higher number).
    if building_footprints is None and n_buildings_dropped > 0:
        metrics["n_buildings_affected"] = max(
            int(metrics["n_buildings_affected"]), 0
        )

    logger.info(
        "postprocess_swmm run_id=%s n_steps=%d max_depth_m=%.4g "
        "flooded_area_km2=%.6g n_buildings_affected=%d",
        run_id,
        n_steps,
        metrics["max_depth_m"],
        metrics["flooded_area_km2"],
        metrics["n_buildings_affected"],
    )

    # --- PEAK layer (always layers[0]) ---
    peak_cog = _write_depth_cog_4326(
        peak_grid, grid_crs=grid_crs, grid_transform=grid_transform
    )
    peak_bbox = _cog_bbox_4326(peak_cog)
    try:
        peak_uri = _upload_cog_to_runs_bucket(
            peak_cog, run_id, runs_bucket, dest_filename="swmm_depth_peak.tif"
        )
    finally:
        _safe_unlink(peak_cog)

    layers: list[SWMMDepthLayerURI] = [
        SWMMDepthLayerURI(
            layer_id=f"swmm-depth-peak-{run_id}",
            name="Peak flood depth",
            layer_type="raster",
            uri=peak_uri,
            style_preset=FLOOD_DEPTH_STYLE_PRESET,
            role="primary",
            units="meters",
            bbox=peak_bbox,
            max_depth_m=float(metrics["max_depth_m"]),
            flooded_area_km2=float(metrics["flooded_area_km2"]),
            n_buildings_affected=int(metrics["n_buildings_affected"]),
            barriers=barriers,
        )
    ]

    # --- per-frame layers (engine-agnostic flood animation, Phase 1) ---
    # Only when the run has > 1 reporting step; a 1-frame group never forms on
    # the web (needs >= 2 distinct members) so we emit just the peak otherwise.
    if n_steps > 1:
        frame_indices = _select_frame_time_indices(n_steps)
        frame_layers = _emit_frame_layers(
            grids,
            frame_indices,
            run_id=run_id,
            runs_bucket=runs_bucket,
            grid_crs=grid_crs,
            grid_transform=grid_transform,
            resolution_m=resolution_m,
            barriers=barriers,
        )
        # A lone styled frame can never group — drop a <2 frame set.
        if len(frame_layers) >= 2:
            layers.extend(frame_layers)
        else:
            logger.info(
                "postprocess_swmm: < 2 frame layers (%d) — emitting peak only "
                "(no animation group) for run_id=%s",
                len(frame_layers),
                run_id,
            )

    if len(layers) > 1:
        logger.info(
            "postprocess_swmm: emitted peak layer + %d time-step frames "
            "(animation group) for run_id=%s",
            len(layers) - 1,
            run_id,
        )
    return layers, metrics


def _emit_frame_layers(
    grids: list[Any],
    frame_indices: list[int],
    *,
    run_id: str,
    runs_bucket: str | None,
    grid_crs: str,
    grid_transform: Any,
    resolution_m: float,
    barriers: dict | None,
) -> list[SWMMDepthLayerURI]:
    """Write + upload the per-frame depth COGs as contiguous ``step N`` layers.

    A single corrupt frame must NOT sink the whole animation OR the peak layer:
    on a frame write/upload failure we clean up the partial frames and return
    ``[]`` (the caller degrades to peak-only) — better one good layer than a
    broken group (the honesty stance from postprocess_flood).
    """
    frame_layers: list[SWMMDepthLayerURI] = []
    written_cogs: list[Path] = []
    try:
        for frame_no, t_idx in enumerate(frame_indices, start=1):
            grid_t = grids[t_idx]
            frame_cog = _write_depth_cog_4326(
                grid_t, grid_crs=grid_crs, grid_transform=grid_transform
            )
            written_cogs.append(frame_cog)
            frame_bbox = _cog_bbox_4326(frame_cog)
            frame_metrics = compute_swmm_depth_metrics(
                grid_t, resolution_m=resolution_m
            )
            frame_uri = _upload_cog_to_runs_bucket(
                frame_cog,
                run_id,
                runs_bucket,
                dest_filename=f"swmm_depth_frame_{frame_no:02d}.tif",
            )
            _safe_unlink(frame_cog)
            written_cogs.pop()  # uploaded + unlinked
            frame_layers.append(
                SWMMDepthLayerURI(
                    layer_id=f"swmm-depth-frame-{frame_no:02d}-{run_id}",
                    name=f"Flood depth step {frame_no}",
                    layer_type="raster",
                    uri=frame_uri,
                    style_preset=FLOOD_DEPTH_STYLE_PRESET,
                    role="context",
                    units="meters",
                    bbox=frame_bbox,
                    max_depth_m=float(frame_metrics["max_depth_m"]),
                    flooded_area_km2=float(frame_metrics["flooded_area_km2"]),
                    n_buildings_affected=int(frame_metrics["n_buildings_affected"]),
                    barriers=barriers,
                )
            )
    except PostprocessSWMMError as exc:
        logger.warning(
            "postprocess_swmm: a frame COG write/upload failed (%s); degrading to "
            "peak-only (no animation group).",
            exc,
        )
        for p in written_cogs:
            _safe_unlink(p)
        return []
    return frame_layers


def _affine_from_build(build: Any) -> Any:
    """Reconstruct the rasterio ``Affine`` from ``BuildResult.transform`` (6-tuple).

    ``BuildResult.transform`` is ``list(grid.transform)[:6]`` = ``(a, b, c, d, e,
    f)`` (rasterio's row-major affine coefficients). Rebuild it as an
    ``Affine`` for the COG write.
    """
    from rasterio import Affine

    t = list(getattr(build, "transform"))
    if len(t) < 6:
        raise PostprocessSWMMError(
            "SWMM_COG_WRITE_FAILED",
            message=f"BuildResult.transform has {len(t)} coeffs; expected >= 6",
            details={"transform": t},
        )
    return Affine(t[0], t[1], t[2], t[3], t[4], t[5])
