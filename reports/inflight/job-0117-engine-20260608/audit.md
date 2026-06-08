# Audit: Florida panther taxon key correction + GBIF + iNat fixture demos

**Job ID:** job-0117-engine-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_gbif_occurrences.py` (job-0087)
- OQ-0087-PANTHER-TAXON-KEY from Wave 1 closeout

### Scope (small mechanical fix — could be Sonnet but the geographic correctness gate keeps it on Opus)

Per OQ-0087-PANTHER-TAXON-KEY surfaced in Wave 1: kickoff specified taxonKey 7193927 for Florida panther but that resolves to *Puma concolor concolor* (broader cougar, ~310 records globally, NONE in Florida). Florida panther is *Puma concolor* with taxonKey **2435099** (~250 records in Big Cypress bbox).

**Updates**:

1. Update the live-test fixture in `services/agent/tests/test_fetch_gbif_occurrences.py` — change 7193927 → 2435099 with a docstring comment noting the OQ-0087-PANTHER-TAXON-KEY resolution
2. Add a SECOND live test using a scientific-name string: `fetch_gbif_occurrences(species_key="Puma concolor", bbox=big_cypress)` exercises the name-resolution path AND confirms it resolves to the right Florida-panther taxonKey
3. Add a NEW file `services/agent/src/grace2_agent/tools/_species_reference.py` (NEW) — small dict mapping common name → preferred taxonKey:
   ```python
   FLORIDA_DEMO_SPECIES = {
       "florida_panther": {"gbif_taxon_key": 2435099, "scientific_name": "Puma concolor", "common": "Florida panther"},
       "american_alligator": {"gbif_taxon_key": 2436873, "scientific_name": "Alligator mississippiensis", "common": "American alligator"},
       "roseate_spoonbill": {"gbif_taxon_key": 2481008, "scientific_name": "Platalea ajaja", "common": "Roseate spoonbill"},
       "manatee": {"gbif_taxon_key": 2440777, "scientific_name": "Trichechus manatus", "common": "West Indian manatee"},
   }
   ```
4. Live verification: re-run job-0087's live test with the corrected key → confirm ≥1 feature in Big Cypress bbox

**Tests** (≥3 unit + 2 live):
- _species_reference dict structure validation
- GBIF name resolution: "Puma concolor" via species/match → taxonKey 2435099
- Live: fetch_gbif_occurrences(2435099, big_cypress_bbox) returns ≥1 feature
- Live: fetch_gbif_occurrences("Puma concolor", big_cypress_bbox) returns ≥1 feature (same data)

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/_species_reference.py` (NEW — small reference)
- `services/agent/tests/test_fetch_gbif_occurrences.py` — additive tests, taxon key correction (extend, don't replace)
- `services/agent/tests/test_species_reference.py` (NEW)
- `reports/inflight/job-0117-engine-20260608/`

### FROZEN

- `services/agent/src/grace2_agent/tools/fetch_gbif_occurrences.py` (don't modify; tool API is fine; only fixture data was wrong)
- All other tools/*
- All workflows/, web/, infra/, docs/srs/, reports/complete/**


### FROZEN

All other `tools/*` (each Wave 1.5 sibling owns one); all `workflows/`, `services/workers/`, `web/`, `infra/`, `docs/srs/`, `styles/`, `reports/complete/**`. For schema/agent jobs, FROZEN is the inverse of their declared file ownership.

### Concurrency note (Wave 1.5 fan-out — 16 parallel)

~16 Wave 1.5 jobs in parallel. Idempotent-append works for `tools/__init__.py` + `main.py` + `packages/contracts/__init__.py` but Wave 1 produced 3 commit-label-swap patterns under load. **Required mitigation**: before `git commit`, run `git pull --rebase=true origin main 2>/dev/null || git stash && git pull --rebase && git stash pop` to handle sibling concurrent landings cleanly. If conflict on registration site, re-apply your import line.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: if your tool emits geometry, verify against actual geography (river mouth where it should be, not just bbox/URL consistency). Every fetcher's live test must check that emitted features fall inside requested bbox AND match the named place's actual outline if applicable.

2. **Kickoff-front-loaded design**: orchestrator did the design — execute, don't redesign. Surface OQs in your report rather than expanding scope.

### Acceptance criteria

- [ ] New tool/contract registered + visible at appropriate test surface
- [ ] ≥4 unit tests + ≥1 live test (env-guarded if external)
- [ ] Geographic-correctness check where applicable
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

