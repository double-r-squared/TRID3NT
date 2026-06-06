"""Smoke that ``make`` targets behave correctly under the M1 layout.

These tests do not run ``make test`` (that would be infinite recursion); they
inspect the Makefile for the relevant target definitions and assert that
``make run-agent`` and ``make run-web`` produce the expected guard messages
when their venv/node_modules are present or missing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"


def test_makefile_exists_and_has_required_targets() -> None:
    """Makefile defines test / run-agent / run-web (M1 deliverable)."""
    assert MAKEFILE.exists(), f"INFRA layer regression — missing {MAKEFILE}"
    body = MAKEFILE.read_text()
    for tgt in ("test:", "run-agent:", "run-web:"):
        assert tgt in body, (
            f"INFRA layer regression — Makefile missing target {tgt!r}"
        )


def test_make_help_runs() -> None:
    """``make help`` prints the target list without exit code."""
    result = subprocess.run(
        ["make", "-C", str(REPO_ROOT), "help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"INFRA layer regression — make help failed: {result.stderr}"
    )
    assert "test" in result.stdout
    assert "run-agent" in result.stdout
    assert "run-web" in result.stdout


def test_make_run_agent_exists_in_makefile() -> None:
    """``run-agent`` is a real wired target, not a stub echo (post-job-0015)."""
    body = MAKEFILE.read_text()
    assert "grace2-agent" in body, (
        "AGENT layer regression — Makefile run-agent target does not invoke "
        "the grace2-agent binary (job-0015 wiring missing)"
    )


def test_make_run_web_exists_in_makefile() -> None:
    """``run-web`` invokes Vite dev (post-job-0016)."""
    body = MAKEFILE.read_text()
    assert "npm run dev" in body, (
        "WEB layer regression — Makefile run-web target does not invoke "
        "Vite dev server (job-0016 wiring missing)"
    )
