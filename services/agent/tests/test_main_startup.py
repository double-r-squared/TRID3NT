"""Verify the agent service startup picks up the tool registry.

Acceptance criterion: ``python -m grace2_agent --startup-only`` imports the
tools package (populating ``TOOL_REGISTRY``) and exits without binding the
WebSocket port. The test exercises the ``run([...])`` entry point directly.
"""

from __future__ import annotations

import logging

from grace2_agent import tools as agent_tools
from grace2_agent.main import _import_tools_registry, run


def test_import_tools_registry_populates_passthroughs():
    n = _import_tools_registry()
    assert n >= 2
    assert "qgis_process" in agent_tools.TOOL_REGISTRY
    assert "mongo_query" not in agent_tools.TOOL_REGISTRY


def test_run_startup_only_returns_zero_without_serving(caplog):
    """``run(['--startup-only'])`` returns 0 and logs the registered tools."""
    caplog.set_level(logging.INFO, logger="grace2_agent.main")
    rc = run(["--startup-only"])
    assert rc == 0
    # Startup log line includes the registered tool names.
    joined = "\n".join(r.message for r in caplog.records)
    assert "tool registry loaded" in joined
    assert "qgis_process" in joined
