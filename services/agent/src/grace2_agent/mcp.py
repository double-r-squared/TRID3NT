"""MongoDB MCP sidecar bootstrap (FR-AS-4, OQ-2 resolution: Cloud Run sidecar).

For job-0015 hello-world the sidecar is launched as a stdio subprocess of the
agent process (the same containment shape that will deploy in Cloud Run as a
sidecar container). The SRV connection string is fetched from Secret Manager
using ADC — never hardcoded, never committed.

We expose two surfaces:

1. ``fetch_srv_from_secret_manager()`` — pulls the SRV string from
   ``projects/425352658356/secrets/mongodb-srv-dev/versions/latest`` (job-0014
   substrate). Uses ``google.cloud.secretmanager``.

2. ``MCPClient`` — a thin JSON-RPC-over-stdio client to ``mongodb-mcp-server``
   (the npm package consumed verbatim — FR-AS-4). Implements just enough of the
   MCP protocol to (a) ``initialize``, (b) ``tools/list``, (c) ``tools/call`` —
   what the M1 hello-world demonstration needs. The ADK MCP toolset will land in
   a follow-up job; this thin client is the smoke harness that proves the seam.

The session-records write carveout (FR-AS-8) is documented but not enforced
here — confirmation hooks are scaffolded in server.py with no triggers yet.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
from collections.abc import Mapping
from typing import Any

# SRV secret resource path — pinned by job-0014 PROJECT_STATE.
SRV_SECRET_RESOURCE = (
    "projects/425352658356/secrets/mongodb-srv-dev/versions/latest"
)


def fetch_srv_from_secret_manager(resource: str = SRV_SECRET_RESOURCE) -> str:
    """Return the MongoDB SRV string. Uses ADC for auth. Never logged."""
    # Local import (job-0203 / M4): only the Secret Manager fetch needs the
    # GCP SDK — a module-level import made ``MCPClient`` unusable on dev
    # boxes without ``google-cloud-secret-manager`` installed. Same lazy
    # pattern as ``persistence.Persistence.get_secret_value``.
    from google.cloud import secretmanager  # production-only dependency

    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(request={"name": resource})
    return response.payload.data.decode("utf-8")


class MCPError(RuntimeError):
    """Raised when the MCP server returns a JSON-RPC error."""


class MCPClient:
    """Minimal JSON-RPC-over-stdio client for ``mongodb-mcp-server``.

    Lifecycle:
        async with MCPClient.start(srv) as mcp:
            tools = await mcp.list_tools()
            result = await mcp.call_tool("list-collections", {"database": "grace2_dev"})

    Containment: nothing here knows about Gemini. The agent's tool router
    forwards results into Gemini's tool-result channel; that wiring lives in
    the ADK registry (future job), not here.
    """

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task | None = None

    @classmethod
    async def start(cls, srv: str, *, database: str = "grace2_dev") -> "MCPClient":
        """Launch ``npx mongodb-mcp-server`` with the SRV as a CLI arg.

        ``mongodb-mcp-server`` accepts ``--connectionString`` (camelCase per its
        README). We pass the SRV via env var ``MDB_MCP_CONNECTION_STRING`` —
        the documented env path — to avoid putting credentials in argv where
        ``ps`` would surface them.
        """
        npx = shutil.which("npx")
        if npx is None:
            raise RuntimeError(
                "npx not on PATH; install Node.js >= 20 (see PROJECT_STATE.md)."
            )

        env = os.environ.copy()
        env["MDB_MCP_CONNECTION_STRING"] = srv
        # Read-only mode for the hello-world; FR-AS-8 confirmation hooks land
        # before any write tools are exposed.
        env.setdefault("MDB_MCP_READ_ONLY", "true")

        def _preexec() -> None:
            # job-0241: own session/process-group so close() can signal the
            # WHOLE tree (npx spawns a Node grandchild — the actual server),
            # and PR_SET_PDEATHSIG so the tree dies if the agent dies
            # abnormally (SIGKILL/crash). 43 orphaned mongodb-mcp-server
            # processes (~2.9 GB RSS) accumulated on the dev box from Wave
            # 4.11-era agent restarts without this.
            os.setsid()
            try:
                import ctypes

                PR_SET_PDEATHSIG = 1
                ctypes.CDLL("libc.so.6", use_errno=True).prctl(
                    PR_SET_PDEATHSIG, signal.SIGKILL
                )
            except Exception:  # noqa: BLE001 — non-Linux: setsid alone
                pass

        proc = await asyncio.create_subprocess_exec(
            npx,
            "-y",
            "mongodb-mcp-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            preexec_fn=_preexec,
        )
        client = cls(proc)
        client._reader_task = asyncio.create_task(client._read_loop())
        await client._initialize()
        return client

    async def __aenter__(self) -> "MCPClient":  # pragma: no cover — alias to start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._proc.returncode is None:
            # job-0241: signal the PROCESS GROUP, not just the npx PID — the
            # Node grandchild (the actual server) otherwise survives.
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                try:
                    self._proc.terminate()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    self._proc.kill()
                await self._proc.wait()

    # ----- JSON-RPC plumbing ------------------------------------------------

    def _make_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _send(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        msg_id = self._make_id()
        request = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params or {},
        }
        line = (json.dumps(request) + "\n").encode("utf-8")
        assert self._proc.stdin is not None
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        self._proc.stdin.write(line)
        await self._proc.stdin.drain()
        return await asyncio.wait_for(future, timeout=30.0)

    async def _read_loop(self) -> None:
        assert self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                # Stream closed; fail all pending futures.
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(MCPError("MCP server stream closed"))
                self._pending.clear()
                return
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                # Non-JSON lines from the server (banners, log lines on stdout)
                # are tolerated — skip them. mongodb-mcp-server logs to stderr.
                continue
            msg_id = message.get("id")
            if msg_id is None:
                # Notification (no response expected). Ignore for hello-world.
                continue
            fut = self._pending.pop(msg_id, None)
            if fut is None or fut.done():
                continue
            if "error" in message:
                fut.set_exception(MCPError(json.dumps(message["error"])))
            else:
                fut.set_result(message.get("result", {}))

    # ----- MCP methods ------------------------------------------------------

    async def _initialize(self) -> None:
        # MCP handshake. Protocol version per modelcontextprotocol.io spec.
        await self._send(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "grace2-agent", "version": "0.1.0"},
            },
        )
        # Send the required initialized notification.
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert self._proc.stdin is not None
        self._proc.stdin.write((json.dumps(notif) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._send("tools/list")
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return await self._send(
            "tools/call", {"name": name, "arguments": dict(arguments or {})}
        )


__all__ = [
    "SRV_SECRET_RESOURCE",
    "MCPClient",
    "MCPError",
    "fetch_srv_from_secret_manager",
]
