"""MODFLOW 6 groundwater-engine contracts (sprint-13 Stage 1, §2.3 MODFLOW
integration, OQ-9 mf6-gwt solute transport).

Two shapes back the Case 2 groundwater-contamination demo path
(news article -> parameter extraction -> MODFLOW run -> plume layer):

- ``MODFLOWRunArgs``  — the forcing parameters the agent confirms with the user
  before submitting a MODFLOW run. Consumed by the engine adapter
  (``services/workers/modflow/gwt_adapter.py``, job-0221) that maps these to
  MF6-GWT input files via ``flopy``, and by the agent-side
  ``run_modflow_job`` tool (job-0227).
- ``PlumeLayerURI`` — the postprocess output layer. Extends ``LayerURI``
  field-for-field (so it still maps onto ``map-command load-layer`` with no
  translation, like every other layer) and adds the two plume scalars the
  agent narrates: peak concentration and plume footprint.

Design notes
------------
- ``spill_location_latlon`` is ordered ``(lat, lon)`` — this is a single point,
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
- ``contaminant`` is a free ``str`` (open by design — the contaminant name is an
  open vocabulary, e.g. "benzene", "TCE", "PFOA"; the engine maps it to MF6-GWT
  transport parameters). It is non-numeric, so it stays a scalar.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from .common import GraceModel
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
    "DEFAULT_STREAMBED_CONDUCTANCE_M2_DAY",
    "DEFAULT_STREAMBED_THICKNESS_M",
]


# TENTATIVE demo defaults (sprint-13 manifest OQ-3). Narrated as demo values,
# not site-specific hydrogeology, by the Case 2 composer.
DEFAULT_AQUIFER_K_MS: float = 1e-4  # hydraulic conductivity, m/s (sandy coastal plain)
DEFAULT_POROSITY: float = 0.3  # effective porosity, dimensionless


class MODFLOWRunArgs(GraceModel):
    """Forcing parameters for a MODFLOW 6 + MF6-GWT groundwater run.

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

    River-coupling fields (sprint-17 J9 — ADDITIVE, all optional; the pure-spill
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
    but the base contract's vocabulary is inherited unchanged — no new format
    set is introduced (rasters COG; vectors FlatGeobuf/GeoParquet).
    """

    max_concentration_mgl: float = Field(ge=0.0)
    plume_area_km2: float = Field(ge=0.0)


class SeepageLayerURI(LayerURI):
    """A ``LayerURI`` for the river-seepage (RIV leakage) layer + narration scalars.

    The companion of ``PlumeLayerURI`` for the sprint-17 river-coupled MODFLOW
    engine. The postprocess reads the GWF cell-by-cell budget RIV term — the
    per-reach-cell head-dependent exchange flux Q = C*(stage - h) — and renders
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
