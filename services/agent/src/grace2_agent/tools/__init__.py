"""Atomic-tool registry skeleton (FR-AS-3, FR-CE-8, FR-TA-2, Decision O).

This package is the agent-service-owned surface for atomic tools (M4 substrate).
``schema`` owns ``AtomicToolMetadata`` (in ``grace2_contracts.tool_registry``);
``agent`` owns the registry that collects the decorated functions at import
time and the cache shim that mediates external-API calls (see ``.cache``).
Pass-through tools (``mongo_query``, ``qgis_process``) live in ``.passthroughs``.

How registration works:

    from grace2_contracts.tool_registry import AtomicToolMetadata
    from grace2_agent.tools import register_tool

    @register_tool(AtomicToolMetadata(
        name="fetch_dem",
        ttl_class="static-30d",
        source_class="dem",
        cacheable=True,
    ))
    def fetch_dem(bbox: BBox) -> str:
        ...

The ``@register_tool`` decorator:

- Re-validates the metadata payload (pydantic auto-validates at construction;
  passing an already-validated model just stores it) and refuses to register
  a tool whose metadata fails the FR-DC-6 cross-field rule.
- Stores ``(fn, metadata, module)`` in module-level ``TOOL_REGISTRY``
  keyed by ``metadata.name``.
- **Fails fast on duplicate names** per FR-CE-8: a second registration under
  the same name raises ``ToolRegistrationError`` at import time so the
  agent service cannot start with an inconsistent tool surface.
- Returns the original function unchanged so direct-call testing is trivial.

The ``get_registered_tools()`` helper returns the current registry contents
(a snapshot list) for the agent service's startup-time ADK FunctionTool
registration; ``register_with_adk(agent)`` is the convenience wrapper that
iterates the snapshot and calls ``agent.tools.append(FunctionTool(...))``
(or whatever the ADK API of the day is — kept thin so ADK churn is contained
to this one site).

Importing the package triggers ``@register_tool`` decorators in submodules
(``.passthroughs`` for M4 job-0032; ``.fetchers`` etc. for M4 job-0033+).
We import them eagerly here so any registration-time ``ValidationError`` or
``ToolRegistrationError`` surfaces at startup (FR-CE-8 fail-fast).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from grace2_contracts.tool_registry import AtomicToolMetadata

__all__ = [
    "RegisteredTool",
    "ToolRegistrationError",
    "TOOL_REGISTRY",
    "register_tool",
    "get_registered_tools",
    "clear_registry_for_tests",
    "register_with_adk",
]


class ToolRegistrationError(RuntimeError):
    """Raised when a tool fails registration (duplicate name, bad metadata)."""


@dataclass(frozen=True)
class RegisteredTool:
    """A tool entry in ``TOOL_REGISTRY``.

    Fields:
    - ``metadata`` — the validated ``AtomicToolMetadata`` for the tool.
    - ``fn`` — the original (undecorated) callable. The registry deliberately
      does NOT wrap it; tests call the function directly via this attribute.
    - ``module`` — the ``__module__`` attribute at registration time, useful
      for diagnostics (`"grace2_agent.tools.passthroughs"` etc.).
    """

    metadata: AtomicToolMetadata
    fn: Callable[..., Any]
    module: str


#: Module-level registry, keyed by ``metadata.name``. Populated at import time
#: by ``@register_tool`` calls in submodules. The agent service iterates this
#: at startup to register each tool with ADK (see ``register_with_adk``).
TOOL_REGISTRY: dict[str, RegisteredTool] = {}


def register_tool(
    metadata: AtomicToolMetadata,
    *,
    supports_global_query: bool | None = None,
    payload_mb_estimator_name: str | None = None,
    read_only_hint: bool | None = None,
    open_world_hint: bool | None = None,
    destructive_hint: bool | None = None,
    idempotent_hint: bool | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Return a decorator that records ``fn`` + ``metadata`` in ``TOOL_REGISTRY``.

    Usage::

        @register_tool(AtomicToolMetadata(name="x", ttl_class="static-30d",
                                          source_class="x"))
        def x(...): ...

    Wave 1.5 (job-0114) added two metadata flags. They may be set either
    on the constructed ``AtomicToolMetadata`` directly OR passed as
    decorator-level kwargs (kwargs win and produce a new metadata via
    ``model_copy(update=...)``)::

        @register_tool(_BASE_META, supports_global_query=True)
        def fetch_nws_alerts_conus(bbox=None): ...

    Wave 4.10 (job-B12) added four MCP annotation hints as decorator-level
    kwargs using the same pattern::

        @register_tool(_BASE_META, read_only_hint=True, open_world_hint=True,
                       destructive_hint=False, idempotent_hint=True)
        def fetch_dem(bbox): ...

    All kwargs default to ``None`` meaning "use whatever the metadata
    already declares" — the kwarg path is a convenience for tool authors
    who want the decorator site to be the single visible declaration of
    the flag. Backward-compatible: existing tools that pre-date the
    kwargs continue to work; the metadata defaults
    (``supports_global_query=False``, ``payload_mb_estimator_name=None``,
    ``read_only_hint=True``, ``open_world_hint=False``,
    ``destructive_hint=False``, ``idempotent_hint=True``)
    preserve pre-Wave-4.10 behaviour.

    Fail-fast invariants (FR-CE-8):

    - ``metadata`` must already be a valid ``AtomicToolMetadata`` (pydantic
      auto-validates at construction, including the FR-DC-6 cross-field
      ``cacheable``/``ttl_class``/``source_class`` rule). Passing anything
      else raises ``TypeError``.
    - The same ``metadata.name`` cannot register twice. A duplicate raises
      ``ToolRegistrationError`` at import time so a misconfigured agent
      service never starts.
    - The original ``fn`` is returned UNCHANGED, so callers can both register
      a tool and call it directly in tests.
    """
    if not isinstance(metadata, AtomicToolMetadata):
        raise TypeError(
            f"register_tool expects AtomicToolMetadata, got {type(metadata).__name__}"
        )

    # If the caller passed Wave-1.5 / Wave-4.10 flags at the decorator level,
    # fold them into a fresh metadata. ``model_copy(update=...)`` re-runs
    # validators because pydantic v2 ``GraceModel`` has
    # ``validate_assignment=True``, so a bad combination still fails fast at
    # import time.
    overrides: dict[str, Any] = {}
    if supports_global_query is not None:
        overrides["supports_global_query"] = supports_global_query
    if payload_mb_estimator_name is not None:
        overrides["payload_mb_estimator_name"] = payload_mb_estimator_name
    if read_only_hint is not None:
        overrides["read_only_hint"] = read_only_hint
    if open_world_hint is not None:
        overrides["open_world_hint"] = open_world_hint
    if destructive_hint is not None:
        overrides["destructive_hint"] = destructive_hint
    if idempotent_hint is not None:
        overrides["idempotent_hint"] = idempotent_hint
    if overrides:
        metadata = metadata.model_copy(update=overrides)

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        name = metadata.name
        existing = TOOL_REGISTRY.get(name)
        if existing is not None:
            raise ToolRegistrationError(
                f"tool {name!r} is already registered "
                f"(existing from module {existing.module!r}, "
                f"new from module {fn.__module__!r}); duplicate registrations "
                f"are rejected at import time per FR-CE-8."
            )
        TOOL_REGISTRY[name] = RegisteredTool(
            metadata=metadata, fn=fn, module=fn.__module__
        )
        return fn

    return _decorator


