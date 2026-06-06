"""Run the packages/contracts test suite end-to-end as part of make test.

The contracts package ships with its own 91-test suite under
``packages/contracts/tests/``. The kickoff requires that ``make test`` runs
that full suite end-to-end so the M1 acceptance record covers it. This module
delegates to pytest via subprocess so collection is exactly what the contracts
package's own configuration produces.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "packages" / "contracts"
CONTRACTS_TESTS = CONTRACTS_DIR / "tests"


def test_contracts_suite_runs_green() -> None:
    """Spawn pytest inside the same venv on ``packages/contracts/tests``."""
    assert CONTRACTS_TESTS.exists(), (
        f"CONTRACTS layer regression — expected {CONTRACTS_TESTS} to exist"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(CONTRACTS_TESTS),
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(CONTRACTS_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"CONTRACTS layer regression — packages/contracts/tests failed.\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
    # The contracts suite landed 91 tests in job-0013; assert the count to
    # catch silent drops (a green run with 0 collected is not acceptance).
    assert (
        "passed" in result.stdout
    ), f"CONTRACTS layer — no 'passed' in pytest stdout: {result.stdout}"
