"""Unit tests for ``compute_blended_composite`` atomic tool (job-0319).

Coverage:
 1. ``test_compute_blended_composite_registered`` — tool in TOOL_REGISTRY with
    correct metadata (cacheable=True, ttl_class="static-30d",
    source_class="blended").
 2. ``test_blend_resolvable_params_in_allowlist`` — base/overlay layer URIs are
    in RESOLVABLE_URI_PARAMS so the server resolves handles → COG URIs.
 3. ``test_compute_blended_composite_multiply_math`` — blends a 3-band RGB base
    with a 1-band grayscale overlay; asserts output COG has overviews, dims
    match the base, and the multiply math is correct on a sample pixel.
 4. ``test_compute_blended_composite_invalid_mode_raises`` — bad blend_mode →
    typed BlendedCompositeError(error_code="INVALID_BLEND_MODE").
 5. ``test_compute_blended_composite_returns_layer_uri_fields`` — LayerURI
    fields correct (raster, role, rgb units, "Shaded" name for multiply).
 6. ``test_compute_blended_composite_cache_hit_skips_fetch`` — second identical
    call hits the cache (blend not re-run).
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from grace2_agent.tools import TOOL_REGISTRY
from grace2_agent.tools.compute_blended_composite import (
    BlendedCompositeError,
    compute_blended_composite,
)

PINNED_NOW = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Synthetic raster helpers (in-memory → temp file)
# ---------------------------------------------------------------------------


def _write_rgb_base(path: str, size: int = 600) -> np.ndarray:
    """Write a 3-band RGB uint8 base raster; return the RGB array (3, H, W)."""
    rng = np.random.default_rng(7)
    rgb = rng.integers(40, 240, size=(3, size, size), dtype=np.uint8)
    transform = from_bounds(0.0, 0.0, size * 10.0, size * 10.0, size, size)
    profile = {
        "driver": "GTiff",
        "dtype": "uint8",
        "width": size,
        "height": size,
        "count": 3,
        "crs": "EPSG:5070",
        "transform": transform,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(rgb)
    return rgb.astype(np.float32)


def _write_gray_overlay(path: str, size: int = 600) -> np.ndarray:
    """Write a 1-band grayscale uint8 overlay on the SAME grid; return (H, W)."""
    rng = np.random.default_rng(13)
    gray = rng.integers(0, 256, size=(size, size), dtype=np.uint8)
    transform = from_bounds(0.0, 0.0, size * 10.0, size * 10.0, size, size)
    profile = {
        "driver": "GTiff",
        "dtype": "uint8",
        "width": size,
        "height": size,
        "count": 1,
        "crs": "EPSG:5070",
        "transform": transform,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(gray, 1)
    return gray.astype(np.float32)


# ---------------------------------------------------------------------------
# Fake GCS scaffolding for cache-shim isolation
# ---------------------------------------------------------------------------


class FakeBlob:
    def __init__(self, store: dict[str, bytes], path: str) -> None:
        self._store = store
        self._path = path
        self.custom_time = None
        self.cache_control = None

    def exists(self) -> bool:
        return self._path in self._store

    def download_as_bytes(self) -> bytes:
        return self._store[self._path]

    def upload_from_string(self, data: bytes, content_type=None) -> None:
        self._store[self._path] = data


class FakeBucket:
    def __init__(self, store: dict[str, bytes]) -> None:
        self._store = store

    def blob(self, path: str) -> FakeBlob:
        return FakeBlob(self._store, path)


class FakeStorageClient:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self._bucket = FakeBucket(self.store)

    def bucket(self, name: str) -> FakeBucket:
        return self._bucket


@pytest.fixture()
def fake_storage():
    return FakeStorageClient()


# ---------------------------------------------------------------------------
# Test 1 — registration / auto-discovery
# ---------------------------------------------------------------------------


def test_compute_blended_composite_registered():
    """compute_blended_composite is in TOOL_REGISTRY with the expected metadata."""
    assert "compute_blended_composite" in TOOL_REGISTRY
    entry = TOOL_REGISTRY["compute_blended_composite"]
    assert entry.metadata.cacheable is True
    assert entry.metadata.ttl_class == "static-30d"
    assert entry.metadata.source_class == "blended"
    # Registered via the @register_tool decorator from the module's own import.
    assert entry.module == "grace2_agent.tools.compute_blended_composite"


# ---------------------------------------------------------------------------
# Test 2 — handle resolution wiring (server resolves base/overlay handles)
# ---------------------------------------------------------------------------


def test_blend_resolvable_params_in_allowlist():
    """base/overlay layer URIs resolve through the session uri-registry."""
    from grace2_agent.uri_registry import RESOLVABLE_URI_PARAMS

    assert "base_layer_uri" in RESOLVABLE_URI_PARAMS
    assert "overlay_layer_uri" in RESOLVABLE_URI_PARAMS


# ---------------------------------------------------------------------------
# Test 3 — multiply math + overviews + dims (the headline correctness test)
# ---------------------------------------------------------------------------


def test_compute_blended_composite_multiply_math(fake_storage):
    """Blend a 3-band RGB base with a 1-band grayscale overlay.

    Asserts: (a) output COG has overviews, (b) output dims == base dims,
    (c) multiply math correct on a sample pixel:
        result_rgb = round(base_rgb * (overlay_gray / 255)).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        base_path = os.path.join(tmpdir, "base_rgb.tif")
        overlay_path = os.path.join(tmpdir, "overlay_gray.tif")
        # 600px > the COG driver's 512px overview threshold, so overviews are
        # built (the production behavior the agent depends on for fast WMS).
        base_rgb = _write_rgb_base(base_path, size=600)
        overlay_gray = _write_gray_overlay(overlay_path, size=600)

        result = compute_blended_composite(
            base_layer_uri=base_path,
            overlay_layer_uri=overlay_path,
            blend_mode="multiply",
            overlay_opacity=1.0,
            _storage_client=fake_storage,
            _bucket="test-bucket",
        )

        # Pull the written COG bytes back out of the fake store.
        assert result.uri is not None and result.uri.endswith(".tif")
        (cog_bytes,) = list(fake_storage.store.values())
        out_path = os.path.join(tmpdir, "out.tif")
        with open(out_path, "wb") as f:
            f.write(cog_bytes)

        with rasterio.open(out_path) as src:
            # (a) overviews present (the COG writer built them).
            assert src.overviews(1), (
                f"output COG must carry overviews; got {src.overviews(1)!r}"
            )
            # (b) dims match the base grid.
            assert (src.height, src.width) == base_rgb.shape[1:], (
                f"dims {(src.height, src.width)} != base {base_rgb.shape[1:]}"
            )
            assert src.count == 4, "RGBA composite expected (3 color + 1 alpha)"
            out_rgb = src.read([1, 2, 3]).astype(np.float32)

        # (c) multiply math on a sample interior pixel.
        r, c = 200, 311
        for band in range(3):
            expected = base_rgb[band, r, c] * (overlay_gray[r, c] / 255.0)
            got = out_rgb[band, r, c]
            # COG DEFLATE is lossless; allow a 1-LSB rounding tolerance.
            assert abs(got - expected) <= 1.0, (
                f"band {band} pixel ({r},{c}): got {got}, expected ~{expected:.2f} "
                f"(base={base_rgb[band, r, c]}, gray={overlay_gray[r, c]})"
            )


# ---------------------------------------------------------------------------
# Test 4 — invalid blend mode raises typed error
# ---------------------------------------------------------------------------


def test_compute_blended_composite_invalid_mode_raises(fake_storage):
    with pytest.raises(BlendedCompositeError) as exc_info:
        compute_blended_composite(
            base_layer_uri="/tmp/whatever_base.tif",
            overlay_layer_uri="/tmp/whatever_overlay.tif",
            blend_mode="not_a_mode",  # type: ignore[arg-type]
            _storage_client=fake_storage,
            _bucket="test-bucket",
        )
    assert exc_info.value.error_code == "INVALID_BLEND_MODE"


# ---------------------------------------------------------------------------
# Test 5 — LayerURI fields
# ---------------------------------------------------------------------------


def test_compute_blended_composite_returns_layer_uri_fields(fake_storage):
    with tempfile.TemporaryDirectory() as tmpdir:
        base_path = os.path.join(tmpdir, "landcover.tif")
        overlay_path = os.path.join(tmpdir, "hillshade.tif")
        _write_rgb_base(base_path, size=32)
        _write_gray_overlay(overlay_path, size=32)

        result = compute_blended_composite(
            base_layer_uri=base_path,
            overlay_layer_uri=overlay_path,
            blend_mode="multiply",
            _storage_client=fake_storage,
            _bucket="test-bucket",
        )

    assert result.layer_type == "raster"
    assert result.role == "context"
    assert result.units == "rgb"
    assert "blended" in result.layer_id
    assert "multiply" in result.layer_id
    assert result.name.startswith("Shaded")


# ---------------------------------------------------------------------------
# Test 6 — cache hit skips re-blend
# ---------------------------------------------------------------------------


def test_compute_blended_composite_cache_hit_skips_fetch(fake_storage):
    with tempfile.TemporaryDirectory() as tmpdir:
        base_path = os.path.join(tmpdir, "base.tif")
        overlay_path = os.path.join(tmpdir, "overlay.tif")
        _write_rgb_base(base_path, size=32)
        _write_gray_overlay(overlay_path, size=32)

        # First call populates the cache.
        first = compute_blended_composite(
            base_layer_uri=base_path,
            overlay_layer_uri=overlay_path,
            _storage_client=fake_storage,
            _bucket="test-bucket",
        )
        assert len(fake_storage.store) == 1

        # Second identical call must hit the cache: _run_blend not invoked.
        with patch(
            "grace2_agent.tools.compute_blended_composite._run_blend",
            side_effect=AssertionError("_run_blend should not run on cache hit"),
        ):
            second = compute_blended_composite(
                base_layer_uri=base_path,
                overlay_layer_uri=overlay_path,
                _storage_client=fake_storage,
                _bucket="test-bucket",
            )
        assert second.uri == first.uri
