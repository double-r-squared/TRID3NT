"""publish_layer F32 (benign vector no-op) + F33 (overview enforcement) tests.

F32 — BENIGN VECTOR REJECTION:
  publish_layer is RASTER-ONLY. Vectors (.fgb/.geojson/...) handed to it are
  ALREADY rendered on the map inline (Wave 4.9 GeoJSON via add_loaded_layer).
  Pre-F32 the tool RAISED ``PUBLISH_LAYER_VECTOR_NOT_RASTER`` → a red
  "Publishing layer… failed" card on a layer the user can already see. F32 turns
  that into a benign, NON-error result: no raise (so the step card stays green),
  no tile template, no ``observe_published_layer`` registration (no hanging-tile
  face), and a calm function_response so the agent narrates honestly + does not
  re-call. Covered on BOTH the s3 (AWS/TiTiler) and gs (GCS/worker) branches.

F33 — OVERVIEW ENFORCEMENT:
  A no-overview COG renders SPOTTY (per-strip range requests time out cold;
  TiTiler/QGIS Server can't downsample for low zooms). Before a raster's tile
  template / WMS face is registered, publish_layer now VALIDATES the COG has
  overviews and AUTO-TRANSLATES to a tiled+overview COG when missing (reusing
  ``compute_hillshade._translate_to_cog`` with a rasterio fallback), then
  publishes THAT. A raster that ALREADY has overviews is published unchanged.

These exercise the pure-helper layer (``_ensure_raster_has_overviews``,
``_is_vector_uri``, ``_benign_vector_noop``) plus the s3 branch end-to-end with
real GeoTIFF bytes built by rasterio — no Cloud Run / GCS / TiTiler network I/O.
"""

from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile

from grace2_agent.tools.publish_layer import (
    PublishLayerError,
    _benign_vector_noop,
    _build_cog_with_overviews,
    _ensure_raster_has_overviews,
    _is_vector_uri,
    _raster_has_overviews,
    publish_layer,
)


# --------------------------------------------------------------------------- #
# GeoTIFF byte builders (real rasterio rasters so overview inspection is real)
# --------------------------------------------------------------------------- #


def _flat_geotiff_bytes(size: int = 1024) -> bytes:
    """A georeferenced single-band GeoTIFF with NO overviews."""
    data = (np.random.rand(size, size) * 255).astype("uint8")
    transform = rasterio.transform.from_origin(0, size, 1, 1)
    with MemoryFile() as mem:
        with mem.open(
            driver="GTiff",
            height=size,
            width=size,
            count=1,
            dtype="uint8",
            crs="EPSG:4326",
            transform=transform,
        ) as dst:
            dst.write(data, 1)
        return mem.read()


def _cog_with_overviews_bytes(size: int = 1024) -> bytes:
    """A tiled GeoTIFF that HAS overviews built in (the desired publish shape)."""
    flat = _flat_geotiff_bytes(size)
    out = _build_cog_with_overviews(flat)
    assert out is not None, "test setup: could not build an overview COG"
    return out


# --------------------------------------------------------------------------- #
# F32 — benign vector no-op (helpers)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "uri",
    [
        "s3://b/roads.fgb",
        "s3://b/rivers.geojson",
        "gs://b/admin.geojson",
        "s3://b/parcels.geoparquet",
        "s3://b/x.parquet",
        "gs://b/y.gpkg",
        "s3://b/z.shp",
        "s3://b/dir/data.json",
        "S3://B/UPPER.FGB",  # case-insensitive
        "s3://b/trailing.fgb/",  # trailing slash tolerated
    ],
)
def test_is_vector_uri_true_for_vector_extensions(uri: str) -> None:
    assert _is_vector_uri(uri) is True


@pytest.mark.parametrize(
    "uri",
    [
        "s3://b/flood_depth_peak.tif",
        "gs://b/hillshade.tif",
        "s3://b/relief.tiff",
        "https://host/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=x",
    ],
)
def test_is_vector_uri_false_for_rasters(uri: str) -> None:
    assert _is_vector_uri(uri) is False


