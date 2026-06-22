"""MODFLOW GWT run-output postprocessing (sprint-13 Stage 2, job-0227).

``postprocess_modflow(run_outputs_uri, *, run_id, model_crs) -> PlumeLayerURI``
reads the MF6-GWT concentration output (``gwt_model.ucn``, a binary
HEADFILE-format array via ``flopy.utils.HeadFile`` with ``text="CONCENTRATION"``),
takes the FINAL-TIMESTEP max-over-layers concentration grid, reprojects it from
the deck's projected (UTM) grid to an EPSG:4326 Cloud-Optimized GeoTIFF,
computes the two narration scalars (``max_concentration_mgl`` +
``plume_area_km2``), uploads the COG, and returns a typed ``PlumeLayerURI``.

This is the MODFLOW analogue of ``postprocess_flood`` (job-0042). Differences:

  * The source is a UCN concentration array, not a SFINCS NetCDF depth field.
  * The grid georegistration (origin / cell size / CRS) is read from the
    DECK manifest's ``model_crs`` (the OQ-MOD-3 handoff field) + the flopy grid
    object — not from a CRS variable inside the output file. MF6 binary output
    carries NO CRS; the deck's ``model_crs`` is authoritative.
  * The output is reprojected to EPSG:4326 so the plume COG aligns with the
    web client's MapLibre basemap exactly like every other published raster.

Determinism boundary (Invariant 1 / Decision H / FR-AS-7): ``PlumeLayerURI``
carries ``max_concentration_mgl`` + ``plume_area_km2`` as typed numbers the
agent narrates — never free-generated. This module computes them from the
concentration array with plain arithmetic; no LLM anywhere.

Tier separation (Invariant 5): the COG lands in the runs bucket; the agent does
not re-render. ``publish_layer`` bridges the COG to QGIS Server WMS so the
client renders it (mocked in tests; callable in production).
"""

from __future__ import annotations

import glob
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from grace2_contracts.modflow_contracts import PlumeLayerURI, SeepageLayerURI

logger = logging.getLogger("grace2_agent.workflows.postprocess_modflow")

__all__ = [
    "PostprocessMODFLOWError",
    "postprocess_modflow",
    "postprocess_river_seepage",
    "compute_plume_metrics",
    "compute_seepage_metrics",
    "PLUME_DETECTION_FLOOR_MGL",
    "PLUME_STYLE_PRESET",
    "SEEPAGE_STYLE_PRESET",
    "GWF_CBC_FILENAME",
    "RUNS_BUCKET_DEFAULT",
]

#: Default runs bucket (matches the SFINCS substrate).
RUNS_BUCKET_DEFAULT: str = "grace-2-hazard-prod-runs"

#: QML style preset name for the plume concentration COG. The styles/ package
#: authors the matching ``continuous_plume_concentration.qml``; surfaced as
#: OQ-MOD-PLUME-PRESET-QML for the engine styles follow-up.
PLUME_STYLE_PRESET: str = "continuous_plume_concentration"

#: Detection floor (mg/L): cells at or below this are NOT counted as plume
#: (kickoff: "cells above a 0.001 mg/L floor"). Also masked to NaN in the COG
#: so the renderer hides clean cells.
PLUME_DETECTION_FLOOR_MGL: float = 0.001

#: Concentration output filename the GWT OC package writes (gwt_adapter).
GWT_UCN_FILENAME: str = "gwt_model.ucn"

#: GWF cell-by-cell budget filename (carries the RIV leakage term). The OC
#: BUDGET FILEOUT uses this bare name; the recursive glob captures it wherever
#: the entrypoint reorg lands it (root, per run_modflow output_globs).
GWF_CBC_FILENAME: str = "gwf_model.cbc"

#: TiTiler style preset for the diverging gaining/losing river-seepage COG
#: (sprint-17 J9). Registered in publish_layer._TITILER_STYLE_REGISTRY by the
#: orchestrator's shared-appends merge as ("-2,2", "rdbu").
SEEPAGE_STYLE_PRESET: str = "diverging_river_seepage"


class PostprocessMODFLOWError(RuntimeError):
    """Raised on read / extraction / reproject / upload failures.

    Open-set A.6 ``error_code`` values:

    - ``PLUME_OUTPUT_READ_FAILED`` — could not locate / read ``gwt_model.ucn``.
    - ``PLUME_OUTPUT_EMPTY`` — the concentration array has no timesteps / cells.
    - ``PLUME_REPROJECT_FAILED`` — the UTM → EPSG:4326 warp failed.
    - ``PLUME_COG_WRITE_FAILED`` — rasterio could not write the COG.
    - ``PLUME_COG_UPLOAD_FAILED`` — the GCS upload of the COG failed.
    """

    error_code: str = "POSTPROCESS_MODFLOW_FAILED"

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
# Pure metric math (unit-testable on synthetic arrays)
# --------------------------------------------------------------------------- #


