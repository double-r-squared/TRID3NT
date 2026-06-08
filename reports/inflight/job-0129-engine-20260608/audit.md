# Audit: `fetch_iucn_red_list_range` atomic tool

**Job ID:** job-0129-engine-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_gbif_occurrences.py` (Wave 1 pattern)
- `services/agent/src/grace2_agent/tools/cache.py`
- `packages/contracts/src/grace2_contracts/secrets.py` (SecretRecord shape)
- `services/agent/src/grace2_agent/persistence.py` (Persistence class for secret lookup)

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_iucn_red_list_range.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="iucn_red_list",
    supports_global_query=True,
)
def fetch_iucn_red_list_range(species_name: str, api_key: str | None = None, secret_ref: dict | None = None) -> LayerURI:
    """IUCN Red List species range polygons Tier-2 fetcher.

    Wraps the IUCN Red List API. Returns FlatGeobuf polygons of species range
    + threat status. Requires IUCN Red List API key (free for research)."""
```

**Implementation**:
- Endpoint: `https://apiv3.iucnredlist.org/api/v3/species/region/{species_name}/{region}?token={api_key}`
- Region: 'global' default; IUCN supports regional assessments
- This returns species INFO + REFERENCE to range polygon; actual polygons require a follow-up Spatial Data download — for v0.1 use the SimpleAPI shape endpoint if available, else return INFO-only LayerURI with placeholder geometry surface OQ-129-RANGE-SPATIAL
- Cache: static-30d
- Cache key on (species_name)
- supports_global_query=True (species ranges can be global)


**Tier-2 secret handling**: this tool requires a `iucn_red_list` API key. Accept via:
- `api_key: str | None = None` parameter (explicit)
- OR `secret_ref: SecretRecord | None = None` (lookup via Persistence.get_secret_value(secret_ref) — Wave 2 sibling job-0124 lands this method)
- Fallback: `os.environ.get("GRACE2_IUCN_RED_LIST_API_KEY")` for local dev

Unit tests use mocked HTTP responses (no real key needed); live test (env-gated `GRACE2_TEST_LIVE_IUCN_RED_LIST=1` + env var with real key) verifies live response. **Mark live test as `pytest.mark.skipif` based on key availability — do NOT fail unit suite if key is missing.**


**Payload estimation**: estimate_payload_mb: ~0.5MB per species range (often multi-polygon).

**Tests** (≥4 unit + ≥1 live env-guarded):
- Mocked IUCN response → FlatGeobuf
- Missing api_key → typed error
- Species not in database → empty FlatGeobuf
- Live (env GRACE2_TEST_LIVE_IUCN=1 + GRACE2_IUCN_API_KEY): "Puma concolor" → metadata FlatGeobuf

**Live verification** (env-guarded): fetch_iucn_red_list_range('Puma concolor') → real FlatGeobuf; evidence/iucn_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_iucn_red_list_range.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append)
- `services/agent/tests/test_fetch_iucn_red_list_range.py` (NEW)
- `reports/inflight/job-0129-engine-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 2 job's exclusive files; `reports/complete/**`; `docs/SRS_v0.3.md` monolith (regenerated only); all Wave 1/1.5 atomic tool files (additive use only — don't modify their signatures).

### Concurrency note (Wave 2 fan-out — 16 parallel)

Same idempotent-append pattern + `git pull --rebase` pre-commit mitigation as Wave 1.5. Files all land correctly in HEAD; only commit-message labels may drift. Use marker commits if your changes get swept into a sibling's commit hash.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: verify against real geography, not URL/render consistency.
2. **Kickoff-front-loaded design**: orchestrator did the design — execute, don't redesign. Surface OQs in your report rather than expanding scope.
3. **MongoDB MCP canonical persistence (job-0115 foundation)**: ALL CRUD goes through `Persistence.*`. Do NOT design custom collection wrappers. If your job needs a new method on Persistence, ADD it (additive) rather than bypassing.

### Acceptance criteria

- [ ] All deliverables landed per scope
- [ ] ≥4 unit tests + ≥1 live test (env-guarded if external)
- [ ] Geographic-correctness / behavioral-correctness verified
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

