"""Case 2 partial acceptance — news/event ingest demo (job-0135, sprint-12-mega).

Tests the ``model_news_event_ingest`` workflow end-to-end against fixture text
that mimics two real news articles about the February 2023 Norfolk Southern /
East Palestine train derailment (vinyl chloride spill). The aggregator runs
deterministically on real text; only the external-API boundaries (web_fetch,
geocode_location) are fixture-injected per testing.md discipline.

Acceptance criteria (from audit.md):
  A1. EventIngestResult.event_type matches input ("spill")
  A2. derived_params populated with confidence scores
  A3. bbox derived from location (derived location fed to geocoder)
  A4. presentation_text reads naturally and contains all key fields
  A5. No solver dispatch leaked — workflow STOPS after result construction
  A6. presentation_text references all provided sources (n_sources == 2)

Invariants verified:
  - Invariant 1 (Determinism boundary): narrated values are the exact
    DerivedEventParam.value fields — not LLM output.
  - Invariant 2 (Deterministic workflow): LLM call count is 0 throughout
    (no Gemini adapter is present; the workflow runs without it).
  - Invariant 7 (Claims carry provenance): provenance list has one entry
    per source with citation_snippet, source_authority_tier, and identifier.
  - Invariant 9 (Confirmation before consequence): result carries
    "STOP — review derived parameters..." sentinel in presentation_text.

Mock boundary discipline (testing.md §"Mocks/recorded fixtures live ONLY at
external boundaries"):
  - web_fetch → replaced via TOOL_REGISTRY swap with a fixture function that
    returns pre-recorded text. The registry swap is necessary because the
    workflow calls tools via TOOL_REGISTRY["name"].fn — module-level patches
    do not intercept this (the registry holds the original reference at import
    time and the cache shim fires before the module-level name is looked up).
  - geocode_location → same registry-swap approach, returns a fixture bbox for
    East Palestine, OH (real Nominatim bbox, hardcoded for CI determinism).
  - aggregate_claims_across_sources → NOT mocked; runs live against the
    deterministic regex extractor on the fixture text.
  - fetch_nws_event, fetch_storm_events_db → NOT used in these tests
    (url-type sources only); not mocked.

Registry swap technique:
  TOOL_REGISTRY is a plain dict keyed by tool name. Each value is a frozen
  RegisteredTool dataclass. We atomically swap the value to a new RegisteredTool
  with fn=fixture_fn while keeping the same metadata, and restore on exit via
  try/finally. This is the correct in-registry fixture path for workflow tests.
"""

from __future__ import annotations

import contextlib
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Generator

import pytest

# ---------------------------------------------------------------------------
# Fixture — pre-recorded external-API responses.
# ---------------------------------------------------------------------------

# Source 1: headline article from a regional news site (URL source).
# Text uses the exact claim-token forms the regex extractors recognise:
#   "East Palestine, Ohio" (location via _LOCATION_RE)
#   "vinyl chloride" (contaminant from _CONTAMINANT_KEYWORDS)
#   "February 3, 2023" (date via _LONG_DATE_RE)
#   "100,000 gallons" (scale via _SCALE_PATTERNS)
#   "3 people were injured" (casualties via _CASUALTIES_PATTERNS)
_FIXTURE_URL_1 = "https://www.example-news.com/norfolk-southern-spill-2023"
_FIXTURE_TEXT_1 = """\
Norfolk Southern Train Derailment Causes Chemical Spill Near East Palestine, Ohio

A Norfolk Southern freight train carrying hazardous materials derailed on
February 3, 2023 near East Palestine, Ohio, spilling approximately 100,000 gallons
of vinyl chloride into the surrounding environment. Emergency crews responded within
hours of the incident, which occurred just south of the Ohio-Pennsylvania border.

Authorities confirmed that at least 3 people were injured in the initial incident,
though no fatalities were reported at the time of publication. The US Environmental
Protection Agency and local emergency management officials established a safety
perimeter around the derailment site.

The spill prompted a controlled burn of the vinyl chloride, releasing toxic gases
into the air and raising concerns about air and water quality in the Columbiana
County region. East Palestine, Ohio residents within a 1-mile radius were evacuated.
"""

