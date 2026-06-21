"""Quadtree (face-indexed UGRID) COG-write tests for postprocess_flood (P1).

The cht_sfincs quadtree solve writes a FACE-INDEXED UGRID ``sfincs_map.nc``:
fields live on ``nmesh2d_face`` (one scalar per quadtree face) with per-face
coordinates ``mesh2d_face_x`` / ``mesh2d_face_y`` — NOT the regular ``(n, m)``
grid + 1D ``x``/``y`` coords the legacy ``_write_verified_cog`` ``from_bounds``
path assumes. Before P1 that path would FAIL on real quadtree output (which also
means the existing DEPTH animation likely never ran on a true quadtree solve).

P1 added:
- ``_is_quadtree_output(ds)`` — probe (``nmesh2d_face`` in dims OR
  ``mesh2d_face_x`` in variables).
- ``_rasterize_face_field(values_1d, face_x, face_y, ...)`` — grid per-face
  scalars onto a regular metric raster (scipy nearest-neighbour) in the deck's
  projected (UTM) CRS.
- ``_write_verified_cog`` branches a face-indexed dataset through the rasterizer.

These tests build a SYNTHETIC face-indexed dataset and assert the writer
produces a valid georeferenced 2D COG — proving DEPTH-on-quadtree works too.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rasterio")
pytest.importorskip("xarray")
pytest.importorskip("scipy")
pytest.importorskip("pyproj")

from grace2_agent.workflows.postprocess_flood import (  # noqa: E402
    NODATA_DEPTH_M,
    PostprocessError,
    _is_quadtree_output,
    _rasterize_face_field,
    _read_face_coords,
    _write_verified_cog,
)


# Mexico Beach UTM zone 16N (matches the coastal North Star deck CRS).
_UTM16N = "EPSG:32616"
# A bbox over the Mexico Beach panhandle (EPSG:4326).
_BBOX = (-85.45, 29.93, -85.38, 29.98)


def _epsg_to_wkt(epsg_str: str) -> str:
    try:
        import pyproj

        return pyproj.CRS.from_string(epsg_str).to_wkt()
    except Exception:
        return epsg_str


def _make_quadtree_ds(
    *,
    n_faces: int = 400,
    n_steps: int = 0,
    rising: bool = False,
    crs: str = _UTM16N,
):
    """Build a synthetic FACE-INDEXED UGRID xr.Dataset.

    - ``hm0(nmesh2d_face[, time])`` — a wave-height-like field (also serves as a
      generic per-face scalar).
    - ``zs(nmesh2d_face[, time])`` + ``zb(nmesh2d_face)`` — water-level + bed
      level so the DEPTH path (zs - zb) resolves on a quadtree dataset.
    - ``mesh2d_face_x`` / ``mesh2d_face_y`` — per-face centroids in UTM metres.
    - ``crs`` variable carrying the WKT (CF-convention).

    When ``n_steps > 0`` the time dim is added (dims (nmesh2d_face, time) to
    mirror the verified ncoutput.F90 ordering); ``rising`` makes the field grow
    with the time index. Face centroids are laid out over a UTM box derived from
    the AOI bbox so the rasterizer's bbox-reproject path is exercised.
    """
    import xarray as xr
    from pyproj import Transformer

    tf = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    x0, y0 = tf.transform(_BBOX[0], _BBOX[1])
    x1, y1 = tf.transform(_BBOX[2], _BBOX[3])
    minx, maxx = min(x0, x1), max(x0, x1)
    miny, maxy = min(y0, y1), max(y0, y1)

    side = int(np.sqrt(n_faces))
    n_faces = side * side
    xs = np.linspace(minx + 50, maxx - 50, side)
    ys = np.linspace(miny + 50, maxy - 50, side)
    gx, gy = np.meshgrid(xs, ys)
    face_x = gx.ravel().astype("float64")
    face_y = gy.ravel().astype("float64")

    # Base per-face field: a smooth ramp 0..3 m across the domain (so masking +
    # aggregates are non-trivial), with a dry NW corner (values below threshold).
    base = np.linspace(0.0, 3.0, n_faces).astype("float32")

    data_vars: dict = {
        "crs": xr.DataArray(0, attrs={"crs_wkt": _epsg_to_wkt(crs)}),
        "mesh2d_face_x": xr.DataArray(face_x, dims=["nmesh2d_face"]),
        "mesh2d_face_y": xr.DataArray(face_y, dims=["nmesh2d_face"]),
    }

    if n_steps > 0:
        hm0 = np.zeros((n_faces, n_steps), dtype="float32")
        zs = np.zeros((n_faces, n_steps), dtype="float32")
        for t in range(n_steps):
            scale = (t / max(1, n_steps - 1)) if rising else 1.0
            hm0[:, t] = base * scale
            zs[:, t] = base * scale  # water level rising = base ramp
        data_vars["hm0"] = xr.DataArray(hm0, dims=["nmesh2d_face", "time"])
        data_vars["zs"] = xr.DataArray(zs, dims=["nmesh2d_face", "time"])
        data_vars["zb"] = xr.DataArray(
            np.zeros(n_faces, dtype="float32"), dims=["nmesh2d_face"]
        )
        coords = {"time": np.arange(n_steps)}
    else:
        data_vars["hm0"] = xr.DataArray(base, dims=["nmesh2d_face"])
        data_vars["zb"] = xr.DataArray(
            np.zeros(n_faces, dtype="float32"), dims=["nmesh2d_face"]
        )
        coords = {}

    return xr.Dataset(data_vars, coords=coords)


# --------------------------------------------------------------------------- #
# Probe
# --------------------------------------------------------------------------- #


def test_is_quadtree_output_detects_face_dim() -> None:
    ds = _make_quadtree_ds()
    assert _is_quadtree_output(ds) is True


def test_is_quadtree_output_false_for_regular_grid() -> None:
    import xarray as xr

    ds = xr.Dataset(
        {"hmax": xr.DataArray(np.zeros((1, 4, 5)), dims=["timemax", "n", "m"])},
        coords={
            "x": xr.DataArray(np.arange(5, dtype="float64"), dims=["m"]),
            "y": xr.DataArray(np.arange(4, dtype="float64"), dims=["n"]),
        },
    )
    assert _is_quadtree_output(ds) is False


def test_read_face_coords_returns_1d_arrays() -> None:
    ds = _make_quadtree_ds(n_faces=100)
    fx, fy = _read_face_coords(ds)
    assert fx.ndim == 1 and fy.ndim == 1
    assert fx.shape[0] == fy.shape[0] == 100


def test_read_face_coords_raises_when_absent() -> None:
    import xarray as xr

    ds = xr.Dataset({"hm0": xr.DataArray(np.zeros(3), dims=["nmesh2d_face"])})
    with pytest.raises(PostprocessError) as ei:
        _read_face_coords(ds)
    assert ei.value.error_code == "RUN_OUTPUT_UNEXPECTED_SHAPE"


# --------------------------------------------------------------------------- #
# Rasterizer
# --------------------------------------------------------------------------- #


def test_rasterize_face_field_produces_2d_grid() -> None:
    ds = _make_quadtree_ds(n_faces=400)
    fx, fy = _read_face_coords(ds)
    vals = ds["hm0"].values
    arr, transform = _rasterize_face_field(
        vals, fx, fy, crs=_UTM16N, bbox=_BBOX, resolution_m=30.0
    )
    assert arr.ndim == 2
    assert arr.shape[0] > 1 and arr.shape[1] > 1
    # The nearest-neighbour grid preserves the per-face value range (no invented
    # magnitudes beyond [min, max] of the source faces).
    finite = arr[np.isfinite(arr)]
    assert finite.size > 0
    assert finite.min() >= float(vals.min()) - 1e-4
    assert finite.max() <= float(vals.max()) + 1e-4
    # from_bounds transform: positive dx, negative dy (north-up).
    assert transform.a > 0
    assert transform.e < 0


def test_rasterize_face_field_length_mismatch_raises() -> None:
    with pytest.raises(PostprocessError) as ei:
        _rasterize_face_field(
            np.zeros(5), np.zeros(4), np.zeros(4), crs=_UTM16N, bbox=None
        )
    assert ei.value.error_code == "RUN_OUTPUT_UNEXPECTED_SHAPE"


# --------------------------------------------------------------------------- #
# _write_verified_cog on a face-indexed dataset → valid georeferenced COG
# --------------------------------------------------------------------------- #


def _assert_valid_projected_cog(cog_path: Path) -> None:
    import rasterio

    assert cog_path.exists()
    with rasterio.open(cog_path) as ds:
        assert ds.count == 1
        assert ds.width > 1 and ds.height > 1
        # Authored in the UTM (projected) CRS of the face coords.
        assert ds.crs is not None
        assert not ds.crs.is_geographic
        assert ds.crs.to_epsg() == 32616
        # Projected bounds are metric (|x| > 1000) — the CRS_TAG_MISMATCH guard
        # passes (projected tag, projected magnitudes).
        assert abs(ds.bounds.left) > 1000
        band = ds.read(1)
        assert band.dtype == np.dtype("float32")
        finite = band[np.isfinite(band)]
        assert finite.size > 0


def test_write_verified_cog_depth_on_quadtree(tmp_path: Path) -> None:
    """A face-indexed DEPTH field (zs.max(time) - zb) writes a valid COG — proves
    depth-on-quadtree works (the legacy from_bounds path would have failed)."""
    ds = _make_quadtree_ds(n_faces=400, n_steps=5, rising=True)
    depth = (ds["zs"].max(dim="time") - ds["zb"]).clip(min=0.0)
    cog, metrics = _write_verified_cog(
        depth.values,
        ds=ds,
        netcdf_path=tmp_path / "sfincs_map.nc",
        bbox=_BBOX,
    )
    try:
        _assert_valid_projected_cog(cog)
        assert metrics["units"] == "meters"
        assert metrics["crs"].endswith("32616")
        assert metrics["max_depth_m"] > NODATA_DEPTH_M
        assert metrics["flooded_cell_count"] > 0
    finally:
        cog.unlink(missing_ok=True)


def test_write_verified_cog_face_values_kwarg(tmp_path: Path) -> None:
    """Passing ``face_values`` explicitly routes any field through the rasterizer
    (the path postprocess_waves uses)."""
    ds = _make_quadtree_ds(n_faces=256)
    vals = ds["hm0"].values
    cog, metrics = _write_verified_cog(
        vals,
        ds=ds,
        netcdf_path=tmp_path / "sfincs_map.nc",
        face_values=vals,
        bbox=_BBOX,
        nodata_threshold_m=0.05,
    )
    try:
        _assert_valid_projected_cog(cog)
    finally:
        cog.unlink(missing_ok=True)


def test_write_verified_cog_quadtree_without_bbox(tmp_path: Path) -> None:
    """No bbox → the rasterizer bounds to the face extent (still valid COG)."""
    ds = _make_quadtree_ds(n_faces=225)
    vals = ds["hm0"].values
    cog, _metrics = _write_verified_cog(
        vals, ds=ds, netcdf_path=tmp_path / "sfincs_map.nc", face_values=vals
    )
    try:
        _assert_valid_projected_cog(cog)
    finally:
        cog.unlink(missing_ok=True)
