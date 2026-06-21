"""Case-EXPORT Lambda for GRACE-2 "download a whole Case as a QGIS-ready zip".

Fronted by the EXISTING wake API Gateway HTTP API (a fourth route,
``GET /case-export-url``, alongside ``POST /wake``, ``GET /case-view-url`` and
``GET /case-list``). A signed-in OWNER calls it to download the WHOLE Case as a
single zip: named per-layer folders of the Case rasters (GeoTIFF/COG ``.tif``)
and vectors (``.geojson``), PLUS a ready-to-open STYLED QGIS ``.qgs`` project
that references those co-zipped files by RELATIVE path. The zip is uploaded to
an ``exports/`` prefix of the durable runs bucket and returned as a pre-signed
GET URL in one synchronous request.

AUTH CONTRACT (the only thing this Lambda decides):
  - SIGNED-IN + OWNER-SCOPED. A valid ``Authorization: Bearer <Cognito ID
    token>`` that verifies (RS256/JWKS by kid, iss = the pool issuer, aud = the
    app client id, token_use == "id", exp valid) yields the verified ``uid``.
    The Case's ``owner_user_id`` / ``user_id`` MUST equal that uid, else HTTP
    **403** (hard owner-mismatch). NO TOKEN / INVALID TOKEN / NO POOL -> HTTP
    **401** (an export is a privileged data egress; unlike the cold-open list it
    is NEVER served anonymously).

KEY DATA-MODEL FACT (read before modifying): a Case's data is NOT under a
per-case S3 prefix; it is CONTENT-ADDRESSED in the cache bucket. The
authoritative layer list is the DynamoDB cases doc ``loaded_layer_summaries``
array (each = ``ProjectLayerSummary``: ``layer_id``, ``name``, ``layer_type``,
``uri``, ``style_preset``). For each layer we derive the s3:// object key from
``uri``:

  - a TiTiler tile template (contains ``/cog/tiles/`` and a ``?url=`` query) ->
    URL-decode the ``?url=`` param to recover the underlying s3:// COG;
  - a bare ``s3://`` uri -> use it directly;
  - a vector with NO standalone object (inline GeoJSON only) -> pull the inline
    GeoJSON from the case-view snapshot
    ``s3://<runs-bucket>/case-views/{case_id}.json`` (the agent materializes
    inline vector GeoJSON onto ``loaded_layers[*].inline_geojson`` there).

The QGIS ``.qgs`` is plain XML (NO PyQGIS in the Lambda): rasters are added via
the gdal provider as ``rasterlayer`` and STYLED as a ``singlebandpseudocolor``
renderer from a ``style_preset -> (rescale_min, rescale_max, colormap)`` table
copied VERBATIM from
``services/agent/src/grace2_agent/tools/publish_layer.py``
``_resolve_titiler_style_params`` (the Lambda cannot import the agent module).
Vectors are added via the ogr provider as a GeoJSON ``vectorlayer``.

The Lambda's IAM role can ``dynamodb:GetItem`` on the cases table ARN,
``s3:GetObject`` on the cache + runs buckets, and ``s3:PutObject`` on the runs
bucket's ``exports/*`` prefix -- nothing else (no list, no other table, no EC2).

Cognito verification is ported VERBATIM from the case-list / view-signer / wake
handlers (``cognito_verify`` + the JWKS helpers) so all copies stay
byte-compatible. The agent module is NOT importable from the Lambda (different
deploy unit), so the logic is duplicated here deliberately; keep the copies in
sync.

Deps beyond the Lambda runtime's boto3: PyJWT[crypto] (RS256 verify) + requests
(JWKS fetch). They are pip-installed into this directory at package time by the
OpenTofu ``null_resource`` + ``archive_file`` in main.tf (mirrors case_list).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.parse
import xml.sax.saxutils as _sax
import zipfile
from decimal import Decimal
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# --------------------------------------------------------------------------- #
# Config (env-driven; read at module load -- Lambda env is fixed per deploy).
# --------------------------------------------------------------------------- #

REGION = os.environ.get("AWS_REGION", "us-west-2")

#: DynamoDB cases table (PK ``_id``). Mirrors GRACE2_DYNAMO_TABLE_PREFIX +
#: "cases"; the live default is grace2_cases. UNSET/empty -> 503 (the export
#: path is not wired in this deployment).
CASES_TABLE = os.environ.get("CASES_TABLE", "grace2_cases").strip()

#: DynamoDB users table (PK ``_id`` = the INTERNAL ULID; GSI
#: ``firebase_uid-index`` keyed by the Cognito subject). Decision 10: the Case
#: doc's owner (``owner_user_id`` / ``user_id``) is the internal ULID, NOT the
#: Cognito sub ``cognito_verify`` yields. The sub MUST be resolved to the
#: internal ULID before the owner check -- otherwise the TRUE owner (sub != ULID)
#: is wrongly 403'd. UNSET/empty -> resolution short-circuits to None (no
#: internal id -> 403 fail-closed; never a 500).
USERS_TABLE = os.environ.get("USERS_TABLE", "grace2_users").strip()

#: GSI on the users table mapping a Cognito sub (``firebase_uid``) -> the user
#: doc whose ``_id`` is the internal ULID. Mirrors dynamo_backend._TABLE_GSIS.
FIREBASE_UID_INDEX = "firebase_uid-index"

#: Content-addressed cache bucket holding the Case COGs (the s3:// objects the
#: layer URIs / TiTiler ?url= params point at). GetObject only.
CACHE_BUCKET = os.environ.get("CACHE_BUCKET", "").strip()

#: Durable runs bucket: holds the case-view snapshots under case-views/ (read,
#: for inline vector GeoJSON) AND receives the export zip under exports/ (write).
RUNS_BUCKET = os.environ.get("RUNS_BUCKET", "").strip()

#: Prefix under the runs bucket the export zip is written to (default exports).
EXPORTS_PREFIX = (os.environ.get("EXPORTS_PREFIX", "exports").strip().strip("/")) or "exports"

#: Pre-signed GET URL expiry (seconds) for the export zip. Default 1h.
EXPORT_SIGNED_TTL_S = int(os.environ.get("EXPORT_SIGNED_TTL_S", "3600"))

#: The case-view snapshot prefix (mirrors persistence.CASE_VIEWS_PREFIX).
VIEW_PREFIX = "case-views"

# Cognito -- mirrors auth_handshake.py / view_sign / case_list / wake env names.
COGNITO_POOL_ENV = "GRACE2_COGNITO_USER_POOL_ID"
COGNITO_CLIENT_ENV = "GRACE2_COGNITO_CLIENT_ID"

#: Clock-skew leeway (seconds) for exp validation. Matches the other handlers.
_JWT_LEEWAY_S = 60
#: HTTPS timeout (seconds) for the public JWKS fetch.
_JWKS_FETCH_TIMEOUT_S = 5.0

#: Lambda ephemeral storage scratch dir (configured to 2048 MiB in main.tf).
_TMP_DIR = "/tmp"

_ddb = boto3.resource(
    "dynamodb",
    region_name=REGION,
    config=BotoConfig(
        connect_timeout=3,
        read_timeout=10,
        retries={"max_attempts": 2, "mode": "standard"},
    ),
)

# Force SigV4 + the regional endpoint so the pre-signed URL is valid in
# us-west-2 (S3 SigV2 pre-signed URLs are deprecated; some regions reject them).
_s3 = boto3.client(
    "s3",
    region_name=REGION,
    config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "virtual"}),
)

_CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    # Each call mints a fresh zip + time-boxed URL; never cache the response.
    "Cache-Control": "no-store",
}


def _response(status: int, body: dict) -> dict:
    """Build an API Gateway (payload format 2.0) proxy response."""
    return {
        "statusCode": status,
        "headers": _CORS_HEADERS,
        "body": json.dumps(body, separators=(",", ":")),
    }


# --------------------------------------------------------------------------- #
# Cognito verification (ported VERBATIM from case_list/view_sign -- keep in sync).
# --------------------------------------------------------------------------- #

_jwks_cache: dict[str, dict[str, dict[str, Any]]] = {}
_jwks_lock = threading.Lock()


def _cognito_region() -> str:
    return (
        os.environ.get("GRACE2_AWS_REGION")
        or os.environ.get("AWS_REGION")
        or "us-west-2"
    )


def _cognito_issuer(region: str, pool_id: str) -> str:
    return f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"


def _fetch_jwks(issuer: str) -> dict[str, dict[str, Any]]:
    import requests  # packaged dep

    url = f"{issuer}/.well-known/jwks.json"
    resp = requests.get(url, timeout=_JWKS_FETCH_TIMEOUT_S)
    resp.raise_for_status()
    keys = resp.json().get("keys", [])
    return {k["kid"]: k for k in keys if "kid" in k}


def _get_jwk(issuer: str, kid: str, *, allow_refetch: bool = True) -> dict[str, Any] | None:
    with _jwks_lock:
        cached = _jwks_cache.get(issuer)
    if cached is not None and kid in cached:
        return cached[kid]
    if not allow_refetch:
        return cached.get(kid) if cached else None
    try:
        fresh = _fetch_jwks(issuer)
    except Exception as exc:  # noqa: BLE001 -- network/parse failure is normal
        logger.info("JWKS fetch failed for %s: %s", issuer, type(exc).__name__)
        return cached.get(kid) if cached else None
    with _jwks_lock:
        _jwks_cache[issuer] = fresh
    return fresh.get(kid)


def cognito_verify(token: str) -> dict[str, Any] | None:
    """Verify a Cognito ID token. Returns claims dict on success, None on any
    failure (invalid/expired/wrong-aud, or no pool configured)."""
    pool_id = os.environ.get(COGNITO_POOL_ENV, "").strip()
    if not pool_id:
        # No pool configured -> cannot authenticate -> None (export -> 401).
        return None
    client_id = os.environ.get(COGNITO_CLIENT_ENV, "").strip()
    region = _cognito_region()
    issuer = _cognito_issuer(region, pool_id)

    try:
        import jwt  # PyJWT[crypto] -- packaged dep
        from jwt.algorithms import RSAAlgorithm

        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            logger.info("Cognito verify: token header missing 'kid'")
            return None
        jwk = _get_jwk(issuer, kid)
        if jwk is None:
            logger.info("Cognito verify: no JWK for kid=%s", kid)
            return None

        public_key = RSAAlgorithm.from_jwk(jwk)

        decode_kwargs: dict[str, Any] = dict(
            algorithms=["RS256"],
            issuer=issuer,
            leeway=_JWT_LEEWAY_S,
            options={
                "require": ["exp", "iss", "sub"],
                "verify_aud": False,  # validated explicitly below
            },
        )
        claims = jwt.decode(token, public_key, **decode_kwargs)
    except Exception as exc:  # noqa: BLE001 -- verification failure is normal
        logger.info("Cognito verify failed: %s", type(exc).__name__)
        return None

    if claims.get("token_use") != "id":
        logger.info("Cognito verify: token_use=%r (expected 'id')", claims.get("token_use"))
        return None

    if not client_id or claims.get("aud") != client_id:
        logger.info("Cognito verify: aud mismatch")
        return None

    sub = claims.get("sub")
    if not sub:
        logger.info("Cognito verify: claims missing 'sub'")
        return None

    return {
        "uid": sub,
        "email": claims.get("email"),
        "name": claims.get("name") or claims.get("cognito:username"),
        "tier": claims.get("custom:tier", "free"),
    }


# --------------------------------------------------------------------------- #
# Request helpers.
# --------------------------------------------------------------------------- #


def _extract_bearer(event: dict) -> str | None:
    """Pull the bearer token from the Authorization header (case-insensitive).

    API Gateway payload 2.0 lower-cases header keys, but be defensive.
    """
    headers = event.get("headers") or {}
    raw = None
    for k, v in headers.items():
        if k.lower() == "authorization":
            raw = v
            break
    if not raw:
        return None
    parts = raw.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    # Tolerate a bare token (no scheme).
    return raw.strip() or None


def _query_case_id(event: dict) -> str | None:
    qs = event.get("queryStringParameters") or {}
    cid = (qs.get("case_id") or "").strip()
    return cid or None


def _from_ddb(value: Any) -> Any:
    """Recursively coerce a DynamoDB-resource value back to JSON-shaped form.

    Ported from dynamo_backend._from_ddb / case_list: integral Decimal -> int,
    fractional Decimal -> float, Set -> list, recursing dict/list. The boto3
    resource API returns numbers as ``Decimal`` (not JSON-serializable), so this
    MUST run on the GetItem result before use.
    """
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {k: _from_ddb(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_from_ddb(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return [_from_ddb(v) for v in value]
    return value


# --------------------------------------------------------------------------- #
# Cognito sub -> internal ULID resolution (Decision 10).
#
# Mirrors ``Persistence.get_user_by_firebase_uid``: query the users table's
# ``firebase_uid-index`` GSI for ``firebase_uid == sub``; the matched item's
# ``_id`` is the internal ULID the Case doc is owned by. FAIL CLOSED: any error,
# no users table, or no record -> None (the owner check then can't match -> 403;
# never a 500, never an implicit allow).
# --------------------------------------------------------------------------- #

#: Tiny in-process cache (sub -> ULID); warm Lambda contexts reuse it.
_uid_cache: dict[str, str] = {}
_uid_cache_lock = threading.Lock()


def _resolve_internal_uid(sub: str) -> str | None:
    """Resolve a Cognito sub to the internal ULID via the users-table GSI.

    Returns the internal ULID (users._id) on a hit, else None. NEVER raises:
    a missing users table, a GSI error, or no matching record all fail closed
    to None so the owner check denies (403) rather than 500-ing.
    """
    if not sub:
        return None
    with _uid_cache_lock:
        cached = _uid_cache.get(sub)
    if cached is not None:
        return cached
    if not USERS_TABLE:
        return None

    from boto3.dynamodb.conditions import Key

    try:
        table = _ddb.Table(USERS_TABLE)
        resp = table.query(
            IndexName=FIREBASE_UID_INDEX,
            KeyConditionExpression=Key("firebase_uid").eq(sub),
            Limit=1,
        )
        items = resp.get("Items") or []
    except Exception as exc:  # noqa: BLE001 -- fail closed to None
        logger.info(
            "users-table resolve failed for sub (%s); treating as no internal id",
            type(exc).__name__,
        )
        return None
    if not items:
        return None
    internal = items[0].get("_id")
    if not internal or not isinstance(internal, str):
        return None
    with _uid_cache_lock:
        _uid_cache[sub] = internal
    return internal


# --------------------------------------------------------------------------- #
# Style table -- COPIED VERBATIM from publish_layer._resolve_titiler_style_params
# (_TITILER_STYLE_REGISTRY + the family substring/prefix rules). The Lambda
# cannot import the agent module, so the preset -> (rescale_min, rescale_max,
# colormap) mapping is replicated here. KEEP IN SYNC with publish_layer.py.
# --------------------------------------------------------------------------- #

#: Exact preset / variable key -> (rescale "lo,hi", colormap_name). Replicated
#: byte-for-byte from publish_layer._TITILER_STYLE_REGISTRY.
_TITILER_STYLE_REGISTRY: dict[str, tuple[str, str]] = {
    # Hydrology.
    "continuous_flood_depth": ("0,3", "ylgnbu"),
    "continuous_plume_concentration": ("0,10", "reds"),
    # Precipitation (mm).
    "precipitation_mm": ("0,100", "blues"),
    "gridmet_pr": ("0,100", "blues"),
    "era5_total_precipitation": ("0,100", "blues"),
    # Temperature (Kelvin).
    "hrrr_2m_temperature": ("250,320", "rdylbu_r"),
    "gridmet_tmmx": ("250,320", "rdylbu_r"),
    "gridmet_tmmn": ("250,320", "rdylbu_r"),
    "era5_2m_temperature": ("250,320", "rdylbu_r"),
    # Wind speed (m/s).
    "wind_speed": ("0,25", "viridis"),
    "hrrr_10m_wind_speed": ("0,25", "viridis"),
    "gridmet_vs": ("0,25", "viridis"),
    # Signed wind components (m/s) -- diverging ramp centered on 0.
    "hrrr_10m_u_wind": ("-25,25", "rdbu"),
    "hrrr_10m_v_wind": ("-25,25", "rdbu"),
    "era5_10m_u_wind": ("-25,25", "rdbu"),
    "era5_10m_v_wind": ("-25,25", "rdbu"),
    # Drought + fuel moisture.
    "gridmet_pdsi": ("-6,6", "rdbu"),
    "gridmet_fm100": ("0,40", "ylgn"),
    "gridmet_fm1000": ("0,40", "ylgn"),
    # GOES satellite.
    "goes_visible": ("0,1", "gray"),
    "goes_ir": ("180,330", "gray_r"),
    "goes_wv": ("180,330", "gray_r"),
}

#: Terrain-family tokens (replicated from publish_layer._TERRAIN_STYLE_TOKENS).
#: A terrain-token preset/URI gets QGIS DEFAULT rendering (no pseudocolor ramp),
#: exactly as the live TiTiler path leaves style_params empty for them.
_TERRAIN_STYLE_TOKENS = frozenset(
    {"dem", "relief", "hillshade", "slope", "aspect", "terrain", "elevation"}
)

#: Safe non-empty default (mirrors publish_layer._TITILER_SAFE_DEFAULT:
#: rescale 0,1 + viridis) so a continuous raster with an unknown preset still
#: gets a real ramp rather than a flat single value.
_SAFE_DEFAULT_RESCALE = (0.0, 1.0)
_SAFE_DEFAULT_COLORMAP = "viridis"


def _is_terrain_token_preset(style_preset: str | None, layer_uri: str) -> bool:
    """True if the preset / URI tokenizes to a TERRAIN-family token.

    Replicates publish_layer._is_terrain_token_preset: tokenize the preset AND
    the uri on non-alphanumerics, match whole tokens against
    ``_TERRAIN_STYLE_TOKENS`` (so ``continuous_dem`` -> {continuous, dem} ->
    matches, but ``demo-flood`` does NOT match ``dem``).
    """
    tokens = set(
        re.split(r"[^a-z0-9]+", f"{style_preset or ''} {layer_uri or ''}".lower())
    )
    return bool(tokens & _TERRAIN_STYLE_TOKENS)


def _registry_lookup(preset: str) -> tuple[str, str] | None:
    """Return (rescale "lo,hi", colormap) for a known preset, else None.

    Replicates publish_layer._registry_style_params resolution order: exact key,
    then ``smoke`` -> None (generic), then family substring/prefix rules, then
    the guarded precipitation prefix.
    """
    key = (preset or "").lower()
    if not key:
        return None
    hit = _TITILER_STYLE_REGISTRY.get(key)
    if hit is not None:
        return hit
    if "smoke" in key:
        return None
    family_rules: tuple[tuple[str, tuple[str, str]], ...] = (
        ("u_wind", ("-25,25", "rdbu")),
        ("v_wind", ("-25,25", "rdbu")),
        ("wind_speed", ("0,25", "viridis")),
        ("temperature", ("250,320", "rdylbu_r")),
        ("pdsi", ("-6,6", "rdbu")),
        ("fm100", ("0,40", "ylgn")),
        ("fm1000", ("0,40", "ylgn")),
    )
    for needle, val in family_rules:
        if needle in key:
            return val
    if (
        key.endswith("_precip")
        or key.endswith("_precipitation")
        or key.endswith("precipitation_mm")
        or key.endswith("_pr")
        or "_precipitation_" in key
    ):
        return ("0,100", "blues")
    return None


def _resolve_style(style_preset: str | None, layer_uri: str) -> tuple[float, float, str] | None:
    """Resolve (rescale_min, rescale_max, colormap) for a raster's preset.

    Mirrors publish_layer._resolve_titiler_style_params at the table level (no
    COG byte-probing in the Lambda):

      - terrain-token preset/URI -> None (QGIS default rendering, no ramp);
      - typed registry (exact + family) -> the registry (lo, hi, colormap);
      - any other preset -> the safe default (0,1 viridis).

    Returns ``None`` for the terrain-default case (the caller writes a plain
    rasterlayer with no pseudocolor renderer); otherwise a 3-tuple.
    """
    if _is_terrain_token_preset(style_preset, layer_uri):
        return None
    hit = _registry_lookup(style_preset or "")
    if hit is not None:
        rescale, cmap = hit
        lo_s, hi_s = rescale.split(",", 1)
        return (float(lo_s), float(hi_s), cmap)
    return (_SAFE_DEFAULT_RESCALE[0], _SAFE_DEFAULT_RESCALE[1], _SAFE_DEFAULT_COLORMAP)


# --------------------------------------------------------------------------- #
# rio-tiler / matplotlib colormap -> QGIS color-ramp anchor stops.
#
# The .qgs singlebandpseudocolor renderer needs explicit (value, r,g,b) stops.
# These 5-stop ramps approximate the named rio-tiler colormaps used by the live
# TiTiler path so the exported QGIS project looks like the in-app render. Stops
# are at 0/25/50/75/100% of the value range. Anchor RGBs are sampled from the
# canonical matplotlib/ColorBrewer ramps.
# --------------------------------------------------------------------------- #

#: 5 RGB anchors (0,0.25,0.5,0.75,1.0 of the range) per colormap.
_COLORMAP_ANCHORS: dict[str, tuple[tuple[int, int, int], ...]] = {
    "ylgnbu": ((255, 255, 217), (161, 218, 180), (65, 182, 196), (34, 94, 168), (8, 29, 88)),
    "reds": ((255, 245, 240), (252, 187, 161), (251, 106, 74), (203, 24, 29), (103, 0, 13)),
    "blues": ((247, 251, 255), (198, 219, 239), (107, 174, 214), (33, 113, 181), (8, 48, 107)),
    "viridis": ((68, 1, 84), (59, 82, 139), (33, 145, 140), (94, 201, 98), (253, 231, 37)),
    "rdylbu_r": ((49, 54, 149), (116, 173, 209), (255, 255, 191), (244, 109, 67), (165, 0, 38)),
    "rdbu": ((103, 0, 31), (214, 96, 77), (247, 247, 247), (67, 147, 195), (5, 48, 97)),
    "ylgn": ((255, 255, 229), (194, 230, 153), (120, 198, 121), (49, 163, 84), (0, 90, 50)),
    "ylorrd": ((255, 255, 178), (254, 204, 92), (253, 141, 60), (240, 59, 32), (189, 0, 38)),
    "gray": ((0, 0, 0), (64, 64, 64), (128, 128, 128), (191, 191, 191), (255, 255, 255)),
    "gray_r": ((255, 255, 255), (191, 191, 191), (128, 128, 128), (64, 64, 64), (0, 0, 0)),
}


def _ramp_stops(lo: float, hi: float, colormap: str) -> list[tuple[float, int, int, int]]:
    """Build (value, r, g, b) stops for a singlebandpseudocolor ramp.

    5 evenly-spaced stops across [lo, hi] using the named colormap's anchors
    (falls back to viridis if the name is unknown). A degenerate lo==hi range is
    widened so the renderer has a non-zero interval.
    """
    anchors = _COLORMAP_ANCHORS.get(colormap, _COLORMAP_ANCHORS["viridis"])
    if hi <= lo:
        hi = lo + 1.0
    n = len(anchors)
    stops: list[tuple[float, int, int, int]] = []
    for i, (r, g, b) in enumerate(anchors):
        frac = i / (n - 1)
        value = lo + frac * (hi - lo)
        stops.append((value, r, g, b))
    return stops


# --------------------------------------------------------------------------- #
# Layer-name sanitization + URI -> s3 key recovery.
# --------------------------------------------------------------------------- #

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_folder(name: str | None, fallback: str) -> str:
    """A filesystem/zip-safe folder name from a layer name (or layer_id).

    Collapses unsafe chars to ``_``, trims, caps length. Empty -> the fallback
    (the layer_id). Never returns ``.``/``..``/empty.
    """
    base = (name or "").strip()
    if not base:
        base = fallback
    safe = _SAFE_NAME_RE.sub("_", base).strip("._")
    if not safe:
        safe = _SAFE_NAME_RE.sub("_", fallback).strip("._") or "layer"
    return safe[:80]


def _parse_s3_uri(uri: str) -> tuple[str, str] | None:
    """Split an ``s3://bucket/key`` URI into (bucket, key); None if not s3://."""
    if not uri.startswith("s3://"):
        return None
    rest = uri[len("s3://"):]
    if "/" not in rest:
        return None
    bucket, key = rest.split("/", 1)
    if not bucket or not key:
        return None
    return (bucket, key)


