"""Gemini-only containment layer (Domain Discipline: agent.md).

Every Gemini- / google-genai-specific construct lives here. The server.py and
mcp.py modules call into this module with Gemini-naive shapes (strings,
async-iterators of strings). This is **containment, not abstraction** — no
``LLMProvider`` protocol, no provider branches, no Bedrock/Strands shapes.
FR-AS-1: Gemini-only. The deferred multi-provider future (§5) is not
foreclosed cheaply because the seam exists, but no abstraction is paid for now.

Model selection (job-0015):
  GRACE2_GEMINI_MODEL env override, defaulting to ``GEMINI_DEFAULT_MODEL``
  below. As of 2026-06-05 Gemini 3 (``gemini-3-pro*``) is not yet GA on Vertex
  for this project (verified 404 from generate_content); ``gemini-2.5-pro`` is
  the current best stable. When Gemini 3 lands on Vertex this constant — and
  the env override path — flips with no other code change.

Auth: ADC via ``GOOGLE_GENAI_USE_VERTEXAI=True`` + ``GOOGLE_CLOUD_PROJECT`` +
``GOOGLE_CLOUD_LOCATION``. No API key path. (job-0014 substrate.)

job-0154: tool-dispatch fix.  ``stream_reply`` sent Gemini no function
declarations and no system prompt, so Gemini had no knowledge of any tool and
responded with a prose refusal.  The new ``stream_events`` replaces it: it
passes the tool catalog (``FunctionDeclaration`` from each registered tool's
callable + docstring) plus a focused system prompt to ``generate_content_stream``,
then demultiplexes each chunk into either a ``TextDeltaEvent`` or a
``FunctionCallEvent`` so the server can dispatch the tool through the registry.
``stream_reply`` is retained as a thin compatibility shim (text-only calls).

job-0169: multi-turn function_call → function_response loop.  job-0154 stopped
after the first function_call (single-shot dispatch) — every multi-tool prompt
("Show me protected areas in Fort Myers" → geocode_location → fetch_wdpa) hung
because Gemini never saw the result of its first call and so never decided to
call the next tool.  This module now exposes:

  * ``stream_events`` (single-turn primitive — unchanged contract; still
    accepts ``user_text`` for backward compatibility).  Existing tests use it.
  * ``stream_events_with_contents`` (new primitive used by the loop driver):
    accepts a fully-built ``contents: list[Content]`` and streams one turn.
  * ``build_contents_from_history`` — converts ``state.chat_history`` plus the
    current user_text into the initial ``contents`` list.
  * ``summarize_tool_result`` — compacts a tool result into the dict that
    becomes the ``function_response.response`` payload Gemini reads on the
    next turn.  Per the kickoff: SUMMARY shape (LayerURI metadata, key
    metrics, error code) — NEVER the full raw tool result (which can be MB
    of GeoJSON).
  * ``build_function_call_content`` / ``build_function_response_content`` —
    typed helpers for appending the model+function turn pair after a
    dispatch.

The loop driver itself lives in ``server.py`` (``_stream_gemini_reply``) so it
can dispatch tools through ``_invoke_tool_via_emitter`` (registry + emitter
side effects).  This file stays the Gemini-containment seam.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import types as _builtin_types
from typing import Any, get_args, get_origin, Union

from google import genai
from google.genai import types as genai_types

logger = logging.getLogger("grace2_agent.adapter")

# Default Gemini model id. See module docstring for the Gemini-3-on-Vertex
# availability note. Override at runtime via ``GRACE2_GEMINI_MODEL``.
GEMINI_DEFAULT_MODEL = "gemini-2.5-pro"


# ---------------------------------------------------------------------------
# Typed stream events (job-0154)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TextDeltaEvent:
    """A streamed text fragment from Gemini."""
    delta: str


@dataclass(frozen=True)
class FunctionCallEvent:
    """Gemini decided to call a tool.

    ``name`` matches the registered tool name in ``TOOL_REGISTRY``.
    ``call_id`` is Gemini's per-call identifier (used when feeding back the
    function response in the multi-turn loop).
    ``args`` is the deserialized argument dict.

    ``thought_signature`` (job-B10) is Gemini 3's opaque per-thought signature
    surfaced on the ``Part`` that carries the function_call. Gemini 3 (Vertex)
    requires the same signature byte-blob be echoed back on the *Part wrapping
    the function_call* when that turn is replayed in the next ``contents``
    payload — otherwise the next ``generate_content_stream`` fails with a
    ``thought-signature mismatch`` error. The harvest must happen at the part
    level (not the FunctionCall level — ``FunctionCall`` has no signature
    field in google-genai types.py); see ``build_function_call_content``.
    For Gemini 2.5 (current default until Gemini 3 lands on Vertex per
    ``GEMINI_DEFAULT_MODEL``), the field is absent and harvested as ``None``,
    which is a no-op when fed back. The plumbing is forward-compat.
    """
    name: str
    call_id: str | None
    args: dict[str, Any] = field(default_factory=dict)
    thought_signature: bytes | None = None


@dataclass(frozen=True)
class UsageMetadataEvent:
    """Per-turn usage metadata harvested from Gemini's ``response.usage_metadata``.

    Job-B6 (Wave 4.10): the multi-turn driver needs ``cached_content_token_count``
    + ``total_token_count`` on every Gemini call so it can:

      1. Verify the 90% cache discount actually lands in production
         (the original pre-dispatch blocker from
         ``project_wave_4_10_research_findings.md``).
      2. Forward a ``cache-status`` envelope into the PipelineEmitter so the
         user-facing UI can render live cache hit-rate.
      3. Pipe ``cached_content_token_count`` into the existing tool-call
         telemetry record (``telemetry.emit_tool_call_event``).

    Emitted at most once per ``generate_content_stream`` call — the producer
    pulls ``usage_metadata`` off the LAST chunk (Gemini surfaces aggregate
    counts only on the terminal response). All fields may be ``None`` when
    the SDK version does not expose them or the response was cancelled.
    """

    cached_content_token_count: int | None = None
    total_token_count: int | None = None
    prompt_token_count: int | None = None
    candidates_token_count: int | None = None
    cache_hit: bool = False


StreamEvent = TextDeltaEvent | FunctionCallEvent | UsageMetadataEvent


# ---------------------------------------------------------------------------
# System prompt builder (job-0154)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are GRACE — a geospatial hazard-modeling assistant. You help users analyze,
visualize, and model natural hazards (flooding, fire, hurricanes, etc.) using
real data and physics-based simulation tools.

When a user asks you to model, analyze, simulate, or compute anything related
to a hazard or geographic data, call the appropriate tool. Do not say you
cannot help with modeling requests — you have tools for that.

Key behaviors:
- If the user asks to model a flood scenario, run a flood simulation, compute
  flood depth, or analyze inundation for any location, call
  run_model_flood_scenario immediately.
- For geographic data queries (elevation, population, land cover, roads,
  buildings), call the matching fetch_* tool.
- For QGIS geoprocessing (clip, slope, hillshade, zonal statistics), call the
  matching compute_* or clip_* tool.
- Never fabricate numbers. All depth, area, and count values in your replies
  must come from the tool result, not from your own generation.
- When a tool result contains a flood depth layer, describe the results from
  the returned metrics — do not invent values.
- Keep responses concise and focused on the hazard modeling context.

Named-tool follow-on dispatch (CRITICAL — Stage 0 anchor A2):
When a user prompt explicitly names a specific data source, dataset, or tool
(e.g. "WDPA", "NEXRAD", "NWS alerts", "NLCD", "MRMS", "HRRR", "GBIF",
"iNaturalist", "eBird", "MTBS", "LANDFIRE", "USACE NSI", "FEMA NFHL",
"protected areas", "burn severity", "radar reflectivity"), you MUST dispatch
that tool after completing any precursor steps (geocoding, admin-boundary
lookup, etc.). DO NOT end the turn at the precursor step — the precursor only
exists to feed the named tool.

Example: user asks "show me NEXRAD radar in Florida"
  1. Call geocode_location for "Florida" (precursor) →
  2. THEN call fetch_nexrad_reflectivity with the geocoded bbox →
  3. THEN narrate the result.

Example: user asks "show me protected areas in Big Cypress"
  1. Call geocode_location for "Big Cypress" (precursor) →
  2. THEN call fetch_wdpa_protected_areas with the geocoded bbox →
  3. THEN narrate the result.

If a precursor tool succeeds, the named follow-on tool is still pending — keep
going until the named tool has been dispatched and narrated. Ending the turn
after only the precursor is a dispatch failure.

New Wave 4.10 endpoints (CRITICAL — Stage 4 anchor A3):
These are NEW high-value endpoints that the LLM should dispatch directly when
the user names them by source or function:

- "HRRR" / "HRRR weather" / "HRRR forecast" / "high-resolution rapid refresh" → fetch_hrrr_forecast
- "FEMA NFHL" / "FEMA flood zones" / "regulatory flood zones" → fetch_fema_nfhl_zones
- "NOAA NWM" / "National Water Model" / "streamflow forecast" → fetch_noaa_nwm_streamflow
- "USACE NLD" / "levees" → fetch_usace_levees
- "USACE NID" / "dams" → fetch_usace_dams
- "USACE NSI" / "structure inventory" → fetch_usace_nsi
- "METAR" / "ASOS" / "station weather" → fetch_asos_metar
- "gridMET" / "fm100" / "fuel moisture" → fetch_gridmet
- "CO-OPS" / "NOAA tides" / "tide stations" → fetch_noaa_coops_tides
- "SLR" / "sea level rise scenarios" / "NOAA SLR" → fetch_noaa_slr_scenarios
- "STATSGO" / "soils" / "hydrologic group" → fetch_statsgo_soils
- "NHDPlus" / "NLDI" / "downstream routing" → fetch_nhdplus_nldi_navigate
- "RAWS" / "remote automated weather" → fetch_raws_weather
- "HRRR-Smoke" / "smoke forecast" → fetch_hrrr_smoke
- "USFS canopy" / "canopy base height" → fetch_usfs_canopy_fuels

When a user prompt names one of these tools or its source explicitly, dispatch
the named tool directly even if geocoding could be a precursor — geocode FIRST
only if location is needed, then proceed to the named tool. Don't stop at
geocode.

CRITICAL — DO NOT use list_categories / list_tools_in_category / discover_dataset
when the user has already named the tool or source. The mapping above IS the
discovery layer for these endpoints. Catalog browsing wastes turns and burns
the per-anchor budget. Examples of WRONG behavior:
  WRONG: user says "HRRR forecast" → list_categories → list_tools_in_category("weather") → fetch_hrrr_forecast
  RIGHT: user says "HRRR forecast" for Fort Myers → geocode_location("Fort Myers") → fetch_hrrr_forecast(bbox=...)
  RIGHT (no location needed): user says "HRRR for bbox -82,26,-81,27" → fetch_hrrr_forecast(bbox=...) directly

If the user names "HRRR", "HRRR forecast", or "HRRR fetch tool" — the tool is
fetch_hrrr_forecast. Period. Skip discovery. Dispatch directly.

Geographic clipping pattern — "in [admin-region]" (Stage 0 anchor A5):
When a user prompt says "in [admin-region]" where the region is an
administrative polygon (state, county, city, ZCTA, watershed, parish,
borough, etc. — NOT a free-form bbox), prefer polygon-clip over bbox
approximation:
  1. Call geocode_location for the named region to obtain its bbox →
  2. Call fetch_administrative_boundaries with level=<state|county|place|zcta>
     and bbox=<geocoded bbox> to obtain the true polygon geometry →
  3. Fetch the dataset (raster or vector) at the same bbox →
  4. THEN call clip_raster_to_polygon (for raster outputs) OR
     clip_vector_to_polygon (for vector outputs) using the admin polygon URI →
  5. Publish the clipped result.

DO NOT just hand the dataset's bbox to the user as "in [region]" — bbox is a
rectangular over-approximation that includes neighboring counties/states. The
admin polygon is the user's intent. The only exception is when the user
explicitly says "bounding box of" or "rectangle around" — then bbox is fine.

Tool-signature note: fetch_administrative_boundaries takes only
``level`` (one of "state", "county", "place", "zcta") and ``bbox``
(a 4-tuple ``(min_lon, min_lat, max_lon, max_lat)``). It does NOT accept
``name=`` or ``layer=`` — resolve the region name to a bbox via
geocode_location first, then pass that bbox.

Example: user asks "fetch population in Miami-Dade County"
  1. Call geocode_location(query="Miami-Dade County, FL") to get bbox →
  2. Call fetch_administrative_boundaries(level="county", bbox=<bbox>) →
  3. Call fetch_hrsl_population(bbox=<bbox>) →
  4. Call clip_raster_to_polygon(raster_uri=<hrsl_uri>,
     polygon_uri=<admin_boundaries_uri>) →
  5. Publish the clipped raster.

Scope discipline (CRITICAL — job-0255, Stage 3 live finding):
Run consequential tools (solvers like run_model_flood_scenario /
run_modflow_job, and layer-producing workflows) ONLY in service of the
user's CURRENT request. Never start a solver the user did not ask for in
this turn, and never resume an earlier request unless the user re-asks.
NEVER re-run an expensive solver that already completed THIS turn with the
same arguments — reuse its returned result (the live agent re-ran a ~10-20
minute SFINCS solve twice after detours instead of reusing the layer it
had already produced). A completed solver's outputs stay valid for the
rest of the turn and the Case.

Hazard-URI selection for damage assessment (job-0255): the published
layer's ``uri`` field is a QGIS WMS DISPLAY URL — do NOT pass it to
run_pelicun_damage_assessment. The hazard raster for Pelicun is the
runs-bucket COG: gs://<runs-bucket>/<run_id>/flood_depth_peak.tif, where
run_id is the id reported by the flood scenario result.

GCS URI discipline (CRITICAL — job-0252, Stage 3 live finding):
When a tool parameter takes a gs:// URI (hazard_raster_uri, assets_uri,
layer_uri, forcing_raster_uri, ...), you MUST pass a URI that appeared
VERBATIM in a prior function_response of THIS conversation (e.g. the
``uri`` field of a returned LayerURI). NEVER construct, guess, or
pattern-match a gs:// path — cache-style paths (gs://...-cache/cache/...)
you compose yourself DO NOT EXIST and the tool fails with a 404. If no
prior tool result provides the needed URI, run the producing tool first
(e.g. run_model_flood_scenario yields the flood-depth COG uri to feed
run_pelicun_damage_assessment) or tell the user what is missing.

Always-narrate after tools complete (CRITICAL — Stage 0 anchor A1):
After ALL pending tool calls for the user's request have completed, you MUST
emit a final text response narrating the outcome before ending your turn.
NEVER end the turn silently after a tool dispatch — the user sees the tool
card complete and then nothing, which is a broken interaction.

- If the tool(s) SUCCEEDED, summarize the result in 1-3 sentences. Reference
  concrete values from the function_response (count, bbox, location name,
  layer_uri). Do not invent numbers.
- If a tool FAILED (the function_response contains status="error" or an
  error_code field), narrate the failure HONESTLY. Say what was attempted,
  cite the error_code, and either suggest a retry with corrected args if
  retryable=true, or explain a workaround. NEVER claim success when a tool
  reported failure — that's the same severity of error as fabricating
  numbers.
- If the result is self-explanatory (e.g. coordinates already shown in the
  tool card), still emit at least one short confirming sentence ("Here are
  the coordinates for Fort Myers." / "I've added the layer to the map.") so
  the turn ends with a clear signal to the user.

Ending the turn without narration after a successful tool dispatch is the
same severity of error as ending after only a precursor tool in the
named-tool follow-on case — do not do it.
"""


