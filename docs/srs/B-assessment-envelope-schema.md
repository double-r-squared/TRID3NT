## Appendix B: AssessmentEnvelope Schema

> **Status: preemptive.** This appendix is a working specification drafted before implementation. Concrete schemas, field names, types, and conventions are subject to revision once implementation surfaces real constraints (SFINCS output structure, ADK serialization behavior, MongoDB MCP query patterns, etc.). Treat as the starting point, not the contract — changes flow back into this appendix as they're learned.

### B.1 Purpose

The `AssessmentEnvelope` is the system's central output structure — what every hazard engine produces, what the agent's narrative reads from, what gets persisted in the `runs` collection, what feeds the UI's layer loading. A single, consistent shape across in-memory (Pydantic), wire (JSON over WebSocket), and storage (MongoDB documents).

### B.2 Top-level structure

```python
class AssessmentEnvelope(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity
    envelope_id: str                  # ULID
    project_id: str                   # ULID, links to projects collection
    session_id: str                   # ULID, links to sessions collection

    # Mode discriminator
    envelope_type: Literal["modeled", "discovered"]
    # "modeled": produced by a solver-backed workflow; solver_run_ids populated
    # "discovered": produced by show_hazard_layer; solver_run_ids is empty; metrics
    #               come from summarize_layer_in_bbox over existing public data

    # Classification
    hazard_type: Literal["flood", "groundwater", "wildfire", "seismic", "spill"]
    workflow_name: str                # e.g., "run_storm_surge_flood", "show_hazard_layer"

    # Spatial and temporal extent
    bbox: tuple[float, float, float, float]   # [minLon, minLat, maxLon, maxLat]
    crs: str = "EPSG:4326"                    # bbox CRS; always 4326 in v0.1
    time_range: TimeRange | None              # event time; None for synthetic / discovery

    # Forcing summary (modeled only; None for discovered)
    forcing: ForcingSummary | None

    # Catalog reference (discovered only; None for modeled)
    catalog_entries: list[CatalogReference] | None

    # Outputs
    layers: list[ResultLayer]                 # all renderable result layers
    metrics: BaseMetrics                      # empty base; subtype payloads carry real metrics

    # Provenance
    provenance: Provenance

    # Lifecycle
    created_at: datetime
    completed_at: datetime
    solver_run_ids: list[str]                 # ULIDs of solver runs; empty list for discovered

    # Subtype payloads (discriminator: hazard_type)
    flood: FloodPayload | None = None
    groundwater: GroundwaterPayload | None = None  # v0.2+
    wildfire: WildfirePayload | None = None         # v0.2+
    seismic: SeismicPayload | None = None           # v0.3+
    spill: SpillPayload | None = None               # v0.3+
```

For a given envelope, exactly one subtype field is populated; the rest are `None`. The populated one matches `hazard_type`. The `envelope_type` field is independent of `hazard_type` and indicates how the envelope was produced (modeling vs discovery).

### B.3 Supporting types

```python
class TimeRange(BaseModel):
    start: datetime                   # UTC
    end: datetime                     # UTC

class ForcingSummary(BaseModel):
    forcing_type: Literal[
        "storm_surge",
        "pluvial_synthetic",
        "fluvial_synthetic",
        "news_derived",
        "user_supplied"
    ]
    source: str                       # human-readable, e.g., "NHC ATCF, Hurricane Ian"
    parameters: dict                  # forcing-specific; validated per workflow
    inputs_uri: str | None            # GCS URI to forcing data file, if any

class ResultLayer(BaseModel):
    layer_id: str                     # stable ID; used in map-command messages
    name: str                         # human-readable display name
    layer_type: Literal["raster", "vector"]
    uri: str                          # gs://... canonical location
    style_preset: str                 # references the QML preset library
    temporal: TemporalConfig | None   # present iff layer is time-varying
    role: Literal["primary", "context", "input"]
    units: str | None                 # e.g., "meters", "m/s", or None for categorical

class TemporalConfig(BaseModel):
    start: datetime
    end: datetime
    step_seconds: int

class DataSource(BaseModel):
    name: str                         # e.g., "USGS 3DEP"
    uri: str                          # the actual data file used
    accessed_at: datetime

class Provenance(BaseModel):
    data_sources: list[DataSource]
    article_ids: list[str]            # MongoDB article IDs, if news-derived
    event_id: str | None              # MongoDB event ID, if news-derived

class CatalogReference(BaseModel):
    catalog_entry_id: str             # references public_hazard_catalog.yaml entry
    title: str                        # denormalized for narrative use
    agency: str                       # denormalized for narrative use
    access_url: str                   # the URL fetched for this layer
    license: str                      # license text or URL

class BaseMetrics(BaseModel):
    pass                              # subtype payloads carry the real fields
```

