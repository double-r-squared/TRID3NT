"""SFINCS model setup via HydroMT (job-0042, OQ-4 §4 — Invariant 7 mitigation).

This module implements ``build_sfincs_model(dem_uri, landcover_uri,
river_geometry_uri, forcing, bbox, options) → ModelSetup`` per the binding
contract in ``docs/decisions/oq-4-hydromt-depth.md`` §4 "Immediate (job-0042)".

The headline of this module — and the safety-critical invariant for sprint-07 —
is the **NLCD vintage validation gate**:

    HydroMT's roughness component maps landcover class integers to Manning's
    values via a user-supplied CSV mapping table. If NLCD changes class
    encoding (historically a real risk), HydroMT **silently fills unmatched
    classes with default Manning's values** (it logs a warning but does not
    raise). This is a silent-wrong-answer failure: the model runs but uses
    incorrect roughness in cells where the mapping failed. — OQ-4 §2

The gate flips this silent failure into a typed
``SFINCSSetupError("LULC_MAPPING_MISMATCH", details=…)`` that the workflow
surface lifts into a failed AssessmentEnvelope. See the matching
``manning_mapping.csv`` for the version-pinned NLCD → Manning's n table.

Decision OQ-4 §3 "Selected: Option A — Full HydroMT" (job-0038, approved):
- ``build_sfincs_model`` wraps Deltares' ``hydromt_sfincs.SfincsModel`` with a
  ``DataCatalog`` constructed from our atomic-tool ``LayerURI`` GCS paths.
- GCS bridging uses ``fsspec[gcs]`` per OQ-4 §4 contract.
- ``hydromt-sfincs >= 1.1.2, < 2.0`` is the pinned dependency; v2.0 RC has
  breaking ``SfincsModel`` API changes — defer until it exits RC.

Cross-cutting principles in force (per AGENTS.md + engine.md):

- **Invariant 1 (Determinism boundary): preserves.** No LLM in the path; the
  YAML build config is programmatically generated; given the same inputs the
  output SFINCS deck is byte-for-byte reproducible.
- **Invariant 2 (Deterministic workflows): preserves.** Pure-Python composition
  of typed atomic-tool outputs; no global state.
- **Invariant 7 (no silent wrong answers): EXTENDS — the headline.** The NLCD
  validation gate is the load-bearing mitigation OQ-4 §4 demanded. Silent
  fallback is replaced with a typed, structured ``SFINCSSetupError`` carrying
  ``error_code="LULC_MAPPING_MISMATCH"`` and the unmapped class set + vintage
  year so the agent surface can render a meaningful failure rather than
  dispatching a broken model to the solver.
- **Decision K (minimal parameter surface): preserves.** The signature exposes
  intent + irreducible inputs only (URIs of upstream atomic-tool outputs, bbox,
  options). Manning's mapping CSV vintage, grid resolution, CRS — all derived
  inside.

This module does NOT register an atomic tool. ``build_sfincs_model`` is a
workflow-internal helper called by ``model_flood_scenario`` after the fetcher
chain has run. The workflow itself is the LLM-exposed surface (via the thin
``run_model_flood_scenario`` wrapper).
"""

from __future__ import annotations

import csv
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from grace2_contracts import new_ulid
from grace2_contracts.execution import LayerURI, ModelSetup

__all__ = [
    "SFINCSSetupError",
    "ForcingSpec",
    "BuildOptions",
    "build_sfincs_model",
    "load_manning_mapping",
    "validate_nlcd_vintage_against_mapping",
    "MANNING_MAPPING_PATH",
    "MANNING_MAPPING_VERSION",
]

logger = logging.getLogger("grace2_agent.workflows.sfincs_builder")


# --------------------------------------------------------------------------- #
# Module constants — Manning's mapping CSV location + version
# --------------------------------------------------------------------------- #


#: Absolute path to the version-pinned Manning's mapping CSV.
#: Co-located with the workflows package so it travels in the agent service
#: container. The validation gate reads this file once per ``build_sfincs_model``
#: call (no module-level caching — keep the read explicit so a hot-swap is
#: trivial in tests).
MANNING_MAPPING_PATH: Path = Path(__file__).parent / "manning_mapping.csv"

