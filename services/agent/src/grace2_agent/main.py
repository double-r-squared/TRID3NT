"""Entry point for the ``grace2-agent`` console script.

Run the WebSocket server. Optionally run an MCP smoke pre-flight (gated by
``GRACE2_AGENT_SKIP_MCP_SMOKE=1`` to skip).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys


def run() -> None:
    """Console-script entry point. ``make run-agent`` calls this."""
    logging.basicConfig(
        level=os.environ.get("GRACE2_AGENT_LOG", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from .server import run_server

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        print("grace2-agent: interrupted, shutting down.", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    run()
