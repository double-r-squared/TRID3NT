"""LIVE E2E evidence harness — GRACE2_MODFLOW_LOCAL=1 (job-0227 acceptance).

MODFLOWRunArgs -> deck -> local mf6 run to Normal termination -> postprocess ->
PlumeLayerURI with non-zero max_concentration_mgl and plume_area_km2 > 0.

NO Gemini/Vertex call — direct Python invocation of the atomic tool (the chat
loop is not used; this is a programmatic acceptance run).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# Local mode + the downloaded mf6 6.5.0 static binary (job-0221 verify dir).
os.environ["GRACE2_MODFLOW_LOCAL"] = "1"
MF6 = (
    "/home/nate/Documents/GRACE-2/reports/inflight/job-0221-engine-20260609/"
    "verify/mf6_extracted/mf6.5.0_linux/bin/mf6"
)
os.environ.setdefault("GRACE2_MF6_BIN", MF6)

from grace2_agent.tools.run_modflow_tool import run_modflow_job  # noqa: E402
from grace2_contracts.modflow_contracts import PlumeLayerURI  # noqa: E402


async def main() -> int:
    # A realistic spill: benzene release near Fort Myers, FL (Case-2 demo
    # geography for this engine substrate run — Case 2 itself is job-0228).
    result = await run_modflow_job(
        spill_location_latlon=(26.64, -81.87),
        contaminant="benzene",
        release_rate_kg_s=0.01,
        duration_days=30.0,
        # aquifer_k_ms / porosity left to the contract demo defaults.
    )

    print("=== run_modflow_job result type:", type(result).__name__)
    if isinstance(result, PlumeLayerURI):
        summary = {
            "layer_id": result.layer_id,
            "name": result.name,
            "layer_type": result.layer_type,
            "uri": result.uri,
            "style_preset": result.style_preset,
            "units": result.units,
            "bbox": list(result.bbox) if result.bbox else None,
            "max_concentration_mgl": result.max_concentration_mgl,
            "plume_area_km2": result.plume_area_km2,
        }
        print(json.dumps(summary, indent=2))
        # Acceptance assertions.
        assert result.max_concentration_mgl > 0.0, "max_concentration_mgl must be > 0"
        assert result.plume_area_km2 > 0.0, "plume_area_km2 must be > 0"
        print("\nACCEPTANCE PASS: non-zero max_concentration_mgl + plume_area_km2 > 0")
        # Persist the plume summary for the report evidence dir.
        out = (
            "/home/nate/Documents/GRACE-2/reports/inflight/"
            "job-0227-agent-20260609/evidence/plume_summary.json"
        )
        with open(out, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"plume summary written to {out}")
        return 0

    print("FAILURE — tool returned a non-layer result:")
    print(json.dumps(result, indent=2, default=str))
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
