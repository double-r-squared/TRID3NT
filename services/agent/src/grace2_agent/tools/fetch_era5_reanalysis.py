"""``fetch_era5_reanalysis`` atomic tool — Copernicus ERA5 reanalysis Tier-2 fetcher (job-0131).

Wraps the Copernicus Climate Data Store (CDS) ``cdsapi`` client to retrieve
the ERA5 hourly reanalysis (``reanalysis-era5-single-levels``) for a single
variable over a bbox and date range, converts the returned NetCDF into a
CRS-tagged COG (one band per hourly timestep, mean across the window), and
routes it through the FR-DC cache shim.

Research-validated as the **compound-flood global substrate** (Bates et al.,
NHESS 2023) — ERA5 winds + precip + significant wave height + storm surge
forcing are how the global SFINCS / GeoFLOOD compound-flood literature builds
boundary conditions outside of agency-instrumented basins. Sprint-12-mega
Wave 2 lands this as a Tier-2 substrate fetcher; downstream composers
(``model_compound_flood_global`` etc.) consume the LayerURI.

Supported variables (single-level, hourly; ERA5 single-levels CDS dataset):
    "10m_u_component_of_wind"        m s-1     wind east-component @ 10m
    "10m_v_component_of_wind"        m s-1     wind north-component @ 10m
    "2m_temperature"                 K         air temperature @ 2m
    "total_precipitation"            m         hourly total precip (cumulative
                                               per hour, native units METRES)
    "runoff"                         m         surface runoff (native METRES)
    "significant_height_of_combined_wind_waves_and_swell"
                                     m         significant wave height

API surface (verified 2026-06-08):

    cdsapi.Client(url, key).retrieve(
        "reanalysis-era5-single-levels",
        {
            "product_type": "reanalysis",
            "variable": "<variable>",
            "year":  ["YYYY", ...],
            "month": ["MM",   ...],
            "day":   ["DD",   ...],
            "time":  ["HH:00", ...],
            "area":  [N, W, S, E],    # CDS bbox convention!
            "format": "netcdf",
        },
        out_path,
    )

The retrieve call is **blocking** from the caller's perspective but **async
on the CDS side**: the server queues the request, runs it, then streams the
result back. The cdsapi client transparently polls until the job completes
(default poll interval ~1s, no caller-side timeout). We wrap retrieve in a
``concurrent.futures`` watchdog with a 5-minute wall-clock budget per the
kickoff so a stuck queue surfaces as ``ERA5UpstreamError`` instead of
hanging the agent process.

API-key resolution (Tier-2 secret handling per kickoff):

1. Explicit ``api_key`` kwarg (live test path, dev override).
2. ``secret_ref`` (a ``SecretRecord`` per ``grace2_contracts.secrets``)
   → ``Persistence.get_secret_value()`` (the production per-Case path
   landed by Wave 2 sibling job-0124).
3. ``GRACE2_COPERNICUS_CDS_API_KEY`` env var (local dev convenience).
4. ``~/.cdsapirc`` if present — the cdsapi library's own default lookup;
   this is what the live test on a developer machine uses.

If none of the four resolve a key, the tool raises ``ERA5MissingKeyError``
(retryable=False) and the agent surface routes a "needs a key" message to
the user via the secrets panel (sprint-12 Case-UX).

FR-TA-2 atomic tool. FR-CE-8 / FR-DC-3/4: routed through ``read_through``
with ``ttl_class="static-30d"`` — ERA5 reanalysis is historical and stable
(month-old data is finalised; ERA5T preliminary data also locks in after
~3 months). A 30-day cache class is conservative; in practice once a
(variable, bbox, date-range) request lands it is byte-stable for years.

Cache key composition (per audit.md): the cache shim hashes
``(variable, bbox-6dp, start_date, end_date)``. The cache key intentionally
does NOT include the api_key — the underlying ERA5 grid is the same for
every caller (FR-DC-4 dedup).

Output COG schema:
    Driver: COG, EPSG:4326 (ERA5 native projection)
    Bands:  1 (window-mean across the requested date range)
    Dtype:  float32
    Nodata: NaN
    Tags:
        units, source="ERA5_reanalysis-era5-single-levels",
        variable, start_date, end_date, tool="fetch_era5_reanalysis"

CRS: EPSG:4326. ERA5 native resolution is 0.25° (≈27 km at the equator).

Geographic-correctness gate (job-0086 codified lesson): we tag the output
COG with the exact pixel-aligned bounds derived from the requested bbox so
``rasterio.open(uri).bounds`` returns a window inside the requested area.

Payload estimation (per audit.md):
    ~0.5 MB per variable per day per 1° square at 0.25° native res.

``supports_global_query=True`` — ERA5 is global, so passing
``bbox=(-180,-90,180,90)`` is a legitimate (if expensive) call.
"""