### B.4 Flood subtype (v0.1)

```python
class FloodPayload(BaseModel):
    metrics: FloodMetrics

class FloodMetrics(BaseMetrics):
    # Spatial extent of impact
    flooded_area_km2: float

    # Depth statistics, computed over flooded cells only
    max_depth_m: float
    mean_depth_m: float
    p95_depth_m: float                # 95th percentile

    # Velocity, if the run computed it
    max_velocity_m_s: float | None

    # Affected assets, optional based on which fetchers ran
    affected_buildings_count: int | None
    affected_buildings_by_depth: dict[str, int] | None
    # e.g., {"0-0.5m": 412, "0.5-1m": 251, "1-2m": 132, "2m+": 52}

    affected_critical_facilities: list[CriticalFacility] | None
    population_exposed: int | None

    # Solver provenance
    solver_version: str               # e.g., "sfincs-v2.0.4"
    grid_resolution_m: float
    simulation_duration_hours: int

class CriticalFacility(BaseModel):
    name: str
    category: Literal["school", "hospital", "fire_station", "police", "other"]
    coordinates: tuple[float, float]  # [lon, lat], EPSG:4326
    max_depth_m: float
```

Future hazard subtypes (`GroundwaterPayload`, `WildfirePayload`, etc.) follow the same pattern: a payload type with a metrics field, hazard-specific.

### B.5 Wire form

`AssessmentEnvelope.model_dump(mode="json")` produces the canonical wire form. Same shape across:
- Workflow function return values
- `tool-call-complete.metrics` field in the WebSocket protocol (Appendix A)
- `runs.assessment` field in the MongoDB `runs` collection (schema in a later round)
- Context provided to the LLM when generating narrative

### B.6 Example: modeled flood envelope (v0.1)

```json
{
  "schema_version": "v1",
  "envelope_id": "01HX...",
  "project_id": "01HX...",
  "session_id": "01HX...",
  "envelope_type": "modeled",
  "hazard_type": "flood",
  "workflow_name": "run_storm_surge_flood",
  "bbox": [-82.10, 26.40, -81.60, 26.90],
  "crs": "EPSG:4326",
  "time_range": {
    "start": "2022-09-28T00:00:00Z",
    "end": "2022-09-30T00:00:00Z"
  },
  "forcing": {
    "forcing_type": "storm_surge",
    "source": "NHC ATCF, Hurricane Ian (AL092022)",
    "parameters": {
      "storm_id": "AL092022",
      "max_winds_kt": 140,
      "saffir_simpson": 4
    },
    "inputs_uri": "gs://bucket/forcings/al092022_track.fgb"
  },
  "catalog_entries": null,
  "layers": [
    {
      "layer_id": "max_depth",
      "name": "Maximum flood depth",
      "layer_type": "raster",
      "uri": "gs://bucket/runs/01HX.../max_depth.tif",
      "style_preset": "depth-blue",
      "temporal": null,
      "role": "primary",
      "units": "meters"
    },
    {
      "layer_id": "depth_temporal",
      "name": "Flood depth over time",
      "layer_type": "raster",
      "uri": "gs://bucket/runs/01HX.../depth_temporal.tif",
      "style_preset": "depth-blue",
      "temporal": {
        "start": "2022-09-28T00:00:00Z",
        "end": "2022-09-30T00:00:00Z",
        "step_seconds": 3600
      },
      "role": "primary",
      "units": "meters"
    },
    {
      "layer_id": "affected_buildings",
      "name": "Affected buildings",
      "layer_type": "vector",
      "uri": "gs://bucket/runs/01HX.../affected_buildings.fgb",
      "style_preset": "buildings-graduated",
      "temporal": null,
      "role": "primary",
      "units": null
    }
  ],
  "metrics": {},
  "provenance": {
    "data_sources": [
      {
        "name": "USGS 3DEP",
        "uri": "gs://bucket/cache/dem/3dep_10m_<hash>.tif",
        "accessed_at": "2026-06-04T20:14:01Z"
      },
      {
        "name": "NHC ATCF",
        "uri": "gs://bucket/forcings/al092022_track.fgb",
        "accessed_at": "2026-06-04T20:14:05Z"
      },
      {
        "name": "Microsoft Building Footprints",
        "uri": "gs://bucket/cache/buildings/ms_<hash>.fgb",
        "accessed_at": "2026-06-04T20:14:08Z"
      }
    ],
    "article_ids": ["01HX...", "01HX..."],
    "event_id": "01HX..."
  },
  "created_at": "2026-06-04T20:14:00Z",
  "completed_at": "2026-06-04T20:22:38Z",
  "solver_run_ids": ["01HX..."],
  "flood": {
    "metrics": {
      "flooded_area_km2": 12.4,
      "max_depth_m": 4.2,
      "mean_depth_m": 0.6,
      "p95_depth_m": 2.1,
      "max_velocity_m_s": 2.8,
      "affected_buildings_count": 847,
      "affected_buildings_by_depth": {
        "0-0.5m": 412,
        "0.5-1m": 251,
        "1-2m": 132,
        "2m+": 52
      },
      "affected_critical_facilities": [
        {
          "name": "Lee Memorial Hospital",
          "category": "hospital",
          "coordinates": [-81.87, 26.65],
          "max_depth_m": 0.4
        }
      ],
      "population_exposed": 11200,
      "solver_version": "sfincs-v2.0.4",
      "grid_resolution_m": 10.0,
      "simulation_duration_hours": 48
    }
  }
}
```

