"""NFR-P-3 WMS tile-latency measurement.

Exit-criterion mapping (sprint-05.md, kickoff §Scope item 3):

* "test_wms_tile_latency.py (NOT under playwright/; this is a pure Python
  HTTP-client test, no browser) — NFR-P-3 measurement: hit the deployed
  Cloud Run /ogc/wms 20 times with varied BBOX values, measure response
  time per call, compute p50 and p95, write a JSON report with full
  environment context, pass if p50 < 2000 ms."

Methodology (honest, per testing.md NFR discipline):

* Single-machine, single-process synchronous measurement using
  ``http.client``. Each call is wall-clock timed from request issue to
  response body fully received.
* N = 20 GetMap calls. First call is the cold tile (no warming pre-call);
  the remaining 19 are warm.
* BBOX values vary across the CONUS basemap-osm-conus tile pyramid so we
  exercise the QGIS Server's tile-mosaicking path, not a single cached
  tile.
* Client geography is the developer's box (Linux x86_64); deployed region
  is us-central1. The client-region geography is unknown to the test —
  we flag this in the JSON report's ``client_region_geography`` field as
  ``"unknown"``.

Pass criterion (per kickoff): p50 < 2000 ms (soft target from NFR-P-3 OQ-23E).
Methodology limitation surfaced honestly in both the JSON report and the
assertion message: this is a single-machine measurement; the true NFR-P-3
budget assumes a US-West-Coast client. We mark the run ``qualified`` in
the JSON when the test passes from an unknown-geography client, and the
assertion message names the limitation.

Failure-naming discipline: every assert names ``QGIS Server | network``.
"""

from __future__ import annotations

import http.client
import json
import os
import platform
import socket
import statistics
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Iterator

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EVIDENCE_DIR = (
    REPO_ROOT
    / "reports"
    / "inflight"
    / "job-0028-testing-20260606"
    / "evidence"
)

# Deployed QGIS Server (PROJECT_STATE.md "Live cloud substrate"; image
# @sha256:57d0f43 after job-0029 CORS fix). us-central1 Cloud Run.
DEFAULT_HOST = "grace-2-qgis-server-425352658356.us-central1.run.app"
DEFAULT_MAP = "/mnt/qgs/grace2-sample.qgs"
DEFAULT_LAYER = "basemap-osm-conus"
N_SAMPLES = 20
SOFT_TARGET_MS = 2000.0  # NFR-P-3 OQ-23E (kickoff §Scope item 3).

# A pyramid of CONUS BBOXes in EPSG:3857 (WebMercator) — 20 distinct windows
# stepping across the CONUS extent and zooming in / out so each request
# fetches a distinct tile composition (cache thrash on purpose).
# Pre-computed offline to keep the test dependency-free.
CONUS_BBOXES_3857 = [
    # zoom-4 ish, sliding window across CONUS
    "-14000000,2500000,-7000000,6000000",
    "-13500000,2700000,-7500000,5800000",
    "-13000000,2900000,-8000000,5600000",
    "-12500000,3100000,-8500000,5400000",
    "-12000000,3300000,-9000000,5200000",
    # zoom-5 ish, smaller windows
    "-11000000,3500000,-9500000,4700000",
    "-10800000,3600000,-9400000,4600000",
    "-10600000,3700000,-9300000,4500000",
    "-10400000,3800000,-9200000,4400000",
    "-10200000,3900000,-9100000,4300000",
    # zoom-6 ish, regional
    "-10100000,3950000,-9050000,4250000",
    "-10000000,4000000,-9000000,4200000",
    "-9900000,4050000,-8950000,4150000",
    "-9800000,4100000,-8900000,4150000",
    "-9700000,4150000,-8850000,4200000",
    # eastern / western edges of CONUS
    "-13200000,3000000,-12000000,4000000",
    "-8800000,3000000,-7800000,4000000",
    "-13500000,5500000,-12300000,6500000",
    "-9000000,2500000,-7800000,3500000",
    # high zoom on a coastal city
    "-9590000,4700000,-9560000,4730000",
]


