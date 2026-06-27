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
import tempfile
from pathlib import Path
from typing import Any

from grace2_contracts.modflow_contracts import (
    BudgetPartitionLayerURI,
    DewaterLayerURI,
    DrawdownLayerURI,
    PlumeLayerURI,
    SeepageLayerURI,
)

from . import cog_io
from .cog_io import CogIoError

logger = logging.getLogger("grace2_agent.workflows.postprocess_modflow")

__all__ = [
    "PostprocessMODFLOWError",
    "postprocess_modflow",
    "postprocess_river_seepage",
    "postprocess_drawdown",
    "postprocess_dewatering",
    "postprocess_budget_partition",
    "publish_modflow_quantities",
    "compute_plume_metrics",
    "compute_seepage_metrics",
    "compute_drawdown_metrics",
    "compute_cbc_term_metrics",
    "compute_budget_partition",
    "PLUME_DETECTION_FLOOR_MGL",
    "PLUME_STYLE_PRESET",
    "SEEPAGE_STYLE_PRESET",
    "HEAD_STYLE_PRESET",
    "DRAWDOWN_STYLE_PRESET",
    "DEWATERING_STYLE_PRESET",
    "GWF_CBC_FILENAME",
    "GWF_HDS_FILENAME",
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

#: TiTiler style preset for the sustainable-yield drawdown (head-decline) COG
#: (sprint-18 Wave-1). Matches the output_quantities registry "drawdown" spec.
DRAWDOWN_STYLE_PRESET: str = "continuous_drawdown_m"

#: TiTiler style preset for the mine-dewatering DRN-outflow COG (sprint-18
#: Wave-1). Matches the output_quantities registry "dewatering-rate" spec.
DEWATERING_STYLE_PRESET: str = "continuous_dewatering_rate"

#: CBC budget terms the generalized cell-by-cell reader scatters onto a grid.
#: Each is a head-dependent / source-sink package whose budget record carries a
#: per-cell signed flow (m^3/day, MF6 sign: positive = INTO the cell/aquifer).
_CBC_GRID_TERMS: frozenset[str] = frozenset(
    {"DRN", "EVT", "RCH", "WEL", "RCHA", "RIV", "GHB", "CHD"}
)

#: Budget partition headline EXCLUDES the inter-cell flow term (it is internal
#: bookkeeping, not a source/sink boundary the user narrates).
_BUDGET_EXCLUDE_FROM_HEADLINE: frozenset[str] = frozenset({"FLOW-JA-FACE"})


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
    import numpy as np  # local - caller vouched for the import path

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
    import numpy as np  # local - caller vouched for the import path

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


def compute_drawdown_metrics(
    decline_grid: Any,
) -> float:
    """Compute the peak head DECLINE (>= 0) from a 2D drawdown grid.

    Pure arithmetic over the per-cell head-decline grid (m; pre-pumping head
    minus pumped head, so a positive value is a drawdown and a negative value is
    a mounding/recovery artifact). The headline is the maximum decline anywhere
    in the domain, clamped at 0 so a tiny numerical mounding never narrates as a
    negative drawdown.

    Args:
        decline_grid: 2D array (rows x cols) of head decline in m (NaN off-grid).

    Returns:
        ``max_drawdown_m`` (>= 0): the largest positive head decline.
    """
    import numpy as np  # local - caller vouched for the import path

    arr = np.asarray(decline_grid, dtype="float64")
    if arr.size == 0:
        return 0.0
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return 0.0
    return max(0.0, float(np.max(finite)))


def compute_cbc_term_metrics(
    term_grid: Any,
) -> tuple[float, int]:
    """Compute (total_outflow_magnitude_m3_day, active_cell_count) from a CBC grid.

    Pure arithmetic over a per-cell signed CBC budget grid (m^3/day, NaN where
    the term is absent). MF6 budget sign: a positive ``q`` is flow INTO the cell
    from the boundary; a negative ``q`` is flow OUT of the cell to the boundary.
    For a DRAIN (mine_dewatering) the drain removes water, so the per-cell flux
    is NEGATIVE and the dewatering RATE is the magnitude of that outflow.

    Returns:
        ``(total_magnitude_m3_day, active_cell_count)``:
          * total_magnitude_m3_day: sum of |q| over every finite (active) cell,
            >= 0 - the pump-to-dewater rate for a DRN term.
          * active_cell_count: number of finite cells the term touched.
    """
    import numpy as np  # local - caller vouched for the import path

    arr = np.asarray(term_grid, dtype="float64")
    if arr.size == 0:
        return 0.0, 0
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return 0.0, 0
    total_mag = float(np.sum(np.abs(finite)))
    return total_mag, int(finite.size)


def compute_budget_partition(
    term_totals: dict[str, float],
) -> dict[str, float]:
    """Build the narration-ready budget partition from per-term CBC sums.

    Pure dict transform over the raw per-term signed budget sums (m^3/day, MF6
    sign: positive = into the aquifer/zone). EXCLUDES the internal inter-cell
    ``FLOW-JA-FACE`` term from the headline partition (it is bookkeeping, not a
    source/sink the user narrates) and drops a term whose magnitude rounds to
    zero. Honest signs are preserved verbatim: an extraction WEL reads negative,
    a recharge reads positive. Never free-generated - every value comes from a
    real CBC record sum the caller measured.

    Args:
        term_totals: mapping of CBC record name -> signed sum over all cells.

    Returns:
        ``budget_partition_m3_day``: the filtered/normalized partition dict.
    """
    partition: dict[str, float] = {}
    for raw_name, value in term_totals.items():
        name = str(raw_name).strip().upper()
        if name in _BUDGET_EXCLUDE_FROM_HEADLINE:
            continue
        q = float(value)
        if abs(q) < 1e-9:
            continue
        partition[name.lower()] = q
    return partition


def _normalize_cbc_record_names(cbc: Any) -> dict[str, str]:
    """Return a {UPPER label -> exact record name} map for a CBC file.

    flopy's ``get_unique_record_names(decode=True)`` returns the record names
    (str or bytes, padded). We strip + decode each and key by the UPPER label so
    a term lookup (``"DRN"``, ``"FLOW-JA-FACE"``) resolves to the exact name the
    ``get_data(text=...)`` call needs. The first record matching a label wins.
    """
    out: dict[str, str] = {}
    for r in cbc.get_unique_record_names(decode=True):
        name = (r.strip() if isinstance(r, str) else r.strip().decode())
        key = name.upper()
        out.setdefault(key, name)
    return out


def _scatter_cbc_term_grid(
    cbc: Any, record_name: str, nrow: int, ncol: int
) -> Any:
    """Scatter the LAST-timestep CBC ``record_name`` budget onto a 2D grid.

    Reads the per-cell signed flux for ONE CBC term (DRN / WEL / RIV / RCH /
    ...) and scatters the last-timestep ``q`` values onto an (nrow, ncol) grid
    (NaN where the term is absent). Multi-layer cells accumulate onto the same
    2D cell (collapse the layer axis). The ``node`` field is a 1-based flat
    structured-grid index = lay*nrow*ncol + row*ncol + col + 1.

    Returns the 2D grid, or an all-NaN grid when the term carries no records.
    """
    import numpy as np  # type: ignore[import-not-found]

    grid = np.full((nrow, ncol), np.nan, dtype="float64")
    data = cbc.get_data(text=record_name)
    if not data:
        return grid
    last = data[-1]
    try:
        nodes = np.asarray(last["node"], dtype="int64")
        qvals = np.asarray(last["q"], dtype="float64")
    except Exception:  # noqa: BLE001 - list-style budget (older formats)
        nodes = np.asarray([int(r["node"]) for r in last], dtype="int64")
        qvals = np.asarray([float(r["q"]) for r in last], dtype="float64")
    cells_per_layer = nrow * ncol
    for node, q in zip(nodes, qvals):
        local = (int(node) - 1) % cells_per_layer
        row = local // ncol
        col = local % ncol
        if 0 <= row < nrow and 0 <= col < ncol:
            grid[row, col] = q if np.isnan(grid[row, col]) else grid[row, col] + q
    return grid


def _read_cbc_term_grid(
    cbc_path: Path, term: str, nrow: int, ncol: int
) -> Any:
    """Read ONE CBC budget term (e.g. DRN / WEL / RCH) into a 2D signed grid.

    Generalization of ``_read_riv_seepage_grid`` for the sprint-18 archetypes:
    the mine-dewatering DRN term, an RCH/EVT recharge term, etc. Resolves the
    term name case-insensitively against the file's unique record names and
    scatters the last-timestep flux onto the grid.

    Raises ``PostprocessMODFLOWError("DEWATER_OUTPUT_EMPTY")`` when the requested
    term is absent from the budget (a DRN run that wrote no DRN term is a real
    failure, not a silent empty layer).
    """
    try:
        import flopy.utils  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "DEWATER_OUTPUT_READ_FAILED",
            message=f"flopy/numpy not importable: {exc}",
            details={"cbc_path": str(cbc_path), "term": term},
        ) from exc

    try:
        cbc = flopy.utils.CellBudgetFile(str(cbc_path))
        names = _normalize_cbc_record_names(cbc)
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "DEWATER_OUTPUT_READ_FAILED",
            message=f"could not open CBC {cbc_path}: {exc}",
            details={"cbc_path": str(cbc_path), "term": term},
        ) from exc

    want = term.strip().upper()
    match = next((exact for key, exact in names.items() if want in key), None)
    if match is None:
        raise PostprocessMODFLOWError(
            "DEWATER_OUTPUT_EMPTY",
            message=(
                f"no {want} budget record in {cbc_path}; "
                f"records present: {sorted(names)}"
            ),
            details={"cbc_path": str(cbc_path), "term": term},
        )
    try:
        return _scatter_cbc_term_grid(cbc, match, nrow, ncol)
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "DEWATER_OUTPUT_READ_FAILED",
            message=f"could not read {want} budget from {cbc_path}: {exc}",
            details={"cbc_path": str(cbc_path), "term": term},
        ) from exc


