"""MODFLOW 6 groundwater-engine contracts (sprint-13 Stage 1, §2.3 MODFLOW
integration, OQ-9 mf6-gwt solute transport).

Two shapes back the Case 2 groundwater-contamination demo path
(news article -> parameter extraction -> MODFLOW run -> plume layer):

- ``MODFLOWRunArgs``  - the forcing parameters the agent confirms with the user
  before submitting a MODFLOW run. Consumed by the engine adapter
  (``services/workers/modflow/gwt_adapter.py``, job-0221) that maps these to
  MF6-GWT input files via ``flopy``, and by the agent-side
  ``run_modflow_job`` tool (job-0227).
- ``PlumeLayerURI`` - the postprocess output layer. Extends ``LayerURI``
  field-for-field (so it still maps onto ``map-command load-layer`` with no
  translation, like every other layer) and adds the two plume scalars the
  agent narrates: peak concentration and plume footprint.

Design notes
------------
- ``spill_location_latlon`` is ordered ``(lat, lon)`` - this is a single point,
  NOT a ``bbox``. The project ``BBox`` convention is ``(min_lon, min_lat, ...)``
  (lon-first, EPSG:4326); a *point* spill location reads more naturally as
  ``(lat, lon)`` and is documented as such here so the engine adapter and the
  agent tool both honor the same order. Each component is range-validated
  (lat in [-90, 90], lon in [-180, 180]).
- Defaults for ``aquifer_k_ms`` (hydraulic conductivity) and ``porosity`` are
  TENTATIVE demo parameterization per sprint-13 manifest OQ-3: K=1e-4 m/s,
  porosity=0.3 (saturated sandy coastal plain). The composer (job-0228) must
  narrate to the user that these are demo defaults, not site-specific
  hydrogeology. See report Open Questions.
- ``PlumeLayerURI`` is a structured numeric carrier (invariant 1 / Decision H /
  FR-AS-7): the agent narrates ``max_concentration_mgl`` and ``plume_area_km2``
  from these typed fields rather than inventing them from free text.
- ``contaminant`` is a free ``str`` (open by design - the contaminant name is an
  open vocabulary, e.g. "benzene", "TCE", "PFOA"; the engine maps it to MF6-GWT
  transport parameters). It is non-numeric, so it stays a scalar.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from .common import EngineRunArgsMixin, GraceModel
from .execution import LayerURI

# Streambed defaults for the RIV head-dependent river<->aquifer flux package
# (sprint-17 J9 river-seepage). Per-cell RIV conductance C = K_bed * L * W / M
# (bed hydraulic conductivity * reach length-in-cell * reach width / bed
# thickness). When a per-cell conductance is not supplied the adapter derives
# it from these defaults (demo streambed, narrated as a demo value just like
# the OQ-3 aquifer K / porosity). A typical silty streambed K ~= 0.1 m/day with
# a 1 m bed thickness and a ~5 m channel across a 50 m cell gives an O(10-100)
# m^2/day conductance; the spike used a flat 100 m^2/day and produced
# 835 m^3/day of leakage, so 50 m^2/day is a conservative default per-cell.
DEFAULT_STREAMBED_CONDUCTANCE_M2_DAY: float = 50.0  # per RIV reach cell
DEFAULT_STREAMBED_THICKNESS_M: float = 1.0  # streambed (M) for K-derived conductance

__all__ = [
    "MODFLOWRunArgs",
    "PlumeLayerURI",
    "SeepageLayerURI",
    "DrawdownLayerURI",
    "DewaterLayerURI",
    "BudgetPartitionLayerURI",
    "MoundingLayerURI",
    "ASRLayerURI",
    "HydroperiodLayerURI",
    "DEFAULT_STREAMBED_CONDUCTANCE_M2_DAY",
    "DEFAULT_STREAMBED_THICKNESS_M",
    "DEFAULT_AQUIFER_SY",
    "DEFAULT_AQUIFER_SS",
    "DEFAULT_WETLAND_SY",
]


# TENTATIVE demo defaults (sprint-13 manifest OQ-3). Narrated as demo values,
# not site-specific hydrogeology, by the Case 2 composer.
DEFAULT_AQUIFER_K_MS: float = 1e-4  # hydraulic conductivity, m/s (sandy coastal plain)
DEFAULT_POROSITY: float = 0.3  # effective porosity, dimensionless

# Transient-storage defaults for the three new sprint-18 archetypes
# (sustainable_yield / mine_dewatering / regional_water_budget). These feed the
# GwfSto package (specific yield + specific storage) the transient stress period
# needs; the existing spill/seepage archetype path does NOT read them (it stays
# steady + the byte-identical conservative-tracer deck). Demo values, narrated.
DEFAULT_AQUIFER_SY: float = 0.2  # specific yield (drainable porosity), dimensionless
DEFAULT_AQUIFER_SS: float = 1e-5  # specific storage (1/m), confined-aquifer demo value

# Wetland-hydroperiod specific-yield default (sprint-18 Wave-2 wetland_hydroperiod
# archetype). The seasonal water-table response under a recharging wetland is
# governed by the unconfined specific yield; a shallow-water-table wetland soil
# drains/fills with a Sy ~= 0.2 (same family as DEFAULT_AQUIFER_SY but named
# separately so the wetland archetype reads its own demo value). Narrated demo.
DEFAULT_WETLAND_SY: float = 0.2  # wetland-soil specific yield, dimensionless


class MODFLOWRunArgs(EngineRunArgsMixin):
    """Forcing parameters for a MODFLOW 6 + MF6-GWT groundwater run.

    Adopts ``EngineRunArgsMixin`` (levers STEP 3): ``temporal_mode`` (default
    ``"steady"``, no-op for the demo deck), ``output_frames`` (default 24), and
    ``advanced_physics`` (default ``None``). ``advanced_physics`` keys are
    validated against ``physics_registry.PHYSICS_REGISTRY["modflow"]`` (sorption
    Kd / bulk density / first-order decay / longitudinal+transverse dispersivity)
    and applied at the ``GwtMst`` / ``GwtDsp`` deck seam; ``None`` =>
    byte-identical conservative-tracer deck.

    Returned/assembled by the Case 2 composer after agent-confirmed parameter
    extraction; consumed by ``run_modflow_job`` (agent) and the ``flopy``
    GWT adapter (engine). The agent confirms these with the user before
    submission (confirmation-before-consequence, invariant 9).

    Use this when:
        Building the input to a groundwater-contamination MODFLOW run from a
        spill event (location + contaminant + release schedule + aquifer
        properties).

    Do NOT use this for:
        Surface-water / flood forcing (that is SFINCS ``ModelSetup``), or for
        carrying solver output (that is ``PlumeLayerURI``).

    Fields:
        schema_version: contract version pin (additive growth only).
        spill_location_latlon: point spill location as ``(lat, lon)`` in
            EPSG:4326. NOTE the order is lat-first (a point, not a bbox).
        contaminant: contaminant name (open vocabulary, e.g. "benzene", "TCE").
        release_rate_kg_s: contaminant mass release rate, kg/s (> 0).
        duration_days: release duration, days (> 0).
        aquifer_k_ms: aquifer hydraulic conductivity, m/s (> 0). Defaults to a
            TENTATIVE demo value (OQ-3); narrate as a demo default.
        porosity: aquifer effective porosity, dimensionless in (0, 1].
            Defaults to a TENTATIVE demo value (OQ-3); narrate as a demo default.

    River-coupling fields (sprint-17 J9 - ADDITIVE, all optional; the pure-spill
    deck is byte-identical when ``river_geometry_uri`` is None):
        river_geometry_uri: a FlatGeobuf / GeoJSON URI of the river polyline(s)
            (from ``fetch_river_geometry`` / NLDI) to drape onto the structured
            grid as RIV head-dependent boundary cells. When None, no RIV/along-
            river SRC is added and the deck is the original spill-only deck.
        river_stage_m: explicit river stage (water-surface elevation, m, local
            datum) for every RIV reach cell. When None the adapter samples stage
            from the DEM at each reach cell (``river_dem_uri``) or, absent a DEM,
            falls back to ``AQUIFER_TOP_M`` + a small head so the reach is a
            gaining/losing boundary, not a no-op.
        river_stage_depth_m: water depth (m) above the streambed bottom used to
            derive stage from the sampled streambed elevation (stage = rbot +
            depth). Demo default applied by the adapter when None.
        streambed_conductance_m2_day: per-reach-cell RIV conductance (m^2/day).
            When None the adapter derives it from
            ``DEFAULT_STREAMBED_CONDUCTANCE_M2_DAY``.
        river_dem_uri: optional DEM COG URI used to sample streambed-bottom
            elevation (rbot) and stage along the reach. When None the adapter
            uses flat demo streambed values relative to ``AQUIFER_TOP_M``.
        along_river_source: when True the contaminant SRC mass-loading is placed
            at the RIV reach cells (the seepage source enters where the river
            leaks into the aquifer) INSTEAD of the single spill cell. When False
            (default) the SRC stays at the spill cell exactly as the original
            groundwater-contamination deck.

    Archetype selector + per-archetype fields (sprint-18 Wave-1 - ADDITIVE, all
    optional/defaulted; ``archetype is None`` is the EXISTING spill/seepage path
    and the deck is byte-identical):
        archetype: which new MODFLOW question this run answers. ``None`` (the
            default) keeps the existing spill/seepage deck. The three new values
            are ``"sustainable_yield"`` (well-pumping drawdown), ``"mine_dewatering"``
            (DRN-package pit dewatering), and ``"regional_water_budget"`` (zonal
            flow-budget partition). The adapter branches on this; an unknown value
            is rejected by the literal.

        --- sustainable_yield (pumping-well drawdown) ---
        well_location_latlon: pumping-well point as ``(lat, lon)`` EPSG:4326
            (lat-first, same convention as ``spill_location_latlon``).
        pumping_rate_m3_day: sustained extraction rate as a POSITIVE magnitude,
            m^3/day (sustainable_yield is always an extraction question). The
            adapter applies the MF6 WEL sign internally (a negative discharge
            removes water from the cell), so the user passes a positive number.
        aquifer_sy: specific yield (drainable porosity) for the GwfSto transient
            storage term, dimensionless. Demo default ``DEFAULT_AQUIFER_SY`` (0.2).
        aquifer_ss: specific storage (1/m) for the GwfSto transient term. Demo
            default ``DEFAULT_AQUIFER_SS`` (1e-5).
        sim_years: transient simulation length, years (> 0). When None the
            adapter uses ``n_periods`` (or its own demo default).
        n_periods: number of transient stress periods (>= 1). An alternative to
            ``sim_years`` for explicit period control.

        --- mine_dewatering (DRN-package pit dewatering) ---
        pit_footprint_lonlat: the pit footprint as an ordered list of
            ``(lon, lat)`` vertices (lon-first, the BBox/polygon convention) draped
            onto the grid as DRN drain cells. A point or single-cell pit is a
            one-element list.
        drain_elevation_m: DRN drain elevation (the target dewatered head, m local
            datum) applied to every pit cell. Water above this elevation drains out.
        drain_conductance_m2_day: per-cell DRN conductance (m^2/day) controlling the
            head-dependent drain flux Q = C*(h - drain_elev) for h > drain_elev.
        well_pumping_rate_m3_day: OPTIONAL supplemental sump-WEL extraction as a
            POSITIVE magnitude (m^3/day; the adapter applies the negative MF6 sign)
            combined with the drains (a pit can be dewatered by drains plus pumping
            wells). None = drains only.

        --- regional_water_budget (flow-budget partition) ---
        zone_partition: RESERVED (Wave-2). regional_water_budget currently returns
            the WHOLE-DOMAIN volumetric budget partition (per-term IN/OUT from the
            real CBC: WEL/RCH/RCHA/CHD/STO/DRN, FLOW-JA-FACE excluded from the
            headline). Per-zone (e.g. upgradient/downgradient) partitioning is not
            yet wired agent-side; this field is accepted but does not change the
            output until then. Leave None.

    Archetype fields (sprint-18 Wave-2 - ADDITIVE, all optional/defaulted; a
    run-args with none of them is byte-identical to the Wave-1 / spill path):

        --- MAR (managed aquifer recharge -> RCH mounding) ---
        basin_footprint_lonlat: the infiltration-basin footprint as an ordered
            list of ``(lon, lat)`` vertices (lon-first, the BBox/polygon
            convention) draped onto the grid as RCH recharge cells.
        infiltration_rate_m_day: applied recharge rate over the basin, m/day
            (> 0). The RCH package raises the water table (mounding) under it.
        recharge_months: number of months the basin floods (>= 1). Drives the
            transient stress-period count alongside ``n_periods``.
        n_periods: REUSED (sustainable_yield) - explicit transient period count
            override.

        --- ASR (aquifer storage & recovery -> seasonal WEL inject/recover) ---
        well_location_latlon: REUSED (sustainable_yield) - the ASR well point as
            ``(lat, lon)`` EPSG:4326 (lat-first).
        injection_rate_m3_day: ASR injection rate as a POSITIVE magnitude,
            m^3/day (> 0). The adapter applies the MF6 WEL sign (inject = +).
        recovery_rate_m3_day: ASR recovery (extraction) rate as a POSITIVE
            magnitude, m^3/day (> 0). The adapter applies the WEL sign (recover
            = -).
        injection_months: months of the injection half-cycle (>= 1).
        recovery_months: months of the recovery half-cycle (>= 1).
        n_cycles: number of inject/recover cycles (>= 1).

        --- wetland_hydroperiod (seasonal water-table range under a wetland) ---
        wetland_footprint_lonlat: the wetland footprint as an ordered list of
            ``(lon, lat)`` vertices (lon-first) draped onto the grid as the
            recharge + EVT cells.
        recharge_schedule_m_day: per-transient-period recharge rate schedule
            (one m/day value per period) applied over the wetland footprint.
        et_surface_m: EVT surface elevation (m, local datum) - the elevation at
            which evapotranspiration is at its max rate.
        et_max_rate_m_day: maximum ET rate at the surface, m/day (> 0).
        et_extinction_depth_m: ET extinction depth (m, > 0) below the surface
            past which ET is zero (the EVT linear-decline depth).
        specific_yield: wetland-soil specific yield for the unconfined seasonal
            response, dimensionless in (0, 1]. Demo default ``DEFAULT_WETLAND_SY``
            (0.2).
    """

    schema_version: Literal["v1", "v2"] = "v2"

    # Point spill location: (lat, lon), EPSG:4326. Lat-first by design (a point,
    # not the lon-first BBox convention). Each component range-validated below.
    spill_location_latlon: tuple[float, float]

    contaminant: str = Field(min_length=1)

    release_rate_kg_s: float = Field(gt=0.0)
    duration_days: float = Field(gt=0.0)

    aquifer_k_ms: float = Field(default=DEFAULT_AQUIFER_K_MS, gt=0.0)
    porosity: float = Field(default=DEFAULT_POROSITY, gt=0.0, le=1.0)

    # --- River-coupling (sprint-17 J9; ADDITIVE, all optional) -------------- #
    river_geometry_uri: str | None = None
    river_stage_m: float | None = None
    river_stage_depth_m: float | None = Field(default=None, gt=0.0)
    streambed_conductance_m2_day: float | None = Field(default=None, gt=0.0)
    river_dem_uri: str | None = None
    along_river_source: bool = False

    # --- Archetype selector (sprint-18 Wave-1 + Wave-2; ADDITIVE, optional) -- #
    # None = the EXISTING spill/seepage path (deck byte-identical). The three
    # Wave-2 values ("MAR", "ASR", "wetland_hydroperiod") are additive on top of
    # the Wave-1 three.
    archetype: (
        Literal[
            "sustainable_yield",
            "mine_dewatering",
            "regional_water_budget",
            "MAR",
            "ASR",
            "wetland_hydroperiod",
        ]
        | None
    ) = None

    # --- sustainable_yield: pumping-well drawdown --------------------------- #
    well_location_latlon: tuple[float, float] | None = None
    pumping_rate_m3_day: float | None = None  # negative = extraction (WEL sign)
    aquifer_sy: float = Field(default=DEFAULT_AQUIFER_SY, gt=0.0, le=1.0)
    aquifer_ss: float = Field(default=DEFAULT_AQUIFER_SS, gt=0.0)
    sim_years: float | None = Field(default=None, gt=0.0)
    n_periods: int | None = Field(default=None, ge=1)

    # --- mine_dewatering: DRN-package pit dewatering ------------------------ #
    pit_footprint_lonlat: list[tuple[float, float]] | None = None
    drain_elevation_m: float | None = None
    drain_conductance_m2_day: float | None = Field(default=None, gt=0.0)
    well_pumping_rate_m3_day: float | None = None  # optional supplemental WEL

    # --- regional_water_budget: zonal flow-budget partition ---------------- #
    zone_partition: str | None = None

    # --- MAR: managed aquifer recharge (RCH mounding) ---------------------- #
    # An infiltration basin floods a footprint with a recharge rate over a number
    # of recharge months; the RCH/RCHA package raises the water table (mounding)
    # under the basin. All optional/defaulted -> additive.
    basin_footprint_lonlat: list[tuple[float, float]] | None = None
    infiltration_rate_m_day: float | None = Field(default=None, gt=0.0)
    recharge_months: int | None = Field(default=None, ge=1)

    # --- ASR: aquifer storage & recovery (seasonal WEL inject/recover) ------ #
    # A single ASR well (reuse ``well_location_latlon``) INJECTS at a positive
    # rate for ``injection_months`` then RECOVERS (extracts) at a positive rate
    # for ``recovery_months``, repeated for ``n_cycles``. Both rates are passed
    # as POSITIVE magnitudes; the adapter applies the MF6 WEL sign (inject = +,
    # recover = -). All optional/defaulted -> additive.
    injection_rate_m3_day: float | None = Field(default=None, gt=0.0)
    recovery_rate_m3_day: float | None = Field(default=None, gt=0.0)
    injection_months: int | None = Field(default=None, ge=1)
    recovery_months: int | None = Field(default=None, ge=1)
    n_cycles: int | None = Field(default=None, ge=1)

    # --- wetland_hydroperiod: seasonal water-table range under a wetland ---- #
    # A wetland footprint receives a per-period recharge schedule
    # (``recharge_schedule_m_day``, one rate per transient period) while EVT
    # removes water at the surface (EVT package: surface elevation, max rate,
    # extinction depth); the seasonal head range is the hydroperiod. All
    # optional/defaulted -> additive.
    wetland_footprint_lonlat: list[tuple[float, float]] | None = None
    recharge_schedule_m_day: list[float] | None = None
    et_surface_m: float | None = None
    et_max_rate_m_day: float | None = Field(default=None, gt=0.0)
    et_extinction_depth_m: float | None = Field(default=None, gt=0.0)
    specific_yield: float = Field(default=DEFAULT_WETLAND_SY, gt=0.0, le=1.0)

    @field_validator("spill_location_latlon")
    @classmethod
    def _validate_latlon(cls, value: tuple[float, float]) -> tuple[float, float]:
        """Enforce ``(lat, lon)`` ranges: lat in [-90, 90], lon in [-180, 180]."""
        lat, lon = value
        if not (-90.0 <= lat <= 90.0):
            raise ValueError(
                f"spill_location_latlon latitude out of range [-90, 90]: {lat!r} "
                f"(expected (lat, lon) order)"
            )
        if not (-180.0 <= lon <= 180.0):
            raise ValueError(
                f"spill_location_latlon longitude out of range [-180, 180]: {lon!r} "
                f"(expected (lat, lon) order)"
            )
        return value

    @field_validator("well_location_latlon")
    @classmethod
    def _validate_well_latlon(
        cls, value: tuple[float, float] | None
    ) -> tuple[float, float] | None:
        """Enforce ``(lat, lon)`` ranges on the pumping well, when supplied."""
        if value is None:
            return None
        lat, lon = value
        if not (-90.0 <= lat <= 90.0):
            raise ValueError(
                f"well_location_latlon latitude out of range [-90, 90]: {lat!r} "
                f"(expected (lat, lon) order)"
            )
        if not (-180.0 <= lon <= 180.0):
            raise ValueError(
                f"well_location_latlon longitude out of range [-180, 180]: {lon!r} "
                f"(expected (lat, lon) order)"
            )
        return value


class PlumeLayerURI(LayerURI):
    """A ``LayerURI`` for a MODFLOW plume layer, plus narration scalars.

    Extends ``LayerURI`` field-for-field so it still maps onto
    ``map-command load-layer`` with no translation (same as every other layer).
    Adds the two structured numbers the agent narrates about the plume so the
    LLM cites typed fields, never invents them (invariant 1, FR-AS-7):

        max_concentration_mgl: peak contaminant concentration in the plume,
            mg/L (>= 0).
        plume_area_km2: areal footprint of the plume above the detection
            threshold, km^2 (>= 0).

    ``layer_type`` for a plume is typically ``"raster"`` (a concentration COG),
    but the base contract's vocabulary is inherited unchanged - no new format
    set is introduced (rasters COG; vectors FlatGeobuf/GeoParquet).
    """

    max_concentration_mgl: float = Field(ge=0.0)
    plume_area_km2: float = Field(ge=0.0)


class SeepageLayerURI(LayerURI):
    """A ``LayerURI`` for the river-seepage (RIV leakage) layer + narration scalars.

    The companion of ``PlumeLayerURI`` for the sprint-17 river-coupled MODFLOW
    engine. The postprocess reads the GWF cell-by-cell budget RIV term - the
    per-reach-cell head-dependent exchange flux Q = C*(stage - h) - and renders
    a DIVERGING gaining/losing-stream COG (negative = the river GAINS water from
    the aquifer i.e. baseflow OUT of the aquifer; positive = the river LOSES
    water to the aquifer i.e. seepage INTO the aquifer, MF6 RIV sign convention:
    a positive budget ``q`` is flow FROM the boundary INTO the cell, so positive
    = aquifer-recharging losing reach).

    Extends ``LayerURI`` field-for-field so it maps onto ``map-command
    load-layer`` with no translation (same as every other layer). Adds the
    structured numbers the agent narrates about the river<->aquifer exchange so
    the LLM cites typed fields, never invents them (invariant 1, FR-AS-7):

        total_leakage_m3_day: net signed RIV exchange summed over all reach
            cells, m^3/day (positive = net losing/recharging the aquifer).
        gaining_m3_day: total magnitude of the GAINING (river-gaining-from-
            aquifer, baseflow) flux over the reach, m^3/day (>= 0).
        losing_m3_day: total magnitude of the LOSING (river-losing-to-aquifer,
            seepage) flux over the reach, m^3/day (>= 0).
        river_cell_count: number of RIV reach cells draped onto the grid (>= 0).

    ``layer_type`` is ``"raster"`` (a diverging seepage COG); the base
    contract's format vocabulary is inherited unchanged.
    """

    total_leakage_m3_day: float
    gaining_m3_day: float = Field(ge=0.0)
    losing_m3_day: float = Field(ge=0.0)
    river_cell_count: int = Field(ge=0)


class DrawdownLayerURI(LayerURI):
    """A ``LayerURI`` for the sustainable-yield drawdown layer + narration scalars.

    The headline output of the ``"sustainable_yield"`` archetype: the postprocess
    reads the transient GWF head (.hds) and renders head-DECLINE (pre-pumping head
    minus pumped head) as a COG so the user sees the cone of depression a pumping
    well draws down around it.

    Extends ``LayerURI`` field-for-field so it maps onto ``map-command load-layer``
    with no translation (same as every other layer). Adds the structured numbers
    the agent narrates so the LLM cites typed fields, never invents them
    (invariant 1, FR-AS-7):

        max_drawdown_m: peak head decline anywhere in the domain, m (>= 0).
        head_decline_timeseries: OPTIONAL per-step head decline at the well (or a
            monitoring cell), m, one value per saved transient step. None when the
            run published a single steady/peak frame with no time series.

    ``layer_type`` is ``"raster"`` (a drawdown COG); the base contract's format
    vocabulary is inherited unchanged.
    """

    max_drawdown_m: float = Field(ge=0.0)
    head_decline_timeseries: list[float] | None = None


class DewaterLayerURI(LayerURI):
    """A ``LayerURI`` for the mine-dewatering DRN-flux layer + narration scalars.

    The headline output of the ``"mine_dewatering"`` archetype: the postprocess
    reads the GWF cell-by-cell budget DRN term (the per-cell head-dependent drain
    flux Q = C*(h - drain_elev)) over the pit footprint and renders the dewatering
    rate as a COG, narrating the total water the pit must pump to stay dewatered.

    Extends ``LayerURI`` field-for-field so it maps onto ``map-command load-layer``
    with no translation (same as every other layer). Adds the structured numbers
    the agent narrates so the LLM cites typed fields, never invents them
    (invariant 1, FR-AS-7):

        dewatering_rate_m3_day: total DRN outflow magnitude summed over the pit
            drain cells, m^3/day (>= 0) - the pumping rate the pit needs.
        drain_cell_count: number of DRN drain cells draped onto the grid (>= 0).

    ``layer_type`` is ``"raster"`` (a dewatering-rate COG); the base contract's
    format vocabulary is inherited unchanged.
    """

    dewatering_rate_m3_day: float = Field(ge=0.0)
    drain_cell_count: int = Field(ge=0)


class BudgetPartitionLayerURI(LayerURI):
    """A ``LayerURI`` for the regional-water-budget zonal partition + scalars.

    The headline output of the ``"regional_water_budget"`` archetype: the
    postprocess reads the GWF cell-by-cell budget and partitions the flow terms
    (CHD in/out, RIV, WEL, storage) by zone, narrating where the regional water
    goes. The ``layer_type`` may be ``"vector"`` (a per-zone polygon carrying the
    partitioned budget) or ``"raster"`` (a zone-id raster); the base contract's
    format vocabulary is inherited unchanged.

    Extends ``LayerURI`` field-for-field so it maps onto ``map-command load-layer``
    with no translation (same as every other layer). Adds the structured budget the
    agent narrates so the LLM cites typed fields, never invents them
    (invariant 1, FR-AS-7):

        budget_partition_m3_day: mapping of zone/term label -> signed flow rate,
            m^3/day (positive = into the aquifer/zone, MF6 budget sign convention).
            e.g. ``{"upgradient_chd_in": 1200.0, "downgradient_chd_out": -1180.0,
            "storage": -20.0}``.
    """

    budget_partition_m3_day: dict[str, float]


class MoundingLayerURI(LayerURI):
    """A ``LayerURI`` for the MAR groundwater-mounding layer + narration scalars.

    The headline output of the ``"MAR"`` (managed aquifer recharge) archetype: the
    postprocess reads the transient GWF head (.hds) and renders the mound (pumped/
    recharged head minus pre-recharge head) as a COG so the user sees how high the
    water table rises under the infiltration basin.

    Extends ``LayerURI`` field-for-field so it maps onto ``map-command load-layer``
    with no translation (same as every other layer). Adds the structured numbers
    the agent narrates so the LLM cites typed fields, never invents them
    (invariant 1, FR-AS-7):

        max_mounding_m: peak head RISE (mounding) anywhere in the domain, m (>= 0).
        recharged_volume_m3: OPTIONAL total volume of water recharged into the
            aquifer over the simulation, m^3 (>= 0). None when not computed.

    ``layer_type`` is ``"raster"`` (a mounding COG); the base contract's format
    vocabulary is inherited unchanged.
    """

    max_mounding_m: float = Field(ge=0.0)
    recharged_volume_m3: float | None = Field(default=None, ge=0.0)


class ASRLayerURI(LayerURI):
    """A ``LayerURI`` for the ASR (aquifer storage & recovery) layer + scalars.

    The headline output of the ``"ASR"`` archetype: the postprocess reads the
    transient GWF head (.hds) at the ASR well and renders a representative head
    surface as a COG, narrating the cyclic inject/recover storage behavior and the
    recovery efficiency (the fraction of injected water recovered).

    Extends ``LayerURI`` field-for-field so it maps onto ``map-command load-layer``
    with no translation (same as every other layer). Adds the structured numbers
    the agent narrates so the LLM cites typed fields, never invents them
    (invariant 1, FR-AS-7):

        recovery_efficiency: OPTIONAL fraction (dimensionless, 0..1) of injected
            water recovered over the ASR cycle(s). None when not computed.
        head_timeseries: OPTIONAL per-step head at the ASR well, m, one value per
            saved transient step (the inject-rise / recover-fall sawtooth). None
            when the run published a single frame with no time series.

    ``layer_type`` is ``"raster"`` (a head COG); the base contract's format
    vocabulary is inherited unchanged.
    """

    recovery_efficiency: float | None = Field(default=None, ge=0.0, le=1.0)
    head_timeseries: list[float] | None = None


class HydroperiodLayerURI(LayerURI):
    """A ``LayerURI`` for the wetland-hydroperiod layer + narration scalars.

    The headline output of the ``"wetland_hydroperiod"`` archetype: the
    postprocess reads the transient GWF head (.hds) under the wetland footprint
    and renders the seasonal head-range (max minus min water table over the
    transient periods) as a COG, narrating how much the wetland water table swings
    across the recharge/ET seasons.

    Extends ``LayerURI`` field-for-field so it maps onto ``map-command load-layer``
    with no translation (same as every other layer). Adds the structured numbers
    the agent narrates so the LLM cites typed fields, never invents them
    (invariant 1, FR-AS-7):

        seasonal_head_range_m: the seasonal water-table swing (max head minus min
            head over the wetland) m (>= 0).
        head_timeseries: OPTIONAL per-step head under the wetland, m, one value
            per saved transient step (the seasonal rise/fall). None when the run
            published a single frame with no time series.

    ``layer_type`` is ``"raster"`` (a seasonal-range COG); the base contract's
    format vocabulary is inherited unchanged.
    """

    seasonal_head_range_m: float = Field(ge=0.0)
    head_timeseries: list[float] | None = None
