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
    "NODATA_DEPTH_M",
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

#: Minimum depth threshold below which cells are masked to NaN (treated as dry).
#: 5 cm is the physically meaningful wet-cell threshold — matches the
#: ``flooded_cell_count`` reporting convention (job-0058 evidence) and the
#: lowest QML colour stop (``continuous_flood_depth.qml`` alpha=0 at 0.05 m).
#: Belt-and-suspenders: the QML renderer also hides values < 0.05 m (alpha=0),
#: so the two layers reinforce each other (job-0071 transparency fix).
NODATA_DEPTH_M: float = 0.05


class PostprocessError(RuntimeError):
    """Raised by ``postprocess_flood`` on read / extraction / upload failures.

    Carries ``error_code`` matching the open-set A.6 surface so the agent
    emitter can render a typed error frame. Codes used here:

    - ``RUN_OUTPUT_READ_FAILED`` — could not read the raw solver output
      (network, missing blob, malformed NetCDF).
    - ``RUN_OUTPUT_EMPTY`` — output exists but contains no depth field /
      no timesteps (defensive; surfaces alongside the typed envelope
      so the user understands why the layer is missing).
    - ``RUN_OUTPUT_UNEXPECTED_SHAPE`` — the extracted depth array has extra
      singleton dims that do not collapse to 2D after squeeze; indicates an
      unexpected HydroMT-SFINCS output shape variant.
    - ``COG_WRITE_FAILED`` — rasterio could not write the COG (encoder
      error, disk full).
    - ``COG_UPLOAD_FAILED`` — the GCS upload of the staged COG failed.
    - ``CRS_TAG_MISMATCH`` — belt-and-suspenders guard (job-0071 /
      research-workflow recommendation 2026-06-07): the CRS tag written to
      the COG does not match what rasterio reads back, OR the tag's
      geographic/projected classification is inconsistent with the actual
      coordinate magnitudes (geographic → |x| ≤ 360; projected → |x| > 1000).
      Raised before the COG is uploaded to the runs bucket so a mistagged
      raster never lands in production. Closes the broader bug class around
      OQ-59 / OQ-69.
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
    """Download (if gs:// / s3://) or resolve (if local) the run output to a
    local NetCDF.

    HydroMT-SFINCS standard output is ``sfincs_map.nc``; if ``run_outputs_uri``
    points at a directory or prefix we look for that filename inside it. If it
    points at a single file we use that.

    job-0291 (sprint-14-aws): ``s3://`` run outputs (the local-docker solver
    backend's runs prefix) download via **boto3** through the solver module's
    shared S3 client seam — boto3 NOT s3fs (job-0289 instance-role lesson).
    """
    if run_outputs_uri.startswith("s3://"):
        from ..tools.solver import _get_s3_client

        tmpdir = Path(tempfile.mkdtemp(prefix="sfincs-output-"))
        local_target = tmpdir / "sfincs_map.nc"
        source = (
            run_outputs_uri
            if run_outputs_uri.endswith(".nc")
            else run_outputs_uri.rstrip("/") + "/sfincs_map.nc"
        )
        bucket_name, _, obj_key = source[len("s3://"):].partition("/")
        try:
            import shutil as _shutil

            resp = _get_s3_client().get_object(Bucket=bucket_name, Key=obj_key)
            with local_target.open("wb") as fh:
                _shutil.copyfileobj(resp["Body"], fh)
        except Exception as exc:  # noqa: BLE001
            raise PostprocessError(
                "RUN_OUTPUT_READ_FAILED",
                message=f"could not fetch run output {source}: {exc}",
                details={"run_outputs_uri": run_outputs_uri},
            ) from exc
        return local_target
    if run_outputs_uri.startswith("gs://"):
        try:
            # job-0250 (OQ-0250-POSTPROCESS-FSSPEC-NOOPCALLBACK): download via
            # google-cloud-storage, NOT fsspec/gcsfs. The fsspec.get() path
            # crashed live (round-6 Stage 3: two completed SFINCS solves,
            # zero published layers) when a version-skewed gcsfs (0.8.0,
            # forced by the old storage<3 pin) choked on modern fsspec's
            # NoOpCallback. The storage client is the proven-everywhere ADC
            # path (cache shim, MODFLOW staging) — same pattern as
            # sfincs_builder._stage_gcs_local.
            from google.cloud import storage

            tmpdir = Path(tempfile.mkdtemp(prefix="sfincs-output-"))
            local_target = tmpdir / "sfincs_map.nc"
            source = (
                run_outputs_uri
                if run_outputs_uri.endswith(".nc")
                else run_outputs_uri.rstrip("/") + "/sfincs_map.nc"
            )
            bucket_name, _, blob_name = source[len("gs://"):].partition("/")
            try:
                client = storage.Client(
                    project=os.environ.get(
                        "GOOGLE_CLOUD_PROJECT", "grace-2-hazard-prod"
                    )
                )
                client.bucket(bucket_name).blob(blob_name).download_to_filename(
                    str(local_target)
                )
            except Exception as exc:  # noqa: BLE001
                raise PostprocessError(
                    "RUN_OUTPUT_READ_FAILED",
                    message=f"could not fetch run output {source}: {exc}",
                    details={"run_outputs_uri": run_outputs_uri},
                ) from exc
            return local_target
        except ImportError as exc:
            raise PostprocessError(
                "RUN_OUTPUT_READ_FAILED",
                message=f"google-cloud-storage not available for {run_outputs_uri}: {exc}",
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


def _read_crs_from_dataset(ds: Any) -> str:
    """Read CRS from a SFINCS netCDF dataset; CF-convention compliant (OQ-59 fix).

    SFINCS stores the CRS in a **data variable** named ``crs``, not in
    ``ds.attrs``.  The variable carries EPSG information in its attributes
    following CF conventions.  We try the known SFINCS encodings in order:

    1. ``crs_var.attrs["epsg_code"]`` — SFINCS emits ``"EPSG:32617"`` (string
       already prefixed); strip any accidental whitespace and return as-is.
    2. ``crs_var.attrs["crs_wkt"]`` — CF canonical WKT string; parse via
       pyproj and return the EPSG authority string.
    3. ``crs_var.attrs["spatial_ref"]`` — OGC WKT variant used by some GDAL
       writers; parse via pyproj.
    4. Fallback: ``ds.attrs.get("crs", "EPSG:3857")`` — original logic,
       retained for any dataset that does not carry the ``crs`` variable.

    A logged warning is emitted whenever the fallback fires so the mismatch
    is visible in the pipeline-strip log rather than silently using EPSG:3857.
    """
    if "crs" in ds.variables:
        crs_var = ds["crs"]
        attrs = crs_var.attrs

        if "epsg_code" in attrs:
            # SFINCS emits e.g. "EPSG:32617" — may occasionally be bare int.
            raw = str(attrs["epsg_code"]).strip()
            if raw.upper().startswith("EPSG:"):
                return raw  # already canonical
            try:
                return f"EPSG:{int(raw)}"
            except ValueError:
                pass  # fall through to next key

        for wkt_key in ("crs_wkt", "spatial_ref"):
            if wkt_key in attrs:
                try:
                    import pyproj  # optional; rasterio ships pyproj
                    return pyproj.CRS.from_wkt(attrs[wkt_key]).to_string()
                except Exception:  # noqa: BLE001
                    pass  # malformed WKT — fall through

    # Fallback: old .attrs encoding or bare dataset without a crs variable.
    fallback = ds.attrs.get("crs", "EPSG:3857")
    if fallback == "EPSG:3857":
        logger.warning(
            "postprocess_flood: no 'crs' variable found in sfincs_map.nc; "
            "falling back to EPSG:3857 — COG CRS tag may not match pixel coords."
        )
    return fallback


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
        # Squeeze any singleton leading dims (e.g. HydroMT-SFINCS 1.2.2 emits
        # hmax with shape (timemax=1, n, m)). COG writer expects exactly 2D.
        if arr.ndim > 2:
            arr = np.squeeze(arr)
            if arr.ndim != 2:
                raise PostprocessError(
                    "RUN_OUTPUT_UNEXPECTED_SHAPE",
                    message=(
                        f"depth array has shape {arr.shape}; expected 2D after squeeze"
                    ),
                    details={"netcdf_path": str(netcdf_path), "shape": list(arr.shape)},
                )

        # --- Rotation fix (job-0071) ---
        # SFINCS netCDF convention: ds["x"].dims = ("m",), ds["y"].dims = ("n",)
        # where m=x-cols, n=y-rows.  Diagnostic (2026-06-07): the Fort Myers run
        # had hmax dims (timemax, m, n) — x-cols in the leading spatial axis —
        # so after squeeze depth.dims = ("m", "n") with shape (m, n).
        # The COG writer expects arr.shape = (y_rows, x_cols); we detect the
        # mismatch by comparing the squeezed DataArray's dim names against
        # ds["x"].dims[0] and ds["y"].dims[0], then transpose if needed.
        # Using dim names (not array shapes) handles square grids correctly.
        try:
            _x_dim = ds["x"].dims[0]  # e.g. "m"
            _y_dim = ds["y"].dims[0]  # e.g. "n"
            _depth_squeezed = depth.squeeze()
            _depth_dims = _depth_squeezed.dims  # e.g. ("n", "m") or ("m", "n")
            if _depth_dims[-1] == _y_dim and _depth_dims[-2] == _x_dim:
                # Axes are swapped: arr is (x_cols, y_rows); transpose to (y_rows, x_cols).
                logger.info(
                    "postprocess_flood: transposing depth array — dims %s have x-dim "
                    "(%s) in rows and y-dim (%s) in cols; expected (y_rows, x_cols). "
                    "This indicates SFINCS hmax was emitted as (timemax, m, n) instead of "
                    "(timemax, n, m). Rotation fix applied (job-0071).",
                    _depth_dims, _x_dim, _y_dim,
                )
                arr = arr.T
        except Exception:  # noqa: BLE001 — dim inspection failure falls through to identity
            pass

        # Mask sub-threshold depths to NaN so the COG is dry-cell-aware.
        # NODATA_DEPTH_M = 0.05 m is the physical wet-cell threshold (job-0071
        # transparency belt-and-suspenders).  Belt-and-suspenders: the QML
        # renderer also hides values < 0.05 m via alpha=0 at the bottom stop.
        arr_masked = np.where(arr > NODATA_DEPTH_M, arr, np.nan)
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
        # output carries its CRS in a data variable named 'crs' (CF-convention),
        # not in .attrs.  Read from the variable first; fall back to .attrs for
        # any dataset that still uses the old encoding (OQ-59 fix, job-0063).
        # x and y may have been read above in the rotation block; reuse them.
        crs = _read_crs_from_dataset(ds)
        try:
            # x/y may already be bound from the rotation block; re-read to be safe
            # (the rotation block catches exceptions so x/y may not be in scope).
            _x = ds["x"].values
            _y = ds["y"].values
            transform = rasterio.transform.from_bounds(
                float(_x.min()), float(_y.min()), float(_x.max()), float(_y.max()),
                arr.shape[-1], arr.shape[-2],
            )
        except Exception:  # noqa: BLE001
            transform = rasterio.Affine.identity()

        # --- Y-orientation guard (job-0086) ---
        # SFINCS often emits y ascending along rows (row 0 = south). COG built via
        # rasterio.transform.from_bounds(...) declares row 0 = north. If we write
        # arr as-is into that transform, the COG is internally Y-flipped: deep-flood
        # pixels (at the SOUTH river mouth) paint onto the NORTH of the bbox.
        # Detect direction along the row axis and flip BOTH arr + arr_masked.
        try:
            _y_vals = ds["y"].values
            if _y_vals.ndim == 2:
                y_ascends_along_rows = bool(_y_vals[0, 0] < _y_vals[-1, 0])
            else:
                y_ascends_along_rows = bool(_y_vals[0] < _y_vals[-1])
            if y_ascends_along_rows:
                logger.info(
                    "postprocess_flood: flipping rows — SFINCS y ascends along rows "
                    "(row 0 = south, %.2f → %.2f); COG expects row 0 = north. "
                    "Y-axis flip applied (job-0086).",
                    float(_y_vals.flat[0]), float(_y_vals.flat[-1]),
                )
                arr = arr[::-1, :]
                arr_masked = arr_masked[::-1, :]
        except Exception:  # noqa: BLE001 — defensive; bad y → identity, no harm
            logger.warning("postprocess_flood: y-orientation probe failed; not flipping")

        # --- X-orientation guard (job-0086, belt-and-suspenders) ---
        # Curvilinear grids can also have x descending along columns (col 0 = east).
        # COG from_bounds always produces west-to-east (ascending x), so if the
        # data has x descending along cols, flip columns to match. Do NOT flip when
        # x is already ascending — this guard is identity for all normal SFINCS runs.
        try:
            _x_vals = ds["x"].values
            if _x_vals.ndim == 2:
                x_descends_along_cols = bool(_x_vals[0, 0] > _x_vals[0, -1])
            else:
                x_descends_along_cols = bool(_x_vals[0] > _x_vals[-1])
            if x_descends_along_cols:
                logger.info(
                    "postprocess_flood: flipping cols — SFINCS x descends along cols "
                    "(col 0 = east, %.2f → %.2f); COG expects col 0 = west. "
                    "X-axis flip applied (job-0086).",
                    float(_x_vals.flat[0]), float(_x_vals.flat[-1]),
                )
                arr = arr[:, ::-1]
                arr_masked = arr_masked[:, ::-1]
        except Exception:  # noqa: BLE001 — defensive; bad x → identity, no harm
            logger.warning("postprocess_flood: x-orientation probe failed; not flipping")

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

        # --- CRS_TAG_MISMATCH guard (job-0071 / research-workflow 2026-06-07) ---
        # Re-open the COG and verify the CRS tag was written correctly BEFORE
        # uploading to the runs bucket.  Two checks:
        # 1. Round-trip: str(verify.crs) must equal str(crs).
        # 2. Sanity: geographic CRS → |x| ≤ 360; projected → |x| > 1000.
        with rasterio.open(tmp_cog, "r") as verify:
            if str(verify.crs) != str(crs):
                raise PostprocessError(
                    "CRS_TAG_MISMATCH",
                    message=(
                        f"COG written with crs={crs!r} but rasterio read back "
                        f"{verify.crs!r}"
                    ),
                    details={"netcdf_path": str(netcdf_path)},
                )
            is_geographic = verify.crs.is_geographic
            bounds_max = max(abs(verify.bounds.left), abs(verify.bounds.right))
            if is_geographic and bounds_max > 360:
                raise PostprocessError(
                    "CRS_TAG_MISMATCH",
                    message=(
                        f"crs={crs!r} is geographic but bounds.left="
                        f"{verify.bounds.left} implies projected coords (|x|>360)"
                    ),
                    details={"netcdf_path": str(netcdf_path)},
                )
            if (not is_geographic) and bounds_max < 1000:
                raise PostprocessError(
                    "CRS_TAG_MISMATCH",
                    message=(
                        f"crs={crs!r} is projected but bounds.left="
                        f"{verify.bounds.left} implies geographic coords (|x|<1000)"
                    ),
                    details={"netcdf_path": str(netcdf_path)},
                )

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
    """Upload the staged COG to
    ``{scheme}://<runs_bucket>/<run_id>/flood_depth_peak.tif``.

    job-0291 (sprint-14-aws): scheme-aware per ``cache.storage_scheme()``.
    Under ``s3`` the upload goes via **boto3** (job-0289 lesson) and the
    runs bucket MUST come from ``GRACE2_RUNS_BUCKET`` / the explicit
    ``runs_bucket`` arg — there is no GCP-named default on AWS. The default
    (``gs``) branch is byte-identical to the pre-job-0291 fsspec[gcs] path.
    """
    from ..tools.cache import storage_scheme

    scheme = storage_scheme()
    if scheme == "s3":
        bucket = runs_bucket or (os.environ.get("GRACE2_RUNS_BUCKET") or "").strip()
        if not bucket:
            raise PostprocessError(
                "COG_UPLOAD_FAILED",
                message=(
                    "GRACE2_RUNS_BUCKET must be set under "
                    "GRACE2_STORAGE_BACKEND=s3 (no GCP-named default on AWS; "
                    "job-0291)"
                ),
                details={"local_cog": str(local_cog)},
            )
        dest = f"s3://{bucket}/{run_id}/flood_depth_peak.tif"
        try:
            from ..tools.solver import _get_s3_client

            with local_cog.open("rb") as fh:
                _get_s3_client().put_object(
                    Bucket=bucket,
                    Key=f"{run_id}/flood_depth_peak.tif",
                    Body=fh,
                    ContentType="image/tiff",
                )
        except Exception as exc:  # noqa: BLE001
            raise PostprocessError(
                "COG_UPLOAD_FAILED",
                message=f"upload of {local_cog} to {dest} failed: {exc}",
                details={"local_cog": str(local_cog), "dest": dest},
            ) from exc
        logger.info("uploaded flood-depth COG to %s (boto3)", dest)
        return dest

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
