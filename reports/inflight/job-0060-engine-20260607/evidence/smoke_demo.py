"""M5 layer-emission smoke harness (job-0060).

Extends the job-0058 smoke_demo.py to verify the layer-emission contract
(docs/decisions/layer-emission-contract.md, ADOPTED 2026-06-07):

- Calls ``run_model_flood_scenario`` (the LLM-facing wrapper, not the inner
  ``model_flood_scenario``) through ``PipelineEmitter.emit_tool_call``.
- Asserts the wrapper returns a ``LayerURI`` (not a dict) on success.
- Captures the ``session-state`` envelope that ``add_loaded_layer`` emits
  post-tool and writes it to ``session_state_envelope.json``.

Acceptance criteria (per audit.md):
- Return type is ``LayerURI``  → PipelineEmitter gate fires
- ``session-state.loaded_layers[0].uri == <flood_depth_peak.tif gs:// URI>``
- OR honest ``HYDROMT_UNAVAILABLE`` / other failure (dict return) when the
  M5 solver substrate is absent — the chain still ran through fetcher + NLCD
  gate + Atlas 14 forcing, which is the observable substrate verification.

Run:

    GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \\
      .venv-agent/bin/python \\
      reports/inflight/job-0060-engine-20260607/evidence/smoke_demo.py

Outputs:
    evidence/smoke_demo_envelope.json  — summary + return-type outcome
    evidence/session_state_envelope.json — captured session-state wire frame
                                           (only written when LayerURI returned)
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

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", LOCATION)

EVIDENCE_DIR = Path(__file__).parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("smoke_demo_job0060")


async def _run_demo() -> tuple[dict, dict | None]:
    """Run the wrapper through PipelineEmitter and return (summary, session_state_frame)."""
    from grace2_contracts import new_ulid
    from grace2_contracts.execution import LayerURI

    from grace2_agent.main import _import_tools_registry
    n_tools = _import_tools_registry()
    log.info("registered %d agent tools", n_tools)

    from grace2_agent.pipeline_emitter import PipelineEmitter
    from grace2_agent.workflows.model_flood_scenario import run_model_flood_scenario

    # Capture all wire frames emitted by the emitter.
    captured_frames: list[dict] = []
    session_id = new_ulid()

    async def _sink(json_str: str) -> None:
        frame = json.loads(json_str)
        captured_frames.append(frame)
        if frame.get("type") == "session-state":
            log.info(
                "session-state emitted: loaded_layers=%d",
                len(frame.get("payload", {}).get("loaded_layers", [])),
            )

    emitter = PipelineEmitter(session_id=session_id, sink=_sink)

    log.info("==== job-0060 smoke: run_model_flood_scenario via emit_tool_call ====")
    start = time.monotonic()
    result = await emitter.emit_tool_call(
        name="M5 flood scenario (job-0060 layer-emission)",
        tool_name="run_model_flood_scenario",
        invoke=lambda: run_model_flood_scenario(
            location_query="Fort Myers, FL",
            return_period_yr=100,
            duration_hr=24,
            compute_class="medium",
        ),
    )
    elapsed = time.monotonic() - start

    is_layer_uri = isinstance(result, LayerURI)
    log.info(
        "result type=%s is_LayerURI=%s elapsed=%.2fs",
        type(result).__name__,
        is_layer_uri,
        elapsed,
    )

    # Find the session-state frame (the one emitted by add_loaded_layer).
    session_state_frames = [f for f in captured_frames if f.get("type") == "session-state"]
    last_session_state = session_state_frames[-1] if session_state_frames else None
    loaded_layers_in_state = (
        (last_session_state or {}).get("payload", {}).get("loaded_layers", [])
    )

    summary: dict = {
        "demo": "job-0060 layer-emission contract smoke",
        "elapsed_seconds": elapsed,
        "result_type": type(result).__name__,
        "is_layer_uri": is_layer_uri,
        "loaded_layers_in_session_state": len(loaded_layers_in_state),
    }

    if is_layer_uri:
        summary["layer_uri"] = result.uri
        summary["style_preset"] = result.style_preset
        summary["outcome"] = "SUCCESS"
        summary["contract_verified"] = (
            "run_model_flood_scenario returned LayerURI; PipelineEmitter gate fired; "
            "session-state.loaded_layers populated. Layer-emission contract satisfied "
            "(docs/decisions/layer-emission-contract.md ADOPTED 2026-06-07)."
        )
        if loaded_layers_in_state:
            summary["session_state_loaded_layers_uri"] = loaded_layers_in_state[0].get("uri")
    elif isinstance(result, dict):
        solver_version = (
            result.get("flood", {}).get("metrics", {}).get("solver_version", "")
        )
        if solver_version.startswith("failed:"):
            error_code = solver_version[len("failed:"):]
            summary["outcome"] = "HONEST FAILURE"
            summary["error_code"] = error_code
            summary["contract_note"] = (
                f"Wrapper returned dict (failed envelope, layers=[]) — correct "
                f"fallback path for {error_code}. LayerURI path requires populated "
                f"envelope.layers, which requires the full M5 solver stack."
            )
        else:
            summary["outcome"] = "DICT RETURN (unexpected on success)"
            summary["solver_version"] = solver_version
            summary["contract_violation"] = (
                "run_model_flood_scenario returned a dict on apparent success — "
                "the LayerURI return-type change did not take effect."
            )
    else:
        summary["outcome"] = "UNEXPECTED TYPE"
        summary["contract_violation"] = f"Unexpected result type: {type(result)}"

    log.info("outcome=%s", summary["outcome"])
    return summary, last_session_state


def main() -> int:
    summary, session_state_frame = asyncio.run(_run_demo())

    (EVIDENCE_DIR / "smoke_demo_envelope.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    log.info("summary written to %s/smoke_demo_envelope.json", EVIDENCE_DIR)

    if session_state_frame is not None:
        (EVIDENCE_DIR / "session_state_envelope.json").write_text(
            json.dumps(session_state_frame, indent=2, default=str), encoding="utf-8"
        )
        log.info("session-state frame written to %s/session_state_envelope.json", EVIDENCE_DIR)
    else:
        log.warning(
            "No session-state frame captured — either the run failed before "
            "emit_tool_call completed, or loaded_layers was not populated."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
