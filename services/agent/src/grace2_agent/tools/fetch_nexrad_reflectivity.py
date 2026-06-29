"""``fetch_nexrad_reflectivity`` atomic tool — NEXRAD composite radar via Iowa State Mesonet WMS (job-0102).

NEXRAD composite radar reflectivity (and base reflectivity + VIL) served as a
public WMS by the Iowa State University Mesonet. The endpoint requires no auth.

Pattern: this is a **WMS-URL passthrough** tool — it composes a service URL the
client (MapLibre via QGIS Server cascade, or any WMS-aware viewer) renders
directly. The tool does **NOT** download or cache pixels because radar
reflectivity refreshes every ~5 minutes; caching a static PNG would mis-represent
the live storm. The cache shim is therefore deliberately bypassed.

URL composition (verified 2026-06-08):

    https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/{product}.cgi

    where ``{product}`` ∈ {``n0r``, ``n0q``, ``vil``}:
      - ``n0r``: composite reflectivity (the all-tilt max; default product)
      - ``n0q``: base reflectivity (lowest tilt, ~0.5° elevation)
      - ``vil``: vertically integrated liquid (precip-totaling diagnostic)

When ``bbox`` is supplied, it is encoded as the ``BBOX=`` WMS parameter
(``min_lon,min_lat,max_lon,max_lat`` in EPSG:4326 / WMS 1.3.0 CRS:84) so a
client GetMap call already scopes geographically. When ``bbox`` is None the
LayerURI carries no bbox and the client will request CONUS extent.

FR-TA-2: atomic tool returning a ``LayerURI``.
FR-DC-6: uncacheable-by-construction (WMS URL passthrough; pixels are dynamic
and live-no-cache classed).

OQ-0102-METADATA-FIELDS: the Wave 1.5 kickoff sketches new
``AtomicToolMetadata`` fields (``supports_global_query``, ``estimate_payload_mb``)
that the current ``packages/contracts`` model does not yet expose. Engine job
scope cannot land schema fields; surfacing as OQ for an upstream schema
amendment. The tool meanwhile documents the intended values in this docstring
so a follow-up registration update is mechanical.

OQ-0102-CACHEABLE-FLAG-CONTRADICTION: the kickoff sketch sets
``cacheable=True, ttl_class='live-no-cache'`` which the existing
``AtomicToolMetadata`` model_validator rejects (``cacheable=True`` is
inconsistent with the live-no-cache class). The kickoff body text is explicit
that pixels are not cached ("does NOT cache pixels (the WMS is dynamic)"), so
we follow the body's clear intent and register ``cacheable=False``. The Wave
1.5 metadata-evolution OQ above will likely resolve this asymmetry.
"""

from __future__ import annotations

import logging
import math
from typing import Literal, Any
from urllib.parse import urlencode

from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool

__all__ = ["fetch_nexrad_reflectivity"]

logger = logging.getLogger("grace2_agent.tools.fetch_nexrad_reflectivity")


# ---------------------------------------------------------------------------
# Error types (FR-AS-11 typed-error surface).
# ---------------------------------------------------------------------------


class NexradError(RuntimeError):
    """Base class for fetch_nexrad_reflectivity failures."""

    error_code: str = "NEXRAD_ERROR"
    retryable: bool = False


class NexradProductError(NexradError):
    """Unknown product was requested."""

    error_code = "NEXRAD_PRODUCT_INVALID"
    retryable = False


class NexradBboxError(NexradError):
    """Bbox is malformed (non-finite, out-of-range, or degenerate)."""

    error_code = "NEXRAD_BBOX_INVALID"
    retryable = False


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

# Iowa State University Mesonet NEXRAD WMS service base.
# Verified 2026-06-08: per-product endpoints at .../wms/nexrad/{product}.cgi.
_NEXRAD_WMS_BASE = "https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad"

_VALID_PRODUCTS = frozenset({"n0r", "n0q", "vil"})

_PRODUCT_DESCRIPTIONS: dict[str, str] = {
    "n0r": "composite reflectivity (all-tilt max, dBZ)",
    "n0q": "base reflectivity tilt 0.5° (dBZ)",
    "vil": "vertically integrated liquid (kg/m²)",
}