def compute_plume_metrics(
    final_grid: Any,
    cell_area_m2: float,
    *,
    floor_mgl: float = PLUME_DETECTION_FLOOR_MGL,
) -> tuple[float, float]:
    """Compute (max_concentration_mgl, plume_area_km2) from a 2D conc grid.

    Pure arithmetic over the FINAL-timestep, max-over-layers concentration grid
    (a 2D ``numpy`` array in mg/L). A cell counts toward the plume iff its
    concentration is strictly greater than ``floor_mgl``.

    Args:
        final_grid: 2D array (rows × cols) of concentration in mg/L.
        cell_area_m2: per-cell area in m² (``delr * delc`` for a structured grid).
        floor_mgl: detection floor; cells ≤ this are clean (not plume).

    Returns:
        ``(max_concentration_mgl, plume_area_km2)``. Both ≥ 0. ``max`` is the
        global maximum over the grid (clamped at 0 so a numerically-negative
        dispersion artifact never narrates as a negative concentration);
        ``area`` is ``(#cells > floor) * cell_area_m2 / 1e6``.
    """
    import numpy as np  # local — caller vouched for the import path

    arr = np.asarray(final_grid, dtype="float64")
    if arr.size == 0:
        return 0.0, 0.0
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return 0.0, 0.0
    max_conc = float(np.max(finite))
    max_conc = max(0.0, max_conc)  # negative dispersion artifact → 0 (never narrate < 0)
    plume_cells = int(np.count_nonzero(finite > floor_mgl))
    plume_area_km2 = float(plume_cells) * float(cell_area_m2) / 1_000_000.0
    return max_conc, plume_area_km2


def compute_seepage_metrics(
    seepage_grid: Any,
) -> tuple[float, float, float, int]:
    """Compute (total_leakage, gaining, losing, river_cell_count) from a 2D grid.

    Pure arithmetic over the per-cell signed RIV exchange grid (m^3/day, NaN
    where no reach cell). MF6 RIV budget sign: a positive ``q`` is flow FROM the
    boundary INTO the cell, i.e. the river LOSES water to the aquifer (seepage
    in, a losing reach); a negative ``q`` is flow OUT of the cell to the river,
    i.e. the river GAINS water from the aquifer (baseflow, a gaining reach).

    Returns:
        ``(total_leakage_m3_day, gaining_m3_day, losing_m3_day, river_cell_count)``:
          * total_leakage_m3_day: net SIGNED sum over all reach cells
            (positive = net losing/recharging the aquifer).
          * gaining_m3_day: total MAGNITUDE of negative (gaining) flux, >= 0.
          * losing_m3_day: total MAGNITUDE of positive (losing) flux, >= 0.
          * river_cell_count: number of finite (reach) cells.
    """
    import numpy as np  # local — caller vouched for the import path

    arr = np.asarray(seepage_grid, dtype="float64")
    if arr.size == 0:
        return 0.0, 0.0, 0.0, 0
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return 0.0, 0.0, 0.0, 0
    total = float(np.sum(finite))
    losing = float(np.sum(finite[finite > 0.0]))  # river -> aquifer
    gaining = float(-np.sum(finite[finite < 0.0]))  # aquifer -> river (magnitude)
    return total, gaining, losing, int(finite.size)


def _read_riv_seepage_grid(
    cbc_path: Path, nrow: int, ncol: int
) -> Any:
    """Read the GWF cbc RIV budget into a 2D per-cell signed seepage grid.

    The RIV cell-by-cell budget is a list/recarray with a ``node`` (1-based
    cell id) + ``q`` (exchange flow) field per reach cell. We scatter the
    last-timestep ``q`` values onto an (nrow, ncol) grid (NaN elsewhere) so the
    seepage COG renders only the reach. flopy's ``CellBudgetFile.get_data(
    text="RIV")`` returns the recarray; the ``node`` is a flat 0-based-after-
    decrement structured-grid index = lay*nrow*ncol + row*ncol + col.
    """
    try:
        import flopy.utils  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "SEEPAGE_OUTPUT_READ_FAILED",
            message=f"flopy/numpy not importable: {exc}",
            details={"cbc_path": str(cbc_path)},
        ) from exc

    try:
        cbc = flopy.utils.CellBudgetFile(str(cbc_path))
        record_names = {
            (r.strip() if isinstance(r, str) else r.strip().decode())
            for r in cbc.get_unique_record_names(decode=True)
        }
        if not any("RIV" in n.upper() for n in record_names):
            raise PostprocessMODFLOWError(
                "SEEPAGE_OUTPUT_EMPTY",
                message=(
                    f"no RIV budget record in {cbc_path}; "
                    f"records present: {sorted(record_names)}"
                ),
                details={"cbc_path": str(cbc_path)},
            )
        riv_data = cbc.get_data(text="RIV")
        if not riv_data:
            raise PostprocessMODFLOWError(
                "SEEPAGE_OUTPUT_EMPTY",
                message=f"RIV budget record present but empty in {cbc_path}",
                details={"cbc_path": str(cbc_path)},
            )
    except PostprocessMODFLOWError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "SEEPAGE_OUTPUT_READ_FAILED",
            message=f"could not read RIV budget from {cbc_path}: {exc}",
            details={"cbc_path": str(cbc_path)},
        ) from exc

    last = riv_data[-1]
    grid = np.full((nrow, ncol), np.nan, dtype="float64")
    # The RIV budget recarray exposes the cell id under "node" (1-based flat).
    try:
        nodes = np.asarray(last["node"], dtype="int64")
        qvals = np.asarray(last["q"], dtype="float64")
    except Exception:  # noqa: BLE001 — list-style budget (older formats)
        # Fall back to attribute access on a list of records.
        nodes = np.asarray([int(r["node"]) for r in last], dtype="int64")
        qvals = np.asarray([float(r["q"]) for r in last], dtype="float64")
    cells_per_layer = nrow * ncol
    for node, q in zip(nodes, qvals):
        idx0 = int(node) - 1  # 1-based -> 0-based flat
        local = idx0 % cells_per_layer  # collapse layers onto the 2D grid
        row = local // ncol
        col = local % ncol
        if 0 <= row < nrow and 0 <= col < ncol:
            # Accumulate (a multi-layer reach maps several cells to one 2D cell).
            grid[row, col] = (
                q if np.isnan(grid[row, col]) else grid[row, col] + q
            )
    return grid