def _read_cbc_budget_partition(cbc_path: Path) -> dict[str, float]:
    """Read every CBC term and sum its per-cell flux -> a per-term total dict.

    Iterates the file's unique record names and sums the LAST-timestep ``q``
    over all cells for each term. The result feeds ``compute_budget_partition``
    (which drops FLOW-JA-FACE + near-zero terms). Honest signs preserved: each
    sum is the signed MF6 budget total (positive = into the aquifer).

    Raises ``PostprocessMODFLOWError("BUDGET_OUTPUT_EMPTY")`` when the file has
    no budget records at all.
    """
    try:
        import flopy.utils  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "BUDGET_OUTPUT_READ_FAILED",
            message=f"flopy/numpy not importable: {exc}",
            details={"cbc_path": str(cbc_path)},
        ) from exc

    try:
        cbc = flopy.utils.CellBudgetFile(str(cbc_path))
        names = _normalize_cbc_record_names(cbc)
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "BUDGET_OUTPUT_READ_FAILED",
            message=f"could not open CBC {cbc_path}: {exc}",
            details={"cbc_path": str(cbc_path)},
        ) from exc

    if not names:
        raise PostprocessMODFLOWError(
            "BUDGET_OUTPUT_EMPTY",
            message=f"no budget records in {cbc_path}",
            details={"cbc_path": str(cbc_path)},
        )

    totals: dict[str, float] = {}
    for key, exact in names.items():
        if key in _BUDGET_EXCLUDE_FROM_HEADLINE:
            # Skip the internal inter-cell term entirely (also dropped later, but
            # its per-cell array is large + uninformative for the partition).
            continue
        try:
            data = cbc.get_data(text=exact)
        except Exception:  # noqa: BLE001 - skip an unreadable term, not fatal
            continue
        if not data:
            continue
        last = data[-1]
        try:
            arr = np.asarray(last["q"], dtype="float64")
        except Exception:  # noqa: BLE001
            try:
                # full-grid arrays come back as a plain ndarray; sum to one total.
                totals[key] = totals.get(key, 0.0) + float(
                    np.nansum(np.asarray(last, dtype="float64"))
                )
            except Exception:  # noqa: BLE001
                pass
            continue
        # Split a head-dependent / source-sink term into IN (q>0, into the
        # aquifer) and OUT (q<0, out of the aquifer) so a balanced boundary like
        # the regional CHD gradient narrates as separate inflow + outflow legs
        # rather than collapsing to a net ~0 that hides the throughflow. Honest
        # MF6 signs preserved (in positive, out negative).
        in_sum = float(np.nansum(arr[arr > 0.0]))
        out_sum = float(np.nansum(arr[arr < 0.0]))
        if abs(in_sum) > 0.0:
            totals[f"{key}_IN"] = totals.get(f"{key}_IN", 0.0) + in_sum
        if abs(out_sum) > 0.0:
            totals[f"{key}_OUT"] = totals.get(f"{key}_OUT", 0.0) + out_sum
        if abs(in_sum) == 0.0 and abs(out_sum) == 0.0:
            # A genuinely-zero term still records its net (0) so the absence is
            # explicit rather than silently dropped before compute_budget_partition.
            totals[key] = totals.get(key, 0.0) + float(np.nansum(arr))
    return totals


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
    except Exception:  # noqa: BLE001 - list-style budget (older formats)
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

    The deck dir holds the GWT (or, for a GWF-only Wave-1 archetype deck, the
    GWF) DIS package; flopy's modelgrid gives the lower-left origin
    (xorigin/yorigin) + cell widths (delr/delc). Returns None if the deck cannot
    be loaded (the caller then falls back to identity, which still yields valid
    metrics - only the geo-placement degrades).

    The two model halves share the SAME georegistered grid (the GWFGWT exchange
    requires it), so either works; we PREFER the GWT model (the spill/seepage
    deck's transport grid) and fall back to the GWF model (a GWF-only archetype
    deck has no GWT model). Any model with a structured modelgrid is acceptable.
    """
    if not deck_dir:
        return None
    try:
        import flopy  # type: ignore[import-not-found]

        sim = flopy.mf6.MFSimulation.load(sim_ws=str(deck_dir), verbosity_level=0)
        model = None
        # Prefer GWT (transport grid); fall back to GWF (GWF-only archetypes); then
        # any model in the sim (defensive). Same grid either way.
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
        logger.warning("could not read deck georegistration from %s: %s", deck_dir, exc)
        return None


# --------------------------------------------------------------------------- #
# COG write + reproject + upload
# --------------------------------------------------------------------------- #


#: stage -> (MODFLOW error_code) map (STEP 1 dedupe; byte-identical codes). The
#: write/reproject stages map to the PLUME_* codes (the seepage path reuses the
#: same writer, exactly as before this dedupe).
_MODFLOW_STAGE_CODES: dict[str, str] = {
    "DEPENDENCY": "PLUME_COG_WRITE_FAILED",
    "WRITE": "PLUME_COG_WRITE_FAILED",
    "REPROJECT": "PLUME_REPROJECT_FAILED",
    "CRS_MISMATCH": "PLUME_REPROJECT_FAILED",
    "UPLOAD": "PLUME_COG_UPLOAD_FAILED",
}


def _reraise_cogio(
    exc: CogIoError, *, model_crs: str | None = None
) -> "PostprocessMODFLOWError":
    """Map a cog_io ``CogIoError`` onto the MODFLOW typed error (preserves codes)."""
    code = _MODFLOW_STAGE_CODES.get(exc.stage, "POSTPROCESS_MODFLOW_FAILED")
    details = dict(exc.details)
    if model_crs is not None and "model_crs" not in details:
        details["model_crs"] = model_crs
    return PostprocessMODFLOWError(code, message=exc.message, details=details)


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
    to EPSG:4326 via ``cog_io.write_cog_4326_from_grid`` (``reproject=True``,
    ``Resampling.bilinear`` for the smooth concentration field; NO CRS round-trip
    guard, byte-identical to the pre-dedupe writer).

    Args:
        mask_below_floor: when True (the plume default — BYTE-IDENTICAL to the
            pre-J9 behavior), cells at/below ``PLUME_DETECTION_FLOOR_MGL`` are
            masked to NaN so the COG renders only the plume. When False (the J9
            river-seepage diverging layer), the array is written AS-IS (already
            NaN off the reach) so negative gaining values survive — masking by a
            positive floor would wrongly drop every gaining (negative) reach
            cell. Passed to cog_io as the declared ``mask`` callable.
    """
    import numpy as np  # type: ignore[import-not-found]
    import rasterio  # type: ignore[import-not-found]
    from rasterio.warp import Resampling

    arr = np.asarray(final2d, dtype="float32")
    nrow, ncol = arr.shape

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

    def _mask(a: Any) -> Any:
        if mask_below_floor:
            # Mask clean cells (<= floor) to NaN so the COG renders only the plume.
            return np.where(a > PLUME_DETECTION_FLOOR_MGL, a, np.nan).astype("float32")
        # Diverging seepage: keep the array as-is (NaN already marks off-reach).
        return a.astype("float32")

    try:
        return cog_io.write_cog_4326_from_grid(
            arr,
            src_crs=model_crs,
            src_transform=src_transform,
            reproject=True,
            resampling=Resampling.bilinear,
            mask=_mask,
            crs_roundtrip_guard=False,
            src_suffix="_src.tif",
            dst_suffix="_4326.tif",
        )
    except CogIoError as exc:
        raise _reraise_cogio(exc, model_crs=model_crs) from exc


