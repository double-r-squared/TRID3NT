# Report: gate-ordering hygiene (no junk anonymous user rows on AUTH_REQUIRED-rejected connections) + stale secrets comment

**Job ID:** job-0252b-agent-20260611
**Sprint:** sprint-13.5 Stage 1
**Specialist:** agent
**Task:** Fix the job-0252 panel's live-verify minor finding — under `AUTH_REQUIRED=true`, rejection must short-circuit BEFORE any anonymous-user provisioning/persistence (zero collection writes on the rejected path); gate-OFF dev behavior byte-identical; gate-ON+valid token unchanged. Plus fix the stale `secrets_handler.py:416` comment.
**Status:** ready-for-audit

## Summary
Under the `AUTH_REQUIRED` gate, `authenticate_token()` no longer provisions/persists an ephemeral anonymous `UserDocument` on its failure paths — it returns an **unprovisioned** anonymous `AuthResult` so the server gate (`_handle_auth_token` / `_ensure_auth_handshake`) rejects the socket (A.5 4401 + A.6 `AUTH_FAILED`) with zero collection writes (and zero `users`-collection access at all). Gate-OFF (dev/demo) behavior is byte-identical to before. The stale `secrets_handler.py` comment referencing the removed `$exists:false` backward-compat branch is rewritten to the current owner-scoped semantics. No `server.py` change was needed — the server-side gate ordering was already correct; the only buggy write came from `authenticate_token` running before the gate check in `_handle_auth_token`.

## Root cause + design

### Where the junk row came from (before)
`_handle_auth_token` (server.py:1515-1525) calls `authenticate_token` BEFORE inspecting `result.is_anonymous`:

    result = await authenticate_token(tok, get_persistence())
    if result.is_anonymous and auth_required():
        await _reject_unauthenticated(...)
        return False

`authenticate_token`'s three failure paths each called `_provision_anonymous_user(persistence)`, which does `persistence.upsert_user(user)` — a write to the `users` collection — BEFORE the caller ever checked the gate. So every forged/unauthenticated connection persisted a junk anonymous row, then got rejected a moment later. (`_ensure_auth_handshake` was already clean: it checks `auth_required()` and rejects BEFORE calling `authenticate_token`.)

### The fix (gate-aware short-circuit inside `authenticate_token`)
`authenticate_token` now reads `auth_required()` once (call-time, local import to avoid an import cycle) and on each anonymous-resolution failure path, when the gate is ON, returns `_anonymous_result_no_persist()` instead of `_provision_anonymous_user(persistence)`.

BEFORE (auth_handshake.py — failure paths):

    if not token_str:
        anon_hint = (...)
        if anon_hint and persistence is not None:
            existing = await _try_reuse_anonymous_user(persistence, anon_hint)
            ...
        return await _provision_anonymous_user(persistence)        # WRITE
    if not claims:
        return await _provision_anonymous_user(persistence)        # WRITE
    if not firebase_uid:
        return await _provision_anonymous_user(persistence)        # WRITE

AFTER:

    gate_on = auth_required()
    if not token_str:
        if gate_on:
            return await _anonymous_result_no_persist()            # NO write, NO sticky read
        anon_hint = (...)
        if anon_hint and persistence is not None:
            existing = await _try_reuse_anonymous_user(persistence, anon_hint)
            ...
        return await _provision_anonymous_user(persistence)        # gate OFF: unchanged
    if not claims:
        if gate_on:
            return await _anonymous_result_no_persist()            # NO write
        return await _provision_anonymous_user(persistence)
    if not firebase_uid:
        if gate_on:
            return await _anonymous_result_no_persist()            # NO write
        return await _provision_anonymous_user(persistence)

`_anonymous_result_no_persist()` is `_provision_anonymous_user(None)` — the existing unbound-persistence branch already builds an in-memory `User` with a fresh ULID and never writes. Expressing it as its own intent-named helper makes "no write on the rejected path" explicit at every call site and prevents a future refactor from accidentally passing a live `persistence`.

