"""M2 acceptance — QGIS Server WMS GetCapabilities + GetMap.

Sprint-04 EC1 + EC3 verification: the deployed QGIS Server Cloud Run
service (job-0018 + job-0024) answers ``GetCapabilities`` against the
canonical sample ``.qgs`` (job-0019) via the gen2 GCS volume mount
contract ``MAP=/mnt/qgs/grace2-sample.qgs`` and renders a PNG via
``GetMap``.

Every failure message attributes the failing layer per the testing.md
"diagnose before fix" discipline:

* HTTP failure → ``QGIS Server`` (Cloud Run service down/unreachable)
* ``<ServerException>`` body → ``QGIS Server`` config + ``.qgs`` content
* Missing layer name → ``.qgs`` content (engine — job-0019)
* PNG decode failure → ``QGIS Server`` rendering layer
"""

from __future__ import annotations

import io
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


WMS_NS = {"wms": "http://www.opengis.net/wms"}


def _http_get(url: str, timeout: float = 30.0) -> tuple[int, bytes, dict[str, str]]:
    """Plain stdlib HTTP GET. Returns ``(status, body_bytes, headers)``.

    Uses urllib so the M2 suite carries no extra dependencies beyond what
    the M1 venv already has. Headers are lower-cased.
    """
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, body, headers
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
        return exc.code, body, headers


def _build_wms_url(
    base_url: str,
    *,
    map_param: str,
    request: str,
    extra: dict[str, str] | None = None,
) -> str:
    """Build a WMS query URL against the deployed QGIS Server.

    The MAP value is intentionally inserted *unencoded* into the query
    string so the QGIS Server FCGI handler sees the canonical
    ``MAP=/mnt/qgs/...`` form (matching the WMS URL contract from
    job-0024).
    """
    params = {
        "MAP": map_param,
        "SERVICE": "WMS",
        "REQUEST": request,
    }
    if extra:
        params.update(extra)
    query = "&".join(
        f"{k}={urllib.parse.quote(str(v), safe=':/,')}" for k, v in params.items()
    )
    return f"{base_url}/ogc/?{query}"


# ---------------------------------------------------------------------------
# EC1 — GetCapabilities
# ---------------------------------------------------------------------------


@pytest.mark.live_qgis_server
def test_getcapabilities_returns_valid_xml(
    qgis_server_url: str,
    sample_qgs_uri: str,
    artifacts_dir: Path,
) -> None:
    """EC1 — GetCapabilities returns valid WMS_Capabilities XML naming
    the ``basemap-osm-conus`` layer (the canonical M2 sample, job-0019).
    """
    url = _build_wms_url(
        qgis_server_url,
        map_param=sample_qgs_uri,
        request="GetCapabilities",
    )
    status, body, headers = _http_get(url)

    assert status == 200, (
        f"layer=QGIS Server (Cloud Run): GetCapabilities expected HTTP 200, "
        f"got {status} from {url!r}; "
        f"body head: {body[:300]!r}"
    )

    # Persist the live transcript so the audit can re-inspect it.
    cap_path = artifacts_dir / "getcapabilities.xml"
    cap_path.write_bytes(body)

    text = body.decode("utf-8", errors="replace")
    assert "<ServerException" not in text, (
        f"layer=QGIS Server: response is a <ServerException> rather than "
        f"capabilities. Likely root cause: ``.qgs`` not reachable at "
        f"{sample_qgs_uri!r} (job-0024 GCS volume mount) or QGIS Server "
        f"image regression. Body head: {text[:600]!r}"
    )

    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise AssertionError(
            f"layer=QGIS Server: capabilities XML did not parse: {exc}. "
            f"Body head: {text[:400]!r}"
        ) from exc

    # Root element name should be WMS_Capabilities (1.3.0 default).
    local_name = root.tag.rsplit("}", 1)[-1]
    assert local_name == "WMS_Capabilities", (
        f"layer=QGIS Server: root element {local_name!r} expected "
        f"'WMS_Capabilities' (WMS 1.3.0). Likely ``.qgs`` load failure "
        f"upstream. Full root tag: {root.tag!r}"
    )

    # Find every <Name> element under any layer; the canonical basemap
    # layer name must be present.
    names = [el.text for el in root.iter("{http://www.opengis.net/wms}Name")]
    # Fallback: also match without namespace for resilience to future
    # WMS-version variations.
    if not names:
        names = [el.text for el in root.iter() if el.tag.endswith("}Name") or el.tag == "Name"]

    assert "basemap-osm-conus" in names, (
        f"layer=.qgs content (engine, job-0019): expected the canonical "
        f"basemap layer name 'basemap-osm-conus' in <Name> elements, "
        f"got {names!r}. Either the sample .qgs was rebuilt with a new "
        f"layer name, or the QGIS Server cannot read it from "
        f"{sample_qgs_uri!r}."
    )


# ---------------------------------------------------------------------------
# EC3 — GetMap returns a valid PNG
# ---------------------------------------------------------------------------


# PNG magic: 89 50 4E 47 0D 0A 1A 0A (the canonical 8-byte signature).
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.mark.live_qgis_server
def test_getmap_returns_png(
    qgis_server_url: str,
    sample_qgs_uri: str,
    artifacts_dir: Path,
) -> None:
    """EC3 — GetMap returns a valid PNG over the CONUS BBOX from the
    sample ``.qgs``'s ``basemap-osm-conus`` layer.

    Kickoff parameters: BBOX=24,-125,50,-66 + WIDTH=800 + HEIGHT=400
    + LAYERS=basemap-osm-conus + FORMAT=image/png. Magic bytes
    (``89 50 4E 47``) are asserted directly — no Pillow dependency.
    """
    url = _build_wms_url(
        qgis_server_url,
        map_param=sample_qgs_uri,
        request="GetMap",
        extra={
            "VERSION": "1.3.0",
            "LAYERS": "basemap-osm-conus",
            "CRS": "EPSG:4326",
            "BBOX": "24,-125,50,-66",
            "WIDTH": "800",
            "HEIGHT": "400",
            "FORMAT": "image/png",
            "STYLES": "",
        },
    )
    status, body, headers = _http_get(url, timeout=60.0)

    assert status == 200, (
        f"layer=QGIS Server (Cloud Run): GetMap expected HTTP 200, got "
        f"{status} from {url!r}; body head: {body[:300]!r}"
    )

    # Detect XML <ServerException> dressed up as 200 with image/png
    # content-type (QGIS Server sometimes returns 200 + XML body when
    # rendering fails internally).
    if body.lstrip().startswith(b"<"):
        raise AssertionError(
            f"layer=QGIS Server: GetMap returned an XML body rather than "
            f"a PNG (likely <ServerException>). Body head: "
            f"{body[:400]!r}"
        )

    assert body.startswith(PNG_MAGIC), (
        f"layer=QGIS Server: GetMap body does not start with the PNG "
        f"magic-bytes signature 89 50 4E 47 0D 0A 1A 0A — got "
        f"{body[:8]!r}. Likely cause: QGIS Server returned a different "
        f"image format (image/jpeg?) or a partial body. "
        f"Content-Type header: {headers.get('content-type')!r}."
    )

    assert len(body) > 1024, (
        f"layer=QGIS Server: GetMap PNG is suspiciously small "
        f"({len(body)} bytes; expected > 1KB for a non-blank CONUS "
        f"tile). Could indicate a blank/error tile."
    )

    # Persist the artifact for the audit and the M2 close package.
    png_path = artifacts_dir / "sample-getmap.png"
    png_path.write_bytes(body)