def _cog_bbox_4326(cog_path: Path) -> tuple[float, float, float, float] | None:
    """Return the COG's (min_lon, min_lat, max_lon, max_lat) for zoom-to."""
    return cog_io.cog_bbox_4326(cog_path)


def _upload_cog(
    local_cog: Path,
    run_id: str,
    runs_bucket: str | None,
    *,
    cog_filename: str = "plume_concentration_4326.tif",
) -> str:
    """Upload the EPSG:4326 plume COG to the runs bucket; return its object URI.

    Thin shim over ``cog_io.upload_cog`` (STEP 1 dedupe; byte-identical):
    scheme-aware per ``cache.storage_scheme()``. ``s3`` via boto3
    (``ContentType=image/tiff``) FAILS TYPED on a missing ``GRACE2_RUNS_BUCKET`` /
    upload error (job-0241 / job-0292b: a silent file:// on AWS is the
    debug-invisible no-render failure). The ``gs`` branch keeps its best-effort
    ``file://`` fallback (the loud ImportError classification for a missing
    ``fsspec[gcs]`` is preserved by cog_io) for the offline-dev / local-mode path.
    """
    try:
        return cog_io.upload_cog(
            local_cog,
            run_id,
            runs_bucket,
            dest_filename=cog_filename,
            content_type="image/tiff",
            gs_backend="fsspec",
            gs_fallback_to_file=True,
            runs_bucket_default=RUNS_BUCKET_DEFAULT,
            log_label="plume COG",
        )
    except CogIoError as exc:
        raise _reraise_cogio(exc) from exc


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