def get_registered_tools() -> list[RegisteredTool]:
    """Return a stable-ordered snapshot of the current registry.

    Used by the agent service at startup to register each tool with ADK.
    Sorted by ``metadata.name`` so the registration order is deterministic
    across runs (important for FR-AS-3 review diffs).
    """
    return sorted(TOOL_REGISTRY.values(), key=lambda t: t.metadata.name)


def clear_registry_for_tests() -> None:
    """Empty the registry. ONLY for tests; never call from product code.

    Atomic-tool registration is import-time; tests that need a fresh registry
    or want to swap implementations call this in a fixture.
    """
    TOOL_REGISTRY.clear()


def register_with_adk(agent: Any) -> int:
    """Register every tool in ``TOOL_REGISTRY`` with an ADK ``Agent`` instance.

    The actual ADK API for adding a function tool varies across releases
    (``Agent.tools.append(FunctionTool(fn))`` or ``agent.add_tool(...)``); we
    keep the import + adaptation contained here so future ADK churn is a
    single-site edit, not a registry-wide refactor.

    Returns the number of tools registered. Raises ``ToolRegistrationError``
    if the ADK import path cannot be resolved, so a misconfigured runtime
    fails at startup rather than silently exposing zero tools.
    """
    try:
        from google.adk.tools import FunctionTool  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001 — surface as ToolRegistrationError
        raise ToolRegistrationError(
            f"could not import google.adk.tools.FunctionTool: {exc}"
        ) from exc

    snapshot = get_registered_tools()
    for tool in snapshot:
        # ADK FunctionTool wraps a Python callable; the description / param
        # surface is derived from the function's signature + docstring per
        # FR-AS-3. Docstring discipline (Use this when / Do NOT use this for)
        # is enforced on the source functions themselves, not here.
        ft = FunctionTool(tool.fn)
        # ADK Agent objects expose ``tools`` as a mutable list in google-adk
        # 2.x; keep this thin and let the duck-type fail loudly if it doesn't.
        agent.tools.append(ft)
    return len(snapshot)


