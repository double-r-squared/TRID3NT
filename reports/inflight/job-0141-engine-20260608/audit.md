# Audit: MRMS evidence audit + re-run live capture

**Job ID:** job-0141-engine-20260608, **Sprint:** sprint-12-mega Wave 3.5, **Specialist:** engine (SMALL Sonnet-tier)

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_mrms_qpe.py` (Wave 1.5 job-0103)
- `reports/PROJECT_LOG.md` (Wave 1.5 entry claims 12.8MB CONUS / 61KB FL clip with specific numbers)

### Why

OQ-PAY-NO-EVIDENCE-MRMS surfaced by Playwright agent: PROJECT_LOG references real numbers from job-0103's live test (12.8MB CONUS GeoTIFF, max 318.7mm; FL clip 61KB, max 40.30mm, mean 5.10mm) but `reports/inflight/job-0103-engine-20260608/evidence/` is empty. Either the runner forgot to write evidence files OR they were not committed. We need the cached MRMS GeoTIFF URI + summary stats persisted for Case 3 (Idaho flood) and any future workflow that wants real precip data.

### Scope

1. **Re-run** `fetch_mrms_qpe` live with:
   - bbox=None (CONUS), accumulation='24H' → expect ~12-13MB GeoTIFF in cache
   - bbox=(-83, 25, -80, 28) FL Gulf, accumulation='24H' → expect ~60KB clipped GeoTIFF
2. **Capture** the cache URIs + summary stats to `reports/inflight/job-0141-engine-20260608/evidence/mrms_audit.txt`:
   - Cache URIs (gs:// paths)
   - File sizes
   - Min/max/mean precip values
   - Bbox extents
   - Acquisition timestamp
3. **Capture** the actual cached GeoTIFF metadata via rasterio.open:
   - CRS
   - Transform (verify standard north-up after job-0086 lesson)
   - Number of finite pixels (non-NaN)
4. **Update** `reports/inflight/job-0103-engine-20260608/evidence/mrms_live.txt` with the same data (idempotent — write the file even if it didn't exist)

**Acceptance**:
- evidence/mrms_audit.txt exists with all 4 data items above
- Cached GCS URIs verified accessible via `gsutil ls`
- Geographic-correctness: pixels in CONUS run cover CONUS bounds; pixels in FL clip stay inside FL Gulf bbox

### File ownership (exclusive)

- `reports/inflight/job-0141-engine-20260608/evidence/`
- `reports/inflight/job-0103-engine-20260608/evidence/mrms_live.txt` — write OR update only

### FROZEN

- All implementation files (just running existing tool)
- All other reports/inflight/* except 0103's evidence dir


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 3/3.5 job's exclusive files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. Geographic-correctness gate (job-0086): pixel-level evidence required.
2. Kickoff-front-loaded design: execute scope, surface OQs, don't redesign.
3. MongoDB MCP persistence (job-0115): use Persistence.* — no custom CRUD.

### Acceptance criteria

- [ ] Deliverables landed per scope
- [ ] Live verification per kickoff
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

