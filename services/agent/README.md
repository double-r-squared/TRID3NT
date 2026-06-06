# services/agent/ — Agent service (ADK + Gemini)

**Owner:** `agent` specialist. **Container/deploy:** `infra` (Cloud Run).

The agent service (SRS v0.3 Decision E/G, FR-AS-*): a Google ADK application on
Gemini that serves the Appendix-A WebSocket protocol, hosts the tool registry
(native ADK FunctionTools + the MongoDB MCP client + hazard-modeling tools),
streams replies, propagates cancellation, and enforces the determinism boundary
(Invariant 1) and confirmation-before-consequence hooks (Invariant 9).

## Layout (job-0015 hello-world skeleton)

```
services/agent/
├── pyproject.toml            grace2-agent package, console script `grace2-agent`
├── README.md                 (this file)
├── src/grace2_agent/
│   ├── __init__.py
│   ├── main.py               entry point (`grace2-agent` → run())
│   ├── server.py             Appendix-A WebSocket server (asyncio + websockets)
│   ├── adapter.py            Gemini-only containment (FR-AS-1, no LLMProvider abstraction)
│   └── mcp.py                MongoDB MCP sidecar bootstrap (SRV from Secret Manager + ADC)
└── scripts/
    └── ws_client.py          live-evidence harness (job-0017 builds on this)
```

## Running locally

```bash
# from repo root, requires the project's virtualenv
make run-agent
# then in another shell:
python services/agent/scripts/ws_client.py "What is SFINCS?"
```

`make run-agent` sources the venv at `.venv-agent/`, exports the Vertex AI env
vars (`GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT`,
`GOOGLE_CLOUD_LOCATION`), and launches `grace2-agent` on port 8765 (override
with `GRACE2_AGENT_PORT`).

ADC at `~/.config/gcloud/application_default_credentials.json` authenticates
both the Vertex AI client and the Secret Manager client (the SRV string is at
`projects/425352658356/secrets/mongodb-srv-dev`). Nothing is hardcoded.

## Hello-world scope (job-0015)

- Real Gemini round-trip with streamed `agent-message-chunk` deltas, terminal
  `done: true` frame.
- `cancel` interrupts in-flight generation within 30s and emits cancelled
  `pipeline-state` (Invariant 8 LLM-side; Cloud Workflows `terminate` deferred
  to v0.2/M5 when solver lands).
- One real MongoDB MCP tool call against the Atlas Flex SRV (sidecar via
  stdio, `mongodb-mcp-server` npm package).
- Every wire message validated via `grace2_contracts` — no hand-rolled JSON.

Workflows, engine tools, confirmation UI flow, and Cloud Workflows cancellation
chain are out of scope here — they land in later jobs.