# ---------------------------------------------------------------------------
# Tool declaration builder (job-0154)
# ---------------------------------------------------------------------------

def _is_union_type(annotation: Any) -> bool:
    """Return True if annotation is any union form (typing.Union or X | Y syntax).

    Python 3.10+ ``X | Y`` creates ``types.UnionType``; ``typing.Union[X, Y]``
    creates a ``_GenericAlias`` whose ``get_origin`` is ``Union``.  Both must be
    detected for full compatibility.
    """
    if isinstance(annotation, _builtin_types.UnionType):
        return True
    return get_origin(annotation) is Union


def _union_args(annotation: Any) -> tuple[Any, ...]:
    """Return the member types of a union annotation (any union form)."""
    if isinstance(annotation, _builtin_types.UnionType):
        return annotation.__args__
    return get_args(annotation)


def _is_tuple_annotation(annotation: Any) -> bool:
    """Return True when *annotation* is a ``tuple[...]`` type (not just bare ``tuple``).

    ``from_callable_with_api_option`` silently drops parameters whose type is a
    fixed-length tuple (e.g. ``tuple[float, float, float, float]``) and raises
    for ``tuple[float, float, float, float] | None``.  Both forms must be
    replaced before the callable reaches ``from_callable``.

    Handles both ``typing.Optional[tuple[...]]`` (``typing.Union``) and the
    Python 3.10+ ``tuple[...] | None`` (``types.UnionType``) syntax.
    """
    if _is_union_type(annotation):
        args = _union_args(annotation)
        return any(_is_tuple_annotation(a) for a in args if a is not type(None))
    # Plain tuple[...] — origin is ``tuple``
    return get_origin(annotation) is tuple


