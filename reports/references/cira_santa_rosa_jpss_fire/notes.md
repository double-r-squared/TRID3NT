# Reference: CIRA JPSS / VIIRS Day Fire animation - Santa Rosa Island Fire (Channel Islands)

Source: CIRA / cira_csu (Cooperative Institute for Research in the Atmosphere,
Colorado State University, RAMMB), Instagram post dated 2026-05-19. Archived
2026-06-22 (NATE) as a primary practitioner reference for the GRACE-2
fire-monitoring demo branch. This is the source animation that "Demo 2" of the
spike (reports/design/demo_spike_goes_fire_animation.md) recreates. It is the
SECOND fire demo, deliberately exercising POLAR VIIRS endpoints distinct from the
GEOSTATIONARY GOES sibling. Sibling archives:
reports/references/cira_goes_fire_animation/notes.md (the GOES-18 GeoColor + Fire
Temperature demo) and reports/references/lecture_aws_swan_making_waves/notes.md
(coastal-wave pipeline).

## The source post (verbatim caption)

Caption: "A dangerous wildfire has been burning on Santa Rosa island over the last
few days, having engulfing a large section of the island in flames. The wildfire
is currently around 17,000 acres in size and is the largest California wildfire so
far in 2026. Details: This animation contains the Day Fire product from the JPSS
weather satellites. It spans the four-day period from 20:47 UTC on 2026-05-15 to
22:01 UTC on 2026-05-19."

The still shows the Channel Islands (San Miguel, Santa Rosa, Santa Cruz) with a
fire-perimeter polygon and bright-red Day Fire hot pixels on Santa Rosa Island.

Note: unlike the GOES sibling post, the time stamps here are internally consistent
(a true four-day window, 20:47Z 05-15 to 22:01Z 05-19) - no copy typo to correct.
The 20:47Z and 22:01Z stamps are the bracketing polar passes, consistent with the
~19-23Z daytime overpass cluster each day.

## The fire (authoritative facts)

- "Santa Rosa Island Fire" - Santa Rosa Island, Channel Islands National Park,
  Santa Barbara County, CA. InciWeb id is the CACNP-prefixed Channel Islands
  National Park id (CACNP = Channel Islands National Park).
- Started 2026-05-15 (boat-grounding flare ignition, human-caused); fully
  contained 2026-06-04.
- Peak ~18,379 acres (about one third of the 53,195-acre island). The CIRA post
  was mid-event (2026-05-19), reporting ~17,000 acres at that point.
- Largest California wildfire of 2026 and the largest ever recorded on the Channel
  Islands.
- Island center ~33.58 N, -120.06 W. Offshore island terrain.

AOI bboxes (west, south, east, north): Santa Rosa Island = -120.25, 33.88,
-119.95, 34.05 ; all three western Channel Islands (San Miguel + Santa Rosa +
Santa Cruz, to match the still) = -120.50, 33.85, -119.50, 34.10.

Resolve the incident BY NAME (IncidentName='Santa Rosa Island'), not by a CONUS
county - POOState / POOCounty fields can be sparse for an offshore island incident.
The AOI is OUTSIDE the GOES-18 CONUS sector's good-geometry zone offshore but well
inside every VIIRS swath - part of why VIIRS (polar) is the right instrument here,
not GOES (geostationary).

## The satellites + instrument (the new endpoint family)

JPSS = the Joint Polar Satellite System: SUOMI-NPP, NOAA-20 (JPSS-1), NOAA-21
(JPSS-2). These are sun-synchronous LOW EARTH ORBIT (~101 min orbit), all crossing
the equator ~13:30 local solar time ascending (daytime) / ~01:30 descending
(night), all three within ~1 hour of each other. The imaging instrument is VIIRS
(Visible Infrared Imaging Radiometer Suite), at I-band 375 m resolution - the
selling point vs GOES ABI's ~2 km: 375 m fire detail.

This is the entire point of the second demo: POLAR, not geostationary. A given AOI
is NOT imaged continuously; it is sampled a handful of times per day (each
satellite ~3-4 daytime passes/day; across all three ~9-12 daytime overpasses/day
that see the Channel Islands, some oblique / edge-of-swath). So the animation is a
SPARSE, IRREGULARLY-SPACED time series, NOT a smooth 5-min loop. Over the 4-day
window that is roughly 36-48 daytime frames.

## The product

VIIRS "Day Fire" RGB (a.k.a. "Day Land Cloud Fire RGB" / "Natural Fire Color RGB";
CIRA Quick Guide). It is a thermal-red fire layer over a near-true-color
land/veg/smoke base. Recipe (confirmed from the CIRA / RAMMB Quick Guide):

- RED   = VIIRS 3.7um band (I04 at 375 m, or M13 at 750 m) BRIGHTNESS TEMPERATURE,
          stretch 0 to 60 C (273.15 to 333.15 K), gamma 0.4, NOT inverted.
