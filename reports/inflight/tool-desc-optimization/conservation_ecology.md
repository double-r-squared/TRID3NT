# Tool-description optimization -- conservation_ecology (9 tools)

**Branch:** `agent/render-honesty-audit`. Docstrings + type annotations only; no logic/return changes.
Same standard + mechanism as `hazard_modeling.md`.

## Verification
All 9: routing block within first 1000 chars; ASCII-clean; no GCP-infra in first-1000; default-in-Literal
clean; `py_compile` clean. Every Do-NOT sibling verified registered. Full pytest left for integration.

## Literal lifts (verified; defaults in-set)
- `fetch_inaturalist_observations.quality_grade` -> research/needs_id/casual/any (vs `_VALID_QUALITY_GRADES`).
- `fetch_mobi.layer` -> species_richness / species_richness_vertebrates / species_richness_plants /
  range_size_rarity / protection_weighted_rsr (vs `MOBI_LAYERS`).
Existing kept: `fetch_movebank_tracks.geometry_type` (linestring/point). Left `str`: gbif taxonKey/name,
ebird species_code, iucn region, wdpa designation_filter (open vocab); home-range isopleths/trajectory
fields are numeric/open.

## Notes
Honesty floors preserved (gbif wrong-taxon guard, iNat coord-obfuscation, iucn PLACEHOLDER geometry,
home_range TOO_FEW_POINTS, trajectory NO_TIMESTAMP_FIELD -- never fabricated output). Occurrence trio
(gbif/inat/ebird) + range (iucn) + tracks->KDE/trajectory feeders disambiguated. GCP run-cache purged;
public GBIF/iNaturalist/eBird/IUCN/Movebank/WDPA/NatureServe(PC) sources kept.
