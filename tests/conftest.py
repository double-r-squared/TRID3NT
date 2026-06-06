"""Root pytest fixtures for the GRACE-2 M1 acceptance suite (job-0017).

The single permitted mock boundary is the Gemini adapter seam (testing.md +
job-0017 kickoff). Every other test uses real transport against the real agent
service subprocess. Cloud-dependent fixtures (Atlas MCP smoke) are qualified
when the dependency is unreachable, never silently passed.

Fixtures:
- ``repo_root``: absolute path to the repo root.
- ``free_port``: an OS-assigned ephemeral TCP port for the agent subprocess.
- ``agent_subprocess``: starts the real ``grace2-agent`` WebSocket server with
  the Gemini adapter stubbed (``GRACE2_TEST_STUB_GEMINI=1``); yields the URL;
  terminates cleanly. The stub returns a deterministic delta stream so cancel +
  envelope-conformance tests are not flaky on real-LLM latency.
- ``agent_subprocess_live_gemini``: same shape but with the real adapter; used
  only by ``-m live_gemini`` opt-in tests.
- ``atlas_srv``: returns the Atlas SRV from Secret Manager via ADC, or ``None``
  when network/auth-gated. Tests check and self-qualify.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_VENV = REPO_ROOT / ".venv-agent"
AGENT_BIN = AGENT_VENV / "bin" / "grace2-agent"
AGENT_PY = AGENT_VENV / "bin" / "python"
AGENT_RUNNER = Path(__file__).resolve().parent / "_agent_runner.py"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def free_port() -> int:
    return _free_port()


def _wait_for_tcp(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _start_agent(env_extra: dict[str, str], port: int) -> subprocess.Popen:
    if not AGENT_PY.exists():
        pytest.fail(
            f"agent venv missing at {AGENT_VENV} — bootstrap with "
            "`virtualenv -p python3 .venv-agent && .venv-agent/bin/pip install "
            "-e packages/contracts -e services/agent`"
        )
    env = os.environ.copy()
    env.update(
        {
            "GRACE2_AGENT_PORT": str(port),
            # The Vertex/ADC vars are set so adapter.load_settings() succeeds even
            # if the stub path is used (build_client/stream_reply are bypassed by
            # the stub but settings still load).
            "GOOGLE_GENAI_USE_VERTEXAI": env.get("GOOGLE_GENAI_USE_VERTEXAI", "True"),
            "GOOGLE_CLOUD_PROJECT": env.get(
                "GOOGLE_CLOUD_PROJECT", "grace-2-hazard-prod"
            ),
            "GOOGLE_CLOUD_LOCATION": env.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            # Lower log noise from the agent subprocess.
            "GRACE2_AGENT_LOG": env.get("GRACE2_AGENT_LOG", "WARNING"),
        }
    )
    env.update(env_extra)
    # Run via the tests/_agent_runner.py shim — it installs the Gemini stub
    # *before* the WebSocket server boots when GRACE2_TEST_STUB_GEMINI=1, and
    # otherwise calls into the real grace2_agent.main.run() entry point.
    proc = subprocess.Popen(
        [str(AGENT_PY), str(AGENT_RUNNER)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if not _wait_for_tcp("127.0.0.1", port, timeout=15.0):
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        stderr = b""
        if proc.stderr:
            try:
                stderr = proc.stderr.read() or b""
            except Exception:
                pass
        pytest.fail(
            f"agent subprocess on port {port} never opened. "
            f"stderr tail: {stderr[-2000:].decode(errors='replace')}"
        )
    return proc


def _stop_agent(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
    # Drain stderr (don't block forever).
    if proc.stderr:
        try:
            proc.stderr.close()
        except Exception:
            pass


@pytest.fixture()
def agent_subprocess(free_port: int):
    """Start the agent with the Gemini adapter stubbed; yield the WS URL."""
    proc = _start_agent({"GRACE2_TEST_STUB_GEMINI": "1"}, free_port)
    url = f"ws://127.0.0.1:{free_port}"
    try:
        yield url
    finally:
        _stop_agent(proc)


@pytest.fixture()
def agent_subprocess_live_gemini(free_port: int):
    """Start the agent with the REAL Gemini adapter; live_gemini marker only."""
    proc = _start_agent({"GRACE2_TEST_STUB_GEMINI": "0"}, free_port)
    url = f"ws://127.0.0.1:{free_port}"
    try:
        yield url
    finally:
        _stop_agent(proc)


@pytest.fixture(scope="session")
def atlas_srv() -> str | None:
    """Return the Atlas SRV from Secret Manager (ADC), or None if unreachable.

    Tests use this to self-qualify when Atlas is network-gated. Never raises.
    """
    try:
        from google.cloud import secretmanager  # type: ignore
    except Exception:
        return None
    try:
        client = secretmanager.SecretManagerServiceClient()
        name = "projects/grace-2-hazard-prod/secrets/mongodb-srv-dev/versions/latest"
        resp = client.access_secret_version(request={"name": name})
        return resp.payload.data.decode("utf-8")
    except Exception:
        return None