### Why gate-OFF behavior cannot change
Every `if gate_on:` arm is purely additive in front of the original line. When `auth_required()` is False, `gate_on` is False, every new branch is skipped, and control reaches the IDENTICAL original `_provision_anonymous_user(persistence)` / sticky-reuse code unchanged. The running dev agent (pid 3799395) has NO `AUTH_REQUIRED` env set -> `auth_required()` returns False -> it provisions + persists + reuses anonymous users exactly as before, with the same auth-ack wire sequence. The valid-token branch (path 3) was never touched, so gate-ON + valid token still resolve-or-provisions the real user and binds.

### Sticky-reuse read also suppressed on the gated path
On the gate-ON empty-token path the short-circuit returns BEFORE the `_try_reuse_anonymous_user` read (a `users` `find-one`), so the rejected path makes ZERO MCP traffic — not even a read. Verified by `test_gate_on_forged_token_with_anon_hint_does_not_read_or_write`.

## Changes Made
- **services/agent/src/grace2_agent/auth_handshake.py**
  - `authenticate_token`: added a call-time `gate_on = auth_required()` read (local import of `auth_required` to avoid an import cycle — `auth.py` imports only stdlib, so the cycle risk is nil; the local import is belt-and-suspenders) and a gate-ON short-circuit on all three anonymous-resolution failure paths (empty/no token, verify failed, uid-missing). Docstring extended with the gate-ordering-hygiene contract.
  - Added `_anonymous_result_no_persist()` helper (`= _provision_anonymous_user(None)`, no write), documented as the gate-rejected constructor.
- **services/agent/src/grace2_agent/secrets_handler.py** (comment only, :414-417)
  - Rewrote the stale comment that described the removed `user_id: {$exists: False}` backward-compat branch of `list_secrets_refs`. New comment describes current owner-scoped semantics: on a failed stamp the record is persisted-but-unowned and the owner-scoped list filter won't surface it (job-0252 removed the leak clause); we log-and-continue rather than fail the add. Runtime unchanged.
- **services/agent/tests/test_auth_required_gate.py** (5 new tests, original 24 untouched)
  - `test_gate_on_forged_token_writes_no_user_row` — gate ON + forged token -> still 4401+AUTH_FAILED+no-bind AND zero collection writes, zero `users` access, empty users store.
  - `test_gate_on_no_token_envelope_writes_no_user_row` — gate ON + non-auth first envelope -> 4401 + zero writes (pins the already-clean `_ensure_auth_handshake` path).
  - `test_gate_on_forged_token_with_anon_hint_does_not_read_or_write` — gate ON + empty token carrying a real reusable `anonymous_user_id` hint -> rejected with ZERO MCP traffic after the seed (no sticky-reuse read, no write).
  - `test_gate_off_forged_token_provisions_and_persists_user` — REGRESSION PIN: gate OFF + forged token -> anonymous user IS provisioned + persisted to the `users` collection, `is_anonymous=True`, connection proceeds (protects the live demo agent).
  - `test_gate_on_valid_token_provisions_real_user` — gate ON + VALID token -> the REAL (non-anonymous) user is provisioned + persisted (`firebase_uid` set, `is_anonymous=False`), not rejected — proves the short-circuit suppresses only the anonymous write.

## Decisions Made
- **Decision:** Put the fix in `authenticate_token` (caller-agnostic), not in the `server.py` call sites.
  - Rationale: the kickoff offered both designs; fixing the producer means BOTH callers (and any future caller) get the property for free, and `_ensure_auth_handshake` (already clean) needs no change. `server.py` required no edit at all — smaller blast radius, gate ordering stays a single concern in one function.
  - Alternatives considered: gating in `server.py` before calling `authenticate_token` — would duplicate the gate check across two call sites and leave the latent footgun in `authenticate_token` for future callers.
