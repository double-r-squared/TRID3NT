"""GeoClaw (Clawpack) deck-build + staging + Batch-dispatch orchestration
(sprint-17 — the GeoClaw analogue of ``run_swmm.py`` / ``run_modflow.py``).

One module owns the GeoClaw engine's solver-dispatch surface. Unlike SWMM (whose
pyswmm runs IN-PROCESS in the agent venv) GeoClaw is a Fortran solver that lives
ONLY in the worker container image (Clawpack compiles its Fortran at install) —
there is NO in-process agent lane. So GeoClaw is BATCH-PRIMARY: the agent stages
a ``build_spec`` (the typed run args) + a topo DEM to S3 and dispatches through
the SAME generic ``run_solver`` / ``wait_for_completion`` seam SFINCS uses, then
downloads the GeoClaw ``fort.q`` frames and postprocesses them.

  1. **build_spec assembly + staging** (``stage_geoclaw_manifest``). Builds the
     worker-contract manifest (``inputs[]`` = the topo DEM + optional dtopo/surge
     forcing; ``build_spec`` = the setrun_builder field dict; ``outputs`` = the
     fort.q globs) and uploads it + the DEM to the cache bucket, returning the
     ``manifest.json`` URI to feed ``run_solver(solver='geoclaw', ...)``.

  2. **GeoClaw solver registration** (``register_geoclaw_solver``). Adds
     ``'geoclaw'`` to ``SOLVER_WORKFLOW_REGISTRY`` (idempotent ``setdefault``,
     mirroring ``register_swmm_solver``) so ``run_solver(solver='geoclaw')``
     dispatches. The orchestrator ALSO pins the registry entry in code (the
     shared-append line this lane returns) so the dispatch works even when this
     module is not imported first.

Determinism boundary (Invariant 1 / 2): no LLM call anywhere in this module. The
deck is authored deterministically (in the worker, via setrun_builder); every
number the agent narrates comes from the typed ``GeoClawDepthLayerURI`` fields the
postprocess computed — never free-generated.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from grace2_contracts import new_ulid
from grace2_contracts.geoclaw_contracts import GeoClawRunArgs

logger = logging.getLogger("grace2_agent.workflows.run_geoclaw")

__all__ = [
    "GeoClawWorkflowError",
    "GeoClawStaging",
    "build_geoclaw_build_spec",
    "stage_geoclaw_manifest",
    "register_geoclaw_solver",
    "plan_geoclaw_domain",
    "resolve_offshore_source",
    "GEOCLAW_SOLVER_NAME",
    "GEOCLAW_OFFSHORE_SCENARIOS",
]

#: Scenarios whose driver source is OFFSHORE (a seafloor Okada deformation) and so
#: REQUIRE the computational domain to extend off the AOI coast into deep water.
#: ``dam_break`` (an onshore impoundment release) + ``surge`` (a uniform sea-level
#: offset, no point source) keep ``domain == AOI``.
GEOCLAW_OFFSHORE_SCENARIOS: frozenset[str] = frozenset({"tsunami"})

#: Elevation (m, positive-up) at/above which a DEM cell is treated as LAND when
#: validating / relocating the Okada source. A source must sit strictly below this
#: (i.e. under water) so the seafloor deformation displaces a real water column.
_SOURCE_WET_ELEV_M: float = 0.0


#: The registry key + handle ``solver`` tag for the GeoClaw engine.
GEOCLAW_SOLVER_NAME: str = "geoclaw"

#: GeoClaw fort.q output globs the postprocess reads (the AMR ASCII frames +
#: their headers + the echoed deck manifest). Kept BYTE-IDENTICAL to the worker
#: entrypoint's output list so the agent + worker agree on the harvested set; the
#: fgmax monitor (fgmax{NNNN}.txt + fgmax_grids.data) + gauge time series
#: (gauge{NNNNN}.txt) ride along for the GAP1 fgmax reader.
GEOCLAW_OUTPUT_GLOBS: list[str] = [
    "_output/fort.q*",
    "_output/fort.t*",
    "_output/fort.h*",
    "_output/fort.b*",
    "_output/fgmax*.txt",
    "_output/fgmax_grids.data",
    "_output/gauge*.txt",
    "deck_manifest.json",
]


# --------------------------------------------------------------------------- #
# Errors (mirrors SWMMWorkflowError shape).
# --------------------------------------------------------------------------- #
class GeoClawWorkflowError(RuntimeError):
    """Raised on any deck-spec / staging / dispatch failure.

    Carries an open-set A.6 ``error_code`` so the agent emitter renders a typed
    error frame. Codes:

    - ``GEOCLAW_PARAMS_INVALID`` — the run args could not be coerced.
    - ``GEOCLAW_STAGING_FAILED`` — the build_spec / DEM upload failed.
    - ``GEOCLAW_RUN_FAILED`` — the Batch solve did not complete.
    - ``GEOCLAW_BATCH_OUTPUT_MISSING`` — a 'complete' solve produced no fort.q.
    """

    error_code: str = "GEOCLAW_WORKFLOW_FAILED"

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
# Staging result — the Batch-lane handoff (mirrors SWMMStaging).
# --------------------------------------------------------------------------- #
@dataclass
class GeoClawStaging:
    """The result of assembling + staging a GeoClaw build_spec + DEM.

    Fields:
        run_id: the run identifier the output COGs are keyed under.
        manifest_uri: the ``s3://`` URI of the staged ``manifest.json``.
        build_spec: the setrun_builder field dict that was staged.
        run_args: the validated ``GeoClawRunArgs`` (echoed for provenance).
        bbox: the AOI the postprocess rasterizes onto.
    """

    run_id: str
    manifest_uri: str
    build_spec: dict[str, Any]
    run_args: GeoClawRunArgs
    bbox: tuple[float, float, float, float]
    n_active_cells: int = 0
    resolution_m: float = 0.0
    staged_inputs: list[dict[str, str]] = field(default_factory=list)
    # The COMPUTATIONAL DOMAIN actually authored into the deck (offshore-extended
    # for a tsunami; == ``bbox`` otherwise). Echoed for provenance / narration.
    domain_bbox: tuple[float, float, float, float] | None = None


# --------------------------------------------------------------------------- #
# build_spec assembly.
# --------------------------------------------------------------------------- #
def build_geoclaw_build_spec(
    run_args: GeoClawRunArgs,
    *,
    topo_dest: str = "topo.asc",
    dtopo_dest: str | None = None,
    surge_dest: str | None = None,
    extra_topo_files: list[str] | None = None,
    base_num_cells: tuple[int, int] = (40, 40),
    domain_bbox: tuple[float, float, float, float] | None = None,
    source_lonlat_override: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Assemble the setrun_builder ``build_spec`` dict from the validated run args.

    The single source of truth for the worker-side deck author's input. Maps the
    typed ``GeoClawRunArgs`` onto the flat build_spec the worker's
    ``setrun_builder.parse_build_spec`` consumes. The staged DEM is referenced by
    its in-deck destination filename (``topo_dest``); a staged dtopo / surge file
    is referenced by ``dtopo_dest`` / ``surge_dest`` when present.

    ``extra_topo_files`` are the staged-destination names of additional topo/bathy
    tiles (ordered coarse -> fine, appended AFTER the primary ``topo_dest`` so the
    worker layers them finest-last). ``fgmax_arrival_tol_m`` always rides along
    (it backs the fgmax wave-arrival monitor); ``coastal_gauge_lonlat`` and the
    four USER-GATED Okada ``fault_*`` keys are threaded ONLY when supplied (the
    engine substitutes scenario defaults otherwise and MUST surface that, never
    silently fabricate them).

    Pure dict assembly — unit-testable with no network.
    """
    spec: dict[str, Any] = {
        "scenario": run_args.scenario,
        "bbox": list(run_args.bbox),
        "topo_file": topo_dest,
        "sim_duration_s": float(run_args.sim_duration_s),
        "output_frames": int(run_args.output_frames),
        "amr_levels": int(run_args.amr_levels),
        "manning_n": float(run_args.manning_n),
        "sea_level_m": float(run_args.sea_level_m),
        "base_num_cells": [int(base_num_cells[0]), int(base_num_cells[1])],
        "source_magnitude": float(run_args.source_magnitude),
        "dam_break_depth_m": float(run_args.dam_break_depth_m),
        "fgmax_arrival_tol_m": float(run_args.fgmax_arrival_tol_m),
    }
    # Source point: a composer-RESOLVED offshore override (tsunami, placed over
    # deep water + spanned by domain_bbox) wins over the user's raw source_lonlat;
    # else the raw source_lonlat; else the worker falls back to the AOI centroid.
    _src = source_lonlat_override or run_args.source_lonlat
    if _src is not None:
        spec["source_lonlat"] = [float(_src[0]), float(_src[1])]
    # The offshore-extended COMPUTATIONAL DOMAIN (clawdata bounds). Only threaded
    # when it differs from the AOI (the worker defaults domain -> AOI otherwise).
    if domain_bbox is not None and tuple(domain_bbox) != tuple(run_args.bbox):
        spec["domain_bbox"] = [float(v) for v in domain_bbox]
    if extra_topo_files:
        spec["extra_topo_files"] = list(extra_topo_files)
    if run_args.coastal_gauge_lonlat is not None:
        spec["coastal_gauge_lonlat"] = [
            float(run_args.coastal_gauge_lonlat[0]),
            float(run_args.coastal_gauge_lonlat[1]),
        ]
    # USER-GATED Okada fault overrides: thread ONLY the ones the user supplied.
    if run_args.fault_strike_deg is not None:
        spec["fault_strike_deg"] = float(run_args.fault_strike_deg)
    if run_args.fault_dip_deg is not None:
        spec["fault_dip_deg"] = float(run_args.fault_dip_deg)
    if run_args.fault_rake_deg is not None:
        spec["fault_rake_deg"] = float(run_args.fault_rake_deg)
    if run_args.fault_depth_km is not None:
        spec["fault_depth_km"] = float(run_args.fault_depth_km)
    if run_args.scenario == "tsunami" and dtopo_dest is not None:
        spec["dtopo_file"] = dtopo_dest
    if run_args.scenario == "surge" and surge_dest is not None:
        spec["surge_forcing_file"] = surge_dest
    return spec