def test_benign_vector_noop_is_non_error_string() -> None:
    """The benign signal does NOT raise and is a clear, honest message."""
    msg = _benign_vector_noop("s3://b/roads.fgb", "roads-layer")
    assert isinstance(msg, str)
    assert "noop" in msg.lower()
    assert "vector" in msg.lower()
    # Must steer the LLM away from retrying.
    assert "roads-layer" in msg


# --------------------------------------------------------------------------- #
# F32 — benign vector no-op (s3 branch end-to-end)
# --------------------------------------------------------------------------- #


@pytest.fixture()
def _s3_titiler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRACE2_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("GRACE2_TILE_SERVER_BASE", "https://cf.example.net")


def test_publish_layer_vector_s3_returns_benign_no_template_no_register(
    _s3_titiler: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A vector on the s3 branch: NO raise, NO tile template, NO registration."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        "grace2_agent.tools.publish_layer.observe_published_layer",
        lambda *a, **k: calls.append((a, k)),
    )

    result = publish_layer(layer_uri="s3://bucket/roads.fgb", layer_id="roads")

    # 1. It returned a benign string (no exception).
    assert isinstance(result, str)
    # 2. It is NOT a tile template (no hanging-tile face minted).
    assert "/cog/tiles/" not in result
    assert "{z}/{x}/{y}" not in result
    assert result.startswith("noop")
    # 3. observe_published_layer was NEVER called for the vector.
    assert calls == [], f"vector no-op must not register a layer face; got {calls}"


def test_publish_layer_geojson_s3_returns_benign_not_error(
    _s3_titiler: None,
) -> None:
    """A .geojson vector also returns benign (does not raise)."""
    out = publish_layer(layer_uri="s3://bucket/rivers.geojson", layer_id="rivers")
    assert out.startswith("noop")


def test_publish_layer_raster_s3_still_raises_for_non_s3(_s3_titiler: None) -> None:
    """A non-vector, non-s3 raster handle still raises (unchanged behavior)."""
    with pytest.raises(PublishLayerError) as exc:
        publish_layer(layer_uri="gs://legacy/bucket/x.tif", layer_id="flood")
    assert exc.value.error_code == "LAYER_URI_NOT_FOUND"


# --------------------------------------------------------------------------- #
# F33 — overview detection
# --------------------------------------------------------------------------- #


def test_raster_has_overviews_false_for_flat_geotiff() -> None:
    assert _raster_has_overviews(_flat_geotiff_bytes()) is False


def test_raster_has_overviews_true_for_overview_cog() -> None:
    assert _raster_has_overviews(_cog_with_overviews_bytes()) is True


def test_raster_has_overviews_none_for_non_raster() -> None:
    """Unreadable / non-raster bytes → None (cannot determine → fail-open)."""
    assert _raster_has_overviews(b"NOT A RASTER") is None


def test_build_cog_with_overviews_adds_overviews() -> None:
    """The auto-translate produces a COG whose band-1 overviews are non-empty."""
    flat = _flat_geotiff_bytes()
    assert _raster_has_overviews(flat) is False
    cog = _build_cog_with_overviews(flat)
    assert cog is not None
    assert _raster_has_overviews(cog) is True


# --------------------------------------------------------------------------- #
# F33 — _ensure_raster_has_overviews (local-path round trip)
# --------------------------------------------------------------------------- #


def test_ensure_overviews_auto_translates_when_missing(tmp_path) -> None:
    """A no-overview COG is auto-translated; the returned URI points at a NEW
    overview-bearing COG (the original is left untouched)."""
    src = tmp_path / "flat.tif"
    src.write_bytes(_flat_geotiff_bytes())

    out_uri = _ensure_raster_has_overviews(str(src))

    # The published URI must differ from the source (a fresh sibling).
    assert out_uri != str(src), "missing-overview raster must be auto-translated"
    with rasterio.open(out_uri) as ds:
        assert ds.overviews(1), "auto-translated COG must carry overviews"
    # The original is untouched (still no overviews).
    with rasterio.open(str(src)) as orig:
        assert orig.overviews(1) == []


