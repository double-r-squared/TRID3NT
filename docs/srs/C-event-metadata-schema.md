## Appendix C: EventMetadata Schema

> **Status: preemptive.** This appendix is a working specification drafted before implementation. Concrete schemas, field names, types, and event-type vocabulary are subject to revision once implementation surfaces real constraints (Gemini structured-output behavior, news article variability, downstream forcing-reconstruction needs, etc.). Treat as the starting point, not the contract — changes flow back into this appendix as they're learned.

### C.1 Purpose

`EventMetadata` is the structured representation of a real-world hazard event extracted from news content. It is produced by the `extract_event_metadata` tool (FR-TA-3), stored as a document in the MongoDB `events` collection, and consumed by `model_news_event` and related dispatcher logic to map an event to model forcing.

The schema is designed so that:
- Gemini can populate it via structured output mode (the schema becomes the JSON-mode response schema)
- Pydantic validation catches malformed extraction
- The dispatcher can match on `event_type` and read the corresponding typed intensity payload
- MongoDB Atlas Vector Search can index the `embedding` field for similar-event retrieval

### C.2 Top-level structure

```python
class EventMetadata(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity
    event_id: str                     # ULID

    # Classification
    event_type: Literal[
        "hurricane",
        "tropical_storm",
        "atmospheric_river",
        "intense_rainfall",
        "dam_failure",
        "levee_failure",
        "storm_surge",
        "river_flood",
        "flash_flood",
        "other"
    ]
    confidence: float                 # 0..1, extractor's self-reported confidence

    # Identity within the event domain, when available
    canonical_name: str | None        # e.g., "Hurricane Ian", "Cyclone Yaas"
    canonical_id: str | None          # e.g., "AL092022" for ATCF storms

    # Location
    location: EventLocation

    # Time
    time_range: TimeRange
    time_classification: Literal["past", "ongoing", "forecast"]

    # Intensity (discriminated by event_type)
    intensity: IntensityIndicators

    # Source attribution
    provenance: EventProvenance

    # Search support (populated separately from extraction)
    embedding: list[float] | None
    embedding_model: str | None       # e.g., "text-embedding-005"

    # Lifecycle
    extracted_at: datetime
    extractor_version: str            # for reproducibility
```

### C.3 Supporting types

```python
class EventLocation(BaseModel):
    # Validators enforce: at least one of bbox or place_name is required
    bbox: tuple[float, float, float, float] | None  # [minLon, minLat, maxLon, maxLat]
    place_name: str | None                          # e.g., "Fort Myers, FL"
    admin_unit: AdminUnit | None                    # parsed administrative context
    geocoded: bool = False                          # True iff bbox came from geocoding

    # Granularity for client-side auto-snap and padding decisions
    granularity: Literal[
        "country", "region", "state", "city", "facility", "bbox"
    ] | None = None

    # Modeling-readiness assessment for the news pipeline / dispatcher
    precision_class: Literal[
        "point_known",        # specific named facility, point coordinates available
        "polygon_known",      # specific neighborhood with defined boundaries
        "bbox_sufficient",    # admin area where bbox is enough for the modeling task
        "imprecise",          # "near the river" — needs user spatial input
        "ambiguous",          # "Springfield" — needs disambiguation
    ] | None = None

class AdminUnit(BaseModel):
    country: str | None               # ISO 3166-1 alpha-2, e.g., "US"
    region: str | None                # state/province, e.g., "FL"
    locality: str | None              # city/town

class TimeRange(BaseModel):
    start: datetime                   # UTC
    end: datetime                     # UTC

class EventProvenance(BaseModel):
    article_ids: list[str]            # MongoDB article IDs that contributed
    primary_article_id: str           # the "main" article when synthesizing across many
    extraction_notes: str | None      # free-text caveats from the extractor

# --- Claim-set types for multi-source numerical evidence ---

class NumericClaim(BaseModel):
    """A single numerical claim from a single source, with provenance."""
    value: float
    unit: str                         # canonical unit string, e.g., "kt", "mb", "ft", "inches"
    source_type: Literal[
        "agency",                     # tier 1 or 2: NWS, USGS, NHC, etc.
        "major_news",                 # tier 3: AP, Reuters, NYT, WaPo with direct sourcing
        "regional_news",              # tier 4: regional dailies, local TV websites
        "aggregator",                 # tier 5: news aggregators, secondary reporting
        "social",                     # tier 6: social/community (deferred to v0.2+)
        "other",
    ]
    source_id: str                    # article_id or agency feed entry id
    source_url: str
    observation_time: datetime | None # when the value was actually measured/observed, if known
    reporting_time: datetime          # when the source reported it
    confidence: float | None          # source's stated confidence, if any (0..1)
    outlier_flag: bool = False        # set by aggregation logic; True if flagged as outlier

class ClaimSet(BaseModel):
    """A set of numerical claims for one quantity across sources, with consensus."""
    claims: list[NumericClaim]
    consensus_value: float | None
    consensus_unit: str | None
    consensus_method: Literal[
        "single_source",              # only one claim, no aggregation
        "median",                     # median across non-outlier claims
        "authority_weighted",         # weighted by source_type tier
        "latest_authoritative",       # most recent agency claim
        "agent_synthesized",          # LLM-reasoned consensus (deep research mode)
    ] | None
    consensus_confidence: Literal["high", "medium", "low"] | None
    notes: str | None                 # agent commentary if relevant
```

