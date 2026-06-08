# Audit: AtomicToolMetadata amendment — `supports_global_query` + `estimate_payload_mb`

**Job ID:** job-0114-schema-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** schema

**Required reads:**
- `packages/contracts/src/grace2_contracts/tool_registry.py` (AtomicToolMetadata)
- `services/agent/src/grace2_agent/tools/__init__.py` (registration decorator wiring)
- Memory: `feedback_layer_global_bbox_policy.md` + `feedback_large_payload_chat_warning.md`

### Scope

EDIT `packages/contracts/src/grace2_contracts/tool_registry.py`:

Add TWO new fields to `AtomicToolMetadata`:

```python
class AtomicToolMetadata(GraceModel):
    # ... existing fields ...

    supports_global_query: bool = False
    """True if this tool accepts bbox=None to mean global/CONUS-wide query.
    Default False (safer — tools opt in). When False, calling with bbox=None
    must raise ToolInputError(code="BBOX_REQUIRED")."""

    payload_mb_estimator_name: str | None = None
    """Optional reference to a callable in the tool module's `__init__` that
    estimates expected payload MB given the tool's args. The callable signature
    is `estimate_payload_mb(**args) -> float`. Wave 2 chat-warning system reads
    this metadata to decide when to gate large fetches."""
```

EDIT `services/agent/src/grace2_agent/tools/__init__.py` `@register_tool` decorator:
- Accept the new fields as kwargs
- Plumb through to AtomicToolMetadata instantiation
- Backward-compatible: existing tools without these kwargs default to `supports_global_query=False`, `payload_mb_estimator_name=None`

ADD shared base typed error in `packages/contracts/src/grace2_contracts/errors.py` (if it doesn't exist, create it):
```python
class ToolInputError(GraceModel):
    code: Literal["BBOX_REQUIRED", "INVALID_ARG", "BAD_FORMAT"]
    message: str
    retryable: Literal[False] = False  # input errors are never retryable
```

**Tests** (in packages/contracts/tests/):
- AtomicToolMetadata round-trip with new fields
- Default values: supports_global_query=False, payload_mb_estimator_name=None
- Existing tools without new fields still register cleanly

### File ownership (exclusive)

- `packages/contracts/src/grace2_contracts/tool_registry.py` — amend (2 fields)
- `packages/contracts/src/grace2_contracts/errors.py` — may CREATE if missing
- `packages/contracts/src/grace2_contracts/__init__.py` — append exports (idempotent w/ siblings)
- `packages/contracts/tests/test_tool_registry.py` (or test_metadata) — additive tests
- `services/agent/src/grace2_agent/tools/__init__.py` — extend `@register_tool` decorator signature
- `reports/inflight/job-0114-schema-20260608/`

### FROZEN

- Every individual tool's .py file (they don't need to change — they'll start using the new metadata fields in Wave 1.5 sibling jobs)
- All `workflows/*`, `services/workers/`, `web/`, `infra/`, `docs/srs/`
- `reports/complete/**`


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

