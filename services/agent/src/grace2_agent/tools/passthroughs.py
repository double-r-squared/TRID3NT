"""Registry pass-through atomic tools (job-0032, M4 substrate).

This module registers two tools that bridge the agent's ADK FunctionTool
surface to existing M1/M2 substrate:

- ``mongo_query``: pass-through to the MongoDB MCP path established by
  job-0015 in ``grace2_agent.mcp``. The MCP server is the LLM-facing DB
  surface per FR-AS-4 / Decision F; this tool exposes it as a typed
  callable Gemini can choose.

- ``qgis_process``: pass-through to the PyQGIS worker invocation path
  established by job-0021 (Cloud Run Jobs submission). Solver dispatch is
  uncacheable-by-construction per FR-DC-6 — results land under
  ``gs://<bucket>/runs/<run_id>/`` per FR-CE-4, not under ``cache/``.

Both tools declare:

    ttl_class = "live-no-cache"
    cacheable = False
    source_class = None  # uncacheable; no bucket prefix

per FR-DC-6's "MongoDB writes" and "Solver dispatchers and their result
fetches" enumeration entries.

The actual MCP client and worker-job submitter wiring is M2/M3 work owned
by their respective M1 substrate jobs; this module is a thin registry-
surface adapter so the M4 tool registry has real entries to exercise. The
function bodies are intentionally narrow and raise ``NotImplementedError``
for the call-time path that hasn't landed yet (worker submission), while
``mongo_query`` delegates to the existing ``MCPClient`` shape.
"""

from __future__ import annotations

import logging
from typing import Any

from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool

__all__ = ["mongo_query", "qgis_process"]

logger = logging.getLogger("grace2_agent.tools.passthroughs")


# Module-level handles for dependency injection. Production wiring sets these
# at startup (the agent service launches ``MCPClient`` per session and binds
# it here); tests overwrite them with stubs. Kept as module-level so the
# registered functions stay zero-arg-bindable from ADK's perspective.
_MCP_CLIENT: Any | None = None
_WORKER_SUBMITTER: Any | None = None


def set_mcp_client(client: Any) -> None:
    """Bind the MongoDB MCP client used by ``mongo_query`` at call time.

    Called by the agent service at startup once the ``MCPClient`` from
    ``grace2_agent.mcp`` is initialized. Kept thin so this module does not
    own MCP lifecycle.
    """
    global _MCP_CLIENT
    _MCP_CLIENT = client


def set_worker_submitter(submitter: Any) -> None:
    """Bind the Cloud Run Jobs submitter used by ``qgis_process`` at call time.

    The submitter is a callable matching the worker-side API established by
    job-0021; binding it here keeps Cloud Run Jobs SDK imports out of this
    module's import graph (so tests can exercise the registry without GCP
    libs installed).
    """
    global _WORKER_SUBMITTER
    _WORKER_SUBMITTER = submitter


# ---------------------------------------------------------------------------
# mongo_query
# ---------------------------------------------------------------------------