_PRODUCT_LAYER_NAME: dict[str, str] = {
    "n0r": "NEXRAD Composite Reflectivity",
    "n0q": "NEXRAD Base Reflectivity (0.5°)",
    "vil": "NEXRAD Vertically Integrated Liquid",
}

# Iowa Mesonet docs note ``nexrad-n0r-wmst`` and product-specific layer names
# served on each cgi endpoint. We use the canonical WMS LAYERS= value per
# product so MapLibre / QGIS Server cascade can request the right product.
_PRODUCT_WMS_LAYER: dict[str, str] = {
    "n0r": "nexrad-n0r-wmst",
    "n0q": "nexrad-n0q-wmst",
    "vil": "nexrad-vil-wmst",
}


# ---------------------------------------------------------------------------
# AtomicToolMetadata — registered once at import time.
#
# See module docstring OQ-0102-CACHEABLE-FLAG-CONTRADICTION for the kickoff
# vs validator reconciliation. Body-text intent is "does NOT cache pixels",
# so cacheable=False.
# ---------------------------------------------------------------------------

_METADATA = AtomicToolMetadata(
    name="fetch_nexrad_reflectivity",
    ttl_class="live-no-cache",
    source_class=None,
    cacheable=False,
    # OQ-0102-METADATA-FIELDS resolved: the schema model now exposes this flag,
    # so the long-parked intent is folded into the live metadata. bbox=None
    # returns the CONUS-wide WMS GetMap URL; this tool transfers only a service
    # URL (~0.1MB), never pixels, so a no-bbox global query is bounded + safe.
    supports_global_query=True,
)


# ---------------------------------------------------------------------------
# bbox helpers (identical-spirit to fetch_administrative_boundaries; copied to
# keep tools modular — there is no shared bbox utility module yet).
# ---------------------------------------------------------------------------


def _validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    """Raise ``NexradBboxError`` if bbox is invalid."""
    if len(bbox) != 4:
        raise NexradBboxError(
            f"bbox must be (min_lon, min_lat, max_lon, max_lat); got {bbox!r}"
        )
    min_lon, min_lat, max_lon, max_lat = bbox
    if not all(math.isfinite(v) for v in bbox):
        raise NexradBboxError(f"bbox contains non-finite values: {bbox!r}")
    if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0):
        raise NexradBboxError(f"bbox lon out of [-180,180]: {bbox!r}")
    if not (-90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0):
        raise NexradBboxError(f"bbox lat out of [-90,90]: {bbox!r}")
    if min_lon >= max_lon or min_lat >= max_lat:
        raise NexradBboxError(
            f"bbox is degenerate (min must be < max on both axes): {bbox!r}"
        )


# ---------------------------------------------------------------------------
# WMS URL builder.
# ---------------------------------------------------------------------------


def _build_wms_url(
    product: str,
    bbox: tuple[float, float, float, float] | None,
) -> str:
    """Compose the Iowa Mesonet WMS service URL for ``product`` (+ optional bbox).

    Returns the full URL the client GetMap call will hit. The WMS service
    itself accepts standard parameters (``SERVICE=WMS``, ``REQUEST=GetMap``,
    ``LAYERS=``, ``BBOX=``, ``WIDTH=``, ``HEIGHT=``, ``CRS=``, ``FORMAT=``,
    ``TIME=``); we include the BBOX as a query-string hint when the caller
    scoped it geographically, so a downstream renderer that just appends
    standard GetMap params produces a correctly-scoped image.

    The output URL is the LayerURI.uri value; web/QGIS Server cascade reads
    it as a base and tacks on per-tile params.
    """
    if product not in _VALID_PRODUCTS:
        raise NexradProductError(
            f"unknown product={product!r}; allowed: {sorted(_VALID_PRODUCTS)}"
        )
    base = f"{_NEXRAD_WMS_BASE}/{product}.cgi"
    if bbox is None:
        # CONUS default; the LayerURI carries no bbox hint.
        return base

    # Encode BBOX as a service-default hint so URL inspection shows the scope.
    # WMS 1.3.0 axis order is lat,lon for some CRS; we use the WMS-1.1.1 lon,lat
    # order for the BBOX param (LonLat) since CRS:84 / EPSG:4326 long-axis-first
    # is the convention Iowa Mesonet documents for their NEXRAD WMS.
    bbox_str = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
    qs = urlencode({"BBOX": bbox_str})
    return f"{base}?{qs}"