# --------------------------------------------------------------------------- #
# UCN read + grid georegistration
# --------------------------------------------------------------------------- #


def _resolve_ucn_path(run_outputs_uri: str) -> Path:
    """Locate ``gwt_model.ucn`` from a local dir / file:// / gs:// / s3:// run
    output.

    Local (``file://`` or a bare path): search the dir tree for the UCN file.
    gs:// : fetch via fsspec into a temp dir (mirrors postprocess_flood).
    s3:// (job-0292b — the local-backend runs prefix): fetch via **boto3**
    through the solver module's shared S3 client seam (job-0289 lesson). The
    local-mode live-evidence path always passes a local dir.
    """
    if run_outputs_uri.startswith("s3://"):
        from ..tools.solver import _get_s3_client

        tmpdir = Path(tempfile.mkdtemp(prefix="modflow-output-"))
        local_target = tmpdir / GWT_UCN_FILENAME
        source = (
            run_outputs_uri
            if run_outputs_uri.endswith(".ucn")
            else run_outputs_uri.rstrip("/") + f"/{GWT_UCN_FILENAME}"
        )
        bucket_name, _, obj_key = source[len("s3://"):].partition("/")
        try:
            import shutil as _shutil

            resp = _get_s3_client().get_object(Bucket=bucket_name, Key=obj_key)
            with local_target.open("wb") as fh:
                _shutil.copyfileobj(resp["Body"], fh)
        except Exception as exc:  # noqa: BLE001
            raise PostprocessMODFLOWError(
                "PLUME_OUTPUT_READ_FAILED",
                message=f"could not fetch UCN from {source}: {exc}",
                details={"run_outputs_uri": run_outputs_uri},
            ) from exc
        return local_target
    if run_outputs_uri.startswith("gs://"):
        try:
            import fsspec  # type: ignore[import-not-found]

            fs = fsspec.filesystem("gcs")
            tmpdir = Path(tempfile.mkdtemp(prefix="modflow-output-"))
            local_target = tmpdir / GWT_UCN_FILENAME
            prefix = run_outputs_uri.rstrip("/")
            candidate = (
                run_outputs_uri
                if run_outputs_uri.endswith(".ucn")
                else f"{prefix}/{GWT_UCN_FILENAME}"
            )
            fs.get(candidate, str(local_target))
            return local_target
        except Exception as exc:  # noqa: BLE001
            raise PostprocessMODFLOWError(
                "PLUME_OUTPUT_READ_FAILED",
                message=f"could not fetch UCN from {run_outputs_uri}: {exc}",
                details={"run_outputs_uri": run_outputs_uri},
            ) from exc

    p = Path(run_outputs_uri.replace("file://", ""))
    if p.is_file() and p.suffix == ".ucn":
        return p
    if p.is_dir():
        hits = sorted(glob.glob(str(p / "**" / GWT_UCN_FILENAME), recursive=True))
        if not hits:
            # any .ucn (defensive: an adapter could rename the stem).
            hits = sorted(glob.glob(str(p / "**" / "*.ucn"), recursive=True))
        if hits:
            return Path(hits[0])
    raise PostprocessMODFLOWError(
        "PLUME_OUTPUT_READ_FAILED",
        message=f"no {GWT_UCN_FILENAME} found under {run_outputs_uri}",
        details={"run_outputs_uri": run_outputs_uri},
    )


