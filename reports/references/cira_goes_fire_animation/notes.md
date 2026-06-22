# Reference: CIRA GOES-18 GeoColor + Fire Temperature animation - Iron Fire (Eureka, Utah)

Source: CIRA / cira_csu (Cooperative Institute for Research in the Atmosphere,
Colorado State University, RAMMB), Instagram post dated 2026-06-22. Archived
2026-06-22 (NATE) as a primary practitioner reference for the GRACE-2 fire-monitoring
demo branch. This is the source animation the demo spike
(reports/design/demo_spike_goes_fire_animation.md) recreates. Sibling archives:
reports/references/lecture_aws_swan_making_waves/notes.md (coastal-wave pipeline) and
reports/references/lecture_baird_coastal/.

## The source post (verbatim caption + correction)

Caption: "The Iron Fire burns near Eureka, Utah, with the Hastings Fire to its
northwest, while the Kane Springs and Grapevine Fires burn in eastern Nevada.
Details: This animation contains the GeoColor and Fire Temperature products from the
GOES-18 weather satellite. It spans the six-and-a-half-hour period from 19:26 UTC to
20:01 UTC on 2026-06-22."

Correction: the "19:26 UTC to 20:01 UTC" end stamps are an internal copy typo (that
span is only 35 minutes, not six and a half hours). Treat the stated ~6.5h DURATION
as authoritative: the window is roughly 13:30 UTC to 20:01 UTC on 2026-06-22. At the
GOES-18 CONUS 5-minute cadence that is about 78 frames.

## The fires named (authoritative locations, live-verified via NIFC/WFIGS 2026-06-22)

- Iron Fire - near Eureka, Utah (Juab County). InitialLat/Lon 39.96976, -112.16481.
  Discovered 2026-06-20. ~21935 acres. Eureka UT is in Juab County = the Iron Fire,
  exactly matching the caption.
- Hastings Fire - Tooele County, Utah (northwest of the Iron Fire). 40.715, -112.946.
  Discovered 2026-06-20. ~6000 acres.
- Grapevine Fire - Lincoln County, eastern Nevada. 37.381, -114.305. Discovered
  2026-06-17. ~13196 acres.
- Kane Springs Fire - Lincoln County, eastern Nevada. 37.284, -114.630. Discovered
  2026-06-17. ~14359 acres.

AOI bboxes (west, south, east, north): Utah pair (Iron + Hastings) = -113.346,
39.57, -111.765, 41.115 ; all four (UT + NV) = -115.13, 36.784, -111.665, 41.215.
All inside the GOES-18 (GOES-West) CONUS sector.

## The products

- GeoColor (CIRA) - a pseudo-true-color day product (Rayleigh-corrected ABI bands +
  a SIMULATED green channel from a Himawari-trained lookup table, since ABI has no
  native green band) that switches at night to a multispectral IR cloud product over
  a static VIIRS city-lights background, blended along the solar terminator. It is a
  proprietary CIRA algorithm; practitioners and this demo source it READY-MADE (CIRA
  SLIDER product=geocolor, or NASA GIBS GOES-West GeoColor WMTS) rather than
  rebuilding it. The 19-20Z window is daytime over UT/NV.
- Fire Temperature RGB (CIRA / NOAA-NESDIS standard) - a 3-channel composite that
  makes active fire pop. RED = ABI Band 7 (3.9um) brightness temperature, stretch
  0-60 C; GREEN = ABI Band 6 (2.2um) reflectance, 0-100 %; BLUE = ABI Band 5 (1.6um)
  reflectance, 0-75 %; gamma 1, no inversion. Cool/small fires show only in 3.9um
  (deep red); as a fire gets hotter the 2.2 then 1.6um channels light up, so it reads
  red -> yellow -> white for the hottest/saturated fires. The genuinely valuable,
  tractable fire-specific product.

## The data path (how this animation is actually made)

The CIRA animation is literally built from CIRA/RAMMB SLIDER pre-rendered tiles -
GeoColor and Fire Temperature are already composited + georeferenced-by-fixed-grid by
CIRA, served as keyless HTTPS PNG tiles with a JSON time index. The same imagery is
available as raw bands from the NOAA GOES-18 ABI-L2 buckets on AWS (anonymous S3),
from which Fire Temperature can be re-composited with full control via the band
recipe above (GeoColor cannot, practically - hence ready-made).

