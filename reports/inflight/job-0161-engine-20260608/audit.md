# Audit: Cases backend dev fallback Persistence (MongoDB MCP not provisioned)

**Job ID:** job-0161-engine-20260608, **Sprint:** sprint-12-mega Wave 4.6, **Specialist:** engine/agent (Opus)

**Required reads:**
- `services/agent/src/grace2_agent/persistence.py` (Wave 1.5 job-0115)
- `services/agent/src/grace2_agent/server.py` Case-command handlers (Wave 2 job-0121)
- `packages/contracts/src/grace2_contracts/case.py`
- Agent log: `MCP not provisioned (set GRACE2_MONGO_MCP_STDIO=1 to enable); Persistence singleton remains unbound`

### Why

User can't make a Case via the UI. Backend log shows `Persistence singleton remains unbound`. The MongoDB MCP server isn't provisioned locally; setting `GRACE2_MONGO_MCP_STDIO=1` would enable stdio mode but still requires a Mongo instance.

For LOCAL DEV: implement a **file-backed Persistence dev fallback** that uses simple JSON files in `~/.grace2/dev_persistence/` (one JSON per collection: `projects.json`, `sessions.json`, `users.json`, `secrets_refs.json`, `audit_log.json`). When MongoDB MCP is provisioned, real MCP path takes over; when not, dev fallback engages automatically.

### Scope

1. **Auto-detect**: at startup, if MongoDB MCP not provisioned AND `GRACE2_DEV_PERSISTENCE=1` (or default-on for local-only): instantiate `FilePersistence` instead of failing.
2. **FilePersistence class**: implements same interface as `Persistence` but persists to JSON. Atomic file writes (write to tmp, then rename). Simple per-collection lock for concurrent safety.
3. **Wire into server**: same Case-command handlers work; just point at FilePersistence.
4. **Tests**: round-trip Case create/select/rename/delete via FilePersistence.
5. **Live test**: with the running agent (restart after fix lands), create a Case from the UI; verify JSON file in `~/.grace2/dev_persistence/projects.json` updates.

### File ownership

- `services/agent/src/grace2_agent/persistence.py` (add FilePersistence + auto-detect)
- `services/agent/src/grace2_agent/main.py` (instantiation logic)
- `services/agent/tests/test_file_persistence.py` (NEW)
- `reports/inflight/job-0161-engine-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 4.6 job's exclusive files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. Geographic-correctness gate (job-0086): pixel-level evidence required.
2. Kickoff-front-loaded design: execute scope, surface OQs.
3. UX language discipline: no internal terms in user-facing surfaces.
4. Pre-commit: `git pull --rebase` before commit.

### Acceptance criteria

- [ ] Deliverables landed per scope
- [ ] Live verification per kickoff
- [ ] No FROZEN edits; single commit prefix; co-author line
- [ ] Returns commit SHA + outcome + headline + evidence + OQs

