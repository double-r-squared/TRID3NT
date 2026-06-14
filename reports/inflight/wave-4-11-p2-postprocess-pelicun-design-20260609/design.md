# Design: `postprocess_pelicun` — Wave 4.11 P2

**Author:** engine specialist  
**Date:** 2026-06-09  
**Status:** DRAFT — for Wave 4.11 implementation specialist  
**Output contract:** `packages/contracts/src/grace2_contracts/impact_envelope.py`  
**SRS refs:** Decision N (line 116), §2.3, FR-CE-5/6/7, FR-TA-1, FR-AS-7/8, Appendix B.6c

---

## 1. Inputs

### 1.1 Primary input: Pelicun damage FlatGeobuf

`run_pelicun_damage_assessment` (see `services/agent/src/grace2_agent/tools/run_pelicun_damage_assessment.py`, lines 1295-1303) returns a `LayerURI` whose `.uri` is a `gs://` path to a FlatGeobuf of asset point/polygon features. The tool does NOT return a DataFrame or netCDF — it returns a GCS-backed FlatGeobuf. The implementation specialist must load it via `geopandas.read_file(local_path, driver="FlatGeobuf")` after downloading to a tempfile.

**Per-feature columns guaranteed on the FlatGeobuf** (produced at `run_pelicun_damage_assessment.py:932-943`):

| Column | Type | Notes |
|--------|------|-------|
| `component_type_used` | str | HAZUS occupancy class actually used (may differ from NSI `occtype` if a fallback fired) |
| `fragility_curve_id` | str | HAZUS curve ID for provenance |
| `hazard_depth_sampled` | float | Raster value at centroid, raster units (typically metres) |
| `ds_mean` | float | Expected DS, range 0.0–4.0 |
| `ds_p05` | float | 5th-pct DS |
| `ds_p95` | float | 95th-pct DS |
| `loss_ratio_mean` | float | Expected loss ratio 0.0–0.6 |
| `loss_ratio_p95` | float | 95th-pct loss ratio |
| `repair_cost_mean` | float | USD |
| `repair_cost_p95` | float | USD |
| `replacement_value` | float | USD |

**NSI-specific columns** (present when `structure_inventory_source == "USACE_NSI"`; sourced from `fetch_usace_nsi.py:186-203`):

| Column | Type | NSI source |
|--------|------|-----------|
| `occtype` | str | NSI canonical occupancy class |
| `component_type` | str | Copy of `occtype` (added by `_geojson_to_fgb`, line 454-458) |
| `pop2amu65` | float/int | AM population under 65 |
| `pop2amo65` | float/int | AM population 65+ |
| `pop2pmu65` | float/int | PM population under 65 |
| `pop2pmo65` | float/int | PM population 65+ |
| `val_struct` | float | Structure replacement value (USD) |

**MS_BUILDINGS source**: no `occtype`, no population columns. `component_type_used` is `"RES1"` for all features (the fallback default at `run_pelicun_damage_assessment.py:857`).

### 1.2 Tool signature

```
postprocess_pelicun(
    damage_layer_uri: str,           # LayerURI.uri from run_pelicun_damage_assessment
    flood_layer_uri: str,            # hazard_raster_uri that was passed upstream
    structure_inventory_source: StructureInventorySource,  # "USACE_NSI" | "MS_BUILDINGS" | "USER_SUPPLIED"
    fragility_set: str,              # carried forward, e.g. "hazus_flood_v6"
    realization_count: int,          # carried forward from upstream run
    pelicun_run_id: str | None = None,  # if None, derive from input hashes (see §8)
) -> ImpactEnvelope
```

The tool does NOT re-invoke Pelicun — it aggregates the already-computed FlatGeobuf. It is a pure aggregation step.

---

## 2. Aggregation Logic

### 2.1 Damage-state classification

All thresholds derive from the `ImpactEnvelope` schema docstring (lines 134-148) and the HAZUS DS ladder established at `run_pelicun_damage_assessment.py:178-189`.

- **`n_structures_total`**: `len(gdf)` — all features in the FlatGeobuf
- **`n_structures_damaged`**: count where `ds_mean >= 1.0`
- **`n_structures_destroyed`**: count where `ds_mean >= 3.5`

**`damage_state_distribution`**: Modal DS per feature, where modal DS is `round(ds_mean)` clamped to `{0,1,2,3,4}`. Bin edges:

| Modal DS | Label | `ds_mean` range |
|----------|-------|----------------|
| 0 | DS0_none | [0.0, 0.5) |
| 1 | DS1_slight | [0.5, 1.5) |
| 2 | DS2_moderate | [1.5, 2.5) |
| 3 | DS3_extensive | [2.5, 3.5) |
| 4 | DS4_complete | [3.5, 4.0] |

