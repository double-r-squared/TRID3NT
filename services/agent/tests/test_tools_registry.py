"""Unit tests for the atomic-tool registry (job-0032, FR-AS-3, FR-CE-8).

Coverage:
- ``@register_tool`` happy path: populates ``TOOL_REGISTRY``, returns fn
  unchanged.
- Duplicate-name registration raises ``ToolRegistrationError``.
- ``get_registered_tools`` returns a sorted snapshot.
- The eager passthroughs import populates ``mongo_query`` + ``qgis_process``.
- ``register_tool`` rejects non-``AtomicToolMetadata`` arguments.
- ``register_with_adk`` mock-test: every entry in the registry is appended
  to the ADK ``Agent.tools`` list (via a duck-typed fake).
"""

from __future__ import annotations

import pytest
from grace2_contracts.tool_registry import AtomicToolMetadata

from grace2_agent import tools as agent_tools
from grace2_agent.tools import (
    RegisteredTool,
    ToolRegistrationError,
    get_registered_tools,
    register_tool,
)


def test_register_tool_decorator_populates_registry(empty_registry):
    """Decorating a function registers it and returns the original fn."""

    md = AtomicToolMetadata(
        name="fetch_demo",
        ttl_class="static-30d",
        source_class="demo",
        cacheable=True,
    )

    @register_tool(md)
    def fetch_demo(x: int) -> int:
        return x * 2

    assert "fetch_demo" in empty_registry
    entry = empty_registry["fetch_demo"]
    assert isinstance(entry, RegisteredTool)
    assert entry.metadata is md
    assert entry.fn is fetch_demo
    # Returned fn is callable directly (decorator does not wrap).
    assert fetch_demo(21) == 42
    assert entry.module == fetch_demo.__module__


def test_register_tool_duplicate_name_fails_fast(empty_registry):
    """A second registration under the same name raises at import time."""
    md = AtomicToolMetadata(
        name="dupe",
        ttl_class="dynamic-1h",
        source_class="dupe",
        cacheable=True,
    )

    @register_tool(md)
    def first() -> None:
        return None

    md2 = AtomicToolMetadata(
        name="dupe",
        ttl_class="dynamic-1h",
        source_class="dupe",
        cacheable=True,
    )
    with pytest.raises(ToolRegistrationError) as exc:

        @register_tool(md2)
        def second() -> None:  # pragma: no cover — must not register
            return None

    assert "dupe" in str(exc.value)
    assert "FR-CE-8" in str(exc.value)


def test_register_tool_rejects_non_metadata_argument():
    """Passing a dict / string / random object raises TypeError."""
    with pytest.raises(TypeError, match="AtomicToolMetadata"):
        register_tool({"name": "bad"})  # type: ignore[arg-type]


def test_get_registered_tools_returns_sorted_snapshot(empty_registry):
    """Snapshot is sorted by name for deterministic startup logs / diffs."""

    @register_tool(
        AtomicToolMetadata(
            name="b_tool", ttl_class="static-30d", source_class="b", cacheable=True
        )
    )
    def b_tool() -> None:
        return None

    @register_tool(
        AtomicToolMetadata(
            name="a_tool", ttl_class="static-30d", source_class="a", cacheable=True
        )
    )
    def a_tool() -> None:
        return None

    snapshot = get_registered_tools()
    assert [t.metadata.name for t in snapshot] == ["a_tool", "b_tool"]


def test_passthroughs_eager_import_registers_mongo_and_qgis():
    """Importing ``grace2_agent.tools`` populates the two pass-throughs.

    This is the acceptance-criterion test: the running agent registers them
    with ADK on startup because their module-level ``@register_tool`` calls
    fire when ``grace2_agent.tools`` is imported. We exercise that by
    reading the live ``TOOL_REGISTRY`` after the package import.
    """
    # No fixture: we deliberately use the live registry populated by import.
    assert "mongo_query" in agent_tools.TOOL_REGISTRY
    assert "qgis_process" in agent_tools.TOOL_REGISTRY

    mq = agent_tools.TOOL_REGISTRY["mongo_query"]
    assert mq.metadata.ttl_class == "live-no-cache"
    assert mq.metadata.cacheable is False
    assert mq.metadata.source_class is None

    qp = agent_tools.TOOL_REGISTRY["qgis_process"]
    assert qp.metadata.ttl_class == "live-no-cache"
    assert qp.metadata.cacheable is False
    assert qp.metadata.source_class is None


def test_misconfigured_metadata_fails_at_construction():
    """FR-CE-8 fail-fast: cacheable=True + ttl_class='live-no-cache' rejects.

    The cross-field validator on ``AtomicToolMetadata`` runs at pydantic
    construction time, so a misconfigured ``@register_tool`` call dies
    before the decorator factory even sees it.
    """
    with pytest.raises(Exception):
        AtomicToolMetadata(
            name="bad",
            ttl_class="live-no-cache",
            source_class="bad",
            cacheable=True,
        )


def test_register_with_adk_appends_each_tool_to_agent_tools(empty_registry):
    """``register_with_adk`` iterates the snapshot and binds each fn to ADK.

    We don't want to import google-adk in tests, so we stub the
    FunctionTool symbol with a no-op wrapper via monkey-patching the lazy
    import.
    """

    @register_tool(
        AtomicToolMetadata(
            name="aaa", ttl_class="static-30d", source_class="x", cacheable=True
        )
    )
    def aaa() -> None:
        return None

    @register_tool(
        AtomicToolMetadata(
            name="bbb", ttl_class="dynamic-1h", source_class="y", cacheable=True
        )
    )
    def bbb() -> None:
        return None

    # Fake ADK Agent with a mutable tools list.
    class FakeAgent:
        def __init__(self) -> None:
            self.tools: list[object] = []

    agent = FakeAgent()
    n = agent_tools.register_with_adk(agent)
    assert n == 2
    assert len(agent.tools) == 2
