"""Live smoke harness for model_flood_scenario (job-0042, M5 capstone).

What this exercises end-to-end against the deployed substrate
(``grace-2-hazard-prod``):

1. The fetcher chain (geocode → fetch_dem → fetch_landcover → fetch_river_geometry
   → lookup_precip_return_period) — uses the M4/sprint-07 atomic-tool surface
   verbatim. Real Nominatim / 3DEP / MRLC WMS / NHDPlus HR / Atlas 14 calls;
   real GCS cache writes.
2. The OQ-4 §4 NLCD validation gate inside ``build_sfincs_model`` — loads the
   manning_mapping.csv, reads the cached landcover GeoTIFF, validates the
   vintage's class set against the mapping. PASS path expected on Fort
   Myers / NLCD 2021.
3. The real ``run_solver`` Cloud Workflows dispatch (job-0041 substrate).
4. The real ``wait_for_completion`` poll + emitter loop.
5. The workflow returning a typed ``AssessmentEnvelope`` (success or honestly-
   failed shape).

HydroMT-SFINCS is NOT installed in the dev venv (it's a heavyweight dep that
the job-0040 SFINCS container has but the agent service test env does not).
Per the kickoff: "if SFINCS itself fails on this smoke ... the workflow chain
succeeding through to run_solver dispatch + wait_for_completion returning a
SOLVER_FAILED RunResult is still acceptable evidence — proves the composition
works." We monkey-patch ``build_sfincs_model`` to return a stub ``ModelSetup``
pointing at a real cache-bucket manifest URI (uploaded by this harness),
preserving the live dispatch + cancel-chain testing.

Run:

    .venv-agent/bin/python reports/inflight/job-0042-engine-20260606/evidence/smoke_workflow.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from grace2_contracts import new_ulid
from grace2_contracts.execution import ModelSetup

# Repo root on sys.path is handled by the venv's editable installs.
PROJECT = os.environ.get("GRACE2_GCP_PROJECT", "grace-2-hazard-prod")
LOCATION = os.environ.get("GRACE2_GCP_LOCATION", "us-central1")
CACHE_BUCKET = os.environ.get("GRACE2_CACHE_BUCKET", "grace-2-hazard-prod-cache")
RUNS_BUCKET = os.environ.get("GRACE2_RUNS_BUCKET", "grace-2-hazard-prod-runs")

EVIDENCE_DIR = Path(__file__).parent
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("smoke_workflow")


def _upload_synthetic_manifest(run_label: str) -> str:
    """Upload a synthetic SFINCS manifest mirroring job-0040's shape and return its gs:// URI."""
    from google.cloud import storage  # type: ignore[import-not-found]

    client = storage.Client(project=PROJECT)
    bucket = client.bucket(CACHE_BUCKET)
    key = f"cache/static-30d/sfincs-smoke/manifest-job-0042-{run_label}-{int(datetime.now(timezone.utc).timestamp())}.json"
    blob = bucket.blob(key)
    manifest = {"inputs": [], "sfincs_args": [], "outputs": []}
    blob.upload_from_string(
        json.dumps(manifest), content_type="application/json"
    )
    uri = f"gs://{CACHE_BUCKET}/{key}"
    log.info("uploaded synthetic SFINCS manifest: %s", uri)
    return uri


