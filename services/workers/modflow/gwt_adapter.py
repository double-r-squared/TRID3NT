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
    flopy.mf6.ModflowGwtdsp(
        gwt,
        alh=LONGITUDINAL_DISPERSIVITY_M,
        ath1=LONGITUDINAL_DISPERSIVITY_M * TRANSVERSE_HORIZONTAL_RATIO,
        atv=LONGITUDINAL_DISPERSIVITY_M * TRANSVERSE_VERTICAL_RATIO,
        filename=f"{gwt_name}.dsp",
    )
    # Mobile storage: porosity controls pore velocity (v = q / porosity).
    flopy.mf6.ModflowGwtmst(
        gwt,
        porosity=porosity,
        filename=f"{gwt_name}.mst",
    )

    # Mass-loading source at the spill cell. SRC injects mass/time directly
    # (g/day here) regardless of local concentration — the spill-loading model.
    # The source is active ONLY in the transient transport period (period 1,
    # 0-based), NOT the steady-state flow spin-up (period 0). This keeps the
    # released-mass yardstick exact: total injected = mass_rate x duration,
    # not mass_rate x (1 spin-up day + duration). Empty list in period 0
    # deactivates the source there.
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

    flopy.mf6.ModflowGwtoc(
        gwt,
        concentration_filerecord=f"{gwt_name}.ucn",
        budget_filerecord=f"{gwt_name}.cbc",
        saverecord=[("CONCENTRATION", "LAST"), ("BUDGET", "LAST")],
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
