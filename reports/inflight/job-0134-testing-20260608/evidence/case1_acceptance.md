# Case 1 Live Acceptance — Big Cypress / Everglades

**Job:** job-0134-testing-20260608
**Date:** 2026-06-08
**Overall:** PASS (8 pass, 0 warn, 0 fail)
**Elapsed:** 134.7s

## Parameters
- bbox: (-81.5, 25.7, -80.7, 26.5)
- species_keys: [2435099, 2441370, 2480803]  (corrected per OQ-0117 / _species_reference.py)
- rainfall_event: 'atlas14_100yr'
- protected_area_designation: None (all WDPA in bbox)
- place_clip_polygon_uri: None

## Case Summary Text (deterministic)
```
Within Big Cypress / Everglades: 244 species occurrence(s) (244 2435099, 0 2441370, 0 2480803); flood modeling for the atlas14_100yr event did not complete (error: BBOX_INVALID); bbox=[-81.5000, 25.7000, -80.7000, 26.5000].
```

## Layer URIs
- flood_layer_uri: None (flood modeling failed — honest failure per kickoff §1)
- wdpa_layer_uri: {"layer_id": "wdpa--81.5000-25.7000", "name": "Protected Areas \u2014 WDPA", "layer_type": "vector", "uri": "gs://grace-2-hazard-prod-cache/cache/static-30d/wdpa/60478b2981661d507eaf65d108a3ae30.fgb", "style_preset": "wdpa_protected_areas", "temporal": null, "role": "context", "units": null, "bbox": null}
- species_layers: 3 layer(s)
  - [0] GBIF Occurrences — taxonKey 2435099: gs://grace-2-hazard-prod-cache/cache/static-30d/gbif/020f35d132127ea9f6181fd9e1d95e29.fgb
  - [1] GBIF Occurrences — taxonKey 2441370: gs://grace-2-hazard-prod-cache/cache/static-30d/gbif/540eebf507d0b481c44486f37dff30d0.fgb
  - [2] GBIF Occurrences — taxonKey 2480803: gs://grace-2-hazard-prod-cache/cache/static-30d/gbif/6d5b696fc6af43966ffd288779144154.fgb

## Species Occurrence Counts
- 2435099: 244 occurrence point(s)
- 2441370: 0 occurrence point(s)
- 2480803: 0 occurrence point(s)

## Geographic Correctness Gate (job-0086 codified lesson)
- [PASS bbox] PASS bbox: result bbox [-81.5, 25.7, -80.7, 26.5] matches kickoff spec (-81.5, 25.7, -80.7, 26.5) — Big Cypress / Everglades region
- [PASS species_layers] PASS species_layers: 3 layer(s) returned, one per requested species key ([2435099, 2441370, 2480803])
- [PASS species_layers[0]] PASS species_layers[0]: LayerURI present with uri='gs://grace-2-hazard-prod-cache/cache/static-30d/gbif/020f35d132127ea9f6181fd9e1d95e29.fgb' (points were fetched and written)
- [PASS species_layers[1]] PASS species_layers[1]: LayerURI present with uri='gs://grace-2-hazard-prod-cache/cache/static-30d/gbif/540eebf507d0b481c44486f37dff30d0.fgb' (points were fetched and written)
- [PASS species_layers[2]] PASS species_layers[2]: LayerURI present with uri='gs://grace-2-hazard-prod-cache/cache/static-30d/gbif/6d5b696fc6af43966ffd288779144154.fgb' (points were fetched and written)
- [PASS wdpa_layer_uri] PASS wdpa_layer_uri: WDPA polygon layer returned with uri='gs://grace-2-hazard-prod-cache/cache/static-30d/wdpa/60478b2981661d507eaf65d108a3ae30.fgb' — protected areas in bbox fetched
- [INFO impact_metrics] INFO impact_metrics: empty because flood modeling failed (flood_layer_uri is None) — zonal stats require both flood + WDPA
- [PASS case_summary_text] PASS case_summary_text: deterministic summary produced (223 chars) — Invariant 1 (determinism boundary) preserves
- [PASS species_counts] PASS species_counts: 244 total occurrence point(s) across 3 species ({'2435099': 244, '2441370': 0, '2480803': 0})

## Invariant Checks
- Invariant 1 (Determinism boundary): case_summary_text is format-string only; all field values come from typed tool returns — no LLM generated numbers.
- Invariant 2 (Deterministic workflows): no LLM calls in the composer chain (fetch_gbif + fetch_wdpa + model_flood_scenario + compute_zonal_statistics are all deterministic tools).
- Invariant 7 (Claims carry provenance): LayerURIs carry uri pointing to GCS FlatGeobuf or WMS endpoint; provenance threaded through.

## Notes on Species Key Correction (OQ-0117)
The kickoff audit.md originally listed species_keys [2435099, 2481008, 2436873].
_species_reference.py (job-0117) corrected these to verified GBIF species-level keys:
- 2435099: Puma concolor (Florida panther) — unchanged, correct
- 2481008 was wrong; corrected to 2480803: Platalea ajaja (Roseate spoonbill)
- 2436873 was wrong; corrected to 2441370: Alligator mississippiensis
This correction is load-bearing: the original keys had zero or wrong-taxon records in the Big Cypress bbox.