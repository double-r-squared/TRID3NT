# MongoDB MCP Server — GRACE-2 infra substrate (Wave 4.11 M1)

## What this is

The **MongoDB Atlas MCP server** (`mongodb-mcp-server` npm package, maintained
by MongoDB Inc.) is the LLM-facing persistence path for GRACE-2 (FR-AS-4,
Decision F). The agent process spawns it as a stdio sidecar subprocess; the
`grace2_agent.mcp.MCPClient` speaks JSON-RPC-over-stdio to it and the
`grace2_agent.persistence.Persistence` wrapper translates between typed
contracts and MCP tool calls.

This directory documents the install and run path; the actual wiring code lives
in `services/agent/src/grace2_agent/mcp.py` and
`services/agent/src/grace2_agent/persistence.py`.

## Package

```
npm package:  mongodb-mcp-server
Registry:     https://www.npmjs.com/package/mongodb-mcp-server
```

MongoDB publishes this package directly. The `MCPClient.start()` method in
`mcp.py` invokes it via:

```bash
npx -y mongodb-mcp-server
```

No global install is required; `npx` fetches and caches the package on first
use. Node.js >= 20 is the only prerequisite (per `PROJECT_STATE.md`).

## Required environment variables

See `.env.example` in this directory for the full list. The two load-bearing
vars:

| Variable | Required | Description |
|----------|----------|-------------|
| `MDB_MCP_CONNECTION_STRING` | YES (prod) | MongoDB Atlas SRV string (`mongodb+srv://...`). In production this is sourced from GCP Secret Manager (`projects/425352658356/secrets/mongodb-srv-dev/versions/latest`) via `fetch_srv_from_secret_manager()` in `mcp.py`. Never committed. |
| `GRACE2_MONGO_MCP_STDIO` | YES (prod) | Set to `1` to tell `init_persistence_from_env()` in `server.py` to launch the MCP sidecar subprocess. Unset in CI and local dev — the `FileMCPClient` dev shim activates instead. |

### Optional / dev-mode variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GRACE2_MONGO_DB` | `grace2_dev` | Atlas database name. Override for staging / test isolation. |
| `MDB_MCP_READ_ONLY` | `true` | Pass to the MCP server at start. Set `false` when the agent needs to write. The hello-world / smoke path starts in read-only mode. |
| `GRACE2_DEV_PERSISTENCE` | auto | `1` forces file-backed dev persistence; `0` disables it. Default: enabled when MCP is not provisioned. |
| `GRACE2_DEV_PERSISTENCE_DIR` | `~/.grace2/dev_persistence/` | Override the file-backed dev persistence directory (useful in tests and CI). |
| `GRACE2_MONGO_MCP_URL` | unset | Reserved for the future HTTP MCP transport (Cloud Run sidecar URL). Not yet wired; falls through to the stdio path. |

## Running locally (smoke test)

### Prerequisites

```bash
node --version  # must be >= 20
npx --version   # bundled with npm >= 5.2
```

No Atlas credentials are needed for the dev-persistence path (the
`FileMCPClient` shim activates automatically). If you have Atlas credentials
and want to test the live MCP path:

```bash
# 1. Export the SRV (from Secret Manager or directly):
export MDB_MCP_CONNECTION_STRING="mongodb+srv://<user>:<pass>@<cluster>.mongodb.net/?retryWrites=true&w=majority"

# 2. Launch the MCP server manually (optional — the agent starts it automatically):
./infra/mongo-mcp/start.sh

# 3. Start the agent with MCP enabled:
GRACE2_MONGO_MCP_STDIO=1 make run-agent
```

### start.sh quick launch

`start.sh` (in this directory) launches the MCP server directly for manual
inspection or integration testing without the full agent process. It reads
`MDB_MCP_CONNECTION_STRING` from the environment (never pass SRV on the CLI
where `ps` would expose it).

## Atlas collection bootstrapping (job-0201)

The three Wave 4.11 Stage 1A collections require Atlas-side setup:

| Collection | TTL index | Atlas search index |
|---|---|---|
| `tool_call_telemetry` | `called_at_utc` TTL 90 days | BM25 on `tool_name` |
| `description_audit` | none | BM25 + dense on `description` |
| `case_telemetry` | none | none (small) |

Bootstrap SQL/Atlas CLI commands are documented in `job-0201-schema` kickoff
once that job lands. The Pydantic models live in
`packages/contracts/src/grace2_contracts/mongo_collections.py`.

## Wire protocol

The `MCPClient` in `mcp.py` uses the MCP protocol version `2024-11-05`
(modelcontextprotocol.io spec). The handshake:

1. `MCPClient.start(srv)` — spawns `npx -y mongodb-mcp-server` subprocess;
   passes `MDB_MCP_CONNECTION_STRING` via env (not argv).
2. `_initialize()` — sends `initialize` + `notifications/initialized`.
3. `list_tools()` — enumerates available tools (smoke-test step).
4. `call_tool(name, args)` — the only method `Persistence` calls into.

The `Persistence` wrapper calls these MCP tool names:

| MCP tool name | Used by |
|---|---|
| `find-one` | `get_case`, `get_user_by_firebase_uid`, `get_user_by_id` |
| `find` | `list_cases_for_user`, `get_session_state`, `list_secrets_refs` |
| `update-one` | `upsert_case`, `upsert_user`, `upsert_secret_ref`, `archive_case`, `delete_case`, `revoke_secret` |
| `insert-one` | `append_chat_message`, `append_audit` |

## Error surface

If `MCPClient.start()` fails (Node.js missing, SRV invalid, Atlas unreachable),
`init_persistence_from_env()` in `server.py` catches the exception and logs at
WARNING level — the agent service starts without persistence rather than
refusing to serve. Any subsequent caller that requires persistence raises a
clear error with the `GRACE2_MONGO_MCP_STDIO=1` remediation hint.

## Cloud Run sidecar (production)

In the production Cloud Run deployment, `mongodb-mcp-server` runs as a sidecar
container alongside the agent container (OQ-2 resolution). The stdio pipe
becomes an inter-container channel. The `GRACE2_MONGO_MCP_STDIO=1` env var
is set in the Cloud Run service definition (via Secret Manager env injection).
The Atlas SRV is in `projects/425352658356/secrets/mongodb-srv-dev`; infra
mounts it as `MDB_MCP_CONNECTION_STRING`. No changes to `mcp.py` are needed for
the sidecar vs subprocess distinction — `MCPClient` only sees stdin/stdout.
