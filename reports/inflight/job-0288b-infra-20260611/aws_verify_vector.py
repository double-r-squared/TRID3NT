"""job-0289 proof: vector overlay renders on AWS (inline_geojson populated via S3)."""
import asyncio, json, sys
import websockets
from grace2_contracts import new_ulid
from grace2_contracts.ws import Envelope, SessionResumePayload, UserMessagePayload

URL = "ws://ec2-35-93-91-8.us-west-2.compute.amazonaws.com:8765"


async def drive():
    sid = new_ulid()
    frames = []
    async with websockets.connect(URL, open_timeout=20.0) as ws:
        await ws.send(Envelope(type="session-resume", session_id=sid,
                               payload=SessionResumePayload()).model_dump_json())
        frames.append(json.loads(await asyncio.wait_for(ws.recv(), 20.0)))
        await ws.send(Envelope(type="user-message", session_id=sid,
                               payload=UserMessagePayload(
                                   text="Fetch and show the administrative boundary "
                                        "for Travis County, Texas on the map.")).model_dump_json())
        try:
            while True:
                frames.append(json.loads(await asyncio.wait_for(ws.recv(), 90.0)))
        except asyncio.TimeoutError:
            pass
    return frames


def main():
    frames = asyncio.run(drive())
    layers = []
    for f in frames:
        if f.get("type") == "session-state":
            for ly in (f.get("payload", {}) or {}).get("loaded_layers", []) or []:
                layers.append(ly)
    inlined = [ly for ly in layers if ly.get("inline_geojson")]
    print(f"[aws] {len(frames)} frames; loaded_layers seen: {len(layers)}; with inline_geojson: {len(inlined)}")
    for ly in inlined[:3]:
        gj = ly.get("inline_geojson") or {}
        feats = gj.get("features", []) if isinstance(gj, dict) else []
        print(f"[aws]   layer '{ly.get('name') or ly.get('layer_id')}' type={ly.get('layer_type')} "
              f"features={len(feats)}")
    if inlined and any((ly.get('inline_geojson') or {}).get('features') for ly in inlined):
        print("[PASS] vector overlay renders on AWS — inline GeoJSON served from S3 store")
        return 0
    print("[INFO] no inline vector populated; layers:", [(l.get('name'), l.get('layer_type')) for l in layers])
    return 1


if __name__ == "__main__":
    sys.exit(main())
