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


# ---------------------------------------------------------------------------
# Archetype demo defaults (sprint-18 Wave-1). The three new MODFLOW archetypes
# (sustainable_yield / mine_dewatering / regional_water_budget) reuse the same
# 40x40x50 m UTM grid + west->east REGIONAL_GRADIENT CHD as the spill/seepage
# deck; only the stress packages + temporal mode differ. Each default is a v0.1
# demo simplification, narrated as a demo value exactly like the OQ-3 aquifer K /
# porosity and the J9 RIV streambed defaults. A real model supplies these.
# ---------------------------------------------------------------------------

#: Specific yield (drainable porosity) for the GwfSto transient storage term when
#: the caller supplies none. Sandy-aquifer demo value.
DEFAULT_AQUIFER_SY = 0.2

#: Specific storage (1/m) for the GwfSto transient storage term. Confined-aquifer
#: demo value.
DEFAULT_AQUIFER_SS = 1e-5

#: Number of transient stress periods for a transient archetype when neither
#: ``sim_years`` nor ``n_periods`` is supplied (e.g. four seasonal periods).
DEFAULT_N_TRANSIENT_PERIODS = 4

#: Length (days) of each transient stress period when derived from a period count
#: rather than ``sim_years`` (a 90-day season per period -> ~1 year for 4).
DEFAULT_TRANSIENT_PERIOD_DAYS = 90.0

#: Time steps per transient stress period (sub-stepping for a stable transient
#: solve + a few saved frames per period).
DEFAULT_STEPS_PER_TRANSIENT_PERIOD = 10

#: Per-cell DRN conductance (m^2/day) for the mine-dewatering pit drain ring when
#: the caller supplies none. High enough that the drain holds the pit head near
#: the drain elevation (the dewatered target).
DEFAULT_DRAIN_CONDUCTANCE_M2_DAY = 100.0

#: When the caller gives no drain elevation, dewater the pit to this depth below
#: the local aquifer datum (AQUIFER_TOP_M) so the DRN actively removes water.
DEFAULT_DRAIN_DEPTH_BELOW_TOP_M = 10.0


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
    # --- Archetype branch (sprint-18 Wave-1; ADDITIVE, default = spill/seepage) -
    # ``archetype is None`` is the EXISTING spill/seepage GWF+GWT deck; the three
    # new archetypes are GWF-only (no GWT block, no GWFGWT exchange). Every field
    # below stays at its default for the spill/seepage path (byte-identical deck).
    archetype: str | None = None  # None | sustainable_yield | mine_dewatering | regional_water_budget
    gwt_present: bool = True  # True iff a GWT (transport) model was written
    transient: bool = False  # True iff a transient TDIS + GwfSto were written
    n_stress_periods: int = 2  # TDIS period count (spill/seepage = 2: steady + transient)
    n_transient_periods: int = 1  # transient (non-spin-up) period count
    # sustainable_yield (WEL pumping-well drawdown):
    well_row: int = -1  # 0-based grid row of the pumping well (-1 = no well)
    well_col: int = -1  # 0-based grid col of the pumping well (-1 = no well)
    well_easting_m: float = 0.0  # projected easting of the well cell centre (m)
    well_northing_m: float = 0.0  # projected northing of the well cell centre (m)
    well_lat: float = 0.0  # well latitude (EPSG:4326)
    well_lon: float = 0.0  # well longitude (EPSG:4326)
    pumping_rate_m3_day: float = 0.0  # WEL discharge written (negative = extraction)
    aquifer_sy: float = 0.0  # GwfSto specific yield written (0.0 = no STO)
    aquifer_ss: float = 0.0  # GwfSto specific storage written (1/m)
    # mine_dewatering (DRN pit dewatering):
    drain_cell_count: int = 0  # number of DRN drain cells draped over the pit
    drain_elevation_m: float = 0.0  # DRN drain elevation written (deck datum m)
    drain_conductance_m2_day: float = 0.0  # per-cell DRN conductance written
    npf_icelltype: int = 0  # NPF icelltype (0 = confined; 1 = unconfined water table)
    # regional_water_budget (zonal CBC partition):
    zone_partition: str | None = None  # zone-split scheme written (None = whole-domain)
    n_zones: int = 0  # number of zones in the written ZONE array (0 = none)
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


