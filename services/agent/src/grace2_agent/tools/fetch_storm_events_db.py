"""``fetch_storm_events_db`` atomic tool — NOAA Storm Events DB Tier-1 fetcher (job-0091).

Downloads the annual NOAA Storm Events Database details CSV (gzip) from
``https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/``, filters by
state and event-type, converts to FlatGeobuf with point geometry from
``BEGIN_LAT``/``BEGIN_LON``, and returns a ``LayerURI`` pointing at the cached
artifact.

The NOAA Storm Events Database is the authoritative US storm-event catalog
maintained by NCEI. Files follow the pattern::

    StormEvents_details-ftp_v1.0_d{year}_c{processed_date}.csv.gz

``processed_date`` is volatile (re-stamped on every NCEI reprocessing), so the
implementation scrapes the HTTP directory index to find the current file for
``year`` rather than hard-coding the processed date.

FR-TA-2: atomic tool returning ``LayerURI``.
FR-CE-8 / FR-DC-3/4: routed through ``read_through`` so identical
``(year, state, event_types)`` calls reuse the cached FlatGeobuf (static-30d).
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import logging
import re
import tempfile
from typing import Any

import httpx

from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from .cache import read_through

__all__ = ["fetch_storm_events_db", "StormEventsUpstreamError"]

logger = logging.getLogger("grace2_agent.tools.fetch_storm_events_db")

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

_INDEX_URL = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"
_FILE_RE = re.compile(
    r"StormEvents_details-ftp_v1\.0_d(\d{4})_c(\d{8})\.csv\.gz"
)

_USER_AGENT = (
    "grace-2/0.1 (Hazard Modeling Agent; "
    "https://github.com/double-r-squared/GRACE-2; agent@grace-2.dev)"
)

# Properties retained on each output point. Matches the audit.md spec.
_RETAINED_COLUMNS = (
    "EVENT_ID",
    "EVENT_TYPE",
    "STATE",
    "BEGIN_DATE_TIME",
    "END_DATE_TIME",
    "INJURIES_DIRECT",
    "DAMAGE_PROPERTY",
    "EPISODE_NARRATIVE",
)


# ---------------------------------------------------------------------------
# Error types (FR-AS-11 typed-error surface).
# ---------------------------------------------------------------------------


class StormEventsError(RuntimeError):
    """Base class for fetch_storm_events_db failures."""

    error_code: str = "STORM_EVENTS_ERROR"
    retryable: bool = True


class StormEventsUpstreamError(StormEventsError):
    """NOAA Storm Events Database download or parsing failed."""

    error_code = "STORM_EVENTS_UPSTREAM_ERROR"
    retryable = True


class StormEventsEmptyError(StormEventsError):
    """No events remain after filtering. Not retryable — filter is the cause."""

    error_code = "STORM_EVENTS_EMPTY"
    retryable = False


class StormEventsArgError(StormEventsError):
    """Invalid argument (e.g. year out of range, non-string state)."""

    error_code = "STORM_EVENTS_ARG_INVALID"
    retryable = False


# ---------------------------------------------------------------------------
# AtomicToolMetadata — registered once at import time.
# ---------------------------------------------------------------------------

_METADATA = AtomicToolMetadata(
    name="fetch_storm_events_db",
    ttl_class="static-30d",
    source_class="storm_events",
    cacheable=True,
)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _validate_inputs(
    year: int,
    state: str | None,
    event_types: list[str] | None,
) -> None:
    """Validate year/state/event_types or raise ``StormEventsArgError``."""
    if not isinstance(year, int):
        raise StormEventsArgError(f"year must be int, got {type(year).__name__}")
    # NOAA Storm Events DB coverage begins 1950.
    if year < 1950 or year > 2100:
        raise StormEventsArgError(
            f"year={year} out of NOAA Storm Events DB range [1950, 2100]"
        )
    if state is not None:
        if not isinstance(state, str) or len(state) != 2:
            raise StormEventsArgError(
                f"state must be ISO 2-letter code (e.g. 'FL'), got {state!r}"
            )
    if event_types is not None:
        if not isinstance(event_types, list) or not all(
            isinstance(e, str) and e for e in event_types
        ):
            raise StormEventsArgError(
                f"event_types must be list[str] with non-empty strings, got {event_types!r}"
            )


def _resolve_csv_url(year: int, *, client: httpx.Client | None = None) -> str:
    """Scrape the directory index to find the current CSV URL for ``year``.

    NCEI re-stamps the ``c{YYYYMMDD}`` suffix on every reprocessing, so we cannot
    hard-code it. We fetch the directory listing once and pick the newest
    processed-date suffix for the requested year.

    Raises:
        ``StormEventsUpstreamError`` if the index cannot be loaded or no entry
        exists for ``year``.
    """
    own_client = client is None
    if client is None:
        client = httpx.Client(
            timeout=60.0,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        )
    try:
        try:
            resp = client.get(_INDEX_URL)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise StormEventsUpstreamError(
                f"failed to fetch NOAA Storm Events index {_INDEX_URL}: {exc}"
            ) from exc

        candidates: list[tuple[str, str]] = []  # (processed_date, filename)
        for match in _FILE_RE.finditer(resp.text):
            file_year, processed_date = match.group(1), match.group(2)
            if int(file_year) == year:
                candidates.append((processed_date, match.group(0)))

        if not candidates:
            raise StormEventsUpstreamError(
                f"no NOAA Storm Events CSV found for year={year} in {_INDEX_URL}"
            )
        # Highest processed date = most recently reprocessed = canonical.
        candidates.sort(reverse=True)
        return _INDEX_URL + candidates[0][1]
    finally:
        if own_client:
            client.close()


def _download_csv_gz(url: str, *, client: httpx.Client | None = None) -> bytes:
    """Download a gzipped CSV from ``url`` and return raw gzip bytes.

    Raises ``StormEventsUpstreamError`` on transport errors.
    """
    own_client = client is None
    if client is None:
        # CSV gzip can be 50MB+ for active years; allow 120s.
        client = httpx.Client(
            timeout=120.0,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        )
    try:
        try:
            resp = client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise StormEventsUpstreamError(
                f"failed to download {url}: {exc}"
            ) from exc
        return resp.content
    finally:
        if own_client:
            client.close()


def _parse_filter_and_serialize(
    gz_bytes: bytes,
    state: str | None,
    event_types: list[str] | None,
) -> bytes:
    """Decompress + filter + emit FlatGeobuf bytes.

    Filters:
        - ``state`` is matched case-insensitively against the ``STATE`` column
          using the ISO 2-letter code's state name (NOAA uses the full name in
          the CSV, e.g. ``FLORIDA``).
        - ``event_types`` is matched case-insensitively against ``EVENT_TYPE``.
        - Rows with non-finite ``BEGIN_LAT``/``BEGIN_LON`` are silently dropped.

    Returns FlatGeobuf bytes of a point layer in EPSG:4326.

    Raises:
        ``StormEventsUpstreamError`` if the gzip is corrupt or the CSV is
          missing required columns.
        ``StormEventsEmptyError`` if all rows are filtered out.
    """
    try:
        import geopandas as gpd  # type: ignore[import-not-found]
        import pandas as pd  # type: ignore[import-not-found]
        from shapely.geometry import Point  # type: ignore[import-not-found]
    except ImportError as exc:
        raise StormEventsUpstreamError(
            f"geopandas / pandas / shapely not available: {exc}"
        ) from exc

    # Decompress.
    try:
        csv_text = gzip.decompress(gz_bytes).decode("utf-8", errors="replace")
    except (OSError, EOFError) as exc:
        raise StormEventsUpstreamError(
            f"NOAA Storm Events gzip is corrupt: {exc}"
        ) from exc

    # Parse CSV with pandas — handles quoting + embedded newlines correctly.
    try:
        # low_memory=False so dtype inference is single-pass and stable on
        # the EPISODE_NARRATIVE long-text column.
        df = pd.read_csv(
            io.StringIO(csv_text),
            dtype=str,
            low_memory=False,
            keep_default_na=False,
            na_values=[""],
        )
    except pd.errors.ParserError as exc:
        raise StormEventsUpstreamError(
            f"NOAA Storm Events CSV parse failed: {exc}"
        ) from exc

    required = {"BEGIN_LAT", "BEGIN_LON", "STATE", "EVENT_TYPE"}
    missing = required - set(df.columns)
    if missing:
        raise StormEventsUpstreamError(
            f"NOAA Storm Events CSV missing required columns: {sorted(missing)}; "
            f"got {sorted(df.columns)[:10]}..."
        )

    # State filter — NOAA writes full state name; we accept ISO 2-letter.
    if state is not None:
        state_name = _ISO_TO_STATE_NAME.get(state.upper())
        if state_name is None:
            # Fallback: match against STATE column directly (handles user
            # passing e.g. "FLORIDA" though we documented ISO 2-letter).
            state_name = state.upper()
        df = df[df["STATE"].str.upper() == state_name.upper()].copy()

    # Event-type filter (case-insensitive).
    if event_types is not None and len(event_types) > 0:
        wanted = {e.upper() for e in event_types}
        df = df[df["EVENT_TYPE"].str.upper().isin(wanted)].copy()

    # Coerce coordinates; drop rows with non-finite or missing values.
    df["BEGIN_LAT"] = pd.to_numeric(df["BEGIN_LAT"], errors="coerce")
    df["BEGIN_LON"] = pd.to_numeric(df["BEGIN_LON"], errors="coerce")
    df = df.dropna(subset=["BEGIN_LAT", "BEGIN_LON"]).copy()
    # Sanity-clip to WGS84 valid range; drop anything else as bad data.
    df = df[
        (df["BEGIN_LAT"].between(-90.0, 90.0))
        & (df["BEGIN_LON"].between(-180.0, 180.0))
    ].copy()

    if df.empty:
        raise StormEventsEmptyError(
            f"no NOAA Storm Events match state={state!r} event_types={event_types!r} "
            "after coordinate filtering"
        )

    # Restrict to retained columns (plus the lat/lon we use for geometry).
    keep_cols = [c for c in _RETAINED_COLUMNS if c in df.columns]
    df_out = df[keep_cols].copy()

    # Build GeoDataFrame with point geometry from BEGIN_LON/BEGIN_LAT.
    geom = [Point(lon, lat) for lon, lat in zip(df["BEGIN_LON"], df["BEGIN_LAT"])]
    gdf = gpd.GeoDataFrame(df_out, geometry=geom, crs="EPSG:4326")

    logger.info(
        "fetch_storm_events_db: %d feature(s) after filter state=%s event_types=%s",
        len(gdf),
        state,
        event_types,
    )

    # Serialize to FlatGeobuf.
    tmp_fgb: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".fgb", delete=False, prefix="grace2_storm_"
        ) as fgb_f:
            tmp_fgb = fgb_f.name
        gdf.to_file(tmp_fgb, driver="FlatGeobuf", engine="pyogrio")
        with open(tmp_fgb, "rb") as f:
            return f.read()
    except Exception as exc:  # noqa: BLE001 — surface as upstream error
        raise StormEventsUpstreamError(
            f"FlatGeobuf serialization failed: {exc}"
        ) from exc
    finally:
        if tmp_fgb is not None:
            import os as _os
            try:
                _os.unlink(tmp_fgb)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# ISO 2-letter → state name (NOAA convention).
#
# NOAA stores the full state name (uppercase) in the STATE column. We accept
# ISO 2-letter from callers (more ergonomic) and map to NOAA's spelling for
# the filter. Coverage: 50 states + DC + territories tracked by Storm Events.
# ---------------------------------------------------------------------------

_ISO_TO_STATE_NAME: dict[str, str] = {
    "AL": "ALABAMA", "AK": "ALASKA", "AZ": "ARIZONA", "AR": "ARKANSAS",
    "CA": "CALIFORNIA", "CO": "COLORADO", "CT": "CONNECTICUT",
    "DE": "DELAWARE", "DC": "DISTRICT OF COLUMBIA", "FL": "FLORIDA",
    "GA": "GEORGIA", "HI": "HAWAII", "ID": "IDAHO", "IL": "ILLINOIS",
    "IN": "INDIANA", "IA": "IOWA", "KS": "KANSAS", "KY": "KENTUCKY",
    "LA": "LOUISIANA", "ME": "MAINE", "MD": "MARYLAND",
    "MA": "MASSACHUSETTS", "MI": "MICHIGAN", "MN": "MINNESOTA",
    "MS": "MISSISSIPPI", "MO": "MISSOURI", "MT": "MONTANA",
    "NE": "NEBRASKA", "NV": "NEVADA", "NH": "NEW HAMPSHIRE",
    "NJ": "NEW JERSEY", "NM": "NEW MEXICO", "NY": "NEW YORK",
    "NC": "NORTH CAROLINA", "ND": "NORTH DAKOTA", "OH": "OHIO",
    "OK": "OKLAHOMA", "OR": "OREGON", "PA": "PENNSYLVANIA",
    "RI": "RHODE ISLAND", "SC": "SOUTH CAROLINA", "SD": "SOUTH DAKOTA",
    "TN": "TENNESSEE", "TX": "TEXAS", "UT": "UTAH", "VT": "VERMONT",
    "VA": "VIRGINIA", "WA": "WASHINGTON", "WV": "WEST VIRGINIA",
    "WI": "WISCONSIN", "WY": "WYOMING", "PR": "PUERTO RICO",
    "VI": "VIRGIN ISLANDS", "GU": "GUAM", "AS": "AMERICAN SAMOA",
    "MP": "NORTHERN MARIANA ISLANDS",
}


# ---------------------------------------------------------------------------
# Fetch function — builds the bytes callable for read_through.
# ---------------------------------------------------------------------------


def _fetch_storm_events_bytes(
    year: int,
    state: str | None,
    event_types: list[str] | None,
) -> bytes:
    """Resolve the NCEI CSV URL, download, filter, serialize to FlatGeobuf."""
    url = _resolve_csv_url(year)
    logger.info("fetch_storm_events_db: resolved URL=%s", url)
    gz_bytes = _download_csv_gz(url)
    logger.info(
        "fetch_storm_events_db: downloaded %d gzip bytes", len(gz_bytes)
    )
    return _parse_filter_and_serialize(gz_bytes, state, event_types)


# ---------------------------------------------------------------------------
# Registered atomic tool.
# ---------------------------------------------------------------------------


@register_tool(_METADATA)
def fetch_storm_events_db(
    year: int,
    state: str | None = None,
    event_types: list[str] | None = None,
) -> LayerURI:
    """Fetch NOAA Storm Events Database events as a point FlatGeobuf.

    Use this when: the agent needs historical storm-event points for spatial
    overlay, narrative context, or comparison against modeled hazards. For
    example: "what storm events affected Florida in 2022?" or "where did
    hurricanes touch down in 2022?" The NOAA Storm Events Database is the
    authoritative US event catalog covering tornadoes, hurricanes, hail,
    flooding, winter storms, and 40+ other event categories (1950-present;
    Tier-1 free, no API key).

    Do NOT use this for: real-time / current storm tracking (use
    ``fetch_hurricane_track`` for NHC ATCF, or ``fetch_nws_event`` for active
    NWS alerts); detailed damage assessment beyond episode narratives (the
    DB carries summary damage strings, not parcel-level loss data); meteorology
    outside the US (Storm Events is US + territories only).

    Params:
        year: integer year in [1950, 2100]. Earlier years are sparse;
            modern coverage is comprehensive from ~1996 onward.
        state: optional ISO 2-letter US state code (e.g. ``"FL"``,
            ``"TX"``). When omitted, all states/territories are returned.
        event_types: optional list of NOAA event type names to filter on
            (case-insensitive). Examples: ``["Hurricane"]``,
            ``["Tornado", "Hail"]``, ``["Flash Flood"]``. When omitted, all
            event types are returned.

    Returns:
        A ``LayerURI`` pointing at a FlatGeobuf in the cache bucket:
        ``gs://grace-2-hazard-prod-cache/cache/static-30d/storm_events/<key>.fgb``
        with point geometry (one feature per event, located at
        ``BEGIN_LAT``/``BEGIN_LON``) in EPSG:4326. Properties include
        ``EVENT_ID``, ``EVENT_TYPE``, ``STATE``, ``BEGIN_DATE_TIME``,
        ``END_DATE_TIME``, ``INJURIES_DIRECT``, ``DAMAGE_PROPERTY``,
        ``EPISODE_NARRATIVE``. ``layer_type="vector"``, ``role="context"``,
        ``units=None``.

    FR-CE-8: Routed through ``read_through`` so identical
    ``(year, state, event_types)`` calls reuse the cached FlatGeobuf.
    Cache key is the SHA-256 of canonical-json ``(year, state.upper(),
    event_types sorted+upper)``.
    """
    _validate_inputs(year, state, event_types)

    # Normalize for cache-key stability: state upper, event_types sorted-upper.
    state_norm = state.upper() if state else None
    event_types_norm = (
        sorted({e.upper() for e in event_types}) if event_types else None
    )

    params: dict[str, Any] = {
        "year": year,
        "state": state_norm,
        "event_types": event_types_norm,
    }

    result = read_through(
        metadata=_METADATA,
        params=params,
        ext="fgb",
        fetch_fn=lambda: _fetch_storm_events_bytes(year, state, event_types),
    )
    assert result.uri is not None, (
        "fetch_storm_events_db is cacheable; uri must be set by read_through"
    )

    # Build a human-friendly layer name reflecting the filter.
    filter_bits: list[str] = []
    if state_norm:
        filter_bits.append(state_norm)
    if event_types_norm:
        filter_bits.append(", ".join(event_types_norm[:3]))
    filter_str = f" — {' / '.join(filter_bits)}" if filter_bits else ""

    # layer_id seed: short content hash of (year, state, event_types) — stable.
    seed_payload = json.dumps(
        {"y": year, "s": state_norm, "e": event_types_norm},
        sort_keys=True,
        separators=(",", ":"),
    )
    layer_seed = hashlib.sha256(seed_payload.encode("utf-8")).hexdigest()[:8]

    return LayerURI(
        layer_id=f"storm-events-{year}-{layer_seed}",
        name=f"NOAA Storm Events {year}{filter_str}",
        layer_type="vector",
        uri=result.uri,
        style_preset="storm_events",
        role="context",
        units=None,
    )
