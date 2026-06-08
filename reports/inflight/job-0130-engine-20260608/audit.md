# Audit: `fetch_movebank_tracks` atomic tool

**Job ID:** job-0130-engine-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_gbif_occurrences.py` (Wave 1 pattern)
- `services/agent/src/grace2_agent/tools/cache.py`
- `packages/contracts/src/grace2_contracts/secrets.py` (SecretRecord shape)
- `services/agent/src/grace2_agent/persistence.py` (Persistence class for secret lookup)

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_movebank_tracks.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="movebank",
    supports_global_query=False,
)
def fetch_movebank_tracks(study_id: int, bbox: tuple[float,float,float,float] | None = None, username: str | None = None, password: str | None = None, secret_ref: dict | None = None) -> LayerURI:
    """Movebank animal tracking trajectory Tier-2 fetcher.

    Wraps Movebank REST API (https://www.movebank.org/movebank/service/json/). Returns
    FlatGeobuf linestrings (per-individual tracks) OR points. Requires Movebank credentials."""
```

**Implementation**:
- Endpoint: `https://www.movebank.org/movebank/service/direct-read?entity_type=event&study_id={study_id}` (Basic auth)
- Properties per point: individual_id, timestamp, lat, long, sensor_type
- Aggregate by individual_id to LineString per animal (sorted by timestamp)
- bbox filter: client-side after fetch (Movebank doesn't always support bbox)
- Cache: static-30d (historic tracks immutable)
- Cache key on (study_id, bbox, username)
- supports_global_query=False (study-specific)


**Tier-2 secret handling**: this tool requires a `movebank` API key. Accept via:
- `api_key: str | None = None` parameter (explicit)
- OR `secret_ref: SecretRecord | None = None` (lookup via Persistence.get_secret_value(secret_ref) — Wave 2 sibling job-0124 lands this method)
- Fallback: `os.environ.get("GRACE2_MOVEBANK_API_KEY")` for local dev

Unit tests use mocked HTTP responses (no real key needed); live test (env-gated `GRACE2_TEST_LIVE_MOVEBANK=1` + env var with real key) verifies live response. **Mark live test as `pytest.mark.skipif` based on key availability — do NOT fail unit suite if key is missing.**


**Payload estimation**: estimate_payload_mb: scales with study size; small study ~1MB, large multi-year ~50MB.

**Tests** (≥4 unit + ≥1 live env-guarded):
- Mocked response → grouped LineStrings per individual
- Missing credentials → typed error
- Empty study → 0-feature FlatGeobuf
- Live (env GRACE2_TEST_LIVE_MOVEBANK=1 + GRACE2_MOVEBANK_USER/PASS): public study (e.g. 1259686571 sandhill cranes) → real tracks

**Live verification** (env-guarded): fetch_movebank_tracks(1259686571) → real FlatGeobuf with crane migration tracks; evidence/movebank_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_movebank_tracks.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append)
- `services/agent/tests/test_fetch_movebank_tracks.py` (NEW)
- `reports/inflight/job-0130-engine-20260608/`


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

