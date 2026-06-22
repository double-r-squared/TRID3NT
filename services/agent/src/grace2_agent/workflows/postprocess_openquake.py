"""OpenQuake PSHA hazard-map postprocessing (sprint-17).

``postprocess_openquake(hazard_map_csv, *, run_id, run_args, ...) ->
SeismicHazardLayerURI`` reads OpenQuake's exported hazard-MAP CSV (one
``lon,lat,<imt-value>`` row per PSHA site), rasterizes the per-site hazard value
onto a regular EPSG:4326 grid, writes a COG, computes the narration scalars, and
returns the typed :class:`~grace2_contracts.openquake_contracts.SeismicHazardLayerURI`.

The OpenQuake analogue of ``postprocess_modflow`` (MF6-GWT plume) /
``postprocess_swmm`` (urban depth) / ``postprocess_flood`` (SFINCS). The defining
difference: OpenQuake emits a SCATTERED set of site values in a CSV, NOT a grid —
we interpolate/rasterize the point hazard onto a raster ourselves (the engine's
site grid is regular, so a nearest/linear fill onto the lon/lat lattice is the
honest reconstruction). The hazard map is in EPSG:4326 already (the site grid was
laid in lon/lat), so unlike the MODFLOW UTM plume there is no reprojection step.

Reuse (do NOT reinvent): the COG-write profile + ``_cog_bbox_4326`` zoom-to +
``_dispatch_publish_layer`` non-fatal publish pattern from ``postprocess_modflow``
(adapted for an already-EPSG:4326 site lattice). The honesty floor (Invariant 1 /
FR-AS-7): the hazard scalars are computed with plain arithmetic from the site
values — no LLM anywhere; the agent narrates the typed fields, never invents them.

Tier separation (Invariant 5): the COG lands in the runs bucket (scheme-aware via
``cache.storage_scheme()``); the agent does not re-render — ``publish_layer`` /
TiTiler serves the tiles from the URI on the envelope.
"""

from __future__ import annotations

import csv
import io
import logging
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from grace2_contracts.openquake_contracts import SeismicHazardLayerURI

logger = logging.getLogger("grace2_agent.workflows.postprocess_openquake")

__all__ = [
    "PostprocessOpenQuakeError",
    "postprocess_openquake",
    "parse_hazard_map_csv",
    "rasterize_hazard_sites",
    "compute_hazard_metrics",
    "SEISMIC_HAZARD_STYLE_PRESET",
    "HAZARD_FLOOR_VALUE",
]


#: The publish_layer style preset key the seismic hazard map renders with (the
#: magma ramp 0..1 in g; an ADDITIVE registry preset, disjoint from the existing
#: flood/plume keys — never mutated). Merged into _TITILER_STYLE_REGISTRY by the
#: orchestrator (see shared_appends.publish_layer_preset).
SEISMIC_HAZARD_STYLE_PRESET: str = "continuous_seismic_pga"

#: Hazard values at/below this floor (g) are masked to NaN so the COG renders
#: only the meaningful hazard (a near-zero PGA site is "no hazard").
HAZARD_FLOOR_VALUE: float = 0.001