- GREEN = VIIRS 0.86um band (I02 at 375 m, or M07 at 750 m) REFLECTANCE, stretch
          0 to 100 %, gamma 1.0.
- BLUE  = VIIRS 0.64um band (I01 at 375 m, or M05 at 750 m) REFLECTANCE, stretch
          0 to 100 %, gamma 1.0.
- Per-channel clip to [0,1] after the linear stretch + gamma.

Interpretation (matches the still): active fire = red; new burn scar =
reddish-brown; smoke = blue; clouds = cyan; healthy vegetation = green; bare /
old-burn / urban = brown; water / night = near-black. Over the Channel Islands the
ocean reads near-black, so the island + fire pop against a dark sea - exactly the
still.

This is a DIFFERENT product from BOTH the GOES Fire Temperature RGB AND the VIIRS
Fire Temperature RGB. All three put a 3.x um thermal channel in RED, but the Fire
Temperature variants use SWIR for GREEN/BLUE (fire reads red -> yellow -> white by
intensity, no green land). The Day Fire RGB uses NIR (0.86um) + visible (0.64um)
for GREEN/BLUE, so land is GREEN and smoke is BLUE. The still's green land + blue
smoke confirm it is the Day Fire recipe, not Fire Temperature.

Known artifact (not a bug): the 3.7um channel saturates ~368 K (~95 C); very
intense fire cores (~500 K) "fold over" and read COLD, so some core fire pixels
appear blue/cyan instead of red. Expect this inside the hottest Santa Rosa pixels.

Day Fire is a DAYTIME product (GREEN/BLUE are reflectance -> black at night), so
night/descending passes are dropped; night fire monitoring is the separate Active
Fire / Fire Temperature / DNB lane. Keeping the demo day-only matches the caption.

## The data path (how this animation is actually made)

The CIRA animation is built from CIRA / RAMMB Polar SLIDER pre-rendered tiles
(sat=jpss) - the Day Fire RGB is already composited + georeferenced-by-fixed-grid
by CIRA, served as keyless HTTPS PNG tiles with a JSON time index, where each
<YYYYMMDDHHMMSS> directory is one polar pass. The same imagery is available as raw
bands from the NOAA JPSS buckets on AWS (anonymous S3, noaa-nesdis-snpp/n20/n21-
pds), from which the Day Fire RGB can be re-composited with full 375 m I-band
control via the recipe above - but raw VIIRS granules are SWATH (curved scan,
bowtie overlap) and need geolocation + EWA resampling to a grid first, a cost the
ready-made SLIDER tiles (pre-gridded) and NASA GIBS WMTS (already EPSG:4326/3857)
both avoid. GIBS does NOT publish the Day Fire composite (its corrected-reflectance
layers have no 3.7um thermal band), so GIBS is used for pass-time enumeration + a
veg/burn-scar base + the Thermal_Anomalies hot-pixel overlay, NOT for the
thermal-red imagery.

## The practitioner pipeline tiers (polar imagery product -> RGB -> animation)

| Pipeline tier | This animation | GRACE-2 status | Role for us |
|---|---|---|---|
| Raw polar spectral imagery (VIIRS L1b / SDR bands) | VIIRS I04 (3.7um) + I02 (0.86um) + I01 (0.64um) at 375 m on noaa-nesdis-*-pds S3 | GAP (no VIIRS/JPSS imagery handling exists; only FIRMS hot-pixel vectors) | PATH B data spine; only if 375 m I-band control beyond SLIDER is needed (pays the swath-resampling cost) |
| Day Fire RGB | 3.7um BT (RED) + 0.86um NIR refl (GREEN) + 0.64um visible refl (BLUE) | GAP (NEW band set vs GOES C07/C06/C05) | NEW 3-band RGB COG via fetch_viirs_day_fire, or pull ready-made from SLIDER |
| Ready-made georeferenced polar tiles | CIRA Polar SLIDER jpss Day Fire tiles (each pass = one dir) | CANDIDATE (new fetch_viirs_day_fire PATH A) | Easiest path; stitch tile grid -> reproject fixed SLIDER grid -> EPSG:4326 COG; no swath resampling |
| GIBS / Worldview VIIRS WMTS | per-pass granule layers (PT6M) + M11-I2-I1 burn-scar + Thermal_Anomalies_375m | GAP (WMTS GetTile-with-TIME unimplemented, ogc_adapter.py OQ-47-WMTS-DIALECT) | Cleanest pass-time enumeration + georeferenced base + hot-pixel overlay; CANNOT make the true Day Fire RGB (no 3.7um) |
| Multi-day polar-pass frame assembly | ~36-48 irregular daytime overpasses across SNPP + NOAA-20 + NOAA-21 over 4 days | GAP (GOES frame-list is geostationary 5-min / most-recent) | NEW: enumerate + merge + sort multi-satellite overpass timestamps, keep day-only |
| Active-fire detection overlay | VIIRS/MODIS hot pixels co-registered with the imagery (SAME VIIRS instrument) | HAVE (fetch_firms_active_fire; GRACE2_FIRMS_MAP_KEY wired; speaks VIIRS_SNPP_NRT + VIIRS_NOAA20_NRT) - needs the historical-date positional | Vector hot-pixel overlay; co-registers tightly with the Day Fire red pixels (same instrument) - the demo cross-check |
| Named-incident location | "Santa Rosa Island" -> point + discovery time | GAP-shared-with-GOES (fetch_wfigs_incident, reused verbatim; resolve by name, offshore island) | The Demo 1 fetch_wfigs_incident, only the query name + AOI change |
| Fire perimeter overlay | the Channel Islands perimeter polygon in the still | HAVE (fetch_nifc_fire_perimeters) | Static shape-aware overlay matching the still |
| News -> AOI + time anchor | "Santa Rosa Island fire in the Channel Islands" -> bbox + 4-day window | HAVE-reuse (model_news_event_ingest, target_event_type='wildfire') + the Demo 1 window step | The "pull news" front half |
| Animation (frames -> scrubber) | a 4-day, ~36-48-frame sparse polar loop | HAVE-reuse (postprocess_flood frame emission + SequenceScrubber / AnimationController / detectSequentialGroups web stack) | Emit per-pass COGs in the flood-frame shape, each labelled real UTC + satellite -> the existing scrubber animates them, zero web changes |