## The practitioner pipeline tiers (spectral imagery product -> RGB -> animation)

| Pipeline tier | This animation | GRACE-2 status | Role for us |
|---|---|---|---|
| Raw spectral imagery (ABI L1b/L2 bands) | GOES-18 ABI-L2-MCMIPC CONUS, 5-min, all 16 CMI bands on noaa-goes18 S3 | HAVE-needs-extension (fetch_goes_satellite reads MCMIPC + reprojects to COG, but only the most-recent frame and only 3 single bands) | The data spine; extend it to a time window + multi-band composite |
| Single-band products | visible (C02), ir_window (C13), water_vapor (C08) | HAVE (fetch_goes_satellite single-band COGs) | Existing GOES single-band layers; not what this demo needs |
| Fire Temperature RGB | C07/C06/C05 composite (R 0-60 C / G 0-100 % / B 0-75 %) | GAP (no Fire Temperature product) | NEW 3-band RGB COG, or pull ready-made from SLIDER |
| GeoColor RGB | CIRA day/night composite | GAP (no GeoColor product) | Pull READY-MADE from SLIDER or NASA GIBS WMTS; do not hand-build |
| Ready-made georeferenced tiles | CIRA/RAMMB SLIDER GeoColor + Fire Temperature tiles | CANDIDATE (new fetch_goes_animation PATH A) | Easiest path; stitch tile grid -> reproject fixed-grid -> EPSG:4326 COG |
| Active-fire detection overlay | VIIRS/MODIS hot pixels co-registered with the imagery | HAVE (fetch_firms_active_fire; GRACE2_FIRMS_MAP_KEY wired) - needs a historical-date positional | Vector hot-pixel overlay; cross-check the animation |
| Named-incident location | Iron / Hastings / Kane Springs / Grapevine -> point + discovery time | GAP (news-ingest geocodes a location but no named-incident lookup) | NEW fetch_wfigs_incident against NIFC/WFIGS FeatureServer |
| Fire perimeter overlay | active perimeter polygons | HAVE (fetch_nifc_fire_perimeters) | Static shape-aware bbox + overlay |
| News -> AOI + time anchor | "fires near Eureka, Utah" -> bbox | HAVE (model_news_event_ingest, target_event_type='wildfire') - needs a time-window derivation | The "pull news" front half |
| Animation (frames -> scrubber) | a ~6.5h, ~78-frame loop per product | HAVE-reuse (postprocess_flood frame emission + SequenceScrubber/AnimationController/detectSequentialGroups web stack) | Emit per-frame COGs in the flood-frame shape -> the existing scrubber animates them, zero web changes |

## Roadmap implications (for the demo spike)

1. This is a BUILD-mostly-reuse demo: the animation web stack, the news-ingest front
   half, and the GOES S3 + reproject + COG primitives all already exist. The new code
   is one extended/new GOES fetcher (time window + Fire Temperature / GeoColor), one
   small named-incident lookup, and one workflow.
2. Two data-path choices: PATH A (ready-made CIRA SLIDER tiles for BOTH products,
   reproject the fixed-grid to EPSG:4326) is the closest to how the source animation
   is actually made and the recommended first cut; PATH B (re-composite Fire
   Temperature from raw noaa-goes18 bands) gives full control. GeoColor is always
   ready-made (proprietary CIRA algorithm).
3. The TIME WINDOW + cadence is a USER lever (the granularity / confirm pattern):
   default ~6.5h / CONUS 5-min (~78 frames), with the WFIGS fire-discovery time as a
   sanity floor (do not request GOES frames before the fire ignited).
4. This is the fire MONITORING lane (satellite imagery + active-fire detections),
   complementary to and not blocked on a fire-spread solver (QUIC-Fire is deferred).

Relates to: reports/design/demo_spike_goes_fire_animation.md,
[[project_credential_card_ssm_vault]] (FIRMS key),
[[project_timeseries_animation_and_overlay_layout]] (the scrubber seam),
[[feedback_research_real_pipelines_first]],
[[reference_engine_cloud_ai_drivability_ranking]] (this is a DATA demo, no Batch).
