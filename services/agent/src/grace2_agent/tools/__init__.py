"""Atomic-tool registry skeleton (FR-AS-3, FR-CE-8, FR-TA-2, Decision O).

This package is the agent-service-owned surface for atomic tools (M4 substrate).
``schema`` owns ``AtomicToolMetadata`` (in ``grace2_contracts.tool_registry``);
``agent`` owns the registry that collects the decorated functions at import
time and the cache shim that mediates external-API calls (see ``.cache``).
Pass-through tools (``mongo_query``, ``qgis_process``) live in ``.passthroughs``.

How registration works:

    from grace2_contracts.tool_registry import AtomicToolMetadata
    from grace2_agent.tools import register_tool

    @register_tool(AtomicToolMetadata(
        name="fetch_dem",
        ttl_class="static-30d",
        source_class="dem",
        cacheable=True,
    ))
    def fetch_dem(bbox: BBox) -> str:
        ...

The ``@register_tool`` decorator:

- Re-validates the metadata payload (pydantic auto-validates at construction;
  passing an already-validated model just stores it) and refuses to register
  a tool whose metadata fails the FR-DC-6 cross-field rule.
- Stores ``(fn, metadata, module)`` in module-level ``TOOL_REGISTRY``
  keyed by ``metadata.name``.
- **Fails fast on duplicate names** per FR-CE-8: a second registration under
  the same name raises ``ToolRegistrationError`` at import time so the
  agent service cannot start with an inconsistent tool surface.
- Returns the original function unchanged so direct-call testing is trivial.

The ``get_registered_tools()`` helper returns the current registry contents
(a snapshot list) for the agent service's startup-time ADK FunctionTool
registration; ``register_with_adk(agent)`` is the convenience wrapper that
iterates the snapshot and calls ``agent.tools.append(FunctionTool(...))``
(or whatever the ADK API of the day is — kept thin so ADK churn is contained
to this one site).

Importing the package triggers ``@register_tool`` decorators in submodules
(``.passthroughs`` for M4 job-0032; ``.fetchers`` etc. for M4 job-0033+).
We import them eagerly here so any registration-time ``ValidationError`` or
``ToolRegistrationError`` surfaces at startup (FR-CE-8 fail-fast).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from grace2_contracts.tool_registry import AtomicToolMetadata

__all__ = [
    "RegisteredTool",
    "ToolRegistrationError",
    "TOOL_REGISTRY",
    "register_tool",
    "get_registered_tools",
    "clear_registry_for_tests",
    "register_with_adk",
]


class ToolRegistrationError(RuntimeError):
    """Raised when a tool fails registration (duplicate name, bad metadata)."""


@dataclass(frozen=True)
class RegisteredTool:
    """A tool entry in ``TOOL_REGISTRY``.

    Fields:
    - ``metadata`` — the validated ``AtomicToolMetadata`` for the tool.
    - ``fn`` — the original (undecorated) callable. The registry deliberately
      does NOT wrap it; tests call the function directly via this attribute.
    - ``module`` — the ``__module__`` attribute at registration time, useful
      for diagnostics (`"grace2_agent.tools.passthroughs"` etc.).
    """

    metadata: AtomicToolMetadata
    fn: Callable[..., Any]
    module: str


#: Module-level registry, keyed by ``metadata.name``. Populated at import time
#: by ``@register_tool`` calls in submodules. The agent service iterates this
#: at startup to register each tool with ADK (see ``register_with_adk``).
TOOL_REGISTRY: dict[str, RegisteredTool] = {}


def register_tool(
    metadata: AtomicToolMetadata,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Return a decorator that records ``fn`` + ``metadata`` in ``TOOL_REGISTRY``.

    Usage::

        @register_tool(AtomicToolMetadata(name="x", ttl_class="static-30d",
                                          source_class="x"))
        def x(...): ...

    Fail-fast invariants (FR-CE-8):

    - ``metadata`` must already be a valid ``AtomicToolMetadata`` (pydantic
      auto-validates at construction, including the FR-DC-6 cross-field
      ``cacheable``/``ttl_class``/``source_class`` rule). Passing anything
      else raises ``TypeError``.
    - The same ``metadata.name`` cannot register twice. A duplicate raises
      ``ToolRegistrationError`` at import time so a misconfigured agent
      service never starts.
    - The original ``fn`` is returned UNCHANGED, so callers can both register
      a tool and call it directly in tests.
    """
    if not isinstance(metadata, AtomicToolMetadata):
        raise TypeError(
            f"register_tool expects AtomicToolMetadata, got {type(metadata).__name__}"
        )

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        name = metadata.name
        existing = TOOL_REGISTRY.get(name)
        if existing is not None:
            raise ToolRegistrationError(
                f"tool {name!r} is already registered "
                f"(existing from module {existing.module!r}, "
                f"new from module {fn.__module__!r}); duplicate registrations "
                f"are rejected at import time per FR-CE-8."
            )
        TOOL_REGISTRY[name] = RegisteredTool(
            metadata=metadata, fn=fn, module=fn.__module__
        )
        return fn

    return _decorator


