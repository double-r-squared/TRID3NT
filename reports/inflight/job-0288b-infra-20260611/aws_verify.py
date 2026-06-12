"""End-to-end proof: drive the AWS EC2-hosted Bedrock agent over its public WS."""
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
                                   text="Fetch the administrative boundary for "
                                        "Miami-Dade County, Florida.")).model_dump_json())
        try:
            while True:
                frames.append(json.loads(await asyncio.wait_for(ws.recv(), 90.0)))
        except asyncio.TimeoutError:
            pass
    return frames


def main():
    frames = asyncio.run(drive())
    types = [f.get("type") for f in frames]
    tool_steps = [s.get("tool_name") for f in frames if f.get("type") == "pipeline-state"
                  for s in (f.get("payload", {}) or {}).get("steps", [])]
    chunks = [ (f.get("payload",{}) or {}).get("delta","") or (f.get("payload",{}) or {}).get("text","")
               for f in frames if "message" in (f.get("type") or "")]
    print(f"[aws] {len(frames)} frames; distinct types: {sorted(set(types))}")
    print(f"[aws] tool steps: {tool_steps}")
    print(f"[aws] narration: {''.join(chunks)[:240]!r}")
    real_tool = any(ts and ts != 'gemini_generate' for ts in tool_steps)
    if real_tool:
        print("[PASS] AWS EC2 Bedrock agent ran the full loop over its public WebSocket")
        return 0
    print("[FAIL] no real tool step observed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
