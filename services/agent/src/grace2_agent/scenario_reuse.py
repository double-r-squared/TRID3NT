"""Deterministic expensive-simulation reuse guard (job-0326, NATE 2026-06-16).

PROBLEM (live): the agent REDUNDANTLY re-runs expensive simulations
(``run_model_flood_scenario`` / ``run_modflow_job`` / Pelicun) and re-derives
layers that ALREADY exist in the Case — burning minutes and money on a SFINCS /
MODFLOW solve whose output layer is already on the map. The F54 soft prompt
steer ("Reuse the existing handle/uri ... do NOT re-fetch or recompute") was
being IGNORED by the live model. This module makes reuse ROBUST: a deterministic,
CONSERVATIVE short-circuit that runs on the dispatch hot path BEFORE the solver
launches, plus the identity machinery the enriched layers-present note uses so
the model can SEE that a result already exists.

Two cooperating pieces:

1. ``scenario_signature(tool_name, params)`` — distills an expensive-scenario
   tool call into a normalized, comparable signature: the ``scenario_type`` (the
   layer-family the run PRODUCES, e.g. ``flood-depth``), the AOI key (a quantized
   bbox AND/OR a normalized ``location_query``), and the KEY physics params that
   change the answer (return period + duration for flood; contaminant + release
   rate + duration + location for a MODFLOW plume). Two calls whose signatures
   compare equal would produce the SAME layer — so the second is redundant.

2. ``ScenarioResultIndex`` — a per-session record of every expensive-scenario
   result already produced THIS session, keyed by its signature and carrying the
   produced ``LayerURI`` identity (handle / uri / name / layer_type / bbox).
   ``find_reuse`` matches a fresh request's signature against the index and, on a
   CLEAR match, returns the existing result so the dispatcher can short-circuit
   the solver. The index is seeded from the (durable, per-Case) ``loaded_layers``
   on a Case reopen so reuse survives a reconnect — a flood RESULT loaded from
   persistence still short-circuits a re-run.

Design stance — CONSERVATIVE BY CONSTRUCTION. We only ever short-circuit on a
CLEAR match (same scenario family + same/equivalent AOI + same key params); any
ambiguity (missing bbox we cannot derive without geocoding, a different return
period, an explicit re-run/refresh request) falls through to RUN. A false
short-circuit hands the user a stale answer; a false run only costs a re-solve.
We bias hard toward correctness.

This module is pure / synchronous / side-effect-free (it never geocodes, never
touches the network) so it is safe on the dispatch hot path and trivially
testable.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("grace2_agent.scenario_reuse")

__all__ = [
    "EXPENSIVE_SCENARIO_TOOLS",
    "ScenarioSignature",
    "ScenarioResult",
    "ScenarioResultIndex",
    "scenario_signature",
    "scenario_type_for_tool",
    "layer_id_scenario_type",
    "bbox_equivalent",
    "get_scenario_index",
    "reset_scenario_indexes_for_tests",
]


# --------------------------------------------------------------------------- #
# Tool → produced-layer scenario family
# --------------------------------------------------------------------------- #
#
# Map each EXPENSIVE simulation composer to the scenario family it PRODUCES.
# ``scenario_type`` is the stable layer-family token used both to key the reuse
# index and to recognize an existing RESULT layer by its ``layer_id`` prefix
# (e.g. ``flood-depth-peak-<run_id>`` → ``flood-depth``). Keep these aligned with
# the layer_id minted by the postprocess step of each workflow.
EXPENSIVE_SCENARIO_TOOLS: dict[str, str] = {
    "run_model_flood_scenario": "flood-depth",
    "run_model_nws_flood_event_scenario": "flood-depth",
    "run_modflow_job": "plume",
    "run_model_groundwater_contamination_scenario": "plume",
}

#: ``layer_id`` prefixes that identify an existing RESULT layer of each family.
#: A flood postprocess mints ``flood-depth-peak-<run_id>``; a MODFLOW postprocess
#: mints a plume layer id. Matching is prefix/substring based so run-id suffixes
#: do not defeat recognition.
_SCENARIO_LAYER_ID_MARKERS: dict[str, tuple[str, ...]] = {
    "flood-depth": ("flood-depth", "flood_depth", "flood-peak"),
    "plume": ("plume", "modflow", "contamination", "concentration"),
}

#: Default bbox quantization (degrees). Two AOIs whose bbox corners agree to
#: this tolerance are treated as the SAME extent for reuse. ~0.01 deg ≈ 1 km;
#: deliberately coarse enough to absorb geocoder jitter on the same place name
#: but fine enough that a genuinely different AOI never collides.
_BBOX_QUANT_DEG: float = 0.02


def scenario_type_for_tool(tool_name: str) -> str | None:
    """Return the produced-layer scenario family for an expensive tool, else None."""
    return EXPENSIVE_SCENARIO_TOOLS.get(tool_name)


def layer_id_scenario_type(layer_id: str | None, name: str | None = None) -> str | None:
    """Classify a loaded layer (by id / name) into a scenario RESULT family.

    Returns the ``scenario_type`` token (e.g. ``"flood-depth"``) when the layer
    looks like the RESULT of an expensive simulation, else ``None`` (an input /
    context layer such as a fetched DEM or landcover). Used by the enriched
    layers-present note to label results and by index seeding from persisted
    ``loaded_layers``.
    """
    hay = " ".join(str(x).lower() for x in (layer_id or "", name or "") if x)
    if not hay:
        return None
    for scenario_type, markers in _SCENARIO_LAYER_ID_MARKERS.items():
        for marker in markers:
            if marker in hay:
                return scenario_type
    return None


# --------------------------------------------------------------------------- #
# Normalization helpers
# --------------------------------------------------------------------------- #


def _normalize_location_query(q: Any) -> str | None:
    """Lower/strip/collapse a free-text place name for stable comparison."""
    if not isinstance(q, str):
        return None
    norm = re.sub(r"\s+", " ", q.strip().lower())
    norm = norm.strip(" ,.")
    return norm or None


def _coerce_bbox(value: Any) -> tuple[float, float, float, float] | None:
    """Coerce a 4-element bbox (list/tuple) of floats, else None."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        b = tuple(float(x) for x in value)
    except (TypeError, ValueError):
        return None
    return b  # type: ignore[return-value]