### C.4 Intensity indicators (discriminated by `event_type`)

Every numerical field across the intensity types is a `ClaimSet`, not a bare number. This holds in both research mode (1-3 claims per set) and deep research mode (5-15 claims per set). Non-numerical fields (landfall location names, breach type enums, etc.) remain as scalar values.

```python
class IntensityIndicators(BaseModel):
    # Discriminated union: exactly one field populated based on event_type
    hurricane: HurricaneIntensity | None = None
    tropical_storm: TropicalStormIntensity | None = None
    atmospheric_river: AtmosphericRiverIntensity | None = None
    rainfall: RainfallIntensity | None = None
    dam_failure: DamFailureIntensity | None = None
    storm_surge: StormSurgeIntensity | None = None
    river_flood: RiverFloodIntensity | None = None
    flash_flood: FlashFloodIntensity | None = None
    generic: GenericIntensity | None = None  # fallback for "other"


class HurricaneIntensity(BaseModel):
    saffir_simpson: ClaimSet | None   # 1..5
    max_winds_kt: ClaimSet | None     # sustained winds at peak
    min_central_pressure_mb: ClaimSet | None
    landfall_location: str | None     # non-numeric; not claim-set wrapped

class TropicalStormIntensity(BaseModel):
    max_winds_kt: ClaimSet | None
    landfall_location: str | None

class AtmosphericRiverIntensity(BaseModel):
    ar_category: ClaimSet | None      # 1..5 (Ralph et al. scale)
    ivt_kg_m_s: ClaimSet | None       # integrated vapor transport

class RainfallIntensity(BaseModel):
    total_inches: ClaimSet | None
    duration_hours: ClaimSet | None
    peak_hourly_inches: ClaimSet | None
    return_period_years: ClaimSet | None

class DamFailureIntensity(BaseModel):
    dam_name: str | None              # non-numeric
    reservoir_volume_acre_feet: ClaimSet | None
    breach_type: Literal["overtopping", "piping", "structural", "unknown"] | None

class StormSurgeIntensity(BaseModel):
    peak_surge_ft: ClaimSet | None
    associated_storm: str | None      # non-numeric

class RiverFloodIntensity(BaseModel):
    river_name: str | None            # non-numeric
    peak_stage_ft: ClaimSet | None
    flood_stage_ft: ClaimSet | None   # official flood-stage threshold
    gauge_id: str | None              # USGS NWIS gauge if extractable

class FlashFloodIntensity(BaseModel):
    duration_hours: ClaimSet | None
    cause: Literal["thunderstorm", "training_storms", "dam_break", "unknown"] | None

class GenericIntensity(BaseModel):
    description: str                  # free-text fallback
    severity: Literal["minor", "moderate", "major", "catastrophic"] | None
```

### C.5 Example: hurricane

