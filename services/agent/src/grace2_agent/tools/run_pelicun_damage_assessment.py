"""``run_pelicun_damage_assessment`` atomic tool — Pelicun fragility damage stub (job-0098).

This Wave 1 file lands the TOOL REGISTRATION + signature + a documented stub
that raises ``PelicunNotImplementedYet`` with ``retryable=False`` and a clear
actionable message. The Wave 2 composer (job-0106) wires the real Pelicun
runtime + fragility database + Monte-Carlo loop.

**Why a stub now (audit.md)**: the LLM tool registry must include the API
contract so Case-1-style prompts can compile a workflow that USES this tool,
even before the implementation lands. This file locks the LLM-visible API
contract — name, parameters, allowed enums, return type, docstring guidance —
so the agent's planning loop converges on the same call shape Wave 2's full
implementation will fulfill.

**Decision N (Pelicun impact post-processor)** — the post-M5 milestone calling
for fragility-curve-driven damage assessment over modeled hazard rasters,
producing an ``ImpactEnvelope``-shaped result with per-asset expected damage
state + repair-cost statistics. See the project memory note
``project_pelicun_impact_postprocessor.md`` for the broader plan and the
``B.6c`` schema slot the v0.2 LayerURI/ImpactEnvelope extension occupies.

**Contract locked at this seam:**

- Inputs: ``hazard_raster_uri`` (e.g. flood depth COG from
  ``run_model_flood_scenario`` / ``postprocess_flood``), ``assets_uri``
  (FlatGeobuf points or polygons), ``fragility_set`` (Literal closed enum),
  ``component_types`` (optional list — None = all components in the
  fragility set), ``realization_count`` (int, default 100).
- Output: ``LayerURI`` with ``layer_type="vector"``, ``role="primary"``,
  ``units="damage_state"``. Features = the asset features from
  ``assets_uri``, with per-feature properties ``ds_mean``, ``ds_p05``,
  ``ds_p95``, ``repair_cost_mean``, ``repair_cost_p95``, ``replacement_value``.
- Cache: ``ttl_class="static-30d"``, ``source_class="pelicun_damage"``,
  ``cacheable=True``. The real implementation produces reproducible results
  (deterministic given inputs + a seeded Monte-Carlo loop), so 30-day stale
  windows are acceptable.

**LLM guidance** (FR-TA-3 docstring discipline): explicit "Use this when" /
"Do NOT use this for" / pairing notes — see the function docstring.

**Codified job-0086 lesson (geographic correctness)**: when the Wave 2
composer lands, its acceptance test MUST assert that damage states are higher
at asset locations where the hazard raster shows higher intensity (e.g.
buildings in the deep-flood footprint have higher ds_mean than buildings on
high ground in the same bbox). Byte round-trips of the output FlatGeobuf
won't catch a sampling bug that maps every asset to the wrong raster pixel.
This stub does not produce geometry, so no geography check applies here; the
note exists so the Wave 2 composer carries it forward.

FR-TA-2: atomic tool, returns ``LayerURI``. FR-CE-8 / FR-DC-3/4: routed
through ``read_through`` once Wave 2 lands.

Invariants:

- **Invariant 1 (Determinism boundary): preserves.** Damage states and
  repair-cost statistics will surface as typed properties on the returned
  vector layer; no LLM narration of numbers. (The stub itself never
  computes — it raises.)
- **Invariant 2 (Deterministic workflows): preserves.** No LLM call inside
  the tool body; Pelicun's Monte-Carlo sampling is seeded for reproducibility
  in Wave 2.
- **Invariant 7 (Claims carry provenance): preserves.** The output features
  carry the source ``hazard_raster_uri`` + ``fragility_set`` in their layer
  metadata so downstream narration can cite the inputs.
- **NFR-R-1 (typed-error surface): honors.** ``PelicunNotImplementedYet``
  carries ``error_code="PELICUN_NOT_IMPLEMENTED_YET"`` and
  ``retryable=False`` — the agent surface will NOT retry; the user-facing
  message names job-0106 as the resolution.
"""

from __future__ import annotations

import logging
from typing import Literal

from grace2_contracts.execution import LayerURI  # noqa: F401 — re-exported in signature
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool

__all__ = [
    "run_pelicun_damage_assessment",
    "PelicunDamageError",
    "PelicunNotImplementedYet",
    "PelicunInputError",
]

logger = logging.getLogger("grace2_agent.tools.run_pelicun_damage_assessment")


# ---------------------------------------------------------------------------
# Error types (FR-AS-11 / NFR-R-1 typed-error surface).
# ---------------------------------------------------------------------------


