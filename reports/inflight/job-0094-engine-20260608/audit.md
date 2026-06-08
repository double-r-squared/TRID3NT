# Audit: `extract_landcover_class` atomic tool

**Job ID:** job-0094-engine-20260608, **Sprint:** sprint-12-mega Wave 1 (parallel fan-out), **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` — pattern reference (job-0084)
- `services/agent/src/grace2_agent/tools/cache.py` — `read_through` shim
- `services/agent/src/grace2_agent/tools/__init__.py` + `main.py` — registration sites

### Scope

NEW file `services/agent/src/grace2_agent/tools/extract_landcover_class.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="landcover_class",
)
def extract_landcover_class(landcover_uri: str, classes: list[int], bbox: tuple[float,float,float,float] | None = None) -> LayerURI:
    """NLCD landcover-class binary mask extractor.

    Reads an NLCD landcover GeoTIFF (typical: USGS NLCD 2021 CONUS), filters
    to the requested integer class codes, returns a binary raster (1=match, 0=other,
    nodata preserved). Useful as input to compute_zonal_statistics (zone_input).
    Returns LayerURI(layer_type="raster", role="context", units=None).
    """
```

**Implementation**:
- Use rasterio to read the NLCD raster (typically already a COG at gs://)
- If `bbox` provided: window-read just that region; else process entire raster
- Output: uint8 binary mask; class_code in `classes` → 1, else → 0; nodata preserved as 255
- Cache key on (landcover_uri, classes sorted tuple, bbox-rounded-6dp)
- Cache prefix: cache/static-30d/landcover_class/<hash>.tif
- LZW-compressed COG output
- NLCD class codes (most common): 11=Open Water, 21-24=Developed, 31=Barren, 41-43=Forest, 52=Shrub, 71-74=Grassland, 81-82=Pasture/Cropland, 90-95=Wetlands

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Synthetic 32x32 NLCD raster with mixed classes → extract class=11 → only water-cells are 1
- Multiple classes: extract [41,42,43] (all forest) → forest-cells are 1
- bbox window: 64x64 raster + bbox covering top-right → output is 32x32 of the top-right
- nodata preservation: input nodata pixels remain 255 in output
- Cache miss/hit

**Live verification**: extract_landcover_class on a real NLCD COG (use Fort Myers job-0075's NLCD cache hit) → binary mask GeoTIFF; evidence/landcover_class_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each. Verify via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/extract_landcover_class.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append)
- `services/agent/tests/test_extract_landcover_class.py` (NEW)
- `reports/inflight/job-0094-engine-20260608/`


### FROZEN

All other `tools/*` (each Wave 1 sibling has its own file ownership); all `workflows/`, `services/workers/`, `packages/contracts/`, `web/`, `infra/`, `docs/srs/`, `styles/`, `reports/complete/**`.


### Concurrency note (Wave 1 fan-out)

~15 Wave 1 jobs run concurrently. Each owns its own NEW tool file but ALL share `tools/__init__.py` + `main.py` registration sites. The idempotent-append pattern from sprint-11 Stage 1 (which handled 6 concurrent additions cleanly) applies: ADD your import line at the end of each file; if your line conflicts with a sibling's, do `git pull --rebase` style re-apply; do NOT remove other tool imports.


### Codified lesson (job-0086, do not violate)

URL/render consistency != geographic correctness. In-COG axis mirrors and similar in-file orientation bugs are invisible to every consistency check (server, client, PIL composite all faithfully serve the mirrored array). If your tool emits geometry, your acceptance test MUST verify the output against the **known geography of the bbox** (e.g. "is the deep-flood pixel at the river mouth?"), not just "did the bytes round-trip?".


### Acceptance criteria

- [ ] New tool registered + visible at `--startup-only` (count = entering_count + 1)
- [ ] ≥4 unit tests + 1 live test (with appropriate env-var guard)
- [ ] Live verification with real upstream response captured to evidence/
- [ ] Geography correctness check per the codified job-0086 lesson (where applicable)
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence paths + any OQs