# --------------------------------------------------------------------------- #
# Offshore-domain planning + bathymetry-aware Okada source placement.
#
# The two physics-setup fixes that turn a zero-inundation coastal tsunami run into
# a real run-up (sprint-17 follow-up):
#
#   1. DOMAIN EXTENT. ``plan_geoclaw_domain`` extends the computational domain
#      offshore so it SPANS the Okada source -> the AOI coast. A source-at-center,
#      land-only micro-AOI can never inundate regardless of bathymetry -- the
#      domain MUST reach offshore into the deep-water column the source displaces.
#
#   2. SOURCE PLACEMENT. ``resolve_offshore_source`` honors a user/composer
#      offshore source when it sits over below-waterline bathymetry, else projects
#      the source onto the DEEPEST deep-water cell seaward of the AOI (reading the
#      fetched DEM with rasterio) -- never the onshore AOI centroid.
#
# General (no Crescent-City hard-coding): the domain is sized from the AOI span +
# the requested source, and the source is chosen from the bathymetry of whatever
# coastal AOI was asked for.
# --------------------------------------------------------------------------- #
def plan_geoclaw_domain(
    bbox: tuple[float, float, float, float],
    scenario: str,
    source_lonlat: tuple[float, float] | None,
) -> tuple[float, float, float, float]:
    """Compute the COMPUTATIONAL DOMAIN bbox for a GeoClaw run.

    For an OFFSHORE-source scenario (tsunami) the domain extends off the AOI on
    all sides by at least one AOI span (floored so a small AOI still reaches the
    shelf), and is grown further to enclose an explicit ``source_lonlat`` with a
    buffer so the Okada deformation + a deep-water column sit INSIDE the domain.
    For dam_break / surge the domain is the AOI unchanged.

    Direction-agnostic (pads all sides equally) so it works for any coastal AOI
    regardless of which side the ocean is on; the seaward side is resolved later
    from the bathymetry by ``resolve_offshore_source``. Returns a lon/lat-clamped
    ``(min_lon, min_lat, max_lon, max_lat)``.
    """
    min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox)
    if str(scenario) not in GEOCLAW_OFFSHORE_SCENARIOS:
        return (min_lon, min_lat, max_lon, max_lat)

    span_x = max_lon - min_lon
    span_y = max_lat - min_lat
    # Offshore pad: at least one AOI span on each side, floored to ~0.1 deg
    # (~11 km, comfortably past the surf zone onto the shelf for ETOPO bathy).
    pad = max(span_x, span_y, 0.1)
    d_min_lon = min_lon - pad
    d_min_lat = min_lat - pad
    d_max_lon = max_lon + pad
    d_max_lat = max_lat + pad

    if source_lonlat is not None:
        slon, slat = float(source_lonlat[0]), float(source_lonlat[1])
        buf = max(0.05, 0.25 * pad)
        d_min_lon = min(d_min_lon, slon - buf)
        d_min_lat = min(d_min_lat, slat - buf)
        d_max_lon = max(d_max_lon, slon + buf)
        d_max_lat = max(d_max_lat, slat + buf)

    # Clamp to valid lon/lat (a coastal AOI near the antimeridian/poles still
    # yields a well-formed, in-range domain).
    d_min_lon = max(d_min_lon, -180.0)
    d_max_lon = min(d_max_lon, 180.0)
    d_min_lat = max(d_min_lat, -90.0)
    d_max_lat = min(d_max_lat, 90.0)
    return (d_min_lon, d_min_lat, d_max_lon, d_max_lat)