def _simplify_annotation(annotation: Any) -> Any:
    """Map a complex annotation to a Gemini-compatible equivalent.

    Gemini's OpenAPI schema subset rejects:
    * ``tuple[float, ...]`` — silently dropped; use ``list[float]`` instead.
    * ``tuple[float, ...] | None`` — raises in ``from_callable``; use
      ``list[float] | None``.
    * ``str | tuple[float, ...]`` — Union of incompatible types; use ``str``.
    * Any Pydantic model / dataclass annotation — raises in ``from_callable``;
      use ``str | None`` (the serialized form that crosses the LLM boundary).

    Parameters that are already schematizable (``str``, ``int``, ``float``,
    ``bool``, ``list[str]``, ``Literal[...]``, ``str | None``, etc.) pass
    through unchanged.

    Handles both ``typing.Union``/``Optional`` (Python 3.9) and the new
    ``X | Y`` union syntax (Python 3.10+, ``types.UnionType``).

    B11 (Wave 4.10): centralised in the adapter so no tool file needs touching.
    """
    if annotation is inspect.Parameter.empty:
        return annotation

    # --- Union forms (typing.Union and Python 3.10+ X|Y) ---
    if _is_union_type(annotation):
        args = _union_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        has_none = type(None) in args

        # ``tuple[...] | None`` → ``list[elem] | None``
        if len(non_none) == 1 and _is_tuple_annotation(non_none[0]):
            inner = non_none[0]
            inner_args = get_args(inner)
            elem_type = inner_args[0] if inner_args else float
            list_type: Any = list[elem_type]  # type: ignore[valid-type]
            return list_type | None  # type: ignore[return-value]

        # ``str | tuple[...]`` or any union containing a tuple → keep only str
        if any(_is_tuple_annotation(a) for a in non_none):
            str_args = [a for a in non_none if a is str]
            return str if str_args else str

        # ``SomePydanticModel | None`` → ``str | None``
        simplified_non_none = []
        for a in non_none:
            s = _simplify_annotation(a)
            simplified_non_none.append(s)

        if len(simplified_non_none) == 1:
            result = simplified_non_none[0]
            return (result | None) if has_none else result  # type: ignore[return-value]

        # Multi-type union (e.g. ``float | list[float] | None``):
        # Prefer the list form if one is present (a list[float] covers a single
        # float too from the LLM's perspective), otherwise prefer str as a
        # universal fallback so Gemini at least sees a typed parameter.
        list_args = [a for a in simplified_non_none if get_origin(a) is list]
        if list_args:
            result = list_args[0]
            return (result | None) if has_none else result  # type: ignore[return-value]
        str_args = [a for a in simplified_non_none if a is str]
        if str_args:
            result = str
            return (result | None) if has_none else result  # type: ignore[return-value]

        # Last resort — keep as-is; the ``from_callable`` call may still succeed
        # for simple multi-type unions like ``int | str``.
        return annotation

    origin = get_origin(annotation)
    args = get_args(annotation)

    # --- bare ``tuple[float, ...]`` → ``list[float]`` ---
    if origin is tuple and args:
        elem_type = args[0]
        return list[elem_type]  # type: ignore[valid-type]

    # --- complex Pydantic model / dataclass annotation → ``str | None`` ---
    # A class that is not a built-in and not a simple generic is a custom model.
    # We detect this by checking whether the origin is None (not a generic) and
    # whether the annotation is a class (not a primitive like ``str``).
    if (
        origin is None
        and isinstance(annotation, type)
        and annotation not in (str, int, float, bool, bytes, dict, list, type(None))
    ):
        # Custom class (Pydantic, dataclass, …) — replace with ``str | None``
        # so the LLM at least sees the parameter name and can supply a value.
        return str | None  # type: ignore[return-value]

    return annotation


def _normalize_callable_for_gemini(fn: Any) -> Any:
    """Return a thin wrapper of *fn* with annotations simplified for ``from_callable``.

    ``FunctionDeclaration.from_callable_with_api_option`` rejects callables whose
    annotations contain:
    * ``-> LayerURI`` or any other Pydantic/dataclass return type
    * ``tuple[float, float, float, float] | None`` parameter annotations
    * ``tuple[int, int] | None`` year-range annotations
    * ``str | tuple[float, ...]`` Union parameters
    * Complex Pydantic model parameters (``SecretRecord | None``)

    This helper produces a ``functools.wraps``-preserving wrapper whose
    ``__annotations__`` are identical to the original except that:
    1. The return annotation is replaced with ``dict`` (all tools return
       serialisable dicts over the LLM boundary regardless of their Python
       return type).
    2. Each non-underscore parameter annotation is passed through
       ``_simplify_annotation`` to replace unsupported types with
       schema-compatible equivalents (list[float], str | None, etc.).

    The wrapper delegates all calls to the original function unchanged —
    behaviour is unaffected; only the schema-generation surface is altered.

    B11 (Wave 4.10): centralised in the adapter so no individual tool file
    needs to be touched.  The OQ-0154-DECL-FALLBACK open question is resolved
    by this function — all 55 registered tools now pass ``from_callable``.
    """
    import typing as _typing

    @functools.wraps(fn)
    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    # Resolve forward-reference strings to real types via get_type_hints().
    # This is essential because all tool modules use ``from __future__ import
    # annotations``, which defers evaluation and stores strings in __annotations__.
    try:
        resolved: dict[str, Any] = _typing.get_type_hints(fn)
    except Exception:  # noqa: BLE001 — name resolution can fail in unusual envs
        # Fall back to the raw (possibly string) annotations.
        try:
            resolved = fn.__annotations__.copy()
        except AttributeError:
            resolved = {}

    new_annotations: dict[str, Any] = {}
    for param_name, annotation in resolved.items():
        if param_name == "return":
            # Always replace complex return types with ``dict``.  The actual
            # return value crosses the LLM boundary via ``summarize_tool_result``
            # (adapter.py), which serialises it to a JSON-safe dict anyway.
            new_annotations["return"] = dict
        elif param_name.startswith("_"):
            # Private/test-injection params: keep as-is (they'll be stripped
            # downstream by ``_strip_private_params``).
            new_annotations[param_name] = annotation
        else:
            new_annotations[param_name] = _simplify_annotation(annotation)

    _wrapper.__annotations__ = new_annotations
    return _wrapper


