"""``fetch_gbif_occurrences`` atomic tool — GBIF species occurrence point fetcher (job-0087).

Wraps the GBIF Occurrence Search API (``https://api.gbif.org/v1/occurrence/search``)
to return species observation points clipped to a bbox as a FlatGeobuf. This is one
of three Tier-1 conservation/biodiversity data fetchers landed in sprint-12 Wave 1
(see ``project_conservation_tool_stubs`` memo: GBIF + iNaturalist + WDPA minimum
for Case 1 species-occurrence overlays).

API surface (verified 2026-06-08):

    Search:    https://api.gbif.org/v1/occurrence/search?taxonKey={key}&decimalLongitude={west},{east}&decimalLatitude={south},{north}&hasCoordinate=true&limit=300&offset={off}[&year={y0},{y1}]
    Species match: https://api.gbif.org/v1/species/match?name={name}

The search endpoint paginates with ``offset``/``limit``; the response carries
``results: list[occ]``, ``endOfRecords: bool``, ``count: int``. We keep fetching
300-record pages until ``endOfRecords`` is True OR we have ``>= max_records``.

The species-match endpoint resolves a scientific name (e.g. ``"Puma concolor coryi"``)
into a numeric ``usageKey`` we can pass to ``search`` as ``taxonKey``.

FR-TA-2 atomic tool. FR-CE-8 / FR-DC-3/4: routed through ``read_through`` so
identical ``(taxonKey, bbox, year_range, max_records)`` calls reuse the cached
FlatGeobuf.

Tier-1 free (no API key required). Generous rate limits (>100 RPS) — we still
apply per-request timeouts and surface 5xx as retryable ``GBIFUpstreamError``.

Cache key composition (per audit.md): SHA-256 of (taxonKey-resolved, bbox-6dp,
year_range, max_records). Note: when ``species_key`` is a *str* (scientific name),
we resolve it to ``taxonKey`` FIRST and key the cache on the resolved int —
two callers asking for "Puma concolor coryi" and ``7193927`` over the same bbox
get the same cache hit.

Output FlatGeobuf schema:
    Geometry: Point (one feature per GBIF occurrence with coordinates)
    Properties:
        gbifID                            (int)   — stable GBIF occurrence ID
        species                           (str)   — verbatim species name
        eventDate                         (str)   — ISO-8601 string (may be partial)
        coordinateUncertaintyInMeters     (float) — null when not reported
        basisOfRecord                     (str)   — e.g. "HUMAN_OBSERVATION", "PRESERVED_SPECIMEN"

CRS: EPSG:4326 (GBIF coordinates are always WGS84 decimal degrees).
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
from typing import Any

import httpx

from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from .cache import read_through

__all__ = ["fetch_gbif_occurrences"]

logger = logging.getLogger("grace2_agent.tools.fetch_gbif_occurrences")


# ---------------------------------------------------------------------------
# Error types (FR-AS-11 typed-error surface).
# ---------------------------------------------------------------------------


class GBIFError(RuntimeError):
    """Base class for fetch_gbif_occurrences failures.

    ``error_code`` maps to the WebSocket A.6 error frame emitted by the agent
    surface. ``retryable`` guides FR-AS-11 retry logic.
    """

    error_code: str = "GBIF_ERROR"
    retryable: bool = True


class GBIFInputError(GBIFError):
    """Bad inputs (unknown species name, malformed bbox, etc.)."""

    error_code = "GBIF_INPUT_ERROR"
    retryable = False


class GBIFUpstreamError(GBIFError):
    """GBIF API returned 5xx or the network call failed."""

    error_code = "GBIF_UPSTREAM_ERROR"
    retryable = True


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

_GBIF_SEARCH_URL = "https://api.gbif.org/v1/occurrence/search"
_GBIF_SPECIES_MATCH_URL = "https://api.gbif.org/v1/species/match"

# Page size per GBIF documentation maximum is 300.
_PAGE_SIZE = 300

# Default per-request timeout. GBIF normally responds within a few seconds; we
# pad generously for global-coverage queries.
_TIMEOUT_S = 30.0

# Cap on max_records per call (defensive — a runaway caller asking for 10M
# records would saturate disk + memory; the tool docstring documents this).
_MAX_RECORDS_HARD_CAP = 100_000

# User-Agent per GBIF usage guidelines.
_USER_AGENT = (
    "grace-2/0.1 (Hazard Modeling Agent; "
    "https://github.com/double-r-squared/GRACE-2; agent@grace-2.dev)"
)

# ---------------------------------------------------------------------------
# AtomicToolMetadata — registered once at import time.
# ---------------------------------------------------------------------------

_METADATA = AtomicToolMetadata(
    name="fetch_gbif_occurrences",
    ttl_class="static-30d",
    source_class="gbif",
    cacheable=True,
)


# ---------------------------------------------------------------------------
# bbox helpers.
# ---------------------------------------------------------------------------


def _validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    """Raise ``GBIFInputError`` if bbox is invalid."""
    if len(bbox) != 4:
        raise GBIFInputError(
            f"bbox must be (west, south, east, north); got {bbox!r}"
        )
    west, south, east, north = bbox
    if not all(math.isfinite(v) for v in bbox):
        raise GBIFInputError(f"bbox contains non-finite values: {bbox!r}")
    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        raise GBIFInputError(f"bbox lon out of [-180,180]: {bbox!r}")
    if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
        raise GBIFInputError(f"bbox lat out of [-90,90]: {bbox!r}")
    if west >= east or south >= north:
        raise GBIFInputError(
            f"bbox is degenerate (west < east, south < north required): {bbox!r}"
        )


def _round_bbox_to_6dp(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Round bbox coords to 6dp (~0.1m) for cache-key stability."""
    return tuple(round(v, 6) for v in bbox)  # type: ignore[return-value]


