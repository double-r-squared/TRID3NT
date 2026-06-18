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


@pytest.fixture(autouse=True)
def _default_vertex_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default the model provider to ``vertex`` for the agent test suite.

    GCP/Vertex is decommissioned and the RUNTIME default is now ``bedrock``
    (``bedrock_adapter.model_provider``). The bulk of the agent-loop tests,
    however, drive the retained google-genai stream-parsing path: they patch
    ``server.build_client`` and feed fake ``generate_content_stream`` chunks
    into ``_stream_gemini_reply`` / ``stream_events_with_contents``. Those tests
    pre-date the provider flip and assume the Vertex branch. Pinning the env to
    ``vertex`` here keeps them exercising the Gemini path; any test that needs
    the Bedrock branch sets ``MODEL_PROVIDER`` itself (monkeypatch wins inside
    the test body). ``google-genai`` is the kept carve-out dependency, so the
    Gemini stream-parser imports/runs fine.
    """
    monkeypatch.setenv("MODEL_PROVIDER", "vertex")


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