def _recover_s3_uri(uri: str) -> str | None:
    """Recover the underlying s3:// COG URI from a layer ``uri``.

    Cases (per the data-model fact):
      - already ``s3://...`` -> returned as-is;
      - a TiTiler tile template (``/cog/tiles/`` + ``?url=`` query) ->
        URL-decode the ``?url=`` param (it holds the percent-encoded s3:// COG);
      - anything else -> None (no standalone object; vectors fall back to the
        inline-GeoJSON snapshot path).
    """
    if not uri:
        return None
    if uri.startswith("s3://"):
        return uri
    low = uri.lower()
    if uri.startswith(("http://", "https://")) and "/cog/tiles/" in low:
        parsed = urllib.parse.urlsplit(uri)
        qs = urllib.parse.parse_qs(parsed.query)
        url_vals = qs.get("url") or []
        if url_vals:
            candidate = urllib.parse.unquote(url_vals[0])
            if candidate.startswith("s3://"):
                return candidate
    return None


def _head_size(bucket: str, key: str) -> int | None:
    """HeadObject -> ContentLength, or None when the object is missing (404/403).

    A GetObject-only role gets 403 (not 404) for a missing key under a prefix it
    can read (S3 hides existence without ListBucket), so treat 403/404 alike as
    "object not present" -> None (the caller skips it with a typed warning). Any
    other ClientError re-raises (a real failure surfaces, not silently skipped).
    """
    try:
        resp = _s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("NoSuchKey", "404", "NotFound") or status == 404:
            return None
        if code in ("403", "AccessDenied", "Forbidden") or status == 403:
            return None
        raise
    return int(resp.get("ContentLength", 0))