def _resolve_transient_periods(
    *,
    sim_years: float | None,
    n_periods: int | None,
) -> list[tuple[float, int, float]]:
    """Resolve the transient stress-period schedule for a transient archetype.

    Returns the per-period ``(perlen_days, nstp, tsmult)`` rows for the TRANSIENT
    periods ONLY (the steady-state spin-up period 0 is prepended by
    ``_add_transient_sto_tdis``). The schedule is derived, in priority order:

      1. ``sim_years`` set -> ``n_periods`` (or the demo default count) equal
         periods spanning ``sim_years`` years (365 days/year).
      2. ``n_periods`` set (no sim_years) -> that many ``DEFAULT_TRANSIENT_PERIOD_DAYS``
         seasonal periods.
      3. neither -> ``DEFAULT_N_TRANSIENT_PERIODS`` seasonal periods.

    Every period uses ``DEFAULT_STEPS_PER_TRANSIENT_PERIOD`` time steps (a single
    tsmult of 1.0). Pure + deterministic (no flopy, no I/O).
    """
    if sim_years is not None and sim_years > 0:
        nper = int(n_periods) if (n_periods and n_periods >= 1) else DEFAULT_N_TRANSIENT_PERIODS
        total_days = float(sim_years) * 365.0
        perlen = total_days / nper
    elif n_periods is not None and n_periods >= 1:
        nper = int(n_periods)
        perlen = DEFAULT_TRANSIENT_PERIOD_DAYS
    else:
        nper = DEFAULT_N_TRANSIENT_PERIODS
        perlen = DEFAULT_TRANSIENT_PERIOD_DAYS
    return [(float(perlen), DEFAULT_STEPS_PER_TRANSIENT_PERIOD, 1.0) for _ in range(nper)]


def _add_transient_sto_tdis(
    sim,
    gwf,
    *,
    transient_periods: list[tuple[float, int, float]],
    sy: float,
    ss: float,
    gwf_name: str,
    iconvert: int = 0,
    spinup_perlen_days: float = 1.0,
) -> int:
    """Add a transient TDIS (steady spin-up + N transient periods) + a GwfSto.

    The transient archetypes (sustainable_yield, and any future transient GWF-only
    archetype) reuse this so the temporal mode + storage term are written in ONE
    tested place. The schedule is: period 0 = a single-step STEADY-state spin-up
    so the head field equilibrates before any transient stress, then the supplied
    ``transient_periods`` as transient periods.

    The ``ModflowGwfsto`` declares ``steady_state={0: True}`` and
    ``transient={i: True}`` for every transient period i (1..N), with ``iconvert``
    (0 = confined storage, 1 = convertible water-table storage), specific yield
    ``sy`` and specific storage ``ss``. STEADY archetypes (mine_dewatering) do NOT
    call this -- they keep the single steady period + no STO.

    Returns the total TDIS stress-period count (1 spin-up + len(transient_periods)).
    """
    perioddata: list[tuple[float, int, float]] = [
        (float(spinup_perlen_days), 1, 1.0),  # steady-state spin-up
    ]
    perioddata.extend(transient_periods)
    nper = len(perioddata)
    flopy.mf6.ModflowTdis(
        sim,
        time_units=TIME_UNITS,
        nper=nper,
        perioddata=perioddata,
    )
    transient_map = {i: True for i in range(1, nper)}  # periods 1..N are transient
    flopy.mf6.ModflowGwfsto(
        gwf,
        iconvert=iconvert,
        ss=ss,
        sy=sy,
        steady_state={0: True},
        transient=transient_map,
        save_flows=True,
        filename=f"{gwf_name}.sto",
    )
    return nper


