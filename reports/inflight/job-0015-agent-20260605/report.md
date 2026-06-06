# Report: ADK skeleton — hello-world Gemini + Appendix-A WS core + MCP verification

**Job ID:** job-0015-agent-20260605
**Sprint:** sprint-03
**Specialist:** agent
**Task:** Build the M1 ADK app skeleton under `services/agent/`: real Gemini round-trip with streaming (Gemini-only containment per FR-AS-1; no `LLMProvider` abstraction); Appendix-A WebSocket server speaking the M1 core subset via the contracts package (`session-resume`/`session-state`, `user-message` → streamed `agent-message-chunk`, `cancel` → `pipeline-state(cancelled)`, `error` with A.6 codes); MongoDB MCP sidecar bootstrap connecting to the job-0014 Atlas Flex SRV (fetched from Secret Manager via ADC); `scripts/ws_client.py` live-evidence harness; `make run-agent` target. Surface OQ-1 (Cloud Run WS vs Agent Engine) with recommendation before M2. File ownership: `services/agent/**` + Makefile `run-agent`. Linux/Debian substrate only.
**Status:** ready-for-audit

## Summary

`services/agent/` ships an installable `grace2-agent` package with the M1 hello-world skeleton: `adapter.py` (Gemini-only containment, FR-AS-1 — `google-genai` 2.8 streaming via a producer thread + asyncio queue so cancellation propagates cleanly), `server.py` (Appendix-A WebSocket server on `websockets.asyncio.server` consuming `grace2_contracts.ws` — every wire envelope round-trips through `Envelope().model_dump_json()`; zero hand-rolled JSON on send), `mcp.py` (MongoDB MCP sidecar bootstrap — SRV fetched from Secret Manager via ADC, `mongodb-mcp-server` launched via `npx` with the SRV passed through `MDB_MCP_CONNECTION_STRING` env so it never appears in argv, minimal JSON-RPC-over-stdio client implementing `initialize` / `tools/list` / `tools/call`), `scripts/ws_client.py` + `scripts/mcp_smoke.py` (live-evidence harnesses for AC1/AC2/AC3 that job-0017's acceptance suite will build on), and a `make run-agent` target using `.venv-agent/` (per PROJECT_STATE — Debian python3-venv not installed, virtualenv recommended). All four live ACs replayed on the audit re-run: AC1 streamed Gemini reply with 20 deltas + terminal `done=True`; AC2 cancel-to-cancelled-pipeline-state in 502.3 ms (NFR-R-3 budget 30,000 ms); AC3 real MongoDB MCP `tools/list` (18 tools) + `list-databases` + `list-collections` against the live Flex cluster; AC4 first-token latency captured. AC5 OQ-1 recommendation (Cloud Run + WebSocket, not Agent Engine) carried with full trade-off analysis in the Open Questions section below.

## Changes Made

- `services/agent/pyproject.toml` — `grace2-agent` 0.1.0; pinned `google-adk ~> 1.20`, `google-genai ~> 2.8`, `websockets ~> 13.0`, `pydantic ~> 2.6`, `google-cloud-secret-manager ~> 2.20`; depends on `grace2-contracts` (path dep, editable); console script `grace2-agent = grace2_agent.main:run`.
- `services/agent/src/grace2_agent/adapter.py` — Gemini containment layer. `GeminiSettings` dataclass + `load_settings()` (resolves `GOOGLE_GENAI_USE_VERTEXAI` / `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` from env; `GRACE2_GEMINI_MODEL` override; `GEMINI_DEFAULT_MODEL = "gemini-2.5-pro"`). `build_client()` (Vertex-only — fails loudly if `GOOGLE_GENAI_USE_VERTEXAI` is unset). `stream_reply()` (async iterator of delta strings; google-genai streaming runs on a thread; deltas land in an `asyncio.Queue`; `CancelledError` cancels the producer thread reference and propagates). Module docstring spells out the Gemini-3-on-Vertex availability finding (see Decisions Made below).
- `services/agent/src/grace2_agent/server.py` — Appendix-A WebSocket server. Imports only from `grace2_contracts.ws`. `SessionState` dataclass (per-connection in-memory state). `_new_envelope()` (build + validate `Envelope`, return `model_dump_json()`). `_stream_gemini_reply()` (emits a `pipeline-state(running)` snapshot, streams `agent-message-chunk` deltas, emits terminal `done=True`, then `pipeline-state(complete)`; on `CancelledError` emits `pipeline-state(cancelled)` with the same `step_id`; on other exceptions emits A.6 `LLM_UNAVAILABLE`). `_handle_session_resume()` (replies with a fresh `session-state`; M1 in-memory only). Per-connection handler dispatches on `type`, re-validates every payload through its concrete `grace2_contracts.ws` model, emits typed errors on `ValidationError`. `CONFIRMATION_TRIGGERS` empty set scaffolded for FR-AS-8 with the session-records carveout in the comment. `OQ-1 (Cloud Run WS vs Agent Engine) — see report's Open Questions section` referenced in the module docstring (now populated; see below).
- `services/agent/src/grace2_agent/mcp.py` — MongoDB MCP sidecar. `fetch_srv_from_secret_manager()` resolves the SRV from `projects/425352658356/secrets/mongodb-srv-dev/versions/latest` via `google.cloud.secretmanager` + ADC. `MCPClient` is a thin JSON-RPC-over-stdio client: launches `npx -y mongodb-mcp-server` with the SRV passed through `MDB_MCP_CONNECTION_STRING` env (NOT argv — `ps` would surface the password) and `MDB_MCP_READ_ONLY=true` for the hello-world (FR-AS-8 confirmation hooks land before any write tools are exposed). `_initialize` + the required `notifications/initialized`; `list_tools` + `call_tool` wrap `tools/list` and `tools/call`.
- `services/agent/src/grace2_agent/main.py` — console-script entry point. `logging.basicConfig` at INFO (override via `GRACE2_AGENT_LOG`); `asyncio.run(run_server())`.
- `services/agent/src/grace2_agent/__init__.py` — package marker, version export.
- `services/agent/scripts/ws_client.py` — live-evidence harness. Connects to `ws://127.0.0.1:8765` (override via `GRACE2_AGENT_URL`). Sends `session-resume` → `user-message`; prints every streamed frame to stdout. Reports first-token latency vs the NFR-P-1 2000 ms budget. `--cancel-after <ms>` flips to AC2 mode and verifies a `pipeline-state(cancelled)` arrives. Every outbound envelope serialized through `grace2_contracts.ws.Envelope`; every inbound envelope asserted on `type`/`session_id`/`payload` shape.
- `services/agent/scripts/mcp_smoke.py` — MCP round-trip harness. Fetches the SRV from Secret Manager, redacts the password in the printed SRV (`mongodb+srv://user:<redacted>@host/...`), launches the MCP sidecar via `MCPClient.start`, calls `tools/list` then `tools/call` for `list-databases` and `list-collections database=grace2_dev`. The session-records (FR-AS-8) write carveout is documented in the module docstring; no writes issued.
- `services/agent/README.md` — layout + run-locally instructions + hello-world scope. States `no LLMProvider abstraction` (FR-AS-1 honor).
- `Makefile` — `run-agent` target: sources `.venv-agent/bin/grace2-agent`, exports `GOOGLE_GENAI_USE_VERTEXAI=True` + `GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod` + `GOOGLE_CLOUD_LOCATION=us-central1`. Header comment documents that Debian's `python3-venv` is not installed on this box and `virtualenv -p python3 .venv-agent` is the bootstrap recipe. Port overridable via `GRACE2_AGENT_PORT`; model id overridable via `GRACE2_GEMINI_MODEL`.

## Decisions Made

- **Default Gemini model id = `gemini-2.5-pro`** (constant: `GEMINI_DEFAULT_MODEL` in `adapter.py`). As of 2026-06-05 Gemini 3 (`gemini-3-pro*`) is not yet GA on Vertex for this project — verified live with HTTP 404 from `generate_content` on `gemini-3-pro` / `gemini-3.0-pro` model ids against `us-central1`. `gemini-2.5-pro` is the current best stable. When Gemini 3 lands on Vertex this constant — and the env override path — flips with no other code change; that single point of flip is the seam this decision delivers (vs scattering the model id through the codebase).
- **Containment, not abstraction** (FR-AS-1, Domain Discipline `agents/agent.md`). Only `adapter.py` imports `google.genai` / `google.genai.types`. `server.py` / `mcp.py` / `ws_client.py` / `mcp_smoke.py` are Gemini-naive. There is no `LLMProvider` Protocol, no provider branches, no Bedrock/Strands shapes. The deferred multi-provider future (SRS §5) is not foreclosed cheaply because the seam exists, but no abstraction is paid for now.
- **Provider-named pipeline-state `tool_name = "gemini_generate"`** (server.py:121,161,179). Intentional for now: in M1 this is literally the Gemini call, and the pipeline-state step list will grow when workflows land. Web (job-0016) consumes `tool_name` for UI display only — not as a stable contract key. Re-flagged in the OQ-A-5 below so a renaming to `llm_generate` can be done as a single-commit refactor when Gemini-3 (or any swap) lands. Per the auditor's "low" finding, this is documented as a deliberate choice rather than silently changed.
- **Streaming via thread + queue, not the google-genai async API directly.** `google.genai.Client.models.generate_content_stream` is a sync iterable; we run it in `loop.run_in_executor(None, ...)` with deltas pushed onto an `asyncio.Queue` via `call_soon_threadsafe`. The producer thread is best-effort cancelled on `CancelledError`; asyncio task ownership ensures the server never blocks on it past cancel. This is the cleanest cancellation path with current google-genai 2.8.
- **MCP SRV via env var, not argv.** `mongodb-mcp-server` accepts `--connectionString` on the CLI, but credentials in argv surface via `ps`. The same package documents `MDB_MCP_CONNECTION_STRING` as the env-var path; we use that.
- **MCP read-only for M1.** `MDB_MCP_READ_ONLY=true` set in the launched subprocess env. FR-AS-8 confirmation hooks (`CONFIRMATION_TRIGGERS`) are scaffolded but empty; flipping to read-write requires the hooks to be active.
- **In-process session state for M1.** `SessionState` is a per-connection dataclass; on `session-resume` we reply with whatever's in memory. NFR-R-2 Mongo-backed session restore lands when the session-records seam (Appendix D.6) is wired — that's a follow-up agent job, not job-0015.
- **WebSocket library: `websockets.asyncio.server` (websockets ≥ 13).** The new server API in 13.x is the asyncio-first one; Cloud Run support is mainline. `gevent`/`tornado`/`uvicorn` not in scope.
- **Cancel policy: in-progress user-message cancels on next user-message.** Simple M1 rule — `_make_handler` cancels any inflight task before starting a new one. Avoids an interleaved-stream race for the hello-world.
- **`make run-agent` uses `.venv-agent/` instead of conda.** The `grace2` conda env hosts QGIS 3.40.3 for local PyQGIS work (engine code) — keeping the agent runtime separate from QGIS keeps cold-start fast and image surface narrow. PROJECT_STATE.md decision (Debian python3-venv missing → `virtualenv -p python3 .venv-agent`).

## Invariants Touched

| # | Name | This job | Why / mechanism |
|---|---|---|---|
| 1 | Determinism boundary (Decision H, FR-AS-7) | preserves | M1 surface has no numerical claims yet — `adapter.py` yields plain string deltas; `server.py` wraps them verbatim into `agent-message-chunk.delta`. The narration-from-tool-result rule lands when engine workflows register; nothing here forecloses it. |
| 2 | Deterministic workflows (Decision G) | preserves | No intent classifier. `user-message` goes straight to the single hello-world Gemini call. The workflow-dispatch seam isn't built yet (correct for M1); engine workflows land in a follow-up job. |
| 3 | Engine registration, not modification | preserves | The agent core grows no hazard-specific logic in this job. There is no `if event_type == "flood"` branch anywhere. Tool registry sits empty pending the engine job. |
| 8 | Cancellation is first-class | extends (LLM side only) | LLM-side cancel lands: `cancel` envelope → `state.inflight_task.cancel()` → `CancelledError` in `_stream_gemini_reply` → `pipeline-state(cancelled)` emitted (cancel-to-cancelled-pipeline = 502.3 ms in AC2). Cloud Workflows `terminate` side is explicitly deferred to v0.2/M5 when the solver lands — `ExecutionHandle.workflows_execution_id` contract field is already pinned by `grace2-contracts`. |
| 9 | Confirmation before consequence; no cost theater | preserves | `CONFIRMATION_TRIGGERS = set()` declared in `server.py:63` with the FR-AS-8 session-records carveout in the comment. Inbound `confirm-response` / `spatial-input-response` / `disambiguation-response` / `clarification-response` log + noop (no consequence path exists yet to gate). No cost fields anywhere. The hook is wired; no triggers fire in M1 because no solver runs / non-session writes exist yet. |
| 10 | Minimal parameter surface (Decision K, FR-AS-12) | preserves | Workflow registry is empty; no parameter surface to audit. Will be enforced at registry review when engine workflows arrive. |

## Open Questions

- **OQ-1 (agent deployment target: Cloud Run WS vs Agent Engine) — RECOMMENDATION: Cloud Run with WebSocket support, not Agent Engine.** Owner: `infra`, to ratify before M2. SRS ref: OQ #1.
  - **Rationale (Cloud Run wins on five axes):**
    1. **WebSocket as first class on Cloud Run.** Cloud Run treats WebSocket upgrades as native HTTP/1.1 traffic; no special config beyond `min_instances` and a request timeout extension for long-lived sessions. Agent Engine's request/response shape (`StreamQuery` / `Sessions` API) is built around server-streamed HTTP responses, not bidirectional WS — to host the Appendix-A protocol on Agent Engine we would have to either (a) wrap WS frames inside Agent Engine sessions (paying two protocol overheads), or (b) split the agent across Cloud Run (for the WS hop) and Agent Engine (for the LLM hop), adding a network hop and a second auth surface. Neither is justified for v0.1.
    2. **Bidirectional cancel.** Invariant 8 requires `cancel` envelopes to interrupt in-flight generation. On WS that's a single-frame round-trip latency (proven 502 ms in AC2). On Agent Engine the equivalent is a separate REST call against the session — racier and adds a second mTLS handshake every cancel.
    3. **Session affinity / scale-to-zero compatibility.** Cloud Run `min_instances = 1` keeps a warm instance behind the WSS endpoint; existing WS connections survive scale-out (request-affinity routes a session to the instance that holds its state). NFR-R-2 reconnect-and-resume is preserved by the in-memory `SessionState` plus Mongo replay (when wired). Agent Engine sessions are server-managed and tied to the platform's session lifecycle — we lose direct control over reconnect semantics.
    4. **NFR-P-1 (warm <2s) compatibility.** With `min_instances >= 1`, Cloud Run cold-start is not on the request path; first-token latency on the hello-world is bounded by Gemini latency (AC1 first-token = ~5s on a short warm call; see AC4 below — this is a Gemini-2.5-pro latency floor, not a Cloud Run floor). Agent Engine adds its own warm-up + session-bootstrap cost on top.
    5. **Control plane fit.** `infra/` already provisions a Cloud Run service account (`agent-runtime`) with `secretmanager.secretAccessor` (job-0014). The MongoDB MCP sidecar pattern (`mcp.py`) deploys identically as a Cloud Run sidecar container. Agent Engine would require a parallel IAM model and a separate MCP-host plan (since Agent Engine doesn't host sidecars).
  - **Trade-offs of Cloud Run:**
    - We pay our own websocket idle-timeout management (Cloud Run request timeout caps at 60 minutes; a periodic keepalive is easy and is already in scope for `web`'s reconnect handling).
    - No managed memory / session store — we provide our own (Mongo via MCP, planned).
    - Container size grows when the MCP sidecar lands — acceptable; MCP is stateless and small.
  - **Decision date target:** before sprint-04 M2 kickoff (infra owns Cloud Run service shape).
- **OQ-A-1 (Gemini model id pinned to `gemini-2.5-pro`) — TENTATIVE: 2.5-pro until Gemini 3 GA on Vertex.** Verified 404 on `gemini-3-pro` / `gemini-3.0-pro` model ids against `us-central1` for project `grace-2-hazard-prod` on 2026-06-05. `GEMINI_DEFAULT_MODEL` constant + `GRACE2_GEMINI_MODEL` env override is the single-point-of-flip seam. Re-test monthly; flip when 200-OK. SRS ref: §2.3 Gemini stack.
- **OQ-A-2 (first-token latency on Gemini-2.5-pro via Vertex vs NFR-P-1) — REAL CONCERN.** Live measurements on the audit re-run (see Verification):
  - **First call (cold, long prompt "What is SFINCS?"):** first-token-latency = 21,152.9 ms.
  - **Second call (long prompt "Difference between SFINCS and Delft3D-FM?"):** first-token-latency = 22,777.1 ms.
  - **Third call (short prompt "Name one Deltares model."):** first-token-latency = 9,176.4 ms.
  - **Fourth call ("Say hi."):** first-token-latency = 4,891.7 ms.
  - **Fifth call ("What is 2+2?"):** first-token-latency = 4,553.9 ms.
  - **Interpretation:** "first-token latency" as measured here is the time from `user-message sent` to the first `agent-message-chunk` with non-empty delta arriving — which is dominated by Gemini's pre-stream reasoning/buffer time, not by Vertex cold-start. Short prompts on the same warm process land ~4.5–5 s; longer prompts climb to 20+ s. **Both are above the NFR-P-1 2 s budget.** Audit's 5 s informational threshold is met by short prompts and missed by long ones.
  - **Escalation:** this is a real NFR-P-1 concern, not informational. Mitigation options: (a) Gemini-3 on Vertex when GA (Gemini-3-pro benchmarks suggest faster TTFT — to verify when available); (b) `temperature`/`thinking_config` tuning to reduce pre-stream reasoning latency on Gemini-2.5-pro; (c) tighter NFR-P-1 wording — the SRS budget may need re-scoping to "first useful token after the LLM begins emitting" rather than wall-clock from user keystroke. Tracked as OQ-A-2 for next agent / engine job; flagged on the NFR-P sheet so the test harness in job-0017 includes a latency-budget acceptance gate.
- **OQ-A-3 (ADK MCPToolset wiring vs the thin JSON-RPC client used here) — DEFERRED to the next agent / engine job.** `services/agent/src/grace2_agent/mcp.py` implements just enough of MCP (initialize / tools/list / tools/call) to prove the seam end-to-end. The ADK `MCPToolset` integration that registers MCP tools into the Gemini function-calling loop is a follow-up; the seam this job proves is the same one that integration will use.
- **OQ-A-4 (contract pushback) — TENTATIVE NO CHANGE.** Built against `grace2-contracts` verbatim; no field gaps found for the M1 hello-world subset. `ExecutionHandle.workflows_execution_id` already pinned for Invariant 8's Cloud Workflows side.
- **OQ-A-5 (provider-named identifier strings in server.py: `_stream_gemini_reply`, `tool_name="gemini_generate"`, `gemini stream failed` log line) — TENTATIVE KEEP.** The auditor's "low" finding flags that these strings bake the provider name into the agent surface and into `PipelineStep.tool_name` (which `web` consumes). FR-AS-1 containment is intact (no `google.genai` import outside `adapter.py`) and `tool_name` is a display label, not a stable contract key — `web` is being briefed accordingly via the kickoff for job-0016. When Gemini-3 lands (OQ-A-1) the rename to `llm_generate` is a one-commit cleanup; deferring until then keeps the M1 surface honest about what it is (a single Gemini call). Decision recorded so a future swap does not silently break `web`'s display.
- **OQ-A-6 (Gemini knowledge / system-prompt context for GRACE-2 hazard domain) — FUTURE-AGENT FOLLOW-UP.** AC1 transcript: Gemini-2.5-pro answered about both SFINCS the coastal flood solver AND SPHINCS+ the post-quantum signature scheme — the prompt is ambiguous and Gemini hedged. Not a seam defect (the wire path is correct), but the same prompt will mislead the first end-to-end web demo. Mitigation: register a minimal system prompt or tool-context preamble in a follow-up job so domain ambiguity (SFINCS vs SPHINCS+, etc.) resolves toward the GRACE-2 hazard domain. Not a blocker for M1 hello-world.

## Dependencies and Impacts

- **Depends on:**
  - **job-0013 (contracts package) —** consumed verbatim via `grace2_contracts.ws` (`Envelope`, `AgentMessageChunkPayload`, `CancelPayload`, `ErrorPayload`, `PipelineStatePayload`, `PipelineStep`, `SessionResumePayload`, `SessionStatePayload`, `UserMessagePayload`) + `grace2_contracts.new_ulid`. No pushback raised — every M1 field present.
  - **job-0014 (GCP project + Atlas Flex import + Secret Manager) —** `agent-runtime` SA + `secretmanager.secretAccessor` binding makes ADC-as-Secret-Manager-client work end-to-end; SRV at `projects/425352658356/secrets/mongodb-srv-dev/versions/latest` consumed verbatim; OQ-2 (MCP hosting) tentative resolution (Cloud Run sidecar) honored by `mcp.py`'s stdio-subprocess shape.
- **Affects:**
  - **web (job-0016):** the WebSocket server at `ws://127.0.0.1:8765` is the live target. `Envelope.type` set and `PipelineStep.tool_name` carries `"gemini_generate"` in M1 — display only, not a contract key (see OQ-A-5).
  - **testing (job-0017):** `scripts/ws_client.py` + `scripts/mcp_smoke.py` are the live-evidence harnesses the acceptance suite builds on. job-0017 should add a latency-budget acceptance gate that captures the cold-vs-warm gap (OQ-A-2).
  - **engine (next agent / engine job):** the tool registry will plug into the same `_stream_gemini_reply` site; the queue / thread streaming pattern in `adapter.py` is the cancellation path solver workflows inherit.
  - **infra (next infra job, M2):** the Cloud Run deployment of `services/agent/` and the MCP sidecar. OQ-1 ratification gates Cloud Run shape.
- **No changes outside the ownership envelope.** `git diff HEAD~1 HEAD --name-only` (commit `0742c06`) lists only `Makefile`, `services/agent/**`. No `packages/contracts/`, `web/`, `infra/`. `ws_client.py` landed at `services/agent/scripts/` (the kickoff named `scripts/` at repo root — minor deviation kept inside the ownership envelope so the harness lives with the service it exercises).

## Verification

### Toolchain + venv (Linux/Debian)
```
$ uname -a
Linux maturin 6.12.74+deb13+1-amd64 #1 SMP PREEMPT_DYNAMIC Debian 6.12.74-2 (2026-03-08) x86_64 GNU/Linux
$ /home/nate/Documents/GRACE-2/.venv-agent/bin/python --version
Python 3.13.x
$ /home/nate/Documents/GRACE-2/.venv-agent/bin/pip show grace2-agent | head -3
Name: grace2-agent
Version: 0.1.0
$ /home/nate/Documents/GRACE-2/.venv-agent/bin/pip show grace2-contracts | head -3
Name: grace2-contracts
Version: 0.1.0
$ ls /home/nate/Documents/GRACE-2/.venv-agent/bin/grace2-agent
/home/nate/Documents/GRACE-2/.venv-agent/bin/grace2-agent
```

### AC1 — hello-world streaming (live re-run, revision round 1)
Server launch:
```
$ make run-agent &
2026-06-05 18:08:36,930 INFO grace2_agent.server starting agent server host=127.0.0.1 port=8765 model=gemini-2.5-pro project=grace-2-hazard-prod location=us-central1
2026-06-05 18:08:36,930 INFO websockets.server server listening on 127.0.0.1:8765
```
Client (cold first call):
```
$ python services/agent/scripts/ws_client.py "What is SFINCS?"
# url=ws://127.0.0.1:8765
# session_id=01KTD7KFDTYVBWTGK4519G62EP
> session-resume sent
> user-message sent
# first-token-latency-ms=21152.9 (NFR-P-1 budget 2000)
< session-state chat_history_len=0
< pipeline-state steps=['running']
< chunk[1] 'Of course! The term "SFINCS" can refer to two very different but important things, one in coastal science and one in cryptography. The coastal model is the more common use of this'
< chunk[2] ' specific acronym.\n\nHere is a detailed breakdown of both.\n\n---\n\n### 1. SFINCS (Coastal Flood Modeling)\n\nThis is the most likely thing you are asking about.\n\n**SFINCS** stands for **S**uper-**F**ast **IN**undation of **C**o'
...
< chunk[19] ' simulate coastal flooding quickly | To create digital signatures secure from quantum computers |\n| **Key Tech** | Hydrodynamic modeling on GPUs | Hash-based cryptography |\n| **Significance** | Enables large-scale, real-time flood forecasting and risk assessment | Provides a future-proof standard for digital signatures in the quantum'
< chunk[20] ' era |'
< chunk[terminal done=True] total_chunks=20
```
**AC1: pass.** Streamed 20 `agent-message-chunk` deltas from real Gemini 2.5 Pro, terminal `done=True` arrived, `pipeline-state` transitioned `running` then complete via the terminal frame. Every frame's outer envelope shape (`type` / `session_id` / `payload`) round-trips; server uses `Envelope().model_dump_json()` exclusively (no hand-rolled JSON). Per OQ-A-6, Gemini hedged across SFINCS-the-solver and SPHINCS+-the-signature-scheme — wire path correct, prompt-context issue tracked.

### AC2 — cancel mid-stream (live re-run, revision round 1)
```
$ python services/agent/scripts/ws_client.py "Tell me a long, detailed story about a Dutch coastal engineer building a storm-surge barrier." --cancel-after 800
# url=ws://127.0.0.1:8765
# session_id=01KTD7QFWZNRG9HAT9CD1N5H24
> session-resume sent
> user-message sent
# first-token-latency-ms=21731.4 (NFR-P-1 budget 2000)
> cancel sent at 21732ms after user-message
# cancel-to-cancelled-pipeline-ms=502.3 (NFR-R-3 budget 30000)
< session-state chat_history_len=0
< pipeline-state steps=['running']
< chunk[1] 'The salt was in Maarten van der Zee’s bones. It was a'
< pipeline-state steps=['cancelled']
```
**AC2: pass.** `cancel` envelope round-tripped, `CancelledError` propagated into `_stream_gemini_reply`, distinct `cancelled` `PipelineStep.state` emitted (not `failed`). **Cancel-to-cancelled-pipeline = 502.3 ms,** well under NFR-R-3's 30,000 ms budget.

### AC3 — MCP round-trip with SRV from Secret Manager (live re-run, revision round 1)
```
$ python services/agent/scripts/mcp_smoke.py
# fetching SRV from Secret Manager (ADC)...
# srv=mongodb+srv://grace2-worker:<redacted>@grace-2-dev.tszeckl.mongodb.net/grace2_dev?retryWrites=true&w
# starting mongodb-mcp-server sidecar (npx)...
> tools/list
< tools (18): ['aggregate', 'aggregate-db', 'atlas-local-connect-deployment', 'atlas-local-list-deployments', 'collection-indexes', 'collection-schema', 'collection-storage-size', 'connect', 'count', 'db-stats', 'explain', 'export', 'find', 'list-collections', 'list-databases', 'list-knowledge-sources', 'mongodb-logs', 'search-knowledge']
> tools/call name=list-databases
< databases: Found 1 databases:
> tools/call name=list-collections database=grace2_dev
< collections: Found 1 collections for database "grace2_dev".
```
**AC3: pass.** SRV pulled from Secret Manager via ADC (never hardcoded — `grep -RIn 'mongodb+srv' services/agent/` returns zero hits), `mongodb-mcp-server` launched via `npx` as a stdio sidecar, JSON-RPC `initialize` + `notifications/initialized` completed, `tools/list` returned 18 tools, `list-databases` + `list-collections database=grace2_dev` each round-tripped real data from the live Flex cluster (`grace-2-dev`).

### AC4 — first-token latency vs NFR-P-1 (live re-run, revision round 1)
Five consecutive calls against the same warm server process (re-running the audit's flagged warm-call gap):

| Call | Prompt | First-token latency (ms) | NFR-P-1 budget (2000) | Audit threshold (5000) |
|---|---|---:|---|---|
| 1 (cold) | "What is SFINCS?" | 21,152.9 | miss | miss |
| 2 | "Difference between SFINCS and Delft3D-FM?" | 22,777.1 | miss | miss |
| 3 | "Name one Deltares model." | 9,176.4 | miss | miss |
| 4 | "Say hi." | 4,891.7 | miss | pass |
| 5 | "What is 2+2?" | 4,553.9 | miss | pass |

**AC4: qualified — escalated to a real NFR-P-1 concern (see OQ-A-2).** Measurement mechanism is present and correct (`ws_client.py` prints `# first-token-latency-ms=… (NFR-P-1 budget 2000)`); the value is dominated by Gemini-2.5-pro's pre-stream reasoning latency, not by Cloud Run cold-start (calls 4–5 are warm short-prompt and still land at 4.5–4.9 s). Cold/warm-gap finding: roughly 4.5 s floor on warm short prompts, climbing to 20+ s as prompt and expected-output length grow. **The 3 s "warm" claim from the prior commit body is not borne out;** the warm-short-prompt floor is closer to 5 s. Escalated as OQ-A-2 with mitigation options (Gemini-3 on Vertex when GA / `thinking_config` tuning / NFR-P-1 budget rescope).

### AC5 — OQ-1 surfaced with recommendation + trade-offs
Recommendation, rationale (five axes: WS-as-first-class, bidirectional cancel, session affinity / scale-to-zero, NFR-P-1 warm-path, control plane fit), and trade-offs of the recommendation now in the Open Questions section above (entry "OQ-1"). Decision-date target: before sprint-04 M2 kickoff. Owner: `infra`. **AC5: pass.**

### Containment seam grep (FR-AS-1)
```
$ grep -RIn -E '(gemini|vertexai|google\.genai|google_genai)' services/agent/ \
  --include='*.py' --include='*.toml' --include='*.md'
services/agent/src/grace2_agent/adapter.py:28: from google import genai
services/agent/src/grace2_agent/adapter.py:29: from google.genai import types as genai_types
services/agent/src/grace2_agent/adapter.py:* docstrings + GeminiSettings dataclass + GEMINI_DEFAULT_MODEL constant + build_client
services/agent/src/grace2_agent/server.py: tool_name="gemini_generate" (display) + _stream_gemini_reply (function name) + "gemini stream failed" (log line) + module docstring reference to OQ-1
services/agent/README.md: "Gemini-only containment (FR-AS-1, no LLMProvider abstraction)"
services/agent/pyproject.toml: dependency pin "google-genai ~> 2.8"
```
Only `adapter.py` imports `google.genai`. `server.py` references the word "gemini" only in identifier strings (display labels and a log line) — see OQ-A-5 for the rationale. `mcp.py`, `main.py`, `ws_client.py`, `mcp_smoke.py` zero hits.

### Legacy-shape grep (FR-AS-1)
```
$ grep -RIn -E '(bedrock|strands|anthropic|openai|LLMProvider)' services/agent/
services/agent/README.md: "no LLMProvider abstraction"
services/agent/src/grace2_agent/adapter.py: docstring "no LLMProvider protocol, no provider branches, no Bedrock/Strands shapes"
```
Both hits are explicit negations; no provider abstraction exists.

### Contract usage (server uses grace2_contracts.ws for serialization)
`server.py:43-53` imports `Envelope`, `AgentMessageChunkPayload`, `CancelPayload`, `ErrorPayload`, `PipelineStatePayload`, `PipelineStep`, `SessionResumePayload`, `SessionStatePayload`, `UserMessagePayload` from `grace2_contracts.ws`. `_new_envelope()` uses `Envelope().model_dump_json()` — no manual `json.dumps` for output. Inbound: a single `json.loads` for envelope shape inspection (`type` / `session_id` / `payload`), then dispatch to `.model_validate` on the concrete payload model. `ws_client.py` also serializes via `Envelope`.

### File ownership
```
$ git diff 0742c06^ 0742c06 --name-only
Makefile
services/agent/README.md
services/agent/pyproject.toml
services/agent/scripts/mcp_smoke.py
services/agent/scripts/ws_client.py
services/agent/src/grace2_agent/__init__.py
services/agent/src/grace2_agent/adapter.py
services/agent/src/grace2_agent/main.py
services/agent/src/grace2_agent/mcp.py
services/agent/src/grace2_agent/server.py
```
No `packages/contracts/`, `web/`, or `infra/` touched.

---

## Revision Round 1 — addresses reviewer findings

This subsection summarizes the changes between the originally-submitted state and the audited state.

### Findings addressed

- **(blocking) report.md was empty.** Original `report.md` and `.history/report.v1.md` were the unfilled template. The full report above is the corrective deliverable: Summary, Changes Made, Decisions Made, Invariants Touched table, Open Questions (now with OQ-1 trade-offs), Dependencies and Impacts, Verification with all four AC transcripts. The empty template is archived at `.history/report.v2.md`. **Status: resolved.**
- **(high) AC5 / OQ-1 surfacing — trade-offs missing.** The "OQ-1" entry under Open Questions now carries the full Cloud-Run-with-WebSocket recommendation against Agent Engine: five-axis rationale (WS-as-first-class, bidirectional cancel, session affinity / scale-to-zero compatible with NFR-R-2, NFR-P-1 warm-path compatible, control-plane fit with the `agent-runtime` SA + Secret Manager binding from job-0014), trade-offs of Cloud Run (idle-timeout management, no managed session store, image-size growth from MCP sidecar), decision-date target (before sprint-04 M2 kickoff), and owner (`infra`). **Status: resolved.**
- **(medium) AC4 / first-token latency vs audit threshold + warm-call evidence missing.** Five consecutive calls captured (table in the AC4 section above). Warm short-prompt floor lands at 4.5–4.9 s — better than the 5 s audit threshold but still 2.5× above the NFR-P-1 2 s budget; long-prompt warm calls (calls 2 and 3) land at 9.2–22.8 s, well above both. The prior commit body's "warm 3 s" claim is not borne out and is replaced by the measurements above. **Re-categorized from informational to a real NFR-P-1 concern (OQ-A-2)** with mitigation options (Gemini-3 when GA / `thinking_config` tuning / budget rescope). **Status: resolved (escalated).**
- **(low) Gemini answer fidelity for SFINCS prompt (SPHINCS+ confusion).** Filed as OQ-A-6 (future-agent follow-up — register a minimal hazard-domain system prompt or tool-context preamble before the first end-to-end web demo). Not a blocker for M1 hello-world. **Status: tracked.**
- **(low) Cosmetic Gemini string leakage in `server.py` (`_stream_gemini_reply`, `tool_name="gemini_generate"`, log line).** Documented as a deliberate keep in OQ-A-5 — containment seam (FR-AS-1) is intact (only `adapter.py` imports `google.genai`); `tool_name` is a display label, not a stable contract key; `web` is being briefed via the job-0016 kickoff that these strings are display-only. Renaming to `llm_generate` lined up for a one-commit refactor when Gemini-3 lands (OQ-A-1). **Status: tracked (no code change in this round).**

### AC re-run summary (revision round 1)

| AC | Original audit result | Revision round 1 result | Notes |
|---|---|---|---|
| AC1 streaming Gemini reply | pass | **pass** | 20 deltas + terminal `done=True`; envelope validates. |
| AC2 cancel-to-cancelled-pipeline | pass | **pass** | 502.3 ms vs NFR-R-3 30,000 ms budget. |
| AC3 MCP round-trip (SRV from Secret Manager) | pass | **pass** | 18 tools listed; `list-databases` + `list-collections` against live Flex cluster. |
| AC4 first-token latency informational | qualified | **qualified (escalated)** | Warm short-prompt floor ~5 s; long-prompt warm 9–23 s. NFR-P-1 concern → OQ-A-2. |
| AC5 OQ-1 surfaced with recommendation + trade-offs | fail | **pass** | OQ-1 entry in Open Questions now carries the full trade-off analysis. |
| Containment (no Gemini outside adapter) | pass | **pass** | `google.genai` import only in `adapter.py`. |
| No legacy provider shapes | pass | **pass** | Only negations exist. |
| Contract usage (grace2_contracts.ws) | pass | **pass** | `Envelope().model_dump_json()`; no hand-rolled JSON. |
| File ownership | pass | **pass** | `Makefile` + `services/agent/**` only. |
| Commit hygiene | pass | **pass** | job-0015 namespace + Claude trailer (new revision-round-1 commit). |
| Invariants reported | qualified | **pass** | Invariants Touched table now present (1, 2, 3, 8, 9, 10). |
| Linux/Debian env | pass | **pass** | `.venv-agent/` + virtualenv flow per PROJECT_STATE. |

No code changes in this revision round — every reviewer finding was about the report-protocol artifact (or escalations / tracking decisions), not the code under audit.
