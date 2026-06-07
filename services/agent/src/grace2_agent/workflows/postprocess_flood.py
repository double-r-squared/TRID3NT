"""SFINCS run-output postprocessing (job-0042).

``postprocess_flood(run_outputs_uri) → list[LayerURI]`` reads the SFINCS run's
raw output (NetCDF ``sfincs_map.nc`` carrying water depth time-series, plus
any auxiliary flux/water-level products HydroMT-SFINCS emits), extracts the
peak flood depth field, converts it to a Cloud-Optimized GeoTIFF, uploads to
GCS, and returns a typed ``LayerURI`` pointing at the COG.

Output format set is fixed by FR-CE-4 + FR-QS-3: rasters COG; vectors
FlatGeobuf/GeoParquet — produced identically by engine, consumed identically
by QGIS Server + web. The postprocess output here is one COG (flood depth at
peak); future workflows may emit additional layers (flood velocity,
arrival-time COG, affected-buildings FlatGeobuf, …) — extend the return list
when those land.

Style preset: ``continuous_flood_depth`` (a new preset name for the M5
substrate). The actual QML file lives in ``styles/`` (FROZEN under this job
per the kickoff), so the style_preset string here references a name that the
engine's styles follow-up job will author. See OQ-42-FLOOD-DEPTH-PRESET-QML.

Tier separation (Invariant 5): the COG is written under
``gs://grace-2-hazard-prod-runs/<run_id>/`` (the runs bucket from job-0040).
The agent service doesn't re-render — QGIS Server picks up the URI from the
AssessmentEnvelope's ``ResultLayer`` and serves WMS/WMTS tiles.

This module is workflow-internal — not registered as an atomic tool.
``model_flood_scenario`` calls it after ``wait_for_completion`` returns a
COMPLETE ``RunResult``.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from grace2_contracts.execution import LayerURI

__all__ = [
    "PostprocessError",
    "postprocess_flood",
    "FLOOD_DEPTH_STYLE_PRESET",
    "RUNS_BUCKET_DEFAULT",
]

logger = logging.getLogger("grace2_agent.workflows.postprocess_flood")


#: Default runs bucket — matches the job-0040 substrate (``grace-2-hazard-prod-runs``).
RUNS_BUCKET_DEFAULT: str = "grace-2-hazard-prod-runs"

#: QML style preset name the workflow attaches to the postprocessed flood-depth COG.
#: The styles/ package is FROZEN under this job; engine styles follow-up
#: authors the matching ``continuous_flood_depth.qml``. Surfaced as
#: OQ-42-FLOOD-DEPTH-PRESET-QML.
FLOOD_DEPTH_STYLE_PRESET: str = "continuous_flood_depth"


class PostprocessError(RuntimeError):
    """Raised by ``postprocess_flood`` on read / extraction / upload failures.

    Carries ``error_code`` matching the open-set A.6 surface so the agent
    emitter can render a typed error frame. Codes used here:

    - ``RUN_OUTPUT_READ_FAILED`` — could not read the raw solver output
      (network, missing blob, malformed NetCDF).
    - ``RUN_OUTPUT_EMPTY`` — output exists but contains no depth field /
      no timesteps (defensive; surfaces alongside the typed envelope
      so the user understands why the layer is missing).
    - ``COG_WRITE_FAILED`` — rasterio could not write the COG (encoder
      error, disk full).
    - ``COG_UPLOAD_FAILED`` — the GCS upload of the staged COG failed.
    """

    error_code: str = "POSTPROCESS_FAILED"

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


def _resolve_run_output_to_local(run_outputs_uri: str) -> Path:
    """Download (if gs://) or resolve (if local) the run output to a local NetCDF.

    HydroMT-SFINCS standard output is ``sfincs_map.nc``; if ``run_outputs_uri``
    points at a directory or prefix we look for that filename inside it. If it
    points at a single file we use that.
    """
    if run_outputs_uri.startswith("gs://"):
        try:
            import fsspec  # type: ignore[import-not-found]

            fs = fsspec.filesystem("gcs")
            # Try directory listing first.
            tmpdir = Path(tempfile.mkdtemp(prefix="sfincs-output-"))
            local_target = tmpdir / "sfincs_map.nc"
            try:
                # If the URI ends with .nc, fetch it directly.
                if run_outputs_uri.endswith(".nc"):
                    fs.get(run_outputs_uri, str(local_target))
                else:
                    # Treat as a directory / prefix: find sfincs_map.nc inside.
                    prefix = run_outputs_uri.rstrip("/")
                    candidate = f"{prefix}/sfincs_map.nc"
                    fs.get(candidate, str(local_target))
            except Exception as exc:  # noqa: BLE001
                raise PostprocessError(
                    "RUN_OUTPUT_READ_FAILED",
                    message=f"could not fetch run output {run_outputs_uri}: {exc}",
                    details={"run_outputs_uri": run_outputs_uri},
                ) from exc
            return local_target
        except ImportError as exc:
            raise PostprocessError(
                "RUN_OUTPUT_READ_FAILED",
                message=f"fsspec[gcs] not available for {run_outputs_uri}: {exc}",
                details={"run_outputs_uri": run_outputs_uri},
            ) from exc
    p = Path(run_outputs_uri.replace("file://", ""))
    if p.is_dir():
        candidate = p / "sfincs_map.nc"
        if candidate.exists():
            return candidate
    if p.exists():
        return p
    raise PostprocessError(
        "RUN_OUTPUT_READ_FAILED",
        message=f"run output not found at {run_outputs_uri}",
        details={"run_outputs_uri": run_outputs_uri},
    )


def _extract_peak_depth_geotiff(netcdf_path: Path) -> tuple[Path, dict[str, Any]]:
    """Read sfincs_map.nc, compute the per-cell peak depth, write a COG to a tmp path.

    SFINCS publishes ``zsmax`` (max water-level) and ``zs`` (water-level time
    series); the depth at peak is ``zsmax - zb`` (water-level minus bed-level).
    HydroMT-SFINCS variants emit ``hmax`` (max water depth) directly. We try
    ``hmax`` first; fall back to computing it from ``zsmax`` - ``zb``.

    Returns the path to the staged COG and a metadata dict (max/mean/p95
    depth, units, crs string) the AssessmentEnvelope's FloodMetrics consumes.
    """
    try:
        import numpy as np  # type: ignore[import-not-found]
        import rasterio  # type: ignore[import-not-found]
        import xarray as xr  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise PostprocessError(
            "RUN_OUTPUT_READ_FAILED",
            message=f"xarray/rasterio/numpy not available: {exc}",
            details={"netcdf_path": str(netcdf_path)},
        ) from exc

    try:
        ds = xr.open_dataset(str(netcdf_path))
    except Exception as exc:  # noqa: BLE001
        raise PostprocessError(
            "RUN_OUTPUT_READ_FAILED",
            message=f"xarray could not open {netcdf_path}: {exc}",
            details={"netcdf_path": str(netcdf_path)},
        ) from exc

    try:
        if "hmax" in ds.variables:
            depth = ds["hmax"]
        elif "zsmax" in ds.variables and "zb" in ds.variables:
            depth = ds["zsmax"] - ds["zb"]
        elif "zs" in ds.variables and "zb" in ds.variables:
            depth = (ds["zs"].max(dim="time") - ds["zb"]).clip(min=0.0)
        else:
            raise PostprocessError(
                "RUN_OUTPUT_EMPTY",
                message=(
                    f"sfincs_map.nc at {netcdf_path} carries neither hmax nor "
                    "zsmax/zs+zb; no depth field to extract."
                ),
                details={"variables": list(ds.variables.keys())},
            )

        arr = np.asarray(depth.values, dtype="float32")
        # Mask non-positive depths to NaN so the COG is dry-cell-aware.
        arr_masked = np.where(arr > 0.0, arr, np.nan)
        flooded = arr_masked[~np.isnan(arr_masked)]
        if flooded.size == 0:
            metrics_summary = {
                "max_depth_m": 0.0,
                "mean_depth_m": 0.0,
                "p95_depth_m": 0.0,
                "flooded_cell_count": 0,
            }
        else:
            metrics_summary = {
                "max_depth_m": float(np.nanmax(flooded)),
                "mean_depth_m": float(np.nanmean(flooded)),
                "p95_depth_m": float(np.nanpercentile(flooded, 95)),
                "flooded_cell_count": int(flooded.size),
            }

        # Write a COG. Best-effort CRS + transform from the dataset; SFINCS
        # output carries a 'spatial_ref'/'crs' attr in v1.x.
        crs = ds.attrs.get("crs", "EPSG:3857")
        try:
            x = ds["x"].values
            y = ds["y"].values
            transform = rasterio.transform.from_bounds(
                float(x.min()), float(y.min()), float(x.max()), float(y.max()),
                arr.shape[-1], arr.shape[-2],
            )
        except Exception:  # noqa: BLE001
            transform = rasterio.Affine.identity()

        tmp_cog = Path(tempfile.NamedTemporaryFile(suffix=".tif", delete=False).name)
        try:
            with rasterio.open(
                tmp_cog,
                "w",
                driver="COG",
                width=arr.shape[-1],
                height=arr.shape[-2],
                count=1,
                dtype="float32",
                crs=crs,
                transform=transform,
                nodata=float("nan"),
                compress="LZW",
            ) as dst:
                dst.write(arr_masked.astype("float32"), 1)
        except Exception as exc:  # noqa: BLE001
            raise PostprocessError(
                "COG_WRITE_FAILED",
                message=f"COG write failed: {exc}",
                details={"netcdf_path": str(netcdf_path)},
            ) from exc

        metrics_summary["crs"] = crs
        metrics_summary["units"] = "meters"
        return tmp_cog, metrics_summary
    finally:
        try:
            ds.close()
        except Exception:  # noqa: BLE001
            pass


def _upload_cog_to_runs_bucket(
    local_cog: Path, run_id: str, runs_bucket: str | None = None
) -> str:
    """Upload the staged COG to ``gs://<runs_bucket>/<run_id>/flood_depth_peak.tif``."""
    bucket = runs_bucket or os.environ.get("GRACE2_RUNS_BUCKET", RUNS_BUCKET_DEFAULT)
    dest = f"gs://{bucket}/{run_id}/flood_depth_peak.tif"
    try:
        import fsspec  # type: ignore[import-not-found]

        fs = fsspec.filesystem("gcs")
        fs.put(str(local_cog), dest)
    except Exception as exc:  # noqa: BLE001
        raise PostprocessError(
            "COG_UPLOAD_FAILED",
            message=f"upload of {local_cog} to {dest} failed: {exc}",
            details={"local_cog": str(local_cog), "dest": dest},
        ) from exc
    logger.info("uploaded flood-depth COG to %s", dest)
    return dest


def postprocess_flood(
    run_outputs_uri: str,
    *,
    run_id: str,
    runs_bucket: str | None = None,
) -> tuple[list[LayerURI], dict[str, Any]]:
    """Convert a SFINCS run's NetCDF output into a flood-depth COG ``LayerURI``.

    Use this when the workflow has a SUCCEEDED ``RunResult`` and needs to
    materialize the renderable layers for the AssessmentEnvelope. v0.1 returns
    a single-element layer list (flood depth at peak); future products
    (velocity, arrival time) extend the list.

    Args:
        run_outputs_uri: the ``gs://`` URI of the SFINCS run output (the
            ``RunResult.output_uri`` from ``wait_for_completion``; may be a
            directory containing ``sfincs_map.nc`` or the NetCDF directly).
        run_id: the run identifier the COG is keyed under in the runs bucket.
        runs_bucket: optional override for the runs bucket name.

    Returns:
        A tuple ``(layers, metrics)`` where ``layers`` is a list of
        ``LayerURI`` (first element is always the peak flood-depth COG;
        CRS-tagged + units-tagged per FR-CE-4) and ``metrics`` is a dict
        carrying ``max_depth_m``, ``mean_depth_m``, ``p95_depth_m``, and
        ``flooded_cell_count`` for the workflow to populate ``FloodMetrics``.

    Raises:
        PostprocessError: any step of the read → COG-write → upload chain
            failed; ``error_code`` identifies the stage.
    """
    netcdf_path = _resolve_run_output_to_local(run_outputs_uri)
    cog_path, metrics = _extract_peak_depth_geotiff(netcdf_path)
    try:
        cog_uri = _upload_cog_to_runs_bucket(cog_path, run_id, runs_bucket)
    finally:
        # Best-effort cleanup of the local COG (the upload made a copy).
        try:
            cog_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    layer = LayerURI(
        layer_id=f"flood-depth-peak-{run_id}",
        name="Flood Depth (peak)",
        layer_type="raster",
        uri=cog_uri,
        style_preset=FLOOD_DEPTH_STYLE_PRESET,
        role="primary",
        units="meters",
    )
    return [layer], metrics