## Map to the GRACE-2 stack

The fire branch generalizes across satellite families with almost no new code
outside one fetcher. Everything UPSTREAM of the imagery fetcher
(fetch_wfigs_incident, the news -> window step, the offshore AOI bbox) and
everything DOWNSTREAM of it (the postprocess_flood-shaped frame emission + the
scrubber group + the bbox/window review gate + the FIRMS hot-pixel overlay + the
NIFC perimeter overlay + the publish_layer multiband-RGB passthrough + TiTiler) is
the SAME as the GOES demo. The ONLY net-new bytes are a single new atomic fetcher,
fetch_viirs_day_fire.py, plus the multi-satellite irregular-overpass frame
enumerator inside it.

The recommendation is to GENERALIZE THE WORKFLOW, NOT THE FETCHER: build a separate
fetch_viirs_day_fire.py (sibling to the GOES fetch_goes_animation.py) and have ONE
shared animation workflow (model_satellite_fire_animation, generalized from
model_goes_fire_animation) dispatch whichever fetcher matches the requested imagery
source. The GOES and VIIRS fetchers share almost no body (geostationary netCDF +
fixed grid + 5-min cadence + most-recent picker vs multi-satellite irregular polar
overpasses + swath/sinusoidal geolocation or GIBS WMTS-with-TIME), so a single
fetcher would be two disjoint implementations behind a source switch - two tools
wearing one name, which hurts tool-selection clarity. What SHOULD be shared lives
in the workflow layer (already does) plus the _reproject_and_clip rasterio warp +
COG core (lift to a small helper); each fetcher owns its source-specific
geolocation.

Roadmap implications:
1. BUILD-mostly-reuse, like the GOES demo: the animation web stack, the
   news-ingest front half, the overlays, and the publish path all already exist.
   The new code is one VIIRS imagery fetcher + its polar-overpass enumerator and a
   small generalization of the animation workflow.
2. Data-path choices: PATH A (ready-made CIRA Polar SLIDER jpss tiles, reproject
   the fixed grid to EPSG:4326) is closest to how the source animation is made and
   the recommended first cut; PATH B (re-composite the Day Fire RGB from raw
   noaa-nesdis-*-pds VIIRS L1b bands) gives 375 m I-band control but pays the
   swath-resampling cost. GIBS is for pass-time enumeration + overlay, not the
   thermal-red imagery.
3. The TIME WINDOW + which satellites is a USER lever (the granularity / confirm
   pattern): default the full 4-day window, all three satellites, DAY-only passes
   (~36-48 frames), with the WFIGS FireDiscoveryDateTime (2026-05-15) as a sanity
   floor. The scrubber must carry each frame's real irregular UTC pass time + the
   satellite name (uneven spacing, unlike the GOES even 5-min cadence).
4. This is the fire MONITORING lane (satellite imagery + active-fire detections),
   complementary to and not blocked on a fire-spread solver (QUIC-Fire is
   deferred).

Relates to: reports/design/demo_spike_goes_fire_animation.md (Demo 2 section),
reports/references/cira_goes_fire_animation/notes.md (the GOES sibling),
[[project_credential_card_ssm_vault]] (FIRMS key),
[[project_timeseries_animation_and_overlay_layout]] (the scrubber seam),
[[feedback_research_real_pipelines_first]],
[[reference_engine_cloud_ai_drivability_ranking]] (this is a DATA demo, no Batch).