from __future__ import annotations

import datetime as _dt
import logging
import math
import os
import tempfile
from typing import Any

from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from .cache import read_through

__all__ = ["fetch_era5_reanalysis"]

logger = logging.getLogger("grace2_agent.tools.fetch_era5_reanalysis")


# ---------------------------------------------------------------------------
# Error types (FR-AS-11 typed-error surface).
# ---------------------------------------------------------------------------


class ERA5Error(RuntimeError):
    """Base class for fetch_era5_reanalysis failures."""

    error_code: str = "ERA5_ERROR"
    retryable: bool = True


class ERA5InputError(ERA5Error):
    """Bad inputs (unknown variable, malformed bbox / dates)."""

    error_code = "ERA5_INPUT_ERROR"
    retryable = False


class ERA5UpstreamError(ERA5Error):
    """CDS API returned an error or the retrieve timed out / network failed."""

    error_code = "ERA5_UPSTREAM_ERROR"
    retryable = True


class ERA5MissingKeyError(ERA5Error):
    """No API key resolved via any of the four lookup paths.

    Raised BEFORE any network call. The agent surface uses this to prompt
    the user to add a Copernicus CDS key via the secrets panel.
    """

    error_code = "ERA5_MISSING_KEY"
    retryable = False


class ERA5AuthError(ERA5Error):
    """CDS API rejected the key (invalid / revoked / not licensed)."""

    error_code = "ERA5_AUTH_ERROR"
    retryable = False


class ERA5EmptyError(ERA5Error):
    """The retrieved NetCDF contained no finite pixels in the requested bbox."""

    error_code = "ERA5_EMPTY"
    retryable = False


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

# CDS dataset id (single-level hourly reanalysis).
_CDS_DATASET = "reanalysis-era5-single-levels"

# Default CDS API endpoint URL. cdsapi >= 0.7 routes to the new CDS-Beta
# (cds-beta.climate.copernicus.eu) but the legacy URL still works.
_DEFAULT_CDS_URL = "https://cds.climate.copernicus.eu/api"

# Wall-clock budget for the CDS retrieve call (queue + run + stream).
# Per audit.md: poll up to 5 min for completion.
_RETRIEVE_TIMEOUT_S = 300

# Allowed single-level variable names — matches the audit.md kickoff list.
_ALLOWED_VARIABLES: frozenset[str] = frozenset(
    {
        "10m_u_component_of_wind",
        "10m_v_component_of_wind",
        "2m_temperature",
        "total_precipitation",
        "runoff",
        "significant_height_of_combined_wind_waves_and_swell",
    }
)

# Native units per variable (used to tag the output COG and surface in
# narration). ERA5 single-levels documentation.
_VARIABLE_UNITS: dict[str, str] = {
    "10m_u_component_of_wind": "m s-1",
    "10m_v_component_of_wind": "m s-1",
    "2m_temperature": "K",
    "total_precipitation": "m",
    "runoff": "m",
    "significant_height_of_combined_wind_waves_and_swell": "m",
}

# Sanity cap on date range — refuse multi-year ad-hoc retrievals that would
# blow through the CDS quota. A 1-year window already produces ~365 hourly
# timesteps * variable * grid. Composers wanting a multi-year climatology
# should call this tool in a loop and aggregate.
_MAX_DATE_RANGE_DAYS = 366


# ---------------------------------------------------------------------------
# AtomicToolMetadata — registered once at import time.
# ---------------------------------------------------------------------------

_METADATA = AtomicToolMetadata(
    name="fetch_era5_reanalysis",
    ttl_class="static-30d",
    source_class="era5",
    cacheable=True,
)


# ---------------------------------------------------------------------------
# Payload-MB estimator (Wave 1.5 chat-warning system).
# ---------------------------------------------------------------------------