class PelicunDamageError(RuntimeError):
    """Base class for ``run_pelicun_damage_assessment`` failures.

    ``error_code`` maps to the WebSocket A.6 error frame emitted by the
    agent surface; ``retryable`` guides FR-AS-11 retry logic.
    """

    error_code: str = "PELICUN_DAMAGE_ERROR"
    retryable: bool = True


class PelicunInputError(PelicunDamageError):
    """Bad ``hazard_raster_uri`` / ``assets_uri`` / ``fragility_set`` / etc.

    Not retryable — input validation failures are deterministic given the same
    inputs, so the agent's retry loop should not re-invoke.
    """

    error_code = "PELICUN_INPUT_INVALID"
    retryable = False


class PelicunNotImplementedYet(PelicunDamageError):
    """Pelicun runtime integration deferred to Wave 2 composer (job-0106).

    Raised by the v0.1 stub of ``run_pelicun_damage_assessment``. Not
    retryable — retrying the same input produces the same outcome; the user
    or planning loop must pick a different tool until job-0106 lands.

    The error message names the resolution (job-0106) so the agent surface
    can offer the user actionable next-steps.
    """

    error_code = "PELICUN_NOT_IMPLEMENTED_YET"
    retryable = False


# ---------------------------------------------------------------------------
# Allowed enum values — locked at this Wave 1 seam so the LLM-visible API
# contract is stable across the Wave 1 → Wave 2 hand-off.
# ---------------------------------------------------------------------------

#: Allowed ``fragility_set`` values. v0.1 ships two: HAZUS-MH flood depth-
#: damage curves (the Wave 2 default for Case-1 pairing with
#: ``run_model_flood_scenario``) and HAZUS earthquake curves (sprint-13+ when
#: the seismic engine lands). The Literal type alias is the single source of
#: truth — the runtime check below validates against the same set.
FragilitySet = Literal["hazus_flood_v6", "fema_hazus_eq_2020"]
_VALID_FRAGILITY_SETS: frozenset[str] = frozenset(
    {"hazus_flood_v6", "fema_hazus_eq_2020"}
)


# ---------------------------------------------------------------------------
# AtomicToolMetadata — registered once at import time.
# ---------------------------------------------------------------------------

_METADATA = AtomicToolMetadata(
    name="run_pelicun_damage_assessment",
    ttl_class="static-30d",
    source_class="pelicun_damage",
    cacheable=True,
)


# ---------------------------------------------------------------------------
# Input-validation helpers.
#
# These run BEFORE the NotImplementedYet raise so the LLM (and the unit
# tests) gets the deterministic typed-error surface even at the stub stage —
# garbage-in cases are caught at the contract boundary, not deferred.
# ---------------------------------------------------------------------------


def _validate_uri(uri: object, field_name: str) -> str:
    """Reject non-string, empty, or obviously-malformed URIs.

    We accept any of:
    - ``gs://bucket/path`` (production GCS URIs)
    - ``http(s)://host/path`` (remote URLs — Wave 2 may fetch directly)
    - local filesystem paths (``/abs/...`` or ``./relative/...``) for tests.

    The stub does not validate that the resource EXISTS — that's an I/O
    operation Wave 2 will do once the Pelicun runtime is wired. We validate
    only the shape so the agent's planning loop gets a typed error for the
    "obviously wrong" cases (None, empty, wrong type).
    """
    if uri is None:
        raise PelicunInputError(
            f"{field_name} is required; got None. "
            "Pass a gs:// URI (or local path) to a "
            f"{'COG raster' if 'hazard' in field_name else 'FlatGeobuf vector'}."
        )
    if not isinstance(uri, str):
        raise PelicunInputError(
            f"{field_name} must be a string URI; got {type(uri).__name__}."
        )
    if not uri.strip():
        raise PelicunInputError(
            f"{field_name} must be a non-empty URI string."
        )
    return uri


def _validate_fragility_set(fragility_set: str) -> str:
    """Reject ``fragility_set`` values outside the allowed enum.

    The Literal type alias does NOT enforce this at runtime (Python's typing
    is erased at runtime); we validate explicitly so the agent's planning
    loop gets a deterministic typed error when it hallucinates a fragility
    set we don't carry.
    """
    if not isinstance(fragility_set, str):
        raise PelicunInputError(
            f"fragility_set must be a string; got {type(fragility_set).__name__}."
        )
    if fragility_set not in _VALID_FRAGILITY_SETS:
        raise PelicunInputError(
            f"fragility_set={fragility_set!r} is not in the allowed set "
            f"{sorted(_VALID_FRAGILITY_SETS)}. v0.1 ships only 'hazus_flood_v6' "
            "(FEMA HAZUS-MH flood depth-damage curves) and 'fema_hazus_eq_2020' "
            "(HAZUS earthquake curves — sprint-13+ when the seismic engine lands)."
        )
    return fragility_set


