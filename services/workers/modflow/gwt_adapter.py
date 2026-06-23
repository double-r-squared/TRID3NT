"""MODFLOW 6 GWF + GWT deck construction for groundwater solute-transport.

Sprint-13 Stage 1 (MOD), job-0221. Owner: engine.

This module assembles a *complete* MODFLOW 6 simulation deck for a
groundwater-contamination ("spill") scenario via FloPy. A single MF6 binary
(`mf6`, version-pinned 6.5.0 in the solver container — see
`reports/inflight/sprint-13-mod-1-modflow-container-design-20260609/design.md`
section 2) executes *both* model types from one simulation namefile:

  * **GWF** (Groundwater Flow) — steady-state saturated flow. A west→east
    constant-head gradient drives a uniform regional flow field that advects
    the plume. This is the hydraulic head field that the transport model
    reads.
  * **GWT** (Groundwater Transport) — transient advection-dispersion of a
    conservative tracer. A mass-loading source (`SRC` package) injects the
    contaminant at the spill cell; advection (`ADV`) and dispersion (`DSP`)
    spread it; output control (`OC`) saves the concentration array.

The two models are coupled by a GWF-GWT exchange (`GWFGWT`) plus the transport
source-sink mixing package (`SSM`) so the flow field built by GWF drives
transport. Reaction kinetics (sorption, biodegradation) are intentionally
**out of scope for v0.1** — the demo contaminant is a conservative tracer
(design.md section 2).

Determinism boundary (engine invariant 1/2): this is pure deterministic
Python — NO LLM call anywhere in this module. It composes FloPy package
constructors in a fixed, tested sequence and returns a typed deck manifest
whose fields carry every number a downstream tool would narrate.

Contract note: `build_modflow_deck` takes plain keyword arguments whose names
match the `MODFLOWRunArgs` Pydantic contract (authored in parallel by
job-0222 and bound in Stage 2 / job-0227). This module deliberately does NOT
import from `grace2_contracts` — the binding happens upstream.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import flopy
from pyproj import CRS, Transformer

# ---------------------------------------------------------------------------
# Demo-scope model constants (design.md section 6 / OQ-MOD-6).
#
# These are v0.1 demo simplifications. A production groundwater model requires
# proper hydrogeologic data (aquifer top/bottom from well logs, anisotropy,
# heterogeneity, recharge). Real models supply these; here the spill scenario
# is parameterised only by (location, contaminant, release_rate, duration,
# aquifer_k, porosity) and the rest is defaulted to geologically reasonable
# demo values. Each default is surfaced explicitly here, not buried.
# ---------------------------------------------------------------------------

DOMAIN_HALF_WIDTH_M = 1000.0  # half the ~2 km square domain
CELL_SIZE_M = 50.0  # 50 m cells -> 40 x 40 structured grid
N_LAYERS = 1  # single layer acceptable for v0.1

# Aquifer geometry (a flat single layer; demo simplification per OQ-MOD-6).
AQUIFER_TOP_M = 0.0  # local datum top of the saturated layer
AQUIFER_THICKNESS_M = 30.0  # saturated thickness -> bottom = top - 30 m
AQUIFER_BOTTOM_M = AQUIFER_TOP_M - AQUIFER_THICKNESS_M

# Regional hydraulic gradient driving west->east flow. 0.002 (2 m/km) is a
# typical shallow-aquifer gradient; over the 2 km domain that is a 4 m head
# drop across the constant-head boundaries.
REGIONAL_GRADIENT = 0.002

# Dispersivity (m). Longitudinal alpha_L scaled to the plume travel length;
# 10 m is a standard intermediate-scale value. Transverse ratios per Gelhar.
LONGITUDINAL_DISPERSIVITY_M = 10.0
TRANSVERSE_HORIZONTAL_RATIO = 0.1
TRANSVERSE_VERTICAL_RATIO = 0.01

# Source concentration handling: the SRC package injects mass directly
# (units: mass/time), so we convert the contract's kg/s into MODFLOW's
# internal mass unit (grams) per day. Reported concentration is then mg/L
# when porosity-scaled pore volumes are in m^3 and mass in g (1 g/m^3 =
# 1 mg/L). The SRC `smassrate` is therefore g/day.
SECONDS_PER_DAY = 86400.0
KG_TO_G = 1000.0

# MODFLOW time unit for this deck: DAYS (TDIS time_units). All rates below are
# therefore expressed per day, and lengths in METERS.
TIME_UNITS = "DAYS"
LENGTH_UNITS = "METERS"

# ---------------------------------------------------------------------------
# River-coupling demo defaults (sprint-17 J9 river-seepage). The RIV package is
# the simplest head-dependent river<->aquifer flux boundary: per reach cell
# (cellid, stage, cond, rbot) with leakage Q = cond*(stage - h) capped at
# cond*(stage - rbot) once the aquifer head drops below the streambed bottom.
# These are v0.1 demo simplifications, narrated as demo values exactly like the
# OQ-3 aquifer K / porosity. A real model samples stage + streambed elevation
# from a DEM and derives conductance from streambed K, length and width.
# ---------------------------------------------------------------------------

#: Per-reach-cell RIV conductance (m^2/day) when the caller supplies none. The
#: spike used a flat 100 m^2/day -> 835 m^3/day leakage over 18 cells; 50 is a
#: conservative default.
DEFAULT_STREAMBED_CONDUCTANCE_M2_DAY = 50.0

#: Water depth (m) above the streambed bottom used to set stage from a sampled
#: (or default) rbot when no explicit stage is supplied: stage = rbot + depth.
DEFAULT_RIVER_STAGE_DEPTH_M = 1.5

#: When no DEM is available the demo streambed bottom (rbot) sits a little above
#: the local aquifer head so the reach is a real head-dependent boundary (not a
#: degenerate no-op). Expressed relative to AQUIFER_TOP_M (local datum).
DEFAULT_RIVER_RBOT_ABOVE_TOP_M = 0.5


@dataclass
class DeckManifest:
    """Typed description of a written MODFLOW 6 deck.

    Every field carries a number a downstream tool (postprocess /
    `run_modflow_job`, job-0227) reads — never prose. `model_crs` (an EPSG
    code, e.g. "EPSG:32617") is the key OQ-MOD-3 field the postprocess step
    needs to reproject the concentration COG back to EPSG:4326.
    """

    sim_dir: str
    sim_name: str
    gwf_name: str
    gwt_name: str
    model_crs: str  # e.g. "EPSG:32617" — projected metric CRS of the grid
    # Grid georegistration (so postprocess can build the affine transform):
    xorigin: float  # projected easting of grid lower-left corner (m)
    yorigin: float  # projected northing of grid lower-left corner (m)
    nrow: int
    ncol: int
    nlay: int
    delr: float  # column width (m)
    delc: float  # row height (m)
    # Spill cell (0-based grid indices) and its projected coordinates:
    spill_row: int
    spill_col: int
    spill_easting_m: float
    spill_northing_m: float
    spill_lat: float
    spill_lon: float
    # Source loading actually written into the SRC package:
    mass_rate_g_per_day: float
    release_rate_kg_s: float
    duration_days: float
    n_transport_steps: int
    contaminant: str
    aquifer_k_ms: float
    porosity: float
    # River-coupling (sprint-17 J9; all default to the no-river spill deck):
    river_coupled: bool = False  # True iff a RIV package was written
    river_cell_count: int = 0  # number of RIV reach cells draped onto the grid
    river_reach_len_m: float = 0.0  # cumulative reach length over the in-grid cells
    river_conductance_m2_day: float = 0.0  # per-cell RIV conductance written
    along_river_source: bool = False  # True iff the SRC was placed along the reach
    # Files written (relative to sim_dir), for manifest/upload assembly:
    files: list[str] = field(default_factory=list)

    def total_released_mass_kg(self) -> float:
        """Plausibility yardstick: release_rate_kg_s x duration in seconds."""
        return self.release_rate_kg_s * self.duration_days * SECONDS_PER_DAY


def _utm_crs_for_lonlat(lon: float, lat: float) -> CRS:
    """Pick the WGS84/UTM zone whose central meridian best fits the point.

    A projected metric CRS is mandatory: SFINCS and MODFLOW transport both run
    on a metric grid (engine domain discipline: "SFINCS runs in a projected
    (metric) CRS"). UTM keeps distortion sub-metre over a 2 km domain, far
    better than a single global projection.
    """
    zone = int(math.floor((lon + 180.0) / 6.0) % 60) + 1
    # EPSG 326xx = northern hemisphere, 327xx = southern.
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    return CRS.from_epsg(epsg)


# ---------------------------------------------------------------------------
# River-draping geometry (PURE — no flopy, no network; unit-testable on a
# synthetic grid + river). The river polyline is projected to the deck's UTM
# grid, then rasterized into the set of (row, col) cells it traverses, with the
# in-cell reach length per cell so conductance can scale with reach length.
# ---------------------------------------------------------------------------


def _easting_northing_to_cell(
    east: float,
    north: float,
    *,
    xorigin: float,
    yorigin: float,
    delr: float,
    delc: float,
    nrow: int,
    ncol: int,
) -> tuple[int, int] | None:
    """Map a projected (easting, northing) to a (row, col) grid index.

    Returns None when the point is outside the grid. Row 0 is the NORTH row
    (flopy convention: yorigin is the lower-left corner, row 0 is northernmost),
    so the row offset is measured from the grid TOP (yorigin + nrow*delc) down.
    """
    col = int((east - xorigin) // delr)
    north_top = yorigin + nrow * delc
    row = int((north_top - north) // delc)
    if row < 0 or row >= nrow or col < 0 or col >= ncol:
        return None
    return (row, col)


def _drape_polyline_onto_grid(
    vertices_en: list[tuple[float, float]],
    *,
    xorigin: float,
    yorigin: float,
    delr: float,
    delc: float,
    nrow: int,
    ncol: int,
) -> list[tuple[int, int, float]]:
    """Rasterize a projected polyline into the grid cells it traverses.

    Args:
        vertices_en: the polyline vertices as projected ``(easting, northing)``
            tuples (metres, in the deck's UTM CRS) in path order.

    Returns:
        A list of ``(row, col, reach_len_m)`` per UNIQUE cell, in first-touch
        order, where ``reach_len_m`` is the cumulative length of the polyline
        that falls inside that cell. Cells outside the grid are dropped. The
        per-cell length lets the RIV conductance scale with the reach length the
        cell carries (C = K_bed * L * W / M).

    The algorithm walks each segment in small sub-steps (a fraction of the cell
    size) and accumulates sub-step length into the cell the sub-step midpoint
    falls in. This is robust to diagonal reaches and segments shorter than a
    cell, and is fully deterministic.
    """
    if len(vertices_en) < 2:
        # A single vertex: assign it to its cell with a nominal half-cell length.
        cells: "dict[tuple[int, int], float]" = {}
        order: list[tuple[int, int]] = []
        if vertices_en:
            cell = _easting_northing_to_cell(
                vertices_en[0][0],
                vertices_en[0][1],
                xorigin=xorigin,
                yorigin=yorigin,
                delr=delr,
                delc=delc,
                nrow=nrow,
                ncol=ncol,
            )
            if cell is not None:
                cells[cell] = 0.5 * (delr + delc) / 2.0
                order.append(cell)
        return [(r, c, cells[(r, c)]) for (r, c) in order]

    # Sub-step length: a quarter of the smaller cell dimension so even diagonal
    # crossings sample each cell at least twice.
    step = min(delr, delc) / 4.0
    cells = {}
    order = []

    def _touch(row: int, col: int, length: float) -> None:
        key = (row, col)
        if key not in cells:
            cells[key] = 0.0
            order.append(key)
        cells[key] += length

    for (e0, n0), (e1, n1) in zip(vertices_en[:-1], vertices_en[1:]):
        seg_len = math.hypot(e1 - e0, n1 - n0)
        if seg_len <= 0.0:
            continue
        n_sub = max(1, int(math.ceil(seg_len / step)))
        sub_len = seg_len / n_sub
        for i in range(n_sub):
            # midpoint of sub-step i
            t = (i + 0.5) / n_sub
            em = e0 + t * (e1 - e0)
            nm = n0 + t * (n1 - n0)
            cell = _easting_northing_to_cell(
                em,
                nm,
                xorigin=xorigin,
                yorigin=yorigin,
                delr=delr,
                delc=delc,
                nrow=nrow,
                ncol=ncol,
            )
            if cell is not None:
                _touch(cell[0], cell[1], sub_len)

    return [(r, c, cells[(r, c)]) for (r, c) in order]


def build_riv_records(
    river_cells: list[tuple[int, int, float]],
    *,
    conductance_m2_day: float,
    stage_fn,
    rbot_fn,
    chd_cols: tuple[int, int] | None = None,
    ncol: int = 0,
) -> list[list]:
    """Build the MF6 RIV stress-period records from draped river cells.

    Each record is ``[(lay, row, col), stage, cond, rbot]`` (layer 0). Cells
    that fall on a CHD boundary column (the west/east constant-head columns) are
    SKIPPED — a cell cannot be both a constant-head boundary and a RIV boundary
    in this single-layer demo (the spike skips boundary columns for the same
    reason).

    Args:
        river_cells: ``(row, col, reach_len_m)`` per cell from
            ``_drape_polyline_onto_grid``.
        conductance_m2_day: the per-cell RIV conductance to write.
        stage_fn: ``(row, col) -> stage_m`` callable (deck datum metres).
        rbot_fn: ``(row, col) -> rbot_m`` callable (deck datum metres).
        chd_cols: ``(west_col, east_col)`` boundary columns to skip, or None.
        ncol: grid column count (for the default west/east boundary skip when
            chd_cols is None).

    Returns:
        The list of RIV records. Stage is clamped to be strictly above rbot so
        every written reach cell is a real head-dependent boundary.
    """
    if chd_cols is None and ncol > 0:
        chd_cols = (0, ncol - 1)
    skip = set(chd_cols) if chd_cols is not None else set()
    records: list[list] = []
    for (row, col, _len) in river_cells:
        if col in skip:
            continue
        rbot = float(rbot_fn(row, col))
        stage = float(stage_fn(row, col))
        if stage <= rbot:
            stage = rbot + DEFAULT_RIVER_STAGE_DEPTH_M
        records.append([(0, row, col), stage, float(conductance_m2_day), rbot])
    return records


def build_modflow_deck(
    spill_location_latlon: tuple[float, float],
    contaminant: str,
    release_rate_kg_s: float,
    duration_days: float,
    aquifer_k_ms: float,
    porosity: float,
    workdir: str | Path,
    *,
    sim_name: str = "mfsim",
    write: bool = True,
    # --- River-coupling (sprint-17 J9; ADDITIVE, all optional) ------------- #
    river_polyline_lonlat: list[tuple[float, float]] | None = None,
    river_stage_m: float | None = None,
    river_stage_depth_m: float | None = None,
    streambed_conductance_m2_day: float | None = None,
    river_rbot_by_cell: dict[tuple[int, int], float] | None = None,
    river_stage_by_cell: dict[tuple[int, int], float] | None = None,
    along_river_source: bool = False,
    # --- advanced-physics overrides (levers STEP 3; ADDITIVE, optional) ----- #
    advanced_physics: dict | None = None,
    save_concentration_all_steps: bool = True,
) -> DeckManifest:
    """Assemble a complete MF6 GWF+GWT spill deck and (optionally) write it.

    Build a physically meaningful minimal MODFLOW 6 simulation for a
    groundwater-contamination scenario: a steady-state groundwater flow model
    (GWF) driving a transient advection-dispersion solute-transport model
    (GWT). The deck is written to disk via FloPy and the function returns a
    typed `DeckManifest` describing it.

    Use this when:
        you need to turn spill parameters (location, contaminant, release
        rate, duration, aquifer hydraulic conductivity, porosity) into a
        runnable MODFLOW 6 input deck for the groundwater-contamination engine
        (Case 2). The caller uploads the resulting files to the cache bucket
        and submits the solver Cloud Run Job (job-0227).

    Do NOT use this for:
        surface-water / inundation flooding (use `build_sfincs_model`);
        reactive transport with sorption or biodegradation (out of scope for
        v0.1 — this builds a conservative-tracer model only); or any case
        requiring real hydrogeologic layering (this is a single-layer demo
        grid centred on the spill point).

    Args:
        spill_location_latlon: (lat, lon) of the spill, EPSG:4326 degrees. The
            structured grid is centred on this point and georegistered in the
            best-fit UTM zone.
        contaminant: contaminant name (carried into the manifest for
            narration; the transport math treats it as a conservative tracer).
        release_rate_kg_s: contaminant mass-loading rate, kilograms per
            second. Converted internally to grams/day for the MF6 `SRC`
            package; `mass_rate_g_per_day` records the written value.
        duration_days: simulated release + transport duration in days; sets
            the transient stress-period length and the number of transport
            time steps.
        aquifer_k_ms: saturated hydraulic conductivity, metres per second.
            Converted to m/day for the NPF package (MF6 length/time units are
            METERS/DAYS for this deck).
        porosity: effective porosity (0-1), used by the transport mobile-
            storage term so advective velocity = Darcy flux / porosity.
        workdir: directory to write the deck into (created if absent).
        sim_name: simulation name (default "mfsim"); the simulation namefile
            is "<sim_name>.nam".
        write: if True (default), write all input files to disk. If False,
            build the FloPy objects and return the manifest without writing
            (used by unit tests that only assert the in-memory deck shape).
        river_polyline_lonlat: an optional river polyline as ``(lon, lat)``
            vertices (EPSG:4326) to drape onto the structured grid as a RIV
            head-dependent river<->aquifer flux boundary (sprint-17 J9). When
            None the deck is the original spill-only deck (no RIV, no along-
            river source) and every river field on the manifest stays at its
            no-river default. The vertices are projected to the deck's UTM grid
            and rasterized into the grid cells they traverse.
        river_stage_m: explicit river stage (water-surface elevation, deck datum
            metres) applied to EVERY RIV reach cell. Takes precedence over
            ``river_stage_by_cell`` and DEM-derived stage.
        river_stage_depth_m: water depth (m) above the streambed bottom used to
            set stage from rbot when no explicit stage is supplied (stage = rbot
            + depth). Defaults to ``DEFAULT_RIVER_STAGE_DEPTH_M``.
        streambed_conductance_m2_day: per-reach-cell RIV conductance (m^2/day).
            Defaults to ``DEFAULT_STREAMBED_CONDUCTANCE_M2_DAY``.
        river_rbot_by_cell: optional ``{(row, col): rbot_m}`` of DEM-sampled
            streambed-bottom elevations per reach cell (the workflow samples the
            DEM and passes this; the adapter stays pure). Cells absent from the
            map fall back to a flat demo rbot above the local aquifer head.
        river_stage_by_cell: optional ``{(row, col): stage_m}`` of per-cell
            stage (e.g. rbot + depth from the DEM). Overridden by
            ``river_stage_m`` when that is given.
        along_river_source: when True the contaminant SRC mass-loading is placed
            at the RIV reach cells (the seepage source enters where the river
            leaks into the aquifer) instead of the single spill cell. Requires a
            ``river_polyline_lonlat``; ignored (with the SRC staying at the spill
            cell) when no river is supplied.

    Returns:
        DeckManifest: typed deck description (paths, grid georegistration,
        spill cell, source loading, `model_crs`). Every field is a number a
        downstream tool reads; nothing is prose-for-number.
    """
    lat, lon = float(spill_location_latlon[0]), float(spill_location_latlon[1])
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        raise ValueError(f"spill_location_latlon out of range: {(lat, lon)!r}")
    if release_rate_kg_s <= 0:
        raise ValueError(f"release_rate_kg_s must be > 0, got {release_rate_kg_s!r}")
    if duration_days <= 0:
        raise ValueError(f"duration_days must be > 0, got {duration_days!r}")
    if aquifer_k_ms <= 0:
        raise ValueError(f"aquifer_k_ms must be > 0, got {aquifer_k_ms!r}")
    if not (0.0 < porosity < 1.0):
        raise ValueError(f"porosity must be in (0,1), got {porosity!r}")

    sim_dir = Path(workdir)
    sim_dir.mkdir(parents=True, exist_ok=True)

    gwf_name = "gwf_model"
    gwt_name = "gwt_model"

    # --- Grid georegistration -------------------------------------------------
    # Project the spill point to a metric UTM zone, then build a square grid
    # centred on it. The grid lower-left corner (xorigin, yorigin) anchors the
    # affine transform the postprocess step uses to reproject the COG.
    crs = _utm_crs_for_lonlat(lon, lat)
    to_utm = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    spill_east, spill_north = to_utm.transform(lon, lat)

    ncol = int(round(2 * DOMAIN_HALF_WIDTH_M / CELL_SIZE_M))  # 40
    nrow = ncol
    delr = CELL_SIZE_M
    delc = CELL_SIZE_M

    # Lower-left corner so the spill lands at the grid centre.
    xorigin = spill_east - DOMAIN_HALF_WIDTH_M
    yorigin = spill_north - DOMAIN_HALF_WIDTH_M

    # Spill cell indices. MODFLOW rows increase from the TOP (north) downward;
    # FloPy's `yorigin` is the lower-left corner and row 0 is the northernmost.
    # Cell-centre of the spill = grid centre, so row/col are the middle cells.
    spill_col = int((spill_east - xorigin) // delc)
    # row 0 is north (top); convert from south-referenced offset:
    north_offset_m = (yorigin + nrow * delc) - spill_north
    spill_row = int(north_offset_m // delc)
    spill_row = max(0, min(nrow - 1, spill_row))
    spill_col = max(0, min(ncol - 1, spill_col))

    # Projected coordinates of the chosen spill cell centre (for manifest).
    spill_cell_east = xorigin + (spill_col + 0.5) * delc
    spill_cell_north = (yorigin + nrow * delc) - (spill_row + 0.5) * delc

    # --- Unit conversions -----------------------------------------------------
    k_m_per_day = aquifer_k_ms * SECONDS_PER_DAY
    mass_rate_g_per_day = release_rate_kg_s * KG_TO_G * SECONDS_PER_DAY

    # Transport time stepping: aim for ~daily resolution but cap step count so
    # tiny demos stay fast and long demos stay bounded.
    n_transport_steps = int(max(1, min(round(duration_days), 365)))

    # --- Simulation + time discretisation ------------------------------------
    sim = flopy.mf6.MFSimulation(
        sim_name=sim_name,
        sim_ws=str(sim_dir),
        exe_name="mf6",
        version="mf6",
    )

    # Two periods:
    #   1) steady-state spin-up (1 step) so the flow field equilibrates and the
    #      GWF model has a defined head field at transport start;
    #   2) transient transport period of `duration_days`, n_transport_steps.
    flopy.mf6.ModflowTdis(
        sim,
        time_units=TIME_UNITS,
        nper=2,
        perioddata=[
            (1.0, 1, 1.0),  # steady-state period: 1-day length, 1 step
            (float(duration_days), n_transport_steps, 1.0),  # transient
        ],
    )

    # Iterative model solution — one for flow, one for transport (separate IMS
    # is the MF6-recommended pattern for GWF+GWT so the nonlinear transport
    # solve does not destabilise the linear flow solve).
    ims_gwf = flopy.mf6.ModflowIms(
        sim,
        filename=f"{gwf_name}.ims",
        complexity="SIMPLE",
        outer_dvclose=1e-6,
        inner_dvclose=1e-6,
        linear_acceleration="CG",
    )
    ims_gwt = flopy.mf6.ModflowIms(
        sim,
        filename=f"{gwt_name}.ims",
        complexity="MODERATE",
        outer_dvclose=1e-6,
        inner_dvclose=1e-6,
        linear_acceleration="BICGSTAB",
    )

    # --- GWF (flow) model -----------------------------------------------------
    gwf = flopy.mf6.ModflowGwf(
        sim,
        modelname=gwf_name,
        model_nam_file=f"{gwf_name}.nam",
        save_flows=True,
    )
    sim.register_ims_package(ims_gwf, [gwf_name])

    dis = flopy.mf6.ModflowGwfdis(
        gwf,
        length_units=LENGTH_UNITS,
        nlay=N_LAYERS,
        nrow=nrow,
        ncol=ncol,
        delr=delr,
        delc=delc,
        top=AQUIFER_TOP_M,
        botm=AQUIFER_BOTTOM_M,
        xorigin=xorigin,
        yorigin=yorigin,
        filename=f"{gwf_name}.dis",
    )
    # Tag the model grid CRS so any FloPy-side georeferencing is correct.
    try:
        gwf.modelgrid.set_coord_info(
            xoff=xorigin, yoff=yorigin, crs=crs.to_epsg()
        )
    except Exception:  # pragma: no cover - older flopy signature fallback
        pass

    # Constant-head gradient: west column high, east column low -> west->east
    # flow. Head drop = gradient x domain width.
    domain_width_m = ncol * delr
    head_west = AQUIFER_TOP_M + REGIONAL_GRADIENT * domain_width_m
    head_east = AQUIFER_TOP_M
    flopy.mf6.ModflowGwfic(gwf, strt=head_west, filename=f"{gwf_name}.ic")
    flopy.mf6.ModflowGwfnpf(
        gwf,
        save_flows=True,
        icelltype=0,  # confined: transmissivity independent of head
        k=k_m_per_day,
        filename=f"{gwf_name}.npf",
    )

    chd_records = []
    for r in range(nrow):
        chd_records.append([(0, r, 0), head_west])  # west boundary (col 0)
        chd_records.append([(0, r, ncol - 1), head_east])  # east boundary
    flopy.mf6.ModflowGwfchd(
        gwf,
        stress_period_data={0: chd_records, 1: chd_records},
        filename=f"{gwf_name}.chd",
    )

    # --- RIV package: drape the river polyline onto the grid (sprint-17 J9) --
    # The RIV head-dependent boundary couples the river to the aquifer: per
    # reach cell leakage Q = cond*(stage - h), capped at cond*(stage - rbot)
    # once the aquifer head drops below the streambed bottom. The set of reach
    # cells and per-cell stage/rbot/conductance are derived deterministically
    # here from the projected polyline + the (DEM-sampled or demo) elevations.
    riv_records: list = []
    river_cell_count = 0
    river_reach_len_m = 0.0
    conductance = (
        float(streambed_conductance_m2_day)
        if streambed_conductance_m2_day is not None
        else DEFAULT_STREAMBED_CONDUCTANCE_M2_DAY
    )
    river_cells: list[tuple[int, int, float]] = []
    if river_polyline_lonlat:
        # Project the polyline vertices to the deck's UTM grid.
        vertices_en = [to_utm.transform(vlon, vlat) for (vlon, vlat) in river_polyline_lonlat]
        river_cells = _drape_polyline_onto_grid(
            vertices_en,
            xorigin=xorigin,
            yorigin=yorigin,
            delr=delr,
            delc=delc,
            nrow=nrow,
            ncol=ncol,
        )
        depth = (
            float(river_stage_depth_m)
            if river_stage_depth_m is not None
            else DEFAULT_RIVER_STAGE_DEPTH_M
        )
        default_rbot = AQUIFER_TOP_M + DEFAULT_RIVER_RBOT_ABOVE_TOP_M

        def _rbot_fn(row: int, col: int) -> float:
            if river_rbot_by_cell and (row, col) in river_rbot_by_cell:
                return float(river_rbot_by_cell[(row, col)])
            return default_rbot

        def _stage_fn(row: int, col: int) -> float:
            if river_stage_m is not None:
                return float(river_stage_m)
            if river_stage_by_cell and (row, col) in river_stage_by_cell:
                return float(river_stage_by_cell[(row, col)])
            return _rbot_fn(row, col) + depth

        riv_records = build_riv_records(
            river_cells,
            conductance_m2_day=conductance,
            stage_fn=_stage_fn,
            rbot_fn=_rbot_fn,
            chd_cols=(0, ncol - 1),
            ncol=ncol,
        )
        if riv_records:
            flopy.mf6.ModflowGwfriv(
                gwf,
                stress_period_data={0: riv_records, 1: riv_records},
                save_flows=True,
                filename=f"{gwf_name}.riv",
                pname="riv-0",
            )
            written_cells = {(rec[0][1], rec[0][2]) for rec in riv_records}
            river_cell_count = len(riv_records)
            river_reach_len_m = sum(
                length for (r, c, length) in river_cells if (r, c) in written_cells
            )

    river_coupled = river_cell_count > 0

    flopy.mf6.ModflowGwfoc(
        gwf,
        head_filerecord=f"{gwf_name}.hds",
        budget_filerecord=f"{gwf_name}.cbc",
        saverecord=[("HEAD", "LAST"), ("BUDGET", "LAST")],
        filename=f"{gwf_name}.oc",
    )

    # --- GWT (transport) model -----------------------------------------------
    gwt = flopy.mf6.ModflowGwt(
        sim,
        modelname=gwt_name,
        model_nam_file=f"{gwt_name}.nam",
        save_flows=True,
    )
    sim.register_ims_package(ims_gwt, [gwt_name])

    flopy.mf6.ModflowGwtdis(
        gwt,
        length_units=LENGTH_UNITS,
        nlay=N_LAYERS,
        nrow=nrow,
        ncol=ncol,
        delr=delr,
        delc=delc,
        top=AQUIFER_TOP_M,
        botm=AQUIFER_BOTTOM_M,
        xorigin=xorigin,
        yorigin=yorigin,
        filename=f"{gwt_name}.dis",
    )
    flopy.mf6.ModflowGwtic(gwt, strt=0.0, filename=f"{gwt_name}.ic")
    flopy.mf6.ModflowGwtadv(gwt, scheme="TVD", filename=f"{gwt_name}.adv")

    # --- advanced-physics overrides (levers STEP 3) -------------------------- #
    # The agent passes an ALREADY-VALIDATED resolved dict (range/type checked by
    # physics_registry.validate_and_resolve_physics("modflow", ...)). None / {}
    # => byte-identical conservative-tracer deck (every default below reproduces
    # today's exact GwtDsp / GwtMst call). The keys mirror the registry
    # deck_target pointers (GwtDsp:alh / GwtDsp:ath1 / GwtMst:distcoef /
    # GwtMst:bulk_density / GwtMst:decay).
    phys = dict(advanced_physics or {})
    alh = float(phys.get("long_dispersivity_m", LONGITUDINAL_DISPERSIVITY_M))
    if "trans_dispersivity_m" in phys:
        ath1 = float(phys["trans_dispersivity_m"])
    else:
        ath1 = LONGITUDINAL_DISPERSIVITY_M * TRANSVERSE_HORIZONTAL_RATIO
    atv = LONGITUDINAL_DISPERSIVITY_M * TRANSVERSE_VERTICAL_RATIO
    flopy.mf6.ModflowGwtdsp(
        gwt,
        alh=alh,
        ath1=ath1,
        atv=atv,
        filename=f"{gwt_name}.dsp",
    )

    # Mobile storage: porosity controls pore velocity (v = q / porosity).
    # advanced_physics may additionally enable LINEAR sorption (distcoef = Kd +
    # bulk_density => a retardation factor) and FIRST_ORDER decay -- both DEFAULT
    # OFF (a conservative tracer) so an absent key is byte-identical.
    mst_kwargs: dict = {"porosity": porosity, "filename": f"{gwt_name}.mst"}
    kd = phys.get("sorption_kd")
    if kd is not None and float(kd) > 0.0:
        mst_kwargs["sorption"] = "LINEAR"
        mst_kwargs["distcoef"] = float(kd)
        mst_kwargs["bulk_density"] = float(phys.get("bulk_density", 1600.0))
    decay = phys.get("decay_rate_per_day")
    if decay is not None and float(decay) > 0.0:
        mst_kwargs["first_order_decay"] = True
        mst_kwargs["decay"] = float(decay)
    flopy.mf6.ModflowGwtmst(gwt, **mst_kwargs)

    # Mass-loading source. SRC injects mass/time directly (g/day here)
    # regardless of local concentration — the spill-loading model. The source
    # is active ONLY in the transient transport period (period 1, 0-based), NOT
    # the steady-state flow spin-up (period 0). This keeps the released-mass
    # yardstick exact: total injected = mass_rate x duration, not mass_rate x
    # (1 spin-up day + duration). Empty list in period 0 deactivates it there.
    #
    # sprint-17 J9: when along_river_source is True AND a river was draped, the
    # source is distributed ALONG the RIV reach cells (the contaminant enters
    # where the river leaks into the aquifer — the river-seepage plume), with
    # the SAME total mass rate (split evenly across the reach cells) so the
    # released-mass yardstick is preserved. Otherwise it stays at the spill cell.
    source_along_river = bool(along_river_source and river_coupled and riv_records)
    if source_along_river:
        reach_cellids = [tuple(rec[0]) for rec in riv_records]  # [(lay,row,col), ...]
        per_cell_rate = mass_rate_g_per_day / float(len(reach_cellids))
        src_record = [[cellid, per_cell_rate] for cellid in reach_cellids]
    else:
        src_record = [[(0, spill_row, spill_col), mass_rate_g_per_day]]
    flopy.mf6.ModflowGwtsrc(
        gwt,
        stress_period_data={0: [], 1: src_record},
        filename=f"{gwt_name}.src",
    )

    # Source-sink mixing is required by GWT whenever the flow model has any
    # boundary package (CHD here). With no AUXMIXED concentrations declared,
    # inflow across the west constant-head boundary carries zero concentration
    # (clean regional recharge) — the physically correct default for a tracer
    # entering from up-gradient. An empty SSM (sources=None) is the MF6 idiom.
    flopy.mf6.ModflowGwtssm(
        gwt,
        sources=None,
        filename=f"{gwt_name}.ssm",
    )

    # levers STEP 3: save ALL transport steps (not just LAST) so the agent can
    # publish a concentration ANIMATION (plume-concentration-ts). The existing
    # final-step plume reads totim=times[-1], so saving ALL is byte-identical for
    # that quantity -- it simply ALSO keeps the intermediate steps the animation
    # needs. ``save_concentration_all_steps=False`` restores the old LAST-only OC
    # (kept as a reversible seam). BUDGET stays LAST (the seepage path reads only
    # the final RIV budget).
    conc_save = "ALL" if save_concentration_all_steps else "LAST"
    flopy.mf6.ModflowGwtoc(
        gwt,
        concentration_filerecord=f"{gwt_name}.ucn",
        budget_filerecord=f"{gwt_name}.cbc",
        saverecord=[("CONCENTRATION", conc_save), ("BUDGET", "LAST")],
        filename=f"{gwt_name}.oc",
    )

    # --- GWF-GWT exchange -----------------------------------------------------
    # Couples the flow solution to transport: GWT reads GWF cell-by-cell flows.
    flopy.mf6.ModflowGwfgwt(
        sim,
        exgtype="GWF6-GWT6",
        exgmnamea=gwf_name,
        exgmnameb=gwt_name,
        filename="gwfgwt.exg",
    )

    manifest = DeckManifest(
        sim_dir=str(sim_dir),
        sim_name=sim_name,
        gwf_name=gwf_name,
        gwt_name=gwt_name,
        model_crs=f"EPSG:{crs.to_epsg()}",
        xorigin=xorigin,
        yorigin=yorigin,
        nrow=nrow,
        ncol=ncol,
        nlay=N_LAYERS,
        delr=delr,
        delc=delc,
        spill_row=spill_row,
        spill_col=spill_col,
        spill_easting_m=spill_cell_east,
        spill_northing_m=spill_cell_north,
        spill_lat=lat,
        spill_lon=lon,
        mass_rate_g_per_day=mass_rate_g_per_day,
        release_rate_kg_s=release_rate_kg_s,
        duration_days=float(duration_days),
        n_transport_steps=n_transport_steps,
        contaminant=contaminant,
        aquifer_k_ms=aquifer_k_ms,
        porosity=porosity,
        river_coupled=river_coupled,
        river_cell_count=river_cell_count,
        river_reach_len_m=float(river_reach_len_m),
        river_conductance_m2_day=conductance if river_coupled else 0.0,
        along_river_source=source_along_river,
    )

    if write:
        sim.write_simulation()
        manifest.files = sorted(
            str(p.relative_to(sim_dir))
            for p in sim_dir.rglob("*")
            if p.is_file()
        )

    return manifest


# Convenience alias matching the design doc's `build_deck` reference
# (design.md section 9 names the function `build_deck`; the kickoff names it
# `build_modflow_deck`). Both resolve to the same implementation.
build_deck = build_modflow_deck