def _build_gwf_only_archetype_deck(
    *,
    archetype: str,
    lat: float,
    lon: float,
    crs,
    to_utm,
    xorigin: float,
    yorigin: float,
    nrow: int,
    ncol: int,
    delr: float,
    delc: float,
    k_m_per_day: float,
    aquifer_k_ms: float,
    porosity: float,
    sim_dir: Path,
    sim_name: str,
    gwf_name: str,
    write: bool,
    # sustainable_yield
    well_location_latlon: tuple[float, float] | None,
    pumping_rate_m3_day: float | None,
    aquifer_sy: float | None,
    aquifer_ss: float | None,
    sim_years: float | None,
    n_periods: int | None,
    # mine_dewatering
    pit_footprint_lonlat: list[tuple[float, float]] | None,
    drain_elevation_m: float | None,
    drain_conductance_m2_day: float | None,
    well_pumping_rate_m3_day: float | None,
    # regional_water_budget
    zone_partition: str | None,
) -> DeckManifest:
    """Assemble a GWF-ONLY archetype deck (no GWT block, no GWFGWT exchange).

    Shared GWF scaffold for the three sprint-18 Wave-1 archetypes. The grid,
    west->east REGIONAL_GRADIENT CHD, IC and OC (HEAD + BUDGET, ALL) are identical
    across them; only the temporal mode + the stress packages differ:

      * ``sustainable_yield``  -> transient (STO via ``_add_transient_sto_tdis``) +
        a sustained WEL extraction well. Headline = the drawdown cone (.hds).
      * ``mine_dewatering``    -> STEADY, NPF icelltype=1 (unconfined water table) +
        a DRN ring over the pit footprint (+ optional sump WEL). Headline = the DRN
        budget term (the pump-to-dewater rate).
      * ``regional_water_budget`` -> STEADY GWF, NO new stress package; the
        deliverable is the CBC budget partition (read agent-side). An optional ZONE
        array is written when ``zone_partition`` is set.

    OC saves HEAD + BUDGET ALL so the agent-side phase can read the .hds drawdown
    and the .cbc DRN/CHD/WEL/STO budget terms.
    """
    sim = flopy.mf6.MFSimulation(
        sim_name=sim_name,
        sim_ws=str(sim_dir),
        exe_name="mf6",
        version="mf6",
    )

    gwf = flopy.mf6.ModflowGwf(
        sim,
        modelname=gwf_name,
        model_nam_file=f"{gwf_name}.nam",
        save_flows=True,
    )
    ims_gwf = flopy.mf6.ModflowIms(
        sim,
        filename=f"{gwf_name}.ims",
        complexity="MODERATE",
        outer_dvclose=1e-6,
        inner_dvclose=1e-6,
        linear_acceleration="BICGSTAB",
    )
    sim.register_ims_package(ims_gwf, [gwf_name])

    # --- Temporal mode + storage --------------------------------------------- #
    transient = archetype == "sustainable_yield"
    sy = float(aquifer_sy) if aquifer_sy is not None else DEFAULT_AQUIFER_SY
    ss = float(aquifer_ss) if aquifer_ss is not None else DEFAULT_AQUIFER_SS
    if transient:
        transient_periods = _resolve_transient_periods(
            sim_years=sim_years, n_periods=n_periods
        )
        n_stress_periods = 1 + len(transient_periods)
        n_transient_periods = len(transient_periods)
    else:
        # STEADY single period (mine_dewatering / regional_water_budget).
        flopy.mf6.ModflowTdis(
            sim,
            time_units=TIME_UNITS,
            nper=1,
            perioddata=[(1.0, 1, 1.0)],
        )
        n_stress_periods = 1
        n_transient_periods = 0

    # --- DIS ----------------------------------------------------------------- #
    flopy.mf6.ModflowGwfdis(
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
    try:
        gwf.modelgrid.set_coord_info(xoff=xorigin, yoff=yorigin, crs=crs.to_epsg())
    except Exception:  # pragma: no cover - older flopy signature fallback
        pass

    # --- IC + NPF ------------------------------------------------------------ #
    domain_width_m = ncol * delr
    head_west = AQUIFER_TOP_M + REGIONAL_GRADIENT * domain_width_m
    head_east = AQUIFER_TOP_M
    flopy.mf6.ModflowGwfic(gwf, strt=head_west, filename=f"{gwf_name}.ic")

    # mine_dewatering uses an UNCONFINED water table (icelltype=1) so the drained
    # cells can de-saturate; the other archetypes stay confined (icelltype=0).
    npf_icelltype = 1 if archetype == "mine_dewatering" else 0
    flopy.mf6.ModflowGwfnpf(
        gwf,
        save_flows=True,
        icelltype=npf_icelltype,
        k=k_m_per_day,
        filename=f"{gwf_name}.npf",
    )

    # --- STO (transient archetypes only) ------------------------------------- #
    if transient:
        _add_transient_sto_tdis(
            sim,
            gwf,
            transient_periods=transient_periods,
            sy=sy,
            ss=ss,
            gwf_name=gwf_name,
            iconvert=npf_icelltype,
        )

    # --- CHD regional gradient (same as the spill deck) ---------------------- #
    chd_records = []
    for r in range(nrow):
        chd_records.append([(0, r, 0), head_west])
        chd_records.append([(0, r, ncol - 1), head_east])
    chd_spd = {i: chd_records for i in range(n_stress_periods)}
    flopy.mf6.ModflowGwfchd(
        gwf,
        stress_period_data=chd_spd,
        filename=f"{gwf_name}.chd",
    )

    # --- Per-archetype stress packages --------------------------------------- #
    well_row = well_col = -1
    well_east = well_north = 0.0
    well_lat = well_lon = 0.0
    pump_rate = 0.0
    drain_cell_count = 0
    drain_elev_written = 0.0
    drain_cond_written = 0.0
    zone_partition_written: str | None = None
    n_zones = 0

    if archetype == "sustainable_yield":
        if well_location_latlon is None:
            raise ValueError(
                "sustainable_yield archetype requires well_location_latlon"
            )
        if pumping_rate_m3_day is None:
            raise ValueError(
                "sustainable_yield archetype requires pumping_rate_m3_day"
            )
        wlat, wlon = float(well_location_latlon[0]), float(well_location_latlon[1])
        if not (-90.0 <= wlat <= 90.0) or not (-180.0 <= wlon <= 180.0):
            raise ValueError(f"well_location_latlon out of range: {(wlat, wlon)!r}")
        well_east, well_north = to_utm.transform(wlon, wlat)
        cell = _easting_northing_to_cell(
            well_east,
            well_north,
            xorigin=xorigin,
            yorigin=yorigin,
            delr=delr,
            delc=delc,
            nrow=nrow,
            ncol=ncol,
        )
        if cell is None:
            # Clamp to the nearest in-grid cell (the well must land on the grid).
            col = max(0, min(ncol - 1, int((well_east - xorigin) // delr)))
            row = max(
                0,
                min(nrow - 1, int(((yorigin + nrow * delc) - well_north) // delc)),
            )
            cell = (row, col)
        well_row, well_col = cell
        well_lat, well_lon = wlat, wlon
        pump_rate = float(pumping_rate_m3_day)
        # WEL is active in EVERY transient period (sustained pumping). The
        # steady-state spin-up (period 0) runs WITHOUT the well so the drawdown
        # is measured against the undisturbed regional head.
        wel_record = [[(0, well_row, well_col), pump_rate]]
        wel_spd = {0: []}
        for i in range(1, n_stress_periods):
            wel_spd[i] = wel_record
        flopy.mf6.ModflowGwfwel(
            gwf,
            stress_period_data=wel_spd,
            save_flows=True,
            filename=f"{gwf_name}.wel",
            pname="wel-0",
        )

    elif archetype == "mine_dewatering":
        if not pit_footprint_lonlat:
            raise ValueError(
                "mine_dewatering archetype requires a pit_footprint_lonlat"
            )
        drain_cond_written = (
            float(drain_conductance_m2_day)
            if drain_conductance_m2_day is not None
            else DEFAULT_DRAIN_CONDUCTANCE_M2_DAY
        )
        drain_elev_written = (
            float(drain_elevation_m)
            if drain_elevation_m is not None
            else AQUIFER_TOP_M - DEFAULT_DRAIN_DEPTH_BELOW_TOP_M
        )
        # Drape the pit footprint onto the grid. A polygon ring is draped as a
        # polyline (its boundary), then the interior is filled so the whole pit
        # footprint is drained (not just the ring).
        verts_en = [to_utm.transform(plon, plat) for (plon, plat) in pit_footprint_lonlat]
        ring = list(verts_en)
        if len(ring) >= 3 and ring[0] != ring[-1]:
            ring.append(ring[0])  # close the ring so the boundary is continuous
        draped = _drape_polyline_onto_grid(
            ring,
            xorigin=xorigin,
            yorigin=yorigin,
            delr=delr,
            delc=delc,
            nrow=nrow,
            ncol=ncol,
        )
        pit_cells = _fill_polygon_cells(
            [(r, c) for (r, c, _l) in draped], nrow=nrow, ncol=ncol
        )
        # Skip the CHD boundary columns (a cell cannot be both CHD and DRN).
        skip_cols = {0, ncol - 1}
        drn_records = [
            [(0, r, c), drain_elev_written, drain_cond_written]
            for (r, c) in pit_cells
            if c not in skip_cols
        ]
        if not drn_records:
            raise ValueError(
                "mine_dewatering pit_footprint_lonlat draped to zero in-grid "
                "drain cells (footprint outside the model grid?)"
            )
        drn_spd = {i: drn_records for i in range(n_stress_periods)}
        flopy.mf6.ModflowGwfdrn(
            gwf,
            stress_period_data=drn_spd,
            save_flows=True,
            filename=f"{gwf_name}.drn",
            pname="drn-0",
        )
        drain_cell_count = len(drn_records)
        # Optional supplemental sump WEL (a pit dewatered by drains + pumping).
        if well_pumping_rate_m3_day is not None and float(well_pumping_rate_m3_day) != 0.0:
            # Place the sump at the pit centroid cell.
            crow = sum(r for (r, _c) in pit_cells) // len(pit_cells)
            ccol = sum(c for (_r, c) in pit_cells) // len(pit_cells)
            ccol = max(1, min(ncol - 2, ccol))  # keep off the CHD columns
            pump_rate = float(well_pumping_rate_m3_day)
            flopy.mf6.ModflowGwfwel(
                gwf,
                stress_period_data={i: [[(0, crow, ccol), pump_rate]] for i in range(n_stress_periods)},
                save_flows=True,
                filename=f"{gwf_name}.wel",
                pname="wel-0",
            )
            well_row, well_col = crow, ccol

    elif archetype == "regional_water_budget":
        # No new stress package -- the deliverable is the CBC budget partition.
        # When a zone_partition is requested, write the optional ZONE array so an
        # agent-side ZoneBudget-style partition can read it.
        if zone_partition:
            zone_partition_written = str(zone_partition)
            zone_array, n_zones = _build_zone_array(zone_partition, nrow=nrow, ncol=ncol)
            # FloPy has no first-class ZONE package; write a plain external array
            # the agent-side partition reads. We persist it as a CSV sidecar so it
            # ships with the deck without perturbing any MF6 input file.
            if write:
                zpath = sim_dir / f"{gwf_name}.zones.csv"
                lines = [",".join(str(int(v)) for v in row) for row in zone_array]
                zpath.write_text("\n".join(lines) + "\n")

    # --- OC: save HEAD + BUDGET ALL ------------------------------------------ #
    flopy.mf6.ModflowGwfoc(
        gwf,
        head_filerecord=f"{gwf_name}.hds",
        budget_filerecord=f"{gwf_name}.cbc",
        saverecord=[("HEAD", "ALL"), ("BUDGET", "ALL")],
        filename=f"{gwf_name}.oc",
    )

    manifest = DeckManifest(
        sim_dir=str(sim_dir),
        sim_name=sim_name,
        gwf_name=gwf_name,
        gwt_name="",  # GWF-only: no transport model
        model_crs=f"EPSG:{crs.to_epsg()}",
        xorigin=xorigin,
        yorigin=yorigin,
        nrow=nrow,
        ncol=ncol,
        nlay=N_LAYERS,
        delr=delr,
        delc=delc,
        # The spill cell fields are unused for GWF-only archetypes; carry the
        # grid centre so the manifest stays well-formed (not prose).
        spill_row=nrow // 2,
        spill_col=ncol // 2,
        spill_easting_m=xorigin + (ncol // 2 + 0.5) * delr,
        spill_northing_m=(yorigin + nrow * delc) - (nrow // 2 + 0.5) * delc,
        spill_lat=lat,
        spill_lon=lon,
        mass_rate_g_per_day=0.0,
        release_rate_kg_s=0.0,
        duration_days=0.0,
        n_transport_steps=0,
        contaminant="",
        aquifer_k_ms=aquifer_k_ms,
        porosity=porosity,
        archetype=archetype,
        gwt_present=False,
        transient=transient,
        n_stress_periods=n_stress_periods,
        n_transient_periods=n_transient_periods,
        well_row=well_row,
        well_col=well_col,
        well_easting_m=float(well_east),
        well_northing_m=float(well_north),
        well_lat=float(well_lat),
        well_lon=float(well_lon),
        pumping_rate_m3_day=pump_rate,
        aquifer_sy=sy if transient else 0.0,
        aquifer_ss=ss if transient else 0.0,
        drain_cell_count=drain_cell_count,
        drain_elevation_m=drain_elev_written,
        drain_conductance_m2_day=drain_cond_written,
        npf_icelltype=npf_icelltype,
        zone_partition=zone_partition_written,
        n_zones=n_zones,
    )

    if write:
        sim.write_simulation()
        manifest.files = sorted(
            str(p.relative_to(sim_dir)) for p in sim_dir.rglob("*") if p.is_file()
        )
    return manifest


def _fill_polygon_cells(
    boundary_cells: list[tuple[int, int]], *, nrow: int, ncol: int
) -> list[tuple[int, int]]:
    """Fill a draped polygon boundary into all interior (row, col) cells.

    Given the set of grid cells the polygon BOUNDARY traverses, return every cell
    inside-or-on the polygon via a per-row span fill: for each row that the
    boundary touches, fill the columns between the min and max boundary column in
    that row (inclusive). This is a deterministic, dependency-free fill that is
    exact for convex footprints and a reasonable filled-hull for concave ones --
    adequate for a demo pit footprint draped onto a 40x40 grid. A single boundary
    cell (point/tiny pit) returns just that cell.

    Returned in (row, col) sorted order so the DRN records are deterministic.
    """
    if not boundary_cells:
        return []
    by_row: dict[int, list[int]] = {}
    for (r, c) in boundary_cells:
        by_row.setdefault(r, []).append(c)
    filled: set[tuple[int, int]] = set()
    for r, cols in by_row.items():
        cmin, cmax = min(cols), max(cols)
        for c in range(cmin, cmax + 1):
            if 0 <= r < nrow and 0 <= c < ncol:
                filled.add((r, c))
    return sorted(filled)


def _build_zone_array(
    zone_partition: str, *, nrow: int, ncol: int
) -> tuple[list[list[int]], int]:
    """Build a per-cell zone-id array for the regional_water_budget partition.

    Returns ``(zone_array, n_zones)``. The only first-class scheme is
    ``"upgradient_downgradient"`` -- a two-zone west/east split across the regional
    CHD gradient (zone 1 = upgradient west half, zone 2 = downgradient east half).
    Any other (non-empty) string falls back to that same two-zone split so a named
    partition the adapter does not special-case still produces a usable array
    (the agent-side partition maps the label). Deterministic, pure.
    """
    mid = ncol // 2
    zone_array = [
        [1 if c < mid else 2 for c in range(ncol)] for _r in range(nrow)
    ]
    return zone_array, 2


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
    # --- Archetype switch (sprint-18 Wave-1; ADDITIVE, all optional) -------- #
    # archetype is None -> the EXISTING spill/seepage GWF+GWT deck (byte-identical).
    # The three new archetypes are GWF-only and dispatch to
    # ``_build_gwf_only_archetype_deck``; the spill-only kwargs above are ignored.
    archetype: str | None = None,
    well_location_latlon: tuple[float, float] | None = None,
    pumping_rate_m3_day: float | None = None,
    aquifer_sy: float | None = None,
    aquifer_ss: float | None = None,
    sim_years: float | None = None,
    n_periods: int | None = None,
    pit_footprint_lonlat: list[tuple[float, float]] | None = None,
    drain_elevation_m: float | None = None,
    drain_conductance_m2_day: float | None = None,
    well_pumping_rate_m3_day: float | None = None,
    zone_partition: str | None = None,
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
    # The release_rate / duration validations are SPILL/SEEPAGE-only -- the three
    # GWF-only archetypes (archetype is not None) carry no contaminant source, so
    # the agent-side phase passes placeholder spill params. Aquifer K + porosity
    # + the (lat, lon) grid centre are meaningful for EVERY archetype (the grid is
    # centred on the AOI point and the NPF reads K), so those stay unconditional.
    if archetype is None:
        if release_rate_kg_s <= 0:
            raise ValueError(
                f"release_rate_kg_s must be > 0, got {release_rate_kg_s!r}"
            )
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

    # --- Archetype switch (sprint-18 Wave-1) ---------------------------------
    # A non-None archetype is one of the three NEW GWF-only MODFLOW questions
    # (sustainable_yield / mine_dewatering / regional_water_budget). They reuse
    # the SAME georegistration computed above (UTM zone, grid origin, 40x40x50 m
    # grid) and the SAME west->east REGIONAL_GRADIENT CHD, but build a GWF-only
    # deck (no GWT/SRC/SSM/DSP/MST block, no GWFGWT exchange) via the shared
    # ``_build_gwf_only_archetype_deck`` helper. ``archetype is None`` falls
    # through to the EXISTING spill/seepage GWF+GWT deck below (byte-identical).
    if archetype is not None:
        if archetype not in (
            "sustainable_yield",
            "mine_dewatering",
            "regional_water_budget",
        ):
            raise ValueError(f"unknown MODFLOW archetype: {archetype!r}")
        return _build_gwf_only_archetype_deck(
            archetype=archetype,
            lat=lat,
            lon=lon,
            crs=crs,
            to_utm=to_utm,
            xorigin=xorigin,
            yorigin=yorigin,
            nrow=nrow,
            ncol=ncol,
            delr=delr,
            delc=delc,
            k_m_per_day=k_m_per_day,
            aquifer_k_ms=aquifer_k_ms,
            porosity=porosity,
            sim_dir=sim_dir,
            sim_name=sim_name,
            gwf_name=gwf_name,
            write=write,
            well_location_latlon=well_location_latlon,
            pumping_rate_m3_day=pumping_rate_m3_day,
            aquifer_sy=aquifer_sy,
            aquifer_ss=aquifer_ss,
            sim_years=sim_years,
            n_periods=n_periods,
            pit_footprint_lonlat=pit_footprint_lonlat,
            drain_elevation_m=drain_elevation_m,
            drain_conductance_m2_day=drain_conductance_m2_day,
            well_pumping_rate_m3_day=well_pumping_rate_m3_day,
            zone_partition=zone_partition,
        )

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
    sorption_active = kd is not None and float(kd) > 0.0
    if sorption_active:
        mst_kwargs["sorption"] = "LINEAR"
        mst_kwargs["distcoef"] = float(kd)
        mst_kwargs["bulk_density"] = float(phys.get("bulk_density", 1600.0))
    decay = phys.get("decay_rate_per_day")
    decay_active = decay is not None and float(decay) > 0.0
    if decay_active:
        mst_kwargs["first_order_decay"] = True
        mst_kwargs["decay"] = float(decay)
        # LIVE BUG FIX (sprint-18 Wave-1): MF6 REQUIRES decay_sorbed in the
        # GRIDDATA block whenever BOTH first-order decay AND sorption are active
        # ("DECAY_SORBED not provided in GRIDDATA block but decay and sorption are
        # active"). Default the sorbed-phase decay coefficient to the aqueous
        # decay value (decay of the sorbed contaminant proceeds at the same
        # first-order rate as the dissolved phase unless the caller overrides it).
        if sorption_active:
            decay_sorbed = phys.get("decay_sorbed_per_day")
            mst_kwargs["decay_sorbed"] = (
                float(decay_sorbed) if decay_sorbed is not None else float(decay)
            )
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