#: Version string embedded in the CSV's header block. Surfaced in
#: ``ModelSetup.parameters`` for provenance.
MANNING_MAPPING_VERSION: str = "1.0.0"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class SFINCSSetupError(RuntimeError):
    """Raised by ``build_sfincs_model`` on any setup-time failure.

    The ``error_code`` is the A.6 open-set code surfaced to the WS error frame
    and threaded into the final ``AssessmentEnvelope`` when the workflow
    returns a failed envelope. ``details`` is a free-form dict with the gate
    specifics — for ``LULC_MAPPING_MISMATCH`` it carries::

        {
          "nlcd_vintage_year": int,
          "mapping_version": str,
          "unmapped_classes": list[int],
          "mapping_csv_path": str,
        }

    Open-set codes used by this module (per OQ-4 §5 OQ-4c):
    - ``LULC_MAPPING_MISMATCH`` — the **headline**; gate fired before HydroMT
      ran the roughness component.
    - ``DEM_COVERAGE_GAP`` — DEM bytes were not readable or had no spatial
      overlap with the bbox (defensive; HydroMT also catches this).
    - ``FORCING_OUT_OF_RANGE`` — forcing tuple was empty or carried no
      precipitation depth.
    - ``HYDROMT_UNAVAILABLE`` — ``import hydromt_sfincs`` failed in the
      runtime (container missing the dep — surfaces as schema-pushback to
      infra job-0040).
    - ``HYDROMT_BUILD_FAILED`` — HydroMT itself raised during the build (any
      uncaught underlying error is re-raised wrapped in this code).
    """

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
# Forcing + options surface (engine-internal)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ForcingSpec:
    """Compact specification of the design-storm forcing for SFINCS.

    For the v0.1 substrate ``model_flood_scenario`` constructs this from the
    ``lookup_precip_return_period`` atomic-tool output — a single
    precipitation depth + duration + ARI metadata. Future workflows (storm
    surge, fluvial) populate it from ``fetch_hurricane_track`` /
    ``fetch_streamflow`` instead; the shape is intentionally open enough to
    grow.

    Fields used by the v0.1 pluvial SFINCS deck:

    - ``forcing_type`` — drives the SFINCS forcing component(s) HydroMT
      configures (``"pluvial_synthetic"`` → uniform rainfall hyetograph;
      ``"storm_surge"`` → wind/pressure/water-level series; future).
    - ``precip_inches`` — total depth from Atlas 14.
    - ``duration_hours`` — design-storm duration (Atlas 14 row).
    - ``return_period_years`` — ARI (Atlas 14 column).
    - ``provenance`` — free-form dict echoed into ``ForcingSummary.parameters``
      so the AssessmentEnvelope carries the Atlas 14 volume / project_area /
      vintage strings for narration.
    """

    forcing_type: str
    precip_inches: float | None = None
    duration_hours: float | None = None
    return_period_years: int | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BuildOptions:
    """Knobs ``build_sfincs_model`` exposes for engine-internal tuning.

    The workflow caller populates these from defaults — never user-input — per
    Decision K. Surfaces:

    - ``grid_resolution_m`` — SFINCS grid spacing. Defaults to 30 m to match
      NLCD native + NFR-P-4 (≤200 km² at 30 m).
    - ``simulation_hours`` — total simulation length (storm duration + spin-up).
    - ``crs`` — projected CRS the model grid is built in. SFINCS runs in a
      projected metric CRS; we use EPSG:3857 (Web Mercator) as a generic
      default for the v0.1 smoke. A production-grade default would route to
      the appropriate UTM zone per bbox center — captured as
      OQ-42-MODEL-CRS-AUTO-UTM (TENTATIVE: EPSG:3857 for v0.1 smoke).
    - ``output_setup_uri`` — explicit override for the staged deck's gs:// URI.
      When ``None`` we derive one inside the cache bucket.
    """

    grid_resolution_m: float = 30.0
    simulation_hours: float = 24.0
    crs: str = "EPSG:3857"
    output_setup_uri: str | None = None


# --------------------------------------------------------------------------- #
# Manning's mapping loader + NLCD vintage validation gate
# --------------------------------------------------------------------------- #