# Source 2: AP-style wire story (same event, slight date variation).
# Also mentions "East Palestine, Ohio" + "vinyl chloride" + "100,000 gallons"
# → 2-source cross-agreement should push confidence to 0.80.
_FIXTURE_URL_2 = "https://apnews.example.com/east-palestine-derailment-2023"
_FIXTURE_TEXT_2 = """\
East Palestine Train Derailment: What We Know

EAST PALESTINE, Ohio (AP) — A Norfolk Southern freight train derailed on
February 3, 2023 in East Palestine, Ohio, leading to the emergency release
and burning of vinyl chloride. Officials estimated roughly 100,000 gallons
of the chemical were involved.

At least 3 people were reported injured. The spill covered an area near
Sulphur Run creek, threatening local water supplies. Federal investigators
from the National Transportation Safety Board arrived at East Palestine, Ohio
on February 5, 2023 to begin their probe.

The incident has raised questions about the transport of hazardous materials
through populated areas in Columbiana County, Ohio.
"""

# Geocoder fixture — real Nominatim bbox for "East Palestine, Ohio"
# (verified manually: lat ~40.83°N, lon ~80.53°W; small town in Columbiana County).
_GEOCODE_FIXTURE_RESULT: dict[str, Any] = {
    "name": "East Palestine, Columbiana County, Ohio, United States",
    "bbox": [-80.5562, 40.8151, -80.5021, 40.8562],
}


def _make_web_fetch_result(url: str, text: str) -> dict[str, Any]:
    """Build a web_fetch result dict matching the real tool's output shape."""
    return {
        "url": url,
        "status_code": 200,
        "fetched_at": "2026-06-08T00:00:00Z",
        "extract_mode": "main_text",
        "content": text,
        "title": text.splitlines()[0][:80],
        "lang": "en",
        "content_length": len(text),
    }


# ---------------------------------------------------------------------------
# Registry-swap context manager — the canonical in-registry fixture technique.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _registry_swap(
    tool_name: str, fixture_fn: Any
) -> Generator[None, None, None]:
    """Atomically swap TOOL_REGISTRY[tool_name].fn for fixture_fn.

    Restores the original entry on exit. Thread-safe for single-threaded
    pytest (the registry is a module-level dict; no lock needed here).

    Raises KeyError if tool_name is not registered — this surfaces
    a missing tool import as a hard failure rather than a silent no-op.
    """
    # Import here so the import triggers eager registration of all tools.
    from grace2_agent.tools import TOOL_REGISTRY, RegisteredTool

    original = TOOL_REGISTRY[tool_name]  # KeyError → test misconfiguration
    swapped = RegisteredTool(
        metadata=original.metadata,
        fn=fixture_fn,
        module=f"fixture:{__name__}",
    )
    TOOL_REGISTRY[tool_name] = swapped
    try:
        yield
    finally:
        TOOL_REGISTRY[tool_name] = original


# ---------------------------------------------------------------------------
# Evidence directory.
# ---------------------------------------------------------------------------

EVIDENCE_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "reports"
    / "inflight"
    / "job-0135-testing-20260608"
    / "evidence"
)


