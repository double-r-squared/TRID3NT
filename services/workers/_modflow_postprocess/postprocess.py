"""Orchestrator: run the MODFLOW plume postprocess on a LOCAL gwt_model.ucn.

The single entry point the MODFLOW worker entrypoint calls after the mf6 solve
(before ``_write_completion``) on the ``--build-spec-uri`` path. It:

  1. locates + reads the LOCAL ``gwt_model.ucn`` (no S3 download) via flopy -- the
     FINAL-timestep, max-over-layers concentration grid (mg/L, 2D),
  2. reads the deck's grid georegistration (origin + cell size + model CRS) via
     flopy so the COG is placed on Earth,
  3. reprojects the plume grid to an EPSG:4326 COG written into the deck dir under
     the DETERMINISTIC key ``plume_concentration_4326.tif`` (so the entrypoint's
     ``*.tif`` output sweep ships it with no new upload code),
  4. computes the plume metrics (max concentration + plume area),
  5. assembles the typed ``publish_manifest.json`` dict (reusing the shared
     ``_raster_postprocess.manifest`` schema),
  6. applies the EMPTY-PLUME HONESTY GATE (plume_area_km2 == 0 -> status=error with
     ``MODFLOW_PLUME_EMPTY``) so the agent never registers a status=ok-but-empty
     layer (Invariant 1 / FR-AS-7).

Byte-faithful port of ``grace2_agent.workflows.postprocess_modflow`` (spill path):
same final-concentration reduction, same 1e30 dry-cell mask, same below-floor
render mask, same bilinear warp, same PLUME_STYLE_PRESET. It NEVER imports agent
code and NEVER itself writes completion.json -- it RETURNS the manifest dict + the
status the entrypoint folds into completion.json.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.workers._raster_postprocess import manifest as _manifest

LOG = logging.getLogger("grace2.worker.modflow_postprocess")

#: The MF6 concentration output stem the GWT OC package writes (gwt_adapter
#: registers ``gwt_model.ucn``). Recursive glob captures it wherever it lands.
GWT_UCN_FILENAME: str = "gwt_model.ucn"

#: Concentration floor (mg/L) below which a cell is NOT counted as plume (and is
#: masked to NaN in the render COG). Byte-identical to the agent's
#: ``postprocess_modflow.PLUME_DETECTION_FLOOR_MGL``.
PLUME_DETECTION_FLOOR_MGL: float = 0.001

#: TiTiler style preset key for the plume layer (agent re-templates the rescale /
#: colormap from this KEY). Byte-identical to the agent's PLUME_STYLE_PRESET.
PLUME_STYLE_PRESET: str = "continuous_plume_concentration"

#: Default per-cell area (m^2) when the deck georegistration is unavailable
#: (gwt_adapter CELL_SIZE_M == 50 m -> 2500 m^2). Mirrors the agent default.
_DEFAULT_CELL_AREA_M2: float = 2500.0

#: The deterministic COG key written into the deck dir (the entrypoint's ``*.tif``
#: sweep ships it; the manifest points at the uploaded runs-bucket URI).
_PLUME_COG_FILENAME: str = "plume_concentration_4326.tif"


@dataclass
class ModflowPostprocessResult:
    """What the entrypoint folds into completion.json + writes as the manifest."""

    status: str  # "ok" | "error"
    manifest: dict[str, Any] | None
    metrics: dict[str, Any] = field(default_factory=dict)
    cog_paths: list[Path] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None


def _read_final_concentration(ucn_path: Path) -> Any:
    """Read the FINAL-timestep, max-over-layers concentration grid (mg/L, 2D).

    MF6 GWT concentration output is a binary HEADFILE-format array; flopy reads it
    via ``HeadFile(..., text="CONCENTRATION")``. ``get_data(totim=last)`` returns a
    ``(nlay, nrow, ncol)`` array; we take ``nanmax`` over the layer axis for a 2D
    worst-case (max-over-depth) grid. Raises ``ValueError`` on no timesteps / a
    non-2D reducible array. Byte-faithful to the agent reader.
    """
    import flopy.utils  # type: ignore[import-not-found]
    import numpy as np  # type: ignore[import-not-found]

    cobj = flopy.utils.HeadFile(str(ucn_path), text="CONCENTRATION")
    times = cobj.get_times()
    if not times:
        raise ValueError(f"{ucn_path} carries no concentration timesteps")
    data = cobj.get_data(totim=times[-1])  # (nlay, nrow, ncol)

    arr = np.asarray(data, dtype="float64")
    if arr.ndim == 3:
        final2d = np.nanmax(arr, axis=0)
    elif arr.ndim == 2:
        final2d = arr
    else:
        final2d = np.squeeze(arr)
        if final2d.ndim != 2:
            raise ValueError(
                f"concentration array has shape {arr.shape}; cannot reduce to 2D"
            )
    # MF6 inactive/dry cells are flagged with a large sentinel (1e30). Mask them.
    final2d = np.where(np.abs(final2d) > 1e29, np.nan, final2d)
    return final2d


def _locate_ucn(deck_dir: Path) -> Path | None:
    """Find ``gwt_model.ucn`` (or any ``*.ucn``) under the local deck dir."""
    import glob

    hits = sorted(glob.glob(str(deck_dir / "**" / GWT_UCN_FILENAME), recursive=True))
    if not hits:
        hits = sorted(glob.glob(str(deck_dir / "**" / "*.ucn"), recursive=True))
    return Path(hits[0]) if hits else None


def _grid_georegistration(deck_dir: Path) -> dict[str, Any] | None:
    """Read grid origin + cell size + CRS from the deck via flopy.

    Prefers the GWT transport grid; falls back to GWF (GWF-only archetypes) or any
    model. Returns ``None`` if the deck cannot be loaded (identity transform then).
    """
    try:
        import flopy  # type: ignore[import-not-found]

        sim = flopy.mf6.MFSimulation.load(sim_ws=str(deck_dir), verbosity_level=0)
        model = None
        for prefix in ("gwt", "gwf"):
            for mname in sim.model_names:
                if mname.startswith(prefix):
                    model = sim.get_model(mname)
                    break
            if model is not None:
                break
        if model is None and sim.model_names:
            model = sim.get_model(sim.model_names[0])
        if model is None:
            return None
        mg = model.modelgrid
        return {
            "xorigin": float(mg.xoffset),
            "yorigin": float(mg.yoffset),
            "delr": float(mg.delr[0]),
            "delc": float(mg.delc[0]),
            "nrow": int(mg.nrow),
            "ncol": int(mg.ncol),
        }
    except Exception as exc:  # noqa: BLE001
        LOG.warning("could not read deck georegistration from %s: %s", deck_dir, exc)
        return None


def _compute_plume_metrics(final_grid: Any, cell_area_m2: float) -> tuple[float, float]:
    """(max_concentration_mgl, plume_area_km2) from a 2D conc grid (mg/L).

    Byte-faithful to ``postprocess_modflow.compute_plume_metrics``: a cell counts
    toward the plume iff its concentration is strictly > the detection floor.
    """
    import numpy as np  # type: ignore[import-not-found]

    arr = np.asarray(final_grid, dtype="float64")
    if arr.size == 0:
        return 0.0, 0.0
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return 0.0, 0.0
    max_conc = max(0.0, float(np.max(finite)))
    plume_cells = int(np.count_nonzero(finite > PLUME_DETECTION_FLOOR_MGL))
    plume_area_km2 = float(plume_cells) * float(cell_area_m2) / 1_000_000.0
    return max_conc, plume_area_km2


def _write_plume_cog(
    final2d: Any, model_crs: str, geo: dict[str, Any] | None, out_path: Path
) -> None:
    """Reproject the concentration grid to an EPSG:4326 COG at ``out_path``.

    Vendored from ``cog_io.write_cog_4326_from_grid`` (reproject path) +
    ``postprocess_modflow._write_reprojected_cog`` (mask-below-floor, bilinear).
    rasterio-only -- NO agent import. flopy row 0 == north, so the source
    top-left is ``(xorigin, yorigin + nrow*delc)``.
    """
    import numpy as np  # type: ignore[import-not-found]
    import rasterio  # type: ignore[import-not-found]
    from rasterio.warp import Resampling, calculate_default_transform
    from rasterio.warp import reproject as _warp_reproject

    arr = np.asarray(final2d, dtype="float32")
    # Render mask: clean cells (<= floor) -> NaN so the COG shows only the plume.
    arr = np.where(arr > PLUME_DETECTION_FLOOR_MGL, arr, np.nan).astype("float32")
    nrow, ncol = arr.shape

    if geo is not None:
        delr = geo["delr"]
        delc = geo["delc"]
        west = geo["xorigin"]
        north = geo["yorigin"] + nrow * delc
        src_transform = rasterio.transform.from_origin(west, north, delr, delc)
    else:
        src_transform = rasterio.Affine.identity()

    src_tmp = out_path.with_suffix(".src.tif")
    try:
        with rasterio.open(
            src_tmp, "w", driver="GTiff", width=ncol, height=nrow, count=1,
            dtype="float32", crs=model_crs, transform=src_transform,
            nodata=float("nan"),
        ) as dst:
            dst.write(arr, 1)
        dst_crs = "EPSG:4326"
        with rasterio.open(src_tmp) as src:
            transform, out_w, out_h = calculate_default_transform(
                src.crs, dst_crs, src.width, src.height, *src.bounds
            )
            profile = {
                "driver": "COG", "crs": dst_crs, "transform": transform,
                "width": out_w, "height": out_h, "count": 1, "dtype": "float32",
                "nodata": float("nan"), "compress": "LZW",
            }
            with rasterio.open(out_path, "w", **profile) as dst:
                _warp_reproject(
                    source=rasterio.band(src, 1),
                    destination=rasterio.band(dst, 1),
                    src_transform=src.transform, src_crs=src.crs,
                    dst_transform=transform, dst_crs=dst_crs,
                    resampling=Resampling.bilinear,
                )
    finally:
        try:
            src_tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _cog_bbox_4326(cog_path: Path) -> list[float] | None:
    """Return the COG's ``[min_lon, min_lat, max_lon, max_lat]`` (or None)."""
    try:
        import rasterio  # type: ignore[import-not-found]

        with rasterio.open(cog_path) as ds:
            b = ds.bounds
            return [float(b.left), float(b.bottom), float(b.right), float(b.top)]
    except Exception:  # noqa: BLE001
        return None