def _read_final_concentration(ucn_path: Path) -> Any:
    """Read the FINAL-timestep, max-over-layers concentration grid (mg/L, 2D).

    MF6 GWT concentration output is a binary HEADFILE-format array; flopy reads
    it via ``HeadFile(..., text="CONCENTRATION")``. ``get_data(totim=last)``
    returns a ``(nlay, nrow, ncol)`` array; we take ``nanmax`` over the layer
    axis to get a 2D worst-case (max-over-depth) grid the plume narrates.
    """
    try:
        import flopy.utils  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "PLUME_OUTPUT_READ_FAILED",
            message=f"flopy/numpy not importable: {exc}",
            details={"ucn_path": str(ucn_path)},
        ) from exc

    try:
        cobj = flopy.utils.HeadFile(str(ucn_path), text="CONCENTRATION")
        times = cobj.get_times()
        if not times:
            raise PostprocessMODFLOWError(
                "PLUME_OUTPUT_EMPTY",
                message=f"{ucn_path} carries no concentration timesteps",
                details={"ucn_path": str(ucn_path)},
            )
        data = cobj.get_data(totim=times[-1])  # (nlay, nrow, ncol)
    except PostprocessMODFLOWError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "PLUME_OUTPUT_READ_FAILED",
            message=f"could not read concentration from {ucn_path}: {exc}",
            details={"ucn_path": str(ucn_path)},
        ) from exc

    arr = np.asarray(data, dtype="float64")
    if arr.ndim == 3:
        # max over the layer axis → 2D worst-case-over-depth grid.
        final2d = np.nanmax(arr, axis=0)
    elif arr.ndim == 2:
        final2d = arr
    else:
        final2d = np.squeeze(arr)
        if final2d.ndim != 2:
            raise PostprocessMODFLOWError(
                "PLUME_OUTPUT_EMPTY",
                message=f"concentration array has shape {arr.shape}; cannot reduce to 2D",
                details={"ucn_path": str(ucn_path), "shape": list(arr.shape)},
            )
    # MF6 inactive/dry cells are flagged with a large sentinel (1e30). Mask them.
    final2d = np.where(np.abs(final2d) > 1e29, np.nan, final2d)
    return final2d


def _grid_georegistration_from_deck(deck_dir: str | None) -> dict[str, Any] | None:
    """Read grid origin + cell size from the deck via flopy (for the COG transform).

    The deck dir holds the GWT DIS package; flopy's modelgrid gives the
    lower-left origin (xorigin/yorigin) + cell widths (delr/delc). Returns None
    if the deck cannot be loaded (the caller then falls back to identity, which
    still yields valid metrics — only the geo-placement degrades).
    """
    if not deck_dir:
        return None
    try:
        import flopy  # type: ignore[import-not-found]

        sim = flopy.mf6.MFSimulation.load(sim_ws=str(deck_dir), verbosity_level=0)
        gwt = None
        for mname in sim.model_names:
            if mname.startswith("gwt"):
                gwt = sim.get_model(mname)
                break
        if gwt is None:
            return None
        mg = gwt.modelgrid
        return {
            "xorigin": float(mg.xoffset),
            "yorigin": float(mg.yoffset),
            "delr": float(mg.delr[0]),
            "delc": float(mg.delc[0]),
            "nrow": int(mg.nrow),
            "ncol": int(mg.ncol),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read deck georegistration from %s: %s", deck_dir, exc)
        return None


# --------------------------------------------------------------------------- #
# COG write + reproject + upload
# --------------------------------------------------------------------------- #


def _write_reprojected_cog(
    final2d: Any,
    model_crs: str,
    geo: dict[str, Any] | None,
    *,
    mask_below_floor: bool = True,
) -> Path:
    """Write the concentration grid to an EPSG:4326 COG, reprojecting from model_crs.

    The grid is in the deck's projected (UTM) CRS. We build the source transform
    from the grid origin + cell size (flopy's row 0 is the NORTH row, so the
    transform's top-left is yorigin + nrow*delc), tag it ``model_crs``, then warp
    to EPSG:4326 via rasterio's reprojection.

    Args:
        mask_below_floor: when True (the plume default — BYTE-IDENTICAL to the
            pre-J9 behavior), cells at/below ``PLUME_DETECTION_FLOOR_MGL`` are
            masked to NaN so the COG renders only the plume. When False (the J9
            river-seepage diverging layer), the array is written AS-IS (already
            NaN off the reach) so negative gaining values survive — masking by a
            positive floor would wrongly drop every gaining (negative) reach
            cell.
    """
    import numpy as np  # type: ignore[import-not-found]
    import rasterio  # type: ignore[import-not-found]
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    arr = np.asarray(final2d, dtype="float32")
    if mask_below_floor:
        # Mask clean cells (≤ floor) to NaN so the COG renders only the plume.
        arr_masked = np.where(
            arr > PLUME_DETECTION_FLOOR_MGL, arr, np.nan
        ).astype("float32")
    else:
        # Diverging seepage: keep the array as-is (NaN already marks off-reach).
        arr_masked = arr.astype("float32")
    nrow, ncol = arr_masked.shape

    if geo is not None:
        delr = geo["delr"]
        delc = geo["delc"]
        xorigin = geo["xorigin"]
        yorigin = geo["yorigin"]
        # flopy row 0 = north; rasterio's from_origin top-left = (west, north).
        west = xorigin
        north = yorigin + nrow * delc
        src_transform = rasterio.transform.from_origin(west, north, delr, delc)
    else:
        # Degraded fallback: identity transform (metrics still valid; placement
        # arbitrary). Logged by the caller via the None geo path.
        src_transform = rasterio.Affine.identity()

    # Stage the source (UTM) COG.
    src_tmp = Path(tempfile.NamedTemporaryFile(suffix="_src.tif", delete=False).name)
    try:
        with rasterio.open(
            src_tmp,
            "w",
            driver="GTiff",
            width=ncol,
            height=nrow,
            count=1,
            dtype="float32",
            crs=model_crs,
            transform=src_transform,
            nodata=float("nan"),
        ) as dst:
            dst.write(arr_masked, 1)
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "PLUME_COG_WRITE_FAILED",
            message=f"source COG write failed: {exc}",
            details={"model_crs": model_crs},
        ) from exc

    # Reproject UTM → EPSG:4326.
    dst_cog = Path(tempfile.NamedTemporaryFile(suffix="_4326.tif", delete=False).name)
    try:
        with rasterio.open(src_tmp) as src:
            dst_crs = "EPSG:4326"
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
                    resampling=Resampling.bilinear,
                )
    except PostprocessMODFLOWError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "PLUME_REPROJECT_FAILED",
            message=f"UTM→EPSG:4326 reprojection failed: {exc}",
            details={"model_crs": model_crs},
        ) from exc
    finally:
        try:
            src_tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    return dst_cog


