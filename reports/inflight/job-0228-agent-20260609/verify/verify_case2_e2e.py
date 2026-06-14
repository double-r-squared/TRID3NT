"""INDEPENDENT live-verify harness for the Case 2 composer (job-0228).

Written from scratch by the LIVE-VERIFY adversarial reviewer — does NOT reuse
the runner's evidence/run_case2_e2e.py. Differences from the runner's harness
that make this an independent regeneration:

  * Uses the REAL nominatim geocoder (NOT an injected fake) — confirmed to work
    offline on this box — so the location->latlon resolution is genuinely
    end-to-end, not stubbed.
  * Re-derives the unit math by hand and asserts it against the composer output
    independently.
  * Opens the published plume COG with rasterio DIRECTLY and numerically
    inspects the concentration array: non-zero, finite, and peaked near the
    spill source cell (argmax distance-to-source check), beyond just the
    scalar summary fields the composer returns.

NO Gemini/Vertex anywhere — programmatic invocation + local mf6 binary only.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import sys
from pathlib import Path

REPO = Path("/home/nate/Documents/GRACE-2")
sys.path.insert(0, str(REPO / "services" / "agent" / "src"))

VERIFY = Path(__file__).resolve().parent
FIXTURE = REPO / "services" / "agent" / "tests" / "fixtures" / "case2_news_article.txt"

# Hand-computed expected unit math (independent of the composer's constants).
_L_PER_GAL = 3.785411784
_TCE_DENSITY = 1.46  # kg/L
_EXP_MASS_KG = 12000.0 * _L_PER_GAL * _TCE_DENSITY
_EXP_DURATION_D = 6.0 / 24.0  # six hours
_EXP_RATE = _EXP_MASS_KG / (_EXP_DURATION_D * 86400.0)


def _find_mf6() -> str:
    for cand in (os.environ.get("GRACE2_MF6_BIN"), "/tmp/mf6", shutil.which("mf6")):
        if cand and Path(cand).exists():
            return cand
    raise SystemExit("mf6 binary not found")


def main() -> int:
    mf6 = _find_mf6()
    os.environ["GRACE2_MODFLOW_LOCAL"] = "1"
    os.environ["GRACE2_MF6_BIN"] = mf6
    log: list[str] = []

    def emit(s: str = "") -> None:
        print(s)
        log.append(s)

    emit(f"[verify] mf6 binary: {mf6}")
    emit(f"[verify] GRACE2_MODFLOW_LOCAL={os.environ['GRACE2_MODFLOW_LOCAL']}")

    import grace2_agent.tools  # noqa: F401 — fire base registration (real geocode)
    import grace2_agent.workflows  # noqa: F401 — fire composer registration
    from grace2_agent.tools import TOOL_REGISTRY
    from grace2_agent.workflows.run_modflow import set_mf6_binary
    from grace2_agent.workflows.model_groundwater_contamination_scenario import (
        DURATION_MAX_DAYS,
        DURATION_MIN_DAYS,
        RELEASE_RATE_MAX_KG_S,
        RELEASE_RATE_MIN_KG_S,
        extract_spill_parameters,
        model_groundwater_contamination_scenario,
        ConfirmationDeniedError,
    )

    set_mf6_binary(mf6)

    text = FIXTURE.read_text()
    emit(f"[verify] fixture: {FIXTURE} ({len(text)} chars)")
    # Independent sanity: fixture is flagged synthetic and is NOT Florida.
    assert "SYNTHETIC" in text.splitlines()[0].upper(), "fixture not flagged synthetic"
    assert "florida" not in text.lower(), "fixture must not be Florida (kickoff)"
    assert "twin falls" in text.lower() and "idaho" in text.lower()

    # --- 1. extraction with the REAL geocoder (no fake) -----------------------
    emit("\n[verify] === extraction (REAL nominatim geocode) ===")
    derived = extract_spill_parameters(text, geocode=True)
    for k, v in derived.items():
        emit(f"  {k}: {v}")

    # Independent unit-math re-derivation.
    emit("\n[verify] === independent unit-math check ===")
    emit(f"  hand mass_kg   = 12000 gal * {_L_PER_GAL} L/gal * {_TCE_DENSITY} kg/L = {_EXP_MASS_KG:.4f}")
    emit(f"  hand duration  = 6 h / 24 = {_EXP_DURATION_D:.6f} d")
    emit(f"  hand rate      = mass / (dur_d*86400) = {_EXP_RATE:.6f} kg/s")
    assert math.isclose(derived["total_mass_kg"], _EXP_MASS_KG, rel_tol=1e-9), "mass mismatch"
    assert math.isclose(derived["duration_days"], _EXP_DURATION_D, rel_tol=1e-9), "duration mismatch"
    assert math.isclose(derived["release_rate_kg_s"], _EXP_RATE, rel_tol=1e-9), "rate mismatch"
    assert derived["contaminant"] == "trichloroethylene"
    assert derived["clamps_applied"] == [], "no clamp should fire on this plausible spill"

    lat, lon = derived["spill_location_latlon"]
    emit(f"\n[verify] REAL geocode -> spill point = ({lat}, {lon})")
    assert 41.0 <= lat <= 49.0, f"lat {lat} not in Idaho band"
    assert -117.5 <= lon <= -111.0, f"lon {lon} not in Idaho band"
    assert RELEASE_RATE_MIN_KG_S <= derived["release_rate_kg_s"] <= RELEASE_RATE_MAX_KG_S
    assert DURATION_MIN_DAYS <= derived["duration_days"] <= DURATION_MAX_DAYS
    emit("[verify] plausibility assertions PASSED")

    (VERIFY / "extracted_params.json").write_text(
        json.dumps(derived, indent=2, default=str)
    )

    # --- 2. fail-closed gate: no hook + confirmed=False MUST NOT run solver ----
    emit("\n[verify] === fail-closed gate (no confirm) ===")
    solver_calls = {"n": 0}
    real_modflow = TOOL_REGISTRY["run_modflow_job"]

    from grace2_agent.tools import RegisteredTool

    def _spy(**kw):
        solver_calls["n"] += 1
        return real_modflow.fn(**kw)

    TOOL_REGISTRY["run_modflow_job"] = RegisteredTool(
        metadata=real_modflow.metadata, fn=_spy, module="verify-spy"
    )
    try:
        asyncio.run(
            model_groundwater_contamination_scenario(
                article_text=text, confirmed=False, confirmation_hook=None
            )
        )
        raise SystemExit("FAIL: gate did not block without confirmation")
    except ConfirmationDeniedError:
        emit("[verify] gate raised ConfirmationDeniedError (fail-closed) — OK")
    assert solver_calls["n"] == 0, "solver ran despite denied confirmation!"
    emit(f"[verify] solver dispatch count after deny = {solver_calls['n']} (must be 0) — OK")
    # restore real tool for the actual run
    TOOL_REGISTRY["run_modflow_job"] = real_modflow

    # --- 3. full chain w/ approving hook -> REAL mf6 local solve --------------
    emit("\n[verify] === full chain: approving hook -> real mf6 ===")
    seen = {}

    async def _approve(env):
        seen["env"] = env.model_dump(mode="json")
        emit(f"[verify] confirmation envelope: tool={env.tool_name} options={env.options}")
        emit(f"[verify]   tool_args={json.dumps(env.tool_args)}")
        emit(f"[verify]   recommendation={env.recommendation}")
        return True

    result = asyncio.run(
        model_groundwater_contamination_scenario(
            article_text=text, confirmed=False, confirmation_hook=_approve
        )
    )
    assert seen.get("env") is not None, "confirmation hook never fired"
    # envelope sanity (independent)
    assert seen["env"]["tool_name"] == "run_modflow_job"
    assert seen["env"]["estimated_mb"] == 0.0 and seen["env"]["threshold_mb"] == 0.0
    assert seen["env"]["tool_args"]["contaminant"] == "trichloroethylene"

    plume = result.plume_layer
    emit("\n[verify] === Case2Result.summary ===")
    emit(json.dumps(result.summary, indent=2))
    emit("\n[verify] === plume layer ===")
    emit(f"  layer_id: {plume.layer_id}")
    emit(f"  uri: {plume.uri}")
    emit(f"  max_concentration_mgl: {plume.max_concentration_mgl}")
    emit(f"  plume_area_km2: {plume.plume_area_km2}")

    assert plume.max_concentration_mgl > 0, "max conc is zero"
    assert plume.plume_area_km2 > 0, "plume area is zero"
    assert math.isfinite(plume.max_concentration_mgl)
    assert math.isfinite(plume.plume_area_km2)

    # --- 4. DIRECT numeric inspection of the published COG --------------------
    emit("\n[verify] === DIRECT raster inspection of plume COG ===")
    cog_path = plume.uri.replace("file://", "")
    assert Path(cog_path).exists(), f"COG not on disk: {cog_path}"
    import numpy as np
    import rasterio

    with rasterio.open(cog_path) as ds:
        emit(f"  crs={ds.crs} size={ds.width}x{ds.height} nodata={ds.nodata}")
        arr = ds.read(1, masked=True)
        valid = arr.compressed()
        emit(f"  valid cells={valid.size} min={float(valid.min()):.6g} "
             f"max={float(valid.max()):.6g} mean={float(valid.mean()):.6g}")
        assert valid.size > 0, "raster has no valid cells"
        assert np.all(np.isfinite(valid)), "raster has non-finite values"
        assert float(valid.max()) > 0, "raster max is not positive"
        # peaked-near-source: the argmax cell should be near the spill lon/lat.
        full = ds.read(1)
        mask = ds.read_masks(1)
        full_masked = np.where(mask > 0, full, -np.inf)
        ji = np.unravel_index(np.argmax(full_masked), full_masked.shape)
        row, col = int(ji[0]), int(ji[1])
        px_lon, px_lat = ds.xy(row, col)  # transform gives (x=lon, y=lat) in 4326
        emit(f"  argmax cell (row={row},col={col}) -> lon/lat=({px_lon:.5f},{px_lat:.5f})")
        emit(f"  spill point lon/lat=({lon:.5f},{lat:.5f})")
        # crude great-circle-ish degree distance; spill grid is small (~km scale)
        d_deg = math.hypot(px_lon - lon, px_lat - lat)
        approx_km = d_deg * 111.0
        emit(f"  argmax-to-source distance ~= {approx_km:.3f} km ({d_deg:.5f} deg)")
        # The demo grid is a few km across centered on the spill; the peak must
        # sit within a small radius of the release point, not at a random edge.
        assert approx_km < 10.0, f"plume peak {approx_km:.2f} km from source (not peaked near source)"
    emit("[verify] raster non-zero + finite + peaked-near-source assertions PASSED")

    (VERIFY / "plume_summary.json").write_text(
        json.dumps(
            {
                "summary": result.summary,
                "plume_layer": plume.model_dump(mode="json"),
                "confirmation_envelope": seen["env"],
                "raster_inspection": {
                    "cog_path": cog_path,
                    "valid_cells": int(valid.size),
                    "max": float(valid.max()),
                    "min": float(valid.min()),
                    "argmax_lonlat": [px_lon, px_lat],
                    "spill_lonlat": [lon, lat],
                    "argmax_to_source_km": approx_km,
                },
            },
            indent=2,
            default=str,
        )
    )

    emit("\n[verify] INDEPENDENT CASE 2 END-TO-END: PASS")
    (VERIFY / "verify_case2_e2e.log").write_text("\n".join(log) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
