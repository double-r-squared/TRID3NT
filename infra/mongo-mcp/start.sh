#!/usr/bin/env bash
# infra/mongo-mcp/start.sh — launch mongodb-mcp-server for manual inspection
# or integration testing outside the agent process.
#
# The GRACE-2 agent normally spawns this subprocess automatically when
# GRACE2_MONGO_MCP_STDIO=1 is set (via MCPClient.start() in mcp.py). Use this
# script only for:
#   - Manual MCP smoke tests (verify Atlas connectivity before starting agent)
#   - IDE / MCP inspector integration (point the inspector at this script)
#   - Diagnosing MCP server output without agent log interleaving
#
# Usage:
#   export MDB_MCP_CONNECTION_STRING="mongodb+srv://..."
#   ./infra/mongo-mcp/start.sh
#
# Or with read-write mode:
#   MDB_MCP_READ_ONLY=false ./infra/mongo-mcp/start.sh
#
# The SRV is read from MDB_MCP_CONNECTION_STRING (never passed on the CLI).
# If the env var is unset the script exits with an error rather than passing
# an empty connection string (which would produce a confusing MCP error).
#
# Requirements:
#   - Node.js >= 20 on PATH
#   - npx (bundled with npm >= 5.2)
#   - MDB_MCP_CONNECTION_STRING exported in the shell environment

set -euo pipefail

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

if ! command -v npx &>/dev/null; then
    echo "ERROR: npx not found on PATH." >&2
    echo "  Install Node.js >= 20: https://nodejs.org/en/download" >&2
    echo "  On Debian/Ubuntu: sudo apt-get install nodejs npm" >&2
    exit 1
fi

NODE_MAJOR=$(node --version 2>/dev/null | sed 's/v\([0-9]*\).*/\1/')
if [[ -z "$NODE_MAJOR" ]] || [[ "$NODE_MAJOR" -lt 20 ]]; then
    echo "ERROR: Node.js >= 20 required; found: $(node --version 2>/dev/null || echo 'not found')" >&2
    exit 1
fi

if [[ -z "${MDB_MCP_CONNECTION_STRING:-}" ]]; then
    echo "ERROR: MDB_MCP_CONNECTION_STRING is not set." >&2
    echo "" >&2
    echo "  Export the Atlas SRV before running this script:" >&2
    echo "    export MDB_MCP_CONNECTION_STRING='mongodb+srv://...'" >&2
    echo "" >&2
    echo "  In the GRACE-2 agent, this value is fetched from GCP Secret Manager" >&2
    echo "  (projects/425352658356/secrets/mongodb-srv-dev) at startup." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

# MDB_MCP_READ_ONLY defaults to "true" (safe default for smoke testing).
# Set to "false" when write operations are required.
export MDB_MCP_READ_ONLY="${MDB_MCP_READ_ONLY:-true}"

echo "Starting mongodb-mcp-server (read_only=${MDB_MCP_READ_ONLY})" >&2
echo "Node: $(node --version)  npx: $(npx --version)" >&2
echo "---" >&2

# -y skips the npx install confirmation prompt.
# mongodb-mcp-server reads MDB_MCP_CONNECTION_STRING from the environment.
exec npx -y mongodb-mcp-server
