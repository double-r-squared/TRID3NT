"""WMS-tile rendering tests against the deployed Cloud Run QGIS Server.

Exit-criterion mapping (sprint-05.md):

* EC1 ("Web client default basemap renders tiles from grace-2-qgis-server-…
  with zero gs:// fetches in browser network logs (FR-DT-5 / Invariant 5)").
* EC5 ("tests/m3/ pytest suite passes against the deployed QGIS Server
  substrate; tile-rendering test asserts at least one valid PNG response from
  /ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs").

Cross-browser scope (kickoff §4): this test is parametrized across Chromium
and Firefox — the two visual smokes whose Firefox-ESR coverage matters most
because the FR-WC-1 acceptance is cross-browser.

Failure-naming discipline (testing.md): every assertion attributes the
failing layer (web client / QGIS Server / network / dev-env).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest


# PNG magic bytes — 89 50 4E 47 0D 0A 1A 0A.
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.mark.live_web
@pytest.mark.live_qgis_wms_browser
def test_qgis_wms_tiles_render_in_browser(
    vite_dev_server: str,
    deployed_wms_origin: str,
    browser,  # parametrized across chromium + firefox
    browser_name: str,
    m3_artifacts_dir: Path,
) -> None:
    """Open the web client and assert that at least 5 successful WMS tile
    responses are observed from the deployed QGIS Server, all of content-type
    image/png, and that no CORS errors fire in the browser console.

    Anti-control: zero ``gs://`` fetches observed at any point — Invariant 5
    (Tier separation) end-to-end through the browser.
    """
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()

    wms_responses: list[dict] = []
    gs_requests: list[str] = []
    cors_console_errors: list[str] = []
    other_console_errors: list[str] = []

    def on_response(response) -> None:
        url = response.url
        if deployed_wms_origin in url:
            try:
                ctype = response.header_value("content-type") or ""
            except Exception:  # noqa: BLE001
                ctype = ""
            wms_responses.append(
                {
                    "status": response.status,
                    "url": url,
                    "content_type": ctype,
                }
            )
        if url.startswith("gs://") or "gs%3A%2F%2F" in url:
            gs_requests.append(url)

    def on_request(request) -> None:
        if request.url.startswith("gs://") or "gs%3A%2F%2F" in request.url:
            gs_requests.append(request.url)

    def on_console(msg) -> None:
        if msg.type == "error":
            text = msg.text or ""
            # Chromium emits CORS errors via the console as "Access to … has
            # been blocked by CORS policy". Firefox uses "Cross-Origin Request
            # Blocked".
            if (
                "CORS" in text
                or "Cross-Origin" in text
                or "cross-origin" in text
            ):
                cors_console_errors.append(text)
            else:
                other_console_errors.append(text)

    page.on("response", on_response)
    page.on("request", on_request)
    page.on("console", on_console)

    page.goto(vite_dev_server, wait_until="load", timeout=60000)

    # MapLibre fires tile fetches once the canvas is sized and the style
    # finishes loading. Wait for enough successful WMS responses to show
    # the basemap actually rendered — five tile requests is enough to
    # disambiguate "one stray probe" from "tile pyramid loading".
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        successful = [
            r
            for r in wms_responses
            if r["status"] == 200 and "image/png" in (r["content_type"] or "")
        ]
        if len(successful) >= 5:
            break
        page.wait_for_timeout(500)

    out_png = m3_artifacts_dir / f"wms-tiles-{browser_name}.png"
    page.screenshot(path=str(out_png), full_page=False)

    # --- Assertions ------------------------------------------------------ //

    assert (
        len(wms_responses) > 0
    ), (
        f"layer=web client (Map.tsx WMS tile fetches): observed zero requests "
        f"to {deployed_wms_origin} during the {browser_name} page load. "
        f"Either the Vite dev server served a stale build, or Map.tsx's "
        f"raster-source `tiles` template no longer points at the deployed "
        f"QGIS Server. Screenshot: {out_png!s}."
    )

    successful_png = [
        r
        for r in wms_responses
        if r["status"] == 200 and "image/png" in (r["content_type"] or "")
    ]
    assert len(successful_png) >= 5, (
        f"layer=QGIS Server (Cloud Run WMS) OR network: expected at least 5 "
        f"successful PNG tile responses from {deployed_wms_origin} in "
        f"{browser_name}, observed {len(successful_png)} (statuses: "
        f"{[r['status'] for r in wms_responses[:10]]!r}; "
        f"content-types: {[r['content_type'] for r in wms_responses[:10]]!r}). "
        f"If statuses are 403 / CORS-blocked the post-CORS-fix substrate "
        f"(job-0029, image @sha256:57d0f43) regressed."
    )

    assert (
        not cors_console_errors
    ), (
        f"layer=QGIS Server (CORS headers) OR web client (origin mismatch): "
        f"{browser_name} reported CORS errors during tile load — the "
        f"job-0029 fix may have regressed. First error: "
        f"{cors_console_errors[0]!r}"
    )

    assert (
        not gs_requests
    ), (
        f"layer=web client (Tier separation, Invariant 5, FR-DT-5): the "
        f"browser issued direct gs:// requests during {browser_name} load: "
        f"{gs_requests!r}. The client must never read GCS directly — "
        f"Tier B reaches the map only via QGIS Server."
    )

    context.close()
