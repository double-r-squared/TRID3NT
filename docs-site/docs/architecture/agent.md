# Agent Process

The agent is the core orchestrator of every live session. It holds the WebSocket connection,
drives LLM turns, dispatches tools, and submits solver jobs to AWS Batch.

Source files: `services/agent/src/grace2_agent/server.py`, `services/agent/src/grace2_agent/`,
`services/agent/src/grace2_agent/tools/solver.py`.

---

## Process architecture

```
Agent Fargate Task (2 vCPU / 8 GB)
 |
 +-- asyncio event loop
      |
      +-- WebSocket server :8765   (client connections via broker)
      +-- HTTP server :8766        (health probe, catalog endpoint)
      |
      +-- Per-connection coroutine
           |
           +-- auth handshake (auth-token frame)
           +-- session-resume handler (replay state)
           +-- user-message handler
                |
                +-- Bedrock converse_stream (tool_use loop)
                     |
                     +-- Tool dispatch (~160 registered tools)
                          |
                          +-- Lightweight tools: run in-loop
                          +-- Heavy sync tools: asyncio.to_thread
                               (_ALWAYS_OFFLOAD_SYNC_TOOLS set, 38 tools)
                          +-- Solver tools: submit to AWS Batch
                               (off-loop poll, DescribeJobs every 10 s)
```

---

## Startup and tool registry

At startup the agent:
1. Imports ~130-160 tool modules (eager import; several pull in rasterio/GDAL/numpy).
2. Registers tools via `register_tool` decorators -- each tool has `name`, `description`, `schema`,
   `cacheable`, `ttl_class`, `source_class`, `primary_category`.
3. Boots the asyncio WS + HTTP servers.
4. Runs cold-view backfill (populates S3 snapshot for any case with no existing snapshot).
5. Starts the idle-exit monitor (30 min, `os._exit(0)`).

**Tool count:** approximately 160 registered tools spanning data fetch, engine workflows,
spatial analysis, visualization, and utility categories.

**Tool categories (primary_category values):** `data_fetch`, `engine_workflow`, `spatial_analysis`,
`visualization`, `utility`, `credentials`, `case_management`.

**Memory floor:** GDAL alone contributes ~300-500 MB RSS. The google-genai SDK is imported at
module level (used as a types lingua franca for Bedrock adapter compatibility) even when running
Bedrock. This is tracked as a Phase-4 diet item.

---

## LLM integration

**Provider:** AWS Bedrock (`MODEL_PROVIDER=bedrock`, default)
**Default model:** `us.anthropic.claude-sonnet-4-6`
**Selectable models:** Haiku, Nova (via client model-selector in Settings)
**Adapter:** `services/agent/src/grace2_agent/bedrock_adapter.py`

The legacy `google-genai` / Vertex Gemini path in `adapter.py` is retained as a dormant, reversible
seam. The `google-adk` dep is decommissioned; `register_with_adk` is dead/uncalled.

**Tool retrieval mode:** controlled by env `GRACE2_TOOL_RETRIEVAL`:
- `off` (default): all tools sent in every turn
- `shadow`: RAG retrieval run but result not enforced
- `enforce`: RAG top-k selection enforced

---

## Heartbeats and keepalive

- **Server -> client DATA heartbeat:** every 12 s per connected session. This satisfies the client's
  10 s pong deadline (see WS Protocol page).
- **Agent idle-exit monitor:** after 30 min of no tool calls, `os._exit(0)`.
- **Cold provision keepalive:** broker sends DATA-frame keepalives to the client during the ~40-48 s
  cold provision to prevent the client's pong watchdog from force-reconnecting.

---

## Durable state (survives process death)

All durable state is external -- in DynamoDB and S3. A process death loses only the in-flight LLM
turn; every Batch job runs independently and completes to S3 regardless.

