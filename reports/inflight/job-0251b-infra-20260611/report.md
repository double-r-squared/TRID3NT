# Report: mint_signed_url owner-identity resolution (Firebase uid → internal users._id ULID) + clamp_ttl OverflowError fix

**Job ID:** job-0251b-infra-20260611
**Sprint:** sprint-13.5 Stage 1
**Specialist:** infra (model: Fable, per the critical-bug routing rule)
**Task:** (verbatim from audit.md) "mint_signed_url owner-identity resolution (Firebase uid → internal users._id ULID) + TTL clamp hardening" — fix-pair closing the job-0251 REFUTED panel, per Decision 10.
**Status:** ready-for-audit (sprint STATE convention: IN_REVIEW)

## Summary

`infra/signed_urls/main.py` now resolves the verified Firebase uid to the
internal `users._id` ULID via a users-collection lookup BEFORE the
`case_owned_by` check, fail-closed at every branch (no users doc → 403; lookup
error → 503; never a raw-uid fall-through) — closing the panel-job-0251/0252
contract refute where every legitimate owner 403'd because the function
compared the Firebase token uid against Case owner fields that store internal
ULIDs. `clamp_ttl` additionally catches `OverflowError` so `float("inf")` /
`1e400` fall back to the default TTL instead of 500ing. Suite extended 55 → 78,
all green; `tofu validate` green; zero infra-resource/env changes.

## The new ownership-resolution chain (documented in the module docstring)

token uid == body.user_id (both the FIREBASE uid; never trust the body) →
resolve to internal ULID via `users` find_one on `{"firebase_uid": <verified
uid>}`, `_id` authoritative / `user_id` key fallback (mirrors
`Persistence.get_user_by_firebase_uid`) → `case_owned_by(case_doc,
resolved_internal_id)` with unchanged comparison semantics (`user_id` OR
`owner_user_id`, no `$exists:False`) → mint. `MIGRATION_ANON_UID`-owned
cases are unmintable by ANY token by construction: no users doc maps a
`firebase_uid` to the sentinel, so resolution can never produce it.

## Changes Made

- File: `infra/signed_urls/main.py`
  - :1-49 module docstring — documented chain rewritten to the 4-step form
    (kickoff items: lines 14-15 and 34-38 of the old text): token uid ==
    body.user_id → resolve to internal ULID (Decision 10, SRS H.2:42/H.5:124)
    → ownership check → mint; users-lookup mirror of
    `Persistence.get_user_by_firebase_uid` documented; MIGRATION_ANON_UID
    non-mintability documented.
  - :107 new `USERS_COLLECTION = "users"` (pinned to persistence.py D.13).
  - :156-164 new `ServiceUnavailable(SignedUrlError)` with `status = 503`
    (kickoff: "Lookup error → 503/fail-closed").
  - :183-185 `_Deps.fetch_user_doc` injection seam added; :301-302 `_resolve()`
    fills it with the production builder.
  - :211-235 `_mongo_find_one(collection, filter_)` — the function's single
    Mongo access path, factored out of the old `_prod_fetch_case_doc` verbatim
    (same Secret Manager SRV secret, same database, same 5000ms timeouts);
    :238-245 `_prod_fetch_user_doc` (find_one `{"firebase_uid": ...}` on
    `users`); :248-255 `_prod_fetch_case_doc` now delegates to the shared path.
  - :324-326 `clamp_ttl` — `OverflowError` added to the
    `(TypeError, ValueError)` catch (panel correctness-lens nit).
  - :366-385 new pure helper `resolve_internal_user_id(user_doc)` — `_id`
    authoritative, `user_id` key fallback, `None` on missing/malformed/non-str
    (caller fails closed).
  - :388-409 `case_owned_by` docstring — comparison SEMANTICS unchanged;
    documents that `user_id` is now the RESOLVED internal ULID.
  - :440-477 `mint_signed_url` signature + docstring — chain + arg semantics
    (`user_id` = Firebase uid, resolved internally; 503 added to Raises).
  - :494-518 the resolution step itself (step 3, between the URI parse and the
    case fetch): `firebase_uid = verified_uid if verified_uid is not None else
    user_id` → `d.fetch_user_doc(firebase_uid)` wrapped so ANY exception →
    `ServiceUnavailable` (503); `resolve_internal_user_id(...)` → `None` →
    `Forbidden` ("no provisioned user for the verified identity").
  - :519-526 ownership check now `case_owned_by(case_doc, internal_user_id)`
    (was `user_id` — the bug); 403 message unchanged.
  - :545-558 `handle_request` docstring — body contract: `body.user_id`
    carries the FIREBASE uid; resolution is internal; body never carries the
    internal id.