### B.6b Example: discovered wildfire envelope (v0.1)

A discovery envelope produced by `show_hazard_layer("wildfire", "Washington state")`. Note `envelope_type: "discovered"`, empty `solver_run_ids`, `forcing: null`, populated `catalog_entries`, and metrics derived from spatial summary rather than simulation.

```json
{
  "schema_version": "v1",
  "envelope_id": "01HX...",
  "project_id": "01HX...",
  "session_id": "01HX...",
  "envelope_type": "discovered",
  "hazard_type": "wildfire",
  "workflow_name": "show_hazard_layer",
  "bbox": [-124.85, 45.54, -116.92, 49.00],
  "crs": "EPSG:4326",
  "time_range": null,
  "forcing": null,
  "catalog_entries": [
    {
      "catalog_entry_id": "usfs-wildfire-hazard-potential",
      "title": "Wildfire Hazard Potential",
      "agency": "USFS",
      "access_url": "https://wildfire.cr.usgs.gov/.../whp.tif",
      "license": "Public domain (US Government work)"
    }
  ],
  "layers": [
    {
      "layer_id": "whp_washington",
      "name": "Wildfire Hazard Potential",
      "layer_type": "raster",
      "uri": "gs://bucket/discoveries/01HX.../whp_wa_clip.tif",
      "style_preset": "wildfire-hazard-potential",
      "temporal": null,
      "role": "primary",
      "units": null
    }
  ],
  "metrics": {},
  "provenance": {
    "data_sources": [
      {
        "name": "USFS Wildfire Hazard Potential",
        "uri": "gs://bucket/discoveries/01HX.../whp_wa_clip.tif",
        "accessed_at": "2026-06-04T20:14:01Z"
      }
    ],
    "article_ids": [],
    "event_id": null
  },
  "created_at": "2026-06-04T20:14:00Z",
  "completed_at": "2026-06-04T20:14:08Z",
  "solver_run_ids": [],
  "wildfire": {
    "discovery_summary": {
      "total_area_km2": 184850.0,
      "area_by_class_km2": {
        "very_low": 42100.0,
        "low": 58200.0,
        "moderate": 47350.0,
        "high": 26400.0,
        "very_high": 10800.0
      },
      "high_or_very_high_pct": 0.201
    }
  }
}
```

Note: `wildfire` subtype payload is defined for v0.2 but the discovery envelope's summary fields are simple enough to define earlier as a `DiscoverySummary`-style payload. Exact subtype schema for discovery-derived wildfire data is to be finalized when the wildfire engine lands; for v0.1 the discovery payload is a permissive `dict` validated at the workflow layer.

### B.6c ImpactEnvelope (post-processing)

> **(Forward-looking — not in M1 / not in sprint-03; first member (Pelicun) targeted post-M5, see Milestone M5.5.)**

The `ImpactEnvelope` is a sibling structure to `AssessmentEnvelope`, produced by the impact post-processing tool class (Decision N). It shares the envelope plumbing — `schema_version`, `project_id`, `session_id`, `bbox`, `crs`, `time_range`, `layers`, `provenance`, lifecycle fields — and adds fields that describe the upstream envelope it was derived from, the fragility/consequence library it used, and the building-level metrics it produced. On `ImpactEnvelope`, `envelope_type` takes the literal value `"impact"` as a parallel discriminator on this sibling type — readers that switch on `envelope_type` get a third arm rather than a new top-level type to dispatch on. `AssessmentEnvelope.envelope_type` (`Literal["modeled", "discovered"]`) is **not** modified by this amendment; the two literal sets are unioned only at the call site of any reader that handles both envelope types.

`hazard_type` is inherited from the parent `AssessmentEnvelope` (an impact envelope derived from a flood run carries `hazard_type: "flood"`); no new `"impact"` hazard-type value is introduced.

