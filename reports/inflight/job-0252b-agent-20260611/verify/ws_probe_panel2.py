"""Panel live-verify probe (job-0252b re-panel, LIVE-VERIFY lens).

Drives a REAL WebSocket against a throwaway agent instance (loopback,
file persistence). NO Gemini: only auth handshakes are sent, never a
user-message.

Scenarios (arg 1 = ws url, arg 2 = scenario):
  forged   - send auth-token envelope with a forged JWT
  nonauth  - send a non-auth first envelope (session-resume)
  anon     - send auth-token with empty token (anonymous connect)

Prints every received envelope, the close code/reason, and exits 0.
"""
from __future__ import annotations

import asyncio
import json
import sys

import websockets
from grace2_contracts.common import new_ulid


async def run(url: str, scenario: str) -> None:
    sid = new_ulid()
    if scenario == "forged":
        out = {
            "type": "auth-token",
            "session_id": sid,
            "payload": {"token": "forged.invalid.jwt-panel-0252b"},
        }
    elif scenario == "nonauth":
        out = {"type": "session-resume", "session_id": sid, "payload": {}}
    elif scenario == "anon":
        out = {"type": "auth-token", "session_id": sid, "payload": {"token": ""}}
    else:
        raise SystemExit(f"unknown scenario {scenario!r}")

    async with websockets.connect(url, open_timeout=10) as ws:
        await ws.send(json.dumps(out))
        print(f"SENT type={out['type']} session_id={sid}")
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                env = json.loads(raw)
                print("RECV", json.dumps(env))
                # On the anon (gate-off) path the server keeps the socket
                # open after auth-ack; close it ourselves once acked.
                if env.get("type") == "auth-ack":
                    print("CLIENT-CLOSE after auth-ack")
                    return
        except websockets.exceptions.ConnectionClosed as cc:
            print(f"CLOSED code={cc.rcvd.code if cc.rcvd else None} "
                  f"reason={cc.rcvd.reason if cc.rcvd else None!r}")
        except asyncio.TimeoutError:
            print("TIMEOUT waiting for server frame (socket still open)")


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1], sys.argv[2]))