def _validate_year_range(year_range: tuple[int, int] | None) -> None:
    """Raise GBIFInputError if year_range is malformed."""
    if year_range is None:
        return
    if len(year_range) != 2:
        raise GBIFInputError(f"year_range must be (start, end); got {year_range!r}")
    y0, y1 = year_range
    if not (isinstance(y0, int) and isinstance(y1, int)):
        raise GBIFInputError(f"year_range values must be ints; got {year_range!r}")
    if y0 > y1:
        raise GBIFInputError(
            f"year_range start must be <= end; got {year_range!r}"
        )
    # GBIF has data from ~1700 onward; reject obviously bad years.
    if y0 < 1500 or y1 > 2100:
        raise GBIFInputError(
            f"year_range outside reasonable bounds [1500, 2100]: {year_range!r}"
        )


# ---------------------------------------------------------------------------
# GBIF species-name → taxonKey resolution.
# ---------------------------------------------------------------------------


def _resolve_species_name_to_taxon_key(
    name: str,
    *,
    client: httpx.Client | None = None,
) -> int:
    """Call GBIF species/match to resolve a scientific name to a ``usageKey`` (taxonKey).

    Raises:
        ``GBIFInputError``: name resolves to no usageKey (unknown species).
        ``GBIFUpstreamError``: GBIF API 5xx or network failure.
    """
    if not name or not name.strip():
        raise GBIFInputError("species_key str must be a non-empty species name")

    owns_client = client is None
    if owns_client:
        client = httpx.Client(
            timeout=_TIMEOUT_S,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        )
    try:
        try:
            resp = client.get(_GBIF_SPECIES_MATCH_URL, params={"name": name})
        except httpx.RequestError as exc:
            raise GBIFUpstreamError(
                f"GBIF species/match network failure for name={name!r}: {exc}"
            ) from exc

        if resp.status_code >= 500:
            raise GBIFUpstreamError(
                f"GBIF species/match returned {resp.status_code} for name={name!r}"
            )
        if resp.status_code >= 400:
            raise GBIFInputError(
                f"GBIF species/match returned {resp.status_code} for name={name!r}: "
                f"{resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise GBIFUpstreamError(
                f"GBIF species/match returned non-JSON for name={name!r}: {exc}"
            ) from exc

        usage_key = payload.get("usageKey")
        if usage_key is None:
            # GBIF returns matchType="NONE" + no usageKey for unknown names.
            match_type = payload.get("matchType", "?")
            raise GBIFInputError(
                f"GBIF could not resolve species name {name!r} "
                f"(matchType={match_type!r})"
            )

        if not isinstance(usage_key, int):
            try:
                usage_key = int(usage_key)
            except (TypeError, ValueError) as exc:
                raise GBIFUpstreamError(
                    f"GBIF species/match usageKey is not an int: {usage_key!r}"
                ) from exc

        logger.info(
            "fetch_gbif_occurrences: resolved species name=%r → taxonKey=%d",
            name,
            usage_key,
        )
        return usage_key
    finally:
        if owns_client:
            client.close()


# ---------------------------------------------------------------------------
# Paginated occurrence fetch.
# ---------------------------------------------------------------------------


