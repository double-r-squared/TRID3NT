# Stage-3 prep — agent restart runbook

**Job:** stage-3-prep (sprint-13 Stage 3 LIVE GATE prep — not a numbered job)
**Specialist:** testing
**Date:** 2026-06-09
**Outcome:** PASS — agent restarted on all sprint-13 code; WS + web + catalog + mf6 verified live.

---

## Why the restart was mandated

The process running on `:8765` before this job was launched **2026-06-09 13:06**
(PID 2441029, ~10h17m uptime) and predated the sprint-13 Stage 1/2/M4 landings.
Its live tool catalog (`evidence/catalog_OLD_baseline.json`) reported **76 tools**
and was MISSING all four sprint-13 tools:

| Tool (registered name) | OLD process | NEW process |
|---|---|---|
| `run_modflow_job` | absent | present |
| `code_exec_request` | absent | present |
| `run_model_groundwater_contamination_scenario` | absent | present |
| `run_model_nws_flood_event_scenario` | absent | present |

The live-drive memory mandates restarting against the latest code, so the old
process was killed (accepted browser-session collateral) and relaunched.

---

## How the agent is launched (fact, from inspection)

- Entry point: `python -m grace2_agent.main` (console script `grace2-agent`).
- The OLD process ran `.venv/bin/python -m grace2_agent.main` with **cwd =
  `/home/nate/Documents/GRACE-2/services/agent`** (NOT the Makefile's
  `make run-agent`, which uses `.venv-agent`). The active venv is `.venv`
  (uv-managed CPython 3.12), editable-installed against the live source tree —
  so it always runs current code.
- WS server binds `127.0.0.1:8765` (override `GRACE2_AGENT_PORT`).
- Read-only tool-catalog HTTP endpoint binds `127.0.0.1:8766`
  (`GET /api/tool-catalog`; override `GRACE2_AGENT_HTTP_PORT`).
- Adapter env defaults (used even when env vars unset):
  `GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod`, `GOOGLE_CLOUD_LOCATION=us-central1`,
  `GOOGLE_GENAI_USE_VERTEXAI=True`, model `gemini-2.5-pro`
  (override `GRACE2_GEMINI_MODEL`). Auth: ADC.

## OLD process environment (replicated)

The OLD process carried only the inherited interactive-shell env — it had
**no** `GOOGLE_*`, `GRACE2_*`, `VERTEX*`, or `GEMINI*` vars set explicitly
(it relied on the adapter defaults above). Full dump:
`evidence/old_process_environ.txt`. There were therefore no secret/Vertex/Gemini
vars to preserve verbatim; the relaunch sets the mandated vars explicitly.

---

## Relaunch command (the one that is now live)

Run from `services/agent/`. Secrets: none on the command line (ADC handles
auth via `~/.config/gcloud/application_default_credentials.json`; the MongoDB
SRV is in Secret Manager and is only fetched when `GRACE2_MONGO_MCP_STDIO=1`,
which is NOT set in dev — the agent uses the file-backed dev Persistence
fallback). No placeholders needed because no key/token literal appears.

```bash
cd /home/nate/Documents/GRACE-2/services/agent

nohup env \
  GRACE2_MODFLOW_LOCAL=1 \
  GRACE2_MF6_BIN=/tmp/mf6 \
  GOOGLE_GENAI_USE_VERTEXAI=True \
  GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \
  GOOGLE_CLOUD_LOCATION=us-central1 \
  PATH="/home/nate/tools/google-cloud-sdk/bin:/home/nate/.local/bin:/home/nate/.nvm/versions/node/v20.20.2/bin:/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games" \
  .venv/bin/python -m grace2_agent.main > <evidence>/agent_startup.log 2>&1 &
disown
```

### Env rationale (each var)

| Var | Value | Why |
|---|---|---|
| `GRACE2_MODFLOW_LOCAL` | `1` | Case 2 local-mf6 path — run the MODFLOW deck against the local `mf6` binary instead of Cloud Workflows (docker daemon unreachable on this box). |
| `GRACE2_MF6_BIN` | `/tmp/mf6` | Pins the verified `mf6 6.5.0` binary (the path job-0227/0228 evidence used). Without this the local runner defaults to `mf6` on PATH (not present), so pinning is required. |
| `GOOGLE_GENAI_USE_VERTEXAI` | `True` | Vertex AI client path (no API-key path). Matches adapter default; set explicitly per kickoff. |
| `GOOGLE_CLOUD_PROJECT` | `grace-2-hazard-prod` | Vertex + Secret Manager project. |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | Vertex region. |
| `PATH` | gcloud SDK first | `/home/nate/tools/google-cloud-sdk/bin` on PATH per corrected env facts so any gcloud calls (Cloud Workflows for SFINCS) resolve. Keeps the OLD process's node/.local paths too. |

