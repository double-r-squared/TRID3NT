# Report: AtomicToolMetadata Wave 1.5 amendment + ToolInputError shared error

**Job ID:** job-0114-schema-20260608
**Sprint:** sprint-12-mega Wave 1.5
**Specialist:** schema
**Status:** ready-for-audit

## Summary

Added two pydantic fields to `AtomicToolMetadata` (`supports_global_query: bool = False`, `payload_mb_estimator_name: str | None = None`); created shared typed-error model `ToolInputError` in new `grace2_contracts/errors.py` with closed `BBOX_REQUIRED`/`INVALID_ARG`/`BAD_FORMAT` codes and `retryable: Literal[False]`; extended `@register_tool` decorator with the two flags as optional kwargs (overrides metadata via `model_copy(update=...)`). 17 new contracts tests + 6 new agent decorator tests, all pass; 61 sibling-tool regression tests pass.

## Changes Made

- `packages/contracts/src/grace2_contracts/tool_registry.py`: added `supports_global_query: bool = False` and `payload_mb_estimator_name: str | None = None` Fields to `AtomicToolMetadata`; re-exports `ToolInputError` + codes for convenience.
- `packages/contracts/src/grace2_contracts/errors.py` (NEW): `ToolInputError` GraceModel + `ToolInputErrorCode` Literal + `TOOL_INPUT_ERROR_CODES` tuple.
- `packages/contracts/src/grace2_contracts/__init__.py`: added `errors` to module re-exports and `__all__`.
- `services/agent/src/grace2_agent/tools/__init__.py`: extended `register_tool(metadata, *, supports_global_query=None, payload_mb_estimator_name=None)`; kwargs override metadata via `model_copy(update=...)`.
- `packages/contracts/tests/test_tool_registry.py`: 11 new tests (defaults, opt-in, both-set composition, JSON round-trip, non-bool/non-str rejection, cross-field-rule preservation, LLM-catalog-surface advertisement).
- `packages/contracts/tests/test_errors.py` (NEW): 12 tests covering all three codes, `retryable=False` pin, empty-message rejection, unknown-code rejection, extra-field rejection, JSON round-trip, wire-form shape, re-export identity.
- `services/agent/tests/test_register_tool_wave15_kwargs.py` (NEW): 6 live decorator-kwarg tests with snapshot/restore of the live registry.

## Decisions Made

- Field name `payload_mb_estimator_name: str | None` (callable-name reference resolved at dispatch time), not a `Callable` field — keeps wire serialization trivial.
- `ToolInputError` is a `GraceModel` (typed payload), not a Python `Exception`. Tools wrap it in their own exception type if needed.
- Decorator kwargs win over metadata constructor args (via `model_copy(update=...)`). Both paths are interchangeable; siblings already using the defensive try/except pattern (e.g. fetch_mrms_qpe) work via the metadata path.
- `errors` module is authoritative home; `tool_registry` re-exports for convenience (tests assert class identity).

## Invariants Touched

- **Invariant 1 (Determinism boundary)**: preserved — both new fields are closed-typed (`bool`, `str | None`); `ToolInputError.code` is a closed Literal. No LLM-judged free text.
- **Invariant 9 (No cost theater)**: preserved — `payload_mb_estimator_name` references a size estimator (MB), never a cost ($) estimator. `ToolInputError` has no cost/retry-cost fields.

## Open Questions

- **OQ-0114-CONSENSUS-NAMING**: kickoff header used `estimate_payload_mb` shorthand; body specified `payload_mb_estimator_name: str | None`. Went with the body (callable-name reference). Recommendation: keep current shape; if Wave-2 needs a stronger `Callable[..., float]` contract, follow-up adds a `Field(exclude=True)` callable alongside.
- **OQ-0114-SRS-AMENDMENT**: neither new field is in the SRS yet; both back memory-codified user direction (`feedback_layer_global_bbox_policy`, `feedback_large_payload_chat_warning`). Recommendation: user lands SRS amendment to `docs/srs/03-functional-requirements.md` (FR-AS-3 tool-registration vocabulary) when the Wave-2 chat-warning system has concrete envelope shapes.

## Dependencies and Impacts

- **Depends on**: nothing in Wave 1.5; foundational schema landing.
- **Affects**:
  - 8+ sibling Wave-1.5 fetcher jobs already using defensive try/except for `supports_global_query` — their try-branch now succeeds.
  - Wave-2 chat-warning dispatcher: reads `payload_mb_estimator_name` and resolves to a callable in the tool's module.
  - Wave-2 web: consumes future `tool-payload-warning` envelope.

## Verification

- `pytest packages/contracts/tests/test_tool_registry.py packages/contracts/tests/test_errors.py -v` → **35 passed**.
- `pytest services/agent/tests/test_register_tool_wave15_kwargs.py -v` → **6 passed**.
- `pytest packages/contracts/tests/ -k "not test_every_a3_a4_a4b"` → **214 passed, 1 deselected** (the deselected test fails because a sibling Wave-1.5 schema job added `secret-add`/`secrets-list`/`secret-revoke` envelopes that the factories test hasn't been updated for — pre-existing failure verified by `git stash` reset; not in this job's file ownership).
- Regression: `pytest services/agent/tests/test_fetch_mrms_qpe.py test_fetch_hrsl_population.py test_fetch_firms_active_fire.py test_fetch_gcn250_curve_numbers.py` → **61 passed, 5 skipped** (network-guarded skips identical to pre-amendment).
- **Live invocation** verbatim:
  ```
  supports_global_query default: False
  payload_mb_estimator_name default: None
  with both fields: True estimate_payload_mb
  ToolInputError wire form: {'code': 'BBOX_REQUIRED', 'message': 'bbox is required', 'retryable': False}
  TOOL_INPUT_ERROR_CODES: ('BBOX_REQUIRED', 'INVALID_ARG', 'BAD_FORMAT')
  Registered tools: 32 total, 1 supports_global_query=True, 0 with estimator
    global-ok: fetch_mrms_qpe
  ```
  This confirms the contract change works end-to-end: `fetch_mrms_qpe`'s defensive try/except now takes the try-branch via the real schema field, proving the integration with sibling Wave-1.5 jobs works.

### Schema versioning

Additive only; no version bump. `schema_version` stays `"v1"`. Existing call sites unaffected (safe defaults).
