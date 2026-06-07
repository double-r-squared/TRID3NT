"""Shared pytest fixtures for the GRACE-2 agent-service test suite.

The agent-service tests are import-light: every test that needs the tool
registry imports ``grace2_agent.tools`` directly. The registry is a
module-level singleton, so tests that mutate it use the
``clear_registry_for_tests`` helper inside a fixture rather than relying on
import ordering.
"""

from __future__ import annotations

import pytest

from grace2_agent import tools as agent_tools


@pytest.fixture()
def empty_registry():
    """Yield a context where ``TOOL_REGISTRY`` is empty; restore on teardown.

    Tests of the ``@register_tool`` decorator and duplicate-name fail-fast
    behavior need a clean slate so the eager passthroughs imports don't
    collide with a test's fixture-registered tool.
    """
    saved = dict(agent_tools.TOOL_REGISTRY)
    agent_tools.clear_registry_for_tests()
    try:
        yield agent_tools.TOOL_REGISTRY
    finally:
        agent_tools.clear_registry_for_tests()
        agent_tools.TOOL_REGISTRY.update(saved)
