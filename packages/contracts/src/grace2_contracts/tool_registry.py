"""Atomic-tool registration metadata (FR-DC-2, FR-CE-8, FR-AS-3).

This module owns ``AtomicToolMetadata`` — the pydantic v2 model every
external-API atomic tool declares at registration time so the cache shim
(SRS §3.9 / FR-DC-1..6) can route the call correctly. ``agent`` consumes
this model in the ADK FunctionTool registry; ``schema`` owns the shape.

Why a dedicated ``tool_registry`` module rather than extending ``agent.py``
(which currently holds tool-docstring conventions and the
``tool_category`` vocabulary)?

- ``tool_metadata`` is convention-only (docstring sections, allowed
  ``tool_category`` strings). It carries no pydantic model.
- ``AtomicToolMetadata`` IS a pydantic v2 model with a cross-field
  ``model_validator`` — a different shape of contract surface. Mixing
  validators into a convention-only module would obscure both.
- The agent service will likely accrete other tool-registration models
  (tool-result schemas, retry-policy descriptors, etc.); giving the
  registry its own module keeps the seam clean.

The four TTL classes match SRS §3.9 FR-DC-2 verbatim. Misconfigured tools
fail-fast at import time (FR-CE-8: "cache class is a required property
validated at tool-registration time").

Invariants this module is responsible for:
- **Invariant 1 (Determinism boundary).** ``ttl_class`` is workflow-declared,
  never LLM-judged; the validator refuses inconsistent combinations.
- **Invariant 9 (No cost theater).** No cost / dollar / latency-estimate
  fields. The cache shim's job is correctness + freshness, not pricing.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import GraceModel

__all__ = [
    "TTLClass",
    "TTL_CLASSES",
    "AtomicToolMetadata",
]


#: The four TTL classes registered per atomic tool (SRS FR-DC-2).
#:
#: Names match the kickoff verbatim. NOTE: SRS FR-DC-2 prose at
#: ``docs/srs/03-functional-requirements.md`` describes the live class as
#: "encoded as ``ttl_class: 'none'``" — that prose-vs-kickoff naming gap is
#: surfaced as an Open Question in this job's report. The pydantic value here
#: is ``"live-no-cache"`` (kickoff-frozen); a follow-up SRS amendment may
#: harmonize the prose to the same literal.
TTLClass = Literal["static-30d", "semi-static-7d", "dynamic-1h", "live-no-cache"]

#: Tuple form of the four TTL classes (useful for parametrized tests + the
#: agent-side registry's known-class assertions).
TTL_CLASSES: tuple[str, ...] = (
    "static-30d",
    "semi-static-7d",
    "dynamic-1h",
    "live-no-cache",
)


class AtomicToolMetadata(GraceModel):
    """Cache-shim metadata for an atomic tool's registration (FR-CE-8, FR-DC-2).

    Every atomic tool that may issue a network call to an external public data
    source declares one of these at registration time. The agent service's
    tool-registry refuses to register a tool whose metadata is missing,
    incomplete, or fails the cross-field validator below.

    Fields:

    - ``name`` — atomic-tool function name (Python identifier, e.g.
      ``"fetch_dem"``). The agent registry uses this as the registry key.
    - ``ttl_class`` — one of the four FR-DC-2 classes. Required for every
      external-API tool. ``"live-no-cache"`` is reserved for the FR-DC-6
      uncacheable-by-construction enumeration (interactive solicitation
      tools, envelope emitters, MongoDB writes, solver dispatchers).
    - ``source_class`` — the ``<source-class>`` prefix in the cache bucket
      layout per FR-DC-1 (e.g. ``"dem"``, ``"buildings"``, ``"geocode"``).
      Required when ``cacheable=True``; MAY be omitted when ``cacheable=False``
      (no bucket prefix is needed if nothing is written).
    - ``cacheable`` — explicit boolean for FR-DC-6 enumeration; defaults to
      ``True`` because the cacheable case is the common case. ``False`` for
      interactive solicitation tools, envelope emitters, MongoDB writes,
      and solver dispatchers per FR-DC-6.

    Cross-field rule (``_validate_cacheable_consistency``):

    - ``cacheable=True`` ⇒ ``ttl_class != "live-no-cache"`` AND
      ``source_class`` is non-empty. A cacheable tool with a live-no-cache
      class would never hit; a cacheable tool with no source_class can't
      construct a cache key path.
    - ``cacheable=False`` ⇒ ``ttl_class == "live-no-cache"``. The other
      classes would suggest the cache is in play.

    The validator runs at construction time, so a misconfigured registration
    raises ``ValidationError`` before the tool is reachable on the wire.
    """

    name: str = Field(min_length=1)
    ttl_class: TTLClass
    source_class: str | None = None
    cacheable: bool = True

    @model_validator(mode="after")
    def _validate_cacheable_consistency(self) -> AtomicToolMetadata:
        """Enforce the FR-DC-6 cross-field consistency rule."""
        if self.cacheable:
            if self.ttl_class == "live-no-cache":
                raise ValueError(
                    "cacheable=True is inconsistent with ttl_class='live-no-cache'; "
                    "a cacheable tool must declare static-30d / semi-static-7d / dynamic-1h."
                )
            if not self.source_class:
                raise ValueError(
                    "cacheable=True requires a non-empty source_class "
                    "(used as the <source-class> prefix in gs://<bucket>/cache/<source-class>/<hash>.<ext>)."
                )
        else:
            if self.ttl_class != "live-no-cache":
                raise ValueError(
                    f"cacheable=False requires ttl_class='live-no-cache'; "
                    f"got ttl_class={self.ttl_class!r}."
                )
        return self