def _validate_component_types(component_types: object) -> list[str] | None:
    """Reject ``component_types`` values outside ``list[str] | None``.

    ``None`` means "all components in the fragility set" (the Wave 2 default).
    Empty list ``[]`` is rejected — the caller almost certainly meant ``None``
    and an empty filter would yield zero output features, which is rarely
    what the user wanted.
    """
    if component_types is None:
        return None
    if not isinstance(component_types, (list, tuple)):
        raise PelicunInputError(
            "component_types must be a list of strings or None; "
            f"got {type(component_types).__name__}."
        )
    if len(component_types) == 0:
        raise PelicunInputError(
            "component_types is an empty list; pass None to include all "
            "components in the fragility set, or pass a non-empty list of "
            "component codes (e.g. ['RES1', 'COM1'])."
        )
    out: list[str] = []
    for idx, ct in enumerate(component_types):
        if not isinstance(ct, str) or not ct.strip():
            raise PelicunInputError(
                f"component_types[{idx}] must be a non-empty string; "
                f"got {ct!r}."
            )
        out.append(ct.strip())
    return out


def _validate_realization_count(realization_count: object) -> int:
    """Reject ``realization_count`` outside positive int range.

    Monte-Carlo realization counts MUST be positive; the Wave 2 composer
    seeds the RNG for reproducibility but cannot run 0 realizations.
    """
    if isinstance(realization_count, bool) or not isinstance(realization_count, int):
        raise PelicunInputError(
            "realization_count must be a positive integer; "
            f"got {type(realization_count).__name__}."
        )
    if realization_count <= 0:
        raise PelicunInputError(
            f"realization_count={realization_count} must be > 0. "
            "Default 100 is suitable for most assessments; raise for "
            "tighter confidence intervals."
        )
    return realization_count


# ---------------------------------------------------------------------------
# Registered atomic tool.
# ---------------------------------------------------------------------------


