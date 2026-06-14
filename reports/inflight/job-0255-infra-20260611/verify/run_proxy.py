"""Throwaway catalog+proxy server for panel live-verify (job-0255).

Mounts ONLY the real serve_catalog_http listener (which carries the /qgis-proxy
route) on a fresh loopback port. QGIS_PROXY_ENABLED + QGIS_SERVER_URL come from
the environment. Reaped on SIGTERM. Does NOT touch the dev agent.
"""
import asyncio
import os
import sys

sys.path.insert(0, "/home/nate/Documents/GRACE-2/services/agent/src")

from grace2_agent.tool_catalog_http import serve_catalog_http  # noqa: E402


async def main() -> None:
    host = os.environ.get("GRACE2_AGENT_HOST", "127.0.0.1")
    server = await serve_catalog_http(host=host)
    sockets = server.sockets or []
    for s in sockets:
        print(f"PROXY-LISTENING {s.getsockname()}", flush=True)
    async with server:
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