def _download(bucket: str, key: str, dest_path: str) -> None:
    """Download an S3 object to ``dest_path`` (raises on real S3 errors)."""
    _s3.download_file(bucket, key, dest_path)


def _load_snapshot_inline(case_id: str) -> dict[str, Any]:
    """Read the case-view snapshot and return ``{layer_id: inline_geojson}``.

    The agent materializes inline vector GeoJSON onto
    ``snapshot.session_state.loaded_layers[*].inline_geojson`` (persistence
    ``build_case_view_snapshot``). For vectors with no standalone S3 object this
    is the only source. Best-effort: a missing/unreadable snapshot returns ``{}``
    (those vectors are simply skipped with a typed warning).
    """
    if not RUNS_BUCKET:
        return {}
    key = f"{VIEW_PREFIX}/{case_id}.json"
    try:
        resp = _s3.get_object(Bucket=RUNS_BUCKET, Key=key)
        body = resp["Body"].read()
        snapshot = json.loads(body)
    except Exception as exc:  # noqa: BLE001 -- snapshot absent / unreadable
        logger.info("case-export: no snapshot for %s (%s)", case_id, type(exc).__name__)
        return {}
    out: dict[str, Any] = {}
    ss = snapshot.get("session_state") if isinstance(snapshot, dict) else None
    layers = ss.get("loaded_layers") if isinstance(ss, dict) else None
    if isinstance(layers, list):
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            lid = layer.get("layer_id")
            gj = layer.get("inline_geojson")
            if lid and gj is not None:
                out[lid] = gj
    return out