# ---------------------------------------------------------------------------
# Registered atomic tool.
# ---------------------------------------------------------------------------


@register_tool(
    _METADATA,
    # Annotations: readOnlyHint=True (read-only; no state mutation),
    # openWorldHint=True (calls external public API endpoint),
    # destructiveHint=False, idempotentHint=True (cache shim deduplicates).
    open_world_hint=True,
)
def fetch_nexrad_reflectivity(
    bbox: tuple[float, float, float, float] | None = None,
    product: Literal["n0r", "n0q", "vil"] = "n0r",
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> LayerURI:
    """Live NEXRAD composite radar reflectivity as a WMS overlay URL (Iowa Mesonet).

    Composes and returns a ``LayerURI`` pointing at the Iowa State University
    Mesonet NEXRAD WMS -- a URL passthrough the client renders directly. Does
    NOT download or cache pixels: radar refreshes every ~5 min, so a cached PNG
    would misrepresent the live storm. CONUS only, Tier-1 free.

    Use this when:
    - Live storm-context display ("show current radar near Tampa", "overlay
      radar on the flood map").
    - Situational-awareness overlay beside ``fetch_nws_alerts_conus`` /
      ``fetch_goes_satellite``.
    - Hail / heavy-precip risk via ``product="vil"`` (vertically integrated
      liquid).

    Do NOT use this for:
    - Precipitation amount -- use ``fetch_mrms_qpe`` (gauge-corrected mm
      accumulation); reflectivity is dBZ, not rainfall.
    - Historical radar replay -- this WMS serves the current mosaic only.
    - Downloading a raster file for analysis -- this emits a WMS URL, not pixels.
    - Non-CONUS coverage -- NEXRAD is the US national radar network.

    Returns a raster LayerURI (role="context") that auto-renders the live WMS --
    do not call publish_layer. Uncacheable by construction (live-no-cache).

    Parameters:
    - ``bbox``: optional ``(min_lon, min_lat, max_lon, max_lat)`` EPSG:4326;
      None returns the CONUS-wide WMS URL.
    - ``product``: ``"n0r"`` composite reflectivity dBZ (default), ``"n0q"``
      base 0.5deg reflectivity dBZ, ``"vil"`` vertically integrated liquid
      (kg/m^2).

    Raises ``NexradProductError`` (unknown product) / ``NexradBboxError``
    (malformed bbox). units="dBZ" for n0r/n0q, "kg/m^2" for vil.
    """
    # Defensive validations on the registered surface (kickoff acceptance
    # criteria call for typed errors on unknown product / bad bbox).
    if product not in _VALID_PRODUCTS:
        raise NexradProductError(
            f"unknown product={product!r}; allowed: {sorted(_VALID_PRODUCTS)}"
        )
    if bbox is not None:
        _validate_bbox(bbox)

    url = _build_wms_url(product, bbox)
    logger.info(
        "fetch_nexrad_reflectivity: product=%s bbox=%s url=%s",
        product,
        bbox,
        url,
    )

    # layer_id encodes product + bbox-or-conus so multiple panels can carry
    # distinct LayerURI instances without colliding on the client.
    if bbox is None:
        layer_id = f"nexrad-{product}-conus"
    else:
        layer_id = (
            f"nexrad-{product}-{bbox[0]:.4f}-{bbox[1]:.4f}-"
            f"{bbox[2]:.4f}-{bbox[3]:.4f}"
        )

    name = f"{_PRODUCT_LAYER_NAME[product]} (Iowa State Mesonet)"

    return LayerURI(
        layer_id=layer_id,
        name=name,
        layer_type="raster",
        uri=url,
        style_preset=f"nexrad_{product}",
        role="context",
        units=("dBZ" if product in ("n0r", "n0q") else "kg/m^2"),
        bbox=bbox,
    )