def estimate_payload_mb(
    bbox: tuple[float, float, float, float] | None = None,
    variable: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    **_kw: Any,
) -> float:
    """Estimate output COG size in MB for a given call (Wave 1.5 surface).

    Per audit.md: ~0.5 MB per variable per day per 1° square at 0.25° native
    res. We treat ``bbox=None`` as global (360° × 180°).

    Used by the tool-payload-warning envelope (see
    ``AtomicToolMetadata.payload_mb_estimator_name``). Wrong answers are
    cheap (a chat warning instead of a hard block); we err on the high
    side so the user sees the warning rather than a surprise download.
    """
    if bbox is None:
        sq_deg = 360.0 * 180.0
    else:
        try:
            west, south, east, north = bbox
            sq_deg = max(0.0, (east - west)) * max(0.0, (north - south))
        except (TypeError, ValueError):
            sq_deg = 1.0

    if not start_date or not end_date:
        n_days = 1
    else:
        try:
            d0 = _dt.date.fromisoformat(start_date)
            d1 = _dt.date.fromisoformat(end_date)
            n_days = max(1, (d1 - d0).days + 1)
        except ValueError:
            n_days = 1

    # 0.5 MB / variable / day / 1° square per audit.md.
    return 0.5 * float(n_days) * float(max(1.0, sq_deg))


# ---------------------------------------------------------------------------
# bbox helpers.
# ---------------------------------------------------------------------------


def _validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    """Raise ``ERA5InputError`` if bbox is invalid."""
    if len(bbox) != 4:
        raise ERA5InputError(
            f"bbox must be (west, south, east, north); got {bbox!r}"
        )
    west, south, east, north = bbox
    if not all(math.isfinite(v) for v in bbox):
        raise ERA5InputError(f"bbox contains non-finite values: {bbox!r}")
    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        raise ERA5InputError(f"bbox lon out of [-180,180]: {bbox!r}")
    if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
        raise ERA5InputError(f"bbox lat out of [-90,90]: {bbox!r}")
    if west >= east or south >= north:
        raise ERA5InputError(
            f"bbox is degenerate (west < east, south < north required): {bbox!r}"
        )