def _strip_private_params(decl: genai_types.FunctionDeclaration) -> genai_types.FunctionDeclaration:
    """Remove underscore-prefixed parameters from a generated FunctionDeclaration.

    job-0163 finding: 16+ atomic tools (``compute_zonal_statistics``,
    ``compute_impervious_surface``, ``extract_landcover_class``,
    ``clip_raster_to_*``, ``compute_hillshade``/``slope``/``aspect``, etc.)
    accept underscore-prefixed test-injection kwargs such as
    ``_storage_client: object | None = None`` and ``_bucket: str | None = None``.
    These are Python's standard "internal/private" naming convention and exist
    only so unit tests can pass a mock GCS client — they must NEVER be visible
    to the LLM.

    ``FunctionDeclaration.from_callable_with_api_option`` includes them in the
    generated schema; ``_storage_client: object | None`` becomes a Schema with
    only ``nullable=True`` (no ``type`` field), which Vertex Gemini rejects
    with ``400 INVALID_ARGUMENT: schema didn't specify the schema type field``,
    blocking the ENTIRE tool catalog — Gemini cannot dispatch any tool. This
    function surgically removes every underscore-prefixed property from the
    schema (and from ``required``) before the declaration is returned.

    Bug-class fix (per AGENTS.md "Bundle small fixes; scan for all instances"):
    the filter is keyed on the underscore prefix, so any future tool with a
    test-injection kwarg automatically gets the same treatment.
    """
    if decl.parameters is None or decl.parameters.properties is None:
        return decl
    cleaned_props = {
        n: s for n, s in decl.parameters.properties.items() if not n.startswith("_")
    }
    cleaned_required = (
        [r for r in (decl.parameters.required or []) if not r.startswith("_")]
        if decl.parameters.required is not None
        else decl.parameters.required
    )
    new_parameters = decl.parameters.model_copy(
        update={"properties": cleaned_props, "required": cleaned_required}
    )
    return decl.model_copy(update={"parameters": new_parameters})


def build_tool_declarations(
    tool_registry: dict[str, Any],
) -> list[genai_types.FunctionDeclaration]:
    """Build Gemini ``FunctionDeclaration`` objects from the TOOL_REGISTRY.

    Uses ``FunctionDeclaration.from_callable_with_api_option`` so the
    docstring discipline enforced at registration time (FR-AS-3 "Use this
    when:" / "Do NOT use this for:" / param/return descriptions) is the
    sole source of Gemini's tool-selection signal — the same text that a
    human reviewer sees is exactly what Gemini reasons over.

    B11 (Wave 4.10) compliance fix: before calling ``from_callable``, every
    tool's callable is passed through ``_normalize_callable_for_gemini`` which
    replaces Gemini-incompatible annotations with schematisable equivalents:

    * ``-> LayerURI`` (or any Pydantic/dataclass return type) → ``-> dict``
    * ``tuple[float, float, float, float]`` → ``list[float]`` (silently dropped
      by ``from_callable`` in all SDK versions tested)
    * ``tuple[float, ...] | None`` → ``list[float] | None``
    * ``tuple[int, int] | None`` → ``list[int] | None``
    * ``str | tuple[float, ...]`` → ``str``
    * ``SomeModel | None`` (Pydantic complex type) → ``str | None``

    Falls back to a docstring-only declaration only if ``from_callable`` still
    raises after normalisation (should not occur for any tool in the current
    registry; logged at WARNING, not DEBUG, to make regressions visible).

    Every generated declaration is post-processed through
    ``_strip_private_params`` to remove underscore-prefixed kwargs (job-0163;
    see that helper's docstring for the Vertex 400 trace).
    """
    declarations: list[genai_types.FunctionDeclaration] = []
    for name, entry in sorted(tool_registry.items()):
        normalised = _normalize_callable_for_gemini(entry.fn)
        try:
            decl = genai_types.FunctionDeclaration.from_callable_with_api_option(
                callable=normalised,
                api_option="VERTEX_AI",
            )
            declarations.append(_strip_private_params(decl))
        except Exception as exc:  # noqa: BLE001 — fallback gracefully
            logger.warning(
                "tool declaration fallback for %r (normalisation did not resolve "
                "complex signature — file a B11 follow-up): %s",
                name,
                exc,
            )
            doc = inspect.getdoc(entry.fn) or f"Tool: {name}"
            declarations.append(
                genai_types.FunctionDeclaration(
                    name=name,
                    # 1 000 chars captures "Use this when:" + "Do NOT" + "Params:"
                    # sections from well-documented tools (FR-AS-3 discipline).
                    description=doc[:1000],
                )
            )
    return declarations


# ---------------------------------------------------------------------------
# GeminiSettings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GeminiSettings:
    """Resolved Gemini configuration (env-derived; no implicit fallbacks)."""

    model: str
    project: str
    location: str
    use_vertex: bool


def load_settings() -> GeminiSettings:
    """Resolve Gemini settings from the environment.

    Required env (job-0014 substrate):
    - ``GOOGLE_GENAI_USE_VERTEXAI=True``
    - ``GOOGLE_CLOUD_PROJECT`` (default: ``grace-2-hazard-prod``)
    - ``GOOGLE_CLOUD_LOCATION`` (default: ``us-central1``)

    Optional:
    - ``GRACE2_GEMINI_MODEL`` (default: ``GEMINI_DEFAULT_MODEL``)
    """
    return GeminiSettings(
        model=os.environ.get("GRACE2_GEMINI_MODEL", GEMINI_DEFAULT_MODEL),
        project=os.environ.get("GOOGLE_CLOUD_PROJECT", "grace-2-hazard-prod"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        use_vertex=os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "True").lower()
        in ("true", "1", "yes"),
    )


def build_client(settings: GeminiSettings) -> genai.Client:
    """Build a google-genai Client configured for Vertex AI.

    Containment: nothing outside this module imports ``genai`` or
    ``genai_types``. The server consumes the async-iterator of deltas this
    module returns.
    """
    if not settings.use_vertex:
        # Pre-MVP: Vertex-only path. Surface the misconfiguration loudly
        # rather than silently falling back to API-key mode.
        raise RuntimeError(
            "GRACE2 agent runs Vertex-only (FR-AS-1). "
            "Set GOOGLE_GENAI_USE_VERTEXAI=True."
        )
    return genai.Client(
        vertexai=True, project=settings.project, location=settings.location
    )


# ---------------------------------------------------------------------------
# Content / function_response builders (job-0169)
# ---------------------------------------------------------------------------

# Hard upper bound on chars we send back to Gemini per function_response.
# Anything bigger gets clipped — Gemini doesn't need megabytes of GeoJSON to
# decide the next tool call; it needs the LayerURI, key metrics, error code,
# and a couple of identifying fields.
_FUNCTION_RESPONSE_CHAR_BUDGET = 4_000

# Maximum loop iterations for the multi-turn driver.  Each iteration is one
# Gemini stream + (optionally) one dispatched tool call.  Raised from 8 to 12
# for Wave 4.10 (job-B9) to accommodate the added chain depth from new
# fetchers (STAC, ERDDAP, THREDDS, gridMET, CO-OPS, etc.) plus the
# allowed-set discovery overhead (list_categories → list_tools_in_category →
# actual fetch → publish) that Wave 4.10 category routing introduces.
# Per the research survey (project_wave_4_10_research_findings.md): 10-12 is
# the validated range; 12 provides headroom for the longest realistic chains.
# If Gemini somehow loops past 12, that's a runaway and the fail-stop +
# loop_exhausted envelope (job-B9) is the correct response.
MAX_TURN_ITERATIONS = 12