# --------------------------------------------------------------------------- #
# levers STEP 3 -- NEW published quantities (registry-driven, ADDITIVE).
#
# The EXISTING plume + seepage stay on the byte-identical old postprocess path
# above. These helpers build the NEW quantities (concentration ANIMATION across
# all saved UCN steps + the GWF head / water-table) as registry readers and
# publish them through the shared executor (publish_quantities). Gated DEFAULT
# behind GRACE2_MODFLOW_REGISTRY_QUANTITIES until live-proven per engine.
# --------------------------------------------------------------------------- #
#: GWF head filename the OC HEAD FILEOUT writes (gwt_adapter).
GWF_HDS_FILENAME: str = "gwf_model.hds"

#: continuous head / water-table style preset (publish_layer._TITILER_STYLE_REGISTRY).
HEAD_STYLE_PRESET: str = "continuous_head_m"

#: MF6 inactive/dry-cell sentinel magnitude.
_MF6_DRY_SENTINEL: float = 1e29


def _resolve_gwf_hds_path(run_outputs_uri: str) -> Path:
    """Locate the GWF head file (``gwf_model.hds``) from a run output.

    Mirrors ``_resolve_gwf_cbc_path`` (s3 / gs / local), but targets the head
    FILEOUT. Raises ``PostprocessMODFLOWError("HEAD_OUTPUT_READ_FAILED")`` when
    the head file cannot be located / fetched.
    """
    if run_outputs_uri.startswith("s3://"):
        from ..tools.solver import _get_s3_client

        tmpdir = Path(tempfile.mkdtemp(prefix="modflow-hds-"))
        local_target = tmpdir / GWF_HDS_FILENAME
        source = (
            run_outputs_uri
            if run_outputs_uri.endswith(".hds")
            else run_outputs_uri.rstrip("/") + f"/{GWF_HDS_FILENAME}"
        )
        bucket_name, _, obj_key = source[len("s3://"):].partition("/")
        try:
            import shutil as _shutil

            resp = _get_s3_client().get_object(Bucket=bucket_name, Key=obj_key)
            with local_target.open("wb") as fh:
                _shutil.copyfileobj(resp["Body"], fh)
        except Exception as exc:  # noqa: BLE001
            raise PostprocessMODFLOWError(
                "HEAD_OUTPUT_READ_FAILED",
                message=f"could not fetch GWF head from {source}: {exc}",
                details={"run_outputs_uri": run_outputs_uri},
            ) from exc
        return local_target
    if run_outputs_uri.startswith("gs://"):
        try:
            import fsspec  # type: ignore[import-not-found]

            fs = fsspec.filesystem("gcs")
            tmpdir = Path(tempfile.mkdtemp(prefix="modflow-hds-"))
            local_target = tmpdir / GWF_HDS_FILENAME
            candidate = (
                run_outputs_uri
                if run_outputs_uri.endswith(".hds")
                else f"{run_outputs_uri.rstrip('/')}/{GWF_HDS_FILENAME}"
            )
            fs.get(candidate, str(local_target))
            return local_target
        except Exception as exc:  # noqa: BLE001
            raise PostprocessMODFLOWError(
                "HEAD_OUTPUT_READ_FAILED",
                message=f"could not fetch GWF head from {run_outputs_uri}: {exc}",
                details={"run_outputs_uri": run_outputs_uri},
            ) from exc

    p = Path(run_outputs_uri.replace("file://", ""))
    if p.is_file() and p.suffix == ".hds":
        return p
    if p.is_dir():
        hits = sorted(glob.glob(str(p / "**" / GWF_HDS_FILENAME), recursive=True))
        if not hits:
            hits = sorted(glob.glob(str(p / "**" / "*.hds"), recursive=True))
        if hits:
            return Path(hits[0])
    raise PostprocessMODFLOWError(
        "HEAD_OUTPUT_READ_FAILED",
        message=f"no {GWF_HDS_FILENAME} found under {run_outputs_uri}",
        details={"run_outputs_uri": run_outputs_uri},
    )