Use `np.round(gdf["ds_mean"]).clip(0, 4).astype(int)` to compute modal DS. The sum of all five bucket counts must equal `n_structures_total` (assert this before returning).

### 2.2 Per-occupancy-class breakdown (`by_occupancy_class`)

Group the GeoDataFrame by `component_type_used`. For each group, compute `OccupancyClassImpact` fields independently using the same threshold logic as the top-level totals. Population fields are `None` for the group when `structure_inventory_source != "USACE_NSI"` or when the NSI population columns are absent/all-null for that occupancy class.

---

## 3. Loss Computation

### 3.1 `expected_loss_usd`

Sum of `repair_cost_mean` across **all** features (DS0 included — features outside the hazard footprint have `repair_cost_mean == 0.0` per `run_pelicun_damage_assessment.py:883-890`).

### 3.2 `loss_percentile_95_usd`

Sum of `repair_cost_p95` across **all** features. The schema docstring (lines 143-146) explicitly documents this as the HAZUS-MH portfolio P95 approximation (sum of per-asset P95s, not a true joint-distribution P95). No Monte-Carlo re-sampling is needed.

### 3.3 `total_replacement_value_usd` and `damaged_replacement_value_usd`

- `total_replacement_value_usd`: sum of `replacement_value` across all features
- `damaged_replacement_value_usd`: sum of `replacement_value` for features with `ds_mean >= 1.0`

### 3.4 Per-class loss fields in `OccupancyClassImpact`

Same pattern applied per group slice:
- `expected_loss_usd`: sum of `repair_cost_mean` for that occupancy class
- `loss_percentile_95_usd`: sum of `repair_cost_p95` for that occupancy class

---

## 4. Population Impact

Population computation only fires when `structure_inventory_source == "USACE_NSI"` AND the NSI columns `pop2amu65`, `pop2amo65` exist on the GeoDataFrame (check `"pop2amu65" in gdf.columns`). If absent or source is not NSI, all three population fields are `None` on both `ImpactEnvelope` and each `OccupancyClassImpact`.

**When NSI source is confirmed:**

- **`population_total`**: `int(gdf["pop2amu65"].fillna(0) + gdf["pop2amo65"].fillna(0)).sum()` — AM residential population across all assets
- **`population_displaced`** (schema line 247-255): sum of AM population for features where `loss_ratio_mean >= 0.20` (DS2+ boundary). Rationale: `loss_ratio_mean >= 0.20` is the `DS2_moderate` threshold from `_DS_LOSS_RATIO_BREAKS` at `run_pelicun_damage_assessment.py:188`. The schema docstring explicitly cites this threshold.
- **`population_at_high_risk`** (schema line 256-264): sum of AM population for features where `ds_mean >= 2.5` (DS3+ extensive-to-complete). Rationale: `ds_mean >= 2.5` rounds to DS3 per the modal-DS binning in §2.1.

**Per-class population** in `OccupancyClassImpact`:
- `population`: sum of AM population for that occupancy class, or `None`
- `population_displaced`: sum of AM population where `loss_ratio_mean >= 0.20` for that class, or `None`

**Note on PM population columns** (`pop2pmu65`, `pop2pmo65`): the schema uses AM population only (the canonical daytime-exposure baseline). PM columns are present in NSI but not used in this tool — log a debug message noting their availability for future extension.

---

## 5. Spatial Summary

### 5.1 `impact_area_km2`

**Decision**: use convex hull of damaged asset centroids (features with `ds_mean >= 1.0`), not the flood layer extent.

**Rationale**: the schema docstring is explicit (lines 270-277): "Area (km²) of the convex hull of damaged asset centroids (DS1+). Approximates the footprint of meaningful structural impact." Using the flood layer extent would include areas with no damaged structures, overstating the impact footprint. Zero when no damaged assets exist.

**Implementation**: 
1. Filter GDF to `ds_mean >= 1.0`
2. Compute `damaged_gdf.geometry.centroid` (already in whatever CRS the FGB was written in, likely EPSG:4326 per `run_pelicun_damage_assessment.py:959-961`)
3. Take `shapely.geometry.MultiPoint(list(centroids)).convex_hull`
4. Project the convex hull to a suitable equal-area CRS (use `pyproj.Geod` with `geod.geometry_area_perimeter` for geodesic area on EPSG:4326 geometries, or reproject to UTM/equal-area). Use `pyproj.Geod(ellps="WGS84").geometry_area_perimeter(hull)[0]` and divide by 1e6 to get km².
5. `abs()` the area (Shoelace formula can give signed area).