# --------------------------------------------------------------------------- #
# Plain-XML .qgs generation (NO PyQGIS).
# --------------------------------------------------------------------------- #


def _qgs_raster_layer_xml(layer_name: str, rel_path: str, style: tuple[float, float, str] | None) -> str:
    """One ``<maplayer>`` raster (gdal provider) element for the .qgs.

    ``rel_path`` is the zip-relative path to the .tif (e.g.
    ``Flood depth/flood_depth_peak.tif``). When ``style`` is a (lo, hi, colormap)
    tuple, a ``singlebandpseudocolor`` renderer with discrete-interpolated ramp
    stops is emitted; when ``None`` (terrain default) a stock ``multibandcolor``
    /single-band default renderer is left to QGIS (we emit a minimal raster
    renderer so the layer still draws).
    """
    name_x = _sax.escape(layer_name)
    src_x = _sax.escape(rel_path)
    if style is None:
        # Terrain / default: a plain single-band-gray renderer (QGIS auto-scales).
        renderer = (
            '<rasterrenderer type="singlebandgray" grayBand="1" '
            'gradient="BlackToWhite" opacity="1">'
            '<contrastEnhancement/></rasterrenderer>'
        )
    else:
        lo, hi, cmap = style
        stops = _ramp_stops(lo, hi, cmap)
        items = "".join(
            '<item value="{v:g}" color="#{r:02x}{g:02x}{b:02x}" '
            'alpha="255" label="{v:g}"/>'.format(v=v, r=r, g=g, b=b)
            for (v, r, g, b) in stops
        )
        renderer = (
            '<rasterrenderer type="singlebandpseudocolor" band="1" '
            'classificationMin="{lo:g}" classificationMax="{hi:g}" opacity="1">'
            '<rastershader><colorrampshader colorRampType="INTERPOLATED" '
            'classificationMode="1">{items}</colorrampshader></rastershader>'
            "</rasterrenderer>"
        ).format(lo=lo, hi=hi, items=items)
    return (
        '<maplayer type="raster">'
        f"<id>{name_x}</id>"
        f"<datasource>./{src_x}</datasource>"
        '<provider>gdal</provider>'
        f"<layername>{name_x}</layername>"
        f"<pipe>{renderer}</pipe>"
        "</maplayer>"
    )


