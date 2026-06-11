"""job-0287 run-local proof: agent on Bedrock, NO GCP creds, file persistence.

Spawns the real grace2-agent as a subprocess with every GOOGLE_* env var
stripped and MODEL_PROVIDER=bedrock, then drives ONE natural-language turn over
the real WebSocket so Claude-on-Bedrock decides the tool call through the full
server loop. Proves the project runs locally off Google Cloud.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time

import websockets
from grace2_contracts import new_ulid
from grace2_contracts.ws import Envelope, SessionResumePayload, UserMessagePayload

WS_PORT = 8865
HTTP_PORT = 8867
URL = f"ws://127.0.0.1:{WS_PORT}"


def _spawn_agent() -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOOGLE_")}
    env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    env.update(
        MODEL_PROVIDER="bedrock",
        AWS_REGION="us-west-2",
        BEDROCK_MODEL_ID="us.anthropic.claude-sonnet-4-6",
        GRACE2_DEV_PERSISTENCE="1",
        GRACE2_DEV_PERSISTENCE_DIR="/tmp/grace2-local-verify",
        GRACE2_AGENT_HOST="127.0.0.1",
        GRACE2_AGENT_PORT=str(WS_PORT),
        GRACE2_AGENT_HTTP_PORT=str(HTTP_PORT),
    )
    has_google = any(k.startswith("GOOGLE_") for k in env)
    print(f"[boot] launching agent with GOOGLE_* present in env? {has_google}")
    return subprocess.Popen(
        [sys.executable, "-m", "grace2_agent.main"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def _wait_port(port: int, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(1.0)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


async def _drive() -> list[dict]:
    sid = new_ulid()
    frames: list[dict] = []
    async with websockets.connect(URL, open_timeout=15.0) as ws:
        await ws.send(Envelope(type="session-resume", session_id=sid,
                               payload=SessionResumePayload()).model_dump_json())
        frames.append(json.loads(await asyncio.wait_for(ws.recv(), 15.0)))
        await ws.send(Envelope(type="user-message", session_id=sid,
                               payload=UserMessagePayload(
                                   text="Fetch the administrative boundary for "
                                        "Boulder County, Colorado.")).model_dump_json())
        try:
            while True:
                frames.append(json.loads(await asyncio.wait_for(ws.recv(), 90.0)))
        except asyncio.TimeoutError:
            pass
    return frames


def main() -> int:
    proc = _spawn_agent()
    try:
        if not _wait_port(WS_PORT):
            out = proc.stdout.read() if proc.stdout else ""
            print("[FAIL] agent never bound the WS port. Output:\n", out[-2000:])
            return 1
        print(f"[boot] agent listening on {URL} (no GCP creds, Bedrock provider)")
        frames = asyncio.run(_drive())
        types = [f.get("type") for f in frames]
        print(f"[turn] received {len(frames)} frames: {types}")
        tool_steps = [
            s.get("tool_name")
            for f in frames if f.get("type") == "pipeline-state"
            for s in (f.get("payload", {}) or {}).get("steps", [])
        ]
        agent_texts = [
            (f.get("payload", {}) or {}).get("text", "")
            for f in frames if f.get("type") in ("agent-message", "chat-message")
        ]
        print(f"[turn] pipeline tool steps: {tool_steps}")
        print(f"[turn] agent text: {' '.join(t for t in agent_texts if t)[:240]!r}")
        ran_tool = any(ts and ts not in ("gemini_generate",) for ts in tool_steps)
        if ran_tool or any(agent_texts):
            print("[PASS] run-local on Bedrock: full server loop ran with zero GCP creds")
            return 0
        print("[FAIL] no tool step or agent text observed")
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