def get_registered_tools() -> list[RegisteredTool]:
    """Return a stable-ordered snapshot of the current registry.

    Used by the agent service at startup to register each tool with ADK.
    Sorted by ``metadata.name`` so the registration order is deterministic
    across runs (important for FR-AS-3 review diffs).
    """
    return sorted(TOOL_REGISTRY.values(), key=lambda t: t.metadata.name)


def clear_registry_for_tests() -> None:
    """Empty the registry. ONLY for tests; never call from product code.

    Atomic-tool registration is import-time; tests that need a fresh registry
    or want to swap implementations call this in a fixture.
    """
    TOOL_REGISTRY.clear()


def register_with_adk(agent: Any) -> int:
    """Register every tool in ``TOOL_REGISTRY`` with an ADK ``Agent`` instance.

    The actual ADK API for adding a function tool varies across releases
    (``Agent.tools.append(FunctionTool(fn))`` or ``agent.add_tool(...)``); we
    keep the import + adaptation contained here so future ADK churn is a
    single-site edit, not a registry-wide refactor.

    Returns the number of tools registered. Raises ``ToolRegistrationError``
    if the ADK import path cannot be resolved, so a misconfigured runtime
    fails at startup rather than silently exposing zero tools.
    """
    try:
        from google.adk.tools import FunctionTool  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001 — surface as ToolRegistrationError
        raise ToolRegistrationError(
            f"could not import google.adk.tools.FunctionTool: {exc}"
        ) from exc

    snapshot = get_registered_tools()
    for tool in snapshot:
        # ADK FunctionTool wraps a Python callable; the description / param
        # surface is derived from the function's signature + docstring per
        # FR-AS-3. Docstring discipline (Use this when / Do NOT use this for)
        # is enforced on the source functions themselves, not here.
        ft = FunctionTool(tool.fn)
        # ADK Agent objects expose ``tools`` as a mutable list in google-adk
        # 2.x; keep this thin and let the duck-type fail loudly if it doesn't.
        agent.tools.append(ft)
    return len(snapshot)


# ---------------------------------------------------------------------------
# Eager submodule import (FR-CE-8 fail-fast).
#
# Importing ``grace2_agent.tools`` should populate ``TOOL_REGISTRY`` with
# every atomic tool the agent service supports. Submodules are imported here
# so their import-time ``@register_tool`` calls fire even if no other code
# references them. Keep this list narrow: only submodules whose tools should
# always be available at startup belong here.
# ---------------------------------------------------------------------------
from . import passthroughs  # noqa: E402,F401 — registers mongo_query, qgis_process
from . import compute_colored_relief  # noqa: E402,F401 — job-0080: registers compute_colored_relief
from . import compute_slope  # noqa: E402,F401 — job-0081: registers compute_slope
from . import compute_aspect  # noqa: E402,F401 — job-0082: registers compute_aspect
from . import compute_zonal_statistics  # noqa: E402,F401 — job-0083: registers compute_zonal_statistics
from . import clip_raster_to_bbox  # noqa: E402,F401 — job-0085: registers clip_raster_to_bbox
from . import fetch_administrative_boundaries  # noqa: E402,F401 — job-0084: registers fetch_administrative_boundaries
from . import compute_hillshade  # noqa: E402,F401 — job-0079: registers compute_hillshade
from . import fetch_wdpa_protected_areas  # noqa: E402,F401 — job-0089: registers fetch_wdpa_protected_areas
from . import fetch_gbif_occurrences  # noqa: E402,F401 — job-0087: registers fetch_gbif_occurrences
from . import fetch_inaturalist_observations  # noqa: E402,F401 — job-0088: registers fetch_inaturalist_observations
from . import web_fetch  # noqa: E402,F401 — job-0092: registers web_fetch
from . import fetch_storm_events_db  # noqa: E402,F401 — job-0091: registers fetch_storm_events_db
from . import fetch_nws_event  # noqa: E402,F401 — job-0090: registers fetch_nws_event
from . import aggregate_claims_across_sources  # noqa: E402,F401 — job-0093: registers aggregate_claims_across_sources
from . import extract_landcover_class  # noqa: E402,F401 — job-0094: registers extract_landcover_class
from . import compute_impervious_surface  # noqa: E402,F401 — job-0095: registers compute_impervious_surface
from . import compute_building_density  # noqa: E402,F401 — job-0096: registers compute_building_density
from . import fetch_roads_osm  # noqa: E402,F401 — job-0097: registers fetch_roads_osm
from . import run_pelicun_damage_assessment  # noqa: E402,F401 — job-0098: registers run_pelicun_damage_assessment (Wave 1 stub; Wave 2 composer is job-0106)