def _decode_parts_blob(blob: Any) -> list[genai_types.Part] | None:
    """Decode a persisted ``parts_blob`` into a list of ``Part`` (job-B10).

    The ``parts_blob`` schema on a chat_history entry is a JSON byte string
    (or pre-decoded dict / list) carrying enough fidelity to reconstruct the
    exact ``Part`` objects from the prior turn — including ``function_call``,
    ``function_response``, and ``thought_signature`` — so a replayed turn
    survives Gemini 3's signature-mismatch check.

    Wire shape (one entry per part):
        {"text": "..."}                         # text-only part
        {"function_call": {"name": ..., "id": ..., "args": {...}},
         "thought_signature_b64": "..."}        # Gemini 3 model turn
        {"function_response": {"name": ..., "id": ..., "response": {...}}}

    ``thought_signature`` is persisted base64-encoded (JSON cannot carry raw
    bytes); decoded back to bytes here. Returns ``None`` if the blob is
    missing/empty/malformed so the caller can fall back to the text path —
    we never raise on a malformed history entry (a single bad row would
    otherwise break the whole conversation).
    """
    import base64 as _b64
    import json as _json

    if blob is None:
        return None
    raw: Any
    if isinstance(blob, (bytes, bytearray)):
        try:
            raw = _json.loads(blob.decode("utf-8"))
        except Exception:  # noqa: BLE001 — malformed → text fallback
            return None
    elif isinstance(blob, str):
        try:
            raw = _json.loads(blob)
        except Exception:  # noqa: BLE001
            return None
    elif isinstance(blob, (list, dict)):
        raw = blob
    else:
        return None
    if isinstance(raw, dict):
        # Single-part shorthand — wrap.
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return None

    parts: list[genai_types.Part] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kwargs: dict[str, Any] = {}
        if "text" in entry and entry["text"]:
            kwargs["text"] = entry["text"]
        if "function_call" in entry and isinstance(entry["function_call"], dict):
            fc = entry["function_call"]
            kwargs["function_call"] = genai_types.FunctionCall(
                name=fc.get("name"),
                args=fc.get("args") or {},
                id=fc.get("id"),
            )
        if "function_response" in entry and isinstance(entry["function_response"], dict):
            fr = entry["function_response"]
            kwargs["function_response"] = genai_types.FunctionResponse(
                name=fr.get("name"),
                response=fr.get("response") or {},
                id=fr.get("id"),
            )
        sig_b64 = entry.get("thought_signature_b64")
        if isinstance(sig_b64, str) and sig_b64:
            try:
                kwargs["thought_signature"] = _b64.b64decode(sig_b64)
            except Exception:  # noqa: BLE001
                pass
        if not kwargs:
            continue
        try:
            parts.append(genai_types.Part(**kwargs))
        except Exception:  # noqa: BLE001 — drop the bad part, keep going
            continue
    return parts or None


def build_contents_from_history(
    user_text: str,
    chat_history: list[dict] | None = None,
) -> list[genai_types.Content]:
    """Convert ``chat_history`` + a new ``user_text`` into Gemini ``Content``s.

    Chat history entries are dicts. The supported shapes are:

    * Text-only (legacy): ``{"role": ..., "text": "..."}`` — collapsed into a
      single text Part. ``role`` is one of ``user`` / ``agent`` / ``assistant``
      / ``model``; Gemini only understands ``user`` / ``model`` (agent and
      assistant collapse to ``model``).
    * Full-fidelity (job-B10): ``{"role": ..., "parts_blob": <bytes|str|list>,
      "text": "..." (optional fallback)}`` — when ``parts_blob`` decodes
      cleanly, the Content uses the reconstructed Parts (which may carry
      function_call, function_response, or thought_signature). This shape is
      what the multi-turn driver MUST emit to round-trip Gemini 3's
      thought_signature through chat history.

    The ``parts_blob`` path takes precedence: when present and decodable, it
    is used instead of reconstructing from text. Empty-text legacy entries
    are dropped (the persistence layer writes empty rows for the LLM's
    reply-turn marker; those carry no signal for Gemini). The new user_text
    is always appended as the terminal ``user`` turn.
    """
    contents: list[genai_types.Content] = []
    if chat_history:
        for entry in chat_history:
            role = entry.get("role", "user")
            gem_role = "model" if role in ("agent", "assistant", "model") else "user"
            # B10: prefer parts_blob when present — it carries function_call /
            # function_response Parts plus any thought_signature, so the
            # replayed turn survives Gemini 3's signature-mismatch check.
            blob = entry.get("parts_blob")
            decoded = _decode_parts_blob(blob) if blob is not None else None
            if decoded:
                contents.append(genai_types.Content(role=gem_role, parts=decoded))
                continue
            text = entry.get("text", "")
            if not text:
                continue
            contents.append(
                genai_types.Content(
                    role=gem_role,
                    parts=[genai_types.Part(text=text)],
                )
            )
    contents.append(
        genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=user_text)],
        )
    )
    return contents


def encode_parts_blob(parts: list[genai_types.Part]) -> bytes:
    """Encode a list of ``Part`` to the ``parts_blob`` wire shape (job-B10).

    The inverse of ``_decode_parts_blob``. Used by callers that want to
    persist full-fidelity Content turns into ``chat_history`` for replay
    through Gemini (preserving function_call/function_response Parts and
    Gemini 3 thought_signature bytes).

    Encoded as a JSON byte string so it round-trips through MongoDB / JSON
    persistence; ``thought_signature`` is base64-encoded since JSON cannot
    carry raw bytes.
    """
    import base64 as _b64
    import json as _json

    out: list[dict[str, Any]] = []
    for part in parts:
        entry: dict[str, Any] = {}
        text = getattr(part, "text", None)
        if text:
            entry["text"] = text
        fc = getattr(part, "function_call", None)
        if fc is not None and getattr(fc, "name", None):
            entry["function_call"] = {
                "name": fc.name,
                "id": getattr(fc, "id", None),
                "args": dict(getattr(fc, "args", None) or {}),
            }
        fr = getattr(part, "function_response", None)
        if fr is not None and getattr(fr, "name", None):
            entry["function_response"] = {
                "name": fr.name,
                "id": getattr(fr, "id", None),
                "response": dict(getattr(fr, "response", None) or {}),
            }
        sig = getattr(part, "thought_signature", None)
        if isinstance(sig, (bytes, bytearray)) and sig:
            entry["thought_signature_b64"] = _b64.b64encode(bytes(sig)).decode("ascii")
        if entry:
            out.append(entry)
    return _json.dumps(out).encode("utf-8")