@register_tool(_METADATA)
def run_pelicun_damage_assessment(
    hazard_raster_uri: str,
    assets_uri: str,
    fragility_set: FragilitySet = "hazus_flood_v6",
    component_types: list[str] | None = None,
    realization_count: int = 100,
) -> LayerURI:
    """Fragility-curve-driven damage assessment via Pelicun.

    For each asset point or polygon in ``assets_uri``:
        1. Sample the hazard raster at the asset location.
        2. Look up the matching fragility function by ``component_type`` +
           hazard intensity (from ``fragility_set``).
        3. Monte-Carlo sample ``realization_count`` damage states.
        4. Aggregate to per-asset expected damage state + 95% CI + repair-cost
           statistics.

    Returns a ``LayerURI`` pointing at a FlatGeobuf of the asset features with
    per-feature damage properties — see "Returns" below.

    Use this when:
        - The user has a modeled or fetched hazard raster (flood depth COG,
          earthquake intensity raster) AND an asset layer (buildings, parcels,
          critical infrastructure) and wants quantitative damage / loss
          estimates over the asset set.
        - The user asks "how much damage", "expected losses", "which buildings
          are most exposed", or "monte-carlo damage assessment" on a modeled
          hazard.

    Do NOT use this for:
        - Plain hazard exposure counts (use ``compute_zonal_statistics`` with
          value=hazard raster, zone=asset polygons — cheaper and faster when
          you only need "how many assets are in the flood zone").
        - Building footprint counts or density (use ``compute_building_density``
          or ``fetch_buildings`` — they emit the asset layer this tool consumes).
        - Loss estimation without an asset layer (this tool requires per-asset
          features; if you only have aggregate population in a zone, use a
          zonal-statistics + WorldPop pipeline instead).
        - Hazards outside the available fragility sets (v0.1 ships flood +
          earthquake; wildfire / wind / liquefaction fragility sets are
          gated on the seismic and wildfire engine work).

    Parameters:
        hazard_raster_uri: gs:// URI (or local path) to a single-band hazard
            intensity raster — e.g. flood depth in metres from
            ``run_model_flood_scenario`` / ``postprocess_flood``. CRS must
            overlap ``assets_uri``; Wave 2 reprojects on the fly.
        assets_uri: gs:// URI (or local path) to a FlatGeobuf of asset
            features. Points (buildings, infrastructure) and polygons
            (parcels, building footprints) are both supported. Each feature
            SHOULD carry a ``component_type`` property matching the
            fragility-set vocabulary (e.g. ``"RES1"`` / ``"COM1"`` for
            HAZUS); features without one fall back to the fragility set's
            default component type.
        fragility_set: which fragility curve family to use. v0.1 ships:
            - ``"hazus_flood_v6"`` — FEMA HAZUS-MH flood depth-damage curves.
              The Wave 2 default; pair with flood depth COGs from the
              modeling workflows. Component vocabulary: HAZUS occupancy
              classes (RES1/RES2/COM1/.../IND1/...).
            - ``"fema_hazus_eq_2020"`` — HAZUS-MH earthquake fragility
              curves. Sprint-13+ once the seismic engine lands and produces
              hazard intensity rasters (PGA / Sa).
        component_types: optional list of component-type codes to RESTRICT
            the assessment to (e.g. ``["RES1", "COM1"]`` for single-family
            residential + retail commercial). Pass ``None`` (default) to
            include every feature in ``assets_uri``. Empty list ``[]`` is
            rejected — pass ``None`` instead.
        realization_count: number of Monte-Carlo realizations per asset.
            Default 100; raise (e.g. 1000) for tighter 95 % CIs at the cost
            of compute. Each realization samples a damage state from the
            fragility function's lognormal distribution.

    Returns:
        A ``LayerURI`` pointing at a FlatGeobuf in the cache bucket. Each
        output feature has the same geometry as the corresponding asset and
        carries these computed properties:

        - ``ds_mean`` (float): expected damage state (HAZUS DS0-DS4 mapped to
          0.0-1.0 fractional damage; or the fragility set's native damage-state
          encoding for non-HAZUS sets in future versions).
        - ``ds_p05`` (float): 5th-percentile damage state across realizations.
        - ``ds_p95`` (float): 95th-percentile damage state.
        - ``repair_cost_mean`` (float): expected repair cost (USD).
        - ``repair_cost_p95`` (float): 95th-percentile repair cost (USD).
        - ``replacement_value`` (float): per-asset replacement value (USD)
          used to denominate repair cost.

        Layer metadata: ``layer_type="vector"``, ``role="primary"``,
        ``units="damage_state"``, ``style_preset="pelicun_damage_state"``.

    LLM guidance:
        - Pair with ``run_model_flood_scenario`` output: pass its returned
          flood depth COG URI as ``hazard_raster_uri``.
        - ``assets_uri``: use ``fetch_administrative_boundaries(level='place')``
          as a coarse asset proxy in v0.1 (each place polygon = one asset).
          Sprint-13 will swap in actual building footprints from
          ``fetch_buildings`` / ``compute_building_density``.
        - For Case 1 demo flows: model flood → run Pelicun on flood COG +
          fetched place polygons → narrate ds_mean + repair_cost_mean from
          the returned feature properties (never from LLM-generated numbers
          — invariant 1).

    Cache: ``ttl_class="static-30d"``, ``source_class="pelicun_damage"``.
    Real implementation (Wave 2 / job-0106) produces reproducible results
    via a seeded Monte-Carlo loop, so identical
    ``(hazard_raster_uri, assets_uri, fragility_set, component_types,
    realization_count)`` calls reuse the cached FlatGeobuf for 30 days.

    Raises:
        PelicunInputError: bad URI shape, ``fragility_set`` outside the
            allowed enum, empty ``component_types`` list, or non-positive
            ``realization_count``. ``error_code="PELICUN_INPUT_INVALID"``,
            ``retryable=False``.
        PelicunNotImplementedYet: the v0.1 stub always raises this AFTER
            input validation succeeds. ``error_code="PELICUN_NOT_IMPLEMENTED_YET"``,
            ``retryable=False``. The Wave 2 composer (job-0106) replaces this
            raise with the actual Pelicun runtime + fragility DB +
            Monte-Carlo loop.
    """
    # Input validation runs FIRST so the deterministic typed-error surface
    # is available even at the stub stage. Garbage-in is caught at the
    # contract boundary, not deferred to Wave 2.
    hazard_raster_uri = _validate_uri(hazard_raster_uri, "hazard_raster_uri")
    assets_uri = _validate_uri(assets_uri, "assets_uri")
    fragility_set_validated = _validate_fragility_set(fragility_set)
    component_types_validated = _validate_component_types(component_types)
    realization_count_validated = _validate_realization_count(realization_count)

    logger.info(
        "run_pelicun_damage_assessment: stub invoked "
        "hazard_raster_uri=%s assets_uri=%s fragility_set=%s "
        "component_types=%s realization_count=%d (raising PelicunNotImplementedYet)",
        hazard_raster_uri,
        assets_uri,
        fragility_set_validated,
        component_types_validated,
        realization_count_validated,
    )

    # The Wave 1 stub locks the LLM-visible API contract; the Wave 2
    # composer (job-0106) wires the real Pelicun runtime + fragility DB +
    # Monte-Carlo loop.
    raise PelicunNotImplementedYet(
        "Implementation deferred to job-0106 composer; this tool registration "
        "locks the LLM-visible API contract."
    )
