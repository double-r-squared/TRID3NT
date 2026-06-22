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