def _read_head_grid(hds_path: Path) -> Any:
    """Read the FINAL-timestep, max-over-layers head grid (m, 2D).

    GWF head output is a binary HEADFILE-format array; flopy reads it via
    ``HeadFile``. ``get_data(totim=last)`` returns ``(nlay, nrow, ncol)``; we
    take ``nanmax`` over the layer axis (the water-table = the uppermost active
    head) and mask the MF6 dry/inactive sentinel to NaN.
    """
    try:
        import flopy.utils  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "HEAD_OUTPUT_READ_FAILED",
            message=f"flopy/numpy not importable: {exc}",
            details={"hds_path": str(hds_path)},
        ) from exc
    try:
        hobj = flopy.utils.HeadFile(str(hds_path))
        times = hobj.get_times()
        if not times:
            raise PostprocessMODFLOWError(
                "HEAD_OUTPUT_EMPTY",
                message=f"{hds_path} carries no head timesteps",
                details={"hds_path": str(hds_path)},
            )
        data = hobj.get_data(totim=times[-1])
    except PostprocessMODFLOWError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "HEAD_OUTPUT_READ_FAILED",
            message=f"could not read head from {hds_path}: {exc}",
            details={"hds_path": str(hds_path)},
        ) from exc
    arr = np.asarray(data, dtype="float64")
    if arr.ndim == 3:
        grid = np.nanmax(arr, axis=0)
    elif arr.ndim == 2:
        grid = arr
    else:
        grid = np.squeeze(arr)
    grid = np.where(np.abs(grid) > _MF6_DRY_SENTINEL, np.nan, grid)
    return grid


def _read_head_decline_grid(
    hds_path: Path, *, invert: bool = False
) -> tuple[Any, list[float] | None]:
    """Read the head DECLINE grid head(t0) - head(t_last) + a well timeseries.

    For a transient sustainable-yield run the FIRST saved head step is the
    pre-pumping steady spin-up and the LAST is the fully-pumped state, so the
    per-cell DECLINE = head(t0) - head(t_last) is the drawdown cone (positive
    where the well drew the water table down). The max-over-layers head is used
    at each step (the water-table head). For a recharge/MOUNDING variant
    ``invert=True`` returns head(t_last) - head(t0) (the mound rise, positive
    where recharge raised the head).

    Returns ``(decline_grid_2d, head_decline_timeseries)`` where the timeseries
    is the per-step decline AT THE CELL OF PEAK FINAL DECLINE (one value per
    saved step, t0..t_last), or None when only a single step was saved.

    Raises ``PostprocessMODFLOWError("DRAWDOWN_OUTPUT_*")`` on read failure.
    """
    try:
        import flopy.utils  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "DRAWDOWN_OUTPUT_READ_FAILED",
            message=f"flopy/numpy not importable: {exc}",
            details={"hds_path": str(hds_path)},
        ) from exc

    def _to2d(data: Any) -> Any:
        a = np.asarray(data, dtype="float64")
        if a.ndim == 3:
            a2 = np.nanmax(a, axis=0)
        elif a.ndim == 2:
            a2 = a
        else:
            a2 = np.squeeze(a)
        return np.where(np.abs(a2) > _MF6_DRY_SENTINEL, np.nan, a2)

    try:
        hobj = flopy.utils.HeadFile(str(hds_path))
        times = hobj.get_times()
        if not times:
            raise PostprocessMODFLOWError(
                "DRAWDOWN_OUTPUT_EMPTY",
                message=f"{hds_path} carries no head timesteps",
                details={"hds_path": str(hds_path)},
            )
        steps = [_to2d(hobj.get_data(totim=t)) for t in times]
    except PostprocessMODFLOWError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "DRAWDOWN_OUTPUT_READ_FAILED",
            message=f"could not read head steps from {hds_path}: {exc}",
            details={"hds_path": str(hds_path)},
        ) from exc

    first, last = steps[0], steps[-1]
    decline = (last - first) if invert else (first - last)

    # Per-step decline at the cell of peak FINAL decline (the well neighbourhood).
    ts: list[float] | None = None
    if len(steps) > 1:
        finite = decline[np.isfinite(decline)]
        if finite.size:
            flat_idx = int(np.nanargmax(np.where(np.isfinite(decline), decline, -np.inf)))
            r, c = np.unravel_index(flat_idx, decline.shape)
            ts = []
            for step in steps:
                val = (step[r, c] - first[r, c]) if invert else (first[r, c] - step[r, c])
                ts.append(float(val) if np.isfinite(val) else 0.0)
    return decline, ts