def _cog_bbox_4326(cog_path: Path) -> tuple[float, float, float, float] | None:
    """Return the COG's (min_lon, min_lat, max_lon, max_lat) for zoom-to."""
    try:
        import rasterio  # type: ignore[import-not-found]

        with rasterio.open(cog_path) as ds:
            b = ds.bounds
            return (float(b.left), float(b.bottom), float(b.right), float(b.top))
    except Exception:  # noqa: BLE001
        return None


def _upload_cog(
    local_cog: Path,
    run_id: str,
    runs_bucket: str | None,
    *,
    cog_filename: str = "plume_concentration_4326.tif",
) -> str:
    """Upload the EPSG:4326 plume COG to the runs bucket; return its object URI.

    job-0292b (sprint-14-aws): scheme-aware per ``cache.storage_scheme()``.
    Under ``s3`` the upload goes via **boto3** (job-0289 lesson) and FAILS
    TYPED on a missing ``GRACE2_RUNS_BUCKET`` or an upload error — on the AWS
    deployment a silent ``file://`` fallback is exactly the debug-invisible
    no-render failure job-0241 burned on, so we surface it honestly instead
    (mirrors ``postprocess_flood._upload_cog_to_runs_bucket``). The default
    ``gs`` branch keeps its best-effort file:// fallback byte-identical (the
    offline-dev / local-mode path depends on it).

    In local mode (no GCS), the upload is skipped and the local ``file://`` URI
    is returned so the live-evidence path completes without cloud access.
    """
    from ..tools.cache import storage_scheme

    if storage_scheme() == "s3":
        bucket = runs_bucket or (os.environ.get("GRACE2_RUNS_BUCKET") or "").strip()
        if not bucket:
            raise PostprocessMODFLOWError(
                "PLUME_COG_UPLOAD_FAILED",
                message=(
                    "GRACE2_RUNS_BUCKET must be set under "
                    "GRACE2_STORAGE_BACKEND=s3 (no GCP-named default on AWS; "
                    "job-0292b)"
                ),
                details={"local_cog": str(local_cog)},
            )
        dest = f"s3://{bucket}/{run_id}/{cog_filename}"
        try:
            from ..tools.solver import _get_s3_client

            with local_cog.open("rb") as fh:
                _get_s3_client().put_object(
                    Bucket=bucket,
                    Key=f"{run_id}/{cog_filename}",
                    Body=fh,
                    ContentType="image/tiff",
                )
        except Exception as exc:  # noqa: BLE001
            raise PostprocessMODFLOWError(
                "PLUME_COG_UPLOAD_FAILED",
                message=f"upload of {local_cog} to {dest} failed: {exc}",
                details={"local_cog": str(local_cog), "dest": dest},
            ) from exc
        logger.info("uploaded plume COG to %s (boto3)", dest)
        return dest

    bucket = runs_bucket or os.environ.get("GRACE2_RUNS_BUCKET", RUNS_BUCKET_DEFAULT)
    dest = f"gs://{bucket}/{run_id}/{cog_filename}"
    try:
        import fsspec  # type: ignore[import-not-found]

        fs = fsspec.filesystem("gcs")
        fs.put(str(local_cog), dest)
        logger.info("uploaded plume COG to %s", dest)
        return dest
    except ImportError as exc:
        # job-0241: a missing fsspec[gcs] is a DEPLOY/ENV DEFECT (it is a
        # declared dependency), not a transient GCS error — this exact gap
        # made the Case 2 live plume silently fail to render (job-0235).
        # Classify it loudly so the next stale-venv regression is one log
        # line, not a basemap-only map.
        logger.error(
            "plume COG upload to %s SKIPPED — fsspec[gcs] not importable (%s). "
            "This is a deploy/env defect: fsspec is a declared dependency. "
            "The plume will fall back to file:// and will NOT render. "
            "Fix: pip install -e . in services/agent (installs fsspec[gcs]).",
            dest,
            exc,
        )
        return f"file://{local_cog}"
    except Exception as exc:  # noqa: BLE001
        # GCS-unavailable (auth, network, bucket): keep the local COG and
        # surface a file:// URI so the pipeline completes (offline-dev path).
        logger.warning(
            "plume COG upload to %s failed (%s); using local file:// URI",
            dest,
            exc,
        )
        return f"file://{local_cog}"


