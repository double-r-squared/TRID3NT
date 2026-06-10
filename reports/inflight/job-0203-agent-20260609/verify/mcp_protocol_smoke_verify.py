"""Reviewer reproduction of the missing find-one/insert-one/update-one finding.

Same as evidence/mcp_protocol_smoke.py but against the reviewer's own mongod
(port 27518, reachable) — confirms the real mongodb-mcp-server@1.12.0 exposes
NO find-one / insert-one / update-one in either read-only or read-write mode.
"""
import asyncio, json, os, sys
sys.path.insert(0, "/home/nate/Documents/GRACE-2/services/agent/src")
from grace2_agent.mcp import MCPClient

NEEDED = {"find", "find-one", "insert-one", "update-one"}


async def run(read_only: str):
    os.environ["MDB_MCP_READ_ONLY"] = read_only
    client = await MCPClient.start(
        "mongodb://localhost:27518/grace2_dev?serverSelectionTimeoutMS=2000&directConnection=true"
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
    # the finding under test: all three single-doc logical names are absent
    assert ro_missing == {"find-one", "insert-one", "update-one"}, ro_missing
    assert rw_missing == {"find-one", "insert-one", "update-one"}, rw_missing
    print("FINDING REPRODUCED: find-one/insert-one/update-one ABSENT in both modes")


asyncio.run(main())