### Vars deliberately NOT set (dev defaults preferred)

- `GRACE2_MONGO_MCP_STDIO` — left unset → file-backed dev Persistence at
  `~/.grace2/dev_persistence/` (the fresh-clone default; Cases + chat persist
  without Atlas/MCP). Set to `1` only when wiring live MongoDB MCP.
- `GRACE2_GEMINI_MODEL` — left unset → `gemini-2.5-pro` (adapter default).

---

## Verification performed (all live)

1. **Old process killed cleanly.** `kill -TERM 2441029` → exited in 2s;
   ports 8765/8766 freed. (`evidence/` — see report.)
2. **New process up.** PID 2805891. Startup log
   (`evidence/agent_startup.log`): `tool registry loaded: 89 tool(s)`,
   `server listening on 127.0.0.1:8765`, `tool-catalog HTTP server listening
   ... port=8766`, model `gemini-2.5-pro`, project `grace-2-hazard-prod`,
   location `us-central1`, dev Persistence bound.
3. **Mandated env confirmed** in `/proc/2805891/environ`:
   `GRACE2_MODFLOW_LOCAL=1`, `GRACE2_MF6_BIN=/tmp/mf6`,
   `GOOGLE_GENAI_USE_VERTEXAI=True`, `GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod`,
   `GOOGLE_CLOUD_LOCATION=us-central1`, PATH with gcloud SDK first.
4. **WS handshake (real frame).** Sent a real `auth-token` envelope on
   `ws://127.0.0.1:8765`; received a valid `auth-ack`
   (`is_anonymous: true`, `tier: free`). `evidence/ws_handshake.log`.
   NOTE: the FIRST frame must carry a real ULID `session_id` (the web client
   always does) — a `session_id: null` first frame trips a server crash path
   (`Envelope.session_id` is a required string). Not a regression; matches how
   the real client behaves.
5. **Web + live WS in browser (Playwright, headless, NO inject seams).**
   `http://localhost:5173` loads (title "GRACE-2 — Hazard Modeling Workbench
   (M1 stub)"); the live WS emitted `auth-ack` + `session-state` + `case-list`
   frames into the browser. `evidence/web_loaded_5173.png`.
6. **Catalog = 89 tools, 4 sprint-13 tools present.**
   `evidence/catalog_NEW_post_restart.json` +
   `evidence/tools_list_NEW.txt`.
7. **mf6 binary present + runs.** `/tmp/mf6 --version` → `mf6: 6.5.0
   05/23/2024` (99 MB, executable). No re-download needed.

## Discrepancy surfaced (not a failure)

- Kickoff said "registry reports **85** tools"; the live count is **89**.
  This is forward expansion (more fetchers landed since the kickoff was
  written — e.g. `fetch_hrrr_forecast`, `fetch_fema_nfhl_zones`,
  `fetch_usace_*`, `fetch_noaa_*`), not a regression. All four required
  sprint-13 tools are present.
- The kickoff named `model_groundwater_contamination_scenario` and
  `model_nws_flood_event_scenario`; the registered names are
  **`run_model_groundwater_contamination_scenario`** and
  **`run_model_nws_flood_event_scenario`** (the `run_model_` workflow-wrapper
  prefix, consistent with `run_model_flood_scenario` / `run_model_flood_habitat_scenario`).
  Same tools, prefixed names.

---

## Restart cheat-sheet (for the live-gate jobs that follow)

- Health check: `curl -s http://127.0.0.1:8766/api/tool-catalog | python3 -c "import sys,json;print(len(json.load(sys.stdin)['tools']))"` → 89.
- WS alive: `ss -ltnp | grep ':8765'`.
- Agent PID: `ps -ef | grep 'grace2_agent.main' | grep -v grep`.
- If the agent dies, re-run the relaunch block above from `services/agent/`.
