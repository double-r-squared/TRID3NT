# GRACE-2 -> TRID3NT internal-identifier migration plan (DEFERRED reference)

Status: **DRAFT for later greenlight.** NATE 2026-06-28 decision is **user-visible rebrand + domain + GitHub repo only**; `grace2` stays the permanent internal codename. This doc is the cost/risk analysis of the ~9,500-ref *internal* migration so the "leave it / do it" call is informed. See memory `project_trid3nt_rebrand_scope`.

## TL;DR recommendation

Two tiers:

- **Tier-1 stateless naming** (safe behind a back-compat shim, downtime = redeploy window): Python packages `grace2_agent`/`grace2_contracts` (~716 import sites), CLI scripts, `GRACE2_*`/`VITE_GRACE2_*` env-var KEY names, logger names, MapLibre/keyframe IDs, data-testids, `__grace2*` window globals, systemd unit, `/opt/grace2`, SSM doc `grace2-runshell`, Batch queue/CEs/job-defs, ECR repos, IAM roles.
- **Tier-2 state-bearing AWS** (CANNOT rename in place -> create-new + copy-data + repoint-env + cutover): DynamoDB `grace2_*` tables, S3 buckets, Cognito pool `grace2-users` (us-west-2_mIpKrr727), SSM SecureString secrets `/grace2/secrets/*`, browser `grace2_*` localStorage keys.

**Verdict:** rename the **developer-facing** Tier-1 (packages, CLI, on-box conventions, stateless control plane) + the new brand domains. **Keep `grace2` permanently** for the DynamoDB prefix, S3 bucket names, env-var KEYS, the Cognito pool, and cosmetic browser keys. Moving live user data + forcing re-auth to chase names no user ever sees is a bad trade. Spend effort only where a human reads the name (repo / stack traces / AWS console-for-operators) or where it is trivially safe.

**The architecture helps:** the DynamoDB prefix and bucket names are already env-driven (`dynamo_backend.py:67-68` reads `GRACE2_DYNAMO_TABLE_PREFIX`, agent reads `GRACE2_RUNS_BUCKET`/`CACHE_BUCKET`), so Tier-2 moves are an env flip + a one-module dual-read shim, not a code rewrite. Proven in-repo dual-read template to copy: `web/src/lib/source_suggestion_suppression.ts:108-185`.

## Three hard couplings (must move in lockstep or the agent 403s / orphans data)

1. IAM hardcodes the wildcard `arn:...:table/grace2_*` (`infra/aws-agent-isolation/iam.tf:139-140`) -- new table prefix without updating this = AccessDenied on every DynamoDB call.
2. SSM secrets prefix `/grace2/secrets` is IAM-scoped AND embedded inside the persisted `vault_ref` strings in `grace2_secrets` items (`secrets_handler.py:169,232`; `persistence.py:1824`) -- renaming orphans every user API key unless each SecureString is copied AND the stored ref rewritten.
3. Stored case manifests embed full `s3://grace2-hazard-runs/...` LayerURIs that a bucket env flip does NOT rewrite -- the old runs bucket must stay readable forever (or a dual-bucket read shim).

NON-NEGOTIABLE: DynamoDB tables, S3 buckets, and Cognito pools have no rename API. Cognito is worst -- a new pool re-authenticates every user (password hashes not bulk-exportable) and kills every refresh token. **Do not recreate the pool.**

## Staged plan

