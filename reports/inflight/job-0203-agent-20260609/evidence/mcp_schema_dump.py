import asyncio, json, os, sys
sys.path.insert(0, "src")
from grace2_agent.mcp import MCPClient

async def main():
    os.environ["MDB_MCP_READ_ONLY"] = "false"
    client = await MCPClient.start(
        "mongodb://localhost:27099/grace2_dev?serverSelectionTimeoutMS=300&directConnection=true"
    )
    try:
        tools = await client.list_tools()
        for t in tools:
            if t["name"] in {"find", "insert-many", "update-many", "delete-many", "count"}:
                print(f"\n===== {t['name']} =====")
                print(json.dumps(t.get("inputSchema", {}), indent=1)[:2200])
    finally:
        await client.close()

asyncio.run(main())