def _qgs_vector_layer_xml(layer_name: str, rel_path: str) -> str:
    """One ``<maplayer>`` vector (ogr provider, GeoJSON) element for the .qgs."""
    name_x = _sax.escape(layer_name)
    src_x = _sax.escape(rel_path)
    return (
        '<maplayer type="vector" geometry="">'
        f"<id>{name_x}</id>"
        f"<datasource>./{src_x}</datasource>"
        '<provider>ogr</provider>'
        f"<layername>{name_x}</layername>"
        "</maplayer>"
    )


def _build_qgs(case_title: str, layer_xml: list[str]) -> str:
    """Assemble a minimal, valid plain-XML QGIS project referencing the layers.

    All datasources are RELATIVE (``./<folder>/<file>``) so the zip is portable:
    unzip + open the .qgs and every layer resolves against the co-zipped files.
    """
    title_x = _sax.escape(case_title or "GRACE-2 Case")
    layers = "".join(layer_xml)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<qgis version="3.34" projectname="{title}">'
        "<title>{title}</title>"
        '<projectCrs><spatialrefsys><authid>EPSG:4326</authid></spatialrefsys></projectCrs>'
        '<homePath path=""/>'
        "<projectlayers>{layers}</projectlayers>"
        "</qgis>"
    ).format(title=title_x, layers=layers)


