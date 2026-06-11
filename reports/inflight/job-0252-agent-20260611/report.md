# job-0252 — Agent auth hardening (AUTH_REQUIRED gate + pre-Auth case migration) — REPORT

**Specialist:** agent · **Sprint:** 13.5 Stage 1 · **Model:** Opus
**State:** IN_REVIEW (adversarial panel follows) · resumed after a runner death on a transient API 529

---

## TL;DR
The production-hardening DELTA on top of the Wave 2 connect handshake is landed and green. The `AUTH_REQUIRED` gate rejects unauthenticated WebSockets (A.5 close `4401` + A.6 `AUTH_FAILED`) when engaged, and falls back to today's anonymous behavior verbatim when off. The `$exists:false` pre-Auth leak clause is gone from both `list_cases_for_user` and `list_secrets_refs`; an idempotent startup migration stamps orphan Cases with `MIGRATION_ANON_UID`. Cases are now owner-scoped end-to-end (create stamps owner; list filters by owner).

**Shipped `AUTH_REQUIRED` default: `"false"`** (loud TODO for job-0257).

## What the dead runner had done (KEPT) vs what I completed (FIXED/ADDED)

### KEPT (correct work left in the tree)
- `auth.py` (NEW, complete): `auth_required()` env gate (call-time read, truthy precedence); `AUTH_CLOSE_CODE=4401`; `AUTH_FAILED_ERROR_CODE`; `MIGRATION_ANON_UID="__preauth_migration_anon__"`; DEFAULT-FLIP DECISION documented in docstring.
- `persistence.py`: `upsert_case(owner_user_id=...)`; idempotent `migrate_preauth_cases(anon_uid)` via `update-many`; `$exists:false` removed from `list_cases_for_user` AND `list_secrets_refs`; `FileMCPClient.update-many` dev-substrate support.
- `server.py`: `_reject_unauthenticated` (4401+AUTH_FAILED); gate wired into `_handle_auth_token` / `_ensure_auth_handshake` (both now return bool); handler loop honors the False return; `_run_preauth_case_migration` startup hook + `run_server` call.

### FIXED (incomplete / would crash or regress)
1. CRITICAL — `MIGRATION_ANON_UID` was NOT imported in `server.py` (used at :476). NameError on every startup the moment the migration runs. Added to the `from .auth import` block.
2. CRITICAL — create call-sites never stamped the owner. Both CREATE sites (`case-command(create)`, `_auto_create_case_from_root`) still called `upsert_case(case)` with no owner. With the leak clause gone, every new Case became an orphan invisible to its own creator — Case feature broken. Wired both to pass `owner_user_id=state.authenticated_user_id`.
3. CRITICAL — read path keyed on the wrong id. `_emit_case_list` listed by `state.session_id` while the write path stamps `authenticated_user_id`. Aligned read to `state.authenticated_user_id or state.session_id` (matches secrets/chat-persist posture).
4. 9 regressions the runner introduced and never reconciled (tests asserting the OLD leak). Updated each to owner-scoped semantics: test_persistence::{list_cases_for_user, list_secrets_filters_active_only}; test_file_persistence::test_file_mcp_list_cases; test_full_stream_persistence_job0267::{deleted_and_archived_excluded, emitted_case_list_excludes_tombstones, pre_status_case_docs_stay_listed}; test_auto_create_case_job0262::test_root_prompt_emits_case_open_then_case_list; test_server_case_handlers::{case_create_emits_case_open_and_case_list, case_rename_updates_title_and_refreshes_case_list}.

### ADDED (deliverable #3 — runner wrote ZERO new tests)
- `tests/test_auth_required_gate.py` (NEW, 24 tests): env precedence; AUTH_REQUIRED=true rejects forged token (4401+AUTH_FAILED, no bind) + non-auth first envelope + accepts VALID token; AUTH_REQUIRED=false preserves anonymous bind exactly; migration stamps orphans / leaves owned untouched / idempotent / writes only projects / migrated Case visible to MIGRATION_ANON_UID only; `_run_preauth_case_migration` no-op when unbound.

