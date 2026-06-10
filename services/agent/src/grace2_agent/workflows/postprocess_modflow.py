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

from grace2_contracts.modflow_contracts import PlumeLayerURI

logger = logging.getLogger("grace2_agent.workflows.postprocess_modflow")

__all__ = [
    "PostprocessMODFLOWError",
    "postprocess_modflow",
    "compute_plume_metrics",
    "PLUME_DETECTION_FLOOR_MGL",
    "PLUME_STYLE_PRESET",
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


# --------------------------------------------------------------------------- #
# UCN read + grid georegistration
# --------------------------------------------------------------------------- #


def _resolve_ucn_path(run_outputs_uri: str) -> Path:
    """Locate ``gwt_model.ucn`` from a local dir / file:// / gs:// run output.

    Local (``file://`` or a bare path): search the dir tree for the UCN file.
    gs:// : fetch via fsspec into a temp dir (mirrors postprocess_flood). The
    local-mode live-evidence path always passes a local dir.
    """
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
) -> Path:
    """Write the concentration grid to an EPSG:4326 COG, reprojecting from model_crs.

    The grid is in the deck's projected (UTM) CRS. We build the source transform
    from the grid origin + cell size (flopy's row 0 is the NORTH row, so the
    transform's top-left is yorigin + nrow*delc), tag it ``model_crs``, then warp
    to EPSG:4326 via rasterio's reprojection. Sub-floor cells are masked to NaN.
    """
    import numpy as np  # type: ignore[import-not-found]
    import rasterio  # type: ignore[import-not-found]
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    arr = np.asarray(final2d, dtype="float32")
    # Mask clean cells (≤ floor) to NaN so the COG renders only the plume.
    arr_masked = np.where(arr > PLUME_DETECTION_FLOOR_MGL, arr, np.nan).astype("float32")
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


def _upload_cog(local_cog: Path, run_id: str, runs_bucket: str | None) -> str:
    """Upload the EPSG:4326 plume COG to the runs bucket; return its gs:// URI.

    In local mode (no GCS), the upload is skipped and the local ``file://`` URI
    is returned so the live-evidence path completes without cloud access.
    """
    bucket = runs_bucket or os.environ.get("GRACE2_RUNS_BUCKET", RUNS_BUCKET_DEFAULT)
    dest = f"gs://{bucket}/{run_id}/plume_concentration_4326.tif"
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


def _dispatch_publish_layer(cog_uri: str, layer_id: str) -> str | None:
    """Publish the plume COG to QGIS Server WMS; return the WMS URL or None.

    Non-fatal: a publish failure (worker SA grant, GCS read) falls back to the
    COG URI so the rest of the envelope is usable. Skips publish entirely for
    non-gs:// URIs (local mode has nothing for QGIS Server to read).
    """
    if not cog_uri.startswith("gs://"):
        # job-0241: loud, not silent — a non-gs:// URI here means the GCS
        # upload fell back (stale venv / auth / network) and the plume will
        # NOT appear on the map. The Case 2 live gate (job-0235) burned on
        # exactly this as a debug-invisible skip.
        logger.warning(
            "publish_layer SKIPPED for %s: COG URI is not gs:// (%s); the "
            "plume will NOT render as a map layer. Check fsspec[gcs] is "
            "installed and the GCS upload succeeded.",
            layer_id,
            cog_uri,
        )
        return None
    try:
        from ..tools.publish_layer import PublishLayerError, publish_layer

        wms_url = publish_layer(
            layer_uri=cog_uri,
            layer_id=layer_id,
            style_preset=PLUME_STYLE_PRESET,
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