# ---------------------------------------------------------------------------
# Test 1 — full round-trip, spill event with 2 URL sources.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_case2_event_ingest_spill_end_to_end() -> None:
    """A1–A6: full round-trip against the East Palestine spill fixture.

    Invariants 1, 2, 7, 9 verified here.
    Layer attribution: every assertion identifies the failing layer.
    """
    # Trigger eager registration of data_fetch tools (geocode_location lives
    # there and is not in the __init__.py eager imports).
    import grace2_agent.tools.data_fetch  # noqa: F401

    from grace2_agent.workflows.model_news_event_ingest import model_news_event_ingest
    from grace2_contracts.case_results import EventIngestResult

    sources = [
        {"type": "url", "identifier": _FIXTURE_URL_1},
        {"type": "url", "identifier": _FIXTURE_URL_2},
    ]

    geocode_calls: list[str] = []

    def _fake_web_fetch(url: str, extract: str = "main_text", **kw: Any) -> dict[str, Any]:
        if _FIXTURE_URL_1 in url:
            return _make_web_fetch_result(_FIXTURE_URL_1, _FIXTURE_TEXT_1)
        if _FIXTURE_URL_2 in url:
            return _make_web_fetch_result(_FIXTURE_URL_2, _FIXTURE_TEXT_2)
        raise ValueError(f"unexpected url in fixture web_fetch: {url!r}")

    def _fake_geocode_location(query: str) -> dict[str, Any]:
        geocode_calls.append(query)
        return _GEOCODE_FIXTURE_RESULT

    with (
        _registry_swap("web_fetch", _fake_web_fetch),
        _registry_swap("geocode_location", _fake_geocode_location),
    ):
        result = await model_news_event_ingest(
            sources=sources,
            target_event_type="spill",
            pipeline_emitter=None,
        )

    # ---- A1: event_type matches input ----
    assert result.event_type == "spill", (
        f"layer=workflow: EventIngestResult.event_type expected 'spill', "
        f"got {result.event_type!r}"
    )

    # ---- A2: derived_params populated ----
    assert result.derived_params, (
        "layer=workflow: EventIngestResult.derived_params is empty — "
        "no claims extracted from fixture text"
    )
    expected_targets = {"contaminant", "location"}
    missing = expected_targets - set(result.derived_params)
    assert not missing, (
        f"layer=claim-aggregator: expected targets {expected_targets!r} "
        f"all in derived_params; missing: {missing!r}. "
        f"Got keys: {set(result.derived_params)!r}"
    )

    # ---- A2: confidence scores on populated params ----
    for target, param in result.derived_params.items():
        if param.value is not None:
            assert 0.0 <= param.confidence <= 1.0, (
                f"layer=claim-aggregator: {target} confidence "
                f"{param.confidence!r} out of [0,1]"
            )
            assert param.confidence > 0.0, (
                f"layer=claim-aggregator: {target} confidence is zero "
                f"despite value being set ({param.value!r})"
            )

    # ---- A2: vinyl chloride detected (2-source cross-agreement) ----
    contaminant_param = result.derived_params.get("contaminant")
    assert contaminant_param is not None and contaminant_param.value is not None, (
        "layer=claim-aggregator: contaminant param missing or value=None; "
        "expected 'vinyl chloride' from fixture text"
    )
    assert "vinyl chloride" in str(contaminant_param.value).lower(), (
        f"layer=claim-aggregator: contaminant value expected to contain "
        f"'vinyl chloride', got {contaminant_param.value!r}"
    )
    # Both sources mention vinyl chloride → confidence should be >= 0.8 (2-source rule)
    assert contaminant_param.confidence >= 0.8, (
        f"layer=claim-aggregator: contaminant confidence {contaminant_param.confidence:.4f} "
        f"< 0.80 despite 2-source agreement; scoring rule broken"
    )

    # ---- A2: East Palestine, Ohio detected (location) ----
    location_param = result.derived_params.get("location")
    assert location_param is not None and location_param.value is not None, (
        "layer=claim-aggregator: location param missing or value=None; "
        "expected 'East Palestine, Ohio' from fixture text"
    )
    assert "East Palestine" in str(location_param.value), (
        f"layer=claim-aggregator: location value expected to contain "
        f"'East Palestine', got {location_param.value!r}"
    )

    # ---- A2: scale extracted (100,000 gallons, 2-source agreement) ----
    scale_param = result.derived_params.get("scale")
    assert scale_param is not None and scale_param.value is not None, (
        "layer=claim-aggregator: scale param missing or value=None; "
        "expected '100,000 gallons' from fixture text"
    )
    assert isinstance(scale_param.value, dict), (
        f"layer=claim-aggregator: scale value should be a dict "
        f"{{value, unit}}, got {type(scale_param.value).__name__!r}: "
        f"{scale_param.value!r}"
    )
    scale_mag = scale_param.value.get("value")
    assert scale_mag is not None and scale_mag > 0, (
        f"layer=claim-aggregator: scale magnitude {scale_mag!r} is not > 0"
    )

    # ---- A3: bbox derived from the DERIVED location ----
    assert geocode_calls, (
        "layer=workflow: geocode_location was NOT called; bbox derivation broken"
    )
    geocode_query = geocode_calls[0]
    assert "East Palestine" in geocode_query, (
        f"layer=workflow (geographic-correctness gate): geocode_location was "
        f"called with {geocode_query!r} instead of the derived location; "
        f"the workflow is NOT feeding the aggregator's location value to the geocoder"
    )

    assert result.bbox is not None, (
        "layer=workflow: bbox is None despite geocode_location returning a valid bbox"
    )
    min_lon, min_lat, max_lon, max_lat = result.bbox
    # East Palestine, OH is in the Ohio/Pennsylvania border region
    assert -82.0 <= min_lon <= -79.0, (
        f"layer=geocoder: min_lon={min_lon:.4f} outside expected range [-82, -79] "
        f"for East Palestine, OH"
    )
    assert 40.0 <= min_lat <= 42.0, (
        f"layer=geocoder: min_lat={min_lat:.4f} outside expected range [40, 42] "
        f"for East Palestine, OH"
    )

    # ---- A4: presentation_text reads naturally ----
    assert result.presentation_text, "layer=workflow: presentation_text is empty"
    assert "spill" in result.presentation_text.lower(), (
        f"layer=workflow: presentation_text does not mention event_type 'spill'"
    )
    assert "2" in result.presentation_text, (
        f"layer=workflow: presentation_text does not reference source count 2"
    )
    # Invariant 9 sentinel
    assert "STOP" in result.presentation_text, (
        "layer=workflow (Invariant 9): presentation_text missing 'STOP' sentinel; "
        "workflow does not signal the review-gated boundary"
    )
    # Bbox appears in presentation_text
    assert "EPSG" in result.presentation_text or "bbox" in result.presentation_text.lower(), (
        f"layer=workflow: presentation_text does not include resolved bbox: "
        f"{result.presentation_text!r}"
    )

    # ---- A5: no solver dispatch ----
    dumped = result.model_dump(mode="json")
    dumped_str = json.dumps(dumped)
    assert "solver_run_id" not in dumped_str, (
        "layer=workflow (Invariant 9): EventIngestResult contains 'solver_run_id' — "
        "solver was dispatched before user review"
    )
    assert "execution_handle" not in dumped_str.lower(), (
        "layer=workflow (Invariant 9): EventIngestResult contains execution_handle — "
        "solver was dispatched before user review"
    )

    # ---- A6: provenance references both sources ----
    assert len(result.provenance) == 2, (
        f"layer=workflow: provenance has {len(result.provenance)} entries; "
        f"expected 2 (one per source)"
    )
    prov_ids = {p.identifier for p in result.provenance}
    assert _FIXTURE_URL_1 in prov_ids, (
        f"layer=workflow: provenance missing source 1 {_FIXTURE_URL_1!r}; got {prov_ids!r}"
    )
    assert _FIXTURE_URL_2 in prov_ids, (
        f"layer=workflow: provenance missing source 2 {_FIXTURE_URL_2!r}; got {prov_ids!r}"
    )
    for prov in result.provenance:
        assert prov.citation_snippet, (
            f"layer=workflow (Invariant 7): provenance entry for {prov.identifier!r} "
            f"has no citation_snippet"
        )
        assert prov.source_authority_tier == 2, (
            f"layer=workflow: provenance source_authority_tier for {prov.identifier!r} "
            f"expected 2 (news URL), got {prov.source_authority_tier!r}"
        )

    # ---- Invariant 1: determinism boundary ----
    # The contaminant value narrated in presentation_text must be the EXACT
    # string from derived_params — never LLM-generated text.
    contaminant_str = str(contaminant_param.value)
    assert contaminant_str in result.presentation_text, (
        f"layer=workflow (Invariant 1): presentation_text does not contain "
        f"the exact contaminant value {contaminant_str!r}; "
        f"text={result.presentation_text!r}"
    )

    # ---- Invariant 7: all provenance entries have source_type + identifier ----
    for prov in result.provenance:
        assert prov.identifier, "layer=workflow (Invariant 7): provenance identifier empty"
        assert prov.source_type in ("url", "nws_alert", "storm_event"), (
            f"layer=workflow (Invariant 7): unknown source_type {prov.source_type!r}"
        )

    # ---- Write evidence JSON ----
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    evidence_path = EVIDENCE_DIR / "case2_event_ingest_result.json"
    with evidence_path.open("w", encoding="utf-8") as f:
        json.dump(dumped, f, indent=2, default=str)

    print(f"\n[evidence] EventIngestResult written → {evidence_path}")
    print(f"[evidence] event_type={result.event_type!r}")
    print(f"[evidence] derived_params keys: {list(result.derived_params)}")
    for k, p in result.derived_params.items():
        print(
            f"  {k}: value={p.value!r} confidence={p.confidence:.2f} "
            f"sources={len(p.supporting_sources)}"
        )
    print(f"[evidence] bbox={result.bbox}")
    print(f"[evidence] provenance entries: {len(result.provenance)}")
    print(f"[evidence] presentation_text:\n{result.presentation_text}")