## AUTH_REQUIRED default as shipped — "false" (load-bearing)
`AUTH_REQUIRED_DEFAULT="false"`. Precedence: explicit env wins (truthy {1,true,yes,on}, case-insensitive); absent → "false". Rationale: the running dev agent has NO AUTH_REQUIRED set; default "true" would reject every connection on its next restart, breaking the live demo. job-0257 flips to true via Cloud Run env. Loud TODO(job-0257) on the constant + in the docstring. Env read at call time so Cloud Run injection takes effect without re-import.

## Test counts
- Full agent suite: 4354 passed, 72 skipped, 1 xfailed, 5 failed (was 4321 passed pre-job; +33 = 24 new + 9 fixed).
- The 5 failures are exactly the allowed pre-existing set: test_data_fetch::{landcover, river_geometry, lookup_precip_return_period} (docstring-tier) + test_model_flood_scenario::{returns_layer_uri, triggers_loaded_layers_emit}.
- No Gemini/Vertex calls. Agent NOT restarted. firebase_admin 6.9.0 already in venv; firebase-admin>=6.5,<8 already in pyproject (Wave 2) — no change needed.

## Files staged (auth-job only)
auth.py (new), server.py, persistence.py, test_auth_required_gate.py (new), + 5 regression-fixup test files. NOT staged (unrelated Wave 4.10 drift): test_multi_turn_loop.py + test_tool_retry_on_failure.py (recovery-flow / circuit-breaker edits depending on untracked circuit_breaker.py — not auth work).

## Risks / follow-ups
- R1 (cosmetic, out of scope): secrets_handler._upsert_with_user has a stale comment referencing the removed $exists:false fallback. Runtime correct; comment stale. Left untouched (file-ownership seam).
- R2: No secrets migration ships (kickoff scoped only a CASE migration). Pre-Auth orphan secrets become invisible after the leak removal; live secrets stamp user_id via _upsert_with_user so are fine. Flag for panel.
- R3: Migration count parsing best-effort vs real mongodb-mcp-server text/EJSON blob; success criterion is "no orphans on next run" (documented). MCPSurfaceTranslator passes update-many through (only intercepts update-one).
- R4 (headline): AUTH_REQUIRED default "false" MUST flip to "true" in prod via Cloud Run env at job-0257, else prod ships open.

---

## Adversarial panel verdict (orchestrator, 2026-06-11, wf_cba0b225-134, 387,469 tok)

**PASS 3/4 — job-0252 DONE.**

- **correctness CONFIRM** — 11 fresh adversarial tests + 24 job tests (35 total) green under `AUTH_REQUIRED=true`; no unauthenticated path reaches a bound session or dispatch (gate rejects BEFORE `_bind_auth_result`; exceptions fail-closed); migration idempotent against the REAL FileMCPClient; live agent pid 3799395 provably unaffected.
- **regression CONFIRM** — full suite re-run 4354 passed with EXACTLY the 5 allowed pre-existing failures; dev no-env neutrality proven on the real handler functions; all 9 reconciled tests strengthened (negative isolation added), not weakened.
- **contract REFUTED (major)** — the cross-JOB seam breaks at the VALUE layer: this job stamps `user_id` = the **internal `users._id` ULID** (`_resolve_or_provision_user` → `new_ulid()`; Firebase uid lives separately in `user.firebase_uid`), which is **SRS H.2/H.5-conformant**; but job-0251's `mint_signed_url` compares `case_doc.user_id == Firebase token uid` (`claims.uid/sub`). They never match → every legitimate owner's mint 403s once the signed-URL chain is wired. The manifest line "sets user_id = uid from the verified token" contradicts SRS H.2 — the SRS is authoritative; the manifest line was wrong. The agent-internal create→store→list chain is self-consistent and leak-free (reproduced). **Fix routed to job-0251b (infra)** — resolve verified Firebase uid → internal users._id via users-collection lookup inside the function; this job's code is the spec-correct side of the seam and stands.
- **live-verify CONFIRM (minor)** — real-wire 4401+AUTH_FAILED on forged token and on non-auth first envelope (throwaway agents, zero orphans, dev agent + tailnet untouched, +66min uptime). Minor pre-existing finding: under `AUTH_REQUIRED=true`, `authenticate_token()` persists an ephemeral anonymous UserDocument BEFORE the gate rejects → junk user rows under hostile load. Ordering fix routed to **job-0252b**.

Panel cost logged to cost_tracking.json (`panel-job-0252`).
