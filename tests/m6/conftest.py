"""Pytest fixtures for the GRACE-2 M6 acceptance suite (job-0066, sprint-09 Stage D).

This conftest is additive over ``tests/conftest.py`` (M1) and the M3 conftest.
M3 fixtures (vite_dev_server, playwright_instance, chromium_browser,
firefox_browser, browser_name, browser, m3_artifacts_dir) are re-used where
possible; M6-specific variants are added here.

Key differences vs M3:
- M6 tests are Chromium-only (the four sprint-09 acceptance screenshots are
  definitive proof; cross-browser coverage was established in M3).
- The artifacts directory is ``tests/m6/artifacts/`` (local) and
  ``reports/inflight/job-0066-testing-20260607/evidence/`` (canonical evidence
  committed to the audit trail).
- The same ``vite_dev_server`` fixture pattern is used (package-scoped).
- M6 tests are opt-in (only run when ``tests/m6`` is named explicitly), following
  the same ``pytest_collection_modifyitems`` pattern as M3 to avoid asyncio
  loop interference.

Marker:
- ``live_m6``: requires a local Vite dev server + Playwright (Chromium).

Boundary discipline:
- All four tests drive the dev-injection seams
  (``window.__grace2InjectSessionState`` and ``window.__grace2InjectPipelineState``)
  registered by App.tsx / Chat.tsx in dev mode. This is documented as intentional
  for sprint-09 acceptance per the kickoff: the live PyQGIS worker round-trip is
  deferred to sprint-10 (OQ-67-WORKER-IMAGE-REBUILD).
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

# Lazy Playwright import — missing wheel surfaces as a skip, not a
# collection failure (mirrors M3 conftest pattern).
try:
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

# Canonical evidence directory — screenshots committed to the audit trail.
EVIDENCE_DIR = (
    REPO_ROOT / "reports" / "inflight" / "job-0066-testing-20260607" / "evidence"
)


# ---------------------------------------------------------------------------
# Marker registration
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live_m6: sprint-09 Stage D acceptance tests — requires a local Vite "
        "dev server + Playwright Chromium. Run via `pytest tests/m6 -m live_m6` "
        "or `pytest tests/m6` (all M6 tests).",
    )


# ---------------------------------------------------------------------------
# Opt-in collection — M6 only runs when tests/m6 is explicitly invoked.
# Same rationale as M3: Playwright sync mode + pytest-asyncio loop conflict.
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    invocation_args = [str(a) for a in (config.invocation_params.args or [])]
    m6_invoked = any(
        "m6" in a and ("tests" in a or a.startswith("tests/m6") or "/m6" in a)
        for a in invocation_args
    ) or any(
        a.endswith("tests/m6") or a == "tests/m6" or "tests/m6" in a
        for a in invocation_args
    )

    if m6_invoked:
        return

    skip_marker = pytest.mark.skip(
        reason=(
            "M6 acceptance suite is opt-in via explicit `tests/m6` invocation "
            "(e.g. `pytest tests/m6 -v`). Skipping to preserve M1/M2 baseline "
            "runtime and asyncio-loop hygiene."
        )
    )
    for item in items:
        nodeid = item.nodeid.replace(str(config.rootpath), "").lstrip("/")
        if nodeid.startswith("m6/") or "/m6/" in nodeid or nodeid.startswith("tests/m6/"):
            item.add_marker(skip_marker)


# ---------------------------------------------------------------------------
# Port + HTTP helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_http(url: str, timeout: float = 90.0) -> bool:
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


# ---------------------------------------------------------------------------
# Vite dev server (package-scoped — torn down when pytest leaves tests/m6)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="package")
def m6_vite_dev_server() -> Iterator[str]:
    """Spawn ``npm run dev`` against ``web/`` on a free port.

    Package-scoped so the process is reaped before the M1 protocol suite
    can run in the same invocation — same rationale as M3.

    Yields the base URL ``http://127.0.0.1:<port>``.
    """
    if not WEB_DIR.is_dir():
        pytest.skip(
            f"layer=dev-env: web/ directory not found at {WEB_DIR!s}; "
            "M3/M6 web substrate missing (job-0025/0026/0027 prereqs)."
        )
    if not NODE_MODULES.is_dir():
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
    cmd = ["npm", "run", "dev", "--", "--port", str(port), "--strictPort"]
    proc = subprocess.Popen(
        cmd,
        cwd=str(WEB_DIR),
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base_url = f"http://127.0.0.1:{port}"
    if not _wait_for_http(base_url, timeout=90.0):
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
            f"{base_url} within 90s. stderr tail: "
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
# Playwright — Chromium only for M6 (cross-browser proved in M3)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="package")
def m6_playwright() -> Iterator["Playwright"]:
    """Playwright runtime scoped to the m6 package."""
    if not PLAYWRIGHT_AVAILABLE:
        pytest.skip(
            "layer=dev-env: playwright Python package not installed. "
            "Run `.venv-agent/bin/pip install playwright` and "
            "`playwright install chromium` to provision browsers."
        )
    assert sync_playwright is not None
    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="package")
def m6_chromium(m6_playwright: "Playwright") -> Iterator["Browser"]:
    """Chromium browser instance scoped to the m6 package."""
    browser = m6_playwright.chromium.launch(headless=True)
    try:
        yield browser
    finally:
        browser.close()


# ---------------------------------------------------------------------------
# Artifacts directories
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def m6_artifacts_dir() -> Path:
    """Local artifacts directory for M6 test screenshots (gitignored)."""
    d = Path(__file__).resolve().parent / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture(scope="session")
def m6_evidence_dir() -> Path:
    """Canonical evidence directory committed to the audit trail."""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    return EVIDENCE_DIR