- File: `infra/signed_urls/test_mint_signed_url.py`
  - 55 → 78 tests. Fixture identities are now DISTINCT per role (`FB_ALICE`
    "fb-uid-alice" vs `ULID_ALICE` "01JXULIDALICE…", same for bob) so any
    regression to a raw-uid comparison cannot pass by accident;
    `MIGRATION_ANON_UID` literal pinned to
    `services/agent/src/grace2_agent/auth.py:116` by comment. `make_deps` grew
    a `users` registry + `fetch_user` override and a default
    `fetch_user_doc`. Existing mint/HTTP tests updated to the new identity
    split (case owners = ULIDs; tokens/bodies = Firebase uids);
    `case_owned_by`/`parse_layer_uri`/`extract_bearer_token` tests untouched.
  - New tests (kickoff list → test name):
    - Owner mints (ULID-owned case + users doc + token==body==uid → 200):
      `test_mint_happy_path` (rewritten to the canonical chain) +
      `test_http_happy_path` (wire-level).
    - No users doc → 403: `test_mint_firebase_uid_with_no_users_doc_403`,
      `test_http_no_users_doc_403`.
    - Resolved user doesn't own the case → 403:
      `test_mint_rejects_wrong_owner` (owner=ULID_BOB), `test_http_wrong_owner_403`.
    - Second user's firebase_uid → different ULID → 403:
      `test_mint_second_user_cannot_mint_first_users_case`.
    - MIGRATION_ANON_UID-owned case unmintable via any token → 403:
      `test_mint_migration_anon_owned_case_unmintable_by_any_user`,
      `test_mint_forged_sentinel_token_still_403` (even a token whose uid IS
      the sentinel resolves to nothing), `test_http_migration_anon_case_403`.
    - Users-lookup failure → fail-closed:
      `test_mint_users_lookup_failure_fails_closed_503` (case doc deliberately
      stores the raw Firebase uid so a fall-through WOULD succeed — asserts it
      does not, and that `sign_url` is never called),
      `test_http_users_lookup_failure_503` (status 503 over the wire).
    - clamp_ttl overflow → DEFAULT_TTL_SECONDS: `test_clamp_ttl` params
      `float("inf")`, `float("-inf")`, `1e400`, `float("nan")` (ValueError
      coverage confirmed).
    - Plus: `test_resolve_internal_user_id_*` (9: _id authoritative, user_id
      fallback, prefers _id, 6 fail-closed param cases) and
      `test_mint_case_storing_raw_firebase_uid_not_mintable` (regression guard
      on the exact panel-refuted doc shape) and
      `test_mint_malformed_users_doc_fails_closed`.
- File: `reports/inflight/sprint-13-5-USER_UNBLOCK.md`
  - Additive "0251-A/D addendum (job-0251b)" under item 0251-D. Edited under
    the kickoff's explicit conditional ("update them if the function's
    env/inputs changed"): env/IAM/resources did NOT change, but the function's
    runtime inputs now include the users-collection mapping, which adds live-
    verify preconditions (test user must have a users doc; case owned by the
    INTERNAL ULID; no MIGRATION_ANON_UID case) and requires the 0251-A zip to
    carry the job-0251b main.py. Without the note, a correct deploy would 403
    the verify and look broken.

