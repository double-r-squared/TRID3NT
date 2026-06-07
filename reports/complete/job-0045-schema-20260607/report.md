# Report: CatalogEntry + D.11/D.12 + Appendix A envelope extensions (Mode 1 substrate)

**Job ID:** job-0045-schema-20260607
**Sprint:** sprint-08
**Specialist:** schema
**Task:** Land the Mode 1 catalog substrate: rewrite `CatalogEntry` to the §F.1.2 Mode 1 binding shape, add two new MongoDB collections (`catalog_entries` + `catalog_audit_log`), add four new Appendix A envelopes (`recovery-choice` + `recovery-choice-response` per §3.10 FR-FR-1; `offer-catalog-addition` + `catalog-addition-response` per §F.1.2 Mode 2), regenerate JSON Schemas idempotently, edit the narrow SRS files, regenerate the monolith, and ship >=6 new contracts tests.
**Status:** ready-for-audit

## Summary

Rewrote `CatalogEntry` from the v0.1 FR-PHC-2 stub shape (`agency` / `topic` / `coverage` / `format` / `style_preset` / `access` / `title` / ...) to the §F.1.2 Mode 1 binding contract (`id` / `name` / `description` / `urls` / `access_tier` / `credential_tier` / `ttl_class` / `source_class` / `license` / `citation` / `vintage` / `last_verified` / `status` / `how_to_use` / `api_key_secret_ref`) with a `model_validator` enforcing the §F.1 credential-tier consistency rule. Added two new Appendix D collections (numbered **D.11** `catalog_entries` and **D.12** `catalog_audit_log` — see Open Questions on numbering). Added the four new Appendix A envelopes per the kickoff with full pydantic shapes + registry registration. JSON Schema export idempotent; +11 net new tests (142 total, up from 131); `make srs` regenerates cleanly.

## Changes Made

- **`packages/contracts/src/grace2_contracts/catalog.py`** — full rewrite to the §F.1.2 Mode 1 shape. New `Literal`s `AccessTier` (1/2/3/4), `CredentialTier` (1/2/3), `TTLClass` (mirrors `tool_registry.TTLClass`), `EntryStatus` (`active` / `deprecated` / `user_proposed_pending_curator_review`). `CatalogEntry` carries `schema_version: Literal["v1"]` first, then identification + endpoints + tier classification + provenance + lifecycle + actionable-catalog payload + conditional credential ref. `@model_validator(mode="after") _validate_credential_tier_consistency` enforces Tier 1 => no `api_key_secret_ref`; Tier 2/3 => non-empty `api_key_secret_ref`. The old `CatalogFormat` literal + v0.1 stub fields are removed wholesale per pre-MVP scope (no migration shims).
- **`packages/contracts/src/grace2_contracts/collections.py`** — imported `CatalogEntry`, extended `__all__`, appended `CatalogEntryDocument(CatalogEntry)` (D.11; no `_id` alias because entry id is free-form string), `CatalogAuditEventType = Literal["add","update","deprecate","user_proposed","curator_approved","curator_rejected"]`, `CatalogAuditLogDocument(DocModel)` (D.12; ULID `_id` aliased; `entry_id` references CatalogEntry.id; open-`dict` `event_payload`), plus `CATALOG_ENTRIES_INDEXES` and `CATALOG_AUDIT_LOG_INDEXES` declared-constants for infra.
- **`packages/contracts/src/grace2_contracts/ws.py`** — appended four new envelope payloads: `RecoveryChoicePayload` + `RecoveryChoiceResponsePayload` (FR-FR-1) with `RecoveryChoiceOption = Literal["deny","retry","chat"]`; `OfferCatalogAdditionPayload` + `CatalogAdditionResponsePayload` (§F.1.2 Mode 2) with helper `ProbeFindings` and permissive `SuggestedCatalogEntry` sub-models. Registered all four in `CLIENT_TO_AGENT_PAYLOADS` (responses) / `AGENT_TO_CLIENT_PAYLOADS` (requests).
- **`packages/contracts/src/grace2_contracts/export_schemas.py`** — registered `catalog_entry_document` + `catalog_audit_log_document` in `_EXPORTS`. The 4 new WS payloads auto-discover via `ws.ALL_PAYLOADS`.
- **`packages/contracts/tests/test_catalog.py`** — full rewrite. 10 new test functions covering: Mode 1 round-trip idempotence (Tier 1 + Tier 2), credential-tier validator (Tier 1+secret-ref / Tier 2 without / empty string / Tier 3 without all rejected), `CatalogEntryDocument` inheritance + Mongo dump shape + index declarations, `CatalogAuditLogDocument` round-trip + ULID `_id` aliasing + closed-Literal rejection (parametrized over all 6 event types), FR-FR-1 envelope round-trips + empty-options rejection + narrowed-options + chat-text + cancellation, §F.1.2 Mode 2 envelope round-trip + accept-with-edits + reject-with-reason + closed `discovered_via`, payload-registry membership for the 4 new envelopes, JSON Schema export + idempotence, Invariant 9 negative control (4 forbidden cost-fields), `RecoveryChoiceOption` closed-Literal enforcement.
- **`packages/contracts/tests/test_ws.py`** — extended `test_every_a3_a4_a4b_payload_round_trips` minimal_factories with the 4 new payload constructors so the full-inventory assertion keeps passing.
- **`packages/contracts/tests/test_export_schemas.py`** — added two new spot-check entries.
- **`packages/contracts/schemas/`** — regenerated. 6 new files: `catalog_entry_document.json`, `catalog_audit_log_document.json`, `ws_recovery_choice.json`, `ws_recovery_choice_response.json`, `ws_offer_catalog_addition.json`, `ws_catalog_addition_response.json`. `catalog_entry.json` modified (new shape).
- **`docs/srs/A-websocket-protocol.md`** — appended `recovery-choice` + `offer-catalog-addition` at end of A.4, `recovery-choice-response` + `catalog-addition-response` at end of A.4b. Canonical JSON examples + field-by-field explanations (closed Literal constraints, max-length caps, default TTL values, cancellation patterns).
- **`docs/srs/D-mongodb-collection-schemas.md`** — appended D.11 `catalog_entries` + D.12 `catalog_audit_log` after the existing D.10. Each carries pydantic class block, indexes block, TTL notes, lifecycle/payload-shape explanation.
- **`docs/SRS_v0.3.md`** — regenerated by `make srs` (3058 -> 3611 lines; +553 matching the section deltas; lossless concat invariant preserved).