class PostprocessOpenQuakeError(RuntimeError):
    """Raised on read / rasterize / COG-write / upload failures.

    ``error_code`` matches the open-set A.6 surface so the agent emitter renders
    a typed error frame. Codes:

    - ``OQ_HAZARD_READ_FAILED`` — could not open / parse the hazard-map CSV.
    - ``OQ_HAZARD_EMPTY`` — the CSV carries no site rows — nothing to rasterize.
    - ``OQ_DEPENDENCY_MISSING`` — numpy / rasterio not importable in the runtime.
    - ``OQ_COG_WRITE_FAILED`` — rasterio could not write the hazard COG.
    - ``OQ_COG_UPLOAD_FAILED`` — the runs-bucket upload of the COG failed.
    """

    error_code: str = "POSTPROCESS_OPENQUAKE_FAILED"

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
# Hazard-map CSV parse.
# --------------------------------------------------------------------------- #
def parse_hazard_map_csv(text: str) -> tuple[list[tuple[float, float, float]], str]:
    """Parse an OpenQuake hazard-MAP CSV into ``[(lon, lat, value), ...]`` + the
    value-column header.

    OpenQuake's ``hazard_map-mean-<IMT>_<...>.csv`` has a one-line ``#`` comment
    banner, then a header row ``lon,lat,<IMT>-<poe>`` (e.g. ``lon,lat,PGA-0.1``),
    then one row per site. We locate the lon/lat columns by name and take the
    remaining numeric column as the hazard value. Robust to the leading comment
    line + arbitrary value-column naming.

    Returns ``(rows, value_header)``. Raises on a structurally unreadable CSV.
    """
    # Drop OpenQuake's leading ``#`` banner comment line(s).
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise PostprocessOpenQuakeError(
            "OQ_HAZARD_EMPTY", message="hazard-map CSV has no data lines"
        )
    reader = csv.reader(io.StringIO("\n".join(lines)))
    header = next(reader, None)
    if not header:
        raise PostprocessOpenQuakeError(
            "OQ_HAZARD_READ_FAILED", message="hazard-map CSV missing a header row"
        )
    cols = [c.strip().lower() for c in header]
    try:
        lon_idx = cols.index("lon")
        lat_idx = cols.index("lat")
    except ValueError as exc:
        raise PostprocessOpenQuakeError(
            "OQ_HAZARD_READ_FAILED",
            message=f"hazard-map CSV header missing lon/lat columns: {header!r}",
        ) from exc
    # The hazard VALUE is the first column that is neither lon nor lat (and not a
    # depth/vs30 site column).
    val_idx = None
    for i, name in enumerate(cols):
        if i in (lon_idx, lat_idx):
            continue
        if name in {"depth", "vs30", "z1pt0", "z2pt5"}:
            continue
        val_idx = i
        break
    if val_idx is None:
        raise PostprocessOpenQuakeError(
            "OQ_HAZARD_READ_FAILED",
            message=f"hazard-map CSV has no hazard-value column: {header!r}",
        )
    value_header = header[val_idx].strip()

    rows: list[tuple[float, float, float]] = []
    for raw in reader:
        if not raw or len(raw) <= max(lon_idx, lat_idx, val_idx):
            continue
        try:
            lon = float(raw[lon_idx])
            lat = float(raw[lat_idx])
            val = float(raw[val_idx])
        except (TypeError, ValueError):
            continue
        rows.append((lon, lat, val))
    if not rows:
        raise PostprocessOpenQuakeError(
            "OQ_HAZARD_EMPTY", message="hazard-map CSV has no parseable site rows"
        )
    return rows, value_header


