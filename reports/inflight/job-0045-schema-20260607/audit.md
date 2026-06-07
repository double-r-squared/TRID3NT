# Audit: CatalogEntry pydantic + MongoDB D.8/D.9 + offer-catalog-addition envelopes (Mode 1 schema)

**Job ID:** job-0045-schema-20260607, **Sprint:** sprint-08, **Auditor:** Development Orchestrator, **Status:** assigned

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
