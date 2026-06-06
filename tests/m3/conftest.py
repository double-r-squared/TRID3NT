"""Pytest fixtures for the GRACE-2 M3 acceptance suite (job-0028).

This conftest is *additive* over ``tests/conftest.py`` (M1) and
``tests/m2/conftest.py`` (M2). The M1 and M2 fixtures stay untouched.

Owned fixtures
--------------

* ``vite_dev_server``: starts the real Vite dev server via ``make run-web``
  on an ephemeral port, yields its base URL, terminates cleanly. Build/install
  on first use; subsequent sessions reuse ``web/node_modules``.
* ``playwright_instance`` (session-scoped): start one Playwright runtime per
  pytest session to avoid the per-test browser spin-up cost.
* ``chromium_browser`` / ``firefox_browser`` (session-scoped): browser
  instances created on demand against ``~/.cache/ms-playwright`` (the npm
  install from ``make playwright-install`` matches the Python wheel versions
  required by ``playwright==1.60.0`` — chromium-1223 + firefox-1522).
* ``browser_name`` (parametrize): "chromium" / "firefox" — the
  parametrization seam for cross-browser tests.
* ``browser`` (function-scoped): yields the browser matching ``browser_name``.
* ``deployed_wms_url`` / ``deployed_wms_origin``: the deployed Cloud Run QGIS
  Server URL constants from PROJECT_STATE.md, scoped to job-0028.

Markers (registered here so tests can pick them up)
---------------------------------------------------

* ``live_web``: requires a running Vite dev server (the ``vite_dev_server``
  fixture).
* ``live_qgis_wms_browser``: requires the deployed Cloud Run QGIS Server
  reachable from a headless browser (consumed by the WMS-tile test).

Boundary discipline (testing.md)
--------------------------------

Layer-panel / pipeline-strip seeding uses the **in-page dev seam** —
``window.__grace2InjectSessionState`` / ``window.__grace2InjectPipelineState``
that ``web/src/App.tsx`` exposes under ``import.meta.env.DEV``. This is an
internal seam (not a mock-replacement for the agent) and is the only path
available for those component tests because the agent does not yet emit
populated ``session-state.loaded_layers`` or ``pipeline-state`` envelopes in
M3 (M4 work). Surfaced as Open Question in the report.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator

import pytest

# Lazy / optional Playwright import so a missing wheel surfaces as a
# fixture-level skip with attribution, not a collection failure.
try:  # pragma: no cover - import guard, not exercised under normal CI
    from playwright.sync_api import Browser, Playwright, sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except Exception:  # noqa: BLE001
    PLAYWRIGHT_AVAILABLE = False
    sync_playwright = None  # type: ignore[assignment]
    Browser = object  # type: ignore[assignment]
    Playwright = object  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WEB_DIR = REPO_ROOT / "web"
NODE_MODULES = WEB_DIR / "node_modules"

# Deployed QGIS Server constants (PROJECT_STATE.md "Live cloud substrate").
# Image @sha256:57d0f43 (post-CORS fix from job-0029 — confirmed by curl-I
# in the verification probe).
DEFAULT_DEPLOYED_QGIS_URL = (
    "https://grace-2-qgis-server-425352658356.us-central1.run.app"
)
DEFAULT_DEPLOYED_WMS_URL = (
    f"{DEFAULT_DEPLOYED_QGIS_URL}/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs"
)
DEFAULT_DEPLOYED_WMS_ORIGIN = (
    "grace-2-qgis-server-425352658356.us-central1.run.app"
)


# ---------------------------------------------------------------------------
# Marker registration (testing.md "Cloud-dependent tests get a documented
# local-fixture variant, or are reported qualified — never silently skipped").
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live_web: requires a local Vite dev server "
        "(spawned by the vite_dev_server fixture).",
    )
    config.addinivalue_line(
        "markers",
        "live_qgis_wms_browser: requires the deployed Cloud Run QGIS Server "
        "WMS endpoint reachable from a headless browser "
        "(deployed_wms_url fixture).",
    )


# ---------------------------------------------------------------------------
# Opt-in collection: M3 tests run only when the invocation explicitly names
# tests/m3 (so `make test-m3` includes them but `make test` does not).
#
# Rationale (Open Question in report): the M3 Playwright suite spins up a
# real Vite dev server, headless Chromium + Firefox, and a local WS capture
# server. Mixing those into the M1/M2 protocol/asyncio runs causes event-loop
# teardown interference (Playwright sync mode + pytest-asyncio Runner). The
# kickoff §Acceptance criteria mandates the M1/M2 baseline stays green under
# `make test` and the M3 suite runs under `make test-m3` — this hook honors
# that split without editing the FROZEN root Makefile or the M1 conftest.
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    invocation_args = [str(a) for a in (config.invocation_params.args or [])]
    m3_invoked = any(
        ("m3" in a) and ("tests" in a or a.startswith("tests/m3") or "/m3" in a)
        for a in invocation_args
    ) or any(a.endswith("tests/m3") or a == "tests/m3" or "tests/m3" in a for a in invocation_args)

    if m3_invoked:
        return

    skip_marker = pytest.mark.skip(
        reason=(
            "M3 acceptance suite is opt-in via `make test-m3` "
            "(tests/m3 explicit invocation). Skipping here to preserve the "
            "M1/M2 baseline runtime + asyncio-loop hygiene."
        )
    )
    for item in items:
        # Identify M3 items by their nodeid prefix.
        nodeid = item.nodeid.replace(str(config.rootpath), "").lstrip("/")
        if nodeid.startswith("m3/") or "/m3/" in nodeid or nodeid.startswith("tests/m3/"):
            item.add_marker(skip_marker)


# ---------------------------------------------------------------------------
# Substrate URL fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def deployed_wms_url() -> str:
    """Deployed QGIS Server WMS endpoint including MAP= query param.

    Default = the live URL captured during job-0029 CORS-fix re-verification.
    Override via ``GRACE2_DEPLOYED_WMS_URL`` for staging redirection.
    """
    return os.environ.get("GRACE2_DEPLOYED_WMS_URL", DEFAULT_DEPLOYED_WMS_URL)


@pytest.fixture(scope="session")
def deployed_wms_origin() -> str:
    """The host (no scheme) the WMS tile requests should target. Used by tests
    asserting that browser network requests went to the QGIS Server.
    """
    return os.environ.get(
        "GRACE2_DEPLOYED_WMS_ORIGIN", DEFAULT_DEPLOYED_WMS_ORIGIN
    )


@pytest.fixture(scope="session")
def qgis_server_url() -> str:
    """Bare QGIS Server URL (no /ogc path). Mirrors the M2 fixture but pinned
    to the current deployed URL (PROJECT_STATE.md; the M2 conftest still
    references an older URL).
    """
    return os.environ.get("GRACE2_QGIS_SERVER_URL", DEFAULT_DEPLOYED_QGIS_URL)


# ---------------------------------------------------------------------------
# Vite dev server lifecycle
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_http(url: str, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if 200 <= resp.status < 500:
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.3)
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


@pytest.fixture(scope="package")
def vite_dev_server() -> Iterator[str]:
    """Spawn ``npm run dev`` against ``web/`` on an ephemeral port.

    Package-scoped (m3) so the subprocess is reaped before the M1 protocol
    suite runs later in the same invocation — same rationale as the
    Playwright fixtures: we keep all M3 background processes inside
    ``tests/m3/``'s lifetime.

    Yields ``http://127.0.0.1:<port>`` once the server responds to HTTP.

    If ``web/node_modules`` is missing this fixture runs ``npm install``
    first (M3 was an additive sprint; node_modules should already be
    present from job-0025 / job-0027).
    """
    if not WEB_DIR.is_dir():
        pytest.skip(
            f"layer=dev-env: web/ directory not found at {WEB_DIR!s}; "
            "M3 web stub missing (job-0025/0026/0027 prereqs)."
        )
    if not NODE_MODULES.is_dir():
        # Bootstrap node_modules. Skip rather than fail if npm install errors
        # — keeps the suite portable across constrained dev hosts.
        try:
            subprocess.run(
                ["npm", "install"],
                cwd=str(WEB_DIR),
                check=True,
                timeout=600,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as exc:  # noqa: BLE001
            pytest.skip(
                f"layer=dev-env: `npm install` failed in {WEB_DIR!s}: {exc!r}. "
                "Cannot launch Vite dev server."
            )

    port = _free_port()
    env = os.environ.copy()
    # Override the port Vite binds via the `--port` CLI flag (Vite picks it up
    # from the `dev` script). We pass via npm-run arg forwarding so
    # vite.config.ts's strictPort + host don't conflict.
    cmd = ["npm", "run", "dev", "--", "--port", str(port), "--strictPort"]
    proc = subprocess.Popen(
        cmd,
        cwd=str(WEB_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base_url = f"http://127.0.0.1:{port}"
    if not _wait_for_http(base_url, timeout=60.0):
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        stderr = b""
        if proc.stderr:
            try:
                stderr = proc.stderr.read() or b""
            except Exception:  # noqa: BLE001
                pass
        pytest.fail(
            f"layer=web client (Vite dev server): never responded at "
            f"{base_url} within 60s. stderr tail: "
            f"{stderr[-2000:].decode(errors='replace')}"
        )
    try:
        yield base_url
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2.0)
                except Exception:  # noqa: BLE001
                    pass
        for stream in (proc.stdout, proc.stderr):
            if stream:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass


# ---------------------------------------------------------------------------
# Playwright lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture(scope="package")
def playwright_instance() -> Iterator["Playwright"]:
    """Playwright runtime scoped to the m3 package only.

    Package scope (not session scope) so the Playwright greenlet driver is
    torn down when pytest leaves ``tests/m3/`` — before the M1 protocol
    suite under ``tests/protocol/`` runs later in the same invocation.
    ``sync_playwright`` spins up a background thread running an asyncio
    loop; if that thread is still alive when pytest-asyncio 1.4.0 wraps
    the next protocol coroutine in ``asyncio.run()``, the protocol
    coroutine's loop-shutdown step raises ``RuntimeError: Cannot run the
    event loop while another loop is running`` and the test counts as
    failed without ever running its body.
    """
    if not PLAYWRIGHT_AVAILABLE:
        pytest.skip(
            "layer=dev-env: playwright python package not installed. "
            "Run `.venv-agent/bin/pip install playwright` and "
            "`make playwright-install` to provision browsers."
        )
    assert sync_playwright is not None
    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="package")
def chromium_browser(playwright_instance: "Playwright") -> Iterator["Browser"]:
    browser = playwright_instance.chromium.launch(headless=True)
    try:
        yield browser
    finally:
        browser.close()


@pytest.fixture(scope="package")
def firefox_browser(playwright_instance: "Playwright") -> Iterator["Browser"]:
    browser = playwright_instance.firefox.launch(headless=True)
    try:
        yield browser
    finally:
        browser.close()


@pytest.fixture(
    params=["chromium", "firefox"],
    ids=["chromium", "firefox"],
)
def browser_name(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture()
def browser(
    request: pytest.FixtureRequest,
    browser_name: str,
) -> "Browser":
    if browser_name == "chromium":
        return request.getfixturevalue("chromium_browser")
    if browser_name == "firefox":
        return request.getfixturevalue("firefox_browser")
    pytest.fail(f"layer=test-harness: unknown browser_name {browser_name!r}")


# ---------------------------------------------------------------------------
# Artifacts directory (per-test PNG / log dumps)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def m3_artifacts_dir() -> Path:
    """Per-session artifact directory for M3 test screenshots / dumps.

    Gitignored — only the canonical evidence captures committed under
    ``reports/inflight/job-0028-testing-20260606/evidence/`` make it into
    the audit trail.
    """
    d = Path(__file__).resolve().parent / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d