# ---------------------------------------------------------------------------
# Eager submodule import (FR-CE-8 fail-fast).
#
# Importing ``grace2_agent.tools`` should populate ``TOOL_REGISTRY`` with
# every atomic tool the agent service supports. Submodules are imported here
# so their import-time ``@register_tool`` calls fire even if no other code
# references them. Keep this list narrow: only submodules whose tools should
# always be available at startup belong here.
# ---------------------------------------------------------------------------
from . import passthroughs  # noqa: E402,F401 — registers mongo_query, qgis_process
from . import compute_colored_relief  # noqa: E402,F401 — job-0080: registers compute_colored_relief
from . import compute_slope  # noqa: E402,F401 — job-0081: registers compute_slope
from . import compute_aspect  # noqa: E402,F401 — job-0082: registers compute_aspect
from . import compute_zonal_statistics  # noqa: E402,F401 — job-0083: registers compute_zonal_statistics
from . import compute_layer_bounds  # noqa: E402,F401 — NATE 2026-06-17: registers compute_layer_bounds (fast layer-extent + fit-the-map; replaces sandbox bbox math + drives zoom-to)
from . import clip_raster_to_bbox  # noqa: E402,F401 — job-0085: registers clip_raster_to_bbox
from . import clip_raster_to_polygon  # noqa: E402,F401 — job-0106: registers clip_raster_to_polygon
from . import fetch_administrative_boundaries  # noqa: E402,F401 — job-0084: registers fetch_administrative_boundaries
from . import compute_hillshade  # noqa: E402,F401 — job-0079: registers compute_hillshade
from . import compute_blended_composite  # noqa: E402,F401 — job-0319: registers compute_blended_composite (server-side raster multiply-blend → one shaded COG; MapLibre can't multiply on the client)
from . import compute_contours  # noqa: E402,F401 — F35: registers compute_contours (elevation contour LINES from a DEM via GDAL gdal_contour; vector LineStrings with an 'elev' attr → inline-GeoJSON line layer; pairs with fetch_dem + compute_hillshade)
from . import fetch_wdpa_protected_areas  # noqa: E402,F401 — job-0089: registers fetch_wdpa_protected_areas
from . import fetch_gbif_occurrences  # noqa: E402,F401 — job-0087: registers fetch_gbif_occurrences
from . import fetch_inaturalist_observations  # noqa: E402,F401 — job-0088: registers fetch_inaturalist_observations
from . import web_fetch  # noqa: E402,F401 — job-0092: registers web_fetch
from . import fetch_storm_events_db  # noqa: E402,F401 — job-0091: registers fetch_storm_events_db
from . import fetch_nws_event  # noqa: E402,F401 — job-0090: registers fetch_nws_event
from . import fetch_nws_alerts_conus  # noqa: E402,F401 — job-0105: registers fetch_nws_alerts_conus (CONUS-wide companion to fetch_nws_event)
from . import aggregate_claims_across_sources  # noqa: E402,F401 — job-0093: registers aggregate_claims_across_sources
from . import extract_landcover_class  # noqa: E402,F401 — job-0094: registers extract_landcover_class
from . import compute_impervious_surface  # noqa: E402,F401 — job-0095: registers compute_impervious_surface
from . import compute_building_density  # noqa: E402,F401 — job-0096: registers compute_building_density
from . import fetch_roads_osm  # noqa: E402,F401 — job-0097: registers fetch_roads_osm
from . import fetch_field_boundaries  # noqa: E402,F401 — NATE 2026-06-17: registers fetch_field_boundaries (agricultural field-boundary vectors from Fields of The World / fiboa published GeoParquet on Source Cooperative; CRS-aware bbox pushdown over HTTP range requests; inline-GeoJSON vector like roads/WDPA; FIELDS_NO_COVERAGE outside benchmark regions; on-demand global inference is a future tool)
from . import run_pelicun_damage_assessment  # noqa: E402,F401 — job-0098: registers run_pelicun_damage_assessment (Wave 1 stub; Wave 2 composer is job-0106)
from . import postprocess_pelicun  # noqa: E402,F401 — Wave 4.11 P2: registers postprocess_pelicun (aggregates Pelicun per-asset FGB → ImpactEnvelope)
from ..workflows import compute_impact_envelope as _compute_impact_envelope_workflow  # noqa: E402,F401 — Wave 4.11 P3: registers compute_impact_envelope (composes NSI/MS → Pelicun → postprocess into one envelope tool)
from . import clip_vector_to_polygon  # noqa: E402,F401 — job-0107: registers clip_vector_to_polygon
from . import fetch_goes_satellite  # noqa: E402,F401 — job-0104: registers fetch_goes_satellite (GOES-16/17/18/19 satellite imagery)
from . import fetch_nexrad_reflectivity  # noqa: E402,F401 — job-0102: registers fetch_nexrad_reflectivity (Iowa Mesonet NEXRAD WMS passthrough)
from . import fetch_mrms_qpe  # noqa: E402,F401 — job-0103: registers fetch_mrms_qpe (NOAA MRMS gauge-corrected QPE)
from . import fetch_hrsl_population  # noqa: E402,F401 — job-0112: registers fetch_hrsl_population (Meta + CIESIN HRSL persons/cell, COG via global VRT)
from . import fetch_firms_active_fire  # noqa: E402,F401 — job-0108: registers fetch_firms_active_fire (NASA FIRMS VIIRS/MODIS active-fire detections)
from . import fetch_landfire_fuels  # noqa: E402,F401 — job-0111: registers fetch_landfire_fuels (LANDFIRE LF2022 fuels & canopy rasters)
from . import fetch_gcn250_curve_numbers  # noqa: E402,F401 — job-0113: registers fetch_gcn250_curve_numbers (GCN250 global SCS curve numbers, Figshare AMC-I/II/III)
from . import fetch_mtbs_burn_severity  # noqa: E402,F401 — job-0109: registers fetch_mtbs_burn_severity (MTBS burn-severity polygons)
from . import fetch_nifc_fire_perimeters  # noqa: E402,F401 — job-0110: registers fetch_nifc_fire_perimeters (NIFC active wildfire perimeters)
from . import fetch_ebird_observations  # noqa: E402,F401 — job-0128: registers fetch_ebird_observations (Cornell Lab eBird Tier-2 recent sightings)
from . import fetch_iucn_red_list_range  # noqa: E402,F401 — job-0129: registers fetch_iucn_red_list_range (IUCN Red List Tier-2 species range info fetcher)
from . import fetch_movebank_tracks  # noqa: E402,F401 — job-0130: registers fetch_movebank_tracks (Movebank Tier-2 animal-tracking trajectories)