def _dem_uri_to_local(dem_uri: str) -> tuple[str, bool]:
    """Resolve a DEM URI to a local readable path for rasterio sampling.

    ``file://`` / bare local paths are returned as-is; ``s3://`` is downloaded to
    a temp file via the SAME boto3 client the solver dispatch uses (no new
    client). Returns ``(path, is_temp)`` so the caller can clean up a temp copy.
    Raises on an unreachable / unsupported URI (the caller degrades to a geometric
    fallback).
    """
    import tempfile as _tf

    if dem_uri.startswith("file://"):
        return dem_uri[len("file://"):], False
    if "://" not in dem_uri:
        return dem_uri, False
    if dem_uri.startswith("s3://"):
        from ..tools.solver import _get_s3_client, _split_object_uri

        _scheme, bucket, key = _split_object_uri(dem_uri)
        s3 = _get_s3_client()
        fd, path = _tf.mkstemp(suffix=".tif", prefix="grace2_geoclaw_bathy_")
        os.close(fd)
        resp = s3.get_object(Bucket=bucket, Key=key)
        import shutil as _shutil

        with open(path, "wb") as fh:
            _shutil.copyfileobj(resp["Body"], fh)
        return path, True
    raise GeoClawWorkflowError(
        "GEOCLAW_STAGING_FAILED",
        message=f"cannot sample bathymetry from unsupported DEM URI scheme: {dem_uri!r}",
    )