# --------------------------------------------------------------------------- #
# ULID-ish id for the zip object key (no external dep).
# --------------------------------------------------------------------------- #

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid() -> str:
    """A monotonic-ish, sortable id for the export object key (Crockford b32).

    Lambda has no ulid dep; this is good enough for a unique, time-prefixed
    object name (48-bit ms timestamp + 80 random bits).
    """
    import os as _os
    import time as _time

    ts = int(_time.time() * 1000) & ((1 << 48) - 1)
    rnd = int.from_bytes(_os.urandom(10), "big")
    value = (ts << 80) | rnd
    chars = []
    for _ in range(26):
        chars.append(_ULID_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


# --------------------------------------------------------------------------- #
# Entrypoint.
# --------------------------------------------------------------------------- #


def handler(event, context):  # noqa: ANN001, ARG001, C901
    """API Gateway HTTP entrypoint. Exports a whole Case as a QGIS-ready zip and
    returns a pre-signed GET URL. Signed-in + owner-scoped (403 on mismatch)."""
    if not isinstance(event, dict):
        event = {}

    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    if method == "OPTIONS":
        return _response(200, {"ok": True})

    if not CASES_TABLE or not RUNS_BUCKET:
        return _response(503, {"error": "case export is not configured in this deployment"})

    case_id = _query_case_id(event)
    if not case_id:
        return _response(400, {"error": "missing required query param 'case_id'"})
    if "/" in case_id or ".." in case_id or "\\" in case_id:
        return _response(400, {"error": "invalid case_id"})

    # AUTH: export is privileged -> a verified token is REQUIRED (401 otherwise).
    token = _extract_bearer(event)
    claims = cognito_verify(token) if token else None
    if claims is None:
        return _response(401, {"error": "authentication required to export a case"})
    uid = claims.get("uid")
    if not uid:
        return _response(401, {"error": "authentication required to export a case"})

    # Load the Case doc (PK _id) + enforce OWNER scoping.
    try:
        table = _ddb.Table(CASES_TABLE)
        got = table.get_item(Key={"_id": case_id})
    except Exception:  # noqa: BLE001
        logger.exception("case-export: GetItem failed for case=%s", case_id)
        return _response(500, {"error": "could not load case"})
    item = got.get("Item")
    if not item:
        return _response(404, {"error": "case not found", "case_id": case_id})
    doc = _from_ddb(item)

    # Decision 10: the Case doc's owner is the INTERNAL ULID, not the Cognito sub
    # ``cognito_verify`` returns. Resolve sub -> ULID before the owner check, else
    # the TRUE owner (sub != ULID) is wrongly 403'd. No user record -> None ->
    # the comparison fails closed to 403 (never an implicit allow, never a 500).
    internal_uid = _resolve_internal_uid(uid)

    # FAIL CLOSED: deny unless a PRESENT owner positively equals the resolved id.
    # An owner-less case (no owner_user_id / user_id) is NOT exportable by anyone
    # -- a falsy owner must never short-circuit the guard into an implicit allow.
    owner = doc.get("owner_user_id") or doc.get("user_id")
    if (not owner) or (not internal_uid) or (owner != internal_uid):
        logger.info(
            "case-export: owner mismatch case=%s uid=%s owner=%s", case_id, internal_uid, owner
        )
        return _response(403, {"error": "not authorized to export this case"})

    case_title = doc.get("title") or case_id
    summaries = doc.get("loaded_layer_summaries")
    if not isinstance(summaries, list):
        summaries = []

    # Inline GeoJSON for vectors with no standalone object (best-effort).
    inline_by_layer = _load_snapshot_inline(case_id)

    try:
        result = _build_export(case_id, case_title, summaries, inline_by_layer)
    except Exception:  # noqa: BLE001 -- never leak a traceback to the client
        logger.exception("case-export: build failed for case=%s", case_id)
        return _response(500, {"error": "could not build export"})

    return _response(200, result)


def _build_export(
    case_id: str,
    case_title: str,
    summaries: list,
    inline_by_layer: dict[str, Any],
) -> dict:
    """Download layers, generate the styled .qgs, zip, upload, and presign.

    Returns the success body ``{url, size_bytes, layer_count, expires_in}``.
    """
    import shutil
    import tempfile

    work = tempfile.mkdtemp(prefix=f"export-{case_id}-", dir=_TMP_DIR)
    try:
        layer_xml: list[str] = []
        manifest_lines: list[str] = [
            f"GRACE-2 case export: {case_title}",
            f"case_id: {case_id}",
            "",
            "Layers:",
        ]
        size_bytes = 0
        layer_count = 0
        used_folders: set[str] = set()

        for entry in summaries:
            if not isinstance(entry, dict):
                continue
            layer_id = str(entry.get("layer_id") or "")
            layer_name = entry.get("name") or layer_id or "layer"
            layer_type = entry.get("layer_type") or ""
            uri = entry.get("uri") or ""
            style_preset = entry.get("style_preset") or ""

            folder = _sanitize_folder(layer_name, layer_id or "layer")
            # De-dup folder names so two layers with the same name don't collide.
            base_folder = folder
            n = 2
            while folder in used_folders:
                folder = f"{base_folder}_{n}"
                n += 1
            used_folders.add(folder)
            folder_abs = os.path.join(work, folder)

            s3_uri = _recover_s3_uri(uri)
            if s3_uri is not None:
                parsed = _parse_s3_uri(s3_uri)
                if parsed is None:
                    manifest_lines.append(f"  - {layer_name}: SKIPPED (unparsable uri {uri})")
                    continue
                bucket, key = parsed
                csize = _head_size(bucket, key)
                if csize is None:
                    # Evicted / missing content-addressed object -> typed skip.
                    logger.info(
                        "case-export: skipping evicted object layer=%s s3=%s",
                        layer_id,
                        s3_uri,
                    )
                    manifest_lines.append(
                        f"  - {layer_name}: SKIPPED (cache object evicted: {s3_uri})"
                    )
                    continue
                os.makedirs(folder_abs, exist_ok=True)
                fname = os.path.basename(key) or f"{folder}.tif"
                if not fname.lower().endswith((".tif", ".tiff")):
                    fname = f"{fname}.tif"
                dest = os.path.join(folder_abs, fname)
                _download(bucket, key, dest)
                size_bytes += csize
                rel = f"{folder}/{fname}"
                style = _resolve_style(style_preset, s3_uri)
                layer_xml.append(_qgs_raster_layer_xml(layer_name, rel, style))
                manifest_lines.append(
                    f"  - {layer_name}: raster {rel} (preset={style_preset or 'none'})"
                )
                layer_count += 1
                continue

            # No standalone object: a vector served inline. Pull the snapshot
            # GeoJSON and write it as a .geojson the .qgs references via ogr.
            gj = inline_by_layer.get(layer_id)
            if gj is None and layer_type == "vector":
                manifest_lines.append(
                    f"  - {layer_name}: SKIPPED (vector has no inline geojson in snapshot)"
                )
                continue
            if gj is None:
                manifest_lines.append(
                    f"  - {layer_name}: SKIPPED (no s3 object and no inline geojson; uri={uri})"
                )
                continue
            os.makedirs(folder_abs, exist_ok=True)
            fname = f"{folder}.geojson"
            dest = os.path.join(folder_abs, fname)
            gj_bytes = json.dumps(gj, separators=(",", ":")).encode("utf-8")
            with open(dest, "wb") as f:
                f.write(gj_bytes)
            size_bytes += len(gj_bytes)
            rel = f"{folder}/{fname}"
            layer_xml.append(_qgs_vector_layer_xml(layer_name, rel))
            manifest_lines.append(f"  - {layer_name}: vector {rel} (inline geojson)")
            layer_count += 1

        # The styled QGIS project + the README/manifest.
        qgs = _build_qgs(case_title, layer_xml)
        qgs_path = os.path.join(work, "project.qgs")
        with open(qgs_path, "w", encoding="utf-8") as f:
            f.write(qgs)

        manifest_lines.append("")
        manifest_lines.append(f"Total layers exported: {layer_count}")
        manifest_lines.append(
            "Open project.qgs in QGIS; all layer datasources are relative to "
            "this folder."
        )
        manifest_path = os.path.join(work, "manifest.txt")
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write("\n".join(manifest_lines) + "\n")

        # Zip the whole /tmp tree.
        zip_path = os.path.join(_TMP_DIR, f"export-{case_id}-{_ulid()}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(work):
                for name in files:
                    abs_p = os.path.join(root, name)
                    arc = os.path.relpath(abs_p, work)
                    zf.write(abs_p, arc)

        zip_size = os.path.getsize(zip_path)

        # Upload to exports/{case_id}/{ulid}.zip and presign a GET.
        object_key = f"{EXPORTS_PREFIX}/{case_id}/{os.path.basename(zip_path)}"
        with open(zip_path, "rb") as f:
            _s3.put_object(
                Bucket=RUNS_BUCKET,
                Key=object_key,
                Body=f.read(),
                ContentType="application/zip",
            )
        url = _s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": RUNS_BUCKET, "Key": object_key},
            ExpiresIn=EXPORT_SIGNED_TTL_S,
        )

        try:
            os.unlink(zip_path)
        except OSError:
            pass

        return {
            "url": url,
            "size_bytes": int(size_bytes),
            "layer_count": int(layer_count),
            "expires_in": int(EXPORT_SIGNED_TTL_S),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