def _read_concentration_steps(ucn_path: Path) -> tuple[list[Any], Any]:
    """Read ALL saved transport steps -> (per-step 2D grids, final/peak grid).

    Each step is the max-over-layers concentration; the PEAK is the final step
    (matches the existing plume). Used by the concentration-animation reader.
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

    def _to2d(data: Any) -> Any:
        a = np.asarray(data, dtype="float64")
        if a.ndim == 3:
            a2 = np.nanmax(a, axis=0)
        elif a.ndim == 2:
            a2 = a
        else:
            a2 = np.squeeze(a)
        return np.where(np.abs(a2) > _MF6_DRY_SENTINEL, np.nan, a2)

    try:
        cobj = flopy.utils.HeadFile(str(ucn_path), text="CONCENTRATION")
        times = cobj.get_times()
        if not times:
            raise PostprocessMODFLOWError(
                "PLUME_OUTPUT_EMPTY",
                message=f"{ucn_path} carries no concentration timesteps",
                details={"ucn_path": str(ucn_path)},
            )
        grids = [_to2d(cobj.get_data(totim=t)) for t in times]
    except PostprocessMODFLOWError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PostprocessMODFLOWError(
            "PLUME_OUTPUT_READ_FAILED",
            message=f"could not read concentration steps from {ucn_path}: {exc}",
            details={"ucn_path": str(ucn_path)},
        ) from exc
    return grids, grids[-1]


def _modflow_src_transform(geo: dict[str, Any] | None, nrow: int) -> Any:
    """Build the rasterio source transform from the deck georegistration."""
    import rasterio  # type: ignore[import-not-found]

    if geo is not None:
        west = geo["xorigin"]
        north = geo["yorigin"] + nrow * geo["delc"]
        return rasterio.transform.from_origin(west, north, geo["delr"], geo["delc"])
    return rasterio.Affine.identity()


def publish_modflow_quantities(
    run_outputs_uri: str,
    *,
    run_id: str,
    model_crs: str,
    register_manifest_layers: Any,
    deck_dir: str | None = None,
    runs_bucket: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> Any:
    """Publish the NEW MODFLOW quantities (concentration animation + head).

    Builds registry readers bound to the in-memory grids, then routes them
    through the shared ``publish_quantities`` executor (ONE registrar). The
    EXISTING plume + seepage layers are produced by the byte-identical old
    postprocess path; this ADDS the animation + water-table layers.

    Returns the executor's ``register_manifest_layers`` result. Never publishes
    the ``default_on=False`` provenance rows (plume-concentration / river-seepage).
    """
    from dataclasses import replace as _dc_replace

    from grace2_contracts.output_quantities import (
        RasterField,
        TimeseriesField,
        get_output_registry,
    )

    from . import publish_quantities as _pq

    geo = _grid_georegistration_from_deck(deck_dir)
    cell_area_m2 = (
        float(geo["delr"]) * float(geo["delc"]) if geo is not None else 2500.0
    )

    import numpy as np  # type: ignore[import-not-found]

    # --- concentration animation reader (all saved UCN steps) --------------- #
    ucn_path = _resolve_ucn_path(run_outputs_uri)
    conc_grids, conc_peak = _read_concentration_steps(ucn_path)
    nrow_c = int(np.asarray(conc_peak).shape[0])
    conc_transform = _modflow_src_transform(geo, nrow_c)

    def _mask_floor(a: Any) -> Any:
        import numpy as np  # type: ignore[import-not-found]

        return np.where(a > PLUME_DETECTION_FLOOR_MGL, a, np.nan).astype("float32")

    def _conc_raster(grid: Any) -> RasterField:
        max_conc, area = compute_plume_metrics(grid, cell_area_m2)
        return RasterField(
            grid=grid,
            src_crs=model_crs,
            src_transform=conc_transform,
            reproject=True,
            mask=_mask_floor,
            crs_roundtrip_guard=False,
            metrics={
                "max_concentration_mgl": max_conc,
                "plume_area_km2": area,
            },
        )

    def _conc_ts_reader(_ctx: Any) -> TimeseriesField:
        return TimeseriesField(
            n_steps=len(conc_grids),
            read_step=lambda i: _conc_raster(conc_grids[i]),
            peak=_conc_raster(conc_peak),
            quantity_label="Plume concentration",
        )

    # --- head / water-table reader (final-step .hds) ------------------------ #
    hds_path = _resolve_gwf_hds_path(run_outputs_uri)
    head_grid = np.asarray(_read_head_grid(hds_path), dtype="float64")
    nrow_h = int(head_grid.shape[0]) if head_grid.ndim == 2 else nrow_c
    head_transform = _modflow_src_transform(geo, nrow_h)

    def _head_reader(_ctx: Any) -> RasterField:
        finite = head_grid[np.isfinite(head_grid)]
        max_head = float(np.max(finite)) if finite.size else 0.0
        min_head = float(np.min(finite)) if finite.size else 0.0
        return RasterField(
            grid=head_grid,
            src_crs=model_crs,
            src_transform=head_transform,
            reproject=True,
            crs_roundtrip_guard=False,
            metrics={"max_head_m": max_head, "min_head_m": min_head},
        )

    readers = {
        "plume-concentration-ts": _conc_ts_reader,
        "water-table": _head_reader,
    }
    specs = [
        _dc_replace(spec, reader=readers[spec.quantity_id])
        for spec in get_output_registry("modflow")
        if spec.quantity_id in readers
    ]

    def _upload(cog: Path, rid: str, _bucket: Any = None, *, dest_filename: str) -> str:
        return _upload_cog(cog, rid, runs_bucket, cog_filename=dest_filename)

    return _pq.publish_quantities(
        "modflow",
        run_id=run_id,
        upload=_upload,
        register_manifest_layers=register_manifest_layers,
        specs=specs,
        bbox=bbox,
    )


# --------------------------------------------------------------------------- #
# sprint-18 Wave-1 archetype postprocess (GWF-only: head + cbc readers).
#
# Each reuses the EXISTING resolve/write/upload/publish seams above and the new
# pure metric math. drawdown reads the transient .hds head decline; dewatering
# reads the .cbc DRN term; budget-partition reads ALL .cbc terms. Every narrated
# scalar is a typed field measured from the real run output (Invariant 1).
# --------------------------------------------------------------------------- #


def postprocess_drawdown(
    run_outputs_uri: str,
    *,
    run_id: str,
    model_crs: str,
    deck_dir: str | None = None,
    runs_bucket: str | None = None,
    publish: bool = True,
    mounding: bool = False,
) -> DrawdownLayerURI:
    """Convert a transient GWF run's head into a drawdown ``DrawdownLayerURI``.

    Reads the GWF head file (``gwf_model.hds``), computes the per-cell head
    DECLINE = head(t0) - head(t_last) (the cone of depression a pumping well
    draws down), reprojects it to an EPSG:4326 COG, computes the peak drawdown
    + the at-well head-decline timeseries, uploads + (optionally) publishes the
    COG, and returns the typed drawdown layer.

    When ``mounding=True`` the sign is inverted (head(t_last) - head(t0)) so a
    recharge run renders the mound rise instead of a drawdown cone (same reader,
    inverse sign).

    Raises:
        PostprocessMODFLOWError: any read / reproject / write / upload step
            failed; ``error_code`` identifies the stage.
    """
    hds_path = _resolve_gwf_hds_path(run_outputs_uri)
    geo = _grid_georegistration_from_deck(deck_dir)

    decline, ts = _read_head_decline_grid(hds_path, invert=mounding)
    max_drawdown_m = compute_drawdown_metrics(decline)
    logger.info(
        "postprocess_drawdown run_id=%s mounding=%s max_drawdown_m=%.6g steps_ts=%s",
        run_id,
        mounding,
        max_drawdown_m,
        len(ts) if ts is not None else 0,
    )

    # The decline grid is already NaN off-grid; write AS-IS (mask_below_floor
    # False) so negative recovery cells survive (do not get floored away).
    cog_path = _write_reprojected_cog(decline, model_crs, geo, mask_below_floor=False)
    bbox_4326 = _cog_bbox_4326(cog_path)
    cog_uri = _upload_cog(
        cog_path, run_id, runs_bucket, cog_filename="drawdown_4326.tif"
    )

    name = "Recharge Mounding (head rise)" if mounding else "Pumping Drawdown (head decline)"
    layer_id = f"{'mounding' if mounding else 'drawdown'}-{run_id}"
    final_uri = cog_uri
    if publish:
        wms_url = _dispatch_publish_layer(
            cog_uri, layer_id, style_preset=DRAWDOWN_STYLE_PRESET
        )
        if wms_url:
            final_uri = wms_url

    return DrawdownLayerURI(
        layer_id=layer_id,
        name=name,
        layer_type="raster",
        uri=final_uri,
        style_preset=DRAWDOWN_STYLE_PRESET,
        role="primary",
        units="m",
        bbox=bbox_4326,
        max_drawdown_m=max_drawdown_m,
        head_decline_timeseries=ts,
    )


def postprocess_dewatering(
    run_outputs_uri: str,
    *,
    run_id: str,
    model_crs: str,
    deck_dir: str | None = None,
    runs_bucket: str | None = None,
    publish: bool = True,
    term: str = "DRN",
) -> DewaterLayerURI:
    """Convert a mine-dewatering GWF run's DRN budget into a ``DewaterLayerURI``.

    Reads the GWF cell-by-cell budget (``gwf_model.cbc``) ``term`` (default DRN)
    into a per-cell signed outflow grid, reprojects it to an EPSG:4326 COG,
    computes the total dewatering rate (sum of |q| over the drain cells) + the
    drain-cell count, uploads + (optionally) publishes the COG, and returns the
    typed dewatering layer. The DRN sum IS the pump-to-dewater rate.

    Raises:
        PostprocessMODFLOWError: any read / reproject / write / upload step
            failed; ``error_code`` identifies the stage.
    """
    cbc_path = _resolve_gwf_cbc_path(run_outputs_uri)
    geo = _grid_georegistration_from_deck(deck_dir)
    nrow = int(geo["nrow"]) if geo is not None else None
    ncol = int(geo["ncol"]) if geo is not None else None
    if nrow is None or ncol is None:
        nrow, ncol = _infer_grid_shape_from_cbc(cbc_path)

    term_grid = _read_cbc_term_grid(cbc_path, term, nrow, ncol)
    dewatering_rate_m3_day, drain_cell_count = compute_cbc_term_metrics(term_grid)
    logger.info(
        "postprocess_dewatering run_id=%s term=%s dewatering_rate_m3_day=%.6g cells=%d",
        run_id,
        term,
        dewatering_rate_m3_day,
        drain_cell_count,
    )

    # The drain outflow is negative per MF6 sign; render its MAGNITUDE so the
    # COG reads as a positive pump-to-dewater rate. Off-grid is already NaN.
    import numpy as np  # type: ignore[import-not-found]

    magnitude_grid = np.abs(np.asarray(term_grid, dtype="float64"))
    cog_path = _write_reprojected_cog(
        magnitude_grid, model_crs, geo, mask_below_floor=False
    )
    bbox_4326 = _cog_bbox_4326(cog_path)
    cog_uri = _upload_cog(
        cog_path, run_id, runs_bucket, cog_filename="dewatering_rate_4326.tif"
    )

    layer_id = f"dewatering-rate-{run_id}"
    final_uri = cog_uri
    if publish:
        wms_url = _dispatch_publish_layer(
            cog_uri, layer_id, style_preset=DEWATERING_STYLE_PRESET
        )
        if wms_url:
            final_uri = wms_url

    return DewaterLayerURI(
        layer_id=layer_id,
        name="Mine Dewatering Rate",
        layer_type="raster",
        uri=final_uri,
        style_preset=DEWATERING_STYLE_PRESET,
        role="primary",
        units="m^3/day",
        bbox=bbox_4326,
        dewatering_rate_m3_day=dewatering_rate_m3_day,
        drain_cell_count=drain_cell_count,
    )


def postprocess_budget_partition(
    run_outputs_uri: str,
    *,
    run_id: str,
    model_crs: str,
    deck_dir: str | None = None,
    runs_bucket: str | None = None,
    publish: bool = True,
) -> BudgetPartitionLayerURI:
    """Convert a regional GWF run's cbc into a ``BudgetPartitionLayerURI``.

    Reads the GWF cell-by-cell budget (``gwf_model.cbc``), sums each term's
    per-cell flux into a per-term total (signs preserved), drops FLOW-JA-FACE +
    near-zero terms, and returns the typed partition. The deliverable is the
    SCALAR budget dict; the layer is rendered as the water-table head COG so the
    user sees the regional flow field the partition summarizes (the head is the
    spatial carrier; the partition is the narrated numbers - never free-generated).

    Raises:
        PostprocessMODFLOWError: any read / reproject / write / upload step
            failed; ``error_code`` identifies the stage.
    """
    cbc_path = _resolve_gwf_cbc_path(run_outputs_uri)
    geo = _grid_georegistration_from_deck(deck_dir)

    term_totals = _read_cbc_budget_partition(cbc_path)
    partition = compute_budget_partition(term_totals)
    logger.info(
        "postprocess_budget_partition run_id=%s terms=%s",
        run_id,
        {k: round(v, 3) for k, v in partition.items()},
    )

    # Spatial carrier = the water-table head COG (continuous head ramp). Best-
    # effort: if the head file is absent the partition is still the deliverable.
    bbox_4326: tuple[float, float, float, float] | None = None
    final_uri: str
    try:
        hds_path = _resolve_gwf_hds_path(run_outputs_uri)
        head_grid = _read_head_grid(hds_path)
        cog_path = _write_reprojected_cog(
            head_grid, model_crs, geo, mask_below_floor=False
        )
        bbox_4326 = _cog_bbox_4326(cog_path)
        final_uri = _upload_cog(
            cog_path, run_id, runs_bucket, cog_filename="water_table_4326.tif"
        )
        layer_id = f"budget-partition-{run_id}"
        if publish:
            wms_url = _dispatch_publish_layer(
                final_uri, layer_id, style_preset=HEAD_STYLE_PRESET
            )
            if wms_url:
                final_uri = wms_url
    except PostprocessMODFLOWError as exc:
        logger.warning(
            "budget-partition head COG unavailable (partition still returned): %s",
            exc,
        )
        final_uri = run_outputs_uri
        layer_id = f"budget-partition-{run_id}"

    return BudgetPartitionLayerURI(
        layer_id=layer_id,
        name="Regional Water Budget (zonal partition)",
        layer_type="raster",
        uri=final_uri,
        style_preset=HEAD_STYLE_PRESET,
        role="primary",
        units="m^3/day",
        bbox=bbox_4326,
        budget_partition_m3_day=partition,
    )