- **Stage 0 -- Cosmetic brand + domains** (the now-track). New `trid3nt` CNAME + ACM cert on the SAME CloudFront `d125yfbyjrpbre` (additive); new Cognito hosted-UI domain alias on the SAME pool; pool DISPLAY-name edit; EC2 Name tags; in-app brand strings/logo. Zero downtime. NATE: DNS/cert + Vercel env.
- **Stage 1 -- In-code string IDs** (~1 day, zero user impact). testids, `__grace2*` globals, MapLibre/keyframe IDs, logger names. Find/replace in LOCKSTEP with tests in one commit (no shim possible/needed).
- **Stage 2 -- Python packages + CLI + on-box + SSM doc** (~2-3 days, highest dev payoff). `grace2_agent`/`grace2_contracts` codemod (~716 sites) behind a `sys.modules` re-export shim for one release; `trid3nt-agent.service` + `grace2-agent.service` symlink; `/opt/grace2 -> /opt/trid3nt` symlink; new `trid3nt-runshell` SSM doc. Downtime = agent redeploy window. Runs under STANDING deploy auth; one end-ask before the new SSM-doc creation.
- **Stage 3 -- Stateless control plane (Batch/ECR/IAM)** (~2-3 days). Recreate-and-flip; no data. Build images into new ECR first, create queue/CEs/job-defs, flip env, redeploy. Riskiest sub-step = the hand-provisioned `grace2-agent-ec2` role reattach (verify on a wake cycle before deleting old). If Tier-2 stays `grace2`, IAM ARN patterns STAY grace2-scoped even on trid3nt-named roles.
- **Stage 4 -- DynamoDB prefix move** [MARGINAL -- recommend defer]. Create `trid3nt_*` + GSIs + PITR, dual-read/dual-WRITE shim, backfill (export-to-S3 + import-table), flip `GRACE2_DYNAMO_TABLE_PREFIX`, drop fallback after parity. Must flip the cold-read Lambdas + the IAM wildcard simultaneously. Zero downtime WITH the shim. Invisible to users.
- **Stage 5 -- SSM secrets + S3 buckets** [MARGINAL/NO -- strong defer]. Old runs bucket can NEVER be safely deleted (LayerURI pins) -- you pay the copy AND keep grace2 in old data forever. New runs bucket MUST get the same scoped CORS or it relapses the box-off no-layers incident (tests can't catch it -- fake fetch). Secrets: copy each SecureString + rewrite the stored `vault_ref`. Cache bucket is the only cheap one (content-addressed, start cold).
- **Stage 6 -- Browser storage keys** [identity keys only]. AUTH-CRITICAL: `grace2_cognito_refresh` (the only durable credential), `grace2.anonymous_user_id` (server keys anon Cases by it), `grace2.deletedCaseIds` (tombstones). Dual-read shim is MANDATORY (read OLD, write NEW, delete OLD 1-2 releases later) -- copy the proven template. Leave the ~13 cosmetic keys as the internal codename. `VITE_GRACE2_*` are build-time inlined -- rename read sites with a `?? VITE_GRACE2_X` fallback AND set new vars in Vercel in the SAME deploy or the app blanks.

## Top user-facing risks (why the shims are mandatory)

- **MASS LOGOUT** -- renaming `grace2_cognito_refresh` without the copy-forward shim signs out every returning user; Vercel deploys to 100% on push, no staged rollout. The documented cases-box-off logout incident, whole user base at once.
- **ANON CASES VANISH** -- renaming `grace2.anonymous_user_id` without the shim orphans every anon user's Cases.
- **STORED LAYERURIs 404** + **BOX-OFF NO-LAYERS RELAPSE** (missing CORS on the new runs bucket) + **ORPHANED API KEYS** (secrets prefix) + **AGENT 403** (IAM wildcard / `/opt` symlink) + **BLANK APP** (VITE env not set in Vercel) + **CI RED-BAR** (testids out of lockstep) + **WRITE-LOSS WINDOW** (snapshot-then-flip without dual-write).

## Effort

Full execution: ~3-4 focused agent-weeks + several NATE end-asks. **Recommended scope (Stages 0-3 + the 3 identity keys, defer 4 & 5): ~1.5 weeks** + 2-3 end-asks -- captures the entire developer-facing + brand payoff; the deferred Tier-2 data moves are pure-internal and independently greenlightable later, each reversible behind its dual-read shim.

## The one decision that sets scope

Must the AWS CONSOLE read `trid3nt` for operators (forcing the DynamoDB-prefix + S3-bucket data moves, Stages 4-5)? Or is `grace2` acceptable as the permanent internal AWS codename behind the `trid3nt` brand domain? The recommendation assumes the latter.