async def _smoke_full_workflow() -> dict:
    """Run the full workflow against Fort Myers with mocked build_sfincs_model.

    Returns a JSON-safe dict capturing the envelope shape + every step's
    discovery for the evidence transcript.
    """
    # Stub build_sfincs_model so it doesn't require HydroMT but still returns
    # a real-looking ModelSetup pointing at the synthetic manifest URI. The
    # live run_solver + wait_for_completion exercise the real cancellation +
    # progress chain — the SFINCS exit-code-2 outcome from job-0040's smoke
    # is the expected failure mode.
    from grace2_agent.workflows import model_flood_scenario as mfs

    label = "happy"
    manifest_uri = _upload_synthetic_manifest(label)

    captured: dict = {
        "bbox": [-81.92, 26.55, -81.80, 26.68],
        "fetched_classes_observed": None,
        "manning_mapping_path": str(Path(mfs.__file__).parent / "manning_mapping.csv"),
        "manifest_uri": manifest_uri,
        "envelope_summary": None,
    }

    def _stub_build_sfincs_model(*, dem_uri, landcover_uri, river_geometry_uri,
                                  forcing, bbox, options=None,
                                  nlcd_vintage_year=None,
                                  manning_mapping_csv=None):
        # Exercise the real OQ-4 §4 NLCD validation gate by calling into the
        # real sfincs_builder helpers — this is the load-bearing assertion
        # for invariant 7. Then return a synthetic ModelSetup pointing at
        # the live manifest URI so run_solver dispatches against the real
        # workflow substrate.
        #
        # GDAL's /vsigs/ backend needs separate auth (boto-style); rather
        # than configure that on the dev box, the smoke downloads the
        # landcover bytes to a local tmp file via google-cloud-storage (the
        # same auth path the rest of the agent uses) and points the
        # rasterio-based class-extractor at it.
        from grace2_agent.workflows.sfincs_builder import (
            _extract_unique_nlcd_classes,
            load_manning_mapping,
            validate_nlcd_vintage_against_mapping,
        )
        import tempfile
        from google.cloud import storage  # type: ignore[import-not-found]

        log.info("[stub build_sfincs_model] downloading landcover %s for local read", landcover_uri)
        sc = storage.Client(project=PROJECT)
        # gs://bucket/key → bucket, key
        no_prefix = landcover_uri[len("gs://"):]
        bucket_name, _, blob_key = no_prefix.partition("/")
        blob = sc.bucket(bucket_name).blob(blob_key)
        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as fh:
            local_lc = fh.name
        blob.download_to_filename(local_lc)
        log.info("[stub build_sfincs_model] reading classes from %s", local_lc)
        fetched = _extract_unique_nlcd_classes(local_lc)
        captured["fetched_classes_observed"] = sorted(fetched)
        mapping = load_manning_mapping(manning_mapping_csv)
        validate_nlcd_vintage_against_mapping(
            fetched_classes=fetched,
            nlcd_vintage_year=nlcd_vintage_year or 2021,
            mapping=mapping,
        )
        log.info(
            "[stub build_sfincs_model] NLCD gate PASS — fetched=%s subset of mapping(%d)",
            sorted(fetched),
            len(mapping),
        )
        return ModelSetup(
            setup_id=new_ulid(),
            solver="sfincs",
            setup_uri=manifest_uri,
            grid_resolution_m=30.0,
            bbox=bbox,
            parameters={
                "stub": True,
                "nlcd_vintage_year": nlcd_vintage_year,
                "fetched_classes": sorted(fetched),
            },
            created_at=datetime.now(timezone.utc),
        )

    mfs.build_sfincs_model = _stub_build_sfincs_model  # type: ignore[assignment]
    log.info("==== smoke: model_flood_scenario(Fort Myers) — composing M5 chain ====")
    envelope = await mfs.model_flood_scenario(
        bbox=(-81.92, 26.55, -81.80, 26.68),
        return_period_yr=100,
        duration_hr=24,
        compute_class="medium",
    )
    captured["envelope_summary"] = {
        "envelope_id": envelope.envelope_id,
        "envelope_type": envelope.envelope_type,
        "hazard_type": envelope.hazard_type,
        "workflow_name": envelope.workflow_name,
        "bbox": list(envelope.bbox),
        "layers_count": len(envelope.layers),
        "solver_run_ids": envelope.solver_run_ids,
        "forcing_type": envelope.forcing.forcing_type if envelope.forcing else None,
        "forcing_params": envelope.forcing.parameters if envelope.forcing else None,
        "flood_solver_version": envelope.flood.metrics.solver_version if envelope.flood else None,
        "flood_max_depth_m": envelope.flood.metrics.max_depth_m if envelope.flood else None,
        "flood_grid_resolution_m": envelope.flood.metrics.grid_resolution_m if envelope.flood else None,
        "data_source_count": len(envelope.provenance.data_sources),
    }
    log.info(
        "envelope_id=%s envelope_type=%s solver_run_ids=%s layers=%d",
        envelope.envelope_id,
        envelope.envelope_type,
        envelope.solver_run_ids,
        len(envelope.layers),
    )
    log.info(
        "flood.solver_version=%s flood.max_depth_m=%s",
        envelope.flood.metrics.solver_version,
        envelope.flood.metrics.max_depth_m,
    )
    return captured