## Decisions Made

- **Decision: rewrite `CatalogEntry` wholesale to the §F.1.2 Mode 1 shape; delete v0.1 stub fields without a shim.**
  - **Rationale:** Pre-MVP per AGENTS.md. The v0.1 stub was never wired to a real catalog file (`public_hazard_catalog.yaml` is NOT YET CREATED per §F.2). No downstream consumers exist. Replacement cleaner than additive coexistence.
  - **Alternatives:** discriminator-versioned shapes (rejected, no v0.1 deployment); additive evolution (rejected, several v0.1 fields don't map cleanly to §F.1.2 Mode 1 axes — `topic`+`coverage` are subsumed by `source_class`; `format` follows from `access_tier`; `style_preset` belongs on the cataloged layer, not the catalog entry).

- **Decision: number the two new collections D.11 + D.12, NOT D.8 + D.9 as the kickoff named.**
  - **Rationale:** Appendix D already uses D.7-D.10 for meta sections (Cross-cutting decisions / Storage sizing / Design rationale / Known open choices). Renaming them to D.13-D.16 to free up D.8/D.9 would churn immutable `reports/complete/` references. The kickoff's D.8/D.9 numbering assumed D.1-D.6 were the only existing sections. Surfaced as Open Question.
  - **Alternatives:** renumber D.7-D.10 (rejected, churn); wedge as D.6.5/D.6.6 (rejected, non-standard).

- **Decision: `CatalogEntryDocument` inherits `CatalogEntry` directly without `_id` alias.**
  - **Rationale:** The entry `id` is a free-form stable string (e.g. `"usgs-3dep-dem-1m"`), not a ULID. The write path can set `_id = doc["id"]` at insert. Aliasing would force every consumer to choose `by_alias=True` vs False for no payoff.
  - **Alternatives:** alias id->_id (rejected, churn); two-id pattern (rejected, harder to reason about).

- **Decision: `CatalogAuditLogDocument` uses ULID `_id` aliasing (D.12, unlike D.11).** Append-only event log without a curator-meaningful id => ULID is natural (time-sortable, mirrors D.3 runs).

- **Decision: `SuggestedCatalogEntry` is a permissive sub-model, NOT a nested `CatalogEntry`.**
  - **Rationale:** §F.1.2 Mode 2 probe drafts may miss fields the strict `CatalogEntry` validator requires (e.g. Tier-2 secret-ref). Wrapping at envelope-emit time would force fabrication of placeholders. Better: draft carries probe-inferred fields permissively, user edits in modal, agent service round-trips accepted draft through full `CatalogEntry` validator at catalog-write time.
  - **Alternatives:** fully-validated nested `CatalogEntry` (rejected per above); raw `dict` (rejected, loses wire-shape documentation + introspection).

- **Decision: rename the offer's `license` field to `license_claim` in `SuggestedCatalogEntry`.**
  - **Rationale:** The SRS §F.1.2 Mode 2 envelope example uses `license` inside the suggested-entry block, but the outer `CatalogEntry` also uses `license`. The `_claim` suffix distinguishes probe observation from curator-attested value. Surfaced as Open Question for SRS amendment.

- **Decision: `RecoveryChoicePayload.options` is `list[RecoveryChoiceOption]` (min 1, max 3), not the bare 3-element tuple from SRS prose.**
  - **Rationale:** FR-FR-2 allows narrowing (`GEOCODE_NO_MATCH` may surface `["chat"]` only). list[Literal] with min/max captures both cases + rejects nonsense.

- **Decision: `recovery-choice-response.chat_text` is permissive (`str | None`, max 4096), NOT strictly required when `choice == "chat"`.**
  - **Rationale:** Mirrors existing A.4b discipline. Strict cross-field at schema level would force ValidationError for a UX bug (user clicked chat then submitted empty). Agent service validates at receipt per FR-AS-11 — the right layer for UX-driven cross-field rules.

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves.** `CatalogEntry.last_verified` is curator-attested, never LLM-judged. `ProbeFindings.access_tier_inferred` is the deterministic probe conclusion. `RecoveryChoicePayload.error_code` is workflow-emitted (FR-FR-2), not LLM-narrated.
- **Invariant 7 (Claims carry provenance): preserves + extends.** `CatalogEntry.license` + `citation` + `vintage` + `last_verified` are required structured fields. `CatalogAuditLogDocument` is the durable trail Decision M requires.
- **Invariant 8 (Cancellation is first-class): preserves.** Both new responses carry `cancelled: bool`. `RecoveryChoiceOption == "deny"` (user-deliberate-deny) is distinct from `cancelled` (modal dismissed without deciding).
- **Invariant 9 (No cost theater): preserves.** No cost fields anywhere — verified by `test_catalog_entry_no_cost_field_invariant9` parametrized over `cost_usd` / `estimated_cost` / `cost_per_call` / `monthly_quota_cost`.

## Open Questions

- **OQ-45-D-NUMBERING (TENTATIVE: D.11 + D.12).** Appendix D already uses D.7-D.10 for meta sections; kickoff named D.8/D.9 but those slots are occupied. Recommendation: keep D.11/D.12 as landed. Route to user.
- **OQ-45-LICENSE-CLAIM-NAMING (TENTATIVE: `license_claim` in SuggestedCatalogEntry).** SRS §F.1.2 Mode 2 sketch uses `license`. Recommendation: amend SRS to `license_claim` to disambiguate from outer `CatalogEntry.license`.
- **OQ-45-CATALOG-ENTRY-ID-ALIAS (TENTATIVE: no alias).** Documented above; revisitable if a consumer wants `_id`-form access.
- **OQ-45-AUDIT-LOG-EVENT-PAYLOAD-CLOSED-SHAPE (TENTATIVE: open dict).** Per-event-type payload shapes still settling; a second-pass schema sprint after Mode 2 ships can introduce a discriminated union.
- **OQ-45-RECOVERY-OPTIONS-ROUTING-IN-SCHEMA (TENTATIVE: agent owns routing).** Schema permissive; agent service enforces FR-FR-2 routing at emit time. Embedding the routing table in the schema would violate Decision G open-enum discipline + duplicate the agent-side table.
- **OQ-45-SCHEMA-VERSION-BUMP (TENTATIVE: keep v1).** New CatalogEntry shape is structurally different from v0.1 stub, but no v0.1 deployment exists. Revisit at first production deployment.
- **OQ-45-FR-FR-2-ERROR-CODES-IN-A6 (TENTATIVE: follow-up job).** FR-FR-2 routing table names new error codes (`UPSTREAM_API_ERROR`, `NETWORK_TIMEOUT`, `GEOCODE_NO_MATCH`, `LULC_MAPPING_MISMATCH`, ...) not yet in A.6 explicit list. A.6 is open per FR-AS-5 prose. Follow-up amendment job for documentation completeness; not blocking.

## Dependencies and Impacts

- **Depends on:** job-0030-schema-20260606 (AtomicToolMetadata pattern; `tool_registry.TTLClass`); job-0013-schema-20260605 (substrate). All intact.
- **Affects (sprint-08 + post):**
  - **engine** (`catalog_search` / `catalog_fetch` tools): reads `CatalogEntry` from D.11; uses `(status, source_class)` compound index for "active-only by source" hot path; `access_tier` drives dispatch (Tier 1 STAC / Tier 2 OGC / Tier 3 HTTPS+Range / Tier 4 region+clip).
  - **engine** (curated seed YAML): conforms to new `CatalogEntry` shape; Tier 2/3 entries require `api_key_secret_ref` populated to a Secret Manager path infra provisions out-of-band.
  - **agent** (forward-looking, post-sprint-08): consumes the 4 new envelopes; classifies failed steps per FR-FR-2 routing.
  - **web** (forward-looking, post-sprint-08): renders FR-FR-1 recovery modal + §F.1.2 Mode 2 review modal.
  - **infra** (sprint-08 or sprint-09): provisions D.11 + D.12 collections + declared indexes; Secret Manager resources for Tier 2/3 `api_key_secret_ref`.
- **Resolves:** new substrate.
- **Conflicts:** none. Disjoint file ownership from concurrent jobs 0048/0049/0052.

## Verification

- **Tests run:** `cd /home/nate/Documents/GRACE-2 && .venv-agent/bin/python -m pytest packages/contracts/tests/ -q` -> `142 passed in 0.41s` (baseline 131; net +11; ≥6 acceptance criterion exceeded).
- **JSON Schema export idempotence:** First run wrote 43 schema files (+6 new + 1 modified `catalog_entry.json`). Second consecutive run: `cp -r packages/contracts/schemas /tmp/schemas-final && .venv-agent/bin/python -m grace2_contracts.export_schemas > /dev/null && diff -qr /tmp/schemas-final packages/contracts/schemas/` -> no output (byte-identical). **IDEMPOTENT confirmed.**
- **SRS monolith regeneration:** `make srs` -> `==> docs/SRS_v0.3.md regenerated (3611 lines)` (was 3058; +553 matching narrow-file deltas). Lossless concat invariant preserved.
- **FROZEN-paths check:** `git status --short services/ web/ infra/ styles/` -> no output. No edits to `services/agent/**`, `services/workers/**`, `web/**`, `infra/**`, `styles/**`, `reports/complete/**`, or to D.1-D.6 collection schemas / existing envelope shapes / `envelope.py` / `event.py` / `execution.py`. The pre-existing modification to `packages/contracts/schemas/ws_session_state.json` is from concurrent job-0048 (FR-FR-3 SessionStateStatus) and was NOT touched.
- **Results:** **pass.** 142/142 contracts tests green in 0.41s; export idempotent; SRS monolith regenerates cleanly; A + D narrow files carry the extensions; no FROZEN-path violations.

## Live E2E evidence

Verbatim transcripts:

```
$ cd /home/nate/Documents/GRACE-2 && .venv-agent/bin/python -m pytest packages/contracts/tests/ -q
........................................................................ [ 50%]
......................................................................   [100%]
142 passed in 0.41s
```

```
$ .venv-agent/bin/python -m grace2_contracts.export_schemas | tail -8
  ws_recovery_choice.json
  ws_recovery_choice_response.json
  ws_session_resume.json
  ws_session_state.json
  ws_spatial_input_request.json
  ws_spatial_input_response.json
  ws_tool_call_complete.json
  ws_tool_call_failed.json
$ cp -r packages/contracts/schemas /tmp/schemas-final
$ .venv-agent/bin/python -m grace2_contracts.export_schemas > /dev/null
$ diff -qr /tmp/schemas-final packages/contracts/schemas/
(no output — byte-identical)
$ echo "IDEMPOTENT OK"
IDEMPOTENT OK
```

```
$ make srs
==> regenerating docs/SRS_v0.3.md from docs/srs/* (DO NOT EDIT MONOLITH; edit parts under docs/srs/)
cat docs/srs/00-preamble.md ... docs/srs/F-data-sources-discovery-secrets.md > docs/SRS_v0.3.md
==> docs/SRS_v0.3.md regenerated (3611 lines)
```

Round-trip evidence (one of the 10 new tests — FR-FR-1 envelope JSON serialize -> deserialize -> re-serialize idempotence):

```
>>> from grace2_contracts.ws import RecoveryChoicePayload
>>> from grace2_contracts.common import new_ulid
>>> import json
>>> req = RecoveryChoicePayload(
...     request_id=new_ulid(), failed_step_id=new_ulid(),
...     error_code="UPSTREAM_API_ERROR",
...     error_message="USGS 3DEP returned HTTP 503",
...     context="fetching DEM at Fort Myers bbox",
...     options=["deny","retry","chat"], ttl_seconds=300)
>>> a = req.model_dump(mode="json")
>>> text_a = json.dumps(a, sort_keys=True)
>>> b = RecoveryChoicePayload.model_validate(json.loads(text_a)).model_dump(mode="json")
>>> json.dumps(b, sort_keys=True) == text_a
True
```
