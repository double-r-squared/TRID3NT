"""MCP round-trip against the Atlas Flex cluster (qualified if unreachable).

The kickoff allows this test to be `qualified` when Atlas is network-gated —
never silently skipped. We exercise the SAME path job-0015 ships: the agent's
``MCPClient`` launches ``mongodb-mcp-server`` via npx with the SRV from Secret
Manager, completes the MCP handshake, lists tools, and calls ``list-databases``.
"""

from __future__ import annotations

import asyncio
import os
import shutil

import pytest


@pytest.mark.live_atlas
async def test_mcp_round_trip_against_atlas_flex(atlas_srv) -> None:
    """One real MCP tool call against the live Atlas Flex SRV."""
    if atlas_srv is None:
        pytest.skip(
            "qualified: atlas SRV unreachable (no ADC / Secret Manager / network); "
            "see job-0014 audit for the reachable-substrate evidence"
        )
    if shutil.which("npx") is None:
        pytest.skip(
            "qualified: npx not on PATH — MCP smoke requires Node toolchain; "
            "see job-0015 AC3 transcript for the proven path on this host"
        )

    from grace2_agent.mcp import MCPClient

    mcp = await MCPClient.start(atlas_srv)
    try:
        tools = await mcp.list_tools()
        names = sorted(t.get("name", "?") for t in tools)
        # Failure here names the AGENT/MCP layer (sidecar startup or handshake);
        # an empty tool list would mean the sidecar started but mcp didn't return.
        assert (
            len(names) > 0
        ), f"AGENT/MCP layer — mongodb-mcp-server returned empty tools list"

        # Real query against the Flex cluster.
        result = await mcp.call_tool("list-databases", {})
        content = result.get("content", [])
        # Either text content or a structured response — accept both forms.
        assert content, (
            "AGENT/MCP layer — list-databases returned no content"
        )
    finally:
        await mcp.close()


# Auto-run variant (no marker) so `make test` exercises the smoke when the
# environment permits, qualifies it cleanly when it doesn't, and never fails
# silently. The kickoff: "qualified if Atlas unreachable".


async def test_mcp_round_trip_auto(atlas_srv) -> None:
    """Atlas MCP smoke that self-qualifies on unreachable substrate."""
    if atlas_srv is None or shutil.which("npx") is None:
        pytest.skip(
            "qualified: Atlas substrate or npx unavailable in this run "
            "(self-qualification per testing.md cloud-dependent rule)"
        )

    from grace2_agent.mcp import MCPClient

    mcp = await MCPClient.start(atlas_srv)
    try:
        tools = await mcp.list_tools()
        assert tools, "AGENT/MCP layer — empty tools list from live Atlas Flex"
    finally:
        await mcp.close()
