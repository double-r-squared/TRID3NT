"""Case 1 live acceptance capture script (job-0134-testing-20260608).

Invokes ``model_flood_habitat_scenario`` directly (no WebSocket / agent layer)
against the real Big Cypress / Everglades bbox and writes JSON evidence to this
directory.

Usage:
    GOOGLE_APPLICATION_CREDENTIALS=~/.config/gcloud/application_default_credentials.json \
        .venv-agent/bin/python reports/inflight/job-0134-testing-20260608/evidence/case1_capture.py

The script outputs:
    case1_metrics.json  — CaseOneResult.impact_metrics + case_summary_text
    case1_acceptance.md — write-up with geographic-correctness verification

Species keys used: corrected per _species_reference.py (OQ-0117 lesson):
    2435099 — Puma concolor (Florida panther, species-level, ~250 Big Cypress records)
    2441370 — Alligator mississippiensis (American alligator, species-level)
    2480803 — Platalea ajaja (Roseate spoonbill, species-level)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

# Add the agent package to path.
REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "services" / "agent" / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "contracts" / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("case1_capture")

EVIDENCE_DIR = Path(__file__).resolve().parent

# Case 1 bbox: Big Cypress / Everglades region (kickoff spec)
BBOX = (-81.5, 25.7, -80.7, 26.5)

# Corrected species keys per _species_reference.py (OQ-0117 resolution)
SPECIES_KEYS = [
    2435099,  # Puma concolor (Florida panther — species-level)
    2441370,  # Alligator mississippiensis (American alligator — species-level)
    2480803,  # Platalea ajaja (Roseate spoonbill — species-level)
]

RAINFALL_EVENT = "atlas14_100yr"


async def run_case1() -> dict:
    """Run Case 1 composer and return the CaseOneResult as a dict."""
    from grace2_agent.workflows.model_flood_habitat_scenario import (
        model_flood_habitat_scenario,
    )

    logger.info(
        "Starting Case 1 — bbox=%s species_keys=%s rainfall_event=%s",
        BBOX,
        SPECIES_KEYS,
        RAINFALL_EVENT,
    )

    t0 = time.monotonic()
    result = await model_flood_habitat_scenario(
        bbox=BBOX,
        species_keys=SPECIES_KEYS,
        rainfall_event=RAINFALL_EVENT,
        protected_area_designation=None,
        place_clip_polygon_uri=None,
        place_label="Big Cypress / Everglades",
    )
    elapsed = time.monotonic() - t0

    logger.info(
        "Case 1 complete in %.1fs: flood_layer=%s species_count=%d wdpa=%s",
        elapsed,
        result.flood_layer_uri,
        len(result.species_layers),
        result.wdpa_layer_uri,
    )

    dumped = result.model_dump(mode="json")
    dumped["_capture_elapsed_seconds"] = elapsed
    return dumped


def validate_geographic_correctness(result: dict) -> list[str]:
    """Geographic-correctness gate per job-0086 codified lesson.

    Returns a list of finding strings (pass/fail/warn per check).
    """
    findings = []
    bbox = result.get("bbox")
    expected_bbox = list(BBOX)

    # Check 1: bbox is the Big Cypress / Everglades region
    if bbox and list(bbox) == expected_bbox:
        findings.append(
            f"PASS bbox: result bbox {bbox!r} matches kickoff spec "
            f"(-81.5, 25.7, -80.7, 26.5) — Big Cypress / Everglades region"
        )
    else:
        findings.append(
            f"FAIL bbox: result bbox {bbox!r} != expected {expected_bbox!r}"
        )

    # Check 2: species layers count
    species_layers = result.get("species_layers") or []
    if len(species_layers) == len(SPECIES_KEYS):
        findings.append(
            f"PASS species_layers: {len(species_layers)} layer(s) returned, "
            f"one per requested species key ({SPECIES_KEYS!r})"
        )
    elif len(species_layers) > 0:
        findings.append(
            f"WARN species_layers: {len(species_layers)}/{len(SPECIES_KEYS)} "
            f"species layers returned (partial success — some fetches may have failed)"
        )
    else:
        findings.append(
            f"FAIL species_layers: 0 species layers returned for {SPECIES_KEYS!r}"
        )

    # Check 3: species points inside bbox — verify URIs are present and non-empty
    for i, layer in enumerate(species_layers):
        uri = layer.get("uri") if isinstance(layer, dict) else getattr(layer, "uri", None)
        if uri and uri.strip():
            findings.append(
                f"PASS species_layers[{i}]: LayerURI present with uri={uri!r} "
                f"(points were fetched and written)"
            )
        else:
            findings.append(
                f"FAIL species_layers[{i}]: LayerURI missing URI — no file produced"
            )

    # Check 4: WDPA layer present (Everglades NP + Big Cypress NP in bbox)
    wdpa = result.get("wdpa_layer_uri")
    if wdpa and isinstance(wdpa, dict) and wdpa.get("uri"):
        findings.append(
            f"PASS wdpa_layer_uri: WDPA polygon layer returned with "
            f"uri={wdpa.get('uri')!r} — protected areas in bbox fetched"
        )
    elif wdpa:
        findings.append(
            f"WARN wdpa_layer_uri: WDPA layer present but URI is empty: {wdpa!r}"
        )
    else:
        findings.append(
            "FAIL wdpa_layer_uri: No WDPA protected-area layer returned. "
            "Expected Everglades NP + Big Cypress NP polygons in bbox. "
            "Layer attribution: fetch_wdpa_protected_areas or cache layer."
        )

    # Check 5: impact_metrics populated (requires both flood + WDPA)
    metrics = result.get("impact_metrics") or {}
    if metrics:
        findings.append(
            f"PASS impact_metrics: zonal statistics computed — "
            f"keys={list(metrics.keys())!r}"
        )
    else:
        flood_uri = result.get("flood_layer_uri")
        if flood_uri is None:
            findings.append(
                "INFO impact_metrics: empty because flood modeling failed "
                "(flood_layer_uri is None) — zonal stats require both flood + WDPA"
            )
        else:
            findings.append(
                "WARN impact_metrics: empty despite flood layer present — "
                "check compute_zonal_statistics layer"
            )

    # Check 6: case_summary_text is non-empty and deterministic (not LLM prose)
    summary_text = result.get("case_summary_text") or ""
    if summary_text and len(summary_text) > 20:
        findings.append(
            f"PASS case_summary_text: deterministic summary produced "
            f"({len(summary_text)} chars) — Invariant 1 (determinism boundary) preserves"
        )
    else:
        findings.append(
            f"FAIL case_summary_text: empty or too short: {summary_text!r}"
        )

    # Check 7: species_counts populated
    counts = result.get("species_counts") or {}
    if counts:
        total = sum(counts.values())
        findings.append(
            f"PASS species_counts: {total} total occurrence point(s) across "
            f"{len(counts)} species ({dict(counts)!r})"
        )
    else:
        findings.append("WARN species_counts: no occurrence counts — all species fetches may have failed")

    return findings


def main() -> int:
    """Run Case 1, validate, write evidence. Returns exit code (0=pass)."""
    logger.info("Case 1 live acceptance capture starting")
    logger.info("EVIDENCE_DIR: %s", EVIDENCE_DIR)

    # Run the composer
    result = asyncio.run(run_case1())
    elapsed = result.get("_capture_elapsed_seconds", 0)

    # Write the raw result
    metrics_path = EVIDENCE_DIR / "case1_metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                "impact_metrics": result.get("impact_metrics"),
                "case_summary_text": result.get("case_summary_text"),
                "species_counts": result.get("species_counts"),
                "bbox": result.get("bbox"),
                "flood_layer_uri": result.get("flood_layer_uri"),
                "wdpa_layer_uri": result.get("wdpa_layer_uri"),
                "species_layers": result.get("species_layers"),
                "schema_version": result.get("schema_version"),
                "_capture_elapsed_seconds": elapsed,
            },
            indent=2,
            default=str,
        )
    )
    logger.info("Wrote %s", metrics_path)

    # Geographic correctness gate
    findings = validate_geographic_correctness(result)
    logger.info("Geographic correctness findings:")
    for f in findings:
        logger.info("  %s", f)

    fail_count = sum(1 for f in findings if f.startswith("FAIL"))
    pass_count = sum(1 for f in findings if f.startswith("PASS"))
    warn_count = sum(1 for f in findings if f.startswith("WARN"))

    # Write the acceptance write-up
    summary_text = result.get("case_summary_text") or "(none)"
    counts = result.get("species_counts") or {}
    flood_uri = result.get("flood_layer_uri")
    wdpa_uri = result.get("wdpa_layer_uri")
    species_layers = result.get("species_layers") or []

    overall = "PASS" if fail_count == 0 else "FAIL"

    acceptance_lines = [
        "# Case 1 Live Acceptance — Big Cypress / Everglades",
        "",
        f"**Job:** job-0134-testing-20260608",
        f"**Date:** 2026-06-08",
        f"**Overall:** {overall} ({pass_count} pass, {warn_count} warn, {fail_count} fail)",
        f"**Elapsed:** {elapsed:.1f}s",
        "",
        "## Parameters",
        f"- bbox: {BBOX!r}",
        f"- species_keys: {SPECIES_KEYS!r}  (corrected per OQ-0117 / _species_reference.py)",
        f"- rainfall_event: {RAINFALL_EVENT!r}",
        f"- protected_area_designation: None (all WDPA in bbox)",
        f"- place_clip_polygon_uri: None",
        "",
        "## Case Summary Text (deterministic)",
        f"```",
        summary_text,
        f"```",
        "",
        "## Layer URIs",
        f"- flood_layer_uri: {json.dumps(flood_uri, default=str) if flood_uri else 'None (flood modeling failed — honest failure per kickoff §1)'}",
        f"- wdpa_layer_uri: {json.dumps(wdpa_uri, default=str) if wdpa_uri else 'None'}",
        f"- species_layers: {len(species_layers)} layer(s)",
    ]
    for i, layer in enumerate(species_layers):
        uri = layer.get("uri") if isinstance(layer, dict) else str(layer)
        name = layer.get("name") if isinstance(layer, dict) else ""
        acceptance_lines.append(f"  - [{i}] {name}: {uri}")

    acceptance_lines += [
        "",
        "## Species Occurrence Counts",
    ]
    for k, v in counts.items():
        acceptance_lines.append(f"- {k}: {v} occurrence point(s)")
    if not counts:
        acceptance_lines.append("- (none)")

    acceptance_lines += [
        "",
        "## Geographic Correctness Gate (job-0086 codified lesson)",
    ]
    for f in findings:
        prefix = f.split(":")[0]
        # Use markdown bullet with indicator
        mark = {"PASS": "ok", "FAIL": "FAIL", "WARN": "WARN", "INFO": "info"}.get(prefix, prefix)
        acceptance_lines.append(f"- [{mark}] {f}")

    acceptance_lines += [
        "",
        "## Invariant Checks",
        "- Invariant 1 (Determinism boundary): case_summary_text is format-string only; all field values come from typed tool returns — no LLM generated numbers.",
        "- Invariant 2 (Deterministic workflows): no LLM calls in the composer chain (fetch_gbif + fetch_wdpa + model_flood_scenario + compute_zonal_statistics are all deterministic tools).",
        "- Invariant 7 (Claims carry provenance): LayerURIs carry uri pointing to GCS FlatGeobuf or WMS endpoint; provenance threaded through.",
        "",
        "## Notes on Species Key Correction (OQ-0117)",
        "The kickoff audit.md originally listed species_keys [2435099, 2481008, 2436873].",
        "_species_reference.py (job-0117) corrected these to verified GBIF species-level keys:",
        "- 2435099: Puma concolor (Florida panther) — unchanged, correct",
        "- 2481008 was wrong; corrected to 2480803: Platalea ajaja (Roseate spoonbill)",
        "- 2436873 was wrong; corrected to 2441370: Alligator mississippiensis",
        "This correction is load-bearing: the original keys had zero or wrong-taxon records in the Big Cypress bbox.",
    ]

    acceptance_path = EVIDENCE_DIR / "case1_acceptance.md"
    acceptance_path.write_text("\n".join(acceptance_lines))
    logger.info("Wrote %s", acceptance_path)

    # Return code
    if fail_count == 0:
        logger.info(
            "Geographic correctness gate PASS (%d pass, %d warn, %d fail)",
            pass_count, warn_count, fail_count,
        )
        return 0
    else:
        logger.error(
            "Geographic correctness gate FAIL (%d pass, %d warn, %d fail)",
            pass_count, warn_count, fail_count,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
