# Demo Spike: GOES-18 GeoColor + Fire Temperature fire animation (CIRA Iron Fire recreate)

Demo spike for the GRACE-2 fire-monitoring tool branch: recreate the CIRA / RAMMB
(Colorado State University) GOES-18 animation of the Iron Fire near Eureka, Utah
(with the Hastings Fire to its NW and the Kane Springs + Grapevine fires in eastern
Nevada), a ~6.5h GeoColor + Fire Temperature loop posted 2026-06-22. This is a
USEFUL DEMO, not a North Star and not an engine go/no-go. It is grounded against
primary practitioner sources (the NOAA GOES-R Big-Data Program ABI-L2 buckets on
AWS, the CIRA/RAMMB SLIDER tile service, the NOAA-NESDIS/CIRA Fire Temperature and
GeoColor RGB Quick Guides, NASA FIRMS, and the NIFC/WFIGS incident service) AND
against the live GRACE-2 seam (the existing fetch_goes_satellite S3+reproject path,
the postprocess_flood frame+scrubber animation seam, and the run_model_news_event
ingest news->bbox front half are the structural analogues cited throughout as the
integration template).

ASCII only. No em/en dashes, no unicode arrows; "->" for arrows. Status: design +
verdict only, no code in this doc.

---

## 0. Verdict

**BUILD - mostly reuses existing seams.**

This demo is NOT an engine evaluation with a go/no-go gate; it is a packaging of
existing GRACE-2 capability plus two honest extensions. The data path is FREE and
unauthenticated (NOAA GOES-18 ABI-L2 on AWS S3 is anonymous; the CIRA/RAMMB SLIDER
GeoColor + Fire Temperature tiles are plain HTTPS with no key), so there is NO
access blocker. The animation web stack already exists and needs ZERO changes: if
the new fetch emits per-frame COGs the way postprocess_flood emits flood-depth
frames (distinct cache keys + shared style_preset + same bbox + a "step N" / time
name token), the existing SequenceScrubber + AnimationController + LayerPanel
detectSequentialGroups animate them automatically. The "pull news" front half
already exists (run_model_news_event_ingest supports target_event_type='wildfire',
geocodes a location to a bbox). The GOES S3 listing + geostationary->EPSG:4326
reproject + COG-write primitives already exist in fetch_goes_satellite. So the
honest framing is BUILD: clone-and-extend, not invent.

The real gaps - none fatal, each tied to a named seam, and each is the REASON this
is a BUILD-with-gaps rather than a pure reuse:

1. **No time-window / historical replay in fetch_goes_satellite.** The existing
   tool fetches ONLY the single most-recent MCMIPC frame (its docstring explicitly
   flags a valid_time param as out-of-scope-for-v0.1 / in-scope-for-v0.2). The demo
   needs a (start_utc, end_utc, step_minutes) window that lists ALL ABI keys in the
   window and emits one frame per scan time. The S3 walk primitives
   (_list_keys_for_prefix, _KEY_START_TIME_RE, _doy_hour) already exist; they just
   need to anchor on a requested time range instead of datetime.now().

2. **No Fire Temperature RGB and no GeoColor product.** The existing tool exposes
   only 3 single CMI bands (visible=C02, ir_window=C13, water_vapor=C08). The Fire
   Temperature RGB is a 3-band composite of ABI C07 (3.9um) / C06 (2.2um) / C05
   (1.6um) - none of which the tool reads. The fastest path is to AVOID rebuilding
   GeoColor from raw bands (it is a proprietary CIRA algorithm: simulated green LUT
   + Rayleigh correction + static VIIRS city-lights night layer) and instead pull
   BOTH products ready-made from CIRA/RAMMB SLIDER tiles (which is literally what
   the CIRA Instagram animation is made from), reprojecting the fixed-grid tiles to
   EPSG:4326 with the existing _reproject_and_clip. Path B (compose Fire Temperature
   from raw S3 bands) is available for full control if SLIDER is unsuitable.

3. **No incident-by-name lookup and no time-window-from-news.** model_news_event
   ingest geocodes a free-text location to a bbox but does NOT resolve a named
   incident (Iron / Hastings / Kane Springs / Grapevine) to an authoritative point,
   and derives a single date claim but no animation (start, end) window. The fix is
   a new fetch_wfigs_incident atomic tool against the NIFC/WFIGS incident
   FeatureServer (live-verified to return all 4 fires with exact coords + discovery
   times) plus a small step that turns the user-named window into start_utc/end_utc.

4. **No GOES RGB TiTiler style + no paste-ready demo prompt.** publish_layer renders
   >=3-band RGB COGs through its multiband passthrough untouched (good - a baked RGB
   Fire Temperature / GeoColor COG renders directly), but _TITILER_STYLE_REGISTRY
   has no 'goes_fire_temp' or 'geocolor' single-band entry (only needed if a
   single-band scalar is emitted instead of RGB). And the deliverable paste-ready
   recreate prompt does not exist yet - it is authored in section 11 of this doc.

If those four are accepted, this is the cheapest high-visibility fire-branch demo
available: it reuses the entire animation web stack, the news-ingest front half, and
the GOES S3 primitives, and adds one extended fetcher (or a small new one), one
optional incident-lookup tool, and one workflow. The ordered build plan is
section 9.

---

## 1. The demo in one paragraph