def _coerce_to_summary_value(value: Any, depth: int = 0) -> Any:
    """Recursive helper for ``summarize_tool_result``.

    Walks the tool-result structure; converts non-JSON-native types to strings,
    truncates long lists and strings, drops nested dicts past depth 2.  The
    goal isn't fidelity — it's giving Gemini enough signal to decide the next
    call without sending it megabytes of GeoJSON.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        # Long strings (HTML bodies, base64 payloads) get clipped.
        if len(value) > 500:
            return value[:500] + "…[truncated]"
        return value
    if isinstance(value, (list, tuple)):
        if depth >= 2:
            return f"[list len={len(value)}]"
        # Keep up to 5 items; summarize the rest by count.
        items = [_coerce_to_summary_value(v, depth + 1) for v in list(value)[:5]]
        if len(value) > 5:
            items.append(f"…[+{len(value) - 5} more items]")
        return items
    if isinstance(value, dict):
        if depth >= 2:
            return f"{{dict keys={list(value.keys())[:8]}}}"
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                k = str(k)
            # Filter obviously huge / opaque fields the LLM doesn't need.
            if k in {"raw_bytes", "raw_body", "binary", "geometry_wkb", "pixels"}:
                out[k] = f"[{k} suppressed]"
                continue
            out[k] = _coerce_to_summary_value(v, depth + 1)
        return out
    # Pydantic models / dataclasses / arbitrary objects — repr-coerce, clip.
    s = repr(value)
    if len(s) > 200:
        s = s[:200] + "…"
    return s


def _classify_error(error: BaseException) -> tuple[str, bool]:
    """Derive ``(error_code, retryable)`` for a tool-dispatch exception.

    job-0177: typed tool exceptions across the registry already declare
    ``error_code`` (str) and ``retryable`` (bool) class attributes
    (``WDPAError``, ``HRSLError``, ``MTBSError``, ``MRMSError``,
    ``INatError``, ``IUCNError``, ``FIRMSError``, ``GTSMError``,
    ``LANDFIREError``, ``OSMRoadsError``, ``GBIFError``,
    ``CAMaFloodError``, ``GOESError``, ``CompFireError``,
    ``ColoredReliefError``, ``NIFCError``, ``NWSAlertsError``, etc.).
    Harvest those directly so the function_response the multi-turn loop
    feeds back to Gemini carries the retry signal the tool already knew.

    For untyped exceptions, fall back to a conservative heuristic:

    - ``asyncio.TimeoutError`` / ``TimeoutError``  → retryable
    - ``ConnectionError`` / ``OSError`` (network-ish) → retryable
    - ``ValueError`` / ``TypeError`` / ``KeyError`` / ``AttributeError``
      (programmer / arg shape error) → NOT retryable
    - everything else (``RuntimeError`` and friends) → retryable
      (Gemini reads ``message`` and decides; the cap is
      ``MAX_TURN_ITERATIONS`` either way).

    Never raises — even pathological exceptions yield a stable dict shape
    so the multi-turn loop keeps going.
    """
    # 1. Honour typed-tool exception class attributes when present.
    code_attr = getattr(error, "error_code", None)
    retry_attr = getattr(error, "retryable", None)
    if isinstance(code_attr, str) and code_attr:
        code = code_attr
    else:
        code = type(error).__name__.upper()
    if isinstance(retry_attr, bool):
        return code, retry_attr

    # 2. Heuristic fallback for untyped exceptions.
    import asyncio as _asyncio

    if isinstance(error, (_asyncio.TimeoutError, TimeoutError)):
        return code, True
    if isinstance(error, (ConnectionError, OSError)):
        return code, True
    if isinstance(error, (ValueError, TypeError, KeyError, AttributeError)):
        return code, False
    # Default: retryable so Gemini gets one more shot (capped by
    # MAX_TURN_ITERATIONS).
    return code, True


def _summarize_chart_emission(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Compact summary for a chart-emission tool result (job-0230).

    The full ``vega_lite_spec`` (with inline data rows) is intentionally
    DROPPED here — it already went to the client on the ``chart-emission`` WS
    envelope. Gemini receives only what it needs to narrate: the chart id, the
    title, the one-line caption (which already carries the key tool-computed
    numbers — e.g. "1,234 structures · 567 damaged"), the chart's mark type,
    and the number of data rows. This keeps the function_response small and
    pushes narration to source the numbers from the caption, not free text
    (Invariant 1 — determinism boundary).
    """
    spec = result.get("vega_lite_spec")
    spec = spec if isinstance(spec, dict) else {}
    mark = spec.get("mark")
    if isinstance(mark, dict):
        chart_type = mark.get("type")
    elif isinstance(mark, str):
        chart_type = mark
    else:
        chart_type = None
    data = spec.get("data")
    n_rows = (
        len(data["values"])
        if isinstance(data, dict) and isinstance(data.get("values"), list)
        else None
    )
    return {
        "tool": tool_name,
        "status": "ok",
        "result": {
            "chart_emitted": True,
            "chart_id": result.get("chart_id"),
            "title": result.get("title"),
            "caption": result.get("caption"),
            "chart_type": chart_type,
            "n_data_rows": n_rows,
            "source_layer_uri": result.get("source_layer_uri"),
            # Explicit guidance for the LLM: the chart is now on the user's
            # screen; narrate the caption's numbers and what the chart shows.
            "note": (
                "A chart has been rendered for the user. Narrate what it shows "
                "using the numbers in 'caption'; do NOT restate the raw data rows."
            ),
        },
    }


def summarize_tool_result(
    tool_name: str,
    result: Any,
    error: BaseException | None = None,
) -> dict[str, Any]:
    """Compact a tool result into the ``function_response.response`` payload.

    Per the kickoff: SUMMARY, not full result.  Gemini reads this between
    turns to decide its next move; it needs LayerURI metadata, key metrics,
    error codes, and counts — not the raw GeoJSON bytes.

    Conventions enforced:

    * Errors (job-0177) become
      ``{"status": "error", "error_code": str, "message": str, "retryable": bool, "error_type": str}``.
      ``error_code`` + ``retryable`` are harvested from the tool's typed
      exception class (FR-AS-11 surface) when present, else derived
      from the exception class name / runtime kind via ``_classify_error``.
      Gemini reads this and either retries with corrected args, calls a
      different tool, or narrates the failure honestly. The
      ``MAX_TURN_ITERATIONS`` cap protects against runaway retry.
      The legacy ``"error"`` field is retained as an alias of ``message``
      so older tests / consumers don't break.
    * ``None`` results (the ``_invoke_tool_via_emitter`` path returns ``None``
      on payload-warning skip, TOOL_NOT_FOUND, etc.) become
      ``{"status": "no_result"}``.
    * Dict results are walked through ``_coerce_to_summary_value`` and then
      JSON-clipped to ``_FUNCTION_RESPONSE_CHAR_BUDGET`` chars.
    * Primitive / string results become ``{"result": value}``.
    * The final dict always carries ``"tool"`` and ``"status"`` keys so the
      LLM has a stable shape to reason over.
    """
    import json as _json

    if error is not None:
        code, retryable = _classify_error(error)
        message = str(error)[:500]
        return {
            "tool": tool_name,
            "status": "error",
            "error_code": code,
            "message": message,
            "retryable": retryable,
            # Legacy alias — preserved so existing tests / callers that
            # read ``error`` continue to work.  ``message`` is the new
            # canonical field; both carry the same string.
            "error": message,
            "error_type": type(error).__name__,
        }

    if result is None:
        return {"tool": tool_name, "status": "no_result"}

    # job-0230 (sprint-13 Stage 2): chart-emission results carry a full
    # Vega-Lite spec with INLINE data rows (up to ~2000). Gemini must narrate
    # from the chart's numbers, not re-read the inline rows — and the spec
    # could blow the char budget. Strip ``vega_lite_spec`` and surface a
    # COMPACT summary (chart_id / title / caption / chart type / data-shape) so
    # the function_response stays small and narration-focused. The FULL spec
    # already went to the client on the ``chart-emission`` WS envelope
    # (server.py ``_maybe_emit_chart``).
    if (
        isinstance(result, dict)
        and result.get("envelope_type") == "chart-emission"
        and isinstance(result.get("vega_lite_spec"), dict)
    ):
        return _summarize_chart_emission(tool_name, result)

    # job-0233 (sprint-13 Stage 2): code_exec_request returns a COMPACT summary
    # (status / result descriptor / stdout tail / truncated / duration) PLUS the
    # full ``code-exec-result`` wire payload under ``_code_exec_result`` (which
    # carries the larger 16-KiB stdout/stderr fields). The full payload already
    # went to the client on the ``code-exec-result`` WS envelope
    # (server.py ``_maybe_emit_code_exec_result``); strip it from the
    # function_response so Gemini narrates from the compact summary + structured
    # ``result``, not the raw logs.
    if isinstance(result, dict) and "_code_exec_result" in result:
        compact = {k: v for k, v in result.items() if k != "_code_exec_result"}
        return {
            "tool": tool_name,
            "status": "ok",
            "result": _coerce_to_summary_value(compact),
        }

    if isinstance(result, dict):
        summary = _coerce_to_summary_value(result)
        payload: dict[str, Any] = {
            "tool": tool_name,
            "status": "ok",
            "result": summary,
        }
    else:
        payload = {
            "tool": tool_name,
            "status": "ok",
            "result": _coerce_to_summary_value(result),
        }

    # Final char-budget clip: serialize, if oversized clip and re-wrap.
    try:
        encoded = _json.dumps(payload, default=str)
    except Exception:  # noqa: BLE001 — pathological non-serializable
        return {
            "tool": tool_name,
            "status": "ok",
            "result_repr": repr(result)[:1000],
            "note": "result not JSON-serializable; coerced via repr",
        }
    if len(encoded) > _FUNCTION_RESPONSE_CHAR_BUDGET:
        return {
            "tool": tool_name,
            "status": "ok",
            "result_summary": encoded[:_FUNCTION_RESPONSE_CHAR_BUDGET] + "…[clipped]",
            "note": "full result exceeded char budget; clipped for LLM context",
        }
    return payload