def _fetch_all_occurrence_pages(
    taxon_key: int,
    bbox: tuple[float, float, float, float],
    year_range: tuple[int, int] | None,
    max_records: int,
    *,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Fetch all occurrence records for ``(taxon_key, bbox, year_range)`` up to ``max_records``.

    Pagination loop: 300-record pages until ``endOfRecords=True`` OR we hit the cap.

    Returns the list of raw occurrence dicts from the GBIF response.

    Raises:
        ``GBIFUpstreamError``: network failure or 5xx after the call.
        ``GBIFInputError``: 4xx (typically caller error — bad taxonKey).
    """
    west, south, east, north = bbox

    owns_client = client is None
    if owns_client:
        client = httpx.Client(
            timeout=_TIMEOUT_S,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        )
    try:
        all_records: list[dict[str, Any]] = []
        offset = 0
        # Defensive page-count cap so a misbehaving server can't loop forever.
        max_pages = (max_records + _PAGE_SIZE - 1) // _PAGE_SIZE + 5

        for page_idx in range(max_pages):
            params: dict[str, Any] = {
                "taxonKey": taxon_key,
                "decimalLongitude": f"{west},{east}",
                "decimalLatitude": f"{south},{north}",
                "hasCoordinate": "true",
                "limit": _PAGE_SIZE,
                "offset": offset,
            }
            if year_range is not None:
                params["year"] = f"{year_range[0]},{year_range[1]}"

            try:
                resp = client.get(_GBIF_SEARCH_URL, params=params)
            except httpx.RequestError as exc:
                raise GBIFUpstreamError(
                    f"GBIF occurrence/search network failure "
                    f"(taxonKey={taxon_key}, offset={offset}): {exc}"
                ) from exc

            if resp.status_code >= 500:
                raise GBIFUpstreamError(
                    f"GBIF occurrence/search returned {resp.status_code} "
                    f"(taxonKey={taxon_key}, offset={offset})"
                )
            if resp.status_code >= 400:
                raise GBIFInputError(
                    f"GBIF occurrence/search returned {resp.status_code} "
                    f"(taxonKey={taxon_key}, offset={offset}): "
                    f"{resp.text[:200]}"
                )

            try:
                payload = resp.json()
            except ValueError as exc:
                raise GBIFUpstreamError(
                    f"GBIF occurrence/search returned non-JSON "
                    f"(taxonKey={taxon_key}, offset={offset}): {exc}"
                ) from exc

            results = payload.get("results", [])
            end_of_records = bool(payload.get("endOfRecords", True))

            if not isinstance(results, list):
                raise GBIFUpstreamError(
                    f"GBIF occurrence/search 'results' is not a list: {type(results).__name__}"
                )

            all_records.extend(results)
            logger.info(
                "fetch_gbif_occurrences: page %d records=%d total=%d endOfRecords=%s",
                page_idx,
                len(results),
                len(all_records),
                end_of_records,
            )

            if end_of_records:
                break
            if len(all_records) >= max_records:
                break
            if not results:
                # Defensive: empty page without endOfRecords — bail out so we
                # don't loop forever on a misbehaving response.
                break

            offset += _PAGE_SIZE
        else:
            logger.warning(
                "fetch_gbif_occurrences: hit max_pages=%d without endOfRecords; "
                "returning %d records",
                max_pages,
                len(all_records),
            )

        # Trim to max_records cap.
        if len(all_records) > max_records:
            all_records = all_records[:max_records]
        return all_records
    finally:
        if owns_client:
            client.close()


# ---------------------------------------------------------------------------
# FlatGeobuf serialization.
# ---------------------------------------------------------------------------


def _records_to_flatgeobuf_bytes(
    records: list[dict[str, Any]],
    bbox: tuple[float, float, float, float],
) -> bytes:
    """Convert GBIF occurrence dicts to a FlatGeobuf with the documented schema.

    For each record, requires ``decimalLongitude`` and ``decimalLatitude`` (we
    skip any record missing them — GBIF *should* not return such records given
    ``hasCoordinate=true`` but we are defensive).

    Also enforces a geographic-correctness check (job-0086 codified lesson):
    every emitted point must lie WITHIN the requested bbox. GBIF occasionally
    returns points slightly outside due to coordinate-uncertainty bbox-tests;
    we hard-filter to keep the contract clean.

    Returns FlatGeobuf bytes (empty FlatGeobuf if no records).
    """
    # Lazy imports — test environments without geopandas/shapely can still
    # import the module.
    try:
        import geopandas as gpd  # type: ignore[import-not-found]
        import pandas as pd  # type: ignore[import-not-found]
        from shapely.geometry import Point  # type: ignore[import-not-found]
    except ImportError as exc:
        raise GBIFUpstreamError(
            f"geopandas / shapely not available: {exc}"
        ) from exc

    west, south, east, north = bbox

    rows: list[dict[str, Any]] = []
    geoms: list[Any] = []
    skipped_missing_coords = 0
    skipped_outside_bbox = 0

    for rec in records:
        lon = rec.get("decimalLongitude")
        lat = rec.get("decimalLatitude")
        if lon is None or lat is None:
            skipped_missing_coords += 1
            continue
        try:
            lon_f = float(lon)
            lat_f = float(lat)
        except (TypeError, ValueError):
            skipped_missing_coords += 1
            continue
        if not (math.isfinite(lon_f) and math.isfinite(lat_f)):
            skipped_missing_coords += 1
            continue
        # Geographic-correctness gate (job-0086 codified lesson): every point
        # must fall in the requested bbox. GBIF should respect the spatial
        # filter, but we double-check.
        if not (west <= lon_f <= east and south <= lat_f <= north):
            skipped_outside_bbox += 1
            continue

        rows.append({
            "gbifID": rec.get("gbifID"),
            "species": rec.get("species") or rec.get("scientificName") or "",
            "eventDate": rec.get("eventDate") or "",
            "coordinateUncertaintyInMeters": rec.get("coordinateUncertaintyInMeters"),
            "basisOfRecord": rec.get("basisOfRecord") or "",
        })
        geoms.append(Point(lon_f, lat_f))

    if skipped_missing_coords:
        logger.info(
            "fetch_gbif_occurrences: skipped %d records with missing/invalid coordinates",
            skipped_missing_coords,
        )
    if skipped_outside_bbox:
        logger.warning(
            "fetch_gbif_occurrences: filtered %d records outside requested bbox %s",
            skipped_outside_bbox,
            bbox,
        )

    if not rows:
        # Empty result — build an empty FlatGeobuf with the right column schema
        # so downstream readers see a well-formed file rather than a parse error.
        # geopandas needs at least one column to write; we synthesize an empty
        # GeoDataFrame with the schema and write it.
        empty_df = pd.DataFrame(
            columns=[
                "gbifID",
                "species",
                "eventDate",
                "coordinateUncertaintyInMeters",
                "basisOfRecord",
            ]
        )
        gdf = gpd.GeoDataFrame(empty_df, geometry=[], crs="EPSG:4326")
    else:
        df = pd.DataFrame(rows)
        gdf = gpd.GeoDataFrame(df, geometry=geoms, crs="EPSG:4326")

    tmp_fgb: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".fgb", delete=False, prefix="grace2_gbif_"
        ) as fgb_f:
            tmp_fgb = fgb_f.name
        try:
            gdf.to_file(tmp_fgb, driver="FlatGeobuf", engine="pyogrio")
        except Exception as exc:  # noqa: BLE001
            raise GBIFUpstreamError(
                f"FlatGeobuf write failed: {exc}"
            ) from exc

        with open(tmp_fgb, "rb") as f:
            fgb_bytes = f.read()

        logger.info(
            "fetch_gbif_occurrences: FlatGeobuf serialized %d feature(s) = %d bytes",
            len(rows),
            len(fgb_bytes),
        )
        return fgb_bytes
    finally:
        if tmp_fgb:
            try:
                os.unlink(tmp_fgb)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Fetch function (passed to read_through).
# ---------------------------------------------------------------------------


def _fetch_gbif_bytes(
    taxon_key: int,
    bbox: tuple[float, float, float, float],
    year_range: tuple[int, int] | None,
    max_records: int,
) -> bytes:
    """Pipeline: paginate GBIF → coordinate-validate → serialize to FlatGeobuf."""
    records = _fetch_all_occurrence_pages(
        taxon_key=taxon_key,
        bbox=bbox,
        year_range=year_range,
        max_records=max_records,
    )
    return _records_to_flatgeobuf_bytes(records, bbox)


# ---------------------------------------------------------------------------
# Registered atomic tool.
# ---------------------------------------------------------------------------


@register_tool(_METADATA)
def fetch_gbif_occurrences(
    species_key: int | str,
    bbox: tuple[float, float, float, float],
    year_range: tuple[int, int] | None = None,
    max_records: int = 5000,
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> LayerURI:
    """GBIF Tier-1 species occurrence point fetcher.

    Use this when: the agent needs species observation points for ecological
    analysis or display — e.g. mapping Florida panther sightings over a flood
    risk surface, overlaying bird occurrences on a wildfire footprint, or
    summarizing biodiversity within a protected area. Returns one FlatGeobuf
    point feature per GBIF occurrence record that has decimal coordinates and
    falls inside the requested bbox.

    Do NOT use this for: protected-area POLYGONS (use a WDPA fetcher for those),
    iNaturalist-only research-grade observations (use ``fetch_inaturalist_observations``;
    GBIF *includes* iNaturalist research-grade as one of many sources but does
    not let you filter to it cleanly), historical-range polygons or species
    distribution models (out of scope; use Maxent or a published SDM raster
    instead), or live tracking data (Movebank/Argos — different tool).

    Wraps the GBIF Occurrence Search API
    (``https://api.gbif.org/v1/occurrence/search``). Accepts species either as
    a GBIF ``taxonKey`` (int — fast path) OR a scientific name (str — extra
    call to ``species/match`` to resolve). Bbox in WGS84 (west, south, east,
    north). Year range filter is inclusive on both ends. The output FlatGeobuf
    has the schema documented in the module docstring (``gbifID``, ``species``,
    ``eventDate``, ``coordinateUncertaintyInMeters``, ``basisOfRecord``) and is
    written in EPSG:4326.

    Pagination caps at ``max_records`` (default 5000, hard cap 100_000). The
    GBIF API itself caps offset at 100_000 per query — for larger pulls split
    by bbox subdivision (out of scope for this tool).

    Params:
        species_key: GBIF ``taxonKey`` (int — preferred) OR scientific name
            (str — resolved via ``species/match``). Example: ``7193927`` is
            ``Puma concolor coryi`` (Florida panther).
        bbox: ``(west, south, east, north)`` in EPSG:4326 (WGS84 decimal degrees).
        year_range: optional ``(start_year, end_year)`` inclusive filter on
            ``eventDate.year``. When ``None`` no year filter is applied.
        max_records: cap on records returned (default 5000). Hard cap 100_000.

    Returns:
        A ``LayerURI`` pointing at a FlatGeobuf in the cache bucket:
        ``gs://grace-2-hazard-prod-cache/cache/static-30d/gbif/<key>.fgb``
        containing the occurrence points clipped to the requested bbox.
        ``layer_type="vector"``, ``role="context"``, ``units=None``.

    FR-CE-8: Routed through ``read_through`` so identical
    ``(taxonKey, bbox, year_range, max_records)`` calls reuse the cached
    FlatGeobuf. Cache key uses the *resolved* taxonKey, so two callers using
    ``species_key="Puma concolor coryi"`` and ``species_key=7193927`` for the
    same bbox hit the same cache entry.
    """
    # ---- Input validation ----
    _validate_bbox(bbox)
    _validate_year_range(year_range)

    if not isinstance(max_records, int):
        raise GBIFInputError(
            f"max_records must be int; got {type(max_records).__name__}"
        )
    if max_records <= 0:
        raise GBIFInputError(f"max_records must be > 0; got {max_records}")
    if max_records > _MAX_RECORDS_HARD_CAP:
        raise GBIFInputError(
            f"max_records exceeds hard cap {_MAX_RECORDS_HARD_CAP}; got {max_records}"
        )

    # ---- Species name → taxonKey resolution (cache key normalization) ----
    if isinstance(species_key, str):
        taxon_key = _resolve_species_name_to_taxon_key(species_key)
    elif isinstance(species_key, int):
        if species_key <= 0:
            raise GBIFInputError(
                f"taxonKey must be a positive int; got {species_key}"
            )
        taxon_key = species_key
    else:
        raise GBIFInputError(
            f"species_key must be int (taxonKey) or str (scientific name); "
            f"got {type(species_key).__name__}"
        )

    # ---- Cache-key params (resolved + quantized) ----
    q_bbox = _round_bbox_to_6dp(bbox)
    params: dict[str, Any] = {
        "taxonKey": taxon_key,
        "bbox": list(q_bbox),
        "max_records": max_records,
    }
    if year_range is not None:
        params["year_range"] = list(year_range)

    result = read_through(
        metadata=_METADATA,
        params=params,
        ext="fgb",
        fetch_fn=lambda: _fetch_gbif_bytes(
            taxon_key=taxon_key,
            bbox=q_bbox,
            year_range=year_range,
            max_records=max_records,
        ),
    )
    assert result.uri is not None, (
        "fetch_gbif_occurrences is cacheable; uri must be set by read_through"
    )

    return LayerURI(
        layer_id=f"gbif-{taxon_key}-{q_bbox[0]:.4f}-{q_bbox[1]:.4f}",
        name=f"GBIF Occurrences — taxonKey {taxon_key}",
        layer_type="vector",
        uri=result.uri,
        style_preset="gbif_occurrences",
        role="context",
        units=None,
    )