def _band_stats(cog_path: Path) -> dict[str, Any]:
    """Precompute min/max/percentiles over the plume COG so the agent skips a read."""
    try:
        import numpy as np  # type: ignore[import-not-found]
        import rasterio  # type: ignore[import-not-found]

        with rasterio.open(cog_path) as ds:
            arr = ds.read(1, masked=True)
        finite = np.asarray(arr.compressed(), dtype="float64")
        finite = finite[np.isfinite(finite)]
        if finite.size == 0:
            return {"min": None, "max": None, "p2": None, "p98": None}
        return {
            "min": float(np.min(finite)),
            "max": float(np.max(finite)),
            "p2": float(np.percentile(finite, 2)),
            "p98": float(np.percentile(finite, 98)),
        }
    except Exception:  # noqa: BLE001
        return {"min": None, "max": None, "p2": None, "p98": None}


def run_plume_postprocess(
    run_id: str,
    deck_dir: Path,
    model_crs: str,
    runs_uri_for: Any,
) -> ModflowPostprocessResult:
    """Run the plume postprocess on the LOCAL deck dir; return the manifest result.

    ``runs_uri_for`` is a callable ``rel -> uri`` (the entrypoint's
    ``lambda rel: _runs_uri(run_id, rel)``). The COG is written into ``deck_dir``
    under the deterministic key so the entrypoint's output sweep uploads it; the
    manifest's ``cog_uri`` is the resolved runs-bucket URI for that key.

    NEVER raises for an expected-empty result -- returns a status=error result with
    the typed ``MODFLOW_PLUME_EMPTY`` code (the honesty gate). A genuine read/write
    failure returns a status=error result with ``MODFLOW_POSTPROCESS_FAILED`` so
    the entrypoint surfaces it (never a silent ok-with-no-layer).
    """
    ucn_path = _locate_ucn(deck_dir)
    if ucn_path is None:
        return ModflowPostprocessResult(
            status="error", manifest=None,
            error_code="MODFLOW_PLUME_OUTPUT_MISSING",
            error_message=f"no {GWT_UCN_FILENAME} found under {deck_dir}",
        )
    try:
        final2d = _read_final_concentration(ucn_path)
    except Exception as exc:  # noqa: BLE001
        return ModflowPostprocessResult(
            status="error", manifest=None,
            error_code="MODFLOW_PLUME_OUTPUT_READ_FAILED",
            error_message=f"could not read concentration from {ucn_path}: {exc}",
        )

    geo = _grid_georegistration(deck_dir)
    cell_area_m2 = (
        float(geo["delr"]) * float(geo["delc"]) if geo is not None
        else _DEFAULT_CELL_AREA_M2
    )
    max_conc, plume_area_km2 = _compute_plume_metrics(final2d, cell_area_m2)
    LOG.info(
        "modflow postprocess run_id=%s max_concentration_mgl=%.6g plume_area_km2=%.6g",
        run_id, max_conc, plume_area_km2,
    )

    # --- EMPTY-PLUME HONESTY GATE (Invariant 1) --------------------------------
    if plume_area_km2 <= 0.0:
        return ModflowPostprocessResult(
            status="error",
            manifest=_manifest.build_manifest(
                engine="modflow", run_id=run_id, status="error",
                frame_count=0,
                metrics={
                    "max_concentration_mgl": max_conc,
                    "plume_area_km2": plume_area_km2,
                },
                layers=[], error_code="MODFLOW_PLUME_EMPTY",
            ),
            metrics={
                "max_concentration_mgl": max_conc,
                "plume_area_km2": plume_area_km2,
            },
            error_code="MODFLOW_PLUME_EMPTY",
            error_message=(
                "solve clean but the plume field is empty "
                "(no cell above the detection floor)"
            ),
        )

    cog_path = deck_dir / _PLUME_COG_FILENAME
    try:
        _write_plume_cog(final2d, model_crs, geo, cog_path)
    except Exception as exc:  # noqa: BLE001
        return ModflowPostprocessResult(
            status="error", manifest=None,
            error_code="MODFLOW_PLUME_COG_WRITE_FAILED",
            error_message=f"plume COG write/reproject failed: {exc}",
        )

    bbox = _cog_bbox_4326(cog_path)
    cog_uri = runs_uri_for(_PLUME_COG_FILENAME)
    layer = _manifest.build_layer_entry(
        layer_id_stem=f"plume-concentration-{run_id}",
        name="Contaminant Plume (peak concentration)",
        role="primary",
        style_preset=PLUME_STYLE_PRESET,
        units="mg/L",
        cog_uri=cog_uri,
        frame_no=None,
        bbox=bbox,
        band_stats=_band_stats(cog_path),
        metrics={
            "max_concentration_mgl": max_conc,
            "plume_area_km2": plume_area_km2,
        },
    )
    manifest = _manifest.build_manifest(
        engine="modflow", run_id=run_id, status="ok", frame_count=1,
        metrics={
            "max_concentration_mgl": max_conc,
            "plume_area_km2": plume_area_km2,
        },
        layers=[layer],
    )
    return ModflowPostprocessResult(
        status="ok", manifest=manifest,
        metrics={
            "max_concentration_mgl": max_conc,
            "plume_area_km2": plume_area_km2,
        },
        cog_paths=[cog_path],
    )