```python
class ImpactEnvelope(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity
    envelope_id: str                       # ULID
    project_id: str                        # ULID
    session_id: str                        # ULID
    envelope_uri: str                      # canonical URI for citation by narrative

    # Mode discriminator (parallel to AssessmentEnvelope.envelope_type;
    # readers handling both types union the literals at the call site)
    envelope_type: Literal["impact"] = "impact"

    # Lineage (binds damage/loss claims to the upstream hazard footprint)
    parent_envelope_id: str                # ULID of the source AssessmentEnvelope
    source_envelope_uri: str               # URI of the source AssessmentEnvelope
    parent_solver_run_ids: list[str]       # ULIDs of solver runs that produced the parent

    # Classification (inherited from parent)
    hazard_type: Literal["flood", "groundwater", "wildfire", "seismic", "spill"]
    workflow_name: str                     # e.g., "run_pelicun_impact"
    tool_name: Literal["pelicun"]          # extensible as new post-processors land
    tool_version: str                      # e.g., "3.9.0"

    # Spatial and temporal extent (typically copied from the parent envelope)
    bbox: tuple[float, float, float, float]
    crs: str = "EPSG:4326"
    time_range: TimeRange | None

    # Inputs to the impact run
    asset_inventory_ref: str               # URI or content hash of building/asset inventory
    hazard_intensity_measure: HazardIntensityMeasure
    monte_carlo_samples: int               # e.g., 10_000

    # Fragility / consequence provenance (per OQ-8 and Decision M citation discipline)
    fragility_source: Literal[
        "hazus_eq", "hazus_hu", "hazus_fl",
        "fema_p58", "bundled", "user_supplied"
    ]
    fragility_provenance: FragilityProvenance

    # Outputs (renderable layers — e.g., per-building damage states as FlatGeobuf)
    layers: list[ResultLayer]

    # Structured metrics — every number the narrative cites lives here
    impact: ImpactPayload

    # Provenance
    provenance: Provenance

    # Lifecycle
    created_at: datetime
    completed_at: datetime
    compute_duration_s: float              # informs cancellation-budget classification
```

**Supporting types:**

```python
class HazardIntensityMeasure(BaseModel):
    kind: Literal[
        "flood_depth_m",
        "pga_g",
        "sa_t1_g",
        "peak_drift_ratio",
        "floor_acceleration_g",
        "wind_3s_gust_mph"
    ]
    sampling_method: str                   # how the IM was sampled from the parent envelope

class FragilityProvenance(BaseModel):
    library: Literal[
        "HAZUS_EQ", "HAZUS_HU", "HAZUS_FL",
        "FEMA_P58", "USER"
    ]
    library_version: str                   # e.g., "HAZUS_FL_v6.1", "FEMA_P58_2nd"
    dlml_commit: str | None                # Damage and Loss Model Library commit hash, if bundled
    notes: str | None

class ImpactPayload(BaseModel):
    metrics: ImpactMetrics

class ImpactMetrics(BaseMetrics):
    # Damage state distribution (HAZUS DS0..DS4, or per-component for P-58)
    damage_state_distribution: dict[str, DamageStateStats]

    # Loss metrics (USD)
    repair_cost_usd: DistributionStats
    repair_cost_ratio: DistributionStats           # fraction of replacement cost

    # Downtime (days)
    repair_time_days: DistributionStats

    # Casualties (counts of people, by HAZUS severity 1-4)
    injuries_by_severity: dict[Literal["sev1", "sev2", "sev3", "sev4"], DistributionStats]
    fatalities: DistributionStats

    # Building-level safety indicators (dimensionless probabilities)
    collapse_probability: float
    unsafe_placard_probability: float

    # Run configuration
    pelicun_version: str                   # denormalized from tool_version for narrative use

class DamageStateStats(BaseModel):
    realization_count: int
    probability_mass: float                # dimensionless [0, 1]

class DistributionStats(BaseModel):
    mean: float
    median: float
    p10: float
    p50: float
    p90: float
```

`tool_name` is currently a `Literal["pelicun"]`; it widens as new post-processors are added (e.g., regional resilience indices, business-interruption tools).

---

### B.6c.1 ImpactEnvelope — Pelicun Post-Processor Output Contract (Wave 4.11 / sprint-12)

> **Status: implemented.** This section supersedes the forward-looking stub in B.6c with the concrete, implemented contract shipped in Wave 4.11 P1. The schema in `packages/contracts/src/grace2_contracts/impact_envelope.py` is the authoritative source; this section is the prose specification and amendment record.

#### Purpose

