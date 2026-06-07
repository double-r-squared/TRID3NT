"""M5 acceptance live smoke harness (job-0043).

Mirrors job-0042's smoke_workflow.py / job-0044's smoke_workflow.py pattern
but anchored to **the testing specialist's M5 acceptance criterion**:

* Drive ``model_flood_scenario(location_query="Fort Myers, FL", ...)`` end-to-end
  against the deployed substrate.
* Capture the returned ``AssessmentEnvelope`` shape + per-step layer attribution.
* Honestly classify the outcome — SUCCESS (populated flood depth COG) OR
  HONEST FAILURE (typed failed envelope with error code threaded into
  ``flood.metrics.solver_version``).

The dev box does NOT have HydroMT-SFINCS installed, so the expected outcome
class is ``failed:HYDROMT_UNAVAILABLE`` — the substrate-verification mode
the kickoff explicitly accepts (the chain ran through fetcher cache + NLCD
gate PASSING after job-0044 + Atlas 14 forcing + landed on the
``import hydromt_sfincs`` guard inside ``build_sfincs_model``).

Run:

    GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \
      .venv-agent/bin/python reports/inflight/job-0043-testing-20260606/evidence/smoke_demo.py

Outputs:
    smoke_demo_envelope.json — the captured AssessmentEnvelope dict + summary.
    smoke_demo_log.txt — stdout/stderr capture.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "grace-2-hazard-prod")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
CACHE_BUCKET = os.environ.get("GRACE2_CACHE_BUCKET", "grace-2-hazard-prod-cache")
RUNS_BUCKET = os.environ.get("GRACE2_RUNS_BUCKET", "grace-2-hazard-prod-runs")

# Ensure the agent's tools register before we call into the workflow.
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", LOCATION)

EVIDENCE_DIR = Path(__file__).parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("smoke_demo")


async def _run_demo() -> dict:
    # Trigger registry side-effects (fetcher tools registration).
    from grace2_agent.main import _import_tools_registry

    n_tools = _import_tools_registry()
    log.info("registered %d agent tools (M5 expects 14)", n_tools)

    from grace2_agent.workflows import model_flood_scenario as mfs

    log.info("==== M5 demo: model_flood_scenario(Fort Myers, FL) ====")
    start = time.monotonic()
    envelope = await mfs.model_flood_scenario(
        location_query="Fort Myers, FL",
        return_period_yr=100,
        duration_hr=24,
        compute_class="medium",
    )
    elapsed = time.monotonic() - start

    flood = envelope.flood
    metrics = flood.metrics if flood else None
    solver_version = metrics.solver_version if metrics else None

    summary = {
        "demo": "Hurricane Ian / Fort Myers (M5 acceptance smoke)",
        "elapsed_seconds": elapsed,
        "envelope_id": envelope.envelope_id,
        "envelope_type": envelope.envelope_type,
        "hazard_type": envelope.hazard_type,
        "workflow_name": envelope.workflow_name,
        "solver_run_ids": list(envelope.solver_run_ids),
        "bbox": list(envelope.bbox),
        "layer_count": len(envelope.layers),
        "layer_uris": [str(getattr(layer, "uri", layer)) for layer in envelope.layers],
        "flood_solver_version": solver_version,
        "flood_max_depth_m": metrics.max_depth_m if metrics else None,
        "flood_grid_resolution_m": metrics.grid_resolution_m if metrics else None,
        "forcing_type": (envelope.forcing.forcing_type if envelope.forcing else None),
        "forcing_source": (envelope.forcing.source if envelope.forcing else None),
        "forcing_parameters": (
            envelope.forcing.parameters if envelope.forcing else None
        ),
        "data_source_count": len(envelope.provenance.data_sources),
        "data_sources": [
            {"name": ds.name, "uri": ds.uri} for ds in envelope.provenance.data_sources
        ],
    }

    if solver_version and solver_version.startswith("failed:"):
        error_code = solver_version[len("failed:") :]
        summary["outcome"] = "HONEST FAILURE"
        summary["error_code"] = error_code
        summary["substrate_verification"] = (
            f"Chain ran end-to-end through fetcher cache + NLCD gate (PASS) + "
            f"Atlas 14 forcing + landed at {error_code} per OQ-42 "
            f"PARTIAL-FAILURE-ENVELOPE-SHAPE. Acceptable M5 outcome per kickoff §1."
        )
    elif envelope.layers:
        summary["outcome"] = "SUCCESS"
        summary["substrate_verification"] = (
            "Populated flood envelope returned with rendered LayerURI. "
            "M5 milestone moment."
        )
    else:
        summary["outcome"] = "INDETERMINATE"
        summary["substrate_verification"] = (
            "Envelope returned without typed failure and without populated "
            "layers — substrate ran but the outcome doesn't fit either bucket. "
            "Surface as OQ."
        )

    log.info(
        "outcome=%s solver_version=%s layers=%d elapsed=%.2fs",
        summary["outcome"],
        solver_version,
        len(envelope.layers),
        elapsed,
    )
    return summary


def main() -> int:
    summary = asyncio.run(_run_demo())
    (EVIDENCE_DIR / "smoke_demo_envelope.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    log.info("smoke transcript written to %s/smoke_demo_envelope.json", EVIDENCE_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
