# Audit: CatalogEntry pydantic + MongoDB D.8/D.9 + offer-catalog-addition envelopes (Mode 1 schema)

**Job ID:** job-0045-schema-20260607, **Sprint:** sprint-08, **Auditor:** Development Orchestrator, **Status:** approved

## Task Assignment

**Specialist:** schema

**Prerequisites:**
- v0.3.18 §F.1.2 (Mode 1 catalog + Mode 2 offer-to-add framing)
- v0.3.19 §3.10 FR-FR (recovery-choice envelope shape — same Appendix A amendment surface as offer-catalog-addition)
- v0.3.20 housekeeping (FR-DC-1 per-TTL-class bucket layout — informs CatalogEntry's path-shape conventions)
- job-0030 (M4 schema substrate — AtomicToolMetadata pattern)

**SRS references** (narrow files only):
- `docs/srs/F-data-sources-discovery-secrets.md` — §F.1 + §F.1.1 + §F.1.2 (Mode 1/2/3) — the binding contract
- `docs/srs/D-mongodb-collection-schemas.md` — D.1–D.6 existing collections; you add D.8 (catalog_entries) + D.9 (catalog_audit_log)
- `docs/srs/A-websocket-protocol.md` — Appendix A envelope catalog; you extend with `offer-catalog-addition` + `catalog-addition-response` from §F.1.2 + `recovery-choice` + `recovery-choice-response` from §3.10
- DO NOT load `docs/SRS_v0.3.md` monolith

### Scope

1. **`CatalogEntry` pydantic model** in `packages/contracts/src/grace2_contracts/catalog.py` (NEW module). Fields per §F.1.2 Mode 1: `id`, `name`, `description`, `urls` (list — primary + alternative mirrors), `access_tier` (Literal 1/2/3/4 per §F.1.1), `credential_tier` (Literal 1/2/3 per §F.1), `ttl_class` (FR-DC-2 Literal), `source_class` (str), `license` (str), `citation` (str), `vintage` (str | None), `last_verified` (datetime), `status` (Literal `"active"` / `"deprecated"` / `"user_proposed_pending_curator_review"`), `how_to_use` (str — multi-line invocation examples + parameter constraints + known quirks). Plus `model_validator` cross-field rule: when `credential_tier == 1`, no `api_key_secret_ref` field expected; when ≥ 2, the field is required.
2. **MongoDB collection D.8 `catalog_entries`** — append to `D-mongodb-collection-schemas.md` Appendix D with the schema declaration, indexes (`{source_class: 1}`, `{status: 1, source_class: 1}` for the catalog_search query path), TTL config (none — catalog entries are durable until curator removes).
3. **MongoDB collection D.9 `catalog_audit_log`** — append to Appendix D. Fields: `entry_id`, `session_id` (optional), `user_id` (optional), `event_type` (Literal `"add"` / `"update"` / `"deprecate"` / `"user_proposed"` / `"curator_approved"` / `"curator_rejected"`), `event_payload` (dict — varies by event_type), `timestamp`. Indexed by `{entry_id: 1, timestamp: -1}`.
4. **Appendix A envelope extensions** — append to `A-websocket-protocol.md`: `offer-catalog-addition` + `catalog-addition-response` per §F.1.2 Mode 2; `recovery-choice` + `recovery-choice-response` per §3.10 FR-FR-1. All four envelopes use the standard A.1 wrapper (`type`, `id`, `ts`, `session_id`, `payload`). The `recovery-choice` payload includes `failed_step_id`, `error_code` (A.6 SCREAMING_SNAKE_CASE), `error_message`, `context`, `options` (Literal subset of `["deny", "retry", "chat"]`), `ttl_seconds`. The `offer-catalog-addition` payload includes `url`, `discovered_via`, `probe_findings` (dict with `tls_cert_org`, `access_tier_inferred`, `supports_range_requests`, `stac_root_found`, `ogc_capabilities_found`, `license_observed`, `content_type`, `last_modified_header`), `suggested_catalog_entry` (a `CatalogEntry`-shaped dict).
5. **JSON Schema re-export** via the existing `_export.py` / `export_schemas.py` pipeline. Verify idempotence (running twice produces empty diff).
6. **Tests** in `packages/contracts/tests/`: at least 6 new tests covering `CatalogEntry` round-trip + validator + each envelope shape + JSON Schema export idempotence.

### File ownership (exclusive)
- `packages/contracts/src/grace2_contracts/catalog.py` (NEW)
- `packages/contracts/src/grace2_contracts/ws_envelopes.py` (or wherever existing Appendix-A envelope pydantic models live — extend)
- `packages/contracts/schemas/*.json` (regenerated; commit the diff)
- `packages/contracts/tests/test_catalog.py` (NEW)
- `docs/srs/D-mongodb-collection-schemas.md` — D.8 + D.9 additions
- `docs/srs/A-websocket-protocol.md` — 4 new envelope shapes
- `reports/inflight/job-0045-schema-20260607/`

### FROZEN
- All existing pydantic models (don't refactor D.1–D.6 + existing envelopes)
- `services/agent/**`, `services/workers/**`, `web/**`, `infra/**`, `styles/**`, `reports/complete/**`
- Stage A concurrent jobs

### Acceptance criteria
- [ ] `CatalogEntry` registered + JSON Schema exported idempotently
- [ ] D.8 + D.9 added to Appendix D with full schema + indexes
- [ ] 4 new Appendix A envelopes added (offer-catalog-addition, catalog-addition-response, recovery-choice, recovery-choice-response)
- [ ] ≥6 new contracts tests; suite still green (131+)
- [ ] `make srs` regenerates monolith cleanly
- [ ] No edits to FROZEN paths

## Assessment

**Verdict:** approved.

The Mode 1 catalog schema substrate lands cleanly with strong execution including a graceful concurrent-reconciliation outcome with the parallel job-0048. `CatalogEntry` is rewritten to the §F.1.2 binding shape with proper `model_validator` cross-field rules; `D.11 catalog_entries` + `D.12 catalog_audit_log` Mongo collections land with correct indexes (`source_class` + compound `(status, source_class)` for the catalog_search query path; compound `(entry_id, timestamp DESC)` for audit history); the 4 envelopes from §F.1.2 + §3.10 are registered in the routing dicts (the payload classes themselves were added by concurrent job-0048; this job's work is the routing-dict registration + test factories — clean division of labor after the fact).

**Honest D-numbering disclosure (OQ-45-D-NUMBERING).** Specialist discovered during work that D.7–D.10 are already used by existing meta sections in Appendix D, so the new collections numbered as D.11/D.12 rather than the kickoff's D.8/D.9. This is exactly the live-verification-beats-kickoff discipline applied to SRS structure. The kickoff was written from a stale mental model; the specialist verified against the actual section structure. Routes for v0.3.21+ housekeeping if the numbering should be reconciled.

**Concurrent reconciliation outcome.** Job-0048 had scope-crept by adding the 4 payload classes to ws.py; this job filled the gap by registering them in `CLIENT_TO_AGENT_PAYLOADS` / `AGENT_TO_CLIENT_PAYLOADS` routing dicts + adding minimal factories to `test_ws.py`. **The combined Stage A outcome is functionally complete** despite the unintentional scope creep — `RecoveryChoicePayload`, `RecoveryChoiceResponsePayload`, `OfferCatalogAdditionPayload` (with `ProbeFindings` + `SuggestedCatalogEntry`), `CatalogAdditionResponsePayload` all exist, are typed, are registered, are tested. No envelope shape was duplicated or lost.

**Tests + schemas:** 11 new contracts tests (Mode 1 round-trip Tier 1/2 + credential validator + D.11/D.12 + parametrized event_type + FR-FR-1 envelope round-trip + cancellation + §F.1.2 Mode 2 + payload registry membership + JSON Schema export + idempotence + Invariant 9 cost-field negative control + Literal enforcement). 6 new JSON Schemas exported (catalog_entry, catalog_entry_document, ws_catalog_addition_response, ws_offer_catalog_addition, ws_recovery_choice, ws_recovery_choice_response). **Idempotence verified live** — running export twice produces byte-identical output. **142/142 contracts green** (+11 net over the 131 baseline).

**Monolith regenerated cleanly** — `make srs` no-ops post-second run; A.* + D.11/D.12 sections added; line count delta consistent with the prose additions.

## Invariant Check

- **Invariant 1, 2, 9:** preserved. Invariant 9 explicitly tested via cost-field negative control (parametrized over 4 forbidden cost-fields).
- **A.1 envelope wrapper discipline:** all 4 new envelopes use the standard `(type, id, ts, session_id, payload)` shape.
- **A.6 SCREAMING_SNAKE_CASE error codes:** preserved (RecoveryChoicePayload.error_code).
- **§F.1.2 + §3.10 binding contracts:** the envelope shapes match the SRS prose verbatim.
- **D.6 / D.11 / D.12 schema discipline:** indexes follow the FR-MP-5 query-driven pattern.

## Decisions Validated

All 7 OQs surfaced reviewed and accepted:
- **OQ-45-D-NUMBERING** (D.11/D.12 vs D.8/D.9) — accepted as honest live-verification outcome.
- **OQ-45-LICENSE-CLAIM-NAMING** — `license_claim` in SuggestedCatalogEntry distinguishes "what the source claims" from `license` in CatalogEntry (what we record after curator verification). Reasonable distinction.
- **OQ-45-CATALOG-ENTRY-DOCUMENT-NO-_id-ALIAS** — entry id is free-form string (not ULID), so no `_id` alias is needed. Correct.
- **OQ-45-EVENT-PAYLOAD-OPEN-DICT** — `event_payload` deferred discriminated union for v0.1; revisit at sprint-09 if audit semantics need typing.
- **OQ-45-RECOVERY-OPTIONS-ROUTING** — kept out of schema (each tool/error decides which options to surface); routing logic lives in agent code per FR-FR-2 table.
- **OQ-45-NO-SCHEMA-VERSION-BUMP** — additive changes don't require version bump per pre-MVP additive discipline; accept.
- **OQ-45-FR-FR-2-ERROR-CODES** — A.6 amendment for the new failure-class codes deferred to a follow-up; non-blocking.

## Dependency Check

- **v0.3.18 §F.1.2** + **v0.3.19 §3.10 FR-FR-1** — binding contracts honored.
- **job-0030 AtomicToolMetadata pattern** — mirrored cleanly (model_validator + Literal discipline).
- **job-0048 concurrent reconciliation** — work composed correctly despite the parallel scope-overlap.

## Follow-up Actions

1. **Unblock Stage B (job-0047 — catalog_search + catalog_fetch atomic tools)** — schema substrate is in place; engine specialist can start.
2. **Bundle OQ-45-D-NUMBERING + OQ-45-FR-FR-2-ERROR-CODES + OQ-45-EVENT-PAYLOAD-OPEN-DICT** into next housekeeping pass for triage.

## Sign-off

**Approved 2026-06-07 by Development Orchestrator.** Schema substrate for Mode 1 is live + tested + JSON-Schema-exported idempotently. Stage A nearly complete (waiting on 0046 catalog seed research). 215,062 tokens (Opus — substantial schema work with concurrent reconciliation handled gracefully).