def _round_bbox_to_6dp(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Round bbox coords to 6dp (~0.1m) for cache-key stability."""
    return tuple(round(v, 6) for v in bbox)  # type: ignore[return-value]


def _validate_variable(variable: str) -> None:
    """Raise ``ERA5InputError`` for unsupported variable names."""
    if not isinstance(variable, str):
        raise ERA5InputError(
            f"variable must be a str; got {type(variable).__name__}"
        )
    if variable not in _ALLOWED_VARIABLES:
        raise ERA5InputError(
            f"unsupported ERA5 variable {variable!r}; allowed: "
            f"{sorted(_ALLOWED_VARIABLES)}"
        )


def _parse_iso_date(s: str, *, field: str) -> _dt.date:
    if not isinstance(s, str):
        raise ERA5InputError(f"{field} must be ISO-8601 YYYY-MM-DD; got {s!r}")
    try:
        return _dt.date.fromisoformat(s)
    except ValueError as exc:
        raise ERA5InputError(
            f"{field}={s!r} is not a valid ISO date (YYYY-MM-DD): {exc}"
        ) from exc


def _validate_date_range(start_date: str, end_date: str) -> tuple[_dt.date, _dt.date]:
    """Validate ISO dates + ordering + reasonable window."""
    d0 = _parse_iso_date(start_date, field="start_date")
    d1 = _parse_iso_date(end_date, field="end_date")
    if d0 > d1:
        raise ERA5InputError(
            f"start_date must be <= end_date; got start={d0}, end={d1}"
        )
    # ERA5 covers 1940-01-01 onward. Reject obvious typos.
    if d0.year < 1940 or d1.year > _dt.date.today().year + 1:
        raise ERA5InputError(
            f"date range [{d0}, {d1}] outside ERA5 coverage (1940 → present)"
        )
    n_days = (d1 - d0).days + 1
    if n_days > _MAX_DATE_RANGE_DAYS:
        raise ERA5InputError(
            f"date range {n_days} days exceeds hard cap "
            f"{_MAX_DATE_RANGE_DAYS}; call in chunks and aggregate"
        )
    return d0, d1


# ---------------------------------------------------------------------------
# API-key resolution (FR-AS-11 + §F.3 per-Case secret path).
# ---------------------------------------------------------------------------


def _resolve_api_key(
    api_key: str | None,
    secret_ref: Any | None,
) -> str | None:
    """Return the live CDS API key from one of the four lookup paths.

    Priority (per audit.md):

    1. Explicit ``api_key`` kwarg.
    2. ``secret_ref`` (a ``SecretRecord``) → ``Persistence.get_secret_value``
       (the per-Case path landed by Wave 2 sibling job-0124).
    3. ``GRACE2_COPERNICUS_CDS_API_KEY`` env var.
    4. ``None`` — cdsapi falls back to ``~/.cdsapirc`` on instantiation.

    A return value of ``None`` means "let cdsapi find its own key via the
    library's default discovery path (``~/.cdsapirc``)". We do NOT raise
    ``ERA5MissingKeyError`` for the None case because the live developer
    workflow stores credentials in ``~/.cdsapirc``; the cdsapi Client
    constructor will raise its own diagnostic if neither the explicit key
    nor the rc file is present. We catch that and re-raise as
    ``ERA5MissingKeyError`` from the call site.
    """
    # 1. Explicit kwarg.
    if api_key:
        return api_key

    # 2. secret_ref via Persistence (lazy import to avoid MCP startup cost).
    if secret_ref is not None:
        try:
            return _materialize_secret(secret_ref)
        except Exception as exc:  # noqa: BLE001
            raise ERA5MissingKeyError(
                f"secret_ref lookup failed: {exc}"
            ) from exc

    # 3. Env var fallback.
    env_key = os.environ.get("GRACE2_COPERNICUS_CDS_API_KEY")
    if env_key:
        return env_key

    # 4. None → cdsapi finds ~/.cdsapirc itself.
    return None


def _materialize_secret(secret_ref: Any) -> str:
    """Bridge ``Persistence.get_secret_value`` (async) into a sync caller.

    Mirrors the fetch_ebird_observations pattern: lazy import of Persistence,
    sync-bridge for async coroutine, and a test-mock shortcut for plain
    strings.
    """
    if isinstance(secret_ref, str):
        return secret_ref

    persistence = _get_persistence_for_secrets()
    if persistence is None:
        raise ERA5MissingKeyError(
            "Persistence not bound; cannot resolve secret_ref. "
            "Pass api_key=... explicitly in this context."
        )

    coro = persistence.get_secret_value(secret_ref)
    return _run_coro_sync(coro)


_PERSISTENCE_FOR_SECRETS: Any | None = None


def set_persistence_for_secrets(persistence: Any | None) -> None:
    """Bind the agent-service ``Persistence`` for secret materialization.

    Mirrors the eBird Tier-2 binding (``fetch_ebird_observations`` job-0128).
    Called once at startup by the agent service; tests inject a mock.
    """
    global _PERSISTENCE_FOR_SECRETS
    _PERSISTENCE_FOR_SECRETS = persistence


def _get_persistence_for_secrets() -> Any | None:
    return _PERSISTENCE_FOR_SECRETS


def _run_coro_sync(coro: Any) -> Any:
    """Run an ``asyncio`` coroutine and return its result from sync context."""
    import asyncio
    import threading

    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False

    if not running:
        return asyncio.run(coro)

    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            result_box["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # noqa: BLE001
            error_box["err"] = exc
        finally:
            loop.close()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if "err" in error_box:
        raise error_box["err"]
    return result_box["value"]


# ---------------------------------------------------------------------------
# CDS retrieve → NetCDF (with timeout watchdog).
# ---------------------------------------------------------------------------


def _build_cds_request(
    variable: str,
    bbox: tuple[float, float, float, float],
    d0: _dt.date,
    d1: _dt.date,
) -> dict[str, Any]:
    """Construct the CDS retrieve-request dict for a (variable, bbox, range).

    CDS expects:
    - ``area = [N, W, S, E]`` (NOT west/south/east/north!).
    - ``year`` / ``month`` / ``day`` as zero-padded string lists. The CDS
      docs allow either every-day-explicit OR a year-month-all-days form;
      we pin to the explicit per-day list so the request shape is
      deterministic across date ranges.
    - ``time = ["HH:00"]`` hourly slots, zero-padded.
    """
    west, south, east, north = bbox

    # Days spanned, expressed as a (year, month, day) explicit list.
    years: set[str] = set()
    months: set[str] = set()
    days: set[str] = set()
    cur = d0
    one = _dt.timedelta(days=1)
    while cur <= d1:
        years.add(f"{cur.year:04d}")
        months.add(f"{cur.month:02d}")
        days.add(f"{cur.day:02d}")
        cur += one

    # All 24 hourly slots.
    hours = [f"{h:02d}:00" for h in range(24)]

    return {
        "product_type": "reanalysis",
        "variable": variable,
        "year": sorted(years),
        "month": sorted(months),
        "day": sorted(days),
        "time": hours,
        "area": [north, west, south, east],  # N, W, S, E — CDS convention
        "format": "netcdf",
    }


def _cds_retrieve_with_timeout(
    api_url: str,
    api_key: str | None,
    request: dict[str, Any],
    out_path: str,
    timeout_s: int = _RETRIEVE_TIMEOUT_S,
) -> None:
    """Call cdsapi.Client.retrieve under a wall-clock timeout watchdog.

    cdsapi has no native ``timeout`` parameter — the library polls the CDS
    queue every ~1s until the job completes (or fails) and only then
    streams the file. We spawn the retrieve in a worker thread and join
    with a deadline; on timeout we raise ``ERA5UpstreamError`` (retryable).

    Note: a timed-out request leaves an orphan CDS job server-side; the
    client cannot cancel it. The user will see it in their CDS dashboard
    queue history. Documented in the docstring; surfaced as
    ``OQ-0131-CDS-ORPHAN-JOB``.
    """
    import threading

    try:
        import cdsapi  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ERA5UpstreamError(
            f"cdsapi package not available: {exc}"
        ) from exc

    err_box: dict[str, BaseException] = {}

    def _do_retrieve() -> None:
        try:
            # cdsapi.Client kwargs:
            #   url, key, verify, timeout, quiet, debug, full_stack,
            #   delete, retry_max, sleep_max, wait_until_complete
            client_kwargs: dict[str, Any] = {"quiet": True}
            if api_url:
                client_kwargs["url"] = api_url
            if api_key:
                client_kwargs["key"] = api_key
            # If api_key is None, cdsapi falls back to ~/.cdsapirc on its own.
            client = cdsapi.Client(**client_kwargs)
            client.retrieve(_CDS_DATASET, request, out_path)
        except BaseException as exc:  # noqa: BLE001
            err_box["err"] = exc

    t = threading.Thread(target=_do_retrieve, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        raise ERA5UpstreamError(
            f"CDS retrieve exceeded {timeout_s}s wall-clock budget; "
            f"the CDS job may still be queued server-side."
        )
    if "err" in err_box:
        exc = err_box["err"]
        msg = str(exc)
        # Distinguish auth from generic upstream — cdsapi raises
        # ``Exception`` with messages mentioning "401" / "403" / "key" /
        # "Authentication" / "User not authenticated".
        low = msg.lower()
        if any(tok in low for tok in ("401", "403", "authentication", "unauthorized")):
            raise ERA5AuthError(
                f"CDS API rejected the key: {msg[:200]}"
            ) from exc
        if "no api key" in low or "missing" in low and "key" in low:
            raise ERA5MissingKeyError(
                f"CDS API key not available: {msg[:200]}"
            ) from exc
        raise ERA5UpstreamError(
            f"CDS retrieve failed: {msg[:200]}"
        ) from exc


# ---------------------------------------------------------------------------
# NetCDF → COG conversion.
# ---------------------------------------------------------------------------


def _netcdf_to_cog_bytes(
    nc_path: str,
    variable: str,
    bbox: tuple[float, float, float, float],
) -> bytes:
    """Open the CDS-returned NetCDF, mean across timesteps, write a COG.

    Returns COG bytes (float32, EPSG:4326). The output has one band carrying
    the time-mean of the requested variable across the date range.

    Per audit.md, the kickoff returns "GeoTIFF" — we write COG which is a
    GeoTIFF profile (and the canonical raster output across the rest of the
    GRACE-2 atomic-tool set: HRSL, MTBS, LANDFIRE, NLCD).

    Geographic-correctness gate (job-0086): we clip the output to the
    requested bbox after reprojection (ERA5 ships on a 0.25° grid with
    longitudes 0..360 OR -180..180 depending on the variable family; we
    normalize to -180..180 with rioxarray before clipping).

    Raises:
        ``ERA5UpstreamError``: NetCDF open / xarray read / COG write failure.
        ``ERA5EmptyError``: bbox falls outside the variable's coverage.
    """
    try:
        import numpy as np
        import rioxarray  # noqa: F401 — registers .rio accessor on DataArrays
        import xarray as xr
    except ImportError as exc:
        raise ERA5UpstreamError(
            f"xarray / rioxarray / numpy not available: {exc}"
        ) from exc

    try:
        ds = xr.open_dataset(nc_path, engine="netcdf4", chunks=None)
    except Exception as exc:  # noqa: BLE001
        # netcdf4 may not be available; try the default engine.
        try:
            ds = xr.open_dataset(nc_path, chunks=None)
        except Exception as exc2:  # noqa: BLE001
            raise ERA5UpstreamError(
                f"xarray could not open CDS NetCDF {nc_path}: {exc2} "
                f"(netcdf4-engine error: {exc})"
            ) from exc2

    try:
        # CDS variable short names differ from the long-name request. The
        # mapping is documented in ERA5 single-levels parameter db; we
        # discover the data variable by exclusion (drop coordinate vars).
        data_vars = [v for v in ds.data_vars if v not in ds.coords]
        if not data_vars:
            raise ERA5UpstreamError(
                f"CDS NetCDF carried no data variables; got {list(ds.variables)}"
            )

        # Prefer the variable whose long_name attribute mentions the
        # requested ERA5 variable name; fall back to the first data_var.
        chosen = data_vars[0]
        target_token = variable.replace("_", " ").lower()
        for v in data_vars:
            ln = ds[v].attrs.get("long_name", "").lower()
            if target_token in ln:
                chosen = v
                break

        da = ds[chosen]

        # Average across all non-spatial dims (time, expver, etc.) so we
        # emit a single 2D band. ERA5T data often ships a second "expver"
        # dim (1 = ERA5, 5 = ERA5T preliminary); a simple mean across this
        # axis is documented as the "merge" path in the ERA5 user guide.
        keep_dims = {"latitude", "longitude", "lat", "lon", "y", "x"}
        reduce_dims = [d for d in da.dims if d not in keep_dims]
        if reduce_dims:
            da = da.mean(dim=reduce_dims, skipna=True, keep_attrs=True)

        # Standardize coord names to latitude/longitude if shipped as lat/lon.
        rename_map: dict[str, str] = {}
        if "lat" in da.dims and "latitude" not in da.dims:
            rename_map["lat"] = "latitude"
        if "lon" in da.dims and "longitude" not in da.dims:
            rename_map["lon"] = "longitude"
        if rename_map:
            da = da.rename(rename_map)

        # Set CRS via rioxarray. ERA5 ships on EPSG:4326.
        da = da.rio.write_crs("EPSG:4326")
        # ERA5 latitudes are typically descending (90 → -90); rioxarray's
        # clip_box wants standard orientation. Sort latitude ascending if
        # required.
        if "latitude" in da.dims and len(da["latitude"]) > 1:
            lat_vals = da["latitude"].values
            if lat_vals[0] > lat_vals[-1]:
                da = da.sortby("latitude")

        # ERA5 longitudes may be 0..360. Convert to -180..180 if needed so
        # the bbox clip works for both (-bbox) and (+bbox) requests.
        if "longitude" in da.dims:
            lon_vals = da["longitude"].values
            if lon_vals.max() > 180.0:
                da = da.assign_coords(
                    longitude=(((da["longitude"] + 180) % 360) - 180)
                )
                da = da.sortby("longitude")

        # Clip to requested bbox (geographic-correctness gate).
        west, south, east, north = bbox
        try:
            da = da.rio.clip_box(
                minx=west, miny=south, maxx=east, maxy=north, crs="EPSG:4326"
            )
        except Exception as exc:  # noqa: BLE001
            raise ERA5UpstreamError(
                f"rioxarray clip_box to bbox={bbox} failed: {exc}"
            ) from exc

        if da.size == 0:
            raise ERA5EmptyError(
                f"bbox={bbox} produced an empty ERA5 window after clip"
            )

        arr = np.asarray(da.values, dtype=np.float32)
        if not np.isfinite(arr).any():
            raise ERA5EmptyError(
                f"bbox={bbox} produced no finite ERA5 pixels (all-NaN window)"
            )

        # Write COG via rioxarray.
        out_fd, out_path = tempfile.mkstemp(
            suffix=".tif", prefix="grace2_era5_"
        )
        os.close(out_fd)
        try:
            da_out = da.astype("float32")
            # Tag metadata.
            da_out.attrs["units"] = _VARIABLE_UNITS.get(
                variable, da.attrs.get("units", "")
            )
            da_out.attrs["source"] = "ERA5_reanalysis-era5-single-levels"
            da_out.attrs["variable"] = variable
            da_out.attrs["tool"] = "fetch_era5_reanalysis"
            try:
                da_out.rio.to_raster(
                    out_path,
                    driver="COG",
                    dtype="float32",
                    compress="DEFLATE",
                    nodata=float("nan"),
                )
            except Exception:  # noqa: BLE001 — fall back to GTiff if COG fails
                da_out.rio.to_raster(
                    out_path,
                    driver="GTiff",
                    dtype="float32",
                    compress="DEFLATE",
                    nodata=float("nan"),
                )

            with open(out_path, "rb") as f:
                cog_bytes = f.read()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

        logger.info(
            "fetch_era5_reanalysis: wrote %d-byte COG (variable=%s, "
            "min=%.4f, max=%.4f, mean=%.4f)",
            len(cog_bytes),
            variable,
            float(np.nanmin(arr)),
            float(np.nanmax(arr)),
            float(np.nanmean(arr)),
        )
        return cog_bytes
    finally:
        try:
            ds.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Fetch function (passed to read_through).
# ---------------------------------------------------------------------------


def _fetch_era5_bytes(
    variable: str,
    bbox: tuple[float, float, float, float],
    d0: _dt.date,
    d1: _dt.date,
    api_key: str | None,
) -> bytes:
    """End-to-end: CDS retrieve → NetCDF → COG bytes."""
    request = _build_cds_request(variable, bbox, d0, d1)

    nc_fd, nc_path = tempfile.mkstemp(
        suffix=".nc", prefix="grace2_era5_cds_"
    )
    os.close(nc_fd)
    try:
        _cds_retrieve_with_timeout(
            api_url=_DEFAULT_CDS_URL,
            api_key=api_key,
            request=request,
            out_path=nc_path,
        )
        return _netcdf_to_cog_bytes(nc_path, variable, bbox)
    finally:
        try:
            os.unlink(nc_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Registered atomic tool.
# ---------------------------------------------------------------------------


@register_tool(
    _METADATA,
    supports_global_query=True,
    payload_mb_estimator_name="estimate_payload_mb",
)
def fetch_era5_reanalysis(
    bbox: tuple[float, float, float, float],
    variable: str,
    start_date: str,
    end_date: str,
    api_key: str | None = None,
    secret_ref: Any | None = None,
) -> LayerURI:
    """Copernicus ERA5 reanalysis Tier-2 fetcher (single-level hourly).

    Use this when: the agent needs a globally-consistent meteorological /
    oceanographic reanalysis variable as gridded raster — e.g. wind forcing
    to drive SFINCS storm surge boundaries outside the US gauge network,
    total precipitation as a global substitute for MRMS in non-CONUS basins,
    significant wave height for the compound-flood literature's compound
    hazard substrate, or surface runoff for global hydrological setup.
    Wraps the Copernicus Climate Data Store (CDS) ``cdsapi`` client and
    returns a CRS-tagged COG carrying the time-mean of the requested
    variable across the date range, clipped to the requested bbox. ERA5 is
    research-validated as the global compound-flood substrate (NHESS 2023,
    Bates et al.) — the canonical choice when an agency-instrumented
    fetcher (NHC ATCF, NWS, CO-OPS, MRMS) doesn't cover the area.

    Do NOT use this for: real-time / live forecast data — ERA5 reanalysis
    is historical only (latest data lags 5 days for ERA5T preliminary,
    3 months for finalised ERA5). Use NWS Alerts / GOES / NEXRAD / MRMS
    for live CONUS-side; ECMWF AIFS / IFS for global forecast (different
    tool). Sub-hourly timesteps — ERA5 is hourly; for ten-minute or
    one-minute timesteps use IMERG / GPM. CONUS-side high-resolution
    precipitation — use ``fetch_mrms_qpe`` instead (1 km gauge-corrected
    vs ERA5's 27 km). Vector data (use the appropriate fetcher per
    domain).

    ERA5 requires a free Copernicus CDS API key (registration at
    ``https://cds.climate.copernicus.eu/user/register``, then "API key"
    on your user profile). The tool resolves the key in this order:
    (1) explicit ``api_key=`` kwarg, (2) ``secret_ref=`` per-Case secret
    via ``Persistence.get_secret_value`` (production path),
    (3) ``GRACE2_COPERNICUS_CDS_API_KEY`` env var, (4) fallback to the
    cdsapi library's own ``~/.cdsapirc`` discovery. If none of the four
    paths resolve a key, raises ``ERA5MissingKeyError`` BEFORE any
    network call — the agent surface uses this to route a "needs a key"
    message via the secrets panel.

    CDS jobs are queued server-side; the cdsapi client polls until the
    job completes. We wrap the retrieve call in a 5-minute wall-clock
    timeout; a stuck queue surfaces as ``ERA5UpstreamError`` (retryable).

    Params:
        bbox: ``(west, south, east, north)`` in EPSG:4326 (WGS84 decimal
            degrees). Passing the global bbox ``(-180,-90,180,90)`` is a
            legitimate (but expensive) call — this tool declares
            ``supports_global_query=True``.
        variable: one of: ``"10m_u_component_of_wind"``,
            ``"10m_v_component_of_wind"``, ``"2m_temperature"``,
            ``"total_precipitation"``, ``"runoff"``,
            ``"significant_height_of_combined_wind_waves_and_swell"``.
        start_date: ISO YYYY-MM-DD; inclusive.
        end_date: ISO YYYY-MM-DD; inclusive. Hard cap 366 days from start.
        api_key: optional explicit CDS API key.
        secret_ref: optional ``SecretRecord`` (from per-Case secrets panel),
            resolved via ``Persistence.get_secret_value`` at invocation time.

    Returns:
        A ``LayerURI`` pointing at a COG in the cache bucket:
        ``gs://grace-2-hazard-prod-cache/cache/static-30d/era5/<key>.tif``
        carrying the time-mean of the requested variable across the date
        range, clipped to the requested bbox. ``layer_type="raster"``,
        ``role="primary"``, ``units`` per the variable
        (``"m s-1"`` for wind, ``"K"`` for temperature, ``"m"`` for precip
        / runoff / wave height).

    Raises:
        ``ERA5MissingKeyError``: no API key resolved (any of the 4 paths).
        ``ERA5AuthError``: CDS rejected the key.
        ``ERA5InputError``: bad bbox / variable / dates.
        ``ERA5EmptyError``: bbox falls outside the variable's coverage.
        ``ERA5UpstreamError``: CDS retrieve timed out or failed (retryable).

    FR-CE-8: Routed through ``read_through`` with ``ttl_class="static-30d"``
    so identical ``(variable, bbox, start_date, end_date)`` calls reuse
    the cached COG. The cache key intentionally does NOT include the
    api_key — the underlying ERA5 grid does not vary by caller (FR-DC-4
    dedup).
    """
    # ---- Input validation ----
    _validate_bbox(bbox)
    _validate_variable(variable)
    d0, d1 = _validate_date_range(start_date, end_date)

    # ---- API-key resolution (pre-network; cheap fail) ----
    resolved_key = _resolve_api_key(api_key=api_key, secret_ref=secret_ref)
    # NOTE: resolved_key may be None — that is intentional. cdsapi falls
    # back to ~/.cdsapirc; the auth error surfaces from the call site.

    # ---- Cache-key params (key omits api_key by design) ----
    q_bbox = _round_bbox_to_6dp(bbox)
    params: dict[str, Any] = {
        "variable": variable,
        "bbox": list(q_bbox),
        "start_date": d0.isoformat(),
        "end_date": d1.isoformat(),
    }

    result = read_through(
        metadata=_METADATA,
        params=params,
        ext="tif",
        fetch_fn=lambda: _fetch_era5_bytes(
            variable=variable,
            bbox=q_bbox,
            d0=d0,
            d1=d1,
            api_key=resolved_key,
        ),
    )
    assert result.uri is not None, (
        "fetch_era5_reanalysis is cacheable; uri must be set by read_through"
    )

    return LayerURI(
        layer_id=(
            f"era5-{variable.replace('_', '-')}-"
            f"{d0.isoformat()}-{d1.isoformat()}-"
            f"{q_bbox[0]:.4f}-{q_bbox[1]:.4f}"
        ),
        name=(
            f"ERA5 Reanalysis — {variable.replace('_', ' ').title()} "
            f"({d0.isoformat()} → {d1.isoformat()})"
        ),
        layer_type="raster",
        uri=result.uri,
        style_preset=f"era5_{variable}",
        role="primary",
        units=_VARIABLE_UNITS.get(variable),
    )
