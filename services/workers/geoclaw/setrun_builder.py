"""GeoClaw ``setrun.py`` authoring — the build_spec -> Clawpack deck adapter.

The GeoClaw analogue of ``services/workers/modflow/gwt_adapter.py`` (which authors
a FloPy deck from typed args). This module is the DETERMINISTIC, UNIT-TESTABLE
core of the GeoClaw worker: it parses the agent-staged ``build_spec`` JSON and
emits a canonical Clawpack/GeoClaw ``setrun.py`` (plus, for a tsunami scenario, a
``maketopo`` helper that synthesizes a dtopo) over the AOI + topo DEM + a driver
scenario.

It deliberately does NOT import clawpack or run the solver — it only WRITES the
deck files (a ``setrun.py`` Python module + a small ``qinit`` / ``dtopo`` data
file when the scenario needs one). The entrypoint then invokes the Clawpack
``runclaw`` / ``make .output`` machinery against the authored deck. Splitting the
authoring out (mirroring gwt_adapter) is what makes the worker testable with NO
Fortran toolchain present.

Canonical real-world pipeline (mirrored, not invented):
    GeoClaw modellers write a ``setrun.py`` that returns a ``clawpack.clawutil
    .data.ClawRunData`` object. The load-bearing blocks, in the order GeoClaw's
    own examples use them, are:
      - clawdata: domain (lower/upper x,y), base grid (num_cells), t span +
        evenly-spaced output_times (the fort.q frames), CFL, bc (boundary
        conditions).
      - geo_data: gravity, coordinate_system=2 (lat/lon), earth_radius,
        sea_level, friction (manning_coefficient), dry_tolerance.
      - topo_data.topofiles: the topography file(s) over the AOI.
      - amrdata: amr_levels_max + refinement_ratios (adaptive mesh refinement).
      - qinit_data (dam_break): a raised-column perturbation file.
      - dtopo_data.dtopofiles (tsunami): the seafloor-deformation source.
      - the surge scenario reuses a sea-surface boundary forcing (a fixed-grid
        sea_level offset for the v0.1 single-pulse fallback).

The build_spec schema (authored agent-side by ``workflows/run_geoclaw.py``):
    {
      "scenario": "dam_break" | "tsunami" | "surge",
      "bbox": [min_lon, min_lat, max_lon, max_lat],   # EPSG:4326
      "topo_file": "topo.asc",        # staged DEM (topotype-3 ESRI ASCII)
      "sim_duration_s": 3600.0,
      "output_frames": 24,
      "amr_levels": 2,
      "manning_n": 0.025,
      "sea_level_m": 0.0,
      "base_num_cells": [40, 40],     # optional; base grid resolution
      # dam_break:
      "dam_break_depth_m": 10.0,
      "source_lonlat": [lon, lat],    # optional; AOI centroid otherwise
      # tsunami:
      "dtopo_file": "dtopo.tt3",      # optional staged dtopo; else synthesize
      "source_magnitude": 8.0,
      # surge:
      "surge_forcing_file": "surge.csv",  # optional staged hydrograph
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "GeoClawDeckError",
    "GeoClawBuildSpec",
    "DeckManifest",
    "parse_build_spec",
    "render_setrun_py",
    "render_qinit_data",
    "render_maketopo_dtopo",
    "build_geoclaw_deck",
]


class GeoClawDeckError(RuntimeError):
    """Raised on a malformed build_spec / unsupported scenario.

    Carries an open-set ``error_code`` so the entrypoint records a typed failure.
    """

    error_code: str = "GEOCLAW_DECK_BUILD_FAILED"

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


_VALID_SCENARIOS = {"dam_break", "tsunami", "surge"}


@dataclass
class GeoClawBuildSpec:
    """The typed, validated build_spec the deck author consumes.

    A plain dataclass (no pydantic dep in the worker image) holding exactly the
    fields ``render_setrun_py`` needs. ``parse_build_spec`` validates + fills
    defaults from the raw manifest dict.
    """

    scenario: str
    bbox: tuple[float, float, float, float]
    topo_file: str
    sim_duration_s: float = 3600.0
    output_frames: int = 24
    amr_levels: int = 2
    manning_n: float = 0.025
    sea_level_m: float = 0.0
    base_num_cells: tuple[int, int] = (40, 40)
    # dam_break.
    dam_break_depth_m: float = 10.0
    source_lonlat: tuple[float, float] | None = None
    # tsunami.
    dtopo_file: str | None = None
    source_magnitude: float = 8.0
    # surge.
    surge_forcing_file: str | None = None


@dataclass
class DeckManifest:
    """Provenance the deck author returns (echoed into completion for narration).

    Mirrors ``gwt_adapter.DeckManifest``: a small typed record the entrypoint /
    postprocess can read to narrate typed numbers about what was built (domain,
    grid, driver) without re-parsing the setrun.py.
    """

    scenario: str
    bbox: tuple[float, float, float, float]
    base_num_cells: tuple[int, int]
    amr_levels: int
    output_frames: int
    sim_duration_s: float
    files_written: list[str] = field(default_factory=list)
    driver_descriptor: str = ""


def parse_build_spec(raw: dict[str, Any]) -> GeoClawBuildSpec:
    """Validate the raw manifest ``build_spec`` dict -> a typed ``GeoClawBuildSpec``.

    Raises ``GeoClawDeckError`` (typed code) on a missing/invalid field so the
    entrypoint records an honest terminal error rather than crashing mid-deck.
    """
    if not isinstance(raw, dict):
        raise GeoClawDeckError(
            "GEOCLAW_SPEC_INVALID", f"build_spec must be a JSON object, got {type(raw)}"
        )

    scenario = str(raw.get("scenario") or "dam_break").strip().lower()
    if scenario not in _VALID_SCENARIOS:
        raise GeoClawDeckError(
            "GEOCLAW_SPEC_INVALID",
            f"scenario must be one of {sorted(_VALID_SCENARIOS)}, got {scenario!r}",
        )

    bbox_raw = raw.get("bbox")
    if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) != 4:
        raise GeoClawDeckError(
            "GEOCLAW_SPEC_INVALID",
            f"bbox must be [min_lon, min_lat, max_lon, max_lat], got {bbox_raw!r}",
        )
    try:
        bbox = tuple(float(v) for v in bbox_raw)  # type: ignore[assignment]
    except (TypeError, ValueError) as exc:
        raise GeoClawDeckError(
            "GEOCLAW_SPEC_INVALID", f"bbox values must be numeric: {bbox_raw!r}"
        ) from exc
    min_lon, min_lat, max_lon, max_lat = bbox
    if not (min_lon < max_lon and min_lat < max_lat):
        raise GeoClawDeckError(
            "GEOCLAW_SPEC_INVALID",
            f"bbox must satisfy min_lon<max_lon and min_lat<max_lat, got {bbox}",
        )

    topo_file = str(raw.get("topo_file") or "").strip()
    if not topo_file:
        raise GeoClawDeckError(
            "GEOCLAW_SPEC_INVALID", "build_spec.topo_file is required (the staged DEM)"
        )

    def _num(key: str, default: float) -> float:
        v = raw.get(key)
        return float(v) if v is not None else float(default)

    def _int(key: str, default: int) -> int:
        v = raw.get(key)
        return int(v) if v is not None else int(default)

    base_cells_raw = raw.get("base_num_cells") or [40, 40]
    if not isinstance(base_cells_raw, (list, tuple)) or len(base_cells_raw) != 2:
        raise GeoClawDeckError(
            "GEOCLAW_SPEC_INVALID",
            f"base_num_cells must be [nx, ny], got {base_cells_raw!r}",
        )
    base_num_cells = (int(base_cells_raw[0]), int(base_cells_raw[1]))
    if base_num_cells[0] < 2 or base_num_cells[1] < 2:
        raise GeoClawDeckError(
            "GEOCLAW_SPEC_INVALID", f"base_num_cells must each be >= 2, got {base_num_cells}"
        )

    src = raw.get("source_lonlat")
    source_lonlat: tuple[float, float] | None = None
    if isinstance(src, (list, tuple)) and len(src) == 2:
        source_lonlat = (float(src[0]), float(src[1]))

    sim_duration_s = _num("sim_duration_s", 3600.0)
    if sim_duration_s <= 0:
        raise GeoClawDeckError(
            "GEOCLAW_SPEC_INVALID", f"sim_duration_s must be > 0, got {sim_duration_s}"
        )
    output_frames = _int("output_frames", 24)
    if output_frames < 1:
        raise GeoClawDeckError(
            "GEOCLAW_SPEC_INVALID", f"output_frames must be >= 1, got {output_frames}"
        )
    amr_levels = _int("amr_levels", 2)
    if amr_levels < 1:
        raise GeoClawDeckError(
            "GEOCLAW_SPEC_INVALID", f"amr_levels must be >= 1, got {amr_levels}"
        )

    return GeoClawBuildSpec(
        scenario=scenario,
        bbox=bbox,  # type: ignore[arg-type]
        topo_file=topo_file,
        sim_duration_s=sim_duration_s,
        output_frames=output_frames,
        amr_levels=amr_levels,
        manning_n=_num("manning_n", 0.025),
        sea_level_m=_num("sea_level_m", 0.0),
        base_num_cells=base_num_cells,
        dam_break_depth_m=_num("dam_break_depth_m", 10.0),
        source_lonlat=source_lonlat,
        dtopo_file=(str(raw["dtopo_file"]).strip() if raw.get("dtopo_file") else None),
        source_magnitude=_num("source_magnitude", 8.0),
        surge_forcing_file=(
            str(raw["surge_forcing_file"]).strip()
            if raw.get("surge_forcing_file")
            else None
        ),
    )


def _centroid(spec: GeoClawBuildSpec) -> tuple[float, float]:
    """The driver source point — explicit ``source_lonlat`` or the AOI centroid."""
    if spec.source_lonlat is not None:
        return spec.source_lonlat
    min_lon, min_lat, max_lon, max_lat = spec.bbox
    return (0.5 * (min_lon + max_lon), 0.5 * (min_lat + max_lat))


def render_qinit_data(spec: GeoClawBuildSpec) -> str:
    """Render a ``qinit.xyz`` raised-column perturbation for the dam_break scenario.

    A topotype-1 (x y z) ESRI-style grid: a circular raised water column of
    height ``dam_break_depth_m`` centred on the source, radius scaled to ~1/8 of
    the domain. GeoClaw's ``qinit`` module adds this perturbation to the initial
    water surface, releasing it at t=0 (the canonical dam-break test).

    Pure string render — unit-testable with no clawpack import.
    """
    min_lon, min_lat, max_lon, max_lat = spec.bbox
    cx, cy = _centroid(spec)
    span = min(max_lon - min_lon, max_lat - min_lat)
    radius = max(span / 8.0, 1e-4)
    h = float(spec.dam_break_depth_m)
    # A small (16x16) perturbation grid covering the source disc. GeoClaw
    # bilinearly interpolates the qinit file onto the computational grid.
    n = 16
    lines = []
    for j in range(n):
        for i in range(n):
            x = cx - radius + (2.0 * radius) * (i / (n - 1))
            y = cy - radius + (2.0 * radius) * (j / (n - 1))
            r = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            z = h if r <= radius else 0.0
            lines.append(f"{x:.8f} {y:.8f} {z:.6f}")
    return "\n".join(lines) + "\n"


def render_maketopo_dtopo(spec: GeoClawBuildSpec) -> str:
    """Render a ``maketopo.py`` that synthesizes an Okada dtopo for the tsunami
    scenario (when no dtopo file was staged).

    Uses ``clawpack.geoclaw.dtopotools`` to build a single-subfault Okada source
    scaled from ``source_magnitude`` at the source point and write ``dtopo.tt3``.
    This is emitted as a SEPARATE Python helper the entrypoint runs BEFORE the
    solve (it imports clawpack, so it must not be imported by this authoring
    module). Pure string render here.
    """
    cx, cy = _centroid(spec)
    mw = float(spec.source_magnitude)
    return f'''"""Auto-generated by GRACE-2 GeoClaw worker — synthesize an Okada dtopo."""
import numpy as np
from clawpack.geoclaw import dtopotools

# Scale a single rectangular subfault from the moment magnitude (Mw).
# Wells & Coppersmith (1994) style log-scaling for length/width; mu = 4e10 Pa.
mw = {mw!r}
M0 = 10.0 ** (1.5 * mw + 9.05)            # seismic moment (N m)
length = 10.0 ** (-2.44 + 0.59 * mw) * 1000.0   # m
width = 10.0 ** (-1.01 + 0.32 * mw) * 1000.0    # m
mu = 4.0e10
slip = M0 / (mu * length * width)

subfault = dtopotools.SubFault()
subfault.strike = 0.0
subfault.dip = 15.0
subfault.rake = 90.0
subfault.length = length
subfault.width = width
subfault.depth = 10.0e3
subfault.slip = slip
subfault.longitude = {cx!r}
subfault.latitude = {cy!r}
subfault.coordinate_specification = "centroid"

fault = dtopotools.Fault()
fault.subfaults = [subfault]

# A dtopo grid covering a generous box around the source.
dx = max(length, width) / 1.0e5 * 2.0
x = np.linspace({cx!r} - 0.5, {cx!r} + 0.5, 101)
y = np.linspace({cy!r} - 0.5, {cy!r} + 0.5, 101)
fault.create_dtopography(x, y, times=[0.0, 1.0])
fault.dtopo.write("dtopo.tt3", dtopo_type=3)
print("wrote dtopo.tt3 mw=%s slip=%.2f m" % (mw, slip))
'''


def render_setrun_py(spec: GeoClawBuildSpec) -> str:
    """Render the canonical GeoClaw ``setrun.py`` for the build_spec.

    Emits a ``setrun(claw_pkg='geoclaw')`` function returning a
    ``ClawRunData`` with the load-bearing clawdata / geo_data / topo_data /
    amrdata / (qinit|dtopo) blocks wired from ``spec``. The output_times list is
    ``output_frames`` evenly-spaced dumps across ``[0, sim_duration_s]`` so the
    postprocess gets exactly that many fort.q frames for the animation group.

    PURE string render — unit-testable with NO clawpack import. The clawpack
    import lives INSIDE the generated module (executed only when the entrypoint
    runs it), never in this authoring module.
    """
    min_lon, min_lat, max_lon, max_lat = spec.bbox
    nx, ny = spec.base_num_cells
    amr_ratios = ", ".join(["2"] * max(spec.amr_levels - 1, 1))

    # Evenly-spaced output frames including the final time (exclude t=0 dump:
    # GeoClaw always writes frame 0 at t=0, so we request output_frames AFTER it
    # via output_style=1 with num_output_times = output_frames and tfinal set).
    num_output_times = int(spec.output_frames)

    # Scenario-specific source blocks.
    qinit_block = ""
    dtopo_block = ""
    if spec.scenario == "dam_break":
        qinit_block = (
            "    qinit_data = rundata.qinit_data\n"
            "    qinit_data.qinit_type = 4  # perturbation to eta (water surface)\n"
            "    qinit_data.qinitfiles = []\n"
            "    qinit_data.qinitfiles.append([1, 'qinit.xyz'])\n"
        )
    elif spec.scenario == "tsunami":
        dtopo_file = spec.dtopo_file or "dtopo.tt3"
        dtopo_block = (
            "    dtopo_data = rundata.dtopo_data\n"
            "    dtopo_data.dtopofiles = []\n"
            f"    dtopo_data.dtopofiles.append([3, {dtopo_file!r}])\n"
            "    dtopo_data.dt_max_dtopo = 1.0\n"
        )
    # surge: the v0.1 fallback applies the sea_level offset only (a uniform
    # raised sea surface as a single-pulse surge); a staged hydrograph upgrade
    # plugs in here via a fgmax/boundary forcing in a later phase.

    return f'''"""Auto-generated by GRACE-2 GeoClaw worker (setrun_builder).