def load_manning_mapping(
    csv_path: Path | str | None = None,
) -> dict[int, float]:
    """Load the version-pinned NLCD class → Manning's n mapping.

    Reads ``manning_mapping.csv`` (default: the module-local file) and returns
    a dict keyed by NLCD class integer. Comments (``#``) and empty lines are
    ignored; the CSV header row is consumed; data rows must have exactly two
    numeric columns at indices 0 (nlcd_class) and 1 (manning_n). Optional
    columns (e.g. ``description``) are tolerated.

    Args:
        csv_path: optional explicit override (tests use this to inject a fixture
            CSV with only a subset of classes); ``None`` reads
            ``MANNING_MAPPING_PATH``.

    Returns:
        ``{nlcd_class_int: manning_n_float}`` — every row in the CSV becomes
        an entry; duplicates are last-wins with a logged warning.

    Raises:
        SFINCSSetupError("MANNING_MAPPING_LOAD_FAILED", …): the CSV is missing,
            empty, or unparseable.
    """
    path = Path(csv_path) if csv_path is not None else MANNING_MAPPING_PATH
    if not path.exists():
        raise SFINCSSetupError(
            "MANNING_MAPPING_LOAD_FAILED",
            message=f"Manning's mapping CSV not found at {path}",
            details={"path": str(path)},
        )

    mapping: dict[int, float] = {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            # Skip leading comments + blank lines.
            data_lines = [
                line
                for line in fh.readlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
        if not data_lines:
            raise SFINCSSetupError(
                "MANNING_MAPPING_LOAD_FAILED",
                message=f"Manning's mapping CSV at {path} is empty after stripping comments",
                details={"path": str(path)},
            )
        reader = csv.reader(data_lines)
        header = next(reader, None)
        if header is None or not header:
            raise SFINCSSetupError(
                "MANNING_MAPPING_LOAD_FAILED",
                message=f"Manning's mapping CSV at {path} has no header row",
                details={"path": str(path)},
            )
        for row_idx, row in enumerate(reader, start=2):
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) < 2:
                continue
            try:
                cls = int(row[0].strip())
                n_val = float(row[1].strip())
            except (ValueError, IndexError):
                logger.warning(
                    "manning_mapping row %d not parseable: %r (skipped)",
                    row_idx,
                    row,
                )
                continue
            if cls in mapping:
                logger.warning(
                    "manning_mapping duplicate nlcd_class=%d at row %d "
                    "(was %.4f, now %.4f) — last-wins",
                    cls,
                    row_idx,
                    mapping[cls],
                    n_val,
                )
            mapping[cls] = n_val
    except SFINCSSetupError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SFINCSSetupError(
            "MANNING_MAPPING_LOAD_FAILED",
            message=f"Manning's mapping CSV at {path} could not be parsed: {exc}",
            details={"path": str(path)},
        ) from exc

    if not mapping:
        raise SFINCSSetupError(
            "MANNING_MAPPING_LOAD_FAILED",
            message=f"Manning's mapping CSV at {path} parsed to an empty mapping",
            details={"path": str(path)},
        )
    return mapping


def validate_nlcd_vintage_against_mapping(
    fetched_classes: set[int],
    nlcd_vintage_year: int,
    mapping: dict[int, float],
    mapping_version: str = MANNING_MAPPING_VERSION,
    mapping_csv_path: str | None = None,
) -> None:
    """The **OQ-4 §4 Invariant-7 mitigation gate**.

    Verifies that every NLCD class integer observed in the fetched landcover
    raster is present in the Manning's mapping. If any class is missing, raises
    ``SFINCSSetupError("LULC_MAPPING_MISMATCH")`` carrying the specifics so the
    workflow surface can render a failed AssessmentEnvelope rather than
    dispatching a model with HydroMT's silently-filled Manning's defaults to
    the solver.

    Args:
        fetched_classes: the set of integer class codes present in the fetched
            NLCD landcover raster. The workflow layer reads the raster
            (lazily) and extracts the unique class set; this gate is the
            verification step.
        nlcd_vintage_year: the NLCD vintage year (e.g. 2021) returned in the
            ``fetch_landcover`` sidecar.
        mapping: the loaded NLCD → Manning's mapping (from
            ``load_manning_mapping``).
        mapping_version: the CSV's pinned version string (echoed into the
            error details for provenance).
        mapping_csv_path: optional path string for diagnostics.

    Raises:
        SFINCSSetupError("LULC_MAPPING_MISMATCH"): one or more classes in
            ``fetched_classes`` is not covered by ``mapping``. The error
            carries the unmapped class list + vintage year + mapping version
            so the failure surface is fully actionable.
    """
    if not fetched_classes:
        # Defensive — an empty set shouldn't happen but isn't a mismatch per se.
        logger.warning(
            "NLCD validation gate received empty fetched_classes for vintage=%d",
            nlcd_vintage_year,
        )
        return
    # NLCD class 0 = nodata; ignore for the gate (HydroMT's mask component
    # handles nodata cells separately).
    candidate = {cls for cls in fetched_classes if cls != 0}
    unmapped = sorted(candidate - set(mapping.keys()))
    if unmapped:
        details: dict[str, Any] = {
            "nlcd_vintage_year": nlcd_vintage_year,
            "mapping_version": mapping_version,
            "unmapped_classes": unmapped,
            "fetched_classes": sorted(candidate),
            "mapped_classes": sorted(mapping.keys()),
        }
        if mapping_csv_path is not None:
            details["mapping_csv_path"] = mapping_csv_path
        raise SFINCSSetupError(
            "LULC_MAPPING_MISMATCH",
            message=(
                f"NLCD vintage {nlcd_vintage_year} contains classes "
                f"{unmapped} not covered by Manning's mapping "
                f"v{mapping_version}; HydroMT roughness would fill silently "
                "with defaults (Invariant 7 violation). Update "
                "manning_mapping.csv before SFINCS setup proceeds."
            ),
            details=details,
        )


# --------------------------------------------------------------------------- #
# Landcover-class extraction
# --------------------------------------------------------------------------- #


def _extract_unique_nlcd_classes(landcover_uri: str) -> set[int]:
    """Read the landcover raster at ``landcover_uri`` and return its unique class set.

    Uses rasterio for the read (transitively available via the agent venv;
    same dep as ``data_fetch._fetch_3dep_dem_bytes``). The raster may live on
    GCS (``gs://...``) or local disk; rasterio's ``/vsigs/`` virtual filesystem
    handles GCS transparently when ADC is configured (the deployed agent
    runtime has ADC; the dev box has it per PROJECT_STATE env-facts).

    Returns:
        Set of integer class codes present in the raster, after filtering
        out the typical nodata sentinels (-9999, 255).

    Raises:
        SFINCSSetupError("LANDCOVER_READ_FAILED"): the read or class
            extraction failed.
    """
    try:
        import rasterio  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise SFINCSSetupError(
            "LANDCOVER_READ_FAILED",
            message=f"rasterio/numpy not available for landcover class extraction: {exc}",
            details={"landcover_uri": landcover_uri},
        ) from exc

    # GCS URIs need to be rewritten to the /vsigs/ form for rasterio's
    # GDAL backend. Local paths pass through unchanged.
    if landcover_uri.startswith("gs://"):
        read_path = "/vsigs/" + landcover_uri[len("gs://") :]
    else:
        read_path = landcover_uri

    try:
        with rasterio.open(read_path) as src:
            arr = src.read(1)
            nodata = src.nodata
    except Exception as exc:  # noqa: BLE001
        raise SFINCSSetupError(
            "LANDCOVER_READ_FAILED",
            message=f"rasterio.open({landcover_uri}) failed: {exc}",
            details={"landcover_uri": landcover_uri},
        ) from exc

    try:
        unique_vals = np.unique(arr)
        classes: set[int] = set()
        for v in unique_vals.tolist():
            try:
                iv = int(v)
            except (TypeError, ValueError):
                continue
            if nodata is not None and iv == int(nodata):
                continue
            if iv in (-9999, 255):  # common GeoTIFF nodata sentinels
                continue
            classes.add(iv)
    except Exception as exc:  # noqa: BLE001
        raise SFINCSSetupError(
            "LANDCOVER_READ_FAILED",
            message=f"class extraction failed for {landcover_uri}: {exc}",
            details={"landcover_uri": landcover_uri},
        ) from exc

    return classes


# --------------------------------------------------------------------------- #
# build_sfincs_model — workflow-internal entry point
# --------------------------------------------------------------------------- #


def _write_hydromt_reclass_table_csv(
    mapping: dict[int, float],
    out_path: Path,
) -> Path:
    """Write a reclass-table CSV in the **hydromt-sfincs 1.2.x** expected format.

    OQ-52 hotfix (job-0053). ``_parse_datasets_rgh`` reads the reclass table
    via ``data_catalog.get_dataframe(reclass_table, index_col=0)`` then
    indexes ``df_map[["N"]]`` — i.e. the first column must be the LULC class
    integer (used as the index), and there must be a column literally named
    ``N`` carrying the Manning's roughness value. Our authored
    ``manning_mapping.csv`` uses ``nlcd_class,manning_n,description`` columns
    (load-bearing for ``load_manning_mapping`` + the OQ-4 §4 validation gate);
    here we rewrite the in-memory mapping into the v1.2.x-shaped CSV that
    HydroMT will actually consume during ``setup_manning_roughness``.

    Args:
        mapping: ``{nlcd_class_int: manning_n_float}`` as loaded by
            ``load_manning_mapping`` (the substrate-version-pinned set).
        out_path: destination path inside the per-build temp dir.

    Returns:
        ``out_path`` for convenience.
    """
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        # First column is the LULC class integer (index_col=0); the ``N``
        # column is what HydroMT's reclassify call picks up.
        writer.writerow(["nlcd_class", "N"])
        for cls in sorted(mapping.keys()):
            writer.writerow([cls, mapping[cls]])
    return out_path


def _default_setup_uri(bbox: tuple[float, float, float, float]) -> str:
    """Compose a default gs:// URI for the staged SFINCS deck.

    Lives under the cache bucket's ``static-30d/sfincs_setup/`` source-class
    prefix (a new dispatch class for staged decks). Per-deck uniqueness is
    enforced via a ULID; HydroMT-determinism would let us cache by content
    hash but the v0.1 smoke skips that.
    """
    cache_bucket = os.environ.get("GRACE2_CACHE_BUCKET", "grace-2-hazard-prod-cache")
    setup_id = new_ulid()
    return f"gs://{cache_bucket}/cache/static-30d/sfincs_setup/{setup_id}/"


def _generate_hydromt_yaml_config(
    *,
    bbox: tuple[float, float, float, float],
    options: BuildOptions,
    dem_local_path: str,
    landcover_local_path: str,
    river_local_path: str | None,
    forcing: ForcingSpec,
    mapping_csv_path: str,
) -> str:
    """Compose a HydroMT-SFINCS YAML build config string.

    Per OQ-4 §3 + §4: the YAML drives ``hydromt build sfincs`` (or the
    equivalent Python API call). Generated programmatically from the typed
    inputs — never user-input.

    The component list is the v0.1 pluvial-flood capstone shape, with every
    step matched to a hydromt-sfincs 1.2.2 live ``inspect.signature`` cite
    (job-0054 comprehensive migration audit):

      * setup_config — config-file passthrough (``SfincsModel.setup_config``
        takes ``**cfdict`` per inheritance from ``hydromt.Model``). Time
        values (``tref``, ``tstart``, ``tstop``) MUST be in SFINCS format
        ``YYYYMMDD HHMMSS`` (e.g. ``"20260101 000000"``), NOT ISO 8601 —
        ``sfincs_input.py`` parses them via
        ``datetime.strptime(val, "%Y%m%d %H%M%S")``, and
        ``utils.parse_datetime`` uses the same format. ISO 8601 strings
        raise ``ValueError: time data '...' does not match format '%Y%m%d
        %H%M%S'`` inside ``setup_precip_forcing → get_model_time()``.
        (Discovered and fixed in job-0055.)
      * setup_grid_from_region — defines the SFINCS grid. Live sig:
        ``(region: dict, res: float = 100, crs: Union[str, int] = "utm", ...)``.
        We pass ``region: {bbox: [...]}`` + ``res``; ``crs`` left at the
        ``"utm"`` default so HydroMT picks the appropriate UTM zone for the
        bbox (Decision K: minimal parameter surface, derive inside).
      * setup_dep — DEM/topobathy ingest. Live sig:
        ``(datasets_dep: List[dict], buffer_cells: int = 0, interp_method:
        str = "linear")``. We pass ``datasets_dep: [{elevtn: <path>}]``.
      * setup_mask_active — active-cell mask. Live sig accepts ``zmin`` +
        ``zmax`` as keyword args; we pass both.
      * setup_manning_roughness — Manning's grid via NLCD + the reclass CSV.
        Live sig: ``(datasets_rgh: List[dict] = [], manning_land=0.04,
        manning_sea=0.02, rgh_lev_land=0)`` — NO top-level ``map_fn``
        (OQ-52). The reclass table lives INSIDE each ``datasets_rgh`` entry
        under key ``reclass_table`` (per ``_parse_datasets_rgh``: each dict
        supports ``manning`` (gridded n) OR ``lulc`` + ``reclass_table``);
        the CSV must be ``index_col=0`` + column literally ``N`` —
        ``_write_hydromt_reclass_table_csv`` materializes that view.
      * setup_river_inflow — NOT EMITTED for v0.1 pluvial deck. The v0.1
        M5 demo is pluvial-only (Atlas 14 design storm, no river forcing
        required). Additionally, hydromt-sfincs 1.2.2's ``set_forcing_1d``
        (sfincs.py:1858) calls ``pd.RangeIndex.is_integer()`` which was
        removed in pandas ≥ 2.0 (we run 3.0.3); this upstream library bug
        is exercised by the river-inflow discharge-point path. Dropping this
        block bypasses ``set_forcing_1d`` entirely and lets the chain reach
        solver dispatch (job-0055, OQ-54 routing recommendation b).
        The ``river_local_path`` parameter is RETAINED in the function
        signature (so call sites still pass the cached FGB without change);
        re-enabling river inflow for v0.2+ (real ATCF storm surge) requires
        adding this block back AND either pinning pandas < 2.0 or applying
        the upstream hydromt-sfincs fix (``pd.api.types.is_integer_dtype``).
      * setup_precip_forcing — uniform precip forcing. Live sig:
        ``(timeseries=None, magnitude=None)`` — accepts EITHER a tabulated
        timeseries CSV OR a single ``magnitude`` float in ``mm/hr``
        (constant rate over the simulation window, then projected onto a
        10-minute time grid). OQ-54 fix: we previously emitted ``precip``
        + ``duration_hr`` (neither is a 1.2.x parameter); we now emit
        ``magnitude: <mm_per_hr>`` derived from Atlas 14 depth ÷ duration.
        The Atlas 14 depth + duration are still echoed via the inline YAML
        comment so the provenance trail survives.

    Returns the YAML as a string. Test code parses it back; the production
    runtime writes it to a temp file and points HydroMT at it.
    """
    crs = options.crs
    grid_res = options.grid_resolution_m
    components: list[str] = []
    components.append("setup_config:")
    components.append(f"  crs: {crs}")
    # Time values MUST be in SFINCS format "YYYYMMDD HHMMSS" — sfincs_input.py
    # parses them with strptime(val, "%Y%m%d %H%M%S"). ISO 8601 format raises
    # ValueError inside setup_precip_forcing -> get_model_time() (job-0055).
    sim_days = max(1, int(options.simulation_hours / 24))
    tstop_day = 1 + sim_days
    components.append(f'  tref: "20260101 000000"')
    components.append(f'  tstart: "20260101 000000"')
    components.append(f'  tstop: "202601{tstop_day:02d} 000000"')
    components.append("setup_grid_from_region:")
    components.append(
        f"  region: {{ bbox: [{bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]}] }}"
    )
    components.append(f"  res: {grid_res}")
    components.append("setup_dep:")
    components.append(f"  datasets_dep: [{{ elevtn: '{dem_local_path}' }}]")
    components.append("setup_mask_active:")
    components.append("  zmin: -10.0")
    components.append("  zmax: 10.0")
    components.append("setup_manning_roughness:")
    components.append(
        f"  datasets_rgh: [{{ lulc: '{landcover_local_path}', "
        f"reclass_table: '{mapping_csv_path}' }}]"
    )
    # v0.1 SCOPE DECISION (job-0055, OQ-54 routing recommendation b):
    # ``setup_river_inflow`` is intentionally NOT emitted for the v0.1 pluvial
    # deck. Two reasons:
    #   1. Scope: the v0.1 M5 demo is pluvial-only (Atlas 14 design storm);
    #      river inflow is M5+ / sprint-9+ scope (real ATCF + storm surge).
    #   2. Upstream bug: hydromt-sfincs 1.2.2's ``set_forcing_1d``
    #      (sfincs.py:1858) calls ``pd.RangeIndex.is_integer()`` which was
    #      removed in pandas ≥ 2.0 (we run pandas 3.0.3). This bug is
    #      exercised by the river-inflow discharge-point path — dropping this
    #      block bypasses ``set_forcing_1d`` entirely and lets the chain
    #      proceed to solver dispatch without triggering the upstream defect.
    # ``river_local_path`` is kept in the function signature so call sites
    # continue to pass the cached NHDPlus HR FlatGeobuf unchanged; the FGB
    # is fetched and cached for future use but not wired into this deck.
    # To re-enable for v0.2+ (real ATCF + river inflow): add the block back
    # AND pin pandas < 2.0 OR apply the upstream patch
    # (``pd.api.types.is_integer_dtype(idx)`` instead of ``idx.is_integer()``).
    if forcing.forcing_type == "pluvial_synthetic" and forcing.precip_inches is not None:
        # OQ-54 fix (job-0054): the live 1.2.x signature is
        # ``setup_precip_forcing(timeseries=None, magnitude=None)``; ``precip``
        # / ``duration_hr`` (what we previously emitted) are NOT accepted
        # kwargs and would raise ``TypeError: got an unexpected keyword
        # argument``. We convert Atlas 14 (depth in inches over duration
        # hours) to a constant rate in mm/hr and pass ``magnitude``:
        #
        #     magnitude = precip_inches * 25.4 / duration_hours    [mm/hr]
        #
        # SFINCS receives this as a uniform precipitation hyetograph (the
        # source builds a 10-minute time grid from ``get_model_time()`` and
        # fills with ``magnitude``).
        duration_hr = forcing.duration_hours or 24.0
        magnitude_mm_per_hr = (forcing.precip_inches * 25.4) / duration_hr
        components.append("setup_precip_forcing:")
        components.append(
            f"  magnitude: {magnitude_mm_per_hr}  # mm/hr "
            f"(Atlas 14: {forcing.precip_inches} in over "
            f"{duration_hr} hr → {magnitude_mm_per_hr:.4f} mm/hr)"
        )
    return "\n".join(components)


def build_sfincs_model(
    dem_uri: str,
    landcover_uri: str,
    river_geometry_uri: str | None,
    forcing: ForcingSpec,
    bbox: tuple[float, float, float, float],
    options: BuildOptions | None = None,
    nlcd_vintage_year: int | None = None,
    manning_mapping_csv: Path | str | None = None,
) -> ModelSetup:
    """Build an SFINCS model deck via HydroMT, returning a typed ``ModelSetup``.

    This is the **workflow-internal** model-setup entry point. It is NOT
    registered as an atomic tool — workflows compose it directly inside
    ``model_flood_scenario``.

    Pre-HydroMT invariant gate (the headline):

        Before ``hydromt_sfincs.SfincsModel.setup_manning_roughness`` runs,
        ``validate_nlcd_vintage_against_mapping`` checks that every NLCD class
        integer present in the fetched landcover raster is covered by the
        version-pinned ``manning_mapping.csv``. If not, raises
        ``SFINCSSetupError("LULC_MAPPING_MISMATCH")`` with the unmapped class
        set and vintage year — Invariant 7 (no silent wrong answers).

    Args:
        dem_uri: ``gs://...`` (or local path) URI of the DEM COG produced by
            ``fetch_dem``.
        landcover_uri: ``gs://...`` (or local path) URI of the NLCD GeoTIFF
            produced by ``fetch_landcover``.
        river_geometry_uri: ``gs://...`` (or local path) URI of the NHDPlus HR
            FlatGeobuf produced by ``fetch_river_geometry``; may be ``None``
            for fluvial-irrelevant workflows.
        forcing: ``ForcingSpec`` carrying the SFINCS forcing
            (``precip_inches`` + ``duration_hours`` + ``return_period_years``
            for the v0.1 pluvial path).
        bbox: ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326 — the
            domain the SFINCS grid is built over.
        options: ``BuildOptions`` knobs (grid resolution, simulation hours,
            CRS, output URI override). ``None`` → ``BuildOptions()`` defaults.
        nlcd_vintage_year: the NLCD vintage year from the
            ``fetch_landcover`` sidecar. **Load-bearing** — the validation
            gate is keyed against this. ``None`` is accepted only when
            ``landcover_uri`` is a local fixture in tests; production callers
            always thread the sidecar through.
        manning_mapping_csv: optional override for the Manning's CSV path
            (tests use this to inject a subset CSV that triggers the gate).

    Returns:
        ``ModelSetup{setup_id, solver="sfincs", setup_uri, grid_resolution_m,
        bbox, parameters: {...}, created_at}`` — the staged deck URI is what
        the workflow hands to ``run_solver``.

    Raises:
        SFINCSSetupError("LULC_MAPPING_MISMATCH"): the gate fired.
        SFINCSSetupError("LANDCOVER_READ_FAILED"): the landcover raster could
            not be read.
        SFINCSSetupError("MANNING_MAPPING_LOAD_FAILED"): the mapping CSV could
            not be loaded.
        SFINCSSetupError("HYDROMT_UNAVAILABLE"): ``hydromt_sfincs`` not
            importable in the runtime.
        SFINCSSetupError("HYDROMT_BUILD_FAILED"): HydroMT raised during build.
        SFINCSSetupError("FORCING_OUT_OF_RANGE"): forcing has no precip depth.
    """
    opts = options or BuildOptions()

    # --- Forcing sanity ---
    if (
        forcing.forcing_type == "pluvial_synthetic"
        and (forcing.precip_inches is None or forcing.precip_inches <= 0)
    ):
        raise SFINCSSetupError(
            "FORCING_OUT_OF_RANGE",
            message=(
                f"pluvial forcing requires positive precip_inches; "
                f"got {forcing.precip_inches!r}"
            ),
            details={"forcing": forcing.__dict__},
        )

    # --- Step 1: Manning's mapping load ---
    mapping = load_manning_mapping(manning_mapping_csv)
    mapping_path = str(
        Path(manning_mapping_csv) if manning_mapping_csv else MANNING_MAPPING_PATH
    )
    logger.info(
        "manning_mapping loaded version=%s classes=%d path=%s",
        MANNING_MAPPING_VERSION,
        len(mapping),
        mapping_path,
    )

    # --- Step 2: extract fetched NLCD class set + validate (the headline gate) ---
    fetched_classes = _extract_unique_nlcd_classes(landcover_uri)
    logger.info(
        "landcover classes observed: %s (vintage_year=%s)",
        sorted(fetched_classes),
        nlcd_vintage_year,
    )
    if nlcd_vintage_year is not None:
        validate_nlcd_vintage_against_mapping(
            fetched_classes=fetched_classes,
            nlcd_vintage_year=nlcd_vintage_year,
            mapping=mapping,
            mapping_version=MANNING_MAPPING_VERSION,
            mapping_csv_path=mapping_path,
        )
    else:
        logger.warning(
            "build_sfincs_model called with nlcd_vintage_year=None — "
            "the OQ-4 §4 validation gate cannot run. Production callers MUST "
            "thread the fetch_landcover sidecar through."
        )

    # --- Step 3: invoke HydroMT-SFINCS ---
    # Lazy import so test environments without HydroMT installed can still
    # import this module and exercise the validation gate.
    try:
        from hydromt_sfincs import SfincsModel  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise SFINCSSetupError(
            "HYDROMT_UNAVAILABLE",
            message=(
                f"hydromt_sfincs not importable: {exc}; agent runtime must "
                "include the dep (job-0040 SFINCS container bundles it per "
                "OQ-4 §4)."
            ),
            details={"import_error": str(exc)},
        ) from exc

    setup_uri = opts.output_setup_uri or _default_setup_uri(bbox)

    # YAML config + HydroMT invocation. We build inside a temp dir, then
    # upload the staged deck via fsspec[gcs]. The hand-off to run_solver is
    # the gs:// URI of the deck root.
    with tempfile.TemporaryDirectory(prefix="sfincs-build-") as tmpdir:
        tmp = Path(tmpdir)
        # OQ-52 hotfix (job-0053): the authored ``manning_mapping.csv`` uses
        # our column names (``nlcd_class,manning_n,description``) that are
        # load-bearing for ``load_manning_mapping`` + the OQ-4 §4 validation
        # gate. hydromt-sfincs 1.2.x expects ``index_col=0`` + a column
        # literally named ``N``. Write a v1.2.x-shaped reclass table into the
        # build's temp dir and point the YAML config's ``reclass_table`` key
        # at it — the original substrate file is unchanged.
        reclass_csv_path = _write_hydromt_reclass_table_csv(
            mapping, tmp / "manning_reclass.csv"
        )
        yaml_path = tmp / "sfincs_build.yml"
        yaml_text = _generate_hydromt_yaml_config(
            bbox=bbox,
            options=opts,
            dem_local_path=dem_uri,  # SfincsModel handles gs:// via fsspec[gcs]
            landcover_local_path=landcover_uri,
            river_local_path=river_geometry_uri,
            forcing=forcing,
            mapping_csv_path=str(reclass_csv_path),
        )
        yaml_path.write_text(yaml_text, encoding="utf-8")
        try:
            # OQ-49 hotfix (job-0052): hydromt-sfincs 1.2.x expects ``opt`` as a
            # parsed ``Dict[str, Dict[str, Any]]`` (step-name → step-kwargs), NOT
            # a raw YAML text string. Passing the unparsed string raises
            # ``'str' object has no attribute 'keys'`` deep inside HydroMT's
            # ``_parse_steps``. Parse with ``yaml.safe_load`` here so the dict
            # the v1.x API documents is what reaches ``SfincsModel.build``.
            # Malformed YAML at this seam surfaces as ``HYDROMT_BUILD_FAILED``
            # via the broad except below (FR-FR-2 substrate-integrity routing).
            opt_dict = yaml.safe_load(yaml_text)
            model = SfincsModel(root=str(tmp / "deck"), mode="w")
            # ``SfincsModel.build`` is the v1.x entrypoint; v2 RC changes to
            # component-based, hence the OQ-4 §4 pin to < 2.0.
            model.build(opt=opt_dict)
            model.write()
        except SFINCSSetupError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SFINCSSetupError(
                "HYDROMT_BUILD_FAILED",
                message=f"HydroMT SfincsModel build failed: {exc}",
                details={
                    "bbox": list(bbox),
                    "dem_uri": dem_uri,
                    "landcover_uri": landcover_uri,
                    "river_geometry_uri": river_geometry_uri,
                    "underlying": str(exc),
                },
            ) from exc

        # Upload staged deck to GCS via fsspec[gcs]. Best-effort: fall back to
        # leaving the deck at a local URI if GCS unavailable (smoke run still
        # produces a typed ModelSetup the workflow can hand to run_solver,
        # which will surface its own dispatch error if the URI isn't gs://).
        try:
            import fsspec  # type: ignore[import-not-found]

            fs = fsspec.filesystem("gcs")
            fs.upload(str(tmp / "deck"), setup_uri, recursive=True)
            logger.info("uploaded SFINCS deck to %s", setup_uri)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "fsspec gcs upload failed (%s); using local URI %s",
                exc,
                tmp / "deck",
            )
            setup_uri = f"file://{tmp / 'deck'}"

    return ModelSetup(
        setup_id=new_ulid(),
        solver="sfincs",
        setup_uri=setup_uri,
        grid_resolution_m=opts.grid_resolution_m,
        bbox=bbox,
        parameters={
            "crs": opts.crs,
            "simulation_hours": opts.simulation_hours,
            "manning_mapping_version": MANNING_MAPPING_VERSION,
            "manning_mapping_path": mapping_path,
            "nlcd_vintage_year": nlcd_vintage_year,
            "fetched_classes": sorted(fetched_classes),
            "forcing_type": forcing.forcing_type,
            "forcing_provenance": dict(forcing.provenance),
            "river_geometry_uri": river_geometry_uri,
        },
        created_at=datetime.now(timezone.utc),
    )