- **Decision:** Express the no-persist path as a named helper `_anonymous_result_no_persist()` rather than inlining `_provision_anonymous_user(None)`.
  - Rationale: makes the "no write on the rejected path" invariant self-documenting at each call site and refactor-resistant.
  - Alternatives considered: inlining `_provision_anonymous_user(None)` — functionally identical but the intent is implicit and a careless edit could swap `None` for `persistence`.

## Invariants Touched
- **Confirmation before consequence / no cost theater (inv 9):** preserves — no cost surfaces touched; this only removes an unwanted write.
- **MCP canonical persistence:** preserves — still routes anonymous/real user CRUD through `Persistence.upsert_user`; the gated path simply makes no call.
- **Determinism boundary / engine registration:** untouched.

## Open Questions
- None blocking. (Out-of-scope note for the orchestrator: the contract-layer finding from the job-0252 panel — `mint_signed_url` comparing `case_doc.user_id` against the Firebase token uid rather than the internal `users._id` ULID — is routed to job-0251b/infra and is a different seam; this job does not touch it.)

## Dependencies and Impacts
- Depends on: job-0252 (DONE, panel 3/4) — this is the fix-pair for its live-verify minor finding.
- Affects: none downstream. `server.py` unchanged; no contract/schema change; no `infra/` overlap with the parallel job-0251b.

## Verification
- **Tests run:**
  - `tests/test_auth_required_gate.py` — 29 passed (24 original unmodified + 5 new). Original 24 verified green and untouched.
  - `tests/test_auth_handshake.py` + `tests/test_sticky_anonymous_user.py` — green (the anonymous-handshake + sticky-reuse paths these cover are gate-OFF and unchanged).
  - Full agent suite: `cd /home/nate/Documents/GRACE-2/services/agent && .venv/bin/python -m pytest tests/ -q --ignore=tests/live` -> **4359 passed, 72 skipped, 1 xfailed, 5 failed** in 285s.
- **The 5 failures are EXACTLY the allowed pre-existing set:** `test_data_fetch::{fetch_landcover_docstring_records_access_tier, fetch_river_geometry_docstring_records_tier_4, lookup_precip_return_period_docstring_records_tier_3}` (docstring-tier) + `test_model_flood_scenario::{returns_layer_uri, triggers_loaded_layers_emit}` (GCS). Passed rose 4354 -> 4359 (= +5 new tests, no regressions).
- **Live E2E evidence:** the gate-ordering property is exercised against the REAL `_handle_auth_token` / `_ensure_auth_handshake` server functions and the REAL `MockMCPClient` (records every MCP call) + real `Persistence.upsert_user` shape — `test_gate_on_forged_token_writes_no_user_row` asserts the users store is empty and no write call fired on the rejected path; `test_gate_off_..._persists_user` asserts the row IS written when the gate is off. The running dev agent (pid 3799395, `AUTH_REQUIRED` unset) was NOT restarted or disturbed — read-only `pgrep` only; its gate-OFF anonymous behavior is unchanged on its next restart by construction (additive `if gate_on:` arms).
- **No Gemini/Vertex calls.** The Firebase verify path is mocked via `set_verify_hook`; persistence is the in-memory `MockMCPClient`.
- **Results: pass.**

---

## Re-panel verdict (orchestrator, 2026-06-11, wf_8fa82d48-ffe, shared with job-0251b)

**PASS 4/4 — job-0252b DONE.** Live-verify drove a real gate-ON throwaway agent through 7 hostile connections: zero files on disk (not even an empty users.json) — the prior panel's junk-row observation is now impossible; gate-OFF anonymous connect provisions exactly 1 row (live-demo pin intact). Gate-OFF byte-identity confirmed additively (hunk audit: three if-guards before verbatim original lines) and behaviorally. Fresh probes: 9/9 zero-MCP-call assertions on all three failure paths against a real counting FileMCPClient.
