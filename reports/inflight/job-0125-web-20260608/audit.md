# Audit: Secrets UX web — key entry UI + Tier-2 unlock indicator

**Job ID:** job-0125-web-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** web

**Required reads:**
- `packages/contracts/src/grace2_contracts/secrets.py` (Wave 1)
- `packages/contracts/src/grace2_contracts/ws.py` (Wave 1.5 — secrets registry)
- `web/src/ws.ts` + `web/src/App.tsx`

### Scope

Add the Secrets UX panel allowing users to enter Tier-2 API keys per Case (or user-scope).

1. **NEW `web/src/components/SecretsPanel.tsx`**: 
   - List existing secret records (no key value shown — only provider, label, last_used)
   - "Add secret" form: select provider (eBird/IUCN/Movebank/Copernicus CDS), enter key, optional label, scope toggle (this-Case vs user-wide), submit
   - Revoke button per existing secret
2. **Hooks to ws.ts**: listen for `secrets-list` envelopes; emit `secret-add` / `secret-revoke` envelopes
3. **Tier-2 unlock indicator**: a subtle pill/badge next to each Tier-2 tool reference in chat ("eBird (key required)") that's GREEN when a key exists, GRAY when not
4. **Empty state**: when no secrets exist, friendly text "Add a key to unlock Tier-2 data sources (eBird, IUCN Red List, Movebank, Copernicus)"
5. **Security**: key field uses `<input type="password">`; clear field after submit; NEVER log or persist client-side

**Tests** (Vitest):
- SecretsPanel renders empty state when no secrets
- Renders list of existing secrets with provider + label + revoke
- Add-secret form submission emits secret-add envelope with correct payload shape
- Revoke button emits secret-revoke envelope
- Tier-2 unlock indicator changes color based on secrets-list

**Live verification** (Playwright):
- Boot dev server; inject secrets-list with 1 fake record; verify panel renders
- Add a secret via UI; verify secret-add envelope captured by mock WS

### File ownership (exclusive)

- `web/src/components/SecretsPanel.tsx` (NEW)
- `web/src/SecretsPanel.test.tsx` (NEW)
- `web/src/ws.ts` — extend with secret event listeners (~25 lines)
- `web/src/App.tsx` — mount SecretsPanel (~15 lines additive)
- `web/src/components/Tier2UnlockBadge.tsx` (NEW — small)
- `reports/inflight/job-0125-web-20260608/`


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

