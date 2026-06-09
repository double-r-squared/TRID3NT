"""Atomic tool ``compute_impervious_surface`` — NLCD impervious-fraction raster (job-0095, FR-CE-8, FR-DC).

This module registers one atomic tool that computes an impervious-surface
fraction raster (float32, range 0.0-1.0) from either:

- the **NLCD Impervious Surface** product (a separate USGS product whose pixel
  values are percent impervious, integer 0-100) — direct read + scale by 1/100;
- or the **NLCD Land Cover** product (the canonical NLCD class-code raster) —
  derive impervious fraction from developed-class membership using the standard
  USGS NLCD developed-density mapping:

      21 = Developed, Open Space         → 0.0
      22 = Developed, Low Intensity      → 0.3
      23 = Developed, Medium Intensity   → 0.6
      24 = Developed, High Intensity     → 0.9
      anything else                      → 0.0

The output is a single-band Float32 GeoTIFF in the same CRS and grid as the
input, with nodata = NaN. ``role="context"``, ``units=None`` (the values are
dimensionless fractions).

**Auto-detection of input product** is by filename heuristic + rasterio tags
inspection: a URI whose path component contains ``impervious`` (case-insensitive)
or whose raster tags include ``NLCD_Impervious_Surface`` is treated as the
impervious product; otherwise the input is assumed to be NLCD landcover and
the dev-class mapping is applied.

**Cache key** is derived from ``(landcover_uri, bbox)`` — both materially affect
output pixels; the chosen path (impervious-product vs landcover-derive) is a
deterministic function of the input URI so it does not need to enter the key.

Cache layout:

    ``gs://grace-2-hazard-prod-cache/cache/static-30d/impervious/<key>.tif``

**Cross-cutting invariants:**

- **Invariant 2 (Deterministic workflows): preserves.** Zero LLM calls; pure
  numpy reclass + scale via rasterio.
- **FR-DC-6 (cacheable): honors.** ``cacheable=True``,
  ``ttl_class="static-30d"``, ``source_class="impervious"`` — output is stable
  for the lifetime of the cached upstream NLCD raster.
- **CRS hygiene (engine.md domain discipline):** the output preserves the input
  CRS verbatim (no reprojection); the transform / size / nodata are propagated.
- **NFR-R-1 (resilience):** failures surface as ``ImperviousSurfaceError`` with
  typed ``error_code``; GCS-read errors and rasterio-open errors are wrapped.

**Codified job-0086 lesson check:** the input/output share grid + transform +
CRS; the tool does not emit new geometry. The unit tests verify pixel-value
correctness against known synthetic landcover (class 22 → 0.3, etc.), and the
live test verifies the developed-class mapping produces sensible mean-fraction
values for a real NLCD bbox.
"""

from __future__ import annotations
from typing import Any

import logging
import os
import tempfile

from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from .cache import CACHE_BUCKET, read_through

__all__ = [
    "compute_impervious_surface",
    "ImperviousSurfaceError",
    "DEVELOPED_CLASS_TO_IMPERVIOUS",
]

logger = logging.getLogger("grace2_agent.tools.compute_impervious_surface")

# ---------------------------------------------------------------------------
# Error class
# ---------------------------------------------------------------------------


class ImperviousSurfaceError(RuntimeError):
    """Raised when impervious-surface computation fails.

    ``error_code`` carries a SCREAMING_SNAKE_CASE code surfaced in the
    pipeline strip (NFR-R-1 typed-error requirement).

    Codes:
    - ``RASTER_OPEN_FAILED`` — rasterio could not open the input.
    - ``RASTER_DOWNLOAD_FAILED`` — GCS download failed.
    - ``RASTER_WRITE_FAILED`` — output rasterio write failed.
    - ``UNKNOWN_RASTER_URI`` — uri not a gs:// URI and not a readable file.
    - ``BBOX_OUTSIDE_RASTER`` — requested bbox does not intersect the raster.
    """

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------

_IMPERVIOUS_METADATA = AtomicToolMetadata(
    name="compute_impervious_surface",
    ttl_class="static-30d",
    source_class="impervious",
    cacheable=True,
)


# ---------------------------------------------------------------------------
# Developed-class → impervious-fraction mapping (USGS NLCD canonical encoding)
# ---------------------------------------------------------------------------