A user pastes the CIRA caption (or just says "pull the news on the fires near Eureka,
Utah and recreate the GOES animation"). The agent (1) resolves the named incidents -
Iron + Hastings near Eureka UT, Kane Springs + Grapevine in eastern NV - to an
authoritative AOI bbox via the news-ingest front half and/or the NIFC/WFIGS incident
service; (2) takes a ~6.5h UTC time window on 2026-06-22 (the user's lever; the
window defaults to ~6.5h ending the named/most-recent time, and the WFIGS fire
discovery time is the sanity floor); (3) fetches the GOES-18 (GOES-West) CONUS-sector
GeoColor and Fire Temperature imagery at 5-minute cadence over that window (~78
frames) - either ready-made from CIRA/RAMMB SLIDER tiles (recommended) or composited
from the raw noaa-goes18 ABI-L2 bands - reprojecting each to EPSG:4326 COGs over the
AOI; (4) emits the per-frame COGs in the SAME shape postprocess_flood emits flood
frames so the existing web scrubber animates them; and (5) optionally overlays the
FIRMS active-fire detections and the NIFC fire perimeter as static co-registered
layers. The result is the CIRA loop, recreated and scrubbable, with two
independently-toggleable animated layers (GeoColor + Fire Temperature).

Two HARD requirements, both already PASS:
- DATA ACCESS (PASS): all imagery is free + unauthenticated (anonymous S3 + keyless
  SLIDER HTTPS). FIRMS needs a key but GRACE2_FIRMS_MAP_KEY is already wired.
- ANIMATION WEB STACK (PASS): the scrubber/controller/grouping web stack already
  exists and needs zero changes if frames are emitted in the flood-frame shape.

---

## 2. Data sources

| Source | What it gives | Access / auth | Use in this demo |
|--------|---------------|---------------|------------------|
| NOAA GOES-18 ABI-L2 on AWS (noaa-goes18 S3, Big-Data Program) | Raw ABI imagery: ABI-L2-MCMIPC (all 16 CMI bands, one netCDF, CONUS, 5-min), ABI-L2-CMIPC (single-band), ABI-L1b-RadC (radiances). Key layout <Product>/<YYYY>/<DOY>/<HH>/OR_..._s<YYYYDOYHHMMSSf>_e..._c....nc. DOY 173 = 2026-06-22. | Anonymous HTTPS / S3 list-type=2 (no key). Already wired in fetch_goes_satellite. | PATH B: compose Fire Temperature RGB from C07/C06/C05 with full control; source of truth for the existing tool. |
| CIRA / RAMMB SLIDER pre-rendered tiles (EASIEST PATH) | READY-MADE GeoColor and Fire Temperature PNG tiles, already composited + georeferenced-by-fixed-grid. Tile template .../data/imagery/<YYYYMMDD>/goes-18---<sector>/<product>/<YYYYMMDDHHMMSS>/<zoom>/<tileY>_<tileX>.png ; time index .../data/json/goes-18/<sector>/<product>/latest_times.json (+ latest_times_5760.json, available_dates.json). | Keyless HTTPS GET. LIVE-VERIFIED 2026-06-22: fire_temperature latest_times.json returned 5-min-spaced timestamps_int on the source date. | PATH A (RECOMMENDED): pull both products at the exact cadence the CIRA animation uses; stitch tile grid -> reproject fixed-grid -> EPSG:4326 COG. |
| Fire Temperature RGB recipe (NOAA-NESDIS / CIRA Quick Guide) | RED = ABI Band 7 (3.9um) brightness temp, stretch 0-60 C (273.15-333.15 K), gamma 1, not inverted. GREEN = ABI Band 6 (2.2um) reflectance, 0-100 %, gamma 1. BLUE = ABI Band 5 (1.6um) reflectance, 0-75 %, gamma 1. Per channel clip to [0,1]. Hot fires read red -> yellow -> white. | Static PDF (extracted). | Band math for PATH B (raw-band Fire Temperature composite). |
| GeoColor (CIRA composite) | DAY: Rayleigh-corrected C01/C02/C03 + SIMULATED green via a Himawari-trained LUT (ABI has no native green). NIGHT: multispectral IR cloud product over a static VIIRS city-lights / Blue-Marble background, blended along the terminator. | Proprietary CIRA algorithm. | DO NOT hand-build for v0.1. Pull ready-made from SLIDER (product=geocolor) or NASA GIBS GOES-West GeoColor WMTS (already georeferenced). The 19-20Z window is daytime over UT/NV, so if a raw approximation is ever needed, daytime pseudo-true-color (C01/C02/C03 + synthetic green) is the tractable subset; label it "GeoColor-style (daytime)". |
| NASA GIBS / Worldview GOES-West GeoColor WMTS | GeoColor as properly georeferenced WMTS (EPSG:4326 / 3857), 10-min cadence, ~40-min latency. NOTE: GIBS does NOT publish Fire Temperature. | Keyless WMTS. | Cleanest GEOREFERENCED GeoColor path (no fixed-grid reprojection). Cadence is coarser than SLIDER's 5-min, so for cadence-matched dual loops prefer SLIDER for both. |
| NASA FIRMS Area API (VIIRS / MODIS active-fire) | Active-fire pixel detections (lat, lon, bright_ti4/ti5, FRP, confidence, acq_date/time, daynight) by bbox + date. Optional trailing /{YYYY-MM-DD} gives a HISTORICAL date. | MAP_KEY (free). GRACE2_FIRMS_MAP_KEY already in services/agent/.env, live-working. LIVE-VERIFIED: 152 detections for the UT cluster on 2026-06-22. | Co-registered "hot pixel" vector overlay; cross-check the animation. The existing fetch_firms_active_fire is MISSING the historical-date positional - that one gap must close for a specific past date. |
| NIFC / WFIGS Current Incident Locations (ArcGIS FeatureServer) | Point incidents with IncidentName, FireDiscoveryDateTime, InitialLatitude/Longitude, IncidentSize, PercentContained, POOState (US-UT / US-NV), POOCounty, IrwinID. | Keyless ArcGIS REST. Same org (T4QMspbfLg3qTGWY) as the already-wired NIFC perimeters. LIVE-VERIFIED 2026-06-22: returned Iron (Juab UT, 39.96976,-112.16481, disc 2026-06-20, 21935 ac), Hastings (Tooele UT, 40.715,-112.946, 6000 ac), Grapevine (Lincoln NV, 37.381,-114.305, 13196 ac), Kane Springs (Lincoln NV, 37.284,-114.630, 14359 ac). | Authoritative named-incident -> point + discovery time -> bbox. The NEW fetch_wfigs_incident tool. Eureka UT is in Juab County = the Iron Fire, exactly matching the CIRA post. |
| NIFC fire perimeters (already wired) | Active fire perimeter polygons by bbox. | Keyless ArcGIS REST (fetch_nifc_fire_perimeters). | Tighter shape-aware bbox + a static overlay. |
| NWS Alerts (already wired) | Red-Flag Warnings / Fire-Weather Watches (CAP) by area/zone. | Keyless (fetch_nws_alerts_conus / fetch_nws_event). | Tier-1 NEWS-adjacent corroborator in the claim aggregator (NOT the geocoder). |

VERIFIED bboxes (west, south, east, north): Utah cluster (Iron + Hastings) =
-113.346, 39.57, -111.765, 41.115 ; all-4 (UT + NV) = -115.13, 36.784, -111.665,
41.215. Eureka UT (~-112.11, 39.96) and eastern NV are inside the GOES-18 CONUS
sector, so the existing tool's CONUS bbox guard is satisfied.

---

## 3. Sector + cadence (the animation core)

Utah / Nevada is inside the GOES-18 (GOES-West) CONUS sector. Use CONUS @ 5-min
cadence (full disk is 10-min and coarser; mesoscale is 1-min but only if a meso
float happened to be tasked over this fire - do NOT assume it, verify the meso
sector center first). 6.5h / 5min = ~78 frames, comfortably under the existing
MAX_FLOOD_FRAMES cap (144), so the whole loop animates with little or no subsample.
Build the ordered frame stamp list by:
1. GET the authoritative time index (SLIDER latest_times.json /
   latest_times_5760.json / available_dates.json for goes-18/conus/<product>, OR the
   S3 listing of noaa-goes18 <Product>/<YYYY>/<DOY>/<HH>/ keys parsing each
   _s<timestamp>).
2. Filter to start <= t <= end.
3. Snap to cadence and even-subsample down to a frame cap (reuse postprocess_flood
   _select_frame_time_indices: first + last always kept, logs any subsample).
4. Emit ordered stamps, each carrying its real UTC valid-time so the scrubber labels
   match the CIRA caption.

NOTE the CIRA caption's "19:26 UTC to 20:01 UTC" end stamps are an internal copy
typo (35 min, not 6.5h). Anchor the window to ~6.5h ending ~20:01Z (start ~13:30Z);
do NOT feed the literal 19:26-20:01 as the window or you get a 35-min clip.

---

## 4. Coverage: HAVE vs GAP

HAVE (reuse, little-to-no change):
- GOES-18 + S3 list-type=2 listing + most-recent picker + geostationary->EPSG:4326
  reproject + COG-write + dynamic-1h read_through cache + CONUS bbox handling
  (fetch_goes_satellite.py: _list_keys_for_prefix, _list_recent_keys,
  _KEY_START_TIME_RE, _doy_hour, _reproject_and_clip).
- The entire time-series animation web stack, ZERO web changes if frames are emitted
  in the flood-frame shape (SequenceScrubber.tsx + lib/animation_controller.ts +
  lib/frame_preload.ts + LayerPanel detectSequentialGroups).
- The frame-emission contract to copy (postprocess_flood.py: distinct per-frame
  cache keys + role='context' + shared style_preset + 'step N' name token + same
  bbox; _select_frame_time_indices cap + even-subsample keeping endpoints).
- The "pull news" front half (model_news_event_ingest.py: target_event_type=
  'wildfire' already supported; web_fetch + aggregate_claims_across_sources +
  geocode_location -> bbox; job-0295 _validate_sources arg-coercion recipe).
- The active-fire vector overlay + the credential pattern (fetch_firms_active_fire.py
  vault -> GRACE2_FIRMS_MAP_KEY env -> demo; credential_registry.py FIRMS_MAP_KEY).
- The NIFC fire perimeter overlay (fetch_nifc_fire_perimeters.py).
- publish_layer >=3-band RGB / RGBA passthrough (_is_rgba_or_multiband,
  _resolve_titiler_style_params) - a baked Fire Temperature / GeoColor RGB COG
  renders directly with no new colormap work.
- The fetcher house style + registration plumbing (fetch_usgs_nwis_gauges.py
  template; tools/__init__.py @register_tool + eager-import; cache.read_through;
  categories.py semantic discovery; data/tool_query_corpus.yaml).

GAP (new code):
- Time-window / multi-frame GOES fetch (no valid_time / start / end / step in
  fetch_goes_satellite; it only fetches the most-recent MCMIPC frame).
- Fire Temperature RGB product (C07/C06/C05 composite) and GeoColor product (pull
  ready-made; do not hand-build GeoColor).
- A GOES animation workflow that chains news/incident -> bbox -> window -> per-frame
  fetch -> publish as a scrubber group (the frame seam exists only inside
  postprocess_flood today).
- Named-incident lookup (fetch_wfigs_incident) - news-ingest geocodes a location but
  resolves no named incident to an authoritative point.
- Time-window-from-news (the date claim is a value, not a (start, end) window).
- FIRMS historical-date positional (fetch_firms_active_fire is rolling days_back
  only; the trailing /{YYYY-MM-DD} must be added for a specific past date).
- Optional single-band GOES RGB style entries in _TITILER_STYLE_REGISTRY
  ('goes_fire_temp' / 'geocolor') - only needed if a single-band scalar is emitted
  instead of a baked RGB COG (RGB sidesteps it via the multiband passthrough).
- The paste-ready demo prompt (authored in section 11).

---

## 5. Integration seam (how the new tools slot in)

Two valid paths, mirroring the existing split; PATH A (hand-written Class-B tool) is
correct here - every existing fetch_* is a hand-written Python module and there is
NO live YAML-wrapper / generic-fetcher executor in services/agent/src (the "YAML
wrappers + generic fetchers" are a design concept in the architecture doc only;
categories.py is just the 12-category semantic-discovery surface).

CANONICAL ADD-A-TOOL RECIPE (mirroring fetch_usgs_nwis_gauges.py job-0332 and
fetch_goes_satellite.py job-0104):
1. NEW MODULE services/agent/src/grace2_agent/tools/fetch_goes_animation.py (or
   extend fetch_goes_satellite.py with a fetch_goes_timeseries entrypoint). Module
   docstring = source URL(s) + numbered strategy + cache-key formula + OQ notes.
   Typed-error hierarchy (one base RuntimeError subclass with error_code + retryable
   class attrs, then one subclass per failure mode: bbox-required, input-invalid,
   upstream, empty). Module-level _METADATA = AtomicToolMetadata(
   name='fetch_goes_animation', ttl_class='dynamic-1h', source_class=
   'goes_satellite', cacheable=True). Pure helpers in __all__ for tests. Decorate
   the entrypoint with @register_tool(_METADATA, open_world_hint=True) imported from
   grace2_agent.tools. Accept band='fire_temperature'|'geocolor', satellite default
   'goes-18', sector='conus', and (start_utc, end_utc, step_minutes); iterate the
   in-window keys instead of picking only the most-recent. Route EACH FRAME through
   cache.read_through (one call per frame so timestamps cache independently). For the
   raw-band Fire Temperature path read 3 CMI channels (C07/C06/C05) and stack into a
   3-band RGB COG (publish_layer's multiband passthrough renders it). For PATH A
   stitch SLIDER tiles -> reproject fixed-grid -> EPSG:4326 COG via _reproject_and
   _clip. Quantize bbox (6dp) + round time into the cache params yourself.
2. REGISTER: add exactly one eager-import line at the bottom of
   services/agent/src/grace2_agent/tools/__init__.py near the existing GOES line:
   "from . import fetch_goes_animation  # noqa: E402,F401 - NATE 2026-06-22:
   registers fetch_goes_animation (GOES-18 GeoColor + Fire Temperature multi-
   timestamp animation frames)".
3. SEMANTIC DISCOVERY: add the tool name to categories.py _TOOL_CATEGORY (->
   'weather_atmosphere', alongside fetch_goes_satellite) and add a
   data/tool_query_corpus.yaml entry so discover_dataset routes "fire satellite
   animation" to it.
4. CREDENTIALS: none for GOES (NOAA S3 + SLIDER are unauthenticated). Reuse the
   already-wired GRACE2_FIRMS_MAP_KEY for the FIRMS overlay.
5. WORKFLOW: NEW workflows/model_goes_fire_animation.py - template the news->bbox->
   time-window front half off model_news_event_ingest.py and the frame seam off
   postprocess_flood.py. Dispatch atomic tools via TOOL_REGISTRY[name].fn (never
   import directly). Emit each frame as its own LayerURI named with a consistent
   token ("GOES Fire Temperature step N" / an ISO-time label) + shared style_preset +
   identical bbox so detectSequentialGroups + AnimationController + SequenceScrubber
   animate it with NO web changes. STOP for bbox + window review before painting all
   frames (the granularity / confirm gate).
6. NEW fetch_wfigs_incident.py - the named-incident lookup (WFIGS FeatureServer),
   slotted into categories.py 'fire'.
7. EXTEND fetch_firms_active_fire.py - add the optional historical-date positional.
8. TESTS: unit-test the pure helpers (key-picking, band/time mapping, bbox
   validation) + a geographic-correctness assertion (output COG inside the requested
   bbox + a Fire-Temp hot-pixel range check reading red->white over the known fire
   AOI), mirroring fetch_goes_satellite's job-0086 lesson.

FILES TOUCHED: NEW tools/fetch_goes_animation.py, NEW tools/fetch_wfigs_incident.py,
NEW workflows/model_goes_fire_animation.py; EDIT tools/__init__.py (+2 import lines),
EDIT categories.py (category slots), EDIT data/tool_query_corpus.yaml,
EDIT fetch_firms_active_fire.py (historical-date positional); reuse publish_layer.py
+ the flood frame seam + the whole web scrubber unchanged.

---

## 6. "Follow the same pattern" - what each new piece mirrors

- fetch_goes_animation.py mirrors fetch_usgs_nwis_gauges.py (house style: module
  docstring + typed errors + AtomicToolMetadata + pure helpers + @register_tool +
  read_through + one __init__.py import line) AND fetch_goes_satellite.py (S3 list +
  reproject + COG). The valid_time / time-window extension is the v0.2 scope the
  fetch_goes_satellite docstring ALREADY flags.
- The Fire Temperature RGB compositing mirrors the NOAA-NESDIS / CIRA Quick Guide
  recipe (R=C07 0-60 C, G=C06 0-100 %, B=C05 0-75 %, gamma 1). Read MCMIPC CMI bands
  with the scale_factor/add_offset applied the way fetch_goes_satellite already does
  (rasterio's NETCDF driver does NOT auto-apply CF scaling).
- The frame emission mirrors postprocess_flood.py verbatim (distinct per-frame keys,
  role='context', shared style_preset, "step N" name token, same bbox; cap +
  even-subsample keeping endpoints).
- The news->bbox front half mirrors model_news_event_ingest.py (wildfire event type,
  geocode_location, _validate_sources arg coercion).
- The RGB render mirrors the publish_layer multiband passthrough (same lesson as the
  terrain-RGBA fix where a single-band ramp painted RGB invisible).
- fetch_wfigs_incident.py mirrors the sibling NIFC perimeter fetcher (same ArcGIS org
  T4QMspbfLg3qTGWY) and the FIRMS credential-free posture (no key).
- This spike doc mirrors reports/design/engine_spike_swan.md; the companion
  reference archive (reports/references/cira_goes_fire_animation/notes.md) mirrors
  reports/references/lecture_aws_swan_making_waves/notes.md.

---

## 7. Gotchas (carried into the jobs)

- SLIDER tiles are NOT lon/lat - they sit in the ABI fixed-grid (geostationary)
  pixel space, indexed by zoom + tileY_tileX. Reproject from the geostationary CRS to
  EPSG:4326 using the sector/zoom corner extents (reuse _reproject_and_clip). S3
  keys are partitioned by JULIAN day-of-year (DDD); SLIDER paths use YYYYMMDD -
  different conventions.
- GIBS publishes GeoColor but NOT Fire Temperature; for cadence-matched dual loops
  pull BOTH from SLIDER (5-min) rather than mixing 10-min GIBS GeoColor with 5-min
  Fire Temperature (different frame counts).
- ABI Band 7 (3.9um) is brightness TEMPERATURE (Kelvin -> subtract 273.15 for the
  0-60 C stretch); Bands 6/5 are REFLECTANCE (0-1 factor -> multiply by 100 for the
  % stretch). Mixing the units yields an all-dark or saturated image.
- MCMIPC CMI bands are scaled int16 with CF scale_factor/add_offset that rasterio's
  NETCDF driver does NOT auto-apply; apply per band before compositing.
- Mesoscale (1-min) is NOT guaranteed over any given fire; do not assume
  mesoscale_01/02 covers Eureka UT - verify the meso sector center, else CONUS 5-min.
- GeoColor is a proprietary CIRA algorithm (no native green band, static city-lights
  night layer); always source it ready-made.
- WFIGS POOState is ISO 3166-2 ('US-UT' / 'US-NV', NOT 'UT'/'NV'); IncidentName is
  the bare token ('Iron', not 'Iron Fire'). FIRMS day_range is clamped 1..5 upstream
  (use day_range=1 + the date positional for a single past date). FIRMS bbox order is
  west,south,east,north (same as GRACE-2 internal min_lon,min_lat,max_lon,max_lat).
- The CIRA caption "19:26-20:01 UTC" stamps are a copy typo (35 min); treat the
  stated 6.5h DURATION as authoritative and let the user override the end time.
- Honesty floor: a GOES envelope with empty / all-zero frames NEVER reads status=ok;
  the Fire-Temp compositor must read physically-plausible ranges or raise a typed
  error (the render-chokepoint / honesty norm).

---

## 8. Cloud / Batch + AI drivability

This demo is a DATA-FETCH + animation pipeline, not a numerical solver, so it runs
ENTIRELY on the existing agent + TiTiler path - NO AWS Batch, NO new compute island.
The per-frame fetch is the only heavy part: ~78 frames * 2 products, each a netCDF
download (or a SLIDER tile-grid stitch) + a reproject + a COG write. To honor the
no-sync-blocking-on-the-asyncio-loop norm, the per-frame fetch must run in
asyncio.to_thread (or be offloaded the way the heavy emit-free fetchers already are),
not on the loop. Frames cache independently (one read_through per frame) so re-runs
and scrubbing are cheap. The COGs publish through the always-on TiTiler box exactly
like flood-depth COGs, so the animation serves 24/7 even with the agent box asleep.

AI drivability: HIGH. The whole flow is "pull news on the Eureka UT fires -> resolve
a bbox + a 6.5h window on 2026-06-22 -> fetch GOES-18 GeoColor + Fire Temperature ->
animate", a sequence of existing-shaped tool calls the model already composes for
news-ingest + fetch + animate. The one user lever is the time window + cadence
(default ~6.5h / 5-min CONUS), surfaced through the granularity / confirm gate.

---

## 9. Ordered minimal-integration job list (build plan, smallest-first)

Each step is single-owner, tied to a file/seam, smallest-first.

1. **agent (S1): fetch_wfigs_incident atomic tool.** NEW
   tools/fetch_wfigs_incident.py against the WFIGS Incident Locations FeatureServer
   (where IncidentName IN (...) AND POOState IN ('US-UT','US-NV'), outFields incl.
   FireDiscoveryDateTime + InitialLat/Lon + IncidentSize, outSR=4326). Mirror the
   sibling NIFC perimeter fetcher; register (+1 __init__.py line, categories.py
   'fire', tool_query_corpus.yaml). Live-verified to return all 4 fires. (Smallest;
   resolves named incidents -> authoritative bbox + discovery-time sanity floor.)

2. **agent (S2): FIRMS historical-date positional.** EXTEND
   fetch_firms_active_fire.py to accept an optional date (trailing /{YYYY-MM-DD},
   day_range=1) so a specific past date works. Unit-test the URL build + the date
   path. (Tiny; unblocks the co-registered hot-pixel overlay for 2026-06-22.)

3. **agent (S3): fetch_goes_animation tool - PATH A (SLIDER ready-made).** NEW
   tools/fetch_goes_animation.py: band='geocolor'|'fire_temperature', satellite
   'goes-18', sector 'conus', (start_utc, end_utc, step_minutes). Build the frame
   list from SLIDER latest_times.json; per frame stitch the tile grid covering the
   AOI; reproject the fixed-grid PNG -> EPSG:4326 COG via _reproject_and_clip; one
   read_through per frame; return an ordered list[LayerURI]. Register (+1
   __init__.py line, categories.py 'weather_atmosphere', tool_query_corpus.yaml).
   Mirror fetch_usgs_nwis_gauges house style + fetch_goes_satellite primitives.

4. **agent (S4): PATH B (raw-band Fire Temperature) + RGB COG.** Add the
   noaa-goes18 ABI-L2-MCMIPC (or CMIPC) C07/C06/C05 read + the Fire Temperature RGB
   recipe (R=C07 0-60 C, G=C06 0-100 %, B=C05 0-75 %, gamma 1) -> a 3-band uint8 COG
   per frame (publish_layer multiband passthrough). Apply CF scale_factor/add_offset
   per band. This gives Fire Temperature without SLIDER if needed. Unit-test the band
   math + a hot-pixel red->white range assertion.

5. **agent (S5): model_goes_fire_animation workflow.** NEW
   workflows/model_goes_fire_animation.py chaining news/incident -> bbox + window ->
   fetch_goes_animation per product per frame -> emit frames in the postprocess_flood
   shape (distinct keys + shared style_preset + "GOES <product> step N" token + same
   bbox) -> STOP for bbox/window review (granularity gate) -> publish via TiTiler.
   Add the small step turning the news date claim / user window into start_utc/
   end_utc. Template the front half off model_news_event_ingest.py, the frame seam
   off postprocess_flood.py. Optionally call fetch_firms_active_fire (S2 date) +
   fetch_nifc_fire_perimeters for static overlays. Run the per-frame fetch in
   asyncio.to_thread (no-loop-blocking norm).

6. **agent (S6, optional): single-band GOES RGB style entries.** Only if a
   single-band scalar is emitted instead of a baked RGB COG: add 'goes_fire_temp' /
   'geocolor' entries to _TITILER_STYLE_REGISTRY in publish_layer.py. RGB COGs
   sidestep this via the multiband passthrough.

7. **testing (S7): live acceptance.** Drive the recreate prompt (section 11) end to
   end: news/incident -> Eureka-UT/eastern-NV bbox -> ~6.5h 2026-06-22 window ->
   GeoColor + Fire Temperature CONUS 5-min frames -> the scrubber animating both
   loops with the FIRMS detections overlaid. Assert ~78 frames, Fire-Temp hot pixels
   red over the Iron Fire AOI, and the scrubber labels carrying real UTC valid-times.

Critical path: S1 -> S2 -> S3 -> {S4, S5} -> S7. S6 only if single-band emit.

---

## 10. Cross-links

- FIRMS key already wired: [[project_credential_card_ssm_vault]] +
  services/agent/.env GRACE2_FIRMS_MAP_KEY + credential_registry.py FIRMS_MAP_KEY +
  fetch_firms_active_fire.py vault->env->demo chain.
- News-ingest front half: model_news_event_ingest.py (wildfire event type);
  [[feedback_research_real_pipelines_first]].
- Time-series animation seam: [[project_timeseries_animation_and_overlay_layout]] +
  postprocess_flood.py + SequenceScrubber.tsx + lib/animation_controller.ts.
- Generic-endpoint / semantic discovery: [[project_generic_endpoint_architecture]] +
  categories.py + data/tool_query_corpus.yaml + discover_dataset.py.
- Engine / data drivability ranking context:
  [[reference_engine_cloud_ai_drivability_ranking]] (this is a DATA demo, not an
  engine; no Batch).
- Fire-engine neighborhood: the QUIC-Fire / fire-spread engine is DEFERRED
  (project_post_sprint_10_roadmap notes ATCF & QUIC-Fire deferred); this demo is the
  fire MONITORING (satellite imagery + active-fire detections) lane, complementary to
  and not blocked on a fire-spread solver.
- Companion reference archive: reports/references/cira_goes_fire_animation/notes.md.

---

## 11. Recreate-this prompt (paste-ready)

Paste this into a GRACE-2 chat to recreate the CIRA animation:

  Pull the latest news on the wildfires burning near Eureka, Utah right now -
  the Iron Fire and the Hastings Fire to its northwest - plus the Kane Springs
  and Grapevine fires over in eastern Nevada. Look them up in the NIFC incident
  data to get their exact locations, then draw an AOI bounding box that covers
  all four fires (Juab and Tooele counties in Utah, Lincoln County in Nevada).

  Now recreate the CIRA GOES satellite animation of this fire cluster: use the
  GOES-18 (GOES-West) CONUS sector and pull BOTH the GeoColor product and the
  Fire Temperature product at 5-minute cadence over a roughly six-and-a-half-hour
  window on 2026-06-22, ending around 20:00 UTC (so start around 13:30 UTC). That
  is about 78 frames per product. Render each product as its own animated, time-
  scrubbable layer so I can play the loop and watch the fires evolve, and overlay
  the VIIRS active-fire detections from FIRMS for 2026-06-22 on top so I can see
  the hot pixels line up with the imagery.

  Show me the AOI bbox and the exact time window before you fetch all the frames
  so I can adjust them.

A one-liner variant: "Pull the news on the Iron / Hastings fires near Eureka Utah
and the Kane Springs / Grapevine fires in eastern Nevada, get an AOI bbox, then
recreate the CIRA GOES-18 GeoColor + Fire Temperature animation over a ~6.5h window
on 2026-06-22 (CONUS 5-min) and animate both products with FIRMS hot pixels
overlaid."

---

## 12. Sources (primary)

- NOAA GOES-R on AWS (Big-Data Program, noaa-goes18 bucket structure):
  https://registry.opendata.aws/noaa-goes/ ; README key layout / sector+product
  naming: github.com/awslabs/open-data-docs (noaa-goes16 README, same scheme for 18).
- CIMSS ABI File Naming Conventions:
  http://cimss.ssec.wisc.edu/goes/ABI_File_Naming_Conventions.pdf ; GOES-R ABI Scan
  Modes (Mode 6 FD 10-min / CONUS 5-min / Meso 1-min):
  https://www.goes-r.gov/users/abiScanModeInfo.html
- CIRA / RAMMB SLIDER (live tile + JSON endpoints used in this research):
  https://rammb-slider.cira.colostate.edu/ ; SLIDER-cli (verbatim tile +
  latest_times URL templates): https://github.com/colinmcintosh/SLIDER-cli
- Fire Temperature RGB Quick Guide (CIRA/RAMMB):
  https://rammb.cira.colostate.edu/training/visit/quick_guides/Fire_Temperature_RGB.pdf
  ; NESDIS STAR copy:
  https://www.star.nesdis.noaa.gov/goes/documents/QuickGuide_Fire_Temperature_RGB.pdf
- GeoColor Quick Guide (CIRA, 2025 update):
  https://rammb2.cira.colostate.edu/wp-content/uploads/2020/01/QuickGuide_CIRA_GeoColor_2025update.pdf
- NASA GIBS API docs (WMTS REST/KVP, TIME format):
  https://nasa-gibs.github.io/gibs-api-docs/access-basics/ ; Worldview adds
  GOES-East/West GeoColor:
  https://www.earthdata.nasa.gov/news/feature-articles/nasa-worldview-adds-geocolor-imagery-from-joint-nasa-noaa-goes-east-goes-west
- NASA FIRMS Area API (CSV endpoint, historical /{YYYY-MM-DD} positional, rate
  limit): https://firms.modaps.eosdis.nasa.gov/api/area/ ; MAP_KEY:
  https://firms.modaps.eosdis.nasa.gov/api/map_key/ ; VIIRS product description:
  https://firms.modaps.eosdis.nasa.gov/descriptions/FIRMS_VIIRS_Firehotspots.html
- WFIGS Current Wildland Fire Incident Locations (NIFC Open Data; live REST
  FeatureServer verified this session):
  https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0
  ; portal: https://data-nifc.opendata.arcgis.com/ ; InciWeb:
  https://inciweb.wildfire.gov/
- GRACE-2 live seam (cross-checked in-repo):
  services/agent/src/grace2_agent/tools/fetch_goes_satellite.py (S3 list +
  geostationary->EPSG:4326 reproject + COG: _list_keys_for_prefix,
  _KEY_START_TIME_RE, _doy_hour, _reproject_and_clip);
  services/agent/src/grace2_agent/workflows/postprocess_flood.py (frame manifest +
  scrubber + MAX_FLOOD_FRAMES even-subsample _select_frame_time_indices);
  services/agent/src/grace2_agent/workflows/model_news_event_ingest.py (news ->
  bbox, wildfire event type, _validate_sources arg coercion);
  services/agent/src/grace2_agent/tools/fetch_firms_active_fire.py (FIRMS overlay +
  GRACE2_FIRMS_MAP_KEY vault->env->demo);
  services/agent/src/grace2_agent/tools/fetch_nifc_fire_perimeters.py (sibling
  ArcGIS fetcher);
  services/agent/src/grace2_agent/tools/publish_layer.py (_is_rgba_or_multiband RGB
  passthrough + _TITILER_STYLE_REGISTRY + _resolve_titiler_style_params);
  services/agent/src/grace2_agent/tools/__init__.py (@register_tool + eager-import);
  services/agent/src/grace2_agent/categories.py (weather_atmosphere + fire
  categories); web/src/components/SequenceScrubber.tsx +
  web/src/lib/animation_controller.ts + web/src/lib/frame_preload.ts (the web
  scrubber stack).

---
---

# Demo 2: JPSS / VIIRS Day Fire (Santa Rosa Island, Channel Islands)

The SECOND fire-branch demo. Same animation/scrubber web stack, same FIRMS +
NIFC overlays, same publish_layer multiband-RGB passthrough, same no-Batch
posture as Demo 1 - but it deliberately exercises a DIFFERENT imagery endpoint
family (POLAR VIIRS on the JPSS satellites, not GEOSTATIONARY GOES ABI), a
DIFFERENT temporal model (a multi-day series of irregular polar overpasses, not
a smooth 5-min geostationary cadence), and a DIFFERENT geography (offshore
California, the Channel Islands). It recreates the CIRA / cira_csu Instagram post
of 2026-05-19: the VIIRS "Day Fire" product over a four-day window (2026-05-15
20:47 UTC to 2026-05-19 22:01 UTC) showing the Santa Rosa Island Fire in the
Channel Islands. ASCII only; "->" for arrows.

## D2.0. Verdict

**BUILD - reuses the Demo 1 plan almost wholesale; one genuinely net-new
fetcher.** Everything UPSTREAM of the imagery fetcher (fetch_wfigs_incident, the
news -> window step, the offshore AOI bbox) and everything DOWNSTREAM of it (the
postprocess_flood-shaped frame emission + scrubber group + bbox/window review
gate + FIRMS hot-pixel overlay + NIFC perimeter overlay + publish_layer
multiband passthrough + TiTiler) is the SAME as Demo 1 S1/S2/S5/overlays/publish.
The ONLY net-new bytes are a single new atomic fetcher, fetch_viirs_day_fire.py,
plus the multi-satellite irregular-overpass frame enumerator inside it. Data
access is FREE + keyless on the recommended path (CIRA Polar SLIDER jpss tiles
and NASA GIBS WMTS are both unauthenticated; only the FIRMS overlay needs the
already-wired GRACE2_FIRMS_MAP_KEY). So the honest framing is again BUILD:
clone the GOES plan, swap the imagery fetcher.

The net-new gaps - none fatal, each tied to a named seam:

1. **No VIIRS / JPSS raster-imagery handling exists anywhere.** The only VIIRS
   code in the repo is fetch_firms_active_fire.py, which is hot-pixel POINT
   vectors via the FIRMS CSV API, not imagery. The Day Fire product is a VIIRS
   RGB (3.7um brightness temp over 0.86um + 0.64um reflectance), an entirely new
   band set vs the GOES C07/C06/C05 used in Demo 1. This is the core net-new
   fetcher.

2. **Multi-day POLAR-pass frame assembly is new temporal logic.** Demo 1's
   frame-list code (_list_recent_keys / _pick_most_recent_key / _doy_hour /
   _KEY_START_TIME_RE in fetch_goes_satellite.py) is anchored on a geostationary
   5-min cadence and a most-recent picker. JPSS frames come from irregular polar
   overpass times - a few per satellite per day across SUOMI-NPP + NOAA-20 +
   NOAA-21 over the 4-day window - so the enumerator must LIST passes by overpass
   timestamp across multiple satellites and merge/sort them, not snap to a fixed
   cadence. This is new code regardless of which imagery source is chosen.

3. **A GIBS/Worldview WMTS-with-TIME imagery path is currently UNSUPPORTED.**
   ogc_adapter.py (lines 389-398) hard-raises OGCAdapterError for WMTS GetTile
   ("v0.1 substrate does not implement this dialect", OQ-47-WMTS-DIALECT). The
   recommended path SIDESTEPS this by using CIRA Polar SLIDER tiles (the same
   tile-stitch shape as Demo 1 PATH A, which is itself still unbuilt), so WMTS is
   NOT on the critical path. GIBS is still the cleanest way to ENUMERATE the polar
   pass timestamps (its granule TIME domain) and to fetch the hot-pixel /
   thermal-anomaly overlay, neither of which needs the missing GetTile dialect.

4. **Offshore-CA AOI handling is slightly new.** The GOES tool carries a
   _CONUS_SECTOR_BBOX guard tuned to land-CONUS; the Channel Islands AOI is
   offshore and a VIIRS/SLIDER path has no such guard to reuse - a small new
   bbox/AOI handling. (VIIRS sees the AOI in every swath, so there is no coverage
   problem; it is exactly the kind of offshore AOI where polar VIIRS beats the
   edge-of-good-geometry GOES CONUS sector, which is part of why VIIRS is the
   right instrument here.)

If those are accepted, this is a cheap, high-contrast second fire demo: it proves
the fire branch generalizes across satellite families and temporal models while
reusing the entire animation + overlay + publish stack. Ordered build plan is
D2.6.

## D2.1. The demo in one paragraph

A user pastes the CIRA Santa Rosa caption (or just says "pull the news on the
Santa Rosa Island fire in the Channel Islands and recreate the JPSS Day Fire
animation"). The agent (1) resolves the named incident - "Santa Rosa Island" -
to an authoritative point + discovery time via fetch_wfigs_incident, resolving by
IncidentName (it is an offshore island, NOT a normal CONUS county geometry); (2)
draws the Channel Islands AOI bbox (Santa Rosa Island, or all three western
islands San Miguel + Santa Rosa + Santa Cruz to match the still); (3) takes the
4-day UTC window 2026-05-15 20:47Z to 2026-05-19 22:01Z (the user's lever; the
WFIGS FireDiscoveryDateTime is the sanity floor); (4) ENUMERATES the VIIRS
DAYTIME overpasses across SUOMI-NPP + NOAA-20 + NOAA-21 in that window (roughly
36-48 daytime passes total) and per pass fetches the VIIRS Day Fire RGB - either
ready-made from CIRA Polar SLIDER (sat=jpss, recommended) or composited from raw
VIIRS L1b bands - reprojecting each to an EPSG:4326 COG over the AOI; (5) emits
the per-pass COGs in the SAME shape postprocess_flood emits flood frames, each
labelled with its REAL irregular UTC pass time + which satellite, so the existing
scrubber animates them; and (6) overlays the FIRMS active-fire detections (the
SAME VIIRS instrument, so the hot pixels and the Day Fire red pixels co-register
tightly) and the NIFC fire perimeter as static co-registered layers. The result
is the CIRA loop, recreated and scrubbable, over a sparse, irregularly-spaced
polar time series rather than a smooth 5-min loop.

Two HARD requirements, both already PASS (same as Demo 1):
- DATA ACCESS (PASS): the recommended imagery path (CIRA Polar SLIDER jpss) is
  keyless HTTPS; GIBS WMTS is keyless; only the FIRMS overlay needs a key and
  GRACE2_FIRMS_MAP_KEY is already wired.
- ANIMATION WEB STACK (PASS): the scrubber/controller/grouping web stack needs
  ZERO changes if frames are emitted in the flood-frame shape.

## D2.2. Why this exercises DIFFERENT endpoints than Demo 1

| Axis | Demo 1 (GOES) | Demo 2 (JPSS / VIIRS) |
|------|---------------|------------------------|
| Satellite family | GOES-18 (GOES-West), GEOSTATIONARY | SUOMI-NPP + NOAA-20 + NOAA-21, POLAR (sun-synchronous LEO) |
| Instrument | ABI | VIIRS |
| Cadence | smooth 5-min CONUS scan | irregular polar overpasses, a few per satellite per day |
| Temporal model | fixed step over a ~6.5h window (~78 frames) | merge/sort multi-satellite overpass timestamps over a 4-day window (~36-48 daytime frames) |
| Imagery product | GeoColor + Fire Temperature RGB (ABI C07/C06/C05 SWIR) | Day Fire RGB (3.7um BT + 0.86um NIR + 0.64um visible - NIR/visible G/B, not SWIR) |
| Resolution | ABI ~2 km | VIIRS I-band 375 m (the selling point: 375 m fire detail) |
| Ready-made source | CIRA SLIDER goes-18 tiles | CIRA Polar SLIDER jpss tiles + NASA GIBS / Worldview VIIRS WMTS |
| Geography | land-CONUS (Eureka UT / eastern NV) | offshore CA (Channel Islands) |
| Geometry to reproject | single fixed geostationary grid | pre-gridded SLIDER polar grid (ready-made) OR swath/sinusoidal per-granule (raw L1b) |

The defining product difference: the VIIRS Day Fire RGB is NOT the GOES Fire
Temperature RGB and NOT even the VIIRS Fire Temperature RGB. All three put a
3.x um thermal channel in RED, but:
- GOES Fire Temperature RGB: R = ABI C07 3.9um BT, G = ABI C06 2.2um refl, B = ABI
  C05 1.6um refl. G/B are SWIR.
- VIIRS Fire Temperature RGB (a separate CIRA Quick Guide): R = 3.74um, G = 2.25um
  (M11), B = 1.61um (M10). G/B are SWIR.
- VIIRS DAY FIRE RGB (THIS demo): R = 3.7um BT, G = 0.86um NIR reflectance, B =
  0.64um visible reflectance. G/B are NIR + visible (vegetation + smoke), NOT
  SWIR. That is why the CIRA still shows GREEN land + BLUE smoke under thermal-red
  fire - a thermal-red layer over a near-true-color land/veg/smoke base. Do not
  cross the recipes.

## D2.3. The Day Fire RGB recipe (and the ready-made shortcut)

READY-MADE (recommended for v0.1): CIRA Polar SLIDER carries the Day Fire RGB as a
pre-rendered product for the JPSS satellites (sat=jpss), so you do NOT have to
composite from bands. Same tile-stitch + reproject shape as Demo 1 PATH A, just
sat=jpss + a polar time index. Confirm the exact product slug from the JPSS
product-list JSON at build time (geocolor is cira_geocolor; the fire RGB is a
sibling cira_-prefixed slug) - do NOT hard-code a guessed token.

RAW RECIPE (PATH B, only if 375 m I-band control beyond SLIDER is required) -
from the CIRA / RAMMB VIIRS Day Fire RGB Quick Guide:
- RED   = VIIRS 3.7um band (I04 at 375 m, or M13 at 750 m) BRIGHTNESS TEMPERATURE,
          stretch 0 to 60 C (273.15 to 333.15 K), gamma 0.4, NOT inverted.
- GREEN = VIIRS 0.86um band (I02 at 375 m, or M07 at 750 m) REFLECTANCE, stretch
          0 to 100 %, gamma 1.0.
- BLUE  = VIIRS 0.64um band (I01 at 375 m, or M05 at 750 m) REFLECTANCE, stretch
          0 to 100 %, gamma 1.0.
- Per-channel clip to [0,1] after the linear stretch + gamma.

Interpretation (matches the CIRA still): active fire = red; new burn scar =
reddish-brown; smoke = blue; clouds = cyan; healthy vegetation = green; bare /
old-burn / urban = brown; water / night = near-black (so the island + fire pop
against a near-black sea over the Channel Islands).

UNITS WARNING: RED is Kelvin BT (subtract 273.15 for the 0-60 C stretch); GREEN /
BLUE are 0-1 reflectance factors (x100 for the 0-100 % stretch). Mixing the units
yields an all-dark or saturated image. Gamma 0.4 on RED (not 1.0) is easy to miss.
If compositing from raw bands, pick ONE resolution family (I-band 375 m is the
selling point) and resample the odd channel; do not stack 375 m + 750 m arrays
unaligned. SLIDER and GIBS already handle this.

KNOWN ARTIFACT (not a bug): the 3.7um channel saturates ~368 K (~95 C); very
intense fire cores (~500 K) "fold over" and read COLD -> some core fire pixels
appear blue/cyan instead of red. Expect this inside the hottest Santa Rosa fire
pixels. The honesty floor still applies - an all-dark / empty frame must NOT read
status=ok.

GIBS CANNOT make the REAL Day Fire RGB: GIBS corrected-reflectance has no 3.7um
thermal band, so a GIBS-only composite yields a true-color or M11-I2-I1
false-color (burn-scar) base, NOT the thermal-red look. Use SLIDER for the actual
Day Fire RGB; use GIBS for (a) pass-time enumeration, (b) a georeferenced veg /
burn-scar base, (c) the Thermal_Anomalies hot-pixel overlay.

## D2.4. Polar cadence + frame assembly (the new temporal model)

SUOMI-NPP, NOAA-20, NOAA-21 are sun-synchronous LEO (~101 min orbit), all crossing
the equator ~13:30 local solar time ascending (daytime) / ~01:30 descending
(night), all three within ~1 hour of each other. Over a CA AOI each satellite
makes ~3-4 DAYTIME passes/day; across all three that is ~9-12 daytime overpasses/
day that see the Channel Islands (some are oblique / edge-of-swath and partly
miss). A given AOI is sampled a handful of times per day - NOT imaged
continuously - so the animation is a SPARSE, IRREGULARLY-SPACED time series.

FRAME COUNT for the 4-day window: ~4 days x ~9-12 good daytime passes/day =
roughly 36 to 48 daytime frames. Day Fire is a DAY product (G/B are reflectance ->
black at night), so DROP descending/night passes - night fire monitoring is the
separate Active Fire / Fire Temperature / DNB lane, not this product; keeping it
day-only matches the CIRA caption. 36-48 frames is well under MAX_FLOOD_FRAMES=144,
so no subsampling needed; reuse postprocess_flood._select_frame_time_indices only
as a safety cap. The CIRA caption's exact start/end (20:47Z, 22:01Z) are the
bracketing passes - consistent with ~3-4 SNPP + N20 + N21 daytime overpasses
bunched in the ~19-23Z window each day. The scrubber MUST label each frame with
its REAL irregular UTC pass time + which satellite, since spacing is uneven
(unlike the GOES even 5-min cadence). Do not assume even spacing or interpolate
between passes.

ENUMERATING per-pass frames (two keyless ways):
(A) READY-MADE TILE (recommended): CIRA Polar SLIDER. Get available pass
    timestamps from the SLIDER time index JSON (latest_times.json /
    available_dates.json) under the jpss sat + sector + product, then per
    timestamp stitch the tile grid. Tile URL (same scheme as GOES SLIDER but
    sat=jpss):
      https://rammb-slider.cira.colostate.edu/data/imagery/<YYYYMMDD>/jpss---<sector>/<product>/<YYYYMMDDHHMMSS>/<zoom>/<tileY>_<tileX>.png
    Each <YYYYMMDDHHMMSS> directory IS one polar pass; enumerating those dirs over
    the 4 days = enumerating the frames. No swath resampling (SLIDER pre-grids).
(B) GRANULE (GIBS WMTS): GIBS per-overpass *_Granule layers at PT6M cadence keyed
    by subdaily TIME = YYYY-MM-DDTHH:MM:SSZ. The set of valid timestamps in the
    window = the set of frames; pull them from the GIBS GetCapabilities time
    dimension. REST tile:
      https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/<Layer>/default/<TIME>/<TileMatrixSet>/<z>/<row>/<col>.<ext>
    BUT GIBS corrected-reflectance granules have NO 3.7um thermal band -> a
    near-true-color / M11-I2-I1 false-color base, not the true Day Fire RGB. Use
    GIBS for frame-time enumeration + overlay, SLIDER for the imagery.

Practical recipe: clone Demo 1's S3 -> frame-list -> per-frame-COG flow, but (1)
drive the frame list from the polar PASS timestamps (SLIDER jpss time index or
GIBS granule TIME domain) instead of a fixed 5-min step; (2) keep only daytime /
ascending passes; (3) one cache.read_through per pass; (4) emit ~36-48 ordered
COGs in the postprocess_flood frame shape with real UTC + satellite labels; the
existing web scrubber animates them unchanged.

## D2.5. Data sources

| Source | What it gives | Access / auth | Use in this demo |
|--------|---------------|---------------|------------------|
| CIRA Polar SLIDER (RAMMB/CIRA), sat=jpss (RECOMMENDED, KEYLESS) | READY-MADE pre-rendered VIIRS Day Fire RGB PNG tiles + a JSON time index; each <YYYYMMDDHHMMSS> dir = one polar pass. Products incl. cira_geocolor + the cira_-prefixed fire RGB (confirm slug from JPSS product-list). Sats SNPP / NOAA-20 (NOAA-21 as available). | Keyless HTTPS GET. Tile: .../data/imagery/<YYYYMMDD>/jpss---<sector>/<product>/<YYYYMMDDHHMMSS>/<zoom>/<tileY>_<tileX>.png ; index: .../data/json/jpss/<sector>/<product>/latest_times.json (+ available_dates.json). UI: rammb-slider.cira.colostate.edu/?sat=jpss | PATH A (RECOMMENDED): no band compositing, no swath resampling; stitch tile grid -> reproject fixed SLIDER grid -> EPSG:4326 COG via the lifted _reproject_and_clip. Same code shape as Demo 1 PATH A, sat=jpss + polar time index. |
| NASA GIBS / Worldview VIIRS WMTS (KEYLESS, GEOREFERENCED) | Per-pass corrected-reflectance granule layers (VIIRS_{SNPP,NOAA20,NOAA21}_CorrectedReflectance_TrueColor_Granule, PT6M) + the M11-I2-I1 burn-scar false-color + the VIIRS_{...}_Thermal_Anomalies_375m_{Day,Night,All} hot-pixel overlay. Already EPSG:4326 / 3857 - no swath resampling. | Keyless WMTS REST / WMS. GetCapabilities: gibs.earthdata.nasa.gov/wmts/epsg4326/best/wmts.cgi?request=GetCapabilities ; docs: nasa-gibs.github.io/gibs-api-docs/access-basics/ | Cleanest way to ENUMERATE polar pass timestamps (granule TIME domain) + a georeferenced veg / burn-scar base + the hot-pixel overlay. CANNOT make the true thermal-red Day Fire RGB (no 3.7um band). WMTS GetTile is the dialect ogc_adapter.py stubs out (OQ-47-WMTS-DIALECT). |
| NOAA JPSS on AWS (noaa-nesdis-*-pds), raw VIIRS L1b / SDR (PATH B, FULL CONTROL) | Raw VIIRS SDR / L1b bands for the Day Fire RGB: I04 (3.7um BT, 375m) or M13; I02 (0.86um, 375m) or M07; I01 (0.64um, 375m) or M05. Geolocation: GITCO/GMTCO (SDR) or VNP03/VJ103 (L1b). | Anonymous S3 (us-east-1). Buckets noaa-nesdis-snpp-pds / noaa-nesdis-n20-pds / noaa-nesdis-n21-pds; registry: registry.opendata.aws/noaa-jpss/ | PATH B: only if 375 m I-band fidelity beyond SLIDER is needed. COST: granules are SWATH (curved scan, bowtie overlap) -> must geolocate + EWA-resample (Polar2Grid-style) to EPSG:4326 before compositing - the swath-resampling pain SLIDER and GIBS avoid. |
| NASA FIRMS active-fire hot pixels (ALREADY WIRED - reuse) | 375 m VIIRS active-fire detections (lat/lon, bright_ti4 = I04 3.7um BT, bright_ti5, FRP, confidence, acq_date/time, daynight, satellite). Same VIIRS instrument as the Day Fire imagery. | MAP_KEY (GRACE2_FIRMS_MAP_KEY already wired). Area API CSV + the Demo 1 historical-date positional /{YYYY-MM-DD}. Sources VIIRS_SNPP_NRT, VIIRS_NOAA20_NRT (NOAA-21 not yet a source). | The co-registered hot-pixel vector overlay; because it is the SAME VIIRS instrument as the imagery, the hot pixels and the Day Fire red pixels physically co-register - the demo's cross-check. Reuse Demo 1 S2 verbatim. |
| NIFC / WFIGS incident + perimeters (ALREADY WIRED + the Demo 1 fetch_wfigs_incident) | IncidentName, FireDiscoveryDateTime (2026-05-15), IncidentSize (-> ~18,379 ac peak), PercentContained, InitialLat/Lon, IrwinID; the perimeter polygon matching the CIRA still. InciWeb id CACNP Santa Rosa Island Fire. | Keyless ArcGIS REST (same org T4QMspbfLg3qTGWY). Incident pts: services3.arcgis.com/T4QMspbfLg3qTGWY/.../WFIGS_Incident_Locations_Current/FeatureServer/0 (where IncidentName='Santa Rosa Island'). | fetch_wfigs_incident (Demo 1 S1) reused VERBATIM; only the query name + AOI change. Resolve BY NAME, not county - it is an offshore island (POOState/POOCounty may be sparse). The perimeter polygon is the exact overlay shown in the still. |

VERIFIED bboxes (west, south, east, north): Santa Rosa Island =
-120.25, 33.88, -119.95, 34.05 ; all three western Channel Islands (San Miguel +
Santa Rosa + Santa Cruz, to match the still) = -120.50, 33.85, -119.50, 34.10.
Island center ~33.58 N, -120.06 W. This AOI is offshore and OUTSIDE the GOES-18
CONUS sector's good-geometry zone but well inside every VIIRS swath - part of why
VIIRS is the right instrument here.

INCIDENT FACTS: "Santa Rosa Island Fire" (InciWeb CACNP; CACNP = Channel Islands
National Park). Started 2026-05-15 (boat-grounding flare ignition, human-caused),
fully contained 2026-06-04, peak 18,379 acres (~1/3 of the 53,195-acre island) -
largest CA wildfire of 2026 and the largest ever recorded on the Channel Islands.
This is the fire in the CIRA 2026-05-19 post (window 2026-05-15 20:47Z to
2026-05-19 22:01Z).

## D2.6. DELTA vs the GOES S1..S7 plan (reuse vs net-new)

REUSED FROM DEMO 1, UNCHANGED:
- S1 fetch_wfigs_incident applies VERBATIM. Santa Rosa resolves the same way:
  query IncidentName='Santa Rosa Island' + POOState='US-CA', outFields
  FireDiscoveryDateTime / InitialLat-Lon / IncidentSize, outSR=4326, same ArcGIS
  org. Only the literal incident name + state filter change (data, not code).
- S2 FIRMS historical-date positional applies VERBATIM and is MORE central here -
  fetch_firms_active_fire already speaks VIIRS_SNPP_NRT + VIIRS_NOAA20_NRT (the
  exact JPSS sensors). Add the trailing /{YYYY-MM-DD} + day_range=1 for the
  2026-05-15..05-19 window (_VALID_SOURCES at line 155, days clamp 158-159, source
  Literal 601-602). No new work beyond Demo 1 S2.
- S5 model_*_fire_animation chaining workflow applies UNCHANGED in SHAPE:
  news/incident -> bbox + window -> per-frame fetch -> emit postprocess_flood-
  shaped frames -> bbox/window review gate -> publish. Front half off
  model_news_event_ingest.py; frame seam off postprocess_flood.py; dispatch via
  TOOL_REGISTRY[name].fn; per-frame fetch in asyncio.to_thread.
- The scrubber frame CONTRACT applies VERBATIM (postprocess_flood.py: distinct
  per-frame cache keys + role='context' + shared style_preset + an ISO-time NAME
  token + identical bbox -> detectSequentialGroups / AnimationController /
  SequenceScrubber animate with ZERO web changes; _select_frame_time_indices line
  824 + MAX_FLOOD_FRAMES=144 line 87 reused as a safety cap).
- FIRMS hot-pixel overlay (fetch_firms_active_fire) + NIFC perimeter overlay
  (fetch_nifc_fire_perimeters) as static co-registered layers apply VERBATIM - the
  CIRA still literally shows a NIFC-style perimeter polygon + VIIRS hot pixels on
  Santa Rosa Island, so both map 1:1.
- publish_layer >=3-band RGB / RGBA passthrough applies VERBATIM: a baked Day Fire
  RGB COG renders directly via _is_rgba_or_multiband / _resolve_titiler_style_
  params with no new colormap; TiTiler serves the COGs 24/7.
- The fetcher house style + registration plumbing applies VERBATIM (module
  docstring + typed-error hierarchy + AtomicToolMetadata + pure helpers in __all__
  + @register_tool + one read_through per frame + one eager-import line in
  tools/__init__.py + a categories.py _TOOL_CATEGORY slot + a tool_query_corpus
  .yaml entry).
- From fetch_goes_satellite.py specifically, reusable VERBATIM: the rasterio.warp
  reproject -> EPSG:4326 + COG-write CORE inside _reproject_and_clip (incl. the
  COG->GTiff fallback + all-NaN honesty guard), the bbox validation / quantization
  helpers, the unauthenticated ?list-type=2 S3 lister _list_keys_for_prefix, the
  read_through cache-key discipline, and the dynamic-1h ttl_class.
- The NO-AWS-Batch posture applies VERBATIM: data-fetch + animation on the agent +
  TiTiler path, per-frame fetch offloaded to asyncio.to_thread, frames cache
  independently. JPSS has FEWER frames than GOES (polar passes, tens vs ~78), so
  it is strictly LIGHTER than Demo 1.

NET-NEW FOR JPSS (the only new bytes):
- A VIIRS Day Fire IMAGERY fetcher (fetch_viirs_day_fire.py) - the core net-new.
  NO existing VIIRS/JPSS raster-imagery handling exists; the only VIIRS code is the
  FIRMS hot-pixel POINT fetcher. New band set (3.7um BT + 0.86um NIR + 0.64um
  visible) vs GOES C07/C06/C05.
- Multi-day POLAR-pass frame assembly - new temporal logic. GOES frame-list code
  is geostationary-5-min / most-recent; JPSS needs a multi-satellite irregular
  overpass enumerator that merges + sorts SNPP + NOAA-20 + NOAA-21 pass timestamps
  and keeps day-only.
- A tile-grid stitch + fixed-grid reproject primitive IF the SLIDER path is chosen
  (no PIL/Image.paste, no mercantile/morecantile, no STAC client, no tile-stitcher
  in the repo). NOTE this is SHARED net-new with Demo 1 PATH A (also unbuilt), not
  unique to JPSS - but VIIRS L1b (PATH B) is swath/sinusoidal per-granule, so
  _reproject_and_clip's rasterio CORE is reusable while its "inherit one
  geostationary CRS" assumption is NOT.
- A GIBS WMTS-with-TIME GetTile path is genuinely net-new and currently
  UNSUPPORTED (ogc_adapter.py OQ-47-WMTS-DIALECT). NOT on the critical path
  because SLIDER sidesteps it; only needed if GIBS imagery is preferred over
  SLIDER.
- Offshore-CA AOI handling - the GOES tool's _CONUS_SECTOR_BBOX guard does not
  apply; a small new bbox handling for the offshore island AOI (no coverage
  problem - VIIRS sees it in every swath).
- A VIIRS imagery TiTiler style ONLY if a single-band scalar is emitted; a baked
  Day Fire RGB COG sidesteps it via the publish_layer multiband passthrough.

## D2.7. Recommendation: generalize the workflow, NOT the fetcher

Build a SEPARATE atomic fetcher fetch_viirs_day_fire.py (sibling to the Demo 1
fetch_goes_animation.py), and have ONE shared animation WORKFLOW
(model_satellite_fire_animation, generalized from model_goes_fire_animation)
dispatch whichever fetcher matches the requested imagery source. Rationale:

1. The GOES and VIIRS fetchers share almost no BODY - GOES is one geostationary
   netCDF with a fixed grid + 5-min cadence + a most-recent S3-key picker; VIIRS
   is multi-satellite irregular polar overpasses + swath/sinusoidal geolocation
   (or GIBS WMTS-with-TIME). A single fetch_satellite_animation tool would be a
   thin dispatcher wrapping two DISJOINT implementations behind a band/source
   switch - two tools wearing one name, which hurts the model's tool-selection
   clarity and the typed-error surface.
2. Every existing fetch_* is a hand-written single-source Python module (no
   generic-fetcher executor exists in services/agent/src; the YAML-wrapper concept
   is doc-only), so a separate fetcher is the HOUSE pattern.
3. What genuinely SHOULD be shared is UPSTREAM of the fetcher (fetch_wfigs_incident,
   news -> window, the offshore bbox) and DOWNSTREAM of it (the postprocess_flood-
   shaped frame emission + scrubber group + review gate + FIRMS/NIFC overlays +
   publish), and those already live in the WORKFLOW layer - so generalize THERE.
   Keep _reproject_and_clip's rasterio warp + COG CORE shared (lift it to a small
   helper if convenient), but let each fetcher own its source-specific
   geolocation.

Net: a separate fetch_viirs_day_fire tool, one generalized fetch/animation
WORKFLOW, and shared reproject + frame-emission primitives.

## D2.8. Integration seam

ATOMIC: NEW services/agent/src/grace2_agent/tools/fetch_viirs_day_fire.py -
AtomicToolMetadata(name='fetch_viirs_day_fire', ttl_class='dynamic-1h',
source_class='viirs_satellite', cacheable=True); accept satellite in
{suomi-npp, noaa-20, noaa-21} (+ an 'all' merge), product='day_fire', sector/AOI
bbox, (start_utc, end_utc); enumerate overpass-time frames across satellites; per
frame pull imagery (CIRA Polar SLIDER VIIRS tile-stitch OR GIBS WMTS/WMS-with-TIME
OR VIIRS L1b/L2 granule composite) -> reuse the rasterio warp -> EPSG:4326 +
COG-write CORE lifted from fetch_goes_satellite._reproject_and_clip -> one
read_through per frame -> return an ordered list[LayerURI].

REGISTER: +1 eager-import line in tools/__init__.py (near the fetch_firms_active_
fire import ~line 254), add 'fetch_viirs_day_fire' to categories.py _TOOL_CATEGORY
(fire alongside fetch_firms_active_fire at line 313, or weather_atmosphere
alongside fetch_goes_satellite at line 271), and a data/tool_query_corpus.yaml
entry.

WORKFLOW: generalize model_goes_fire_animation into model_satellite_fire_animation
(or add a JPSS branch) chaining fetch_wfigs_incident -> Santa-Rosa-Island bbox +
the 2026-05-15..05-19 window -> dispatch fetch_viirs_day_fire per frame via
TOOL_REGISTRY -> emit frames in the postprocess_flood shape (distinct keys +
shared style_preset + 'VIIRS Day Fire <ISO-time>' NAME token + identical bbox so
detectSequentialGroups / SequenceScrubber animate with NO web change) -> STOP at
the bbox/window review gate -> overlay fetch_firms_active_fire (VIIRS_NOAA20_NRT /
SNPP, historical date) + fetch_nifc_fire_perimeters -> publish_layer multiband-RGB
passthrough -> TiTiler. NO AWS Batch.

The ONLY net-new bytes are inside fetch_viirs_day_fire (VIIRS imagery acquisition +
polar-overpass frame enumeration + swath/sinusoidal-or-WMTS reproject); the WMTS
path additionally requires implementing the GetTile-with-TIME dialect that
ogc_adapter.py currently stubs out (OQ-47-WMTS-DIALECT).

## D2.9. Ordered delta build steps (smallest-first)

Assumes Demo 1 S1 (fetch_wfigs_incident) + S2 (FIRMS historical date) already
landed; if not, they come first (shared, unchanged).

1. **agent (J1): fetch_wfigs_incident query for Santa Rosa.** DATA-ONLY reuse of
   Demo 1 S1 - call it with IncidentName='Santa Rosa Island' + POOState='US-CA',
   resolve by NAME (offshore island). No code change if S1 landed. (Smallest;
   resolves the incident -> authoritative point + discovery-time floor.)

2. **agent (J2): FIRMS historical-date overlay for the window.** Reuse Demo 1 S2
   verbatim with source=VIIRS_NOAA20_NRT / VIIRS_SNPP_NRT, the Channel Islands
   bbox, and dates across 2026-05-15..05-19 (day_range=1 + the trailing date). No
   new code if S2 landed. (Tiny; the co-registered hot-pixel overlay - same VIIRS
   instrument as the imagery.)

3. **agent (J3): fetch_viirs_day_fire tool - PATH A (CIRA Polar SLIDER).** NEW
   tools/fetch_viirs_day_fire.py: satellite in {suomi-npp, noaa-20, noaa-21, all},
   product='day_fire', the Channel Islands bbox, (start_utc, end_utc). Build the
   frame list from the SLIDER jpss time index (each <YYYYMMDDHHMMSS> dir = one
   pass), MERGE + SORT passes across satellites, keep DAY-only; per pass stitch the
   tile grid covering the AOI; reproject the fixed SLIDER grid -> EPSG:4326 COG via
   the lifted _reproject_and_clip; one read_through per frame; return an ordered
   list[LayerURI] carrying real UTC + satellite labels. Register (+1 __init__.py
   line, categories.py 'fire', tool_query_corpus.yaml). Mirror fetch_usgs_nwis_
   gauges house style + fetch_goes_satellite primitives. CONFIRM the SLIDER fire
   product slug from the JPSS product-list at build time.

4. **agent (J4, optional): PATH B (raw VIIRS L1b Day Fire) + RGB COG.** Add the
   noaa-nesdis-*-pds VIIRS I04/I02/I01 (or M13/M07/M05) read + the Day Fire RGB
   recipe (R=3.7um BT 0-60 C g0.4, G=0.86um 0-100 % g1, B=0.64um 0-100 % g1) -> a
   3-band uint8 COG per pass (publish_layer multiband passthrough). Geolocate +
   EWA-resample the SWATH (Polar2Grid-style) to EPSG:4326 - do NOT reuse the
   geostationary CRS assumption. Pick ONE resolution family (I-band 375 m).
   Unit-test the band math + a hot-pixel red->white range assertion + the fold-over
   note. Only if 375 m I-band control beyond SLIDER is required.

5. **agent (J5): model_satellite_fire_animation workflow (or a JPSS branch).**
   Generalize model_goes_fire_animation: chain fetch_wfigs_incident -> Santa-Rosa-
   Island bbox + the 4-day window -> dispatch fetch_viirs_day_fire per pass ->
   emit frames in the postprocess_flood shape (distinct keys + shared style_preset
   + 'VIIRS Day Fire <ISO-time> (<sat>)' token + same bbox) -> STOP for bbox/window
   review (granularity gate) -> overlay fetch_firms_active_fire (J2 date) +
   fetch_nifc_fire_perimeters -> publish via TiTiler. Run the per-frame fetch in
   asyncio.to_thread (no-loop-blocking norm).

6. **agent (J6, optional): GIBS WMTS-with-TIME path + OQ-47-WMTS-DIALECT.** Only
   if GIBS imagery is preferred over SLIDER: implement the WMTS GetTile-with-TIME
   dialect ogc_adapter.py currently stubs out (lines 389-398), or use a GIBS WMS
   GetMap-with-TIME shortcut (the WMS branch IS implemented). Off the critical path.

7. **testing (J7): live acceptance.** Drive the recreate prompt (D2.10) end to
   end: news/incident -> Channel Islands bbox -> 2026-05-15..05-19 window ->
   VIIRS Day Fire daytime passes across SNPP + NOAA-20 + NOAA-21 -> the scrubber
   animating ~36-48 irregularly-spaced frames with FIRMS hot pixels + the NIFC
   perimeter overlaid. Assert frames are day-only, labelled with real UTC pass
   times + satellite, the Day Fire red pixels co-register with the FIRMS hot
   pixels, and the island + fire pop against a near-black sea (no per-frame
   auto-stretch).

Critical path: J1 -> J2 -> J3 -> J5 -> J7. J4 only for 375 m raw control; J6 only
if GIBS imagery is chosen.

## D2.10. Recreate-this prompt (paste-ready)

Paste this into a GRACE-2 chat to recreate the CIRA Santa Rosa animation:

  Pull the news on the Santa Rosa Island fire that burned in the Channel Islands
  off the California coast back in May - it was the big one on Santa Rosa Island
  in Channel Islands National Park, the largest California wildfire of 2026. Look
  it up in the NIFC incident data to get its location and when it started (resolve
  it by the incident name, not by county, since it is an offshore island), then
  draw an AOI bounding box over the Channel Islands that covers San Miguel, Santa
  Rosa, and Santa Cruz islands.

  Now recreate the CIRA JPSS satellite animation of this fire. Use the VIIRS Day
  Fire product from the JPSS polar satellites - Suomi-NPP, NOAA-20, and NOAA-21 -
  and pull every daytime overpass over the four-day window from 20:47 UTC on
  2026-05-15 to 22:01 UTC on 2026-05-19. These are polar passes, so they are
  irregularly spaced (a few per satellite per day, not a smooth loop) - label each
  frame with its real UTC pass time and which satellite it came from. That should
  be somewhere around three to four dozen daytime frames. Render the Day Fire
  imagery as one animated, time-scrubbable layer so I can play the loop and watch
  the fire grow on the island, and overlay the VIIRS active-fire hot pixels from
  FIRMS for those dates plus the NIFC fire perimeter on top.

  Show me the AOI bbox and the exact list of pass times before you fetch all the
  frames so I can adjust them.

A one-liner variant: "Pull the news on the Santa Rosa Island fire in the Channel
Islands (resolve the NIFC incident by name), get a Channel Islands AOI bbox, then
recreate the CIRA JPSS VIIRS Day Fire animation over the 4-day 2026-05-15 to
2026-05-19 window - every daytime SNPP / NOAA-20 / NOAA-21 overpass as a scrubbable
frame labelled with its real UTC pass time + satellite - with the FIRMS hot pixels
and the NIFC perimeter overlaid."

## D2.11. Cross-links

- Sibling demo (same web stack + overlays + publish): Demo 1 above (GOES-18
  GeoColor + Fire Temperature, Iron Fire).
- Companion reference archive:
  reports/references/cira_santa_rosa_jpss_fire/notes.md.
- FIRMS key already wired: [[project_credential_card_ssm_vault]] +
  GRACE2_FIRMS_MAP_KEY + fetch_firms_active_fire.py vault->env->demo chain.
- Time-series animation seam: [[project_timeseries_animation_and_overlay_layout]]
  + postprocess_flood.py + SequenceScrubber.tsx + lib/animation_controller.ts.
- Generic-endpoint / semantic discovery: [[project_generic_endpoint_architecture]]
  + categories.py + data/tool_query_corpus.yaml.
- WMTS dialect gap: ogc_adapter.py lines 389-398 (OQ-47-WMTS-DIALECT) - off the
  critical path because SLIDER sidesteps it.
- This is a DATA demo, not an engine: [[reference_engine_cloud_ai_drivability_
  ranking]] (no Batch). Fire MONITORING lane (satellite imagery + active-fire
  detections), complementary to and not blocked on a fire-spread solver.

## D2.12. Sources (primary)

- CIRA / RAMMB VIIRS Day Fire RGB Quick Guide (recipe R=3.7um 0-60 C g0.4,
  G=0.86um 0-100 % g1, B=0.64um 0-100 % g1):
  https://rammb2.cira.colostate.edu/wp-content/uploads/2020/01/VIIRS_Day_Fire_RGB_Quick_Guide_v1.pdf
- CIRA / RAMMB VIIRS Day Land Cloud Fire RGB Quick Guide ("Natural Fire Color",
  368 K saturation / fold-over note):
  https://rammb.cira.colostate.edu/training/visit/quick_guides/VIIRS_Day_Land_Cloud_Fire_RGB_Quick_Guide_10182018.pdf
- CIRA / RAMMB VIIRS Fire Temperature RGB Quick Guide (the DIFFERENT SWIR-based
  product, for contrast):
  https://rammb2.cira.colostate.edu/wp-content/uploads/2025/06/VIIRS_Fire_Temperature_RGB_Quick_Guide_02052024.pdf
- CIRA Polar SLIDER (sat=jpss; ready-made polar VIIRS products, 6 zoom levels to
  375 m): https://rammb-slider.cira.colostate.edu/?sat=jpss ; SLIDER-cli (verbatim
  polar tile + latest_times URL templates, jpss---<sector>):
  https://github.com/colinmcintosh/SLIDER-cli
- NASA GIBS access basics (WMTS REST + subdaily TIME=YYYY-MM-DDTHH:MM:SSZ + granule
  PT6M): https://nasa-gibs.github.io/gibs-api-docs/access-basics/ ;
  GetCapabilities:
  https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/wmts.cgi?request=GetCapabilities
- NASA GIBS VIIRS layer naming (CorrectedReflectance TrueColor + BandsM11-I2-I1 +
  Thermal_Anomalies_375m, _Granule PT6M variants):
  https://github.com/nasa-gibs/gibs-ml/blob/master/gibs_layer.py
- NOAA JPSS on AWS (Registry of Open Data; buckets noaa-nesdis-snpp/n20/n21-pds,
  anonymous): https://registry.opendata.aws/noaa-jpss/
- VIIRS L1b reader + swath resampling (Polar2Grid EWA; why swath needs geolocation
  + resample): https://www.ssec.wisc.edu/software/polar2grid/readers/viirs_l1b.html
  ; VNP03/VJ103 geolocation:
  https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/VNP03IMG/
- VIIRS polar overpass cadence (sun-synchronous ~13:30 ascending; 3-4 daytime
  passes/day each, all three within ~1 h):
  https://usradioguy.com/modis-viirs-global-coverage/ ; FIRMS VIIRS hotspots
  (SNPP / N20 / N21, VNP14IMGTDL_NRT / VJ114 / VJ214):
  https://firms.modaps.eosdis.nasa.gov/descriptions/FIRMS_VIIRS_Firehotspots.html
- Santa Rosa Island Fire incident (2026-05-15 to 2026-06-04, 18,379 ac, ~33.58 N
  120.06 W, largest Channel Islands fire on record):
  https://en.wikipedia.org/wiki/Santa_Rosa_Island_Fire ; InciWeb CACNP:
  https://inciweb.wildfire.gov/incident-information/cacnp-santa-rosa-island-fire ;
  CAL FIRE:
  https://www.fire.ca.gov/incidents/2026/5/15/santa-rosa-island-fire ; NASA Earth
  Observatory:
  https://science.nasa.gov/earth/earth-observatory/fires-footprint-on-santa-rosa-island/
- GRACE-2 reuse seams (clone targets): reports/design/demo_spike_goes_fire_
  animation.md Demo 1 (fetch_wfigs_incident, FIRMS historical-date positional,
  SLIDER tile-stitch + reproject, postprocess_flood frame seam,
  model_goes_fire_animation); fetch_goes_satellite.py (_list_keys_for_prefix,
  _reproject_and_clip, COG write); workflows/postprocess_flood.py (frame manifest
  + scrubber + MAX_FLOOD_FRAMES); fetch_firms_active_fire.py;
  fetch_nifc_fire_perimeters.py; publish_layer.py (multiband-RGB passthrough);
  ogc_adapter.py (the WMTS GetTile stub OQ-47-WMTS-DIALECT).
