"""job-0203 M4 live evidence: real mongodb-mcp-server JSON-RPC handshake
through the production MCPClient class (initialize + tools/list).

Connection string is well-formed but unreachable (localhost:27099) — the
server starts and answers protocol calls lazily; no Atlas needed for the
protocol-level evidence. Run twice: READ_ONLY=true (production default in
MCPClient.start) and false, to capture both exposed tool surfaces.
"""
import asyncio, json, os, sys
sys.path.insert(0, "src")
from grace2_agent.mcp import MCPClient

NEEDED = {"find", "find-one", "insert-one", "update-one"}

async def run(read_only: str):
    os.environ["MDB_MCP_READ_ONLY"] = read_only
    client = await MCPClient.start(
        "mongodb://localhost:27099/grace2_dev?serverSelectionTimeoutMS=300&directConnection=true"
    )
    try:
        tools = await client.list_tools()
        names = sorted(t["name"] for t in tools)
        print(f"== READ_ONLY={read_only}: initialize OK, {len(names)} tools ==")
        print(json.dumps(names))
        missing = NEEDED - set(names)
        print(f"Persistence-needed {sorted(NEEDED)}: missing={sorted(missing) or 'NONE'}")
        return names, missing
    finally:
        await client.close()

async def main():
    ro_names, ro_missing = await run("true")
    rw_names, rw_missing = await run("false")
    print("\n== VERDICT ==")
    print(f"read-only missing: {sorted(ro_missing) or 'NONE'}")
    print(f"read-write missing: {sorted(rw_missing) or 'NONE'}")

asyncio.run(main())