NOT changed: `infra/signed_urls.tf` (no resources/env/IAM touched),
`infra/signed_urls/requirements.txt` (the users lookup uses the already-pinned
pymongo + secret-manager deps), `requirements-dev.txt`, anything agent-side.

## Decisions Made

- Decision: resolution lives in the `mint_signed_url` core (not `handle_request`),
  as step 3 between the URI parse and the case fetch.
  - Rationale: the core is the unit-tested, deps-injected surface; resolving
    before the case fetch fails fast and leaks nothing (an unprovisioned user
    gets 403 without learning whether the case exists). The kickoff's
    "main.py:448 area" (token verification) feeds it via the existing
    `verified_uid` parameter — `handle_request` needed no flow change.
  - Alternatives considered: resolving in `handle_request` (would bypass the
    `_Deps` test seam and leave the no-verified_uid core path unresolved).
- Decision: the no-verified_uid path (internal/test callers) ALSO resolves —
  `firebase_uid = verified_uid if verified_uid is not None else user_id`.
  - Rationale: body `user_id` is a Firebase uid by contract; nothing may
    bypass Decision 10. `test_mint_no_verified_uid_skips_match_check` still
    proves only the token-match check is skipped.
  - Alternatives considered: trusting body user_id as an internal id when no
    token is present — rejected as a raw-uid side door.
- Decision: lookup ERROR → new `ServiceUnavailable` (503); missing OR
  malformed users doc → `Forbidden` (403).
  - Rationale: kickoff names 503 for lookup errors (retryable, infra fault)
    and 403 for "owns nothing" (a Firebase user who never connected to the
    agent). A malformed doc cannot establish identity → fail-closed 403.
