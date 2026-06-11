"""System-prompt snapshot tests (job B-sys, Wave 4.10 Stage-0 anchor A2/A5).

Stage 0 baseline anchor A2 surfaced: when a user prompt names a verbatim tool
(e.g. "show me protected areas in Big Cypress" → expects
``fetch_wdpa_protected_areas``) and the agent successfully geocodes a
precursor location, the agent CURRENTLY ENDS the turn without dispatching the
named tool. job B-sys amends ``SYSTEM_PROMPT`` with an explicit "Named-tool
follow-on dispatch" instruction so Gemini does not stop at the precursor step.

Stage 0 anchor A5 surfaced the parallel geographic-clipping gap: when the user
says "in [admin-region]", the agent should use ``fetch_administrative_boundaries``
+ ``clip_raster_to_polygon`` / ``clip_vector_to_polygon`` rather than collapsing
to a rectangular bbox approximation that bleeds into neighboring regions.

These tests are text snapshots — they confirm the prompt carries the new
sections verbatim. If the prompt is reworded substantively, update both the
prompt and these assertions in the same commit so the routing intent stays
visible to reviewers.
"""

from __future__ import annotations

from grace2_agent.adapter import SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# A2 — Named-tool follow-on dispatch
# ---------------------------------------------------------------------------


def test_system_prompt_has_named_tool_followon_section() -> None:
    """Prompt must carry the Stage-0 anchor A2 routing fix."""
    assert "Named-tool follow-on dispatch" in SYSTEM_PROMPT


def test_system_prompt_lists_named_data_source_triggers() -> None:
    """A2 fix must name the verbatim dataset keywords the user types."""
    # A representative subset — full keyword list is in the prompt; the test
    # just guards against accidental deletion of the trigger vocabulary.
    for keyword in (
        "WDPA",
        "NEXRAD",
        "NWS alerts",
        "NLCD",
        "MRMS",
        "GBIF",
        "MTBS",
        "LANDFIRE",
    ):
        assert keyword in SYSTEM_PROMPT, (
            f"named-data-source keyword {keyword!r} missing — A2 routing weakens"
        )


def test_system_prompt_forbids_ending_at_precursor() -> None:
    """The 'DO NOT end the turn at the precursor' instruction is the load-bearing
    sentence that fixes Stage-0 anchor A2."""
    assert "DO NOT end the turn at the precursor" in SYSTEM_PROMPT


def test_system_prompt_carries_named_tool_example() -> None:
    """A2 prompt must include at least one geocode → fetch_* → narrate example."""
    # NEXRAD + Florida is the canonical worked example.
    assert "fetch_nexrad_reflectivity" in SYSTEM_PROMPT
    assert "geocode_location" in SYSTEM_PROMPT
    # And the WDPA Big Cypress example that anchored the baseline finding.
    assert "fetch_wdpa_protected_areas" in SYSTEM_PROMPT
    assert "Big Cypress" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# A5 — Geographic clipping pattern (in [admin-region])
# ---------------------------------------------------------------------------


def test_system_prompt_has_geographic_clipping_section() -> None:
    """Prompt must carry the Stage-0 anchor A5 polygon-clip instruction."""
    assert "Geographic clipping pattern" in SYSTEM_PROMPT


def test_system_prompt_names_admin_polygon_clip_tools() -> None:
    """A5 fix must reference the admin-boundary fetcher + both clip tools."""
    assert "fetch_administrative_boundaries" in SYSTEM_PROMPT
    assert "clip_raster_to_polygon" in SYSTEM_PROMPT
    assert "clip_vector_to_polygon" in SYSTEM_PROMPT


def test_system_prompt_lists_admin_region_kinds() -> None:
    """A5 fix must name the admin-region categories that trigger the pattern."""
    for kind in ("state", "county", "city", "ZCTA", "watershed"):
        assert kind in SYSTEM_PROMPT, (
            f"admin-region kind {kind!r} missing — A5 trigger vocabulary weakens"
        )


def test_system_prompt_forbids_bbox_approximation() -> None:
    """A5 fix must explicitly reject bbox-as-region for admin-polygon prompts."""
    # The load-bearing prohibition: "DO NOT just hand the dataset's bbox..."
    assert "DO NOT just hand the dataset's bbox" in SYSTEM_PROMPT


def test_system_prompt_carries_admin_clipping_example() -> None:
    """A5 fix must include a Miami-Dade-style worked example."""
    assert "Miami-Dade" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# job-0270 — Publish-to-map discipline
# ---------------------------------------------------------------------------


def test_system_prompt_has_publish_discipline_section() -> None:
    """Prompt must carry the job-0270 publish-to-map instruction — the live
    finding: Gemini computed a colored relief for Boulder, never called
    publish_layer, and the user saw nothing on the map."""
    assert "Publish-to-map discipline" in SYSTEM_PROMPT


def test_system_prompt_says_layers_invisible_until_published() -> None:
    """The load-bearing sentence: storage is not the map."""
    assert "NOT pixels on the user's map" in SYSTEM_PROMPT
    assert "publish_layer(layer_uri=<handle>" in SYSTEM_PROMPT


def test_system_prompt_forbids_claiming_display_without_wms_url() -> None:
    """The anti-fabrication half: never narrate a layer as displayed unless
    publish_layer returned a WMS URL this turn."""
    flat = " ".join(SYSTEM_PROMPT.split())
    assert (
        "NEVER claim a layer is displayed, shown, or \"added to the map\" "
        "unless publish_layer returned a WMS URL THIS turn" in flat
    )


def test_system_prompt_keeps_always_narrate_section() -> None:
    """The job-0270 insertion sits directly above the always-narrate clause —
    guard that the A1 section header survived the splice."""
    assert "Always-narrate after tools complete" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Regression — existing behaviors from job-0154 must survive the amendment
# ---------------------------------------------------------------------------


def test_system_prompt_still_routes_flood_modeling() -> None:
    """job-0154 routing instruction (flood → run_model_flood_scenario) survives."""
    assert "run_model_flood_scenario" in SYSTEM_PROMPT


def test_system_prompt_still_forbids_fabricated_numbers() -> None:
    """job-0154 anti-fabrication guard survives the amendment."""
    assert "Never fabricate numbers" in SYSTEM_PROMPT