```json
{
  "schema_version": "v1",
  "event_id": "01HXEVT...",
  "event_type": "hurricane",
  "confidence": 0.92,
  "canonical_name": "Hurricane Ian",
  "canonical_id": "AL092022",
  "location": {
    "bbox": [-82.30, 26.20, -81.40, 27.10],
    "place_name": "Fort Myers, FL",
    "admin_unit": {
      "country": "US",
      "region": "FL",
      "locality": "Fort Myers"
    },
    "geocoded": true
  },
  "time_range": {
    "start": "2022-09-28T18:00:00Z",
    "end": "2022-09-30T06:00:00Z"
  },
  "time_classification": "past",
  "intensity": {
    "hurricane": {
      "saffir_simpson": {
        "claims": [
          {
            "value": 4,
            "unit": "category",
            "source_type": "agency",
            "source_id": "nhc-al092022-final",
            "source_url": "https://nhc.noaa.gov/data/tcr/AL092022_Ian.pdf",
            "observation_time": "2022-09-28T19:05:00Z",
            "reporting_time": "2023-04-03T00:00:00Z",
            "confidence": null,
            "outlier_flag": false
          }
        ],
        "consensus_value": 4,
        "consensus_unit": "category",
        "consensus_method": "single_source",
        "consensus_confidence": "high",
        "notes": "NHC Tropical Cyclone Report (final post-storm assessment)"
      },
      "max_winds_kt": {
        "claims": [
          {
            "value": 140,
            "unit": "kt",
            "source_type": "agency",
            "source_id": "nhc-al092022-final",
            "source_url": "https://nhc.noaa.gov/data/tcr/AL092022_Ian.pdf",
            "observation_time": "2022-09-28T19:05:00Z",
            "reporting_time": "2023-04-03T00:00:00Z",
            "confidence": null,
            "outlier_flag": false
          },
          {
            "value": 150,
            "unit": "kt",
            "source_type": "major_news",
            "source_id": "01HXART...A",
            "source_url": "https://example.com/ian-coverage",
            "observation_time": null,
            "reporting_time": "2022-09-28T20:30:00Z",
            "confidence": null,
            "outlier_flag": false
          }
        ],
        "consensus_value": 140,
        "consensus_unit": "kt",
        "consensus_method": "latest_authoritative",
        "consensus_confidence": "high",
        "notes": "NHC final assessment (140 kt) preferred over preliminary news reports (150 kt at landfall)"
      },
      "min_central_pressure_mb": {
        "claims": [
          {
            "value": 940,
            "unit": "mb",
            "source_type": "agency",
            "source_id": "nhc-al092022-final",
            "source_url": "https://nhc.noaa.gov/data/tcr/AL092022_Ian.pdf",
            "observation_time": "2022-09-28T18:35:00Z",
            "reporting_time": "2023-04-03T00:00:00Z",
            "confidence": null,
            "outlier_flag": false
          }
        ],
        "consensus_value": 940,
        "consensus_unit": "mb",
        "consensus_method": "single_source",
        "consensus_confidence": "high",
        "notes": null
      },
      "landfall_location": "Cayo Costa, FL"
    },
    "tropical_storm": null,
    "atmospheric_river": null,
    "rainfall": null,
    "dam_failure": null,
    "storm_surge": null,
    "river_flood": null,
    "flash_flood": null,
    "generic": null
  },
  "provenance": {
    "article_ids": ["01HXART...A", "01HXART...B", "01HXART...C"],
    "primary_article_id": "01HXART...A",
    "extraction_notes": null
  },
  "embedding": [0.0231, -0.1843, "..."],
  "embedding_model": "text-embedding-005",
  "extracted_at": "2026-06-04T20:13:55Z",
  "extractor_version": "v1.0.0"
}
```

### C.6 Example: atmospheric river (no canonical ID)

```json
{
  "schema_version": "v1",
  "event_id": "01HXEVT...",
  "event_type": "atmospheric_river",
  "confidence": 0.81,
  "canonical_name": null,
  "canonical_id": null,
  "location": {
    "bbox": [-124.0, 36.5, -121.5, 39.0],
    "place_name": "San Francisco Bay Area, CA",
    "admin_unit": {
      "country": "US",
      "region": "CA",
      "locality": null
    },
    "geocoded": true
  },
  "time_range": {
    "start": "2024-02-04T00:00:00Z",
    "end": "2024-02-06T00:00:00Z"
  },
  "time_classification": "past",
  "intensity": {
    "hurricane": null,
    "tropical_storm": null,
    "atmospheric_river": {
      "ar_category": {
        "claims": [
          {
            "value": 4,
            "unit": "category",
            "source_type": "regional_news",
            "source_id": "01HXART...",
            "source_url": "https://example-regional.com/ar-feb-2024",
            "observation_time": null,
            "reporting_time": "2024-02-05T14:00:00Z",
            "confidence": null,
            "outlier_flag": false
          }
        ],
        "consensus_value": 4,
        "consensus_unit": "category",
        "consensus_method": "single_source",
        "consensus_confidence": "medium",
        "notes": "AR category inferred from CW3E classification mentioned in article; no direct CW3E source fetched in research mode"
      },
      "ivt_kg_m_s": {
        "claims": [
          {
            "value": 850.0,
            "unit": "kg/m/s",
            "source_type": "regional_news",
            "source_id": "01HXART...",
            "source_url": "https://example-regional.com/ar-feb-2024",
            "observation_time": null,
            "reporting_time": "2024-02-05T14:00:00Z",
            "confidence": null,
            "outlier_flag": false
          }
        ],
        "consensus_value": 850.0,
        "consensus_unit": "kg/m/s",
        "consensus_method": "single_source",
        "consensus_confidence": "low",
        "notes": "Single regional-news source; deep research mode would query CW3E directly"
      }
    },
    "rainfall": null,
    "dam_failure": null,
    "storm_surge": null,
    "river_flood": null,
    "flash_flood": null,
    "generic": null
  },
  "provenance": {
    "article_ids": ["01HXART..."],
    "primary_article_id": "01HXART...",
    "extraction_notes": "AR category inferred from CW3E classification mentioned in article"
  },
  "embedding": [0.0188, -0.0921, "..."],
  "embedding_model": "text-embedding-005",
  "extracted_at": "2026-06-04T20:13:55Z",
  "extractor_version": "v1.0.0"
}
```