def _build_path(map_q: str, layer: str, bbox: str) -> str:
    params = {
        "MAP": map_q,
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetMap",
        "CRS": "EPSG:3857",
        "LAYERS": layer,
        "FORMAT": "image/png",
        "WIDTH": "256",
        "HEIGHT": "256",
        "BBOX": bbox,
        "STYLES": "",
    }
    return "/ogc/wms?" + urllib.parse.urlencode(params)


def _time_one_call(host: str, path: str, timeout: float = 30.0) -> dict:
    """Issue one HTTPS GetMap call to ``host`` + ``path``. Return a dict
    with ``status`` (HTTP status code), ``ms`` (wall-clock duration of
    issue→full-body), ``bytes`` (response body length), and ``ctype``
    (content-type header), or an ``error`` field if the call failed."""
    conn = http.client.HTTPSConnection(host, timeout=timeout)
    try:
        t0 = time.perf_counter()
        conn.request("GET", path, headers={"User-Agent": "grace2-test/0.0"})
        resp = conn.getresponse()
        body = resp.read()  # fully consume body — measures real tile latency
        t1 = time.perf_counter()
        return {
            "status": resp.status,
            "ms": (t1 - t0) * 1000.0,
            "bytes": len(body),
            "ctype": (resp.getheader("content-type") or "").lower(),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc!s}"}
    finally:
        conn.close()