def build_function_call_content(
    name: str,
    args: dict[str, Any],
    call_id: str | None = None,
    thought_signature: bytes | None = None,
) -> genai_types.Content:
    """Build the ``model``-role Content wrapping the function_call.

    This is appended to ``contents`` after a dispatch so the next Gemini
    stream sees its own prior tool-call decision.

    job-B10: ``thought_signature`` (when non-None) is attached to the wrapping
    ``Part`` (not the ``FunctionCall`` — google-genai's ``FunctionCall`` has
    no signature field; only ``Part`` does, per types.py line 2044). Gemini 3
    requires the same opaque byte-blob be echoed back on the function_call
    Part for the replayed model turn or generate_content_stream raises
    ``thought-signature mismatch``. For Gemini 2.5 (current default), the
    field is None and the resulting Part carries no signature — a no-op for
    the model. The plumbing is forward-compat.
    """
    fn_call = genai_types.FunctionCall(name=name, args=args or {}, id=call_id)
    part_kwargs: dict[str, Any] = {"function_call": fn_call}
    if thought_signature is not None:
        part_kwargs["thought_signature"] = thought_signature
    return genai_types.Content(
        role="model",
        parts=[genai_types.Part(**part_kwargs)],
    )


def build_function_response_content(
    name: str,
    response: dict[str, Any],
    call_id: str | None = None,
) -> genai_types.Content:
    """Build the ``function``-role Content wrapping the function_response.

    Appended right after the matching ``model`` function_call content so
    Gemini has the (call, response) pair before deciding its next turn.
    """
    fn_resp = genai_types.FunctionResponse(name=name, response=response, id=call_id)
    return genai_types.Content(
        role="user",
        parts=[genai_types.Part(function_response=fn_resp)],
    )


# ---------------------------------------------------------------------------
# stream_events — tool-aware streaming (job-0154, root fix)
# ---------------------------------------------------------------------------

async def stream_events(
    client: genai.Client,
    model: str,
    user_text: str,
    tool_declarations: list[genai_types.FunctionDeclaration] | None = None,
    system_prompt: str | None = None,
    chat_history: list[dict] | None = None,
    cached_content_name: str | None = None,
) -> AsyncIterator[StreamEvent]:
    """Stream Gemini's reply as typed ``StreamEvent`` objects.

    Replaces the text-only ``stream_reply`` path.  When ``tool_declarations``
    is supplied (non-empty list), Gemini receives the full function catalog so
    it can emit ``FunctionCallEvent`` objects instead of prose refusals.

    Each yielded item is either:
    - ``TextDeltaEvent(delta)`` — a streamed text fragment; caller wraps it
      in ``agent-message-chunk``.
    - ``FunctionCallEvent(name, call_id, args)`` — Gemini wants to call a
      tool; caller dispatches through ``_invoke_tool_via_emitter``.

    Cancellation semantics are identical to ``stream_reply``.

    Args:
        client: google-genai ``Client`` built by ``build_client``.
        model: model identifier string (e.g. ``"gemini-2.5-pro"``).
        user_text: the user's message text.
        tool_declarations: optional list of ``FunctionDeclaration`` objects
            built by ``build_tool_declarations``; pass an empty list or
            ``None`` to send no tool catalog (text-only mode).
        system_prompt: optional system instruction string; passed as
            ``GenerateContentConfig.systemInstruction``.
        chat_history: optional list of prior ``{role, text}`` dicts from
            ``SessionState.chat_history``.  Included as prior ``Content``
            turns so Gemini has conversational context.
    """
    contents = build_contents_from_history(user_text, chat_history)
    async for event in stream_events_with_contents(
        client,
        model,
        contents,
        tool_declarations=tool_declarations,
        system_prompt=system_prompt,
        cached_content_name=cached_content_name,
    ):
        yield event


# ---------------------------------------------------------------------------
# stream_events_with_contents — single-turn primitive for the multi-turn loop
# ---------------------------------------------------------------------------


def _coerce_int(v: Any) -> int | None:
    """Return ``v`` as a real ``int``, or ``None`` for anything else.

    Defends against MagicMock-on-attribute coercion in unit tests (whose
    auto-attrs implement ``__int__`` and return 1, silently fabricating
    usage counts) AND against protobuf scalars / pydantic-wrapped ints on
    the wire (the genai SDK occasionally hands these back as objects that
    coerce cleanly via ``int()`` but are not ``isinstance(int)``).
    """
    if v is None:
        return None
    if isinstance(v, bool):
        # bool is a subclass of int; reject (no real usage count is a bool).
        return None
    if isinstance(v, int):
        return v
    # Accept "looks like a real number" — int(str) / int(float) — but NOT a
    # MagicMock (whose __int__ returns 1 unconditionally and would inject
    # phantom counts into the stream).
    try:
        import unittest.mock as _mock
        if isinstance(v, _mock.NonCallableMock):
            return None
    except Exception:  # noqa: BLE001 — defensive; mock import should always work
        pass
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _usage_has_real_counts(usage: Any) -> bool:
    """Return True only if ``usage`` carries at least one real integer count.

    A MagicMock surfaces all attrs as MagicMocks — ``_coerce_int`` rejects
    those, so an all-MagicMock usage object returns False here.  A real
    SDK ``UsageMetadata`` carries at least one int (typically
    ``total_token_count`` is always populated).
    """
    for fname in (
        "total_token_count",
        "cached_content_token_count",
        "prompt_token_count",
        "candidates_token_count",
    ):
        if _coerce_int(getattr(usage, fname, None)) is not None:
            return True
    return False


