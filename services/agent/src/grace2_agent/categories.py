"""12-category tool registry + post-hoc allowed-set validator (Wave 4.10 job-B5).

This module implements the Wave 4.10 CachedContent Option A architecture from
``project_wave_4_10_research_findings.md``:

- The agent caches the FULL tool catalog via Gemini ``CachedContent.tools[]``
  at session start. Per-turn ``allowed_function_names`` cannot be passed
  alongside ``cached_content`` (Vertex 400s on that combination), so the
  "allowed set" enforcement happens **in our code, not in Gemini's request**.

- Every Gemini-emitted ``function_call`` is validated against the current turn's
  per-session ``AllowedToolSet`` BEFORE dispatch. If the call name IS a real
  registered tool (present in ``TOOL_REGISTRY``) but outside the allowed set,
  the validator AUTO-WIDENS the set with that name and lets the dispatch
  proceed (job-0270 — Gemini saw the full catalog via CachedContent, so a
  registry-valid call is correct routing, not a hallucination). Only names
  that do NOT exist in the registry raise ``OutOfAllowedSetError`` — a typed
  exception with ``error_code='OUT_OF_ALLOWED_SET'`` and ``retryable=False``.
  ``summarize_tool_result`` in ``adapter.py`` then renders this as the
  canonical Wave 4.9 ``{status: error, error_code, retryable, message}``
  envelope, which Gemini reads on its next turn and retries (per Wave 4.9
  retry-on-failure).

Twelve categories (from ``project_generic_endpoint_architecture.md``):

1. ``hazard_modeling`` — SFINCS, MODFLOW (future), Pelicun
2. ``weather_atmosphere`` — NWS, NEXRAD, MRMS, HRRR, GOES, ERA5, ASOS, RAWS,
   gridMET, HRRR-Smoke
3. ``hydrology`` — NWM, NHDPlus, river geometry, precip return period, STATSGO
4. ``terrain_elevation`` — DEM, hillshade, slope, aspect, colored relief,
   LANDFIRE
5. ``land_cover_development`` — NLCD, building density, impervious, HRSL/MS
   Buildings, OSM roads, USACE NSI
6. ``conservation_ecology`` — GBIF, iNat, WDPA, IUCN, eBird, Movebank
7. ``fire`` — FIRMS, MTBS, NIFC, LANDFIRE fuels, USFS canopy
8. ``coastal`` — GTSM, CO-OPS tides, SLR scenarios, bathymetry
9. ``damage_assessment`` — Pelicun, USACE NSI (cross-listed)
10. ``flood_infrastructure`` — FEMA NFHL, USACE NLD (levees), USACE NID (dams)
11. ``geographic_primitives`` — geocode, administrative boundaries, clip,
    publish, discovery, catalog
12. ``news_events`` — web_fetch, NWS event, storm events, aggregate claims

The hot set (always-on at session start, before any category has been opened)
is defined in ``HOT_SET_TOOLS`` — ten tools that span the most common entry
points to a session: the two top-level workflow composers, geocoding, terrain,
weather alerts (CONUS sweep + state/county-scoped — job-0261), code-exec
(job-0247), and the meta-tools (list_categories, list_tools_in_category,
discover_dataset).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Iterable

from grace2_contracts.tool_registry import AtomicToolMetadata

from .tools import register_tool

logger = logging.getLogger(__name__)

__all__ = [
    "CATEGORIES",
    "CategorySpec",
    "PRIMARY_CATEGORY",
    "SECONDARY_CATEGORIES",
    "HOT_SET_TOOLS",
    "AllowedToolSet",
    "OutOfAllowedSetError",
    "UnknownCategoryError",
    "validate_function_call",
    "list_categories",
    "list_tools_in_category",
    "tools_for_category",
]


# ---------------------------------------------------------------------------
# Category specifications.
#
# Each entry primes Gemini for category-aware routing — the ``description`` is
# what Gemini sees when it calls ``list_categories()``. Keep it crisp (one or
# two short sentences); the goal is to disambiguate this category from the
# others so the LLM picks the right ``list_tools_in_category()`` arg.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategorySpec:
    """One of the 12 top-level tool categories.

    Fields:

    - ``id`` — short stable identifier (e.g. ``"hazard_modeling"``). This is
      the argument the LLM passes to ``list_tools_in_category``.
    - ``name`` — human-readable name shown in the catalog UI / system prompt.
    - ``description`` — one-sentence priming for the LLM. Tells it when this
      category is the right one to open.
    """

    id: str
    name: str
    description: str


CATEGORIES: tuple[CategorySpec, ...] = (
    CategorySpec(
        id="hazard_modeling",
        name="Hazard modeling",
        description=(
            "End-to-end hazard simulation workflows: flood (SFINCS), "
            "groundwater contamination plume (MODFLOW 6 + MF6-GWT), flood "
            "+ habitat composers, Pelicun damage assessment. Use this when the "
            "user wants to RUN a model, not just fetch source data."
        ),
    ),
    CategorySpec(
        id="weather_atmosphere",
        name="Weather and atmosphere",
        description=(
            "Active weather alerts, radar, precipitation, forecasts, and "
            "reanalysis. Covers NWS alerts/events, NEXRAD, MRMS QPE, HRRR, "
            "GOES satellite, ERA5, ASOS/METAR, RAWS, gridMET, HRRR-Smoke."
        ),
    ),
    CategorySpec(
        id="hydrology",
        name="Hydrology",
        description=(
            "Surface-water datasets: USGS NWIS gauge stations (real observed "
            "discharge/stage), NOAA NWM modeled streamflow, NHDPlus/NLDI "
            "navigation, river geometry, CaMa-Flood discharge, GCN250 curve "
            "numbers, STATSGO soils, precipitation return-period lookups."
        ),
    ),
    CategorySpec(
        id="terrain_elevation",
        name="Terrain and elevation",
        description=(
            "Digital elevation models and terrain-derivative rasters: DEMs "
            "(3DEP standard + extra resolutions), hillshade, slope, aspect, "
            "colored-relief renderings, and elevation contour lines "
            "(topographic isolines)."
        ),
    ),
    CategorySpec(
        id="land_cover_development",
        name="Land cover and built environment",
        description=(
            "Land-cover classification and the built environment: NLCD "
            "landcover, building footprints, building density, impervious "
            "surface, HRSL population, OSM roads, agricultural field "
            "boundaries (Fields of The World / fiboa), USACE NSI structure "
            "inventory."
        ),
    ),
    CategorySpec(
        id="conservation_ecology",
        name="Conservation and ecology",
        description=(
            "Biodiversity, species occurrences, and protected areas: GBIF, "
            "iNaturalist, eBird, IUCN Red List ranges, Movebank animal tracks, "
            "WDPA protected-area polygons."
        ),
    ),
    CategorySpec(
        id="fire",
        name="Wildfire",
        description=(
            "Wildfire detections, perimeters, and fuels: NASA FIRMS active "
            "fire, MTBS burn severity, NIFC perimeters, LANDFIRE fuel models, "
            "USFS canopy fuels (CBH/CBD)."
        ),
    ),
    CategorySpec(
        id="coastal",
        name="Coastal and ocean",
        description=(
            "Coastal water levels, tides, surge, sea-level-rise scenarios, and "
            "merged topo-bathymetry: GTSM tide+surge reanalysis, NOAA CO-OPS "
            "station tides, NOAA OCM SLR bathtub scenarios, NOAA NCEI CUDEM "
            "topo-bathymetric DEM (sea-floor + land elevation for coastal "
            "SFINCS)."
        ),
    ),
    CategorySpec(
        id="damage_assessment",
        name="Damage assessment",
        description=(
            "Fragility-curve loss estimation and the structure inventories "
            "that feed it: Pelicun damage runs (flood-only and "
            "buildings-coupled), USACE NSI assets."
        ),
    ),
    CategorySpec(
        id="flood_infrastructure",
        name="Flood-control infrastructure",
        description=(
            "Regulatory flood zones and critical flood-control assets: FEMA "
            "NFHL flood zones, USACE National Levee Database, USACE National "
            "Inventory of Dams."
        ),
    ),
    CategorySpec(
        id="geographic_primitives",
        name="Geographic primitives",
        description=(
            "Foundational geographic operations and platform plumbing: "
            "geocoding, admin-boundary fetching, raster/vector clipping, "
            "layer publishing to QGIS, catalog search/fetch. Two Class-A "
            "discovery entry points: discover_dataset for DATA (free-text -> "
            "vetted source), and the list_qgis_algorithms -> "
            "describe_qgis_algorithm -> qgis_process triple for COMPUTE (run "
            "any curated QGIS/GDAL/GRASS/SAGA algorithm)."
        ),
    ),
    CategorySpec(
        id="news_events",
        name="News and event ingest",
        description=(
            "Hazard-event narratives and structured-claim ingestion: open "
            "web fetch, NWS event records, NOAA Storm Events Database, "
            "cross-source claim aggregation."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Primary + secondary category mappings.
#
# Every registered tool has exactly one primary category (used for
# ``list_tools_in_category`` membership). A small number cross-list as
# secondaries when they materially belong to a second category too —
# Pelicun shows up under both ``hazard_modeling`` (you run it as a hazard
# workflow) and ``damage_assessment`` (it IS damage assessment). USACE NSI
# shows up under ``land_cover_development`` (structure inventory) and
# ``damage_assessment`` (it's the canonical Pelicun asset source in CONUS).
# ---------------------------------------------------------------------------


PRIMARY_CATEGORY: dict[str, str] = {
    # ---- 1. hazard_modeling ------------------------------------------------
    "run_model_flood_scenario": "hazard_modeling",
    "run_model_flood_habitat_scenario": "hazard_modeling",
    "run_model_news_event_ingest": "hazard_modeling",
    "run_model_nws_flood_event_scenario": "hazard_modeling",
    # fire-animation demos (GOES geostationary + JPSS/VIIRS polar): the
    # news/incident -> bbox+window -> per-frame imagery -> scrubber-group
    # composer (review-gated). Cross-listed to fire + news_events below.
    "run_model_satellite_fire_animation": "hazard_modeling",
    "run_model_groundwater_contamination_scenario": "hazard_modeling",
    "run_modflow_job": "hazard_modeling",
    "run_swmm_urban_flood": "hazard_modeling",
    "run_pelicun_damage_assessment": "hazard_modeling",
    "run_pelicun_with_buildings": "hazard_modeling",
    # sprint-17 NEW engines (parallel lanes) — all are run_* hazard solvers /
    # composers, filed alongside the other engines above.
    "run_river_seepage_job": "hazard_modeling",
    "run_model_river_seepage_scenario": "hazard_modeling",
    "run_geoclaw_inundation": "hazard_modeling",
    "run_seismic_hazard_psha": "hazard_modeling",
    "run_landlab_susceptibility": "hazard_modeling",
    # SWAN Phase 1: standalone spectral nearshore wave-field engine (the additive
    # comparison engine vs SFINCS+SnapWave). Filed as a hazard engine; also
    # cross-listed under coastal (SECONDARY_CATEGORIES) since it is a coastal/wave
    # tool a user reaches from the coastal lane.
    "run_swan_waves": "hazard_modeling",
    "run_solver": "hazard_modeling",
    "wait_for_completion": "hazard_modeling",
    # ---- 2. weather_atmosphere --------------------------------------------
    "fetch_nws_alerts_conus": "weather_atmosphere",
    "fetch_nws_event": "weather_atmosphere",
    "fetch_nexrad_reflectivity": "weather_atmosphere",
    "fetch_mrms_qpe": "weather_atmosphere",
    "fetch_hrrr_forecast": "weather_atmosphere",
    "fetch_hrrr_smoke": "weather_atmosphere",
    "fetch_goes_satellite": "weather_atmosphere",
    # fire-animation demo S3: GOES GeoColor + Fire Temperature multi-timestamp
    # animation frames (filed in weather next to fetch_goes_satellite; cross-
    # listed to 'fire' via SECONDARY_CATEGORIES).
    "fetch_goes_animation": "weather_atmosphere",
    # blended GeoColor + Fire Temperature animation (one composite scrubber: the
    # CIRA combined product) -- filed alongside fetch_goes_animation, cross-listed
    # to 'fire' via SECONDARY_CATEGORIES.
    "fetch_goes_blend_animation": "weather_atmosphere",
    "fetch_era5_reanalysis": "weather_atmosphere",
    "fetch_asos_metar": "weather_atmosphere",
    "fetch_raws_weather": "weather_atmosphere",
    "fetch_gridmet": "weather_atmosphere",
    # ---- 3. hydrology -----------------------------------------------------
    "fetch_usgs_nwis_gauges": "hydrology",
    "fetch_noaa_nwm_streamflow": "hydrology",
    "fetch_nhdplus_nldi_navigate": "hydrology",
    "fetch_river_geometry": "hydrology",
    "fetch_cama_flood_discharge": "hydrology",
    "lookup_precip_return_period": "hydrology",
    "fetch_gcn250_curve_numbers": "hydrology",
    "fetch_statsgo_soils": "hydrology",
    # ---- 4. terrain_elevation ---------------------------------------------
    "fetch_dem": "terrain_elevation",
    "fetch_3dep_extra": "terrain_elevation",
    "compute_hillshade": "terrain_elevation",
    "compute_slope": "terrain_elevation",
    "compute_aspect": "terrain_elevation",
    "compute_colored_relief": "terrain_elevation",
    "compute_blended_composite": "terrain_elevation",
    "compute_contours": "terrain_elevation",
    # ---- 5. land_cover_development ----------------------------------------
    "fetch_landcover": "land_cover_development",
    "extract_landcover_class": "land_cover_development",
    "compute_impervious_surface": "land_cover_development",
    "fetch_buildings": "land_cover_development",
    "compute_building_density": "land_cover_development",
    "fetch_hrsl_population": "land_cover_development",
    "fetch_population": "land_cover_development",
    "fetch_roads_osm": "land_cover_development",
    "fetch_field_boundaries": "land_cover_development",
    "fetch_usace_nsi": "land_cover_development",
    # ---- 6. conservation_ecology ------------------------------------------
    "fetch_gbif_occurrences": "conservation_ecology",
    "fetch_inaturalist_observations": "conservation_ecology",
    "fetch_ebird_observations": "conservation_ecology",
    "fetch_iucn_red_list_range": "conservation_ecology",
    "fetch_movebank_tracks": "conservation_ecology",
    "fetch_wdpa_protected_areas": "conservation_ecology",
    # ---- 7. fire ----------------------------------------------------------
    "fetch_firms_active_fire": "fire",
    "fetch_mtbs_burn_severity": "fire",
    "fetch_nifc_fire_perimeters": "fire",
    "fetch_landfire_fuels": "fire",
    "fetch_usfs_canopy_fuels": "fire",
    # fire-animation demos: named-incident lookup + the JPSS/VIIRS Day Fire
    # polar animation fetcher (the GOES animation fetcher is filed in
    # weather_atmosphere with its fetch_goes_satellite sibling).
    "fetch_wfigs_incident": "fire",
    "fetch_viirs_day_fire": "fire",
    # ---- 8. coastal -------------------------------------------------------
    "fetch_gtsm_tide_surge": "coastal",
    "fetch_noaa_coops_tides": "coastal",
    "fetch_noaa_slr_scenarios": "coastal",
    # SFINCS North Star P1: merged coastal topo-bathymetry DEM (NOAA NCEI CUDEM
    # 1/9 arc-sec + USGS 3DEP land) — the bathymetric input the coastal SFINCS
    # bed needs (fetch_dem alone is land-only). EPSG:32616 NAVD88 positive-up.
    "fetch_topobathy": "coastal",
    # AWS / Australian-Water-School "Making Waves" SWAN-lecture post-processors:
    # two pure-analytic coastal-wave tools (no fetch, no solver). The nomograph
    # is the wind+fetch -> Hs/Tp pre-flight sanity bound on a SWAN run; the
    # EurOtop tool turns a nearshore Hs/Tp + structure crest into a mean
    # overtopping discharge. Both sit in the coastal lane next to run_swan_waves.
    "compute_wave_nomograph": "coastal",
    "compute_overtopping": "coastal",
    # ---- 9. damage_assessment ---------------------------------------------
    "compute_impact_envelope": "damage_assessment",
    "postprocess_pelicun": "damage_assessment",
    # ---- 10. flood_infrastructure -----------------------------------------
    "fetch_fema_nfhl_zones": "flood_infrastructure",
    "fetch_usace_levees": "flood_infrastructure",
    "fetch_usace_dams": "flood_infrastructure",
    # ---- 11. geographic_primitives ----------------------------------------
    "geocode_location": "geographic_primitives",
    "fetch_administrative_boundaries": "geographic_primitives",
    "clip_raster_to_bbox": "geographic_primitives",
    "clip_raster_to_polygon": "geographic_primitives",
    "clip_vector_to_polygon": "geographic_primitives",
    # QGIS-wrapping backlog (DigitizingTools + Profile tool) -- clean-room GEOS
    # reimplementations via shapely/rasterio (GPL-clean), siblings of clip/cross-section.
    "merge_features": "geographic_primitives",
    "cut_features_with_polygon": "geographic_primitives",
    "fill_gaps": "geographic_primitives",
    "compute_terrain_profile": "geographic_primitives",
    "compute_zonal_statistics": "geographic_primitives",
    # NATE 2026-06-17: fast layer-extent + fit-the-map tool. Replaces the
    # sandbox bbox-math anti-pattern and drives the zoom-to map-command.
    "compute_layer_bounds": "geographic_primitives",
    # FR-AS-10 / FR-WC-16: pause-the-turn and ask the user to DRAW on the map
    # (AOI + tagged flood walls / flap gates, or a point/bbox pick). The drawn
    # barriers feed run_swmm_urban_flood; cross-cutting view/input action.
    "request_spatial_input": "geographic_primitives",
    "summarize_layer_statistics": "geographic_primitives",
    "count_features_above_threshold": "geographic_primitives",
    "aggregate_property_within_zone": "geographic_primitives",
    # job-0230 (sprint-13 Stage 2): chart-generation tools — visual companions
    # to the analytical Q&A tools above (conversational data-analysis layer).
    "generate_histogram": "geographic_primitives",
    "generate_choropleth_legend": "geographic_primitives",
    "generate_time_series": "geographic_primitives",
    "generate_damage_distribution": "geographic_primitives",
    # cross-section / profile tool - the distance-along-a-line chart companion
    # to the time-series / histogram charts above (samples raster value(s) at N
    # stations along a drawn-or-derived line; multi-layer overlay). Filed in the
    # same conversational-analysis surface as the other chart-emission tools.
    "compute_cross_section": "geographic_primitives",
    # job-0233 (sprint-13 Stage 2): user-confirmed Python sandbox — the ad-hoc
    # computation escape hatch behind the conversational data-analysis layer.
    # The kickoff named a "data_analysis" category; no such category exists, so
    # this is filed under geographic_primitives alongside the analytical Q&A +
    # chart tools (the conversational-analysis surface). See job-0233 report
    # OQ-CODE-EXEC-CATEGORY.
    "code_exec_request": "geographic_primitives",
    "publish_layer": "geographic_primitives",
    "discover_dataset": "geographic_primitives",
    "catalog_search": "geographic_primitives",
    "catalog_fetch": "geographic_primitives",
    "list_qgis_algorithms": "geographic_primitives",
    "describe_qgis_algorithm": "geographic_primitives",
    "qgis_process": "geographic_primitives",
    # ---- 12. news_events --------------------------------------------------
    "web_fetch": "news_events",
    "fetch_storm_events_db": "news_events",
    "aggregate_claims_across_sources": "news_events",
}


#: Cross-listings — a tool that materially belongs to a second category.
#: Used by ``tools_for_category`` so a tool appears in BOTH categories' member
#: lists. Membership is additive: the validator treats a tool as allowed if it
#: matches either the primary or any secondary category it carries.
SECONDARY_CATEGORIES: dict[str, tuple[str, ...]] = {
    "run_pelicun_damage_assessment": ("damage_assessment",),
    "run_pelicun_with_buildings": ("damage_assessment",),
    "fetch_usace_nsi": ("damage_assessment",),
    # NWS event ingest spans hazard_modeling (it's the news-event composer)
    # AND news_events (it's the canonical entry point to that category).
    "run_model_news_event_ingest": ("news_events",),
    # Case 2 groundwater composer spans hazard_modeling (it runs MODFLOW) AND
    # news_events (it's driven by a spill news article — the canonical "model
    # the spill from this article" entry point). job-0228.
    "run_model_groundwater_contamination_scenario": ("news_events",),
    # Case 3 composer spans hazard_modeling (it runs SFINCS) AND
    # weather_atmosphere (it's driven by an active NWS flood warning + MRMS
    # observed precip — the canonical "model the live flood" entry point).
    "run_model_nws_flood_event_scenario": ("weather_atmosphere",),
    # SWAN spans hazard_modeling (it runs the SWAN spectral solver) AND coastal
    # (it is THE defensible nearshore wave-field tool -- a user reaches it from the
    # coastal lane to compare against SFINCS+SnapWave on the same case).
    "run_swan_waves": ("coastal",),
    # The GOES animation fetcher is primary-filed in weather_atmosphere (next to
    # fetch_goes_satellite) but materially belongs to the fire branch too.
    "fetch_goes_animation": ("fire",),
    # The blended GeoColor + Fire Temperature animation fetcher: same cross-list.
    "fetch_goes_blend_animation": ("fire",),
    # The satellite fire-animation composer spans hazard_modeling (it composes a
    # multi-tool imagery pipeline) AND fire (it is the fire-branch demo) AND
    # news_events (it ingests the fire news / incident lookup up front).
    "run_model_satellite_fire_animation": ("fire", "news_events"),
}


# ---------------------------------------------------------------------------
# Hot set — always-on tools surfaced before any category has been opened.
# Picked to span the most-common entry points to a session:
#
# - run_model_flood_scenario, run_model_flood_habitat_scenario — the two
#   top-level workflow composers a user is likely to invoke first.
# - geocode_location, fetch_dem, fetch_nws_alerts_conus, fetch_nws_event —
#   the most commonly cited "before you can do anything else" tools
#   (fetch_nws_event added by job-0261 — see inline comment).
# - list_categories, list_tools_in_category, discover_dataset — the three
#   meta-tools that let Gemini surface anything else when the hot set
#   isn't enough.
# - code_exec_request — cross-cutting capability (job-0247).
# ---------------------------------------------------------------------------


HOT_SET_TOOLS: frozenset[str] = frozenset(
    {
        "run_model_flood_scenario",
        "run_model_flood_habitat_scenario",
        "geocode_location",
        "fetch_dem",
        "fetch_nws_alerts_conus",
        "list_categories",
        "list_tools_in_category",
        "discover_dataset",
        # job-0247 (OQ-0247-CODE-EXEC-NOT-IN-HOT-SET): code-exec is a
        # cross-cutting capability like the meta-tools, not a geographic
        # primitive the agent should have to discover via category listing.
        # Round-4 live: Gemini called it CORRECTLY on the first turn, the
        # post-hoc validator rejected it (OutOfAllowedSetError), and the
        # agent narrated a false "I am unable to run Python code" instead
        # of widening. Always reachable; the user-confirm gate (job-0233)
        # remains the safety boundary.
        "code_exec_request",
        # job-0261: same failure mode as job-0247, worse outcome. Live demo
        # "show me weather alerts in texas": Gemini called
        # fetch_nws_event(area='TX') CORRECTLY on the first turn, the
        # validator rejected it, and Gemini fell back to the in-hot-set
        # fetch_nws_alerts_conus() — the UNSCOPED national sweep — so
        # alerts rendered far beyond the named state. The state-scoped NWS
        # tool must be as reachable as its CONUS sibling.
        "fetch_nws_event",
        # NATE 2026-06-17: fit/zoom/resize-to-encompass-all-features is a
        # cross-cutting view action a user invokes at any point ("resize the box
        # to encompass all the buildings"). It must be reachable WITHOUT a
        # category-open round-trip — same rationale as code_exec_request above.
        # Critically, this keeps the agent from falling back to the Python
        # sandbox for bbox math when compute_layer_bounds isn't in the allowed
        # set (the job-0247 / job-0261 failure mode).
        "compute_layer_bounds",
        # FR-AS-10 / FR-WC-16: request_spatial_input is a cross-cutting user-
        # input action the agent invokes at any point ("let me draw the flood
        # walls"). Same hot-set rationale as code_exec_request / compute_layer_
        # bounds — it must be reachable WITHOUT a category-open round-trip so the
        # urban-flood draw flow does not stall on the post-hoc allowed-set
        # validator (the job-0247 / job-0261 failure mode).
        "request_spatial_input",
    }
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class UnknownCategoryError(ValueError):
    """Raised when a category id is not one of the 12 registered ids.

    Carried by ``list_tools_in_category`` so a bad LLM-supplied category arg
    surfaces as a structured tool-error envelope rather than a silent empty
    result.
    """

    error_code: str = "UNKNOWN_CATEGORY"
    retryable: bool = False

    def __init__(self, category_id: str, valid_ids: Iterable[str]) -> None:
        valid_list = sorted(valid_ids)
        super().__init__(
            f"category {category_id!r} is not one of the 12 registered "
            f"categories; valid ids are {valid_list}"
        )
        self.category_id = category_id
        self.valid_ids = valid_list


class OutOfAllowedSetError(RuntimeError):
    """Raised when Gemini emits a ``function_call`` for a name that is not in
    the current turn's allowed set AND not a registered tool.

    Per the Wave 4.10 CachedContent Option A architecture: every Gemini
    function_call is validated against the per-session ``AllowedToolSet``
    BEFORE dispatch. Since job-0270, a registry-valid name outside the
    allowed set auto-widens the set instead of raising — this exception is
    now the HALLUCINATION GUARD: it fires only for names that exist nowhere
    in ``TOOL_REGISTRY``. ``summarize_tool_result`` (adapter.py) renders it
    as the canonical Wave 4.9 structured error envelope with
    ``error_code='OUT_OF_ALLOWED_SET'`` and ``retryable=False``. Gemini
    reads the envelope on its next turn and retries — typically by calling
    ``list_tools_in_category`` / ``discover_dataset`` to find a real tool.
    """

    error_code: str = "OUT_OF_ALLOWED_SET"
    retryable: bool = False

    def __init__(self, tool_name: str, hot_set: Iterable[str]) -> None:
        # Limit hint to first 16 names so the function_response stays under
        # the adapter's character budget.
        hot_hint = sorted(hot_set)[:16]
        super().__init__(
            f"tool {tool_name!r} not in allowed set; consider calling "
            f"list_tools_in_category(...) first to widen the allowed set. "
            f"hot-set tools currently available (first 16): {hot_hint}"
        )
        self.tool_name = tool_name
        self.hot_set_hint = hot_hint


# ---------------------------------------------------------------------------
# Allowed set
# ---------------------------------------------------------------------------


@dataclass
class AllowedToolSet:
    """Per-session "allowed tools" tracker for the post-hoc validator.

    Composition (from ``project_wave_4_10_research_findings.md`` §
    Architecture decisions):

    - **Hot set** (always on): the tools in ``HOT_SET_TOOLS`` (static), OR
      the top-K tools from ``get_dynamic_hot_set`` when
      ``GRACE2_DYNAMIC_HOT_SET=1`` and Mongo is available (M6 wire-up).
      The dynamic hot set is fetched lazily on the first ``as_frozenset``
      call that goes through ``as_frozenset_async``; synchronous callers
      (``validate_function_call``, ``__contains__``) always get the cached
      value or the static fallback.
    - **Sticky-after-list**: every category id the LLM has called
      ``list_tools_in_category(category_id=...)`` on this session opens up
      the full member list of that category for the rest of the session.
    - **Sticky-after-dispatch**: every tool the agent has successfully
      dispatched this session stays in the allowed set (the LLM may need
      to re-issue the same tool on a later turn with refined args).
    - **Explicit overrides**: tools the agent core has added directly via
      ``add_tools(...)`` (used by category-router code paths that want to
      pre-warm a specific tool).
    - **Always-available meta-tools**: ``list_categories``,
      ``list_tools_in_category``, and ``discover_dataset`` are merged into
      every snapshot so the escape-hatch discovery path is never gated.

    The set is **monotonically growing** within a session — tools never
    leave. A new session (new WebSocket connection / new ``SessionState``)
    starts with a fresh ``AllowedToolSet`` seeded from the hot set.

    Wave 4.11 M6 extension: the optional ``user_id`` field carries the
    authenticated user identity so ``get_dynamic_hot_set`` can filter
    telemetry per-user.  When ``None`` the global (all-users) tallying path
    is used.  The ``GRACE2_DYNAMIC_HOT_SET=1`` feature flag must also be set
    for the dynamic path to engage; without the flag (the default) the static
    ``HOT_SET_TOOLS`` is used regardless of ``user_id``.
    """

    #: Categories the LLM has opened via list_tools_in_category this session.
    opened_categories: set[str] = field(default_factory=set)
    #: Tools the agent has successfully dispatched this session.
    dispatched_tools: set[str] = field(default_factory=set)
    #: Explicit additions (category-router pre-warm, etc.).
    explicit_tools: set[str] = field(default_factory=set)
    #: Authenticated user_id for per-user dynamic hot-set (None = global).
    user_id: str | None = None
    #: Cached hot set (populated lazily by ``as_frozenset_async``; starts
    #: as None so the first async call re-fetches from Mongo / static).
    _dynamic_hot_set: frozenset[str] | None = field(default=None, repr=False)

    # Always-available meta-tools — never gated behind the hot set.
    _META_TOOLS: frozenset[str] = field(
        default=frozenset({"list_categories", "list_tools_in_category", "discover_dataset"}),
        init=False,
        repr=False,
        compare=False,
    )

    def open_category(self, category_id: str) -> None:
        """Mark a category as opened (sticky-after-list)."""
        self.opened_categories.add(category_id)

    def record_dispatch(self, tool_name: str) -> None:
        """Mark a tool as dispatched (sticky-after-dispatch)."""
        self.dispatched_tools.add(tool_name)

    def add_tools(self, names: Iterable[str]) -> None:
        """Explicit pre-warm of one or more tool names."""
        self.explicit_tools.update(names)

    def _build_from_hot_set(self, hot_set: frozenset[str]) -> frozenset[str]:
        """Build the full allowed set from a given hot_set base.

        Composition: ``hot_set`` ∪ meta-tools ∪ tools-in-opened-categories
        ∪ dispatched ∪ explicit.
        """
        allowed: set[str] = set(hot_set)
        allowed.update(self._META_TOOLS)
        for cat in self.opened_categories:
            allowed.update(tools_for_category(cat))
        allowed.update(self.dispatched_tools)
        allowed.update(self.explicit_tools)
        return frozenset(allowed)

    def as_frozenset(self) -> frozenset[str]:
        """Synchronous snapshot — uses static ``HOT_SET_TOOLS`` or the
        already-cached dynamic hot set (if ``as_frozenset_async`` has run
        at least once this session).

        Callers that need the *latest* dynamic hot set should prefer
        ``as_frozenset_async`` on the first turn of a session.  After the
        first async resolution the cached value is reused synchronously so
        the validate/dispatch inner-loop stays zero-await.
        """
        hot_set = (
            self._dynamic_hot_set
            if self._dynamic_hot_set is not None
            else HOT_SET_TOOLS
        )
        return self._build_from_hot_set(hot_set)

    async def as_frozenset_async(self) -> frozenset[str]:
        """Async snapshot — fetches the dynamic hot set from Mongo when
        ``GRACE2_DYNAMIC_HOT_SET=1``, then caches it for subsequent
        synchronous calls.

        When the env flag is unset (the default), delegates immediately to
        the synchronous ``as_frozenset()`` path (no Mongo round-trip,
        backward-compat).

        When Mongo is unavailable ``get_dynamic_hot_set`` falls back to the
        static ``HOT_SET_TOOLS`` internally, so the caller always gets a
        non-empty set.
        """
        if os.environ.get("GRACE2_DYNAMIC_HOT_SET") != "1":
            return self.as_frozenset()

        try:
            from .tools.discover_dataset import get_dynamic_hot_set as _get_dyn

            dynamic = await _get_dyn(user_id=self.user_id, top_k=8)
            # Merge with the static set so tools the user has never called
            # (e.g. on a cold-start user account) still see the canonical
            # baseline — the dynamic set only *replaces* the hot-set slot;
            # the meta-tools are always present via ``_build_from_hot_set``.
            self._dynamic_hot_set = dynamic if dynamic else HOT_SET_TOOLS
        except Exception:  # noqa: BLE001 — Mongo unavailable; stay on static
            import logging as _logging

            _logging.getLogger("grace2_agent.categories").debug(
                "dynamic hot-set fetch failed; falling back to static HOT_SET_TOOLS",
                exc_info=True,
            )
            self._dynamic_hot_set = HOT_SET_TOOLS

        return self._build_from_hot_set(self._dynamic_hot_set)

    def __contains__(self, tool_name: object) -> bool:  # pragma: no cover — thin
        if not isinstance(tool_name, str):
            return False
        return tool_name in self.as_frozenset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def tools_for_category(category_id: str) -> tuple[str, ...]:
    """Return the registered-tool names that belong to ``category_id``.

    Includes both primary memberships (``PRIMARY_CATEGORY[name] ==
    category_id``) and secondary cross-listings
    (``category_id in SECONDARY_CATEGORIES[name]``). Sorted for determinism.

    Raises ``UnknownCategoryError`` if ``category_id`` is not one of the 12.
    """
    valid_ids = {c.id for c in CATEGORIES}
    if category_id not in valid_ids:
        raise UnknownCategoryError(category_id, valid_ids)
    out: set[str] = set()
    for name, primary in PRIMARY_CATEGORY.items():
        if primary == category_id:
            out.add(name)
    for name, secondaries in SECONDARY_CATEGORIES.items():
        if category_id in secondaries:
            out.add(name)
    return tuple(sorted(out))


# ---------------------------------------------------------------------------
# Post-hoc validator
# ---------------------------------------------------------------------------


def validate_function_call(call_name: str, allowed: AllowedToolSet) -> None:
    """Validate a Gemini ``function_call`` name; raise only for non-tools.

    Per Wave 4.10 CachedContent Option A: every Gemini-emitted ``function_call``
    must be validated against the current turn's allowed set BEFORE we hand
    it off to ``_invoke_tool_via_emitter``. The hot set is always present; the
    allowed set widens monotonically through the session as the LLM opens
    categories (``list_tools_in_category``) or dispatches tools.

    job-0270 (auto-widen for REAL tools): when ``call_name`` IS a registered
    tool (in the live ``TOOL_REGISTRY``) but outside the current allowed set,
    do NOT raise — Gemini saw the full catalog via CachedContent, so a
    registry-valid call is a correct routing decision, not a hallucination.
    The set auto-widens with that name (same monotonic explicit-tools growth
    path used by category pre-warm) and the dispatch proceeds; a WARNING log
    records the widening. Live evidence (job-0247, job-0261, agent_demo7/8):
    rejecting registry-valid first calls to ``compute_colored_relief`` /
    ``compute_hillshade`` / ``publish_layer`` burned 2-4 detour iterations
    per turn while Gemini guessed category names, and once left a computed
    raster unpublished (invisible to the user).

    Names NOT in the registry still raise ``OutOfAllowedSetError`` exactly as
    before — that is the hallucination guard, unweakened. The caller
    (server.py) catches the typed exception and routes it through
    ``summarize_tool_result(error=...)`` so Gemini sees a structured envelope
    and can retry.

    Returns ``None`` on success.
    """
    snapshot = allowed.as_frozenset()
    if call_name in snapshot:
        return
    # Import locally to avoid a circular import at module-load time (same
    # seam ``_list_tools_in_category_impl`` uses for category listings).
    from .tools import TOOL_REGISTRY

    if call_name in TOOL_REGISTRY:
        allowed.add_tools((call_name,))
        logger.warning(
            "allowed-set auto-widen tool=%s (was outside hot set)", call_name
        )
        return
    raise OutOfAllowedSetError(call_name, hot_set=HOT_SET_TOOLS)


# ---------------------------------------------------------------------------
# list_categories + list_tools_in_category atomic tools
#
# Both are registered as @register_tool meta-tools; both default to
# ``supports_global_query=True`` because they take no spatial input. They're
# part of the hot set so the LLM can always reach them.
# ---------------------------------------------------------------------------


def _list_categories_impl() -> dict:
    """Return all 12 categories with ids, names, and descriptions."""
    return {
        "categories": [
            {"id": c.id, "name": c.name, "description": c.description}
            for c in CATEGORIES
        ]
    }


def _list_tools_in_category_impl(category_id: str) -> dict:
    """Return member tools for ``category_id`` with short description snippets.

    The snippet is the first sentence (or first 200 chars) of each tool's
    docstring — enough to let Gemini decide whether to call the tool without
    the full FunctionDeclaration overhead.

    Raises ``UnknownCategoryError`` if ``category_id`` is not registered.
    """
    # Import locally to avoid a circular import at module-load time.
    from .tools import TOOL_REGISTRY

    names = tools_for_category(category_id)
    tools: list[dict] = []
    for name in names:
        entry = TOOL_REGISTRY.get(name)
        if entry is None:
            # Tool listed in PRIMARY_CATEGORY but not registered — should not
            # happen in product code; covered by test_categories. Skip rather
            # than raise so a temporary registry-skew during local dev does
            # not crash discovery for the LLM.
            continue
        doc = (entry.fn.__doc__ or "").strip()
        snippet = _first_sentence(doc)
        tools.append({"name": name, "description_snippet": snippet})
    return {"category_id": category_id, "tools": tools}


def _first_sentence(doc: str, *, max_chars: int = 200) -> str:
    """Extract a short snippet from a tool's docstring.

    Strategy: take the first non-empty line; truncate to ``max_chars``.
    Tool docstrings in this repo follow the convention that the first line is
    a one-sentence summary, so this is usually a clean snippet.
    """
    if not doc:
        return ""
    for line in doc.splitlines():
        line = line.strip()
        if line:
            if len(line) > max_chars:
                return line[: max_chars - 1].rstrip() + "…"
            return line
    return ""


@register_tool(
    AtomicToolMetadata(
        name="list_categories",
        ttl_class="static-30d",
        source_class="meta",
        cacheable=True,
        read_only_hint=True,
        open_world_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
    ),
    supports_global_query=True,
)
def list_categories() -> dict:
    """List the 12 top-level tool categories with ids, names, and descriptions.

    Use this when:
        - You are not sure which tool to call and want to narrow the search.
        - The user's request spans a new domain you have not opened this
          session.

    Do NOT use this for:
        - Querying member tools of a specific category — call
          ``list_tools_in_category`` with the ``id`` instead.
        - Free-text retrieval of a tool by user-query — use
          ``discover_dataset`` for that.

    Returns:
        A dict ``{"categories": [{"id": str, "name": str, "description": str},
        ...]}`` of length 12. The order is stable and follows the registry.
    """
    return _list_categories_impl()


@register_tool(
    AtomicToolMetadata(
        name="list_tools_in_category",
        ttl_class="static-30d",
        source_class="meta",
        cacheable=True,
        read_only_hint=True,
        open_world_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
    ),
    supports_global_query=True,
)
def list_tools_in_category(category_id: str) -> dict:
    """List the member tools of one category, with short description snippets.

    Use this when:
        - You have decided which category to open (typically right after
          ``list_categories``).
        - You want to widen the allowed tool set for the current session so
          subsequent function_calls into this category are not rejected.

    Do NOT use this for:
        - Free-text search across all tools — call ``discover_dataset``.
        - Listing the categories themselves — call ``list_categories``.

    Args:
        category_id: One of the 12 stable category ids returned by
            ``list_categories``. Raises ``UnknownCategoryError`` (rendered as
            error_code ``UNKNOWN_CATEGORY``) if the id is not registered.

    Returns:
        A dict ``{"category_id": str, "tools": [{"name": str,
        "description_snippet": str}, ...]}`` listing every tool in that
        category. The list is sorted by tool name.
    """
    return _list_tools_in_category_impl(category_id)