# --------------------------------------------------------------------------- #
# publish_layer dispatch (callable; mocked in tests)
# --------------------------------------------------------------------------- #


def _dispatch_publish_layer(
    cog_uri: str, layer_id: str, *, style_preset: str = PLUME_STYLE_PRESET
) -> str | None:
    """Publish the plume COG; return the WMS URL / tile template or None.

    Non-fatal: a publish failure (worker SA grant, GCS read) falls back to the
    COG URI so the rest of the envelope is usable. Skips publish entirely for
    non-object-store URIs (local mode has nothing for a tile server to read).

    job-0292b: ``s3://`` COGs pass through too — on the AWS deployment
    ``publish_layer`` returns a TiTiler XYZ tile TEMPLATE for them (the
    job-0290 ``GRACE2_TILE_SERVER_BASE`` path), which closes the job-0254
    PlumeLayerURI rendering gap on AWS the same way flood-depth COGs publish.
    """
    if not (cog_uri.startswith("gs://") or cog_uri.startswith("s3://")):
        # job-0241: loud, not silent — a non-object-store URI here means the
        # upload fell back (stale venv / auth / network) and the plume will
        # NOT appear on the map. The Case 2 live gate (job-0235) burned on
        # exactly this as a debug-invisible skip.
        logger.warning(
            "publish_layer SKIPPED for %s: COG URI is not gs:// or s3:// (%s); "
            "the plume will NOT render as a map layer. Check the object-store "
            "upload succeeded.",
            layer_id,
            cog_uri,
        )
        return None
    try:
        from ..tools.publish_layer import PublishLayerError, publish_layer

        wms_url = publish_layer(
            layer_uri=cog_uri,
            layer_id=layer_id,
            style_preset=style_preset,
        )
        logger.info("publish_layer succeeded layer_id=%s wms_url=%s", layer_id, wms_url)
        return wms_url
    except Exception as exc:  # noqa: BLE001
        # PublishLayerError or any import/dispatch failure: non-fatal.
        logger.warning("publish_layer failed for %s: %s", layer_id, exc)
        return None


# --------------------------------------------------------------------------- #
# Top-level postprocess
# --------------------------------------------------------------------------- #


def postprocess_modflow(
    run_outputs_uri: str,
    *,
    run_id: str,
    model_crs: str,
    deck_dir: str | None = None,
    runs_bucket: str | None = None,
    publish: bool = True,
) -> PlumeLayerURI:
    """Convert a MODFLOW GWT run's UCN output into a plume ``PlumeLayerURI``.

    Reads the final-timestep, max-over-layers concentration grid, reprojects it
    to an EPSG:4326 COG, computes the plume metrics, uploads + (optionally)
    publishes the COG, and returns the typed plume layer.

    Args:
        run_outputs_uri: the run output location (local dir / ``file://`` for the
            local path, ``gs://`` for the cloud path; finds ``gwt_model.ucn``).
        run_id: the run identifier the COG is keyed under in the runs bucket.
        model_crs: the deck's projected CRS (e.g. ``"EPSG:32617"``) — the
            OQ-MOD-3 handoff field the reprojection needs.
        deck_dir: optional on-disk deck dir for grid georegistration (origin +
            cell size). When ``None``, the COG uses an identity transform
            (metrics stay valid; geographic placement degrades).
        runs_bucket: optional override for the runs bucket name.
        publish: when True, dispatch ``publish_layer`` (mocked in tests).

    Returns:
        ``PlumeLayerURI`` with ``max_concentration_mgl`` + ``plume_area_km2``
        and (when published) a WMS ``uri``, else the COG ``gs://`` / ``file://``
        URI.

    Raises:
        PostprocessMODFLOWError: any read / reproject / write / upload step
            failed; ``error_code`` identifies the stage.
    """
    ucn_path = _resolve_ucn_path(run_outputs_uri)
    final2d = _read_final_concentration(ucn_path)

    geo = _grid_georegistration_from_deck(deck_dir)
    cell_area_m2 = (
        float(geo["delr"]) * float(geo["delc"]) if geo is not None else 2500.0
    )  # default 50 m cells if deck georegistration unavailable (gwt_adapter CELL_SIZE_M)

    max_conc, plume_area_km2 = compute_plume_metrics(final2d, cell_area_m2)
    logger.info(
        "postprocess_modflow run_id=%s max_concentration_mgl=%.6g plume_area_km2=%.6g",
        run_id,
        max_conc,
        plume_area_km2,
    )

    cog_path = _write_reprojected_cog(final2d, model_crs, geo)
    bbox_4326 = _cog_bbox_4326(cog_path)
    try:
        cog_uri = _upload_cog(cog_path, run_id, runs_bucket)
    finally:
        # The upload made a copy (cloud) or we returned the local path; only
        # unlink when we did NOT keep the local file as the URI.
        pass

    layer_id = f"plume-concentration-{run_id}"
    final_uri = cog_uri
    if publish:
        wms_url = _dispatch_publish_layer(cog_uri, layer_id)
        if wms_url:
            final_uri = wms_url

    return PlumeLayerURI(
        layer_id=layer_id,
        name="Contaminant Plume (peak concentration)",
        layer_type="raster",
        uri=final_uri,
        style_preset=PLUME_STYLE_PRESET,
        role="primary",
        units="mg/L",
        bbox=bbox_4326,
        max_concentration_mgl=max_conc,
        plume_area_km2=plume_area_km2,
    )