### 5.2 `bbox`

Copy the bounding box of the **full** damage layer (all features, not just damaged): `tuple(gdf.total_bounds)` → `(minx, miny, maxx, maxy)`. Reorder to `(minLon, minLat, maxLon, maxLat)` if necessary (geopandas `total_bounds` returns `[minx, miny, maxx, maxy]` which matches the `BBox` contract).

---

## 6. Provenance Handling

| Field | Source |
|-------|--------|
| `pelicun_run_id` | If caller passes one, use it. If `None`, derive as ULID seeded from `sha256(damage_layer_uri + flood_layer_uri)[:16]` converted to ULID bytes via `ulid.ULID.from_bytes`. This makes the ID stable across re-runs with the same inputs. |
| `damage_layer_uri` | Directly from the `damage_layer_uri` argument |
| `flood_layer_uri` | Directly from the `flood_layer_uri` argument |
| `structure_inventory_source` | Directly from the argument |
| `fragility_set` | Carried forward from the upstream run argument |
| `realization_count` | Carried forward from the upstream run argument |
| `generated_at` | `datetime.now(timezone.utc)` at call time |

The `pelicun_run_id` is scoped to the *postprocess* run (this tool's run), not to `run_pelicun_damage_assessment`'s run. The upstream tool does not emit its own ULID in the `LayerURI` — its identity is the content-addressed GCS key. The `pelicun_run_id` here identifies this aggregation run for MongoDB audit-log correlation.

---

## 7. Error Envelope

Define a new exception hierarchy in `postprocess_pelicun.py`, parallel to `PelicunDamageError` (established pattern at `run_pelicun_damage_assessment.py:109-160`):

```
PelicunPostprocessError(RuntimeError)
    error_code: str = "PELICUN_POSTPROCESS_ERROR"
    retryable: bool = True

PelicunPostprocessInputError(PelicunPostprocessError)
    error_code = "PELICUN_POSTPROCESS_INPUT_INVALID"
    retryable = False
    # Bad damage_layer_uri, unrecognized structure_inventory_source, etc.

PelicunPostprocessIOError(PelicunPostprocessError)
    error_code = "PELICUN_POSTPROCESS_IO_ERROR"
    retryable = True
    # GCS download failed, FlatGeobuf unreadable, geopandas not installed

PelicunPostprocessEmptyError(PelicunPostprocessError)
    error_code = "PELICUN_POSTPROCESS_EMPTY_LAYER"
    retryable = False
    # FlatGeobuf has zero features (guard against empty upstream result)

PelicunPostprocessSchemaError(PelicunPostprocessError)
    error_code = "PELICUN_POSTPROCESS_SCHEMA_MISMATCH"
    retryable = False
    # Required columns (ds_mean, repair_cost_mean, etc.) missing from FGB
```

All errors carry `retryable` and `error_code` matching the Wave 4.9 WebSocket A.6 error-frame convention. Input validation (URI format, `structure_inventory_source` enum check) must fire before any GCS I/O so the typed-error surface is always clean.

---

## 8. Caching

- **`ttl_class`**: `"static-30d"` — the upstream FlatGeobuf is cache-stable for 30 days (same TTL as `run_pelicun_damage_assessment`). The postprocessing is a pure deterministic aggregation of that blob; the envelope is as fresh as its input.
- **`cacheable`**: `True`
- **Cache key composition**: `sha256(damage_layer_uri + "|" + flood_layer_uri + "|" + structure_inventory_source)[:32]` — omit `fragility_set` and `realization_count` because they do not change the FlatGeobuf being aggregated (they are already baked into the upstream cache key). This key is also the seed for `pelicun_run_id` when the caller does not supply one.
- **`source_class`**: `"pelicun_postprocess"` — distinct from `"pelicun_damage"` so the GCS lifecycle rule can be tuned separately if needed.
- **Storage path**: `gs://grace-2-hazard-prod-cache/cache/static-30d/pelicun_postprocess/<key>.json` — serialize the `ImpactEnvelope` as JSON via `envelope.model_dump(mode="json")`.

The `read_through` shim in `services/agent/src/grace2_agent/tools/cache.py` handles the GCS read/write. The `fetch_fn` lambda performs the FlatGeobuf download → aggregation → `ImpactEnvelope` construction. The cache shim's `ext` argument should be `"json"` (not `"fgb"`).

---

## 9. Required Tests

The implementation specialist must provide at minimum:

1. **`test_aggregation_basic`**: synthetic GDF with 5 features, known `ds_mean`/`repair_cost_mean`/`replacement_value` values → assert all top-level counts and sums match hand-calculated values. Use `USACE_NSI` source with fabricated population columns. Verify `damage_state_distribution` sums to `n_structures_total`.

2. **`test_damage_state_thresholds`**: verify the 1.0 and 3.5 boundaries precisely — features at exactly `ds_mean=1.0` count as damaged; features at `ds_mean=0.999` do not. Features at `ds_mean=3.5` count as destroyed; `ds_mean=3.499` do not.

3. **`test_population_nsi_vs_ms`**: two runs — one with `USACE_NSI` source (population columns present) → all population fields populated; one with `MS_BUILDINGS` source → all population fields `None`.

4. **`test_impact_area_zero_when_no_damage`**: GDF where all `ds_mean < 1.0` → `impact_area_km2 == 0.0`.

5. **`test_impact_area_convex_hull`**: GDF with 4 known damaged-asset centroids at known lat/lon → `impact_area_km2` within 5% of hand-computed geodesic area.

6. **`test_by_occupancy_class_keys`**: GDF mixing RES1, COM1, IND1 → `by_occupancy_class` has exactly those three keys; per-class `n_structures` sum equals `n_structures_total`.

7. **`test_missing_columns_raises_schema_error`**: GDF missing `ds_mean` → `PelicunPostprocessSchemaError`.

8. **`test_empty_gdf_raises_empty_error`**: zero-feature GDF → `PelicunPostprocessEmptyError`.

9. **`test_pelicun_run_id_stable`**: identical inputs called twice → same `pelicun_run_id` each time (seeded-ULID determinism).

10. **`test_loss_ratio_displacement_threshold`**: features at `loss_ratio_mean=0.20` count toward `population_displaced`; features at `0.199` do not.

---

## 10. Open Questions

1. **`pelicun_run_id` seeding via ULID bytes**: `ulid.ULID.from_bytes` requires exactly 16 bytes, but `sha256` output is 32 bytes. The standard pattern is to take `sha256(...)[:16]` as the 16-byte seed. Confirm `python-ulid`'s `ULID.from_bytes` accepts arbitrary bytes (not just timestamp-seeded ones) — if not, use `str(ULID())` with a `random.seed(int.from_bytes(sha256[:8], "big"))` before generation, or just generate a fresh ULID and accept non-determinism (at cost of cache-key divergence).

2. **`component_type_used` vs `occtype` grouping**: the `by_occupancy_class` breakdown should group by `component_type_used` (the column Pelicun actually used, `run_pelicun_damage_assessment.py:932`), not by the raw NSI `occtype`. These are identical when NSI fires, but differ for the MS_BUILDINGS path (all features get `"RES1"`). Confirm this is the intended grouping key.

3. **`USER_SUPPLIED` population handling**: `StructureInventorySource` includes `"USER_SUPPLIED"` (schema line 74). The design treats this like `MS_BUILDINGS` (population fields `None`) because user-supplied layers have unknown population coverage. Should `USER_SUPPLIED` attempt to read NSI-compatible population columns if present, with a best-effort fallback to `None`? This affects `OccupancyClassImpact.population` semantics for the user-supplied path.

4. **`repair_cost_p95` presence guarantee**: the schema assumes `repair_cost_p95` always exists on the FlatGeobuf. The `run_pelicun_damage_assessment` tool writes this column at line 916 (`repair_p95s`). However, if the upstream GFB was generated by an older tool version (pre-Wave 2) that did not emit `repair_cost_p95`, the postprocessor will fail at column access. Should the postprocessor treat a missing `repair_cost_p95` column as a `PelicunPostprocessSchemaError` or fall back to `loss_percentile_95_usd = expected_loss_usd` (conservative estimate)? Recommend the schema-error path since Wave 2 is already the baseline — but this needs explicit acknowledgment.

5. **Convex hull vs. alpha shape for `impact_area_km2`**: the schema specifies convex hull (line 271). For impact patterns with large un-damaged interiors (e.g. a flood that bypasses a hilltop neighborhood), the convex hull substantially overstates the impact footprint. Concave hull / alpha shape would be more accurate but requires `shapely >= 2.0` (alpha shape) or `scipy`. The design stays with convex hull per the schema contract — future sprints may amend if the overestimation is user-visible. Record as OQ-P2-CONVEX-HULL.

6. **`bbox` CRS assumption**: `geopandas.total_bounds` returns bounds in the GDF's native CRS. The FlatGeobuf from `run_pelicun_damage_assessment` is written in EPSG:4326 (`_gdf_to_fgb_bytes` at line 959 falls back to EPSG:4326 if CRS is None). Confirm the CRS is always 4326 before reading `total_bounds` for the `bbox` field — or explicitly reproject to 4326 before calling `total_bounds`.