from . import fetch_era5_reanalysis  # noqa: E402,F401 — job-0131: registers fetch_era5_reanalysis (Copernicus ERA5 reanalysis Tier-2 fetcher; compound-flood global substrate)
from . import fetch_gtsm_tide_surge  # noqa: E402,F401 — job-0132: registers fetch_gtsm_tide_surge (GTSM v3.0 Tier-2 coastal water-level via CDS; compound-flood coastal boundary)
from . import fetch_cama_flood_discharge  # noqa: E402,F401 — job-0133: registers fetch_cama_flood_discharge (CaMa-Flood global river discharge Tier-2 fetcher; compound-flood fluvial forcing)
from . import fetch_usace_nsi  # noqa: E402,F401 — job-A6: registers fetch_usace_nsi (USACE National Structure Inventory; preferred Pelicun assets in CONUS)
from . import fetch_fema_nfhl_zones  # noqa: E402,F401 — job-A1: registers fetch_fema_nfhl_zones (FEMA National Flood Hazard Layer regulatory flood-zone polygons; ArcGIS REST MapServer/28)
from . import fetch_usace_levees  # noqa: E402,F401 — job-A4: registers fetch_usace_levees (USACE National Levee Database critical-infrastructure polygons/lines; ArcGIS REST FeatureServer)
from . import fetch_noaa_nwm_streamflow  # noqa: E402,F401 — job-A3 (Wave 4.10): registers fetch_noaa_nwm_streamflow (NOAA National Water Model streamflow; CONUS fluvial forcing via NHDPlus reaches)
from . import fetch_usgs_nwis_gauges  # noqa: E402,F401 — job-0332 (NATE 2026-06-17): registers fetch_usgs_nwis_gauges (REAL observed USGS NWIS/Water Services stream gauges + latest discharge/stage; the gap NATE hit when the agent fell back to MODELED NWM reach flow — distinct from fetch_noaa_nwm_streamflow; stateCd-or-bbox spatial selector with the ~25 deg^2 bBox guard; IV→Site fallback→typed error)
from . import fetch_hrrr_forecast  # noqa: E402,F401 — job-A2 (Wave 4.10): registers fetch_hrrr_forecast (NOAA HRRR 3km hourly CONUS short-term weather forecast via U.Utah HRRR-Zarr S3 mirror)
from . import fetch_hrrr_smoke  # noqa: E402,F401 — job-A13 (Wave 4.10): registers fetch_hrrr_smoke (NOAA HRRR-Smoke smoke/aerosol forecast via U.Utah HRRR-Zarr S3 mirror; pairs with NIFC fire perimeters for air-quality demo)
from . import fetch_asos_metar  # noqa: E402,F401 — job-A7 (Wave 4.10): registers fetch_asos_metar (Iowa State IEM ASOS/METAR hourly surface observations; station weather obs for hazard context)
from . import fetch_gridmet  # noqa: E402,F401 — job-A8 (Wave 4.10): registers fetch_gridmet (gridMET CONUS daily 4 km meteorology via NKN THREDDS OPeNDAP; fire-weather + drought substrate)
from . import fetch_noaa_coops_tides  # noqa: E402,F401 — job-A9 (Wave 4.10): registers fetch_noaa_coops_tides (NOAA CO-OPS tide-station water-level observations + predictions; SFINCS coastal boundary forcing for US/territory basins)
from . import fetch_usace_dams  # noqa: E402,F401 — job-A5 (Wave 4.10): registers fetch_usace_dams (USACE National Inventory of Dams point inventory via public ESRI Living Atlas mirror; dam-break / hazard-overlay substrate)
from . import fetch_noaa_slr_scenarios  # noqa: E402,F401 — job-A10 (Wave 4.10): registers fetch_noaa_slr_scenarios (NOAA OCM SLR Viewer bathtub inundation polygons for 0–10 ft scenarios; CONUS coastal planning-level overlay)
from . import fetch_usfs_canopy_fuels  # noqa: E402,F401 — job-A14 (Wave 4.10): registers fetch_usfs_canopy_fuels (USFS LANDFIRE LF2022 canopy base height + bulk density rasters; crown-fire model inputs CBH/CBD)
from . import fetch_statsgo_soils  # noqa: E402,F401 — job-A11 (Wave 4.10): registers fetch_statsgo_soils (USGS STATSGO COG collection — KFFACT / THICK — via pfdf.data.usgs.statsgo; post-fire debris-flow + runoff-CN substrate)
from . import fetch_nhdplus_nldi_navigate  # noqa: E402,F401 — job-A11 (Wave 4.10): registers fetch_nhdplus_nldi_navigate (USGS NLDI navigate over the NHDPlus v2.1 channel network — UM / UT / DM / DD traversal from a seed point or COMID)
from . import fetch_raws_weather  # noqa: E402,F401 — job-A12 (Wave 4.10): registers fetch_raws_weather (Iowa Mesonet IEM RAWS fire-weather station observations; wind/RH/temp/solar for wildfire hazard context + fire-behavior model forcing)
from . import fetch_3dep_extra  # noqa: E402,F401 — job-A11 (Wave 4.10): registers fetch_3dep_extra (USGS 3DEP non-default resolutions via pfdf.data.usgs.tnm.dem — 1 arc-sec / 1/9 arc-sec / 1 m / 2 arc-sec / 5 m)
from . import discover_dataset  # noqa: E402,F401 — job-B7 (Wave 4.10 Stage 2): registers discover_dataset (hybrid BM25 + dense retrieval over audited docstrings + tool_query_corpus.yaml; routes free-text user queries to top-k atomic tools via RRF fusion; hot-set tool surfaced by B5 per-turn filter)
from . import analytical_qa  # noqa: E402,F401 — job-0224 (sprint-13 Stage 1): registers summarize_layer_statistics + count_features_above_threshold + aggregate_property_within_zone
from . import chart_tools  # noqa: E402,F401 — job-0230 (sprint-13 Stage 2): registers generate_histogram + generate_choropleth_legend + generate_time_series + generate_damage_distribution
from . import run_modflow_tool  # noqa: E402,F401 — job-0227 (sprint-13 Stage 2): registers run_modflow_job (MODFLOW 6 + MF6-GWT groundwater-plume engine; Cloud Workflows + local mf6 modes)
from . import code_exec_tool  # noqa: E402,F401 — job-0233 (sprint-13 Stage 2): registers code_exec_request (user-confirmed Python sandbox; conversational data-analysis escape hatch)

# job-B5 (Wave 4.10 Stage 2): the 12-category registry + the two meta-tools
# (``list_categories`` + ``list_tools_in_category``) live alongside the rest
# of the tool surface. Importing the module fires its two ``@register_tool``
# decorators so the meta-tools are in TOOL_REGISTRY at startup; the hot set,
# allowed-set tracker, and post-hoc validator are exposed through
# ``grace2_agent.categories`` for the server.py dispatch loop.
from .. import categories as _categories  # noqa: E402,F401
