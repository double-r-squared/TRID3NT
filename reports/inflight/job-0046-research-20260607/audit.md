# Audit: Catalog seed research — 30–60 vetted endpoints across 8 domains

**Job ID:** job-0046-research-20260607, **Sprint:** sprint-08, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine (**Sonnet** — research + summarize + structure; no production code)

**Prerequisites:**
- **v0.3.20 housekeeping** (just landed orchestrator-direct): §F.1 WorldPop + NLCD prose now correct (1km REST + WCS GetCoverage). **Read this prose for canonical "how to use" metadata patterns** — your catalog entries should mirror this voice.
- **v0.3.18 §F.1.2 CatalogEntry schema** (whatever shape job-0045 lands; for v0.1 you write the YAML by hand against the §F.1.2 documented fields)

**SRS references** (narrow files only):
- `docs/srs/F-data-sources-discovery-secrets.md` — §F.1 + §F.1.1 + §F.1.2 (the binding contract for entry shape + tier discipline)
- DO NOT load `docs/SRS_v0.3.md` monolith

### Scope

1. **Live-verify + author `public_data_source_catalog.yaml` v0.1.0** at the repo root (per §3.5.5 FR-PHC `public_hazard_catalog.yaml` convention — but this catalog is broader, covers all data sources not just hazard layers). Target 30–60 entries across these 8 domains:
   - **Terrain** (DEM, slope, hillshade): USGS 3DEP, Copernicus DEM, ASTER GDEM (NASA Earthdata), NED legacy
   - **Hydrology** (rivers, watersheds, gauges): NHDPlus HR, USGS NWIS, NOAA NWS AHPS gauges, MERIT-Basins, HydroSHEDS
   - **Weather + Precipitation**: NOAA Atlas 14 PFDS, NEXRAD Level II, MRMS QPE, GPM IMERG, Storm Events DB, NHC ATCF
   - **Buildings + Infrastructure**: Microsoft Building Footprints, OSM Overpass critical-infrastructure, FEMA HAZUS-MH defaults, USGS NSI National Structures Inventory
   - **Population + Demographics**: WorldPop 1km REST, US Census ACS, Gridded Population of the World (CIESIN/NASA SEDAC), Global Human Settlement Layer (GHSL — EU JRC)
   - **Land Cover**: NLCD via MRLC WCS, ESA WorldCover 10m, ESRI Land Cover, MODIS land cover (NASA LP DAAC), CCI Land Cover (ESA)
   - **Fire / Wildfire**: USFS Wildfire Hazard Potential, USDA Wildfire Risk to Communities, NIFC active incidents, LANDFIRE fuels, NASA FIRMS active fire
   - **Seismic + Earthquake**: USGS National Seismic Hazard Map, USGS Earthquake Catalog (ComCat), GEM Foundation models
2. **For EACH entry, live-verify the access tier** per §F.1.1 discipline:
   - HEAD or OPTIONS request to the candidate URL
   - Check for STAC root (`/api/stac/v1/` / `/stac/catalog.json`) → Tier 1
   - Check for OGC `GetCapabilities` response → Tier 2 (WMS or WCS depending on whether canonical raster bytes are needed)
   - Check `Accept-Ranges: bytes` header on file URLs → Tier 3
   - Otherwise → Tier 4 (region download)
   - **Don't infer the tier from documentation; verify the tier from the live response.** This is the §F.1.1 lesson sprint-7 taught (WorldPop's STAC didn't exist; NLCD's WMS returned palette indices).
3. **Per-entry fields** (match the §F.1.2 schema):
   ```yaml
   - id: stable-kebab-case-id
     name: "Human-readable name"
     description: "What this provides"
     urls: ["primary URL", "alternative mirror if exists"]
     access_tier: 1  # or 2/3/4 per §F.1.1
     credential_tier: 1  # or 2/3 per §F.1
     ttl_class: static-30d  # or semi-static-7d / dynamic-1h / live-no-cache per FR-DC-2
     source_class: dem  # or landcover/buildings/population/etc — stable per atomic tool
     license: "Public domain (US Federal data)"  # CITE
     citation: "USGS 3DEP, U.S. Geological Survey, accessed via ..."  # CITE
     vintage: "2023"
     last_verified: "2026-06-07"
     status: active
     how_to_use: |
       Mirror the §F.1 voice. Include invocation examples, parameter
       constraints, and known quirks discovered during live verification
       (e.g., "WorldPop server returns 200 not 206 for Range requests
       — use region-download Tier 4 path; specify country in
       params.iso3"). This is the difference between a sterile URL
       list and an actionable catalog.
   ```
4. **Honest qualification.** If a source you expected to be alive is down OR if live verification reveals a different tier than documentation implied (very likely per sprint-7's pattern), note in the YAML comment + report. **Live-verified wins over kickoff-inferred** — same discipline as job-0037 + job-0039 + job-0044.
5. **Surface deferrals**: if a domain has fewer than 3 candidates that pass live verification, note it and recommend sprint-09+ follow-up. Don't bulk-pad with low-quality entries.

### File ownership (exclusive)
- `public_data_source_catalog.yaml` (NEW at repo root)
- `reports/inflight/job-0046-research-20260607/`

### FROZEN
- `packages/contracts/**` (consume `CatalogEntry` shape; don't redefine)
- `services/**`, `web/**`, `infra/**`, `styles/**`, `docs/srs/**`, `docs/SRS_v0.3.md`, `reports/complete/**`
- Stage A concurrent jobs

### Acceptance criteria
- [ ] `public_data_source_catalog.yaml` v0.1.0 exists with 30–60 entries
- [ ] Each entry has all required §F.1.2 fields populated
- [ ] Each entry's `access_tier` is from live verification, not documentation inference (note deviations from kickoff guess)
- [ ] Coverage across all 8 domains; honest gaps surfaced rather than padded
- [ ] `how_to_use` notes capture known quirks discovered during live verification
- [ ] No edits to FROZEN paths