`ImpactEnvelope` is the typed aggregate produced by `postprocess_pelicun` by collapsing the per-feature damage FlatGeobuf returned by `run_pelicun_damage_assessment` into portfolio-level statistics: total and damaged structure counts, expected and P95 financial loss, displaced population, per-occupancy-class breakdowns, and provenance pointers back to the upstream hazard and damage layers.

Every numeric field is a deterministic aggregate; no LLM-generated numbers appear (Invariant 1 / Decision N). The agent narrates directly from these fields — `n_structures_damaged`, `expected_loss_usd`, `population_displaced` — without inventing values.

#### Schema reference

`packages/contracts/src/grace2_contracts/impact_envelope.py` — `ImpactEnvelope` and `OccupancyClassImpact` classes.

#### Required fields

| Field | Type | Constraint | Description |
|---|---|---|---|
| `schema_version` | `Literal["v1"]` | = `"v1"` | Schema version sentinel. |
| `n_structures_total` | `int` | `≥ 0` | All asset features in the damage layer. |
| `n_structures_damaged` | `int` | `≥ 0` | Assets with `ds_mean ≥ 1.0` (DS1+). |
| `n_structures_destroyed` | `int` | `≥ 0` | Assets with `ds_mean ≥ 3.5` (DS4-dominant). |
| `damage_state_distribution` | `dict[DamageStateKey, int]` | keys: DS0_none…DS4_complete | Modal DS counts; values sum to `n_structures_total`. |
| `total_replacement_value_usd` | `float` | `≥ 0.0` | Sum of `replacement_value` for all assets. |
| `damaged_replacement_value_usd` | `float` | `≥ 0.0` | Sum of `replacement_value` for DS1+ assets. |
| `expected_loss_usd` | `float` | `≥ 0.0` | Sum of `repair_cost_mean` across all assets. |
| `loss_percentile_95_usd` | `float` | `≥ 0.0` | Sum of `repair_cost_p95` (HAZUS-MH portfolio P95 approximation). |
| `impact_area_km2` | `float` | `≥ 0.0` | Convex-hull area of DS1+ asset centroids (km²). |
| `bbox` | `BBox` | EPSG:4326 | Full damage layer extent: `[minLon, minLat, maxLon, maxLat]`. |
| `by_occupancy_class` | `dict[str, OccupancyClassImpact]` | | Per-HAZUS-class breakdown; only classes present in the layer. |
| `pelicun_run_id` | `ULIDStr` | | ULID for this `postprocess_pelicun` run (cache-stable). |
| `damage_layer_uri` | `str` | non-empty | `gs://` URI of the FlatGeobuf aggregated. |
| `structure_inventory_source` | `StructureInventorySource` | Literal | `"USACE_NSI"`, `"MS_BUILDINGS"`, or `"USER_SUPPLIED"`. |
| `flood_layer_uri` | `str` | non-empty | `gs://` URI of the source hazard raster. |
| `fragility_set` | `str` | non-empty | Fragility set used, e.g. `"hazus_flood_v6"`. |
| `realization_count` | `int` | `> 0` | Monte-Carlo realization count from upstream run. |
| `generated_at` | `UTCDatetime` | UTC, `Z` suffix | Timestamp when `postprocess_pelicun` produced this envelope. |

**Optional population fields** (None when `structure_inventory_source != "USACE_NSI"`):

| Field | Type | Description |
|---|---|---|
| `population_total` | `int | None` | AM residential population (NSI `pop2amu65+pop2amo65`) across all assets. |
| `population_displaced` | `int | None` | Population in DS2+ assets (`loss_ratio_mean ≥ 0.20`). |
| `population_at_high_risk` | `int | None` | Population in DS3+ assets (`ds_mean ≥ 2.5`). |

#### OccupancyClassImpact fields

Each entry in `by_occupancy_class` (keyed by HAZUS occupancy code, e.g. `"RES1"`, `"COM1"`):

| Field | Type | Constraint | Description |
|---|---|---|---|
| `n_structures` | `int` | `≥ 0` | Total structures of this class. |
| `n_damaged` | `int` | `≥ 0` | DS1+ count. |
| `n_destroyed` | `int` | `≥ 0` | DS4-dominant count. |
| `expected_loss_usd` | `float` | `≥ 0.0` | Sum of `repair_cost_mean` for this class. |
| `loss_percentile_95_usd` | `float` | `≥ 0.0` | Sum of `repair_cost_p95` for this class. |
| `population` | `int | None` | `≥ 0` | AM population for this class (NSI); None if not available. |
| `population_displaced` | `int | None` | `≥ 0` | Displaced population for this class; None if not available. |

#### Provenance fields

