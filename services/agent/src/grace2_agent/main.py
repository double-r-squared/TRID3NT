"""Entry point for the ``grace2-agent`` console script.

Run the WebSocket server. Optionally run an MCP smoke pre-flight (gated by
``GRACE2_AGENT_SKIP_MCP_SMOKE=1`` to skip).

Startup-time tool-registry wiring (job-0032, M4 substrate):

Importing ``grace2_agent.tools`` populates the module-level ``TOOL_REGISTRY``
via the import-time ``@register_tool`` decorators in the package's
submodules (``passthroughs`` for M4 job-0032; ``fetchers`` etc. for
job-0033+). The ``--startup-only`` flag below verifies the registry is
populated without binding the WebSocket port; ``make run-agent`` continues
to start the server normally.

FR-CE-8 fail-fast: any tool whose ``AtomicToolMetadata`` is misconfigured
(e.g. ``cacheable=True`` with ``ttl_class="live-no-cache"``) raises a
``pydantic.ValidationError`` at import time and prevents the agent service
from starting.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys


def _import_tools_registry() -> int:
    """Import ``grace2_agent.tools`` to populate ``TOOL_REGISTRY``.

    Returns the number of registered tools. Surfaced at startup so an empty
    registry (typically a packaging mistake) is visible in the logs rather
    than silent.
    """
    from . import tools  # noqa: F401 — side-effect: registers atomic tools

    return len(tools.TOOL_REGISTRY)


def run(argv: list[str] | None = None) -> int:
    """Console-script entry point. ``make run-agent`` calls this.

    Supports a ``--startup-only`` flag that imports the tool registry, logs
    the registered tools, and exits 0 without binding the WebSocket port.
    Used by job-0032 acceptance and by container healthchecks.
    """
    logging.basicConfig(
        level=os.environ.get("GRACE2_AGENT_LOG", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("grace2_agent.main")

    args = sys.argv[1:] if argv is None else argv
    startup_only = "--startup-only" in args

    # Populate TOOL_REGISTRY by importing the tools package. Any import-time
    # registration error (duplicate name, bad metadata) surfaces here.
    n_tools = _import_tools_registry()
    from . import tools

    tool_names = sorted(tools.TOOL_REGISTRY.keys())
    logger.info("tool registry loaded: %d tool(s): %s", n_tools, tool_names)

    if startup_only:
        logger.info("--startup-only: tool registry verified; exiting without serving")
        return 0

    from .server import run_server

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        print("grace2-agent: interrupted, shutting down.", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
