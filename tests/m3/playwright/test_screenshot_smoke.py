"""Smoke test: ``make ui-tour`` produces the expected PNGs.

Exit-criterion mapping (sprint-05.md, kickoff §Scope item 2):

* "test_screenshot_smoke.py — Chromium-only. Subprocess-invokes
  ``make ui-tour`` (cwd=repo root), asserts exit 0, asserts six PNGs land
  under the configured screenshot dir."

The kickoff explicitly notes: do not hardcode ``/tmp/grace2-shots/`` if the
Makefile uses a per-run dir. The current Makefile (job-0027) uses
``SHOTDIR ?= /tmp/grace2-shots`` — i.e., a default that's overridable from
the environment. This test overrides ``SHOTDIR`` to a per-test temp
directory so re-runs are deterministic and the assertion can count files
under that exact directory without colliding with a developer's stale
``/tmp/grace2-shots/`` from earlier captures.

Per-state count: ``make ui-tour`` walks six UI states across two browsers
(``UI_TOUR_STATES = initial after-message layer-panel-open
pipeline-running cancelled disconnected``) — 12 PNGs total. Six PNGs is
the minimum the kickoff requires; we assert >= 6 (the 12-PNG happy path
also satisfies it).

Failure-naming discipline: ``web client`` on capture failures (the
screenshot pipeline is web-tooling).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
WEB_DIR = REPO_ROOT / "web"


@pytest.mark.live_web
def test_make_ui_tour_smoke(
    tmp_path: Path,
    m3_artifacts_dir: Path,
) -> None:
    """Run ``make ui-tour SHOTDIR=<per-test>`` from the repo root; assert
    the make invocation succeeds and at least six PNG files land under
    the configured shot directory.
    """
    if not WEB_DIR.is_dir():
        pytest.skip(
            f"layer=dev-env: web/ directory not found at {WEB_DIR!s}; "
            "M3 web stub missing."
        )

    shotdir = tmp_path / "ui-tour-shots"
    shotdir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["SHOTDIR"] = str(shotdir)

    # `make ui-tour` walks 6 states x 2 browsers; the launcher is in
    # tools/screenshot.mjs and uses headless Playwright. Allow generous
    # timeout — 12 captures of MapLibre WebGL paint sum to ~60s.
    res = subprocess.run(
        ["make", "ui-tour"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )

    assert res.returncode == 0, (
        f"layer=web client (make ui-tour / tools/screenshot.mjs): the "
        f"ui-tour Make target exited rc={res.returncode}. "
        f"stdout tail: {res.stdout[-1200:]!r}; "
        f"stderr tail: {res.stderr[-1200:]!r}."
    )

    pngs = sorted(shotdir.glob("*.png"))
    assert len(pngs) >= 6, (
        f"layer=web client (make ui-tour PNG production): expected at "
        f"least 6 PNGs under {shotdir!s}, found {len(pngs)}. "
        f"Files: {[p.name for p in pngs]!r}. ui-tour stdout tail: "
        f"{res.stdout[-800:]!r}"
    )

    # Sanity: every produced file is a non-empty PNG (magic-byte check).
    PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
    for png in pngs:
        data = png.read_bytes()[:8]
        assert data.startswith(PNG_MAGIC), (
            f"layer=web client (make ui-tour PNG content): file {png!s} "
            f"does not start with PNG magic bytes; got {data!r}. "
            f"tools/screenshot.mjs is emitting a non-PNG artifact."
        )

    # Copy a single representative PNG into the m3 artifacts dir for the
    # evidence trail (the rest are ephemeral per kickoff §7).
    keep = pngs[0]
    dest = m3_artifacts_dir / f"ui-tour-smoke-sample-{keep.name}"
    dest.write_bytes(keep.read_bytes())