- **`pelicun_run_id`** — ULID seeded from the input FlatGeobuf content hash + bbox. Stable: identical inputs always produce the same `pelicun_run_id`, enabling cache deduplication.
- **`damage_layer_uri`** — the `LayerURI.uri` (gs:// FlatGeobuf URI) returned by `run_pelicun_damage_assessment`. Every damage and loss claim is traceable to this layer.
- **`flood_layer_uri`** — the `hazard_raster_uri` passed to `run_pelicun_damage_assessment`. Binds the impact numbers to the specific hazard footprint that drove them.
- **`structure_inventory_source`** — typed Literal; determines whether population fields are populated.
- **`fragility_set`** — carried forward from the upstream call; the claim `"expected loss via HAZUS Flood v6.1"` cites this field (Decision M citation discipline).

#### Consumer guidance

- **Emitted by**: `postprocess_pelicun` (Wave 4.11 P2 atomic tool, sprint-12).
- **Consumed by**:
  - Agent narration: cite `n_structures_damaged`, `expected_loss_usd`, `population_displaced`, `by_occupancy_class["RES1"].n_damaged` etc. All cite-safe; no LLM-invented numbers.
  - Case summary panel (UI): headline stats block (total damage, loss, displaced population).
  - MongoDB `runs` collection: stored alongside the parent `AssessmentEnvelope` as a sub-document.
- **Wire form**: `ImpactEnvelope.model_dump(mode="json")` — same canonical pattern as all `GraceModel` subclasses.

#### Example payload (Hurricane Ian, Fort Myers — USACE NSI substrate)

Derived from the `run_pelicun_damage_assessment` run over Fort Myers CDP polygons using the job-0086 Y-flip-fixed flood COG. Values are illustrative.

```json
{
  "schema_version": "v1",
  "n_structures_total": 847,
  "n_structures_damaged": 432,
  "n_structures_destroyed": 44,
  "damage_state_distribution": {
    "DS0_none": 415,
    "DS1_slight": 183,
    "DS2_moderate": 142,
    "DS3_extensive": 63,
    "DS4_complete": 44
  },
  "total_replacement_value_usd": 211750000.0,
  "damaged_replacement_value_usd": 108000000.0,
  "expected_loss_usd": 29245000.0,
  "loss_percentile_95_usd": 51840000.0,
  "population_total": 11200,
  "population_displaced": 4980,
  "population_at_high_risk": 1870,
  "impact_area_km2": 8.4,
  "bbox": [-82.10, 26.40, -81.60, 26.90],
  "by_occupancy_class": {
    "RES1": {
      "n_structures": 612,
      "n_damaged": 318,
      "n_destroyed": 32,
      "expected_loss_usd": 20140000.0,
      "loss_percentile_95_usd": 35600000.0,
      "population": 9840,
      "population_displaced": 4210
    },
    "COM1": {
      "n_structures": 143,
      "n_damaged": 87,
      "n_destroyed": 10,
      "expected_loss_usd": 7400000.0,
      "loss_percentile_95_usd": 13200000.0,
      "population": 820,
      "population_displaced": 560
    },
    "IND1": {
      "n_structures": 92,
      "n_damaged": 27,
      "n_destroyed": 2,
      "expected_loss_usd": 1705000.0,
      "loss_percentile_95_usd": 3040000.0,
      "population": 540,
      "population_displaced": 210
    }
  },
  "pelicun_run_id": "01JZABC123DEF456GHI78901JK",
  "damage_layer_uri": "gs://grace-2-cache/pelicun_damage/01KTJX71-hash.fgb",
  "structure_inventory_source": "USACE_NSI",
  "flood_layer_uri": "gs://grace-2-hazard-prod-runs/01KTJX71NKGDMXB9TN0DV75JWK/flood_depth_peak_0086.tif",
  "fragility_set": "hazus_flood_v6",
  "realization_count": 100,
  "generated_at": "2026-06-09T14:35:22Z"
}
```

---

### B.6d Example: Hurricane Ian Pelicun ImpactEnvelope (forward-looking — not in M1 / not in sprint-03)

Derived from the modeled flood envelope in B.6 (Hurricane Ian storm surge over Fort Myers). Note `envelope_type: "impact"`, `parent_envelope_id` pointing at the source `AssessmentEnvelope`, `hazard_type: "flood"` inherited from the parent, and `fragility_source: "hazus_fl"` for HAZUS Flood v6.1.

```json
{
  "schema_version": "v1",
  "envelope_id": "01HY...",
  "project_id": "01HX...",
  "session_id": "01HX...",
  "envelope_uri": "gs://bucket/impacts/01HY.../envelope.json",
  "envelope_type": "impact",
  "parent_envelope_id": "01HX...",
  "source_envelope_uri": "gs://bucket/runs/01HX.../envelope.json",
  "parent_solver_run_ids": ["01HX..."],
  "hazard_type": "flood",
  "workflow_name": "run_pelicun_impact",
  "tool_name": "pelicun",
  "tool_version": "3.9.0",
  "bbox": [-82.10, 26.40, -81.60, 26.90],
  "crs": "EPSG:4326",
  "time_range": {
    "start": "2022-09-28T00:00:00Z",
    "end": "2022-09-30T00:00:00Z"
  },
  "asset_inventory_ref": "gs://bucket/cache/buildings/ms_<hash>.fgb",
  "hazard_intensity_measure": {
    "kind": "flood_depth_m",
    "sampling_method": "per-building point sample from max_depth raster"
  },
  "monte_carlo_samples": 10000,
  "fragility_source": "hazus_fl",
  "fragility_provenance": {
    "library": "HAZUS_FL",
    "library_version": "HAZUS_FL_v6.1",
    "dlml_commit": "a1b2c3d",
    "notes": "bundled DLML defaults; no user override"
  },
  "layers": [
    {
      "layer_id": "building_damage_states",
      "name": "Per-building damage state (most likely)",
      "layer_type": "vector",
      "uri": "gs://bucket/impacts/01HY.../building_ds.fgb",
      "style_preset": "damage-state-graduated",
      "temporal": null,
      "role": "primary",
      "units": null
    }
  ],
  "impact": {
    "metrics": {
      "damage_state_distribution": {
        "DS0": {"realization_count": 2410, "probability_mass": 0.241},
        "DS1": {"realization_count": 3120, "probability_mass": 0.312},
        "DS2": {"realization_count": 2180, "probability_mass": 0.218},
        "DS3": {"realization_count": 1490, "probability_mass": 0.149},
        "DS4": {"realization_count": 800,  "probability_mass": 0.080}
      },
      "repair_cost_usd": {
        "mean": 184500000.0, "median": 172000000.0,
        "p10": 121000000.0, "p50": 172000000.0, "p90": 268000000.0
      },
      "repair_cost_ratio": {
        "mean": 0.27, "median": 0.24, "p10": 0.14, "p50": 0.24, "p90": 0.42
      },
      "repair_time_days": {
        "mean": 142.0, "median": 118.0, "p10": 60.0, "p50": 118.0, "p90": 260.0
      },
      "injuries_by_severity": {
        "sev1": {"mean": 84.0, "median": 76.0, "p10": 41.0, "p50": 76.0, "p90": 140.0},
        "sev2": {"mean": 22.0, "median": 19.0, "p10": 9.0,  "p50": 19.0, "p90": 41.0},
        "sev3": {"mean": 7.0,  "median": 6.0,  "p10": 2.0,  "p50": 6.0,  "p90": 14.0},
        "sev4": {"mean": 3.0,  "median": 2.0,  "p10": 0.0,  "p50": 2.0,  "p90": 7.0}
      },
      "fatalities": {
        "mean": 3.0, "median": 2.0, "p10": 0.0, "p50": 2.0, "p90": 7.0
      },
      "collapse_probability": 0.018,
      "unsafe_placard_probability": 0.229,
      "pelicun_version": "3.9.0"
    }
  },
  "provenance": {
    "data_sources": [
      {
        "name": "HAZUS Flood v6.1 (bundled via Pelicun DLML)",
        "uri": "pelicun://dlml/HAZUS_FL_v6.1",
        "accessed_at": "2026-06-04T20:23:01Z"
      },
      {
        "name": "Microsoft Building Footprints",
        "uri": "gs://bucket/cache/buildings/ms_<hash>.fgb",
        "accessed_at": "2026-06-04T20:23:02Z"
      }
    ],
    "article_ids": ["01HX...", "01HX..."],
    "event_id": "01HX..."
  },
  "created_at": "2026-06-04T20:23:00Z",
  "completed_at": "2026-06-04T20:24:18Z",
  "compute_duration_s": 78.0
}
```

**Design rationale (forward-looking):**
- **Sibling, not extension.** `ImpactEnvelope` is a separate top-level type, not a new subtype of `AssessmentEnvelope`. It has its own `envelope_type` field pinned to `Literal["impact"]`; `AssessmentEnvelope.envelope_type` keeps its existing `Literal["modeled", "discovered"]` and is not extended by this amendment. Engines and post-processors emit semantically different artifacts (hazard footprint vs. building-level damage/loss) and a single envelope conflating both would force every reader to handle every field combination. The shared plumbing (`bbox`, `crs`, `layers`, `provenance`, lifecycle) is duplicated by design; the duplication is cheaper than a discriminated mega-envelope.
- **`envelope_type: "impact"` is a parallel discriminator value on the sibling class.** Readers that switch on `envelope_type` get a third arm rather than dispatching on a different top-level type. `AssessmentEnvelope`'s `Literal["modeled", "discovered"]` is not modified; code paths that handle both envelope shapes union the two literal sets at the call site (`Literal["modeled", "discovered", "impact"]`).
- **Lineage by `parent_envelope_id` + `parent_solver_run_ids`, not by overloading `solver_run_ids`.** Reusing `AssessmentEnvelope.solver_run_ids` for impact lineage would conflate "runs that produced this envelope" with "runs that produced this envelope's parent". A dedicated lineage field keeps the semantics unambiguous.
- **`hazard_type` inherited from parent, no `"impact"` hazard value.** Impact is computed against a hazard footprint; the hazard remains what it was (flood, seismic, etc.). The `hazard_type` literal in B.2 does not need extension.
- **`forcing` and `catalog_entries` are absent on `ImpactEnvelope`.** Impact envelopes do not carry their own forcing summary (the parent does) and do not reference public catalogs (the parent or its provenance does). The corresponding bullets in B.7 are amended to acknowledge the impact case explicitly.
- **`fragility_provenance` is first-class.** Per Decision M (source-authority tiers and citation discipline), every numerical claim cites its source. Damage and loss numbers must be traceable to the fragility/consequence library that produced them; the field is required, not optional.
- **Confirmation gating lives in FR-AS-8, not in the schema.** The envelope itself is data; gating is workflow-layer policy — any cost-incurring run requires explicit confirmation.

### B.7 Design rationale

- **`envelope_type` discriminator**: modeling and discovery produce semantically different artifacts but share the same shape downstream (UI rendering, narrative generation, storage). The discriminator makes the distinction explicit without forking the schema.
- **`forcing` is None for discovery, and absent on `ImpactEnvelope`**: there's no boundary condition to summarize on a discovery envelope (the catalog entry serves that role), and impact envelopes do not carry their own forcing summary — the parent `AssessmentEnvelope` does.
- **`catalog_entries` is None for modeling, and absent on `ImpactEnvelope`**: solver outputs aren't catalog-sourced, even when they read public data as inputs (those go in `provenance.data_sources` instead); impact envelopes inherit catalog provenance from their parent envelope and do not duplicate it.
- **`solver_run_ids` empty for discovery**: distinguishes computational artifacts from referential ones; supports queries like "which envelopes required actual compute?"
- **Discriminator + optional subtype payloads**: hazard-specific fields stay typed; the base stays clean; one envelope works for all hazards.
- **All metrics structured, none free-text**: every number the narrative cites lives in a typed field. The LLM reads them; it cannot invent them.
- **Layers as first-class objects, not bare URIs**: units, style preset, role, temporal config travel with the layer so the UI knows how to render and the agent knows how to describe.
- **Provenance is structured**: data sources, article IDs, event IDs as separate queryable fields, not free-text.
- **Optional fields stay optional, not absent**: `population_exposed: None` rather than omitting; keeps the schema shape stable.
- **Schema versioning from day one**: `schema_version: "v1"` as the first field; old documents stay readable when the schema evolves.
- **The base `metrics` field is empty**: forward-compatible slot; real metrics live in the subtype payload (`flood.metrics`, etc.).
- **`bbox` always EPSG:4326**: one CRS for cross-system communication; display and storage CRSes may differ but the envelope is canonical.
- **Times as UTC datetimes**: Pydantic handles conversion; storage is ISO 8601 with `Z`.
- **`solver_run_ids` as a list**: anticipates ensemble runs (averaging multiple SFINCS runs for uncertainty); single-element list is the common case for modeled envelopes; empty for discovered.

### B.8 Known open choices

- **Critical facility vocabulary**: `school, hospital, fire_station, police, other` covers v0.1; may extend (water treatment, power substations) when relevant data sources are wired.
- **Affected-buildings depth bins**: `0-0.5m, 0.5-1m, 1-2m, 2m+` is one reasonable bucketing; FEMA HAZUS uses different bins. Worth aligning to a downstream standard before locking.
- **Population source**: WorldPop vs. GHSL vs. LandScan — each has different licensing and accuracy. Pick during M5.
- **Base `metrics` field**: currently empty `BaseMetrics()`. Could drop entirely since subtypes carry their own. Kept for forward compatibility.
- **Discovery subtype schema**: for v0.1, discovery payloads use a permissive `dict` validated at the workflow layer. As discovered-data summaries become more common, formalize a `DiscoverySummary` subtype per hazard.

---