| State | Storage | Key / table |
|---|---|---|
| Cases | DynamoDB `trid3nt_cases` | PK `_id`, GSI `user_id-index` |
| Chat history | DynamoDB `trid3nt_chat` | per-case |
| Sessions | DynamoDB `trid3nt_sessions` | session -> user mapping |
| Users | DynamoDB `trid3nt_users` | PK `_id`, GSI `firebase_uid-index` (legacy name) |
| Secrets (vault) | DynamoDB `trid3nt_secrets` | per-user soft-revokable |
| Telemetry | DynamoDB (telemetry table) | per-tool invocation records |
| Case snapshot (cold view) | S3 `grace2-hazard-runs-*/case-views/<case_id>/` | JSON layer refs |
| Case manifests | S3 `grace2-hazard-runs-*/case-manifests/` | per-case published manifest |
| Run outputs | S3 `grace2-hazard-runs-*/runs/<run_id>/` | completion.json + COG URIs |
| Published COGs | S3 `grace-2-hazard-prod-cog` | `/cog/` prefix; served via TiTiler |
| QGS project files | S3 `grace-2-hazard-prod-qgs` | per-case `.qgs` files |

### In-memory state (lost on crash)

- Pending confirmation Futures (`_PENDING_CONFIRMATIONS`, `_PENDING_CREDENTIALS`,
  `_PENDING_REGION_CHOICES`, `_PENDING_SPATIAL_INPUTS`)
- Live-turn registry (in-flight LLM turn context)
- Session-to-case pointer (rehydrated from DynamoDB on session-resume)

---

## Confirmation gates

Before any high-consequence action the agent pauses and sends a wire envelope requesting user
confirmation. Three gate types:

| Gate | Env | Tools |
|---|---|---|
| **Solver confirm** | `SOLVER_CONFIRM_TOOLS` set | `run_model_groundwater_contamination_scenario`, `run_model_contamination_affected_fields`, `run_model_flood_scenario`, `run_model_flood_habitat_scenario`, `run_swmm_urban_flood`, `run_seismic_hazard_psha` |
| **Fetch confirm** | `FETCH_CONFIRM_TOOLS` set | `fetch_dem`, `fetch_topobathy` |
| **Payload warning** | Size threshold | Any tool whose args exceed the size limit |
| **Code exec** | `code-exec-request` frame | Before sandbox dispatch |

Decision options: `proceed`, `cancel`, `narrow_scope` (for solver/fetch/payload); `proceed`, `cancel`
(for code exec). `narrow_scope` includes `revised_args` in the wire frame.

---

## Cancellation (Invariant 8)

Cancellation is first-class: end-to-end within 30 s. The cancel path:

1. Client sends `cancel` frame.
2. Agent's `CancelledError` propagates from poll sleep -> `_terminate_batch_job` / `_request_local_cancel` -> re-raise.
3. `pipeline-state(cancelled)` emitted to client within NFR-R-3 30 s.

Per-turn `ContextVar` tracks in-flight Batch jobs so cancel terminates the Batch job (orphan-job guard).

---

## Heavy-compute offload status

See `reports/design/heavy-compute-offload-2026-07-02.md` for the full design.

| Engine | Build | Solve | Postprocess | Status |
|---|---|---|---|---|
| SFINCS coastal quadtree | Worker (`sfincs_deckbuilder`) | Worker | Worker | Fully offloaded |
| SFINCS pluvial | Worker (`sfincs-build`) | Worker | Worker | Offloaded 2026-07-03 |
| MODFLOW (all archetypes) | Worker (`modflow-build`) | Worker | Worker | Offloaded 2026-07-03 |
| GeoClaw | In-agent | Worker | Worker (manifest-gated, in-agent fallback) | Solve/post offloaded |
| PySWMM | In-agent | Worker | Worker (manifest-gated, in-agent fallback) | Solve/post offloaded |
| OpenQuake | In-agent | Worker | Worker (manifest-gated, in-agent fallback) | Solve/post offloaded |
| Landlab | In-agent | Worker | Worker (manifest-gated, in-agent fallback) | Solve/post offloaded |
| SWAN | In-agent | Worker | Worker (manifest-gated, in-agent fallback) | Solve/post offloaded |

**Remaining in-agent heavy work:** the 38-tool `_ALWAYS_OFFLOAD_SYNC_TOOLS` fetcher set
(rasterio/xarray, 50-200 MB per active thread), vector densify (shapely), geopandas county fetch,
and in-agent postprocess fallbacks.

**Agent size target:** from 8 GB toward 4 GB after Phase-4 diet (lazy imports + fetcher offload).