# --------------------------------------------------------------------------- #
# River-seepage postprocess (sprint-17 J9) — GWF cbc RIV budget -> seepage COG
# --------------------------------------------------------------------------- #


def _resolve_gwf_cbc_path(run_outputs_uri: str) -> Path:
    """Locate the GWF cell-by-cell budget (``gwf_model.cbc``) from a run output.

    Mirrors ``_resolve_ucn_path`` but targets the GWF budget file that carries
    the RIV leakage term. Local (``file://`` / bare path): search the dir tree.
    ``s3://`` / ``gs://``: fetch the cbc into a temp dir via the same boto3 /
    fsspec seams the UCN resolver uses.
    """
    if run_outputs_uri.startswith("s3://"):
        from ..tools.solver import _get_s3_client

        tmpdir = Path(tempfile.mkdtemp(prefix="modflow-cbc-"))
        local_target = tmpdir / GWF_CBC_FILENAME
        source = (
            run_outputs_uri
            if run_outputs_uri.endswith(".cbc")
            else run_outputs_uri.rstrip("/") + f"/{GWF_CBC_FILENAME}"
        )
        bucket_name, _, obj_key = source[len("s3://"):].partition("/")
        try:
            import shutil as _shutil

            resp = _get_s3_client().get_object(Bucket=bucket_name, Key=obj_key)
            with local_target.open("wb") as fh:
                _shutil.copyfileobj(resp["Body"], fh)
        except Exception as exc:  # noqa: BLE001
            raise PostprocessMODFLOWError(
                "SEEPAGE_OUTPUT_READ_FAILED",
                message=f"could not fetch GWF cbc from {source}: {exc}",
                details={"run_outputs_uri": run_outputs_uri},
            ) from exc
        return local_target
    if run_outputs_uri.startswith("gs://"):
        try:
            import fsspec  # type: ignore[import-not-found]

            fs = fsspec.filesystem("gcs")
            tmpdir = Path(tempfile.mkdtemp(prefix="modflow-cbc-"))
            local_target = tmpdir / GWF_CBC_FILENAME
            candidate = (
                run_outputs_uri
                if run_outputs_uri.endswith(".cbc")
                else f"{run_outputs_uri.rstrip('/')}/{GWF_CBC_FILENAME}"
            )
            fs.get(candidate, str(local_target))
            return local_target
        except Exception as exc:  # noqa: BLE001
            raise PostprocessMODFLOWError(
                "SEEPAGE_OUTPUT_READ_FAILED",
                message=f"could not fetch GWF cbc from {run_outputs_uri}: {exc}",
                details={"run_outputs_uri": run_outputs_uri},
            ) from exc

    p = Path(run_outputs_uri.replace("file://", ""))
    if p.is_file() and p.suffix == ".cbc" and "gwf" in p.name.lower():
        return p
    if p.is_dir():
        hits = sorted(glob.glob(str(p / "**" / GWF_CBC_FILENAME), recursive=True))
        if not hits:
            # any GWF cbc (defensive: the OC stem may differ).
            hits = sorted(
                g
                for g in glob.glob(str(p / "**" / "*.cbc"), recursive=True)
                if "gwf" in Path(g).name.lower()
            )
        if hits:
            return Path(hits[0])
    raise PostprocessMODFLOWError(
        "SEEPAGE_OUTPUT_READ_FAILED",
        message=f"no {GWF_CBC_FILENAME} found under {run_outputs_uri}",
        details={"run_outputs_uri": run_outputs_uri},
    )


