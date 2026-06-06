"""No-``gs://``-URI grep over the production web build.

Exit-criterion mapping (sprint-05.md): EC1 anti-control —
"zero ``gs://`` fetches observed in browser network logs (FR-DT-5,
Invariant 5 Tier separation)".

This test is the STATIC half of the Tier-separation invariant check (the
runtime half is in ``test_wms_tiles.py`` which asserts zero gs:// network
requests during a real browser session). We run ``npm run build`` over the
web client and grep the bundled output for the literal ``gs://`` substring;
zero hits is the acceptance.

No browser — this is a pure subprocess test, but lives in the m3/playwright
suite so the test split (5–8 unique functions per kickoff §Scope) stays
discoverable in a single directory.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
WEB_DIR = REPO_ROOT / "web"
DIST_DIR = WEB_DIR / "dist"


def _build_web() -> tuple[int, str, str]:
    """Run ``npm run build`` in ``web/`` and return (returncode, stdout, stderr)."""
    res = subprocess.run(
        ["npm", "run", "build"],
        cwd=str(WEB_DIR),
        capture_output=True,
        text=True,
        timeout=300,
    )
    return res.returncode, res.stdout, res.stderr


def test_no_gs_uri_in_web_build() -> None:
    """Build the web client and assert ``gs://`` never appears in any bundled
    asset. Tier separation (Invariant 5, FR-DT-5) is a static guarantee:
    no client code can reference GCS URIs.
    """
    if not WEB_DIR.is_dir():
        pytest.skip(
            f"layer=dev-env: web/ directory not found at {WEB_DIR!s}; "
            "M3 web stub missing."
        )

    rc, out, err = _build_web()
    assert rc == 0, (
        f"layer=web client (build): `npm run build` failed (rc={rc}). "
        f"stdout: {out[-1000:]!r}; stderr: {err[-1000:]!r}"
    )

    assert DIST_DIR.is_dir(), (
        f"layer=web client (build): dist/ missing after build. "
        f"Build stdout tail: {out[-500:]!r}"
    )

    offenders: list[Path] = []
    for path in DIST_DIR.rglob("*"):
        if not path.is_file():
            continue
        # Map source files and the index.html are fair game — grep raw bytes.
        try:
            data = path.read_bytes()
        except Exception:  # noqa: BLE001
            continue
        if b"gs://" in data:
            offenders.append(path)

    assert not offenders, (
        f"layer=web client (FR-DT-5 / Invariant 5 Tier separation): bundled "
        f"web build contains literal 'gs://' references. Offending files: "
        f"{[str(p.relative_to(REPO_ROOT)) for p in offenders]!r}. The "
        f"client must never read GCS directly; Tier B reaches the map only "
        f"via QGIS Server."
    )