async def stream_events_with_contents(
    client: genai.Client,
    model: str,
    contents: list[genai_types.Content],
    tool_declarations: list[genai_types.FunctionDeclaration] | None = None,
    system_prompt: str | None = None,
    cached_content_name: str | None = None,
) -> AsyncIterator[StreamEvent]:
    """Stream one Gemini turn from a fully-built ``contents`` list (job-0169).

    This is the primitive the multi-turn loop driver in ``server.py`` uses.
    Each call corresponds to exactly one ``generate_content_stream`` round —
    the driver appends function_call + function_response Content entries to
    ``contents`` and re-calls this until Gemini emits no further function
    calls (only text → terminal turn).

    ``stream_events`` (the user-text variant) now delegates here after
    building ``contents`` via ``build_contents_from_history``.

    Cancellation: ``asyncio.CancelledError`` cancels the underlying producer
    thread and re-raises.

    Job-B6 (Wave 4.10) — CachedContent integration:
        When ``cached_content_name`` is provided, the request is built
        WITHOUT ``tools[]`` and WITHOUT ``tool_config``. The cache carries
        the full catalog + system instruction; sending either field
        alongside ``cached_content`` is a Vertex 400 (the original
        pre-dispatch blocker). ``system_prompt`` and ``tool_declarations``
        are silently ignored in this path.

        Per-turn allowed-set enforcement happens server-side via
        ``categories.validate_function_call`` (see ``server.py``); the cache
        always carries the FULL catalog.

        A ``UsageMetadataEvent`` is emitted from the final chunk's
        ``usage_metadata`` so the multi-turn driver can verify the cached
        token discount, emit the ``cache-status`` envelope into the
        PipelineEmitter, and pipe ``cached_content_token_count`` into the
        tool-call telemetry record.
    """
    loop = asyncio.get_running_loop()

    # Build the tool list for the config. SKIPPED when a cache is supplied —
    # the cache carries the catalog and Vertex 400s when both are passed.
    gem_tools: list[genai_types.Tool] | None = None
    if tool_declarations and not cached_content_name:
        gem_tools = [genai_types.Tool(function_declarations=tool_declarations)]

    def _open_stream():
        if cached_content_name:
            # Cached path: NO tools[], NO tool_config, NO system_instruction.
            # All three live in the cache. Sending them alongside
            # ``cached_content`` is a Vertex 400. The temperature / AFC fields
            # are per-request, so they stay.
            cfg = genai_types.GenerateContentConfig(
                temperature=0.7,
                cached_content=cached_content_name,
                automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            )
        else:
            cfg = genai_types.GenerateContentConfig(
                temperature=0.7,
                system_instruction=system_prompt or None,
                tools=gem_tools or None,
                # Disable automatic function calling — we handle dispatch ourselves.
                automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(
                    disable=True
                ) if gem_tools else None,
            )
        return client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=cfg,
        )

    # Use a typed queue: items are StreamEvent | None (sentinel) | BaseException.
    queue: asyncio.Queue[StreamEvent | None | BaseException] = asyncio.Queue()

    def _producer() -> None:
        try:
            last_usage: Any = None  # last seen ``usage_metadata`` across chunks.
            for chunk in _open_stream():
                # Walk parts: each chunk may carry text OR function_call parts.
                cands = getattr(chunk, "candidates", None) or []
                emitted_something = False
                for cand in cands:
                    content = getattr(cand, "content", None)
                    if content is None:
                        continue
                    parts = getattr(content, "parts", None) or []
                    for part in parts:
                        fn_call = getattr(part, "function_call", None)
                        if fn_call is not None and getattr(fn_call, "name", None):
                            # job-B10: harvest Gemini 3 thought_signature off
                            # the Part level. ``Part.thought_signature`` is the
                            # google-genai SDK field (types.py line 2044) — a
                            # bytes blob the model uses to re-anchor its
                            # reasoning across turns. On Gemini 2.5 the field
                            # is None (the model does not surface signatures);
                            # on Gemini 3 it must be echoed back unchanged on
                            # the function_call Part of the replayed turn or
                            # generate_content_stream fails with a
                            # ``thought-signature mismatch`` error.
                            sig = getattr(part, "thought_signature", None)
                            event = FunctionCallEvent(
                                name=fn_call.name,
                                call_id=getattr(fn_call, "id", None),
                                args=dict(fn_call.args or {}),
                                thought_signature=sig if isinstance(sig, (bytes, bytearray)) else None,
                            )
                            loop.call_soon_threadsafe(queue.put_nowait, event)
                            emitted_something = True
                        else:
                            text = getattr(part, "text", None)
                            if text:
                                loop.call_soon_threadsafe(
                                    queue.put_nowait, TextDeltaEvent(delta=text)
                                )
                                emitted_something = True
                # Fallback: some SDK versions expose chunk.text directly.
                if not emitted_something:
                    delta = getattr(chunk, "text", None)
                    if delta:
                        loop.call_soon_threadsafe(
                            queue.put_nowait, TextDeltaEvent(delta=delta)
                        )
                # Job-B6: harvest usage_metadata as it appears. Gemini surfaces
                # aggregate counts only on the terminal response chunk; we
                # capture every non-None value so a fallback path still works
                # if the SDK changes which chunk carries usage. We require at
                # least one bona-fide int field on the metadata object — this
                # avoids spurious UsageMetadataEvent emission from MagicMocks
                # in unit tests (whose auto-attrs coerce to 1 via __int__) and
                # from SDK chunks that carry a usage object with all-None
                # fields.
                usage = getattr(chunk, "usage_metadata", None)
                if usage is not None and _usage_has_real_counts(usage):
                    last_usage = usage
            # Once the stream completes, emit a single UsageMetadataEvent so
            # the caller can stash cached_content_token_count for telemetry +
            # the cache-status envelope.
            if last_usage is not None:
                cached_tokens = _coerce_int(
                    getattr(last_usage, "cached_content_token_count", None)
                )
                total_tokens = _coerce_int(
                    getattr(last_usage, "total_token_count", None)
                )
                prompt_tokens = _coerce_int(
                    getattr(last_usage, "prompt_token_count", None)
                )
                cand_tokens = _coerce_int(
                    getattr(last_usage, "candidates_token_count", None)
                )
                ev = UsageMetadataEvent(
                    cached_content_token_count=cached_tokens,
                    total_token_count=total_tokens,
                    prompt_token_count=prompt_tokens,
                    candidates_token_count=cand_tokens,
                    cache_hit=bool(cached_tokens and cached_tokens > 0),
                )
                loop.call_soon_threadsafe(queue.put_nowait, ev)
            loop.call_soon_threadsafe(queue.put_nowait, None)
        except BaseException as exc:  # noqa: BLE001 — surface any error to caller
            loop.call_soon_threadsafe(queue.put_nowait, exc)

    producer_task = loop.run_in_executor(None, _producer)

    try:
        while True:
            item = await queue.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    except asyncio.CancelledError:
        producer_task.cancel()
        raise


# ---------------------------------------------------------------------------
# stream_reply — text-only shim (kept for backward-compat; delegates to
# stream_events without tool declarations)
# ---------------------------------------------------------------------------

async def stream_reply(
    client: genai.Client, model: str, user_text: str
) -> AsyncIterator[str]:
    """Stream Gemini's reply as a sequence of delta strings (text-only).

    Retained for callers that only want text.  Internally delegates to
    ``stream_events`` with no tool declarations.

    Cancellation: ``asyncio.CancelledError`` is the cancel path.
    """
    async for event in stream_events(client, model, user_text):
        if isinstance(event, TextDeltaEvent):
            yield event.delta


__all__ = [
    "GEMINI_DEFAULT_MODEL",
    "MAX_TURN_ITERATIONS",
    "GeminiSettings",
    "StreamEvent",
    "TextDeltaEvent",
    "FunctionCallEvent",
    "UsageMetadataEvent",
    "SYSTEM_PROMPT",
    "build_client",
    "build_contents_from_history",
    "build_function_call_content",
    "build_function_response_content",
    "build_tool_declarations",
    "encode_parts_blob",
    "load_settings",
    "stream_events",
    "stream_events_with_contents",
    "stream_reply",
    "summarize_tool_result",
    # B11 schema-normalisation helpers (exported for audit / test use)
    "_is_tuple_annotation",
    "_normalize_callable_for_gemini",
    "_simplify_annotation",
]