async def _smoke_dispatch_chain() -> dict:
    """Second smoke pass: bypass the NLCD gate (treat observed classes as mapped),
    exercise the live run_solver + wait_for_completion chain end-to-end against
    the deployed substrate. The kickoff explicitly accepts a SOLVER_FAILED
    RunResult as evidence the composition works through to dispatch."""
    from grace2_agent.workflows import model_flood_scenario as mfs

    label = "dispatch"
    manifest_uri = _upload_synthetic_manifest(label)

    captured: dict = {
        "phase": "dispatch_chain",
        "manifest_uri": manifest_uri,
        "run_result": None,
        "envelope_summary": None,
    }

    def _stub_build_sfincs_model(*, dem_uri, landcover_uri, river_geometry_uri,
                                  forcing, bbox, options=None,
                                  nlcd_vintage_year=None,
                                  manning_mapping_csv=None):
        # Pass-through ModelSetup pointing at the live synthetic manifest.
        # NLCD validation gate is intentionally bypassed in this pass to
        # exercise the downstream run_solver + wait_for_completion seam.
        log.info("[stub build_sfincs_model] (gate-bypassed) returning ModelSetup -> %s", manifest_uri)
        return ModelSetup(
            setup_id=new_ulid(),
            solver="sfincs",
            setup_uri=manifest_uri,
            grid_resolution_m=30.0,
            bbox=bbox,
            parameters={"stub": True, "gate_bypassed_for_dispatch_smoke": True},
            created_at=datetime.now(timezone.utc),
        )

    mfs.build_sfincs_model = _stub_build_sfincs_model  # type: ignore[assignment]

    log.info("==== smoke: dispatch chain — live run_solver + wait_for_completion ====")
    envelope = await mfs.model_flood_scenario(
        bbox=(-81.92, 26.55, -81.80, 26.68),
        return_period_yr=100,
        duration_hr=24,
        compute_class="medium",
    )
    captured["envelope_summary"] = {
        "envelope_id": envelope.envelope_id,
        "envelope_type": envelope.envelope_type,
        "hazard_type": envelope.hazard_type,
        "workflow_name": envelope.workflow_name,
        "solver_run_ids": envelope.solver_run_ids,
        "layers_count": len(envelope.layers),
        "flood_solver_version": envelope.flood.metrics.solver_version if envelope.flood else None,
        "flood_max_depth_m": envelope.flood.metrics.max_depth_m if envelope.flood else None,
        "data_source_count": len(envelope.provenance.data_sources),
    }
    log.info(
        "envelope_id=%s solver_run_ids=%s flood.solver_version=%s",
        envelope.envelope_id,
        envelope.solver_run_ids,
        envelope.flood.metrics.solver_version if envelope.flood else None,
    )
    return captured


def main() -> int:
    captured_gate = asyncio.run(_smoke_full_workflow())
    (EVIDENCE_DIR / "smoke_envelope.json").write_text(
        json.dumps(captured_gate, indent=2, default=str), encoding="utf-8"
    )
    log.info("smoke transcript written to evidence/smoke_envelope.json")

    if os.environ.get("SMOKE_SKIP_DISPATCH"):
        log.info("SMOKE_SKIP_DISPATCH=1; not running the dispatch chain")
        return 0

    captured_dispatch = asyncio.run(_smoke_dispatch_chain())
    (EVIDENCE_DIR / "smoke_dispatch.json").write_text(
        json.dumps(captured_dispatch, indent=2, default=str), encoding="utf-8"
    )
    log.info("dispatch transcript written to evidence/smoke_dispatch.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
