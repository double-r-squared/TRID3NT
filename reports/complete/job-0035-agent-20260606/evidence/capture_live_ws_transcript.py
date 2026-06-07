"""job-0035 live-evidence harness: capture a real WS frame transcript.

Demonstrates the OQ-T-28-SIM-WS-BOUNDARY closure: the M3 dev-injection seam
(``window.__grace2InjectPipelineState``) is no longer necessary — the agent
itself emits real ``pipeline-state`` and ``session-state`` envelopes for
every tool invocation.

What this script does:

1. Stub Gemini settings (the M1 ``adapter.load_settings()`` requires GCP
   ADC; we patch ``build_client``/``stream_reply`` so no API key is needed —
   the directive path doesn't touch Gemini anyway).
2. Register two M4-style demo tools into the agent's ``TOOL_REGISTRY`` so
   the ``/invoke <tool> <json>`` path actually returns ``LayerURI`` objects
   the emitter funnels into ``loaded_layers``. (Per FROZEN-paths the
   ``tools/`` package source files are not edited; we register from the
   harness at runtime — this is the same import-time pattern job-0032
   established, just done from a script.)
3. Boot the WS server on a free localhost port.
4. Connect a websocket client; send ``session-resume`` then three
   ``user-message`` envelopes (Appendix A) carrying the ``/invoke``
   directive for ``demo_geocode`` → ``demo_fetch_dem`` → ``demo_fetch_pop``.
5. Capture every inbound frame and dump them to
   ``ws_transcript.json`` + a human-readable ``ws_transcript.txt`` so the
   orchestrator audit can re-grep for envelope types.

Mirrors the M3 Playwright ``page.on("websocket") -> framesent`` capture
pattern but on the server side, because the live evidence here is the
AGENT's emissions, not the client's.

Run:
    .venv-agent/bin/python reports/inflight/job-0035-agent-20260606/evidence/capture_live_ws_transcript.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
from pathlib import Path

# Path hygiene so we can import grace2_agent without installing.
THIS = Path(__file__).resolve()
ROOT = THIS.parent.parent.parent.parent.parent  # /home/nate/Documents/GRACE-2
sys.path.insert(0, str(ROOT / "services" / "agent" / "src"))

import websockets  # type: ignore
from grace2_contracts import new_ulid
from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata
from grace2_contracts.ws import Envelope, SessionResumePayload, UserMessagePayload

from grace2_agent import adapter as agent_adapter
from grace2_agent import server as agent_server
from grace2_agent.tools import register_tool

logger = logging.getLogger("job-0035.evidence")


# --------------------------------------------------------------------------- #
# 1. Stub Gemini so the M1 path doesn't bring up a real Vertex client. (The
#    /invoke directive bypasses _stream_gemini_reply entirely, but
#    load_settings still runs at server boot.)
# --------------------------------------------------------------------------- #


def _patch_adapter() -> None:
    class _StubSettings:
        model = "gemini-3-stub"
        project = "stub-project"
        location = "us-central1"

    agent_adapter.load_settings = lambda: _StubSettings()  # type: ignore[assignment]
    agent_adapter.build_client = lambda settings: None  # type: ignore[assignment]

    async def _stub_stream(client, model, text):  # type: ignore[no-untyped-def]
        yield "stub"

    agent_adapter.stream_reply = _stub_stream  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# 2. Register demo M4 tools. These return real LayerURIs so the emitter's
#    session-state path is exercised end-to-end.
# --------------------------------------------------------------------------- #


def _register_demo_tools() -> None:
    @register_tool(
        AtomicToolMetadata(
            name="demo_geocode",
            ttl_class="static-30d",
            source_class="nominatim",
            cacheable=True,
        )
    )
    def demo_geocode(query: str) -> dict:
        """Use this when: the agent needs a bbox + canonical name for a place.

        Do NOT use this for: spatial picks where the user must draw a point —
        use request_spatial_input instead.

        Params:
            query: human-readable place name (e.g. ``"Fort Myers, FL"``).
        Returns:
            ``{label, bbox}`` dict ready for the ``location-resolved``
            envelope. Real fetcher lands in job-0033.
        """
        # Fort Myers approximate bbox for the demo.
        return {
            "label": f"Fort Myers (resolved from {query!r})",
            "bbox": [-82.10, 26.40, -81.60, 26.90],
        }

    @register_tool(
        AtomicToolMetadata(
            name="demo_fetch_dem",
            ttl_class="static-30d",
            source_class="usgs-3dep",
            cacheable=True,
        )
    )
    def demo_fetch_dem(bbox: list) -> LayerURI:
        """Use this when: the agent needs a DEM raster for a bbox.

        Do NOT use this for: vector layers (use demo_fetch_pop or others).

        Params:
            bbox: ``[minLon, minLat, maxLon, maxLat]`` in EPSG:4326.
        Returns:
            ``LayerURI`` pointing at a COG in the cache bucket. job-0033's
            real implementation goes through ``cache.read_through``.
        """
        return LayerURI(
            layer_id=f"dem_{new_ulid()[:8]}",
            name="Demo DEM (Fort Myers)",
            layer_type="raster",
            uri="gs://grace-2-hazard-prod-cache/cache/static-30d/usgs-3dep/demo-dem.tif",
            style_preset="dem-default",
        )

    @register_tool(
        AtomicToolMetadata(
            name="demo_fetch_pop",
            ttl_class="static-30d",
            source_class="worldpop",
            cacheable=True,
        )
    )
    def demo_fetch_pop(bbox: list) -> LayerURI:
        """Use this when: the agent needs a population raster for a bbox.

        Do NOT use this for: discrete building footprints (use
        demo_fetch_buildings — job-0033).

        Params:
            bbox: ``[minLon, minLat, maxLon, maxLat]`` in EPSG:4326.
        Returns:
            ``LayerURI`` pointing at a population COG in the cache bucket.
        """
        return LayerURI(
            layer_id=f"pop_{new_ulid()[:8]}",
            name="Demo Population (Fort Myers)",
            layer_type="raster",
            uri="gs://grace-2-hazard-prod-cache/cache/static-30d/worldpop/demo-pop.tif",
            style_preset="population-quantile",
        )


# --------------------------------------------------------------------------- #
# 3. Boot the WS server on a free localhost port.
# --------------------------------------------------------------------------- #


def _pick_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _run_capture(out_dir: Path) -> dict:
    port = _pick_free_port()

    settings = agent_adapter.load_settings()
    handler = agent_server._make_handler(settings)

    server = await websockets.serve(handler, "127.0.0.1", port)
    logger.info("agent ws server up on ws://127.0.0.1:%d", port)

    transcript: list[dict] = []
    session_id = new_ulid()

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
            # --- 1. session-resume -> initial session-state ---------------- #
            await client.send(
                Envelope(
                    type="session-resume",
                    session_id=session_id,
                    payload=SessionResumePayload(),
                ).model_dump_json()
            )
            transcript.append(json.loads(await client.recv()))  # session-state

            # --- 2. /invoke demo_geocode "Fort Myers" --------------------- #
            await client.send(
                Envelope(
                    type="user-message",
                    session_id=session_id,
                    payload=UserMessagePayload(
                        text='/invoke demo_geocode {"query": "Fort Myers, FL"}'
                    ),
                ).model_dump_json()
            )
            # Drain frames until close_pipeline completes (3 pipeline-state).
            for _ in range(3):
                transcript.append(json.loads(await client.recv()))

            # --- 3. /invoke demo_fetch_dem -------------------------------- #
            await client.send(
                Envelope(
                    type="user-message",
                    session_id=session_id,
                    payload=UserMessagePayload(
                        text=(
                            '/invoke demo_fetch_dem '
                            '{"bbox": [-82.10, 26.40, -81.60, 26.90]}'
                        )
                    ),
                ).model_dump_json()
            )
            # Expected: pipeline-state(pending), pipeline-state(running),
            # session-state(after add_loaded_layer), pipeline-state(complete).
            for _ in range(4):
                transcript.append(json.loads(await client.recv()))

            # --- 4. /invoke demo_fetch_pop -------------------------------- #
            await client.send(
                Envelope(
                    type="user-message",
                    session_id=session_id,
                    payload=UserMessagePayload(
                        text=(
                            '/invoke demo_fetch_pop '
                            '{"bbox": [-82.10, 26.40, -81.60, 26.90]}'
                        )
                    ),
                ).model_dump_json()
            )
            for _ in range(4):
                transcript.append(json.loads(await client.recv()))

            # --- 5. Final session-resume to grab the cumulative session-state #
            await client.send(
                Envelope(
                    type="session-resume",
                    session_id=session_id,
                    payload=SessionResumePayload(),
                ).model_dump_json()
            )
            transcript.append(json.loads(await client.recv()))

    finally:
        server.close()
        await server.wait_closed()

    counts: dict[str, int] = {}
    for frame in transcript:
        t = frame.get("type", "?")
        counts[t] = counts.get(t, 0) + 1

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ws_transcript.json").write_text(json.dumps(transcript, indent=2))

    lines = [
        f"# job-0035 live WS transcript (session_id={session_id})",
        f"# Total frames: {len(transcript)}",
        f"# Type histogram: {json.dumps(counts)}",
        "#",
    ]
    for i, frame in enumerate(transcript):
        ftype = frame.get("type")
        payload = frame.get("payload", {})
        if ftype == "pipeline-state":
            states = [
                f"{s.get('name')}={s.get('state')}"
                + (
                    f"(progress={s['progress_percent']})"
                    if s.get("progress_percent") is not None
                    else ""
                )
                for s in payload.get("steps", [])
            ]
            lines.append(
                f"[{i:02d}] pipeline-state pipeline_id={payload.get('pipeline_id')} "
                f"steps={states}"
            )
        elif ftype == "session-state":
            layers = [layer.get("uri") for layer in payload.get("loaded_layers", [])]
            cp = payload.get("current_pipeline")
            cp_summary = (
                f"pipeline_id={cp.get('pipeline_id')} final_state={cp.get('final_state')}"
                if cp
                else "None"
            )
            lines.append(
                f"[{i:02d}] session-state loaded_layers={layers} "
                f"current_pipeline={cp_summary}"
            )
        else:
            lines.append(f"[{i:02d}] {ftype} payload={json.dumps(payload)[:80]}")
    (out_dir / "ws_transcript.txt").write_text("\n".join(lines) + "\n")

    return {"frames": len(transcript), "counts": counts, "session_id": session_id}


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    _patch_adapter()
    _register_demo_tools()

    out_dir = THIS.parent
    result = asyncio.run(_run_capture(out_dir))

    print(
        f"OK: captured {result['frames']} frames; "
        f"histogram={result['counts']}; session_id={result['session_id']}"
    )
    print(f"Artifacts: {out_dir / 'ws_transcript.json'}")
    print(f"           {out_dir / 'ws_transcript.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