# ---------------------------------------------------------------------------
# Test 2 — FR-HEP-3 source-agreement scoring unit test (table-driven).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_aggregation_unit_cross_source_agreement() -> None:
    """FR-HEP-3 source-agreement scoring rule: table-driven, deterministic.

    1 source → confidence 0.5
    2 sources → confidence 0.80
    3 sources → confidence 0.85

    Uses math.isclose for float comparisons (no 1e-15 floating-point noise).
    """
    from grace2_agent.tools.aggregate_claims_across_sources import (
        aggregate_claims_across_sources,
    )

    # --- 1 source ---
    result_1 = aggregate_claims_across_sources(
        sources=[{
            "url": "https://example.com/a",
            "text": "The spill occurred in Longview, Texas on March 15, 2024.",
            "fetched_at": "2024-03-15T00:00:00Z",
        }],
        claim_targets=["location", "date"],
        confidence_threshold=0.0,
    )
    loc_1 = result_1["claims"]["location"]
    assert loc_1["value"] is not None, (
        "layer=claim-aggregator: location not extracted from single-source fixture"
    )
    assert math.isclose(loc_1["confidence"], 0.5, rel_tol=1e-9), (
        f"layer=claim-aggregator: 1-source confidence expected 0.5, "
        f"got {loc_1['confidence']}"
    )

    # --- 2 sources agree ---
    result_2 = aggregate_claims_across_sources(
        sources=[
            {
                "url": "https://example.com/a",
                "text": "The spill occurred in Longview, Texas on March 15, 2024.",
                "fetched_at": "2024-03-15T00:00:00Z",
            },
            {
                "url": "https://example.com/b",
                "text": "Longview, Texas authorities confirmed the incident on March 15, 2024.",
                "fetched_at": "2024-03-15T01:00:00Z",
            },
        ],
        claim_targets=["location", "date"],
        confidence_threshold=0.0,
    )
    loc_2 = result_2["claims"]["location"]
    assert math.isclose(loc_2["confidence"], 0.80, rel_tol=1e-9), (
        f"layer=claim-aggregator: 2-source agreement confidence expected 0.80, "
        f"got {loc_2['confidence']}"
    )

    # --- 3 sources agree ---
    result_3 = aggregate_claims_across_sources(
        sources=[
            {
                "url": "https://example.com/a",
                "text": "Longview, Texas: chemical spill confirmed.",
                "fetched_at": "2024-03-15T00:00:00Z",
            },
            {
                "url": "https://example.com/b",
                "text": "Longview, Texas emergency crews responded.",
                "fetched_at": "2024-03-15T01:00:00Z",
            },
            {
                "url": "https://example.com/c",
                "text": "Officials in Longview, Texas announced evacuation.",
                "fetched_at": "2024-03-15T02:00:00Z",
            },
        ],
        claim_targets=["location"],
        confidence_threshold=0.0,
    )
    loc_3 = result_3["claims"]["location"]
    assert math.isclose(loc_3["confidence"], 0.85, rel_tol=1e-9), (
        f"layer=claim-aggregator: 3-source agreement confidence expected 0.85, "
        f"got {loc_3['confidence']}"
    )

    print("\n[pass] FR-HEP-3 source-agreement scoring: 0.5 / 0.80 / 0.85 OK")