Scenario: {spec.scenario}
Domain (EPSG:4326): {spec.bbox}
Do NOT hand-edit — regenerate from the build_spec.
"""
from clawpack.clawutil import data


def setrun(claw_pkg="geoclaw"):
    assert claw_pkg.lower() == "geoclaw", "setrun expects claw_pkg='geoclaw'"
    num_dim = 2
    rundata = data.ClawRunData(claw_pkg, num_dim)
    rundata = setgeo(rundata)

    clawdata = rundata.clawdata
    clawdata.num_dim = num_dim

    # --- Domain (lon/lat) ---
    clawdata.lower[0] = {min_lon!r}
    clawdata.upper[0] = {max_lon!r}
    clawdata.lower[1] = {min_lat!r}
    clawdata.upper[1] = {max_lat!r}

    # --- Base computational grid ---
    clawdata.num_cells[0] = {nx!r}
    clawdata.num_cells[1] = {ny!r}

    clawdata.num_eqn = 3
    clawdata.num_aux = 3
    clawdata.capa_index = 2

    # --- Time domain + evenly-spaced output frames (the fort.q animation) ---
    clawdata.t0 = 0.0
    clawdata.output_style = 1
    clawdata.num_output_times = {num_output_times!r}
    clawdata.tfinal = {float(spec.sim_duration_s)!r}
    clawdata.output_t0 = True
    clawdata.output_format = "ascii"
    clawdata.output_q_components = "all"
    clawdata.output_aux_components = "none"

    # --- Numerics ---
    clawdata.dt_initial = 1.0
    clawdata.dt_variable = True
    clawdata.dt_max = 1.0e99
    clawdata.cfl_desired = 0.75
    clawdata.cfl_max = 1.0
    clawdata.steps_max = 100000
    clawdata.order = 2
    clawdata.dimensional_split = "unsplit"
    clawdata.transverse_waves = 2
    clawdata.num_waves = 3
    clawdata.limiter = ["mc", "mc", "mc"]
    clawdata.use_fwaves = True
    clawdata.source_split = "godunov"

    # --- Boundary conditions (extrap = open / non-reflecting) ---
    clawdata.num_ghost = 2
    clawdata.bc_lower[0] = "extrap"
    clawdata.bc_upper[0] = "extrap"
    clawdata.bc_lower[1] = "extrap"
    clawdata.bc_upper[1] = "extrap"

    # --- AMR (adaptive mesh refinement) ---
    amrdata = rundata.amrdata
    amrdata.amr_levels_max = {int(spec.amr_levels)!r}
    amrdata.refinement_ratios_x = [{amr_ratios}]
    amrdata.refinement_ratios_y = [{amr_ratios}]
    amrdata.refinement_ratios_t = [{amr_ratios}]
    amrdata.aux_type = ["center", "capacity", "yleft"]
    amrdata.flag_richardson = False
    amrdata.flag2refine = True
    amrdata.regrid_interval = 3
    amrdata.regrid_buffer_width = 2
    amrdata.verbosity_regrid = 0

{qinit_block}{dtopo_block}    return rundata


def setgeo(rundata):
    try:
        geo_data = rundata.geo_data
    except AttributeError:
        raise AttributeError("Missing geo_data; rundata must be a GeoClaw run.")

    geo_data.gravity = 9.81
    geo_data.coordinate_system = 2  # 2 = lat/lon (spherical)
    geo_data.earth_radius = 6367500.0

    geo_data.dry_tolerance = 1.0e-3
    geo_data.friction_forcing = True
    geo_data.manning_coefficient = {float(spec.manning_n)!r}
    geo_data.friction_depth = 1.0e6

    geo_data.sea_level = {float(spec.sea_level_m)!r}

    refine_data = rundata.refinement_data
    refine_data.wave_tolerance = 0.05
    refine_data.speed_tolerance = [0.25, 0.5, 1.0, 2.0]
    refine_data.variable_dt_refinement_ratios = True

    topo_data = rundata.topo_data
    topo_data.topofiles = []
    # topotype 3 = ESRI/GeoClaw header ASCII; the entrypoint converts the staged
    # DEM to this form as {spec.topo_file!r}.
    topo_data.topofiles.append([3, {spec.topo_file!r}])

    return rundata


if __name__ == "__main__":
    rundata = setrun()
    rundata.write()
'''


def build_geoclaw_deck(build_spec_raw: dict[str, Any], deck_dir: Any) -> DeckManifest:
    """Author the full GeoClaw deck (setrun.py + scenario source files) into
    ``deck_dir`` from a raw build_spec dict. Returns a ``DeckManifest`` of what
    was written.

    The single entrypoint-facing call: parse -> render -> write. clawpack is NOT
    imported (the rendered ``maketopo.py`` imports it, executed later by the
    entrypoint). Pure file I/O + string render -> unit-testable with no Fortran.
    """
    from pathlib import Path

    deck = Path(deck_dir)
    deck.mkdir(parents=True, exist_ok=True)
    spec = parse_build_spec(build_spec_raw)

    written: list[str] = []

    setrun_text = render_setrun_py(spec)
    (deck / "setrun.py").write_text(setrun_text, encoding="utf-8")
    written.append("setrun.py")

    driver = ""
    if spec.scenario == "dam_break":
        (deck / "qinit.xyz").write_text(render_qinit_data(spec), encoding="utf-8")
        written.append("qinit.xyz")
        driver = f"dam_break raised column {spec.dam_break_depth_m:.1f} m at {_centroid(spec)}"
    elif spec.scenario == "tsunami":
        if spec.dtopo_file is None:
            (deck / "maketopo.py").write_text(
                render_maketopo_dtopo(spec), encoding="utf-8"
            )
            written.append("maketopo.py")
            driver = (
                f"tsunami synthetic Okada source Mw{spec.source_magnitude:.1f} "
                f"at {_centroid(spec)}"
            )
        else:
            driver = f"tsunami staged dtopo {spec.dtopo_file}"
    else:  # surge
        driver = f"surge sea_level offset {spec.sea_level_m:.2f} m (v0.1 fallback)"

    manifest = DeckManifest(
        scenario=spec.scenario,
        bbox=spec.bbox,
        base_num_cells=spec.base_num_cells,
        amr_levels=spec.amr_levels,
        output_frames=spec.output_frames,
        sim_duration_s=spec.sim_duration_s,
        files_written=written,
        driver_descriptor=driver,
    )
    # Persist the manifest alongside the deck for provenance / debugging.
    (deck / "deck_manifest.json").write_text(
        json.dumps(
            {
                "scenario": manifest.scenario,
                "bbox": list(manifest.bbox),
                "base_num_cells": list(manifest.base_num_cells),
                "amr_levels": manifest.amr_levels,
                "output_frames": manifest.output_frames,
                "sim_duration_s": manifest.sim_duration_s,
                "files_written": manifest.files_written,
                "driver_descriptor": manifest.driver_descriptor,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest
