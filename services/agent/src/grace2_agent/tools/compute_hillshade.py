"""Atomic tool ``compute_hillshade`` — hillshade raster from DEM (job-0079, FR-CE-8, FR-DC).

This module registers one atomic tool that computes a hillshade raster from a DEM
by wrapping GDAL's ``gdaldem hillshade`` command:

    ``compute_hillshade(dem_uri, style, algorithm, azimuth, altitude, z_factor) → LayerURI``

The result is a single-band GeoTIFF in the same CRS and grid as the input DEM,
stored under the FR-DC-3 cache shim at:

    ``gs://grace-2-hazard-prod-cache/cache/static-30d/hillshade/<key>.tif``

**Style presets:**

- ``"standard"`` — single hillshade, Horn algorithm, azimuth 315°, altitude 45°
  (the GDAL default). Fast, suitable for general use.
- ``"swiss_double"`` — two hillshades (Horn @ 315° + Horn @ 135°) multiply-blended
  into a single GeoTIFF via numpy (Imhof-style richer cartographic depth). Pre-
  composite approach selected (kickoff §A): the LLM-visible result is one layer.
- ``"multidirectional"`` — single hillshade with ``-multidirectional`` flag; combines
  NE/SE/NW/SW illuminations, no dead-lit sides.
- ``"combined"`` — ``-combined`` flag; brightness incorporates slope steepness; best
  for steep mountainous terrain.
- ``"smooth"`` — Horn algorithm + ZevenbergenThorne smoothing flag; smoother results
  on rough terrain.

**Cache key** is derived from ``(dem_uri, style, algorithm, azimuth, altitude, z_factor)``
— all six parameters materially affect the output pixels (FR-DC-3).

**Implementation flow (cache miss):**

1. Download the DEM bytes from GCS (or read a local path for dev/test).
2. Write to a temp file (``gdaldem`` requires a file path).
3. ``subprocess.run(["gdaldem", "hillshade", <input>, <output>, *flags])`` where:
   - ``-az <azimuth>`` sets the azimuth (315° default).
   - ``-alt <altitude>`` sets the altitude (45° default).
   - ``-z <z_factor>`` sets the vertical exaggeration (1.0 default).
   - ``-alg ZevenbergenThorne`` is added when ``style="smooth"``.
   - ``-multidirectional`` is added when ``style="multidirectional"``.
   - ``-combined`` is added when ``style="combined"``.
   - ``swiss_double`` runs gdaldem TWICE (315° + 135°) then numpy multiply-blends.
4. Read the output bytes, clean up temp files.
5. ``read_through`` writes the bytes to the cache bucket.

**Cross-cutting invariants:**

- **Invariant 2 (Deterministic workflows): preserves.** Zero LLM calls.
- **FR-DC-6 (cacheable): honors.** ``cacheable=True``, ``ttl_class="static-30d"``,
  ``source_class="hillshade"`` — DEM-derived output is stable for the lifetime of
  the cached DEM.
- **NFR-R-1 (resilience): preserves.** ``subprocess.run`` failures surface as
  ``HillshadeComputeError`` (typed, never unhandled exception); GCS download errors
  are let through for the agent FR-AS-11 surface to handle.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from typing import Literal

from grace2_contracts.execution import LayerURI
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool
from .cache import CACHE_BUCKET, read_through

__all__ = [
    "compute_hillshade",
    "HillshadeComputeError",
]

logger = logging.getLogger("grace2_agent.tools.compute_hillshade")

# ---------------------------------------------------------------------------
# Error class
# ---------------------------------------------------------------------------


class HillshadeComputeError(RuntimeError):
    """Raised when ``gdaldem hillshade`` fails or the DEM cannot be fetched.

    ``error_code`` carries a SCREAMING_SNAKE_CASE code surfaced in the
    pipeline strip (NFR-R-1 typed-error requirement).

    Codes:
    - ``GDALDEM_UNAVAILABLE`` — ``gdaldem`` binary not found on PATH.
    - ``GDALDEM_FAILED`` — ``gdaldem hillshade`` returned non-zero.
    - ``DEM_DOWNLOAD_FAILED`` — GCS download for the DEM URI failed.
    - ``BLEND_FAILED`` — numpy multiply-blend step failed (swiss_double only).
    """

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------

_COMPUTE_HILLSHADE_METADATA = AtomicToolMetadata(
    name="compute_hillshade",
    ttl_class="static-30d",
    source_class="hillshade",
    cacheable=True,
)

# ---------------------------------------------------------------------------
# gdaldem binary resolution
# ---------------------------------------------------------------------------

# The ``gdaldem`` binary is expected on PATH. In the dev environment it lives
# in the ``grace2`` conda env (``~/miniforge3/envs/grace2/bin/gdaldem``).
# In the agent container it will be installed alongside GDAL. Override via
# ``GRACE2_GDALDEM_BIN`` env var for environments where the binary is not on
# the default PATH.

_GDALDEM_BIN: str | None = None


def _get_gdaldem_bin() -> str:
    """Resolve the ``gdaldem`` binary path, with env-var override support.

    Checks ``GRACE2_GDALDEM_BIN`` first, then PATH (via ``shutil.which``),
    then the known conda-env path from the dev environment. Raises
    ``HillshadeComputeError`` if not found.
    """
    global _GDALDEM_BIN
    if _GDALDEM_BIN is not None:
        return _GDALDEM_BIN

    import shutil

    candidate = (
        os.environ.get("GRACE2_GDALDEM_BIN")
        or shutil.which("gdaldem")
        or _conda_grace2_gdaldem()
    )
    if candidate is None or not os.path.isfile(candidate):
        raise HillshadeComputeError(
            "GDALDEM_UNAVAILABLE",
            "gdaldem binary not found on PATH; set GRACE2_GDALDEM_BIN "
            "or install gdal-bin / activate the grace2 conda env.",
        )
    _GDALDEM_BIN = candidate
    return _GDALDEM_BIN


def _conda_grace2_gdaldem() -> str | None:
    """Return the grace2 conda-env gdaldem path if it exists."""
    candidate = os.path.expanduser("~/miniforge3/envs/grace2/bin/gdaldem")
    return candidate if os.path.isfile(candidate) else None


# ---------------------------------------------------------------------------
# GCS download helper
# ---------------------------------------------------------------------------


def _download_dem_bytes(dem_uri: str, storage_client: object | None) -> bytes:
    """Download the DEM bytes from a ``gs://`` URI.

    ``storage_client`` is injected by tests; production callers pass None and
    the function builds an ADC-authenticated client lazily.

    Raises ``HillshadeComputeError`` on any failure so callers get a typed error.
    """
    if not dem_uri.startswith("gs://"):
        # Local path — read directly (test / dev convenience).
        try:
            with open(dem_uri, "rb") as f:
                return f.read()
        except OSError as exc:
            raise HillshadeComputeError(
                "DEM_DOWNLOAD_FAILED",
                f"Could not read local DEM path {dem_uri!r}: {exc}",
            ) from exc

    # GCS path.
    rest = dem_uri[len("gs://"):]
    slash = rest.find("/")
    if slash == -1:
        raise HillshadeComputeError(
            "DEM_DOWNLOAD_FAILED",
            f"Malformed gs:// URI (no object key): {dem_uri!r}",
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
        raise HillshadeComputeError(
            "DEM_DOWNLOAD_FAILED",
            f"GCS download failed for {dem_uri!r}: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# gdaldem hillshade subprocess wrapper
# ---------------------------------------------------------------------------


def _run_gdaldem_hillshade(
    input_path: str,
    output_path: str,
    azimuth: float,
    altitude: float,
    z_factor: float,
    algorithm: Literal["Horn", "ZevenbergenThorne", "Igor"],
    *,
    multidirectional: bool = False,
    combined: bool = False,
) -> None:
    """Run ``gdaldem hillshade`` as a subprocess.

    Args:
        input_path: local file path to the input DEM GeoTIFF.
        output_path: local file path for the output hillshade GeoTIFF.
        azimuth: sun azimuth in degrees (0–360, clockwise from north).
        altitude: sun altitude in degrees above the horizon (0–90).
        z_factor: vertical exaggeration factor (1.0 = no exaggeration).
        algorithm: gradient algorithm. ``"Horn"`` is the GDAL default.
            ``"ZevenbergenThorne"`` adds ``-alg ZevenbergenThorne``.
            ``"Igor"`` adds ``-igor``.
        multidirectional: if True, adds ``-multidirectional`` flag.
        combined: if True, adds ``-combined`` flag.

    Raises:
        HillshadeComputeError: if the binary is missing or returns non-zero.
    """
    gdaldem = _get_gdaldem_bin()

    cmd: list[str] = [
        gdaldem, "hillshade",
        input_path, output_path,
    ]
    # -az and -multidirectional are mutually exclusive in GDAL; omit -az when
    # multidirectional mode is active (gdaldem rejects the combination).
    if not multidirectional:
        cmd.extend(["-az", str(azimuth)])
    cmd.extend([
        "-alt", str(altitude),
        "-z", str(z_factor),
        "-of", "GTiff",
    ])
    if algorithm == "ZevenbergenThorne":
        cmd.extend(["-alg", "ZevenbergenThorne"])
    elif algorithm == "Igor":
        cmd.extend(["-igor"])
    if multidirectional:
        cmd.append("-multidirectional")
    if combined:
        cmd.append("-combined")

    logger.info(
        "compute_hillshade: running gdaldem hillshade input=%s az=%s alt=%s z=%s "
        "algorithm=%s multidirectional=%s combined=%s cmd=%s",
        input_path, azimuth, altitude, z_factor, algorithm,
        multidirectional, combined, " ".join(cmd),
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            timeout=300,  # 5-min ceiling; hillshade of any reasonable DEM is seconds
        )
    except FileNotFoundError as exc:
        raise HillshadeComputeError(
            "GDALDEM_UNAVAILABLE",
            f"gdaldem binary not executable at {gdaldem!r}: {exc}",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise HillshadeComputeError(
            "GDALDEM_FAILED",
            f"gdaldem hillshade timed out after 300 s for input={input_path!r}: {exc}",
        ) from exc

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        raise HillshadeComputeError(
            "GDALDEM_FAILED",
            f"gdaldem hillshade returned exit code {result.returncode}; "
            f"stderr={stderr!r}; stdout={stdout!r}",
        )

    logger.info(
        "compute_hillshade: gdaldem hillshade completed output=%s", output_path
    )


def _multiply_blend_hillshades(
    path_a: str,
    path_b: str,
    output_path: str,
) -> None:
    """Multiply-blend two single-band hillshade GeoTIFFs into one.

    Implements the Imhof "swiss double" blending technique:

        result = (A / 255.0) * (B / 255.0) * 255.0

    The multiply blend darkens valleys (both illuminations are dark) while
    preserving brightness on sun-facing ridges, giving richer cartographic
    depth than a single illumination direction.

    Both inputs must share the same CRS, extent, and grid (they are derived
    from the same DEM so this is guaranteed). The output is written as a
    single-band Float32-normalized-to-uint8 GeoTIFF.

    Args:
        path_a: local path to first hillshade GeoTIFF (e.g. azimuth 315°).
        path_b: local path to second hillshade GeoTIFF (e.g. azimuth 135°).
        output_path: local path for the blended output GeoTIFF.

    Raises:
        HillshadeComputeError: on any numpy/rasterio failure.
    """
    try:
        import numpy as np
        import rasterio

        with rasterio.open(path_a) as src_a:
            data_a = src_a.read(1).astype(np.float32)
            profile = src_a.profile.copy()
            nodata_a = src_a.nodata

        with rasterio.open(path_b) as src_b:
            data_b = src_b.read(1).astype(np.float32)
            nodata_b = src_b.nodata

        # Build masks for nodata regions (gdaldem uses 0 for flat/nodata in
        # hillshade output; preserve those as 0 in the blend).
        mask_a = (data_a == nodata_a) if nodata_a is not None else np.zeros_like(data_a, dtype=bool)
        mask_b = (data_b == nodata_b) if nodata_b is not None else np.zeros_like(data_b, dtype=bool)
        nodata_mask = mask_a | mask_b

        # Multiply blend: (A/255) * (B/255) * 255 — keeps values in [0, 255].
        blended = (data_a / 255.0) * (data_b / 255.0) * 255.0
        blended = np.clip(blended, 0, 255)
        blended[nodata_mask] = 0.0

        # Write as uint8 GeoTIFF (standard hillshade output dtype).
        profile.update(dtype="uint8", count=1, nodata=0)
        with rasterio.open(output_path, "w", **profile) as dst:
            dst.write(blended.astype(np.uint8), 1)

        logger.info(
            "compute_hillshade: swiss_double blend complete output=%s", output_path
        )

    except HillshadeComputeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HillshadeComputeError(
            "BLEND_FAILED",
            f"numpy multiply-blend failed for swiss_double: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Per-style fetch function builders
# ---------------------------------------------------------------------------


def _make_fetch_fn(
    dem_uri: str,
    style: str,
    algorithm: Literal["Horn", "ZevenbergenThorne", "Igor"],
    azimuth: float,
    altitude: float,
    z_factor: float,
    storage_client: object | None,
) -> bytes:
    """Produce hillshade bytes for the given style on cache-miss.

    Returns the raw bytes of the output GeoTIFF.
    """
    dem_bytes = _download_dem_bytes(dem_uri, storage_client)

    in_tmp: str | None = None
    out_tmp: str | None = None
    out_tmp_b: str | None = None  # only for swiss_double
    blend_tmp: str | None = None  # only for swiss_double

    try:
        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as in_f:
            in_tmp = in_f.name
            in_f.write(dem_bytes)

        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as out_f:
            out_tmp = out_f.name
        os.unlink(out_tmp)  # gdaldem errors if output file already exists on some builds

        if style == "swiss_double":
            # Run gdaldem twice: azimuth 315° + azimuth 135°; then multiply-blend.
            with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as out_b_f:
                out_tmp_b = out_b_f.name
            os.unlink(out_tmp_b)

            # First pass: primary azimuth (315° — the classic NW sun position).
            _run_gdaldem_hillshade(
                in_tmp, out_tmp,
                azimuth=315.0, altitude=altitude, z_factor=z_factor,
                algorithm=algorithm,
            )
            # Second pass: complementary azimuth (135° — SE, fills shadows from 315°).
            _run_gdaldem_hillshade(
                in_tmp, out_tmp_b,
                azimuth=135.0, altitude=altitude, z_factor=z_factor,
                algorithm=algorithm,
            )
            # Multiply-blend the two hillshades into a single GeoTIFF.
            with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as blend_f:
                blend_tmp = blend_f.name
            os.unlink(blend_tmp)
            _multiply_blend_hillshades(out_tmp, out_tmp_b, blend_tmp)
            with open(blend_tmp, "rb") as f:
                return f.read()

        elif style == "multidirectional":
            _run_gdaldem_hillshade(
                in_tmp, out_tmp,
                azimuth=azimuth, altitude=altitude, z_factor=z_factor,
                algorithm=algorithm,
                multidirectional=True,
            )
        elif style == "combined":
            _run_gdaldem_hillshade(
                in_tmp, out_tmp,
                azimuth=azimuth, altitude=altitude, z_factor=z_factor,
                algorithm=algorithm,
                combined=True,
            )
        elif style == "smooth":
            # ZevenbergenThorne smoothing — use the algorithm kwarg override if
            # the caller explicitly chose a different algorithm, but the preset
            # itself is intended for smoothed results.
            _run_gdaldem_hillshade(
                in_tmp, out_tmp,
                azimuth=azimuth, altitude=altitude, z_factor=z_factor,
                algorithm="ZevenbergenThorne",
            )
        else:
            # "standard" (and custom: use whatever algorithm/az/alt/z are set).
            _run_gdaldem_hillshade(
                in_tmp, out_tmp,
                azimuth=azimuth, altitude=altitude, z_factor=z_factor,
                algorithm=algorithm,
            )

        with open(out_tmp, "rb") as f:
            return f.read()

    finally:
        for path in (in_tmp, out_tmp, out_tmp_b, blend_tmp):
            if path is not None:
                try:
                    os.unlink(path)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


@register_tool(_COMPUTE_HILLSHADE_METADATA)
def compute_hillshade(
    dem_uri: str,
    style: Literal["standard", "swiss_double", "multidirectional", "combined", "smooth"] = "standard",
    # Power-user overrides (primarily consulted for "standard"; presets override
    # specific fields — e.g. "smooth" always uses ZevenbergenThorne).
    algorithm: Literal["Horn", "ZevenbergenThorne", "Igor"] = "Horn",
    azimuth: float = 315.0,
    altitude: float = 45.0,
    z_factor: float = 1.0,
    *,
    _storage_client: object | None = None,
    _bucket: str | None = None,
) -> LayerURI:
    """Compute a hillshade raster from a DEM. Wraps ``gdaldem hillshade``.

    Use this when: the agent needs cartographic terrain context — a flood layer,
    habitat map, or any spatial overlay is easier to read when placed over a
    hillshaded terrain base. Hillshade adds spatial depth that helps users
    orient themselves and understand terrain influence on the analysis.

    Do NOT use this for: slope or aspect analysis (use ``compute_slope`` /
    ``compute_aspect``); quantitative elevation display (use
    ``compute_colored_relief``); bathymetry or sub-aqueous terrain; animated
    or time-varying terrain (output is static single-time).

    Style preset semantics:
        "standard": single hillshade, Horn algorithm, azimuth 315°, altitude
            45° — the GDAL default. Fast, suitable for general use.
        "swiss_double": two hillshades (Horn @ azimuth 315° + Horn @ 135°)
            pre-composited via numpy multiply-blend into a single GeoTIFF. The
            Imhof-style multiply blend gives richer cartographic depth —
            valleys are darkened (both illuminations see shadow) while ridges
            remain bright. Best for terrain reading, professional cartography.
        "multidirectional": single hillshade with GDAL's ``-multidirectional``
            flag — combines NE/SE/NW/SW illuminations; no dead-lit sides where
            one direction casts total shadow. Good for complex ridge terrain.
        "combined": ``-combined`` flag — brightness incorporates slope
            steepness alongside illumination; best for steep mountainous terrain
            where standard hillshade washes out high slopes.
        "smooth": Horn algorithm with ZevenbergenThorne gradient estimator —
            smoother results on rough or noisy DEMs; less high-frequency noise.

    LLM guidance:
        - Pick "swiss_double" when the user asks for "cartographic" /
          "professional" / "nice-looking" / "beautiful" terrain rendering.
        - Pick "multidirectional" when the user mentions "no dead spots" /
          "see all sides" / "no shadows" on complex terrain.
        - Pick "combined" for mountains, steep terrain, or when the user wants
          the slope steepness to be visible in the shading.
        - Pick "smooth" when the user mentions rough terrain, noisy DEM, or
          requests smoother results.
        - Default to "standard" otherwise (cheapest / fastest).

    Params:
        dem_uri: ``gs://`` URI of a DEM GeoTIFF (typically from ``fetch_dem``
            or a previous fetch pipeline step). Must be a single-band raster
            with elevation values in meters.
        style: one of the five preset names above. Controls the illumination
            algorithm and blending method. Power-user ``algorithm``,
            ``azimuth``, ``altitude``, and ``z_factor`` overrides are
            honoured for "standard"; preset-specific behaviour overrides
            them for other styles (e.g. "smooth" always uses
            ZevenbergenThorne regardless of ``algorithm``).
        algorithm: gradient algorithm. ``"Horn"`` (default) — standard 3×3
            Horn gradient. ``"ZevenbergenThorne"`` — smoother alternative.
            ``"Igor"`` — Igor's shading (experimental; steep terrain). Only
            consulted for "standard" and "swiss_double" styles.
        azimuth: sun azimuth in degrees (0–360, clockwise from north).
            Default 315° (NW). Only consulted for "standard" and
            "swiss_double" (primary pass) styles.
        altitude: sun altitude above the horizon in degrees (0–90). Default
            45°. Higher values flatten the shading; lower values emphasize
            terrain relief.
        z_factor: vertical exaggeration. Default 1.0 (no exaggeration).
            Values > 1.0 amplify terrain relief — useful for low-relief
            coastal DEMs.

    Returns:
        A ``LayerURI`` pointing at a hillshade GeoTIFF in the cache bucket:
        ``gs://grace-2-hazard-prod-cache/cache/static-30d/hillshade/<key>.tif``.
        For "swiss_double", the URI points at the pre-blended composite;
        the LLM-visible result is always a single layer. The output is a
        single-band uint8 GeoTIFF (0–255 intensity) in the same CRS and
        grid as the input DEM.

    FR-CE-8: Results are routed through ``read_through`` so repeat calls with
    the same ``(dem_uri, style, algorithm, azimuth, altitude, z_factor)``
    tuple return the cached hillshade without re-running gdaldem. TTL is
    30 days (DEM-derived outputs are stable over that window).

    Raises:
        HillshadeComputeError: if gdaldem is unavailable, returns non-zero,
            the DEM GCS download fails, or the swiss_double blend fails.
            Error carries ``error_code`` for the pipeline strip.
    """
    effective_bucket = _bucket or CACHE_BUCKET

    def _fetch() -> bytes:
        return _make_fetch_fn(
            dem_uri=dem_uri,
            style=style,
            algorithm=algorithm,
            azimuth=azimuth,
            altitude=altitude,
            z_factor=z_factor,
            storage_client=_storage_client,
        )

    # Cache key on all six parameters; style drives the actual algorithm choices
    # but we include algorithm/azimuth/altitude/z_factor so that "standard" with
    # custom overrides can coexist with "standard" at defaults in the same cache.
    params = {
        "dem_uri": dem_uri,
        "style": style,
        "algorithm": algorithm,
        "azimuth": azimuth,
        "altitude": altitude,
        "z_factor": z_factor,
    }

    result = read_through(
        metadata=_COMPUTE_HILLSHADE_METADATA,
        params=params,
        ext="tif",
        fetch_fn=_fetch,
        bucket=effective_bucket,
        storage_client=_storage_client,
    )
    assert result.uri is not None, "compute_hillshade is cacheable; uri must be set"

    # Build a concise layer_id and human-readable name.
    dem_key = dem_uri.rstrip("/").rsplit("/", 1)[-1].replace(".tif", "")
    layer_id = f"hillshade-{dem_key}-{style}"

    style_labels = {
        "standard": "Hillshade (Standard)",
        "swiss_double": "Hillshade (Swiss Double)",
        "multidirectional": "Hillshade (Multidirectional)",
        "combined": "Hillshade (Combined)",
        "smooth": "Hillshade (Smooth)",
    }
    name = style_labels.get(style, f"Hillshade ({style})")

    return LayerURI(
        layer_id=layer_id,
        name=name,
        layer_type="raster",
        uri=result.uri,
        style_preset="continuous_dem",  # closest existing preset; hillshade-specific QML deferred
        role="context",
        units="intensity",  # 0–255 uint8 luminance
    )