def _quantize_bbox(
    bbox: tuple[float, float, float, float] | None,
    quant: float = _BBOX_QUANT_DEG,
) -> tuple[int, int, int, int] | None:
    """Snap a bbox to a quantization grid so near-equal extents key identically."""
    if bbox is None or quant <= 0:
        return None
    return tuple(round(c / quant) for c in bbox)  # type: ignore[return-value]


def bbox_equivalent(
    a: Any,
    b: Any,
    quant: float = _BBOX_QUANT_DEG,
) -> bool:
    """True iff two bboxes are the SAME extent within the quantization tolerance."""
    qa = _quantize_bbox(_coerce_bbox(a), quant)
    qb = _quantize_bbox(_coerce_bbox(b), quant)
    return qa is not None and qa == qb


def _coerce_latlon(value: Any) -> tuple[float, float] | None:
    """Coerce a 2-element (lat, lon) point, else None. No string parsing here —
    the server's ``coerce_latlon`` runs earlier; this guard only sees the
    already-normalized shape (and tolerates the raw 2-list)."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return (float(value[0]), float(value[1]))
    except (TypeError, ValueError):
        return None


def _round_num(value: Any, ndigits: int = 4) -> float | None:
    """Round a numeric param for signature comparison, else None."""
    try:
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Signature
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScenarioSignature:
    """Normalized identity of an expensive-scenario request.

    Two signatures with equal ``scenario_type`` + equal ``key_params`` AND an
    equivalent AOI (matching ``bbox_q`` OR — when no bbox is resolvable — matching
    ``location_norm``) describe runs that produce the SAME layer.

    Fields:
        scenario_type: produced-layer family (``flood-depth`` / ``plume``).
        tool_name: the originating tool (for telemetry; NOT part of equality
            matching — two flood tools producing flood-depth are interchangeable).
        bbox: the resolved AOI bbox (lon-first 4-tuple) when one is present in
            params, else None.
        bbox_q: the quantized bbox key (the AOI-equality anchor when present).
        location_norm: normalized ``location_query`` (the AOI-equality fallback
            when no bbox is resolvable without geocoding).
        key_params: the frozenset of (name, value) pairs that change the physics
            answer (return period, duration, contaminant, release rate, ...).
    """

    scenario_type: str
    tool_name: str
    bbox: tuple[float, float, float, float] | None
    bbox_q: tuple[int, int, int, int] | None
    location_norm: str | None
    key_params: frozenset[tuple[str, Any]]

    def aoi_resolvable(self) -> bool:
        """True iff this request carries an AOI we can compare WITHOUT geocoding."""
        return self.bbox_q is not None or self.location_norm is not None


def _flood_signature(tool_name: str, params: dict) -> ScenarioSignature | None:
    bbox = _coerce_bbox(params.get("bbox"))
    location_norm = _normalize_location_query(params.get("location_query"))
    if bbox is None and location_norm is None:
        # No AOI we can key on without geocoding → cannot match safely → RUN.
        return None
    # Key physics params (accept both _yr/_years and _hr/_hours aliases — by the
    # time the guard runs, normalize_args has canonicalized to _yr/_hr, but be
    # defensive). A forcing_raster_uri (observed-precip path) makes the run a
    # DIFFERENT physics answer, so it participates in the key.
    rp = _round_num(
        params.get("return_period_yr", params.get("return_period_years")), 0
    )
    dur = _round_num(params.get("duration_hr", params.get("duration_hours")), 0)
    forcing = params.get("forcing_raster_uri")
    key: set[tuple[str, Any]] = set()
    if rp is not None:
        key.add(("return_period_yr", rp))
    if dur is not None:
        key.add(("duration_hr", dur))
    if isinstance(forcing, str) and forcing:
        key.add(("forcing_raster_uri", forcing))
    return ScenarioSignature(
        scenario_type="flood-depth",
        tool_name=tool_name,
        bbox=bbox,
        bbox_q=_quantize_bbox(bbox),
        location_norm=location_norm,
        key_params=frozenset(key),
    )


def _plume_signature(tool_name: str, params: dict) -> ScenarioSignature | None:
    loc = _coerce_latlon(params.get("spill_location_latlon"))
    contaminant = params.get("contaminant")
    rate = _round_num(params.get("release_rate_kg_s"), 6)
    duration = _round_num(params.get("duration_days"), 4)
    # A plume run is identified by its spill point + contaminant + rate +
    # duration. Without a usable spill point we cannot match safely → RUN.
    if loc is None:
        return None
    # Treat the spill POINT as the AOI anchor (quantize a degenerate bbox around
    # it so near-equal points collide). Plume point is (lat, lon) → build a
    # lon-first degenerate bbox for the shared quantizer.
    lat, lon = loc
    point_bbox = (lon, lat, lon, lat)
    key: set[tuple[str, Any]] = set()
    if isinstance(contaminant, str) and contaminant.strip():
        key.add(("contaminant", contaminant.strip().lower()))
    if rate is not None:
        key.add(("release_rate_kg_s", rate))
    if duration is not None:
        key.add(("duration_days", duration))
    return ScenarioSignature(
        scenario_type="plume",
        tool_name=tool_name,
        bbox=point_bbox,
        bbox_q=_quantize_bbox(point_bbox),
        location_norm=None,
        key_params=frozenset(key),
    )


def scenario_signature(tool_name: str, params: dict) -> ScenarioSignature | None:
    """Build a normalized reuse signature for an expensive-scenario tool call.

    Returns ``None`` when the tool is not a guarded expensive scenario OR when
    the request lacks the identity we need to match SAFELY without geocoding /
    side effects (CONSERVATIVE: no signature → never short-circuit → RUN).
    """
    scenario_type = scenario_type_for_tool(tool_name)
    if scenario_type is None:
        return None
    if not isinstance(params, dict):
        return None
    if scenario_type == "flood-depth":
        return _flood_signature(tool_name, params)
    if scenario_type == "plume":
        return _plume_signature(tool_name, params)
    return None


# --------------------------------------------------------------------------- #
# Result identity + per-session index
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScenarioResult:
    """The reusable identity of an already-produced expensive-scenario layer."""

    scenario_type: str
    layer_id: str
    name: str
    layer_type: str
    uri: str
    bbox: tuple[float, float, float, float] | None
    signature: ScenarioSignature | None = None


@dataclass
class ScenarioResultIndex:
    """Per-session record of expensive-scenario results already produced.

    Keyed for matching by ``scenario_type`` + AOI + key params. Populated when an
    expensive composer returns a layer (``record_result``) and seeded from a
    Case's persisted ``loaded_layers`` on reopen (``seed_from_loaded_layers``).
    """

    session_id: str
    _results: list[ScenarioResult] = field(default_factory=list)

    def record_result(
        self,
        signature: ScenarioSignature | None,
        *,
        layer_id: str,
        name: str,
        layer_type: str,
        uri: str,
        bbox: Any = None,
    ) -> None:
        """Record a freshly produced expensive-scenario result for future reuse."""
        if not layer_id or not uri:
            return
        scenario_type = (
            signature.scenario_type
            if signature is not None
            else layer_id_scenario_type(layer_id, name)
        )
        if scenario_type is None:
            return
        rec_bbox = _coerce_bbox(bbox)
        if rec_bbox is None and signature is not None:
            rec_bbox = signature.bbox
        result = ScenarioResult(
            scenario_type=scenario_type,
            layer_id=layer_id,
            name=name or layer_id,
            layer_type=layer_type or "raster",
            uri=uri,
            bbox=rec_bbox,
            signature=signature,
        )
        # Replace any existing entry for the SAME layer_id (re-run refresh), else
        # append. Newest-wins ordering: move/append to the end so ``find_reuse``
        # prefers the most recent result on a tie.
        self._results = [r for r in self._results if r.layer_id != layer_id]
        self._results.append(result)
        logger.info(
            "scenario_reuse[%s]: recorded result type=%s layer_id=%s",
            self.session_id, scenario_type, layer_id,
        )

    def seed_from_loaded_layers(self, loaded_layers: Any) -> None:
        """Seed the index from a Case's persisted ``loaded_layers`` on reopen.

        Each loaded layer that looks like an expensive RESULT (its layer_id /
        name classifies into a scenario family) is recorded WITHOUT a signature —
        the persisted summary has no bbox / key params, so it can only support
        the ``location_norm``-absent, same-family AOI reuse path when the next
        request is bbox-keyed AND the result also carries a bbox. We still record
        it so the enriched note can label it and so an identical bbox-keyed
        re-run in a reopened single-result Case short-circuits.
        """
        known = {r.layer_id for r in self._results}
        for layer in loaded_layers or []:
            d = _layer_to_dict(layer)
            if d is None:
                continue
            layer_id = d.get("layer_id")
            if not isinstance(layer_id, str) or not layer_id:
                continue
            # CRITICAL: never clobber an in-session record (which carries the
            # full signature — bbox + key params) with a signature-LESS persisted
            # seed. The in-session record is strictly richer; a re-seed would
            # downgrade it and defeat the short-circuit on the very next call.
            if layer_id in known:
                continue
            name = d.get("name") if isinstance(d.get("name"), str) else None
            scenario_type = layer_id_scenario_type(layer_id, name)
            if scenario_type is None:
                continue
            uri = d.get("uri")
            if not isinstance(uri, str) or not uri:
                continue
            self.record_result(
                None,
                layer_id=layer_id,
                name=name or layer_id,
                layer_type=d.get("layer_type") or "raster",
                uri=uri,
                bbox=d.get("bbox"),
            )

    def find_reuse(
        self,
        request: ScenarioSignature | None,
        *,
        case_bbox: Any = None,
    ) -> ScenarioResult | None:
        """Return an existing result that CLEARLY answers ``request``, else None.

        CONSERVATIVE matching ladder (most-recent first), short-circuit only on a
        clear match:

          1. Same ``scenario_type`` AND same ``key_params`` AND equivalent AOI:
             - bbox-keyed request: result's bbox (or its signature's bbox)
               quantizes equal to the request bbox, OR (when the result has no
               recorded bbox) the request bbox matches the Case AOI bbox AND this
               is the only result of its family;
             - location-keyed request (no bbox): result's signature
               ``location_norm`` matches.

        Anything ambiguous → ``None`` → caller RUNS.
        """
        if request is None or not request.aoi_resolvable():
            return None
        same_family = [
            r for r in self._results if r.scenario_type == request.scenario_type
        ]
        if not same_family:
            return None
        case_bbox_t = _coerce_bbox(case_bbox)
        # Newest-first.
        for result in reversed(same_family):
            if not self._key_params_match(request, result):
                continue
            if self._aoi_match(request, result, same_family, case_bbox_t):
                return result
        return None

    @staticmethod
    def _key_params_match(
        request: ScenarioSignature, result: ScenarioResult
    ) -> bool:
        """Key physics params must agree. A result with no recorded signature
        (seeded from persistence) has UNKNOWN params — only matchable when the
        request itself carries no key params (a bare "model the flood here")."""
        if result.signature is None:
            return len(request.key_params) == 0
        return request.key_params == result.signature.key_params

    def _aoi_match(
        self,
        request: ScenarioSignature,
        result: ScenarioResult,
        same_family: list[ScenarioResult],
        case_bbox: tuple[float, float, float, float] | None,
    ) -> bool:
        # bbox-keyed request.
        if request.bbox is not None:
            # Prefer the result's SIGNATURE bbox (the request-equivalent AOI
            # anchor — for a plume this is the degenerate spill-POINT bbox, NOT
            # the plume FOOTPRINT recorded in ``result.bbox``). Fall back to the
            # recorded footprint bbox only when no signature is available
            # (persistence-seeded results).
            result_bbox = (
                result.signature.bbox if result.signature else None
            ) or result.bbox
            if result_bbox is not None:
                return bbox_equivalent(request.bbox, result_bbox)
            # Result has no bbox (persistence-seeded). Only safe to reuse when
            # the request bbox matches the Case AOI AND there is exactly one
            # result of this family (no ambiguity about which it is).
            if case_bbox is not None and len(same_family) == 1:
                return bbox_equivalent(request.bbox, case_bbox)
            return False
        # location-keyed request (no bbox): need a recorded location_norm.
        if request.location_norm is not None and result.signature is not None:
            return request.location_norm == result.signature.location_norm
        return False


def _layer_to_dict(layer: Any) -> dict | None:
    """Coerce a loaded-layer entry (dict or pydantic summary) to a plain dict."""
    if isinstance(layer, dict):
        return layer
    if hasattr(layer, "model_dump") and callable(layer.model_dump):
        try:
            return layer.model_dump(mode="json")
        except Exception:  # noqa: BLE001 — non-pydantic duck
            return None
    return None


# --------------------------------------------------------------------------- #
# Module-level per-session index store (mirrors uri_registry's store pattern —
# survives reconnects; shared across a session's sibling WebSocket connections)
# --------------------------------------------------------------------------- #

_INDEX_STORE_CAP = 256
_SESSION_SCENARIO_INDEXES: "OrderedDict[str, ScenarioResultIndex]" = OrderedDict()


def get_scenario_index(session_id: str) -> ScenarioResultIndex:
    """Return (creating if needed) the scenario-result index for ``session_id``."""
    idx = _SESSION_SCENARIO_INDEXES.get(session_id)
    if idx is None:
        while len(_SESSION_SCENARIO_INDEXES) >= _INDEX_STORE_CAP:
            _SESSION_SCENARIO_INDEXES.popitem(last=False)
        idx = ScenarioResultIndex(session_id=session_id)
        _SESSION_SCENARIO_INDEXES[session_id] = idx
    return idx


def reset_scenario_indexes_for_tests() -> None:
    """Test hook — wipe the module-level store."""
    _SESSION_SCENARIO_INDEXES.clear()
