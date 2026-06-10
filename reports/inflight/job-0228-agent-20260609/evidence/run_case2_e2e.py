"""Live end-to-end evidence harness for the Case 2 composer (job-0228).

Runs ``model_groundwater_contamination_scenario`` against the synthetic Twin
Falls, Idaho TCE-spill fixture with ``GRACE2_MODFLOW_LOCAL=1`` and the real
``mf6`` 6.5.0 binary — NO Gemini/Vertex anywhere.

Geocoding is injected through the registry seam (a fixed Twin Falls centroid)
so the run is offline + deterministic; everything downstream — the MODFLOW deck
build (real flopy), the mf6 solve (real binary), and the UCN postprocess (real
rasterio reprojection) — is genuine.

Outputs:
  - a transcript to stdout (captured to evidence/case2_e2e.log)
  - extracted-params JSON to evidence/extracted_params.json
  - the plume summary JSON to evidence/plume_summary.json
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "services" / "agent" / "src"))

EVIDENCE = Path(__file__).resolve().parent
FIXTURE = (
    REPO / "services" / "agent" / "tests" / "fixtures" / "case2_news_article.txt"
)


def _find_mf6() -> str:
    env = os.environ.get("GRACE2_MF6_BIN")
    if env and Path(env).exists():
        return env
    for cand in ("/tmp/mf6", shutil.which("mf6")):
        if cand and Path(cand).exists():
            return cand
    for cand in REPO.rglob("mf6.5.0_linux/bin/mf6"):
        if cand.is_file():
            return str(cand)
    raise SystemExit("mf6 binary not found; set GRACE2_MF6_BIN")


def main() -> int:
    mf6 = _find_mf6()
    os.environ["GRACE2_MODFLOW_LOCAL"] = "1"
    os.environ["GRACE2_MF6_BIN"] = mf6
    print(f"[harness] mf6 binary: {mf6}")
    print(f"[harness] GRACE2_MODFLOW_LOCAL={os.environ['GRACE2_MODFLOW_LOCAL']}")

    from grace2_agent.tools import RegisteredTool, TOOL_REGISTRY
    import grace2_agent.workflows  # noqa: F401 — fire registration
    from grace2_agent.workflows.model_groundwater_contamination_scenario import (
        extract_spill_parameters,
        model_groundwater_contamination_scenario,
        RELEASE_RATE_MIN_KG_S,
        RELEASE_RATE_MAX_KG_S,
        DURATION_MIN_DAYS,
        DURATION_MAX_DAYS,
    )
    from grace2_agent.workflows.run_modflow import set_mf6_binary

    set_mf6_binary(mf6)

    # --- inject an offline Twin Falls, Idaho geocode (centroid) ---
    def _fake_geocode(query: str, **_):
        return {
            "name": query,
            "bbox": [-114.55, 42.45, -114.35, 42.65],
            "latitude": 42.5630,
            "longitude": -114.4609,
            "source": "harness-offline",
        }

    geo_meta = TOOL_REGISTRY["geocode_location"].metadata
    TOOL_REGISTRY["geocode_location"] = RegisteredTool(
        metadata=geo_meta, fn=_fake_geocode, module="harness"
    )

    text = FIXTURE.read_text()
    print(f"[harness] fixture: {FIXTURE} ({len(text)} chars)")

    # --- 1. pure extraction (assert plausibility) ---
    derived = extract_spill_parameters(text, geocode=True)
    print("\n[harness] === extracted spill parameters ===")
    for k, v in derived.items():
        print(f"  {k}: {v}")

    lat, lon = derived["spill_location_latlon"]
    # Idaho CONUS bands.
    assert 41.0 <= lat <= 49.0, f"lat {lat} not in Idaho band"
    assert -117.5 <= lon <= -111.0, f"lon {lon} not in Idaho band"
    assert (
        RELEASE_RATE_MIN_KG_S
        <= derived["release_rate_kg_s"]
        <= RELEASE_RATE_MAX_KG_S
    ), "release rate out of clamp band"
    assert (
        DURATION_MIN_DAYS <= derived["duration_days"] <= DURATION_MAX_DAYS
    ), "duration out of clamp band"
    assert derived["contaminant"] == "trichloroethylene"
    print("\n[harness] plausibility assertions PASSED (lat/lon in Idaho, "
          "rate+duration in clamp band, contaminant=TCE)")

    (EVIDENCE / "extracted_params.json").write_text(
        json.dumps(derived, indent=2, default=str)
    )

    # --- 2. full composer chain: confirmation gate -> real mf6 -> plume ---
    confirm_seen = {}

    async def _approve(env):
        confirm_seen["envelope"] = env.model_dump(mode="json")
        print(f"\n[harness] confirmation envelope emitted: tool={env.tool_name} "
              f"options={env.options}")
        print(f"[harness]   recommendation: {env.recommendation}")
        print("[harness] user APPROVES (programmatic) -> proceeding to MODFLOW")
        return True

    print("\n[harness] === running composer (real mf6 local solve) ===")
    result = asyncio.run(
        model_groundwater_contamination_scenario(
            article_text=text,
            confirmed=False,
            confirmation_hook=_approve,
        )
    )

    print("\n[harness] === Case2Result.summary ===")
    print(json.dumps(result.summary, indent=2))

    plume = result.plume_layer
    print("\n[harness] === plume layer ===")
    print(f"  layer_id: {plume.layer_id}")
    print(f"  uri: {plume.uri}")
    print(f"  max_concentration_mgl: {plume.max_concentration_mgl}")
    print(f"  plume_area_km2: {plume.plume_area_km2}")

    assert confirm_seen.get("envelope") is not None, "confirmation gate did not fire"
    assert plume.max_concentration_mgl > 0, "plume max concentration is zero"
    assert plume.plume_area_km2 > 0, "plume area is zero"
    print("\n[harness] plume summary NON-ZERO assertions PASSED")

    (EVIDENCE / "plume_summary.json").write_text(
        json.dumps(
            {
                "summary": result.summary,
                "plume_layer": plume.model_dump(mode="json"),
                "confirmation_envelope": confirm_seen["envelope"],
            },
            indent=2,
            default=str,
        )
    )
    print("\n[harness] EVIDENCE WRITTEN: extracted_params.json, plume_summary.json")
    print("[harness] CASE 2 END-TO-END: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