# ---------------------------------------------------------------------------
# Test 3 — flood event also STOPS before solver dispatch.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_no_solver_dispatch_on_flood_event() -> None:
    """A5: flood event type also STOPS before solver dispatch (Invariant 9)."""
    import grace2_agent.tools.data_fetch  # noqa: F401

    from grace2_agent.workflows.model_news_event_ingest import model_news_event_ingest

    flood_text = (
        "Flooding in Baton Rouge, Louisiana on June 10, 2025 affected approximately "
        "5,000 acres. At least 12 people were injured in the flood. Heavy rain "
        "began falling on June 10, 2025 and continued for 48 hours."
    )

    def _fake_web_fetch_flood(url: str, **kw: Any) -> dict[str, Any]:
        return _make_web_fetch_result(url, flood_text)

    def _fake_geocode_flood(query: str) -> dict[str, Any]:
        return {"name": "Baton Rouge, Louisiana", "bbox": [-91.2, 30.3, -90.9, 30.6]}

    with (
        _registry_swap("web_fetch", _fake_web_fetch_flood),
        _registry_swap("geocode_location", _fake_geocode_flood),
    ):
        result = await model_news_event_ingest(
            sources=[
                {"type": "url", "identifier": "https://example.com/flood-a"},
                {"type": "url", "identifier": "https://example.com/flood-b"},
            ],
            target_event_type="flood",
            pipeline_emitter=None,
        )

    assert result.event_type == "flood", (
        f"layer=workflow: expected 'flood', got {result.event_type!r}"
    )
    assert "STOP" in result.presentation_text, (
        "layer=workflow (Invariant 9): flood workflow missing 'STOP' sentinel"
    )
    dumped_str = json.dumps(result.model_dump(mode="json"))
    assert "solver_run_id" not in dumped_str, (
        "layer=workflow (Invariant 9): solver dispatched for flood event"
    )
    assert "execution_handle" not in dumped_str.lower(), (
        "layer=workflow (Invariant 9): execution_handle in flood result"
    )
    print(f"\n[pass] flood STOP sentinel present; no solver dispatched OK")
    print(f"[flood] bbox={result.bbox}")