@register_tool(
    AtomicToolMetadata(
        name="mongo_query",
        ttl_class="live-no-cache",
        source_class=None,
        cacheable=False,
    )
)
def mongo_query(
    collection: str,
    filter: dict[str, Any],  # noqa: A002 — matches MongoDB-domain naming
    projection: dict[str, Any] | None = None,
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> list[dict[str, Any]]:
    """Run a read query against the agent's MongoDB MCP server.

    Use this when: the user asks about durable knowledge persisted in Atlas
    (sessions, runs, events, articles, projects). Returns the matching
    documents as a list of plain dicts.

    Do NOT use this for: solver outputs (they live under ``gs://.../runs/``
    not Mongo); cache lookups (use the cache shim instead); MongoDB writes
    that would require a confirmation hook (those land in a separate tool
    once FR-AS-8 triggers are wired).

    Params:
        collection: Mongo collection name (e.g. ``"sessions"``, ``"runs"``).
        filter: pymongo-style filter document.
        projection: optional pymongo-style projection (None == full docs).

    Returns:
        List of matching documents (each a dict).

    FR-DC-6: This tool is uncacheable-by-construction (Atlas writes/reads
    are the durable knowledge layer per Decision F, not the cache layer).

    Note: the actual MCP wire shape will route through ``MCPClient.call_tool``
    once job-0015's stdio sidecar is bound via ``set_mcp_client``. Until then
    this raises a clear ``RuntimeError`` so a premature LLM dispatch surfaces
    fast rather than returning ``[]``.
    """
    if _MCP_CLIENT is None:
        raise RuntimeError(
            "mongo_query invoked but MCP client is not bound; "
            "agent service startup should call set_mcp_client(...)."
        )
    logger.info(
        "mongo_query collection=%s filter_keys=%s", collection, sorted(filter.keys())
    )
    # MCP tool name shape per mongodb-mcp-server's tools/list output. The
    # M1 smoke harness covers ``list-collections``; the read-query tool name
    # below is the mongodb-mcp-server v0.x ``find`` tool. If the wire name
    # diverges in M4 integration, this single line moves.
    args: dict[str, Any] = {
        "database": "grace2_dev",
        "collection": collection,
        "filter": filter,
    }
    if projection is not None:
        args["projection"] = projection
    # MCPClient.call_tool is async; the LLM-facing wrapper here is sync to
    # match ADK's FunctionTool signature. A future job lands the async-
    # adaptation layer; for the M4 substrate this is the registration point.
    raise NotImplementedError(
        "mongo_query wire integration lands in the M4 follow-up that binds "
        "MCP async tools to the ADK sync surface; registry placement is "
        "in place via @register_tool."
    )


# ---------------------------------------------------------------------------
# qgis_process
# ---------------------------------------------------------------------------


@register_tool(
    AtomicToolMetadata(
        name="qgis_process",
        ttl_class="live-no-cache",
        source_class=None,
        cacheable=False,
    )
)
def qgis_process(
    algorithm: str,
    params: dict[str, Any],
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> dict[str, Any]:
    """Submit a PyQGIS Processing algorithm for execution on the worker.

    Use this when: the agent needs to run a QGIS Processing algorithm
    (vector / raster / GDAL / GRASS / SAGA / plugin) that maps to one
    discovered via ``list_qgis_algorithms`` / ``describe_qgis_algorithm``.
    The worker runs the algorithm and persists outputs under
    ``gs://<bucket>/runs/<run_id>/`` per FR-CE-4.

    Do NOT use this for: solver runs that have a dedicated workflow
    (``run_sfincs_solver``, ``run_pelicun_impact``, etc. — those go through
    their own dispatchers); render-only requests (use the layer-style /
    map-command path).

    Params:
        algorithm: QGIS algorithm id (e.g. ``"native:reprojectlayer"``).
        params: algorithm parameters as a JSON-serializable dict.

    Returns:
        A dict carrying the worker's ``ExecutionHandle`` (run_id, output
        URIs, status). Shape comes from
        ``grace2_contracts.execution.ExecutionHandle`` once wired.

    FR-DC-6: This tool is uncacheable-by-construction (solver / dispatcher
    outputs live under ``runs/`` not ``cache/``); the cache shim is
    deliberately bypassed.
    """
    if _WORKER_SUBMITTER is None:
        raise RuntimeError(
            "qgis_process invoked but worker submitter is not bound; "
            "agent service startup should call set_worker_submitter(...)."
        )
    logger.info("qgis_process algorithm=%s param_keys=%s", algorithm, sorted(params.keys()))
    raise NotImplementedError(
        "qgis_process worker-submission integration lands in the M4 follow-up "
        "that binds the Cloud Run Jobs submitter; registry placement is "
        "in place via @register_tool."
    )