### C.7 Production and consumption

**Production** — by `extract_event_metadata(article_text)`:
1. Gemini is invoked in structured-output mode with the `EventMetadata` schema attached
2. Gemini returns a populated object
3. The tool validates via Pydantic; on validation failure, the article is flagged for re-extraction or human review
4. The tool computes `embedding` via `text-embedding-005` over a canonical text representation (canonical name + event type + location + time + key intensity values)
5. The tool assigns `event_id` (ULID), `extracted_at`, and `extractor_version`
6. The document is upserted into the `events` collection per the metadata-payload pattern (§3.7)

**Consumption** — by `model_news_event(event_id)`:
1. Read the event document from MongoDB
2. Match on `event_type` to dispatch to the appropriate forcing-reconstruction logic:
   - `hurricane`, `tropical_storm` → fetch NHC ATCF track → `run_storm_surge_flood`
   - `intense_rainfall`, `atmospheric_river` → reconstruct precipitation event → `run_pluvial_flood`
   - `river_flood` → fetch USGS NWIS stage data → `run_fluvial_flood`
   - `dam_failure`, `levee_failure` → user-supplied or LLM-estimated breach hydrograph → `run_fluvial_flood`
   - `storm_surge` → fetch associated storm track → `run_storm_surge_flood`
   - `flash_flood`, `other` → ask user for clarification rather than dispatch
3. The returned `AssessmentEnvelope.provenance.event_id` references this event; `provenance.article_ids` is copied from the event's provenance

### C.8 Design rationale

- **Discriminated intensity mirrors `AssessmentEnvelope`**: same pattern across the system; reduces cognitive load and code duplication.
- **`confidence` as a first-class field**: extractor's self-assessment is preserved; the dispatcher can ask the user for confirmation when low (e.g., < 0.6).
- **`canonical_id` optional**: storms have ATCF IDs; atmospheric rivers don't; dam failures don't. The field is there when applicable, `None` otherwise.
- **Location allows bbox, place_name, or both**: real-world articles vary; the schema reflects all realistic cases including ambiguous ones requiring user clarification.
- **`time_classification` drives data-source selection**: past events use historical archives (NHC ATCF archive, USGS NWIS history); forecast events use forecast products (NHC active advisories); ongoing events may mix both.
- **`embedding` is optional in the schema**: population happens after extraction; the schema accommodates the just-extracted state and the fully-indexed state.
- **`extractor_version` captured**: when extraction logic changes, old documents stay attributable to their producer. Reproducibility for the news pipeline.
- **`extraction_notes` lets the extractor flag uncertainty in text**: free-text escape hatch for "I inferred X from Y" annotations that don't fit structured fields.
- **Provenance distinguishes primary from contributing articles**: if multiple articles feed one extraction, the primary is the canonical one for citation.
- **`generic` intensity handles "other" events**: hazards happen that don't fit pre-defined categories; the system degrades gracefully rather than failing extraction.

### C.9 Known open choices

- **Embedding dimension**: `text-embedding-005` is 768-dim by default but supports configurable down to 128. Trade off recall vs. index size.
- **Event type taxonomy growth**: the list of 10 covers v0.1 hazards (flood-adjacent); expands when wildfire, seismic, and contaminant transport engines land.
- **`canonical_id` namespacing**: for storms, ATCF IDs work; no agreed-upon ID system for floods, ARs, rainfall. May need a `canonical_id_scheme` field later.
- **Multi-event articles**: one article might describe multiple distinct events (e.g., a season summary). Schema currently assumes one event per extraction call; the agent may need to extract multiple from one article and produce N documents.
- **Confidence calibration**: `confidence` is currently the model's self-report. Could leave as-is or post-process via calibration.
- **Re-extraction policy**: when extractor_version changes, do existing documents get re-extracted automatically, on-demand, or never?

---