@pytest.mark.live_qgis_wms_browser
def test_wms_tile_latency_nfr_p3() -> None:
    """N=20 GetMap calls against the deployed Cloud Run QGIS Server.

    Honest single-machine methodology. Writes a JSON report under
    reports/inflight/job-0028-testing-20260606/evidence/ regardless of
    pass/fail. Passes if p50 < 2000 ms.
    """
    host = os.environ.get("GRACE2_DEPLOYED_QGIS_HOST", DEFAULT_HOST)
    map_q = os.environ.get("GRACE2_DEPLOYED_WMS_MAP", DEFAULT_MAP)
    layer = os.environ.get("GRACE2_DEPLOYED_WMS_LAYER", DEFAULT_LAYER)

    # Cheap reachability probe — if DNS fails or the host refuses, skip
    # rather than fail (testing.md "cloud-dependent tests get a documented
    # local-fixture variant or are reported qualified").
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        pytest.skip(
            f"layer=network: cannot resolve {host!r} ({exc!s}). NFR-P-3 "
            f"measurement requires the deployed Cloud Run QGIS Server."
        )

    samples: list[dict] = []
    errors: list[dict] = []
    for i, bbox in enumerate(CONUS_BBOXES_3857[:N_SAMPLES]):
        path = _build_path(map_q, layer, bbox)
        cold_warm = "cold" if i == 0 else "warm"
        rec = _time_one_call(host, path)
        rec["index"] = i
        rec["bbox"] = bbox
        rec["cold_warm"] = cold_warm
        if "error" in rec:
            errors.append(rec)
        else:
            samples.append(rec)

    # Compute percentiles on durations of successful 200/PNG calls only.
    successful = [
        s for s in samples
        if s.get("status") == 200 and "image/png" in s.get("ctype", "")
    ]
    durations = [s["ms"] for s in successful]
    durations_sorted = sorted(durations)

    def _percentile(xs: list[float], p: float) -> float | None:
        if not xs:
            return None
        # statistics.quantiles requires n>=2; for p50 use median.
        if p == 50:
            return statistics.median(xs)
        if len(xs) < 2:
            return xs[0]
        # nearest-rank percentile (Type 7 / linear)
        k = (len(xs) - 1) * (p / 100.0)
        lo = int(k)
        hi = min(lo + 1, len(xs) - 1)
        frac = k - lo
        return xs[lo] * (1 - frac) + xs[hi] * frac

    p50 = _percentile(durations_sorted, 50)
    p95 = _percentile(durations_sorted, 95)
    p99 = _percentile(durations_sorted, 99)
    pmin = min(durations_sorted) if durations_sorted else None
    pmax = max(durations_sorted) if durations_sorted else None
    pmean = statistics.fmean(durations_sorted) if durations_sorted else None

    # NFR-P-3 status. "qualified" = passes the soft target but the
    # client-region geography is unknown.
    status = "fail"
    if p50 is not None and p50 < SOFT_TARGET_MS:
        status = (
            "qualified" if os.environ.get("GRACE2_CLIENT_REGION") is None else "pass"
        )

    report = {
        "test": "tests/m3/test_wms_tile_latency.py::test_wms_tile_latency_nfr_p3",
        "nfr": "NFR-P-3 (OQ-23E soft target: QGIS Server tile latency p50)",
        "soft_target_ms_p50": SOFT_TARGET_MS,
        "status": status,
        "methodology_limit": (
            "single-machine synchronous HTTPS measurement; client-region "
            "geography unknown; NFR-P-3 budget assumes US-West-Coast "
            "client per testing.md NFR discipline (PROJECT_STATE.md "
            "Environment facts)."
        ),
        "environment": {
            "client_os": platform.platform(),
            "client_arch": platform.machine(),
            "client_python": sys.version.split()[0],
            "client_region_geography": os.environ.get(
                "GRACE2_CLIENT_REGION", "unknown"
            ),
            "deployed_region": "us-central1",
            "deployed_host": host,
            "qgis_server_image_pin": "@sha256:57d0f43 (post job-0029 CORS fix)",
        },
        "samples": {
            "n_total": N_SAMPLES,
            "n_successful_png_200": len(successful),
            "n_errors": len(errors),
            "errors": errors[:5],  # head only — keep the report bounded
            "first_call_cold": True,
        },
        "stats_ms": {
            "min": pmin,
            "max": pmax,
            "mean": pmean,
            "p50": p50,
            "p95": p95,
            "p99": p99,
        },
        "per_sample_ms": [
            {
                "index": s["index"],
                "ms": s.get("ms"),
                "status": s.get("status"),
                "cold_warm": s.get("cold_warm"),
                "bbox": s["bbox"],
                "bytes": s.get("bytes"),
            }
            for s in samples + errors
        ],
    }

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EVIDENCE_DIR / "wms_tile_latency.json"
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True))

    # --- Assertions --------------------------------------------------- //
    assert len(successful) >= max(1, N_SAMPLES - 2), (
        f"layer=QGIS Server | network: expected at least {N_SAMPLES - 2} "
        f"of {N_SAMPLES} GetMap calls to return 200/PNG against {host!r}, "
        f"observed {len(successful)} ({len(errors)} errors, "
        f"{len(samples) - len(successful)} non-200/non-PNG). Errors head: "
        f"{errors[:3]!r}. Report: {out_path!s}"
    )

    assert p50 is not None, (
        f"layer=QGIS Server | network: no successful samples to compute "
        f"p50 against {host!r}. Errors: {errors!r}. Report: {out_path!s}"
    )

    assert p50 < SOFT_TARGET_MS, (
        f"layer=QGIS Server | network (NFR-P-3 OQ-23E soft target): tile "
        f"latency p50 = {p50:.1f} ms exceeds the {SOFT_TARGET_MS:.0f} ms "
        f"soft target. Methodology limit: single-machine sync measurement "
        f"from an unknown-region client against us-central1 — the NFR "
        f"budget assumes a US-West-Coast client (per testing.md NFR "
        f"discipline). Honest status from this run is "
        f"`qualified` not `pass` even on green. Full report: "
        f"{out_path!s}. p95={p95}, max={pmax}, n_ok={len(successful)}."
    )