# --------------------------------------------------------------------------- #
# Rasterize the scattered site values onto a regular lon/lat lattice.
# --------------------------------------------------------------------------- #
def rasterize_hazard_sites(
    rows: list[tuple[float, float, float]],
) -> tuple[Any, tuple[float, float, float, float], float]:
    """Place the OpenQuake site values onto a regular EPSG:4326 raster grid.

    The PSHA site grid is regular (laid by ``region_grid_spacing``), so the
    distinct sorted lon/lat values define the lattice. We snap each site onto its
    nearest lattice cell and fill the value; un-hit cells stay NaN. Returns
    ``(grid, (min_lon,min_lat,max_lon,max_lat), cell_deg)``. ``grid`` is row 0 =
    NORTH (north-up, ready for ``from_origin(west, north, ...)``).
    """
    try:
        import numpy as np  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise PostprocessOpenQuakeError(
            "OQ_DEPENDENCY_MISSING", message=f"numpy unavailable: {exc}"
        ) from exc

    lons = sorted({round(r[0], 6) for r in rows})
    lats = sorted({round(r[1], 6) for r in rows})
    if len(lons) < 1 or len(lats) < 1:
        raise PostprocessOpenQuakeError(
            "OQ_HAZARD_EMPTY", message="no distinct site coordinates"
        )

    # Cell size from the median spacing of the lattice axes (robust to a single
    # row/col). Default to a small epsilon so a 1x1 grid still rasterizes.
    def _median_step(vals: list[float]) -> float:
        if len(vals) < 2:
            return 0.05
        steps = [b - a for a, b in zip(vals, vals[1:]) if b > a]
        steps.sort()
        return steps[len(steps) // 2] if steps else 0.05

    step_lon = _median_step(lons)
    step_lat = _median_step(lats)
    cell = float(min(step_lon, step_lat))

    width = len(lons)
    height = len(lats)
    lon_index = {v: i for i, v in enumerate(lons)}
    # row 0 = north -> iterate lats descending.
    lats_desc = list(reversed(lats))
    lat_index = {v: i for i, v in enumerate(lats_desc)}

    grid = np.full((height, width), np.nan, dtype="float32")
    for lon, lat, val in rows:
        # Snap to the nearest lattice node (handles float jitter).
        li = lon_index.get(round(lon, 6))
        ri = lat_index.get(round(lat, 6))
        if li is None:
            li = min(range(width), key=lambda i: abs(lons[i] - lon))
        if ri is None:
            ri = min(range(height), key=lambda i: abs(lats_desc[i] - lat))
        grid[ri, li] = float(val)

    min_lon, max_lon = lons[0], lons[-1]
    min_lat, max_lat = lats[0], lats[-1]
    # Expand the bbox by half a cell so the raster bounds frame the site centers.
    half = cell / 2.0
    bbox = (min_lon - half, min_lat - half, max_lon + half, max_lat + half)
    return grid, bbox, cell


# --------------------------------------------------------------------------- #
# Metrics (the narration scalars).
# --------------------------------------------------------------------------- #
def compute_hazard_metrics(
    grid: Any, cell_deg: float, mean_lat: float
) -> tuple[float, float, int]:
    """Compute ``(max_hazard_value, hazard_area_km2, n_sites)`` from the grid.

    ``hazard_area_km2`` is the footprint of cells above ``HAZARD_FLOOR_VALUE``
    (each cell's area = the cell extent in km^2, accounting for the lat-dependent
    longitude foreshortening). ``n_sites`` counts the non-NaN cells (the PSHA
    sites). Plain arithmetic — no LLM.
    """
    import numpy as np  # type: ignore[import-not-found]

    arr = np.asarray(grid, dtype="float64")
    finite = np.isfinite(arr)
    n_sites = int(np.count_nonzero(finite))
    if n_sites == 0:
        return 0.0, 0.0, 0
    max_val = float(np.nanmax(arr))
    # Cell area in km^2: (cell_deg * 111.32) for lat extent;
    # (cell_deg * 111.32 * cos(lat)) for lon extent.
    km_per_deg = 111.32
    lat_km = cell_deg * km_per_deg
    lon_km = cell_deg * km_per_deg * abs(math.cos(math.radians(mean_lat)))
    cell_area = lat_km * lon_km
    above = np.logical_and(finite, arr > HAZARD_FLOOR_VALUE)
    hazard_area = float(np.count_nonzero(above)) * cell_area
    return max_val, hazard_area, n_sites


# --------------------------------------------------------------------------- #
# COG write (already EPSG:4326 — no reprojection).
# --------------------------------------------------------------------------- #
def _write_cog(grid: Any, bbox: tuple[float, float, float, float]) -> Path:
    """Write the hazard grid to an EPSG:4326 COG. Sub-floor cells -> NaN."""
    try:
        import numpy as np  # type: ignore[import-not-found]
        import rasterio  # type: ignore[import-not-found]
        from rasterio.transform import from_bounds
    except Exception as exc:  # noqa: BLE001
        raise PostprocessOpenQuakeError(
            "OQ_DEPENDENCY_MISSING",
            message=f"numpy/rasterio unavailable: {exc}",
        ) from exc

    arr = np.asarray(grid, dtype="float32")
    arr_masked = np.where(arr > HAZARD_FLOOR_VALUE, arr, np.nan).astype("float32")
    height, width = arr_masked.shape
    min_lon, min_lat, max_lon, max_lat = bbox
    transform = from_bounds(min_lon, min_lat, max_lon, max_lat, width, height)

    dst_cog = Path(tempfile.NamedTemporaryFile(suffix="_hazard_4326.tif", delete=False).name)
    try:
        profile = {
            "driver": "COG",
            "crs": "EPSG:4326",
            "transform": transform,
            "width": width,
            "height": height,
            "count": 1,
            "dtype": "float32",
            "nodata": float("nan"),
            "compress": "LZW",
        }
        with rasterio.open(dst_cog, "w", **profile) as dst:
            dst.write(arr_masked, 1)
    except Exception as exc:  # noqa: BLE001
        raise PostprocessOpenQuakeError(
            "OQ_COG_WRITE_FAILED",
            message=f"hazard COG write failed: {exc}",
        ) from exc
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
    """Upload the EPSG:4326 hazard COG to the runs bucket; return its object URI.

    Scheme-aware per ``cache.storage_scheme()`` (mirrors postprocess_modflow /
    postprocess_swmm). Under ``s3`` the upload goes via boto3 and FAILS TYPED on
    a missing ``GRACE2_RUNS_BUCKET`` or upload error — a silent file:// fallback
    on AWS is exactly the debug-invisible no-render failure. The default ``gs``
    branch keeps a best-effort file:// fallback for offline/local dev.
    """
    from ..tools.cache import storage_scheme

    if storage_scheme() == "s3":
        bucket = runs_bucket or (os.environ.get("GRACE2_RUNS_BUCKET") or "").strip()
        if not bucket:
            raise PostprocessOpenQuakeError(
                "OQ_COG_UPLOAD_FAILED",
                message=(
                    "GRACE2_RUNS_BUCKET must be set under storage_scheme=s3 "
                    "(no GCP-named default on AWS)"
                ),
                details={"local_cog": str(local_cog)},
            )
        dest = f"s3://{bucket}/{run_id}/seismic_hazard_4326.tif"
        try:
            from ..tools.solver import _get_s3_client

            bkt, _, k = dest[len("s3://"):].partition("/")
            with open(local_cog, "rb") as fh:
                _get_s3_client().put_object(Bucket=bkt, Key=k, Body=fh)
        except Exception as exc:  # noqa: BLE001
            raise PostprocessOpenQuakeError(
                "OQ_COG_UPLOAD_FAILED",
                message=f"hazard COG upload to {dest} failed: {exc}",
                details={"local_cog": str(local_cog)},
            ) from exc
        return dest

    # gs / local-dev best-effort: try GCS, else return the local file:// URI.
    bucket = runs_bucket or (os.environ.get("GRACE2_RUNS_BUCKET") or "").strip()
    if not bucket:
        return f"file://{local_cog}"
    try:
        from google.cloud import storage  # type: ignore[import-not-found]

        dest = f"gs://{bucket}/{run_id}/seismic_hazard_4326.tif"
        client = storage.Client()
        b, _, k = dest[len("gs://"):].partition("/")
        client.bucket(b).blob(k).upload_from_filename(str(local_cog))
        return dest
    except Exception:  # noqa: BLE001
        return f"file://{local_cog}"


# --------------------------------------------------------------------------- #
# publish_layer dispatch (callable; mocked in tests).
# --------------------------------------------------------------------------- #
def _dispatch_publish_layer(cog_uri: str, layer_id: str) -> str | None:
    """Publish the hazard COG; return the WMS URL / tile template or None.

    Non-fatal (mirrors postprocess_modflow): a publish failure falls back to the
    COG URI so the rest of the envelope is usable. Skips publish for
    non-object-store URIs (local mode has nothing for a tile server to read).
    """
    if not (cog_uri.startswith("gs://") or cog_uri.startswith("s3://")):
        logger.warning(
            "publish_layer SKIPPED for %s: COG URI is not gs:// or s3:// (%s); "
            "the hazard map will NOT render as a map layer.",
            layer_id,
            cog_uri,
        )
        return None
    try:
        from ..tools.publish_layer import publish_layer

        wms_url = publish_layer(
            layer_uri=cog_uri,
            layer_id=layer_id,
            style_preset=SEISMIC_HAZARD_STYLE_PRESET,
        )
        logger.info("publish_layer succeeded layer_id=%s wms_url=%s", layer_id, wms_url)
        return wms_url
    except Exception as exc:  # noqa: BLE001
        logger.warning("publish_layer failed for %s: %s", layer_id, exc)
        return None


# --------------------------------------------------------------------------- #
# Top-level postprocess.
# --------------------------------------------------------------------------- #
def postprocess_openquake(
    hazard_map_csv_text: str,
    *,
    run_id: str,
    imt: str,
    poe: float,
    investigation_time_years: float,
    runs_bucket: str | None = None,
    publish: bool = True,
) -> SeismicHazardLayerURI:
    """Convert an OpenQuake hazard-MAP CSV into a ``SeismicHazardLayerURI``.

    Parses the per-site hazard values, rasterizes them onto a regular EPSG:4326
    grid, writes a COG, computes the hazard metrics, uploads + (optionally)
    publishes the COG, and returns the typed seismic-hazard layer.

    Args:
        hazard_map_csv_text: the text of OpenQuake's exported hazard-map CSV.
        run_id: the run identifier the COG is keyed under in the runs bucket.
        imt: the Intensity Measure Type the map represents (echoed onto the layer).
        poe: the probability of exceedance the map was computed at.
        investigation_time_years: the PoE window, years (for the return-period
            scalar the agent narrates).
        runs_bucket: optional override for the runs bucket name.
        publish: when True, dispatch ``publish_layer`` (mocked in tests).

    Returns:
        ``SeismicHazardLayerURI`` with ``max_hazard_value`` + ``hazard_area_km2``
        + ``return_period_years`` and (when published) a WMS ``uri``, else the
        COG URI.

    Raises:
        PostprocessOpenQuakeError: any read / rasterize / write / upload step
            failed; ``error_code`` identifies the stage.
    """
    rows, _value_header = parse_hazard_map_csv(hazard_map_csv_text)
    grid, bbox, cell_deg = rasterize_hazard_sites(rows)
    mean_lat = (bbox[1] + bbox[3]) / 2.0
    max_val, hazard_area_km2, n_sites = compute_hazard_metrics(grid, cell_deg, mean_lat)

    # Return period implied by the PoE over the investigation time.
    try:
        rp_years = (
            -float(investigation_time_years) / math.log(1.0 - float(poe))
            if 0.0 < poe < 1.0 and investigation_time_years > 0.0
            else 0.0
        )
    except (ValueError, ZeroDivisionError):
        rp_years = 0.0

    logger.info(
        "postprocess_openquake run_id=%s imt=%s poe=%.4g rp=%.0fyr "
        "max_hazard=%.6g hazard_area_km2=%.6g n_sites=%d",
        run_id,
        imt,
        poe,
        rp_years,
        max_val,
        hazard_area_km2,
        n_sites,
    )

    cog_path = _write_cog(grid, bbox)
    bbox_4326 = _cog_bbox_4326(cog_path) or bbox
    cog_uri = _upload_cog(cog_path, run_id, runs_bucket)

    layer_id = f"seismic-hazard-{run_id}"
    final_uri = cog_uri
    if publish:
        wms_url = _dispatch_publish_layer(cog_uri, layer_id)
        if wms_url:
            final_uri = wms_url

    # Units: PGA/SA in g, PGV in cm/s.
    units = "cm/s" if imt.upper().startswith("PGV") else "g"
    name = f"Seismic hazard ({imt}, {int(round(rp_years))}-yr return period)"

    return SeismicHazardLayerURI(
        layer_id=layer_id,
        name=name,
        layer_type="raster",
        uri=final_uri,
        style_preset=SEISMIC_HAZARD_STYLE_PRESET,
        role="primary",
        units=units,
        bbox=bbox_4326,
        imt=imt,
        poe=poe,
        investigation_time_years=investigation_time_years,
        return_period_years=rp_years,
        max_hazard_value=max_val,
        hazard_area_km2=hazard_area_km2,
        n_sites=n_sites,
    )