def test_ensure_overviews_unchanged_when_already_present(tmp_path) -> None:
    """A COG that ALREADY has overviews is published unchanged (same URI)."""
    src = tmp_path / "good_cog.tif"
    src.write_bytes(_cog_with_overviews_bytes())

    out_uri = _ensure_raster_has_overviews(str(src))

    assert out_uri == str(src), "overview-bearing COG must publish unchanged"


def test_ensure_overviews_fail_open_on_unreadable(tmp_path) -> None:
    """An unreadable raster fails open: URI returned unchanged (legacy)."""
    src = tmp_path / "junk.tif"
    src.write_bytes(b"NOT A RASTER")
    out_uri = _ensure_raster_has_overviews(str(src))
    assert out_uri == str(src)


def test_ensure_overviews_fail_open_on_missing_path() -> None:
    """A non-existent local path fails open (read returns None)."""
    out_uri = _ensure_raster_has_overviews("/nonexistent/path/raster.tif")
    assert out_uri == "/nonexistent/path/raster.tif"


# --------------------------------------------------------------------------- #
# F33 — s3 branch end-to-end (auto-translate then tile template)
# --------------------------------------------------------------------------- #


def test_publish_layer_s3_auto_translates_no_overview_cog(
    _s3_titiler: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """s3 raster lacking overviews: publish_layer reads it, auto-translates to a
    NEW overview COG, and bakes the NEW s3 URI into the tile template."""
    flat_bytes = _flat_geotiff_bytes()
    written: dict[str, bytes] = {}

    def _fake_read(uri: str) -> bytes:
        assert uri == "s3://bucket/runs/flat.tif"
        return flat_bytes

    def _fake_write(uri: str, cog_bytes: bytes) -> str:
        # Simulate the s3 sibling write; assert the bytes carry overviews.
        assert _raster_has_overviews(cog_bytes) is True
        new_uri = "s3://bucket/runs/overviews/NEWULID.tif"
        written[new_uri] = cog_bytes
        return new_uri

    monkeypatch.setattr(
        "grace2_agent.tools.publish_layer._read_raster_bytes", _fake_read
    )
    monkeypatch.setattr(
        "grace2_agent.tools.publish_layer._write_overview_cog", _fake_write
    )

    template = publish_layer(
        layer_uri="s3://bucket/runs/flat.tif", layer_id="flood-demo"
    )

    # The template must reference the AUTO-TRANSLATED (overview) COG, NOT the
    # original no-overview source.
    assert "overviews%2FNEWULID.tif" in template or "overviews/NEWULID.tif" in template
    assert "runs%2Fflat.tif" not in template
    assert template.startswith("https://cf.example.net/cog/tiles/")
    assert written, "an overview COG should have been written"


def test_publish_layer_s3_overview_cog_published_unchanged(
    _s3_titiler: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """s3 raster that ALREADY has overviews: URI unchanged, no re-translate."""
    good = _cog_with_overviews_bytes()

    monkeypatch.setattr(
        "grace2_agent.tools.publish_layer._read_raster_bytes",
        lambda uri: good,
    )

    def _must_not_write(uri: str, cog_bytes: bytes) -> str:  # pragma: no cover
        raise AssertionError("must NOT re-translate an overview-bearing COG")

    monkeypatch.setattr(
        "grace2_agent.tools.publish_layer._write_overview_cog", _must_not_write
    )

    from urllib.parse import quote

    template = publish_layer(
        layer_uri="s3://bucket/runs/good.tif", layer_id="flood-demo"
    )
    # Original s3 URI is what rides in ?url=.
    assert f"?url={quote('s3://bucket/runs/good.tif', safe='')}" in template