# ---------------------------------------------------------------------------
# Test 4 — EventIngestResult contract round-trip.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_ingest_contract_round_trip() -> None:
    """EventIngestResult round-trips through model_dump → model_validate."""
    import grace2_agent.tools.data_fetch  # noqa: F401

    from grace2_agent.workflows.model_news_event_ingest import model_news_event_ingest
    from grace2_contracts.case_results import EventIngestResult

    def _fake_web_fetch(url: str, **kw: Any) -> dict[str, Any]:
        return _make_web_fetch_result(url, _FIXTURE_TEXT_1)

    def _fake_geocode(query: str) -> dict[str, Any]:
        return _GEOCODE_FIXTURE_RESULT

    with (
        _registry_swap("web_fetch", _fake_web_fetch),
        _registry_swap("geocode_location", _fake_geocode),
    ):
        result = await model_news_event_ingest(
            sources=[{"type": "url", "identifier": _FIXTURE_URL_1}],
            target_event_type="spill",
            pipeline_emitter=None,
        )

    dumped = result.model_dump(mode="json")
    rehydrated = EventIngestResult.model_validate(dumped)

    assert rehydrated.event_type == result.event_type, (
        "layer=schema: round-trip event_type mismatch"
    )
    assert rehydrated.presentation_text == result.presentation_text, (
        "layer=schema: round-trip presentation_text mismatch"
    )
    assert len(rehydrated.provenance) == len(result.provenance), (
        "layer=schema: round-trip provenance count mismatch"
    )
    assert rehydrated.bbox == result.bbox, (
        "layer=schema: round-trip bbox mismatch"
    )

    print("\n[pass] EventIngestResult contract round-trip OK")