def resolve_offshore_source(
    dem_uri: str,
    domain_bbox: tuple[float, float, float, float],
    aoi_bbox: tuple[float, float, float, float],
    requested_source: tuple[float, float] | None,
) -> tuple[float, float] | None:
    """Resolve the Okada source to an OFFSHORE, over-deep-water point.

    Reads the fetched topo/bathy DEM (rasterio) over ``domain_bbox`` and:

      1. Honors ``requested_source`` when it falls inside the domain AND over
         below-waterline bathymetry (elevation < ``_SOURCE_WET_ELEV_M``).
      2. Else projects the source onto the DEEPEST deep-water cell, preferring
         cells SEAWARD of the AOI (outside the AOI bbox) and inset off the domain
         boundary (so the source is not on the absorbing edge).

    Returns the resolved ``(lon, lat)``, or ``None`` when the DEM has no
    below-waterline cell in the domain (a fully-dry/inland domain -- the caller
    then keeps the requested source / honest fallback and logs it). Best-effort:
    any read error returns ``None`` rather than raising (the run still proceeds
    with the requested source).
    """
    path = None
    is_temp = False
    try:
        import numpy as np  # noqa: WPS433 - agent venv
        import rasterio  # noqa: WPS433

        path, is_temp = _dem_uri_to_local(dem_uri)
        with rasterio.open(path) as ds:
            band = ds.read(1, masked=True).astype("float64")
            transform = ds.transform
        height, width = band.shape
        if height < 2 or width < 2:
            return None

        cols = np.arange(width)
        rows = np.arange(height)
        lons = transform.c + transform.a * (cols + 0.5)
        lats = transform.f + transform.e * (rows + 0.5)  # transform.e < 0
        lon_grid, lat_grid = np.meshgrid(lons, lats)

        valid = ~np.ma.getmaskarray(band)
        elev = band.filled(1.0e9)
        wet = valid & (elev < _SOURCE_WET_ELEV_M)

        # (1) Honor a requested source that sits over water.
        if requested_source is not None:
            rlon, rlat = float(requested_source[0]), float(requested_source[1])
            col = int((rlon - transform.c) / transform.a)
            row = int((rlat - transform.f) / transform.e)
            if 0 <= row < height and 0 <= col < width and wet[row, col]:
                return (rlon, rlat)

        if not wet.any():
            return None

        # (2) Inset off the domain boundary (avoid the absorbing edge).
        d_min_lon, d_min_lat, d_max_lon, d_max_lat = (float(v) for v in domain_bbox)
        ix = 0.08 * (d_max_lon - d_min_lon)
        iy = 0.08 * (d_max_lat - d_min_lat)
        inset = (
            (lon_grid > d_min_lon + ix)
            & (lon_grid < d_max_lon - ix)
            & (lat_grid > d_min_lat + iy)
            & (lat_grid < d_max_lat - iy)
        )
        a_min_lon, a_min_lat, a_max_lon, a_max_lat = (float(v) for v in aoi_bbox)
        outside_aoi = ~(
            (lon_grid >= a_min_lon)
            & (lon_grid <= a_max_lon)
            & (lat_grid >= a_min_lat)
            & (lat_grid <= a_max_lat)
        )

        def _deepest(mask: "np.ndarray") -> tuple[float, float] | None:
            if not mask.any():
                return None
            masked_elev = np.where(mask, elev, 1.0e9)
            idx = np.unravel_index(int(np.argmin(masked_elev)), masked_elev.shape)
            return (float(lon_grid[idx]), float(lat_grid[idx]))

        # Prefer deep water SEAWARD of the AOI, inset off the boundary; then any
        # inset deep water; then any deep water at all.
        for mask in (wet & inset & outside_aoi, wet & inset, wet):
            pt = _deepest(mask)
            if pt is not None:
                return pt
        return None
    except Exception as exc:  # noqa: BLE001 - best-effort; degrade to fallback
        logger.warning(
            "resolve_offshore_source: bathymetry sampling failed (%s); keeping "
            "the requested source",
            exc,
        )
        return None
    finally:
        if is_temp and path:
            try:
                os.unlink(path)
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# Staging — upload the build_spec manifest + the topo DEM to S3.
# --------------------------------------------------------------------------- #
def stage_geoclaw_manifest(
    run_args: GeoClawRunArgs,
    *,
    dem_uri: str,
    run_id: str | None = None,
    dtopo_uri: str | None = None,
    surge_uri: str | None = None,
    extra_dem_uris: list[str] | None = None,
    base_num_cells: tuple[int, int] = (40, 40),
    domain_bbox: tuple[float, float, float, float] | None = None,
    source_lonlat_override: tuple[float, float] | None = None,
) -> GeoClawStaging:
    """Stage the GeoClaw ``manifest.json`` (build_spec + input refs) to S3.

    The GeoClaw analogue of ``stage_swmm_manifest``. Mirrors that path EXACTLY
    (no new client): the same ``cache.storage_scheme()`` scheme + the same
    ``tools.solver._get_s3_client()`` boto3 client + the same
    ``GRACE2_CACHE_BUCKET`` staging bucket the SWMM/SFINCS decks upload to.

    The worker downloads the topo DEM (and optional dtopo / surge) listed in
    ``inputs[]`` BY SCHEME and authors the deck from ``build_spec``. ``dem_uri``
    is a cache/runs ``s3://`` URI produced by ``fetch_topobathy`` / ``fetch_dem``
    upstream (it is staged BY REFERENCE — the worker downloads it directly — so we
    do not re-upload the DEM bytes here, only point at them).

    Args:
        run_args: the validated ``GeoClawRunArgs``.
        dem_uri: the ``s3://`` URI of the topo/bathy DEM (ESRI-ASCII topotype-3
            preferred; the worker references it as ``topo.asc``).
        run_id: optional ULID; minted if absent.
        dtopo_uri: optional ``s3://`` URI of a staged dtopo (tsunami scenario).
        surge_uri: optional ``s3://`` URI of a staged surge hydrograph CSV.
        extra_dem_uris: optional ordered (coarse -> fine) list of additional
            topo/bathy DEM ``s3://`` URIs; each is staged BY REFERENCE as
            ``topo_extra_{i}.asc`` and threaded into the build_spec after the
            primary topo so the worker layers them finest-last.
        base_num_cells: the GeoClaw base computational-grid resolution.

    Returns:
        ``GeoClawStaging`` carrying the manifest URI + the build_spec + bbox.

    Raises:
        GeoClawWorkflowError("GEOCLAW_STAGING_FAILED"): the upload could not
            complete (the Batch lane cannot dispatch without a reachable
            manifest — fail loudly, never a silent dead-end).
    """
    from ..tools.cache import storage_scheme
    from ..tools.solver import _get_s3_client

    rid = run_id or new_ulid()
    bbox = tuple(run_args.bbox)

    # Stage the DEM BY REFERENCE; the worker downloads it as topo.asc.
    inputs: list[dict[str, str]] = [{"gs_uri": dem_uri, "dest": "topo.asc"}]
    dtopo_dest: str | None = None
    surge_dest: str | None = None
    # Additional topo/bathy tiles (ordered coarse -> fine) staged BY REFERENCE.
    extra_topo_files: list[str] = []
    for i, uri in enumerate(extra_dem_uris or []):
        if not uri:
            continue
        dest = f"topo_extra_{i}.asc"
        inputs.append({"gs_uri": str(uri), "dest": dest})
        extra_topo_files.append(dest)
    if run_args.scenario == "tsunami" and dtopo_uri:
        dtopo_dest = "dtopo.tt3"
        inputs.append({"gs_uri": dtopo_uri, "dest": dtopo_dest})
    if run_args.scenario == "surge" and surge_uri:
        surge_dest = "surge.csv"
        inputs.append({"gs_uri": surge_uri, "dest": surge_dest})

    build_spec = build_geoclaw_build_spec(
        run_args,
        topo_dest="topo.asc",
        dtopo_dest=dtopo_dest,
        surge_dest=surge_dest,
        extra_topo_files=extra_topo_files,
        base_num_cells=base_num_cells,
        domain_bbox=domain_bbox,
        source_lonlat_override=source_lonlat_override,
    )

    manifest_dict: dict[str, Any] = {
        "inputs": inputs,
        "build_spec": build_spec,
        "outputs": list(GEOCLAW_OUTPUT_GLOBS),
    }

    scheme = storage_scheme()  # "s3" on AWS (GCP decommissioned)
    cache_bucket = os.environ.get("GRACE2_CACHE_BUCKET", "grace-2-hazard-prod-cache")
    prefix = f"cache/static-30d/geoclaw_setup/{rid}/"
    manifest_key = f"{prefix}manifest.json"
    manifest_uri = f"{scheme}://{cache_bucket}/{manifest_key}"

    try:
        s3 = _get_s3_client()
        s3.put_object(
            Bucket=cache_bucket,
            Key=manifest_key,
            Body=json.dumps(manifest_dict, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception as exc:  # noqa: BLE001
        raise GeoClawWorkflowError(
            "GEOCLAW_STAGING_FAILED",
            message=f"failed to stage GeoClaw manifest to {manifest_uri}: {exc}",
            details={"run_id": rid, "manifest_uri": manifest_uri},
        ) from exc

    logger.info(
        "stage_geoclaw_manifest run_id=%s scenario=%s dem=%s -> manifest=%s",
        rid,
        run_args.scenario,
        dem_uri,
        manifest_uri,
    )
    # n_active_cells used only for telemetry + compute-class sizing; the base grid
    # cell count is a coarse proxy (AMR refines it dynamically downstream).
    n_active = int(base_num_cells[0]) * int(base_num_cells[1])
    _dom = build_spec.get("domain_bbox")
    return GeoClawStaging(
        run_id=rid,
        manifest_uri=manifest_uri,
        build_spec=build_spec,
        run_args=run_args,
        bbox=bbox,  # type: ignore[arg-type]
        n_active_cells=n_active,
        staged_inputs=inputs,
        domain_bbox=(tuple(_dom) if _dom else None),  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# GeoClaw solver registration (mirrors register_swmm_solver).
# --------------------------------------------------------------------------- #
def register_geoclaw_solver() -> None:
    """Register ``'geoclaw'`` in ``tools.solver.SOLVER_WORKFLOW_REGISTRY``.

    Mirrors ``register_swmm_solver``. GeoClaw is Batch-only (the Fortran lives in
    the worker image, never in the agent venv), so it maps to the AWS-Batch
    workflow-name sentinel. ``run_solver`` only requires the KEY to be present to
    dispatch (the backend seam routes to ``_run_solver_aws_batch``, and the
    per-solver job-def is resolved from ``GRACE2_AWS_BATCH_JOB_DEF_GEOCLAW``).
    Idempotent ``setdefault`` — safe to call at import. The orchestrator ALSO
    pins this in code via the shared-append line so dispatch works regardless of
    import order.
    """
    from ..tools.solver import AWS_BATCH_WORKFLOW_NAME, SOLVER_WORKFLOW_REGISTRY

    SOLVER_WORKFLOW_REGISTRY.setdefault(GEOCLAW_SOLVER_NAME, AWS_BATCH_WORKFLOW_NAME)


# Register at import so ``run_solver(solver='geoclaw')`` is wired wherever this
# module is imported (the composer + the tool wrapper both import it).
register_geoclaw_solver()