#: USGS NLCD developed-density classes → typical impervious fraction.
#: The mapping reflects the canonical NLCD developed-class descriptions:
#: 21 Developed/Open Space (<20% impervious), 22 Low (20-49%), 23 Medium
#: (50-79%), 24 High (80-100%). The representative-midpoint fractions follow
#: the USGS impervious-surface companion product's typical values. Other NLCD
#: classes (water, forest, agriculture, etc.) map to 0.0 by default.
DEVELOPED_CLASS_TO_IMPERVIOUS: dict[int, float] = {
    21: 0.0,
    22: 0.3,
    23: 0.6,
    24: 0.9,
}


# ---------------------------------------------------------------------------
# GCS-read helper
# ---------------------------------------------------------------------------


def _download_raster_bytes(uri: str, storage_client: object | None) -> bytes:
    """Download raster bytes from a ``gs://`` URI or read from a local path.

    ``storage_client`` is injected by tests; production callers pass None and
    the function builds an ADC-authenticated client lazily.

    Raises ``ImperviousSurfaceError`` on any failure so callers get a typed
    error.
    """
    if not uri.startswith("gs://"):
        # Local path — read directly (test / dev convenience).
        try:
            with open(uri, "rb") as f:
                return f.read()
        except OSError as exc:
            raise ImperviousSurfaceError(
                "RASTER_DOWNLOAD_FAILED",
                f"Could not read local raster path {uri!r}: {exc}",
            ) from exc

    # GCS path.
    rest = uri[len("gs://"):]
    slash = rest.find("/")
    if slash == -1:
        raise ImperviousSurfaceError(
            "UNKNOWN_RASTER_URI",
            f"Malformed gs:// URI (no object key): {uri!r}",
        )
    bucket_name = rest[:slash]
    blob_path = rest[slash + 1:]

    try:
        if storage_client is None:
            from google.cloud import storage  # type: ignore[import-not-found]

            storage_client = storage.Client()
        bucket_obj = storage_client.bucket(bucket_name)
        blob = bucket_obj.blob(blob_path)
        return blob.download_as_bytes()
    except Exception as exc:  # noqa: BLE001
        raise ImperviousSurfaceError(
            "RASTER_DOWNLOAD_FAILED",
            f"GCS download failed for {uri!r}: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Product-type detection
# ---------------------------------------------------------------------------


def _is_impervious_product(uri: str, tags: dict[str, object] | None) -> bool:
    """Auto-detect whether an input raster is the NLCD Impervious Surface product.

    Heuristics:
    - Filename contains the substring ``impervious`` (case-insensitive)
      anywhere in the URI path (matches typical USGS names like
      ``NLCD_2021_Impervious_L48.tif``, ``annual_nlcd_impervious_2021.tif``).
    - rasterio tags include a key whose name contains ``impervious`` or whose
      stringified value contains the substring ``Impervious_Surface`` /
      ``percent_developed_impervious`` (the canonical NLCD layer aliases).

    Returns True if the input looks like the impervious product, False
    otherwise (in which case the landcover dev-class mapping path is used).
    """
    if "impervious" in uri.lower():
        return True
    if tags:
        for k, v in tags.items():
            if "impervious" in str(k).lower():
                return True
            sv = str(v).lower()
            if "impervious_surface" in sv or "percent_developed_impervious" in sv:
                return True
    return False


# ---------------------------------------------------------------------------
# Core computation — pure-numpy, no LLM
# ---------------------------------------------------------------------------


def _derive_impervious_from_landcover(
    landcover_array: object,  # numpy.ndarray (int)
    nodata: int | float | None,
) -> object:  # numpy.ndarray (float32)
    """Map NLCD landcover class codes to impervious fractions (developed-class lookup).

    Args:
        landcover_array: 2D numpy array of NLCD class codes (integers).
        nodata: input nodata sentinel (or None).

    Returns:
        Float32 numpy array of impervious fractions in [0.0, 1.0], with
        ``np.nan`` wherever the input is nodata.
    """
    import numpy as np  # type: ignore[import-not-found]

    out = np.zeros(landcover_array.shape, dtype=np.float32)
    # Apply developed-class mapping. All other classes (water, forest, ag, …)
    # remain at the default 0.0 — physically meaningful: a forest is impervious-
    # 0%, a water body is impervious-0% from a runoff-routing perspective (the
    # water IS the runoff destination).
    for class_code, fraction in DEVELOPED_CLASS_TO_IMPERVIOUS.items():
        out[landcover_array == class_code] = fraction

    # Preserve nodata as NaN per audit.md spec.
    if nodata is not None:
        out[landcover_array == nodata] = np.nan

    return out


def _scale_impervious_product(
    impervious_array: object,  # numpy.ndarray (int)
    nodata: int | float | None,
) -> object:  # numpy.ndarray (float32)
    """Scale NLCD Impervious Surface product values (0-100) to fractions (0.0-1.0).

    Args:
        impervious_array: 2D numpy array of percent-impervious values.
        nodata: input nodata sentinel (or None).

    Returns:
        Float32 numpy array of impervious fractions in [0.0, 1.0], with
        ``np.nan`` wherever the input is nodata.
    """
    import numpy as np  # type: ignore[import-not-found]

    out = impervious_array.astype(np.float32) / 100.0

    # Clip to [0.0, 1.0]. Anything outside is either nodata (handled below) or
    # an upstream encoding anomaly; clipping is safer than emitting fractions
    # > 1.0 that downstream consumers would mis-interpret.
    out = np.clip(out, 0.0, 1.0)

    # Preserve nodata as NaN.
    if nodata is not None:
        out[impervious_array == nodata] = np.nan

    return out


def _compute_impervious_bytes(
    landcover_bytes: bytes,
    bbox: tuple[float, float, float, float] | None,
    force_impervious_product: bool | None = None,
) -> bytes:
    """Read landcover bytes, derive impervious, return a Float32 GeoTIFF bytes.

    Args:
        landcover_bytes: bytes of the input raster (GeoTIFF).
        bbox: optional ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326
            for a windowed read.  When ``None`` the full raster is processed.
            Tested-by-construction: if the bbox does not intersect the raster
            after CRS-aware transform, ``BBOX_OUTSIDE_RASTER`` is raised.
        force_impervious_product: tests can pass True/False to override the
            auto-detection heuristic on synthetic data that lacks the
            ``impervious`` filename token.

    Returns:
        Float32 GeoTIFF bytes (single-band), nodata = NaN, CRS + transform
        inherited from the input.
    """
    import numpy as np  # type: ignore[import-not-found]
    import rasterio  # type: ignore[import-not-found]
    from rasterio.io import MemoryFile  # type: ignore[import-not-found]
    from rasterio.warp import transform_bounds  # type: ignore[import-not-found]
    from rasterio.windows import from_bounds as window_from_bounds  # type: ignore[import-not-found]

    try:
        memfile = MemoryFile(landcover_bytes)
    except Exception as exc:  # noqa: BLE001
        raise ImperviousSurfaceError(
            "RASTER_OPEN_FAILED",
            f"rasterio MemoryFile open failed: {exc}",
        ) from exc

    try:
        with memfile.open() as src:
            src_crs = src.crs
            src_transform = src.transform
            src_nodata = src.nodata
            tags = src.tags()

            # Decide path.
            if force_impervious_product is not None:
                is_imp = bool(force_impervious_product)
            else:
                # uri is not available at this layer; the outer wrapper passes
                # the URI through filename inspection. Here we only have tags
                # to inspect, plus the outer wrapper's URI-based check.
                is_imp = _is_impervious_product("", tags)

            # Compute the read window if bbox is provided.
            if bbox is not None:
                if src_crs is None:
                    raise ImperviousSurfaceError(
                        "RASTER_OPEN_FAILED",
                        "Input raster has no CRS; cannot transform bbox.",
                    )
                # bbox is EPSG:4326 per LayerURI / docstring convention.
                try:
                    west, south, east, north = transform_bounds(
                        "EPSG:4326", src_crs, *bbox, densify_pts=21
                    )
                except Exception as exc:  # noqa: BLE001
                    raise ImperviousSurfaceError(
                        "RASTER_OPEN_FAILED",
                        f"bbox transform_bounds failed: {exc}",
                    ) from exc

                try:
                    window = window_from_bounds(
                        west, south, east, north, transform=src_transform
                    )
                    # Intersect with the raster's own window.
                    raster_window = rasterio.windows.Window(
                        0, 0, src.width, src.height
                    )
                    # rasterio's intersection helper — returns the overlap.
                    window = window.intersection(raster_window)
                except (ValueError, rasterio.windows.WindowError) as exc:
                    raise ImperviousSurfaceError(
                        "BBOX_OUTSIDE_RASTER",
                        f"bbox {bbox} does not intersect raster: {exc}",
                    ) from exc

                # Round window to integer pixels (rasterio convention).
                window = window.round_lengths().round_offsets()
                if window.width <= 0 or window.height <= 0:
                    raise ImperviousSurfaceError(
                        "BBOX_OUTSIDE_RASTER",
                        f"bbox {bbox} produced an empty window for the raster.",
                    )

                arr = src.read(1, window=window)
                out_transform = src.window_transform(window)
                out_width = int(window.width)
                out_height = int(window.height)
            else:
                arr = src.read(1)
                out_transform = src_transform
                out_width = src.width
                out_height = src.height

            # Compute output array.
            if is_imp:
                logger.info(
                    "compute_impervious_surface: scaling impervious product "
                    "(min=%s, max=%s, nodata=%s)",
                    arr.min(),
                    arr.max(),
                    src_nodata,
                )
                result = _scale_impervious_product(arr, src_nodata)
            else:
                logger.info(
                    "compute_impervious_surface: deriving from landcover "
                    "(unique classes=%s, nodata=%s)",
                    np.unique(arr).tolist()[:10],
                    src_nodata,
                )
                result = _derive_impervious_from_landcover(arr, src_nodata)

            # Sanity: result must be Float32 in [0.0, 1.0] or NaN.
            assert result.dtype == np.float32, (
                f"result dtype is {result.dtype}, expected float32"
            )

            # Write Float32 GeoTIFF to bytes.
            profile = {
                "driver": "GTiff",
                "dtype": "float32",
                "count": 1,
                "width": out_width,
                "height": out_height,
                "crs": src_crs,
                "transform": out_transform,
                "nodata": float("nan"),
                "compress": "deflate",
                "predictor": 3,  # float predictor for float32
                "tiled": True,
                "blockxsize": 256,
                "blockysize": 256,
            }

            try:
                with MemoryFile() as out_memfile:
                    with out_memfile.open(**profile) as dst:
                        dst.write(result, 1)
                        # Tag the output so consumers can identify it.
                        dst.update_tags(
                            grace2_tool="compute_impervious_surface",
                            grace2_source_path=(
                                "impervious_product" if is_imp else "landcover_derived"
                            ),
                            grace2_units="fraction",
                        )
                    return out_memfile.read()
            except Exception as exc:  # noqa: BLE001
                raise ImperviousSurfaceError(
                    "RASTER_WRITE_FAILED",
                    f"output GeoTIFF write failed: {exc}",
                ) from exc

    except ImperviousSurfaceError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ImperviousSurfaceError(
            "RASTER_OPEN_FAILED",
            f"unexpected error reading input raster: {exc}",
        ) from exc
    finally:
        try:
            memfile.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


@register_tool(_IMPERVIOUS_METADATA)
def compute_impervious_surface(
    landcover_uri: str,
    bbox: tuple[float, float, float, float] | None = None,
    *,
    _storage_client: object | None = None,
    _bucket: str | None = None,
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> LayerURI:
    """NLCD impervious-surface fraction computation.

    Reads NLCD impervious-surface raster (separate USGS product from NLCD
    landcover). OR if given NLCD landcover, derives impervious surface from
    developed-class membership (21=Open Space 0%, 22=Low 30%, 23=Medium 60%,
    24=High 90%). Returns float32 raster of impervious fraction 0.0-1.0.

    Use this when: the agent needs an impervious-surface fraction layer for
    urban hydrology, runoff modeling, SFINCS infiltration setup, urban heat
    island analysis, or a quick percent-developed visualization. Accepts
    either the NLCD landcover product (canonical class codes) — in which case
    the dev-class density mapping is applied — or the separate NLCD Impervious
    Surface product (integer 0-100 percent) — in which case values are scaled
    by 1/100. The path is auto-selected by filename heuristic + raster tags.

    Do NOT use this for: per-building impervious-area calculation (use
    ``fetch_buildings`` + a geometry-based metric); time-varying impervious
    change detection (this is a snapshot); non-CONUS coverage (NLCD is L48
    only — ESA WorldCover does not encode impervious fraction the same way);
    Pelicun-style damage modeling (this is an input layer, not a postprocess).

    Params:
        landcover_uri: a ``gs://`` URI (or local path) of either an NLCD
            landcover GeoTIFF (typical output of ``fetch_landcover``) or an
            NLCD Impervious Surface GeoTIFF (separate USGS product). The path
            selection is auto-detected via filename substring "impervious" and
            via the raster's embedded tags.
        bbox: optional ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326.
            When provided, the input is windowed-read to the bbox (CRS-aware
            via ``rasterio.warp.transform_bounds``) before computation —
            cheaper for large source rasters with a small AOI. When ``None``,
            the full input raster is processed.

    Returns:
        A ``LayerURI`` pointing at a Float32 GeoTIFF in the cache bucket:
        ``gs://grace-2-hazard-prod-cache/cache/static-30d/impervious/<key>.tif``.
        Single-band, values in [0.0, 1.0] (NaN nodata), same CRS and grid as
        the input. ``layer_type="raster"``, ``role="context"``, ``units=None``
        (the values are dimensionless fractions).

    LLM guidance:
        - If the user already has an NLCD landcover URI (typical from
          ``fetch_landcover``), pass it directly — the dev-class mapping path
          fires automatically.
        - If the user explicitly fetched the NLCD Impervious Surface companion
          product, the filename will contain "impervious" and the scaling
          path fires.
        - Pass ``bbox`` only when the input raster is materially larger than
          the AOI — for AOI-sized inputs, leave bbox=None (full read).

    FR-CE-8: Results are routed through ``read_through`` so repeat calls with
    the same ``(landcover_uri, bbox)`` pair return the cached impervious
    raster without re-running the computation. TTL is 30 days (input is
    static-30d, output is a deterministic function of the input).

    Raises:
        ImperviousSurfaceError: if the input cannot be read, the bbox does not
            intersect the raster, or the output cannot be written. Error
            carries ``error_code`` for the pipeline strip.
    """
    effective_bucket = _bucket or CACHE_BUCKET

    # Validate bbox shape early if provided.
    if bbox is not None:
        if len(bbox) != 4:
            raise ImperviousSurfaceError(
                "BBOX_OUTSIDE_RASTER",
                f"bbox must be (min_lon, min_lat, max_lon, max_lat); got {bbox!r}",
            )
        min_lon, min_lat, max_lon, max_lat = bbox
        if min_lon >= max_lon or min_lat >= max_lat:
            raise ImperviousSurfaceError(
                "BBOX_OUTSIDE_RASTER",
                f"bbox is degenerate (min must be < max on both axes): {bbox!r}",
            )

    # URI-based product detection — the inner _compute_impervious_bytes also
    # checks raster tags, but the URI heuristic is the strongest signal so
    # we apply it here and pass through as an override.
    uri_says_impervious = "impervious" in landcover_uri.lower()

    def _fetch() -> bytes:
        # 1. Download or read the source raster.
        raster_bytes = _download_raster_bytes(landcover_uri, _storage_client)

        # 2. Compute impervious bytes.
        return _compute_impervious_bytes(
            raster_bytes,
            bbox=bbox,
            force_impervious_product=(True if uri_says_impervious else None),
        )

    # Cache key on (landcover_uri, bbox).
    params: dict[str, object] = {
        "landcover_uri": landcover_uri,
    }
    if bbox is not None:
        # 6dp ≈ 0.1m; sufficient for cache-key stability across float jitter.
        params["bbox"] = [round(v, 6) for v in bbox]

    result = read_through(
        metadata=_IMPERVIOUS_METADATA,
        params=params,
        ext="tif",
        fetch_fn=_fetch,
        bucket=effective_bucket,
        storage_client=_storage_client,
    )
    assert result.uri is not None, (
        "compute_impervious_surface is cacheable; uri must be set by read_through"
    )

    # Build a stable layer_id from the input URI's last path component.
    input_key = landcover_uri.rstrip("/").rsplit("/", 1)[-1]
    for ext in (".tif", ".tiff", ".TIF", ".TIFF"):
        if input_key.endswith(ext):
            input_key = input_key[: -len(ext)]
            break
    bbox_suffix = ""
    if bbox is not None:
        bbox_suffix = f"-bbox{bbox[0]:.4f}-{bbox[1]:.4f}"

    return LayerURI(
        layer_id=f"impervious-{input_key}{bbox_suffix}",
        name="Impervious Surface Fraction (NLCD-derived)",
        layer_type="raster",
        uri=result.uri,
        style_preset="continuous_dem",  # placeholder; impervious-specific QML deferred
        role="context",
        units=None,
    )