- Decision: `resolve_internal_user_id` prefers `_id` over the `user_id` key.
  - Rationale: kickoff wording is binding ("`_id` is authoritative, `user_id`
    key fallback"). Note: `Persistence.get_user_by_firebase_uid` keeps a
    pre-existing `user_id` key and only falls back to `_id` — the opposite
    priority — but the agent's `upsert_user` writes both keys equal
    (`body["_id"] = user.user_id`), so the orders are indistinguishable on
    real documents. Flagged in Open Questions for the record.
- Decision: `_mongo_find_one` shared helper instead of duplicating the
  Secret-Manager+PyMongo block.
  - Rationale: kickoff requires "the same client/seam it already uses for the
    case-doc fetch"; one access path is the auditable form of that.
- Decision: `MIGRATION_ANON_UID` stays a test-file literal (pinned by comment
  to `grace2_agent/auth.py:116`), not a `main.py` constant or denylist.
  - Rationale: the function needs no runtime special-case — resolution can
    only return ids present in users docs, and no users doc carries the
    sentinel as a `firebase_uid`. A denylist would imply a path that does not
    exist; the property is asserted by three tests instead.
- Decision: test fixtures use distinct Firebase-uid vs internal-ULID values
  everywhere.
  - Rationale: the old suite used "user-alice" for both roles, which is
    exactly how the raw-uid comparison passed 55 tests while 403ing every
    production owner. Distinct values make that bug class unrepresentable.

## Invariants Touched

- Tier separation / NFR-S-1 (signing boundary): extends — the trust
  boundary now resolves identity per Decision 10 before granting object
  access; still fail-closed, still keyless signBlob, still no public access.
- Metadata-payload pattern (Invariant 6): preserves — MongoDB remains the
  only discovery path; one additional single-document metadata read, no bucket
  enumeration.
- Confirmation before consequence / no cost theater: preserves — no new
  user-facing fields or estimates.
- Determinism boundary / rendering / cancellation: not touched.

## Open Questions

- OQ-1 (non-blocking, TENTATIVE resolution applied): `_id`-first vs
  `user_id`-key-first normalization order differs between this function
  (kickoff-mandated `_id` authoritative) and
  `Persistence.get_user_by_firebase_uid` (keeps `user_id` key, falls back to
  `_id`). Indistinguishable on agent-written docs (`upsert_user` writes both
  equal); divergent only for a hand-edited doc where they differ. Proposed
  resolution: none needed now; if schema ever allows the two keys to diverge,
  pin one order in the SRS (H.5) and align both sides. Agent file untouched
  per the stop-don't-edit rule.
- OQ-2 (non-blocking, surfaced not fixed — agent-side concern, stop-don't-edit):
  `users` has no declared index on `firebase_uid` that I could verify from
  this seam; the new per-mint `find_one({"firebase_uid": ...})` is a collection
  scan at demo scale (harmless on M0, D.8 baseline ~50 users) but should ride
  an index if user count grows. Owner: schema/agent (collection contract is
  not infra's). Routing suggestion: fold into the existing job-0252b ordering
  fix or a schema follow-up.

## Dependencies and Impacts

- Depends on: job-0252 (DONE, panel 3/4 — owner stamping + MIGRATION_ANON_UID
  migration give Case docs the field this job's check reads); Decision 10 in
  `reports/sprints/sprint-13-5-decisions.md` (binding identity contract);
  job-0251 (the function + suite this job hardens; its REFUTED panel state is
  what the gating re-panel clears).
- Affects: job-0254 (signed-URL consumer wiring — the body contract it must
  send is unchanged: `user_id` = Firebase uid); the user's 0251-A/D unblock
  steps (addendum appended, see Changes); job-0252b (junk-user ordering fix —
  unrelated finding already routed there by panel-job-0252; nothing new added
  by this job).

## Verification

Re-runnable verbatim, from the repo root:

- Tests run:
  - `services/agent/.venv/bin/python -m pytest infra/signed_urls/test_mint_signed_url.py -v`
    → 78 passed in 0.06s (baseline before this job, same command: 55
    passed; +23 = 4 clamp-overflow params + 9 resolve_internal_user_id + 7
    mint-resolution + 3 HTTP-resolution). Zero failures, zero skips. Full
    transcript: `evidence/pytest.txt`.
  - `cd infra && tofu validate` → "Success! The configuration is valid."
    (OpenTofu v1.12.1). Transcript: `evidence/tofu_validate.txt`. No `.tf`
    file changed, as the kickoff expected.
- Bug-class scan (AGENTS.md "bundle small fixes"): `grep -n "int(" main.py` →
  one site (clamp_ttl, fixed); raw-uid comparison sites → one (fixed; the only
  other `claims.get` use feeds `verified_uid` into the resolved chain). No
  other instances in `infra/`.
- Live E2E evidence: qualified — deployment and the live mint round-trip
  are classifier-blocked USER steps by the kickoff's own hard constraint ("NO
  deployment (user-only step)") and the sprint's Gemini-free/no-production-
  mutation posture. The live verify is item 0251-D (now with the job-0251b
  preconditions addendum); the panel-refuted failure mode itself is reproduced
  and pinned by unit tests that fail on the old code
  (`test_mint_case_storing_raw_firebase_uid_not_mintable`,
  `test_mint_users_lookup_failure_fails_closed_503`) — both asserted against
  fakes that exactly mirror the agent's real document shapes
  (`{_id: <ULID>, firebase_uid: <uid>}` per `upsert_user`/
  `_resolve_or_provision_user`).
- Results: pass (unit + IaC validation), qualified (live deploy = user step).

## Hard-constraint compliance

No Gemini/Vertex calls. No agent restart; no file outside
`infra/signed_urls/` + this report dir except the kickoff-authorized
USER_UNBLOCK addendum. No service-account keys (signBlob path untouched). No
deployment. `git add` scoped to the six files named above; commit message
`job-0251b: ...` + Fable co-author trailer.