def postprocess_river_seepage(
    run_outputs_uri: str,
    *,
    run_id: str,
    model_crs: str,
    deck_dir: str | None = None,
    runs_bucket: str | None = None,
    publish: bool = True,
) -> SeepageLayerURI:
    """Convert a MODFLOW GWF run's RIV budget into a ``SeepageLayerURI``.

    Reads the GWF cell-by-cell budget (``gwf_model.cbc``) RIV leakage term,
    scatters the per-reach-cell signed exchange flux onto the model grid,
    reprojects to a DIVERGING EPSG:4326 gaining/losing-seepage COG, computes the
    leakage narration scalars, uploads + (optionally) publishes the COG, and
    returns the typed seepage layer.

    Sign convention (MF6 RIV budget): positive ``q`` = flow INTO the cell from
    the river (LOSING reach, seepage INTO the aquifer); negative ``q`` = flow OUT
    to the river (GAINING reach, baseflow). The diverging ``rdbu`` ramp centred
    on 0 renders losing (positive) one colour and gaining (negative) the other.

    Args:
        run_outputs_uri: the run output location (local dir / ``file://`` for the
            local path, ``s3://`` / ``gs://`` for the cloud path; finds
            ``gwf_model.cbc``).
        run_id: the run identifier the COG is keyed under in the runs bucket.
        model_crs: the deck's projected CRS (e.g. ``"EPSG:32617"``).
        deck_dir: optional on-disk deck dir for grid georegistration + the grid
            (nrow/ncol) used to scatter the budget. When None the COG uses an
            identity transform and the grid shape is inferred from the budget.
        runs_bucket: optional override for the runs bucket name.
        publish: when True, dispatch ``publish_layer`` (mocked in tests).

    Returns:
        ``SeepageLayerURI`` with ``total_leakage_m3_day`` + ``gaining_m3_day`` +
        ``losing_m3_day`` + ``river_cell_count`` and a published WMS / tile URI
        (else the COG URI).

    Raises:
        PostprocessMODFLOWError: any read / reproject / write / upload step
            failed; ``error_code`` identifies the stage.
    """
    cbc_path = _resolve_gwf_cbc_path(run_outputs_uri)
    geo = _grid_georegistration_from_deck(deck_dir)
    nrow = int(geo["nrow"]) if geo is not None else None
    ncol = int(geo["ncol"]) if geo is not None else None
    if nrow is None or ncol is None:
        # No deck georegistration: infer a square grid from the budget node ids.
        nrow, ncol = _infer_grid_shape_from_cbc(cbc_path)

    seepage = _read_riv_seepage_grid(cbc_path, nrow, ncol)
    total, gaining, losing, river_cell_count = compute_seepage_metrics(seepage)
    logger.info(
        "postprocess_river_seepage run_id=%s total_leakage_m3_day=%.6g "
        "gaining_m3_day=%.6g losing_m3_day=%.6g cells=%d",
        run_id,
        total,
        gaining,
        losing,
        river_cell_count,
    )

    cog_path = _write_reprojected_cog(
        seepage, model_crs, geo, mask_below_floor=False
    )
    bbox_4326 = _cog_bbox_4326(cog_path)
    cog_uri = _upload_cog(
        cog_path,
        run_id,
        runs_bucket,
        cog_filename="river_seepage_4326.tif",
    )

    layer_id = f"river-seepage-{run_id}"
    final_uri = cog_uri
    if publish:
        wms_url = _dispatch_publish_layer(
            cog_uri, layer_id, style_preset=SEEPAGE_STYLE_PRESET
        )
        if wms_url:
            final_uri = wms_url

    return SeepageLayerURI(
        layer_id=layer_id,
        name="River Seepage (gaining / losing reach)",
        layer_type="raster",
        uri=final_uri,
        style_preset=SEEPAGE_STYLE_PRESET,
        role="primary",
        units="m^3/day",
        bbox=bbox_4326,
        total_leakage_m3_day=total,
        gaining_m3_day=gaining,
        losing_m3_day=losing,
        river_cell_count=river_cell_count,
    )


def _infer_grid_shape_from_cbc(cbc_path: Path) -> tuple[int, int]:
    """Best-effort grid (nrow, ncol) when no deck georegistration is available.

    The cbc reader needs a grid shape to scatter the RIV nodes. flopy's
    ``CellBudgetFile`` exposes ``nrow``/``ncol`` header attributes for a
    structured grid; fall back to a 40x40 demo grid (gwt_adapter default) if
    they are absent.
    """
    try:
        import flopy.utils  # type: ignore[import-not-found]

        cbc = flopy.utils.CellBudgetFile(str(cbc_path))
        nrow = int(getattr(cbc, "nrow", 0) or 0)
        ncol = int(getattr(cbc, "ncol", 0) or 0)
        if nrow > 0 and ncol > 0:
            return nrow, ncol
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not infer grid shape from %s: %s", cbc_path, exc)
    return 40, 40
