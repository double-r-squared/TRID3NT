"""Regression tests for _extract_peak_depth_geotiff Y/X orientation guard (job-0086).

Coverage:
1. ``test_y_ascending_gets_flipped`` — synthetic netCDF with y ascending along rows
   (SFINCS south-at-row-0 convention) → after _extract_peak_depth_geotiff, high
   values land at the SOUTH edge (COG row index = height-1 since row 0 = north).
2. ``test_y_descending_is_idempotent`` — y already descending (north at row 0) →
   guard is a no-op; COG is identical to what a direct write would produce.
3. ``test_metrics_are_flip_invariant`` — max_depth_m / mean_depth_m / p95_depth_m /
   flooded_cell_count are identical regardless of y direction (they're aggregates).
4. ``test_x_descending_gets_flipped`` — synthetic netCDF with x descending along cols
   → belt-and-suspenders X guard fires and the COG columns are east-to-west corrected.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

# These imports are heavy (rasterio, xarray) — mark them skippable in
# environments that lack the deps (the CI .venv-agent has them).
pytest.importorskip("rasterio")
pytest.importorskip("xarray")
pytest.importorskip("numpy")


def _make_sfincs_nc(
    tmp_path: Path,
    *,
    x_vals: list[float],
    y_vals: list[float],
    hmax_pattern: np.ndarray,
    crs_wkt: str = "EPSG:32617",
    filename: str = "sfincs_map.nc",
) -> Path:
    """Write a minimal synthetic SFINCS-style netCDF to ``tmp_path/filename``.

    hmax_pattern shape must be (len(y_vals), len(x_vals)).
    """
    import xarray as xr
    import numpy as np_inner

    assert hmax_pattern.shape == (len(y_vals), len(x_vals)), (
        f"hmax_pattern shape {hmax_pattern.shape} != "
        f"({len(y_vals)}, {len(x_vals)})"
    )

    # Wrap hmax with a singleton time dimension (SFINCS emits (timemax=1, n, m)).
    hmax_3d = hmax_inner = hmax_pattern[np_inner.newaxis, :, :]  # (1, ny, nx)

    ds = xr.Dataset(
        {
            "hmax": xr.DataArray(
                hmax_3d,
                dims=["timemax", "n", "m"],
                attrs={"units": "m"},
            ),
            "crs": xr.DataArray(
                0,
                attrs={
                    "crs_wkt": (
                        # Use pyproj to emit a real WKT for the given EPSG string.
                        # Fallback: store the EPSG string itself (job-0063 path picks it up).
                        _epsg_to_wkt(crs_wkt)
                    ),
                    "grid_mapping_name": "transverse_mercator",
                },
            ),
        },
        coords={
            "x": xr.DataArray(np_inner.array(x_vals, dtype="float64"), dims=["m"]),
            "y": xr.DataArray(np_inner.array(y_vals, dtype="float64"), dims=["n"]),
        },
    )
    out = tmp_path / filename
    ds.to_netcdf(str(out))
    return out


def _epsg_to_wkt(epsg_str: str) -> str:
    """Convert 'EPSG:NNNN' to WKT via pyproj; fall back to the string itself."""
    try:
        import pyproj
        return pyproj.CRS.from_string(epsg_str).to_wkt()
    except Exception:
        return epsg_str


# ---------------------------------------------------------------------------
# Shared asymmetric depth pattern.
# High values (3.0 m) at y-index 0 (the low-y / south row in ascending y).
# Zero at y-index 3 (the high-y / north row).
# After the Y-flip, the high values should land at COG row (height-1).
# ---------------------------------------------------------------------------
# Use realistic UTM Zone 17N coordinates (Fort Myers area, EPSG:32617).
# x ≈ 420000 easting, y ≈ 2937000 northing — both well above 1000 so the
# CRS sanity check (projected CRS → |x| > 1000) in postprocess_flood passes.
X_VALS_ASC = [420000.0, 420030.0, 420060.0, 420090.0, 420120.0]  # 5 cols, 30 m spacing
Y_VALS_ASC = [2937000.0, 2937030.0, 2937060.0, 2937090.0]  # 4 rows, south → north
Y_VALS_DESC = [2937090.0, 2937060.0, 2937030.0, 2937000.0]  # 4 rows, north → south

# Row 0 = south (high-y index 0 in ascending convention = lowest y).
# Place a 3.0 m depth block on row 0 (all cols), zero elsewhere.
HMAX_SOUTH_HIGH = np.array(
    [
        [3.0, 3.0, 3.0, 3.0, 3.0],  # row 0: y=0 (south) → high depth
        [1.0, 1.0, 1.0, 1.0, 1.0],  # row 1: y=10
        [0.5, 0.5, 0.5, 0.5, 0.5],  # row 2: y=20
        [0.0, 0.0, 0.0, 0.0, 0.0],  # row 3: y=30 (north) → dry
    ],
    dtype="float32",
)

# Same depths but stored in descending y order (north at row 0) — no flip needed.
HMAX_NORTH_HIGH = HMAX_SOUTH_HIGH[::-1, :].copy()  # row 0 = north = dry


# ---------------------------------------------------------------------------
# Test 1: y ascending → guard fires, high values land at COG south edge
# ---------------------------------------------------------------------------

def test_y_ascending_gets_flipped(tmp_path: Path) -> None:
    """Y-ascending SFINCS data → guard flips rows; deep flood at south edge of COG."""
    from grace2_agent.workflows.postprocess_flood import _extract_peak_depth_geotiff
    import rasterio

    nc = _make_sfincs_nc(
        tmp_path,
        x_vals=X_VALS_ASC,
        y_vals=Y_VALS_ASC,
        hmax_pattern=HMAX_SOUTH_HIGH,
    )

    cog_path, metrics = _extract_peak_depth_geotiff(nc)
    try:
        with rasterio.open(cog_path) as src:
            data = src.read(1)  # shape (height, width)

        height = data.shape[0]
        # The south edge of the COG is at row (height-1) because row 0 = north.
        south_row = data[height - 1, :]  # should be high-depth (≥ 2.5 m)
        north_row = data[0, :]           # should be NaN/dry (≤ NODATA_DEPTH_M)

        # After flip the 3.0 m block is at the south (last row).
        assert np.all(south_row > 2.5), (
            f"Expected south row ≥ 2.5 m (deep flood) after Y-flip; "
            f"got {south_row}"
        )
        # North row should be NaN (masked dry) since original row 3 had 0 m depth.
        assert np.all(np.isnan(north_row)), (
            f"Expected north row to be NaN (dry) after Y-flip; got {north_row}"
        )
    finally:
        Path(cog_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 2: y descending → guard is idempotent, no flip
# ---------------------------------------------------------------------------

def test_y_descending_is_idempotent(tmp_path: Path) -> None:
    """Y-descending SFINCS data (north at row 0) → guard is a no-op."""
    from grace2_agent.workflows.postprocess_flood import _extract_peak_depth_geotiff
    import rasterio

    nc = _make_sfincs_nc(
        tmp_path,
        x_vals=X_VALS_ASC,
        y_vals=Y_VALS_DESC,
        hmax_pattern=HMAX_NORTH_HIGH,  # row 0 = north = 0.0 m (dry), row 3 = south = 3.0 m
    )

    cog_path, metrics = _extract_peak_depth_geotiff(nc)
    try:
        with rasterio.open(cog_path) as src:
            data = src.read(1)

        height = data.shape[0]
        south_row = data[height - 1, :]  # row 3 = south → high depth
        north_row = data[0, :]           # row 0 = north → dry

        assert np.all(south_row > 2.5), (
            f"Y-descending: expected south row ≥ 2.5 m; got {south_row}"
        )
        assert np.all(np.isnan(north_row)), (
            f"Y-descending: expected north row NaN (dry); got {north_row}"
        )
    finally:
        Path(cog_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 3: Aggregate metrics are flip-invariant
# ---------------------------------------------------------------------------

def test_metrics_are_flip_invariant(tmp_path: Path) -> None:
    """max/mean/p95/flooded_cell_count are identical for ascending vs descending y."""
    from grace2_agent.workflows.postprocess_flood import _extract_peak_depth_geotiff

    nc_asc = _make_sfincs_nc(
        tmp_path,
        x_vals=X_VALS_ASC,
        y_vals=Y_VALS_ASC,
        hmax_pattern=HMAX_SOUTH_HIGH,
        filename="sfincs_asc.nc",
    )
    nc_desc = _make_sfincs_nc(
        tmp_path,
        x_vals=X_VALS_ASC,
        y_vals=Y_VALS_DESC,
        hmax_pattern=HMAX_NORTH_HIGH,
        filename="sfincs_desc.nc",
    )

    _, m_asc = _extract_peak_depth_geotiff(nc_asc)
    _, m_desc = _extract_peak_depth_geotiff(nc_desc)

    for key in ("max_depth_m", "mean_depth_m", "p95_depth_m", "flooded_cell_count"):
        assert m_asc[key] == pytest.approx(m_desc[key], rel=1e-5), (
            f"Metric '{key}' differs between ascending ({m_asc[key]}) and "
            f"descending ({m_desc[key]}) y; must be flip-invariant."
        )


# ---------------------------------------------------------------------------
# Test 4: X descending → belt-and-suspenders X guard fires
# ---------------------------------------------------------------------------

def test_x_descending_gets_flipped(tmp_path: Path) -> None:
    """X-descending SFINCS data (east at col 0) → X-axis guard flips columns."""
    from grace2_agent.workflows.postprocess_flood import _extract_peak_depth_geotiff
    import rasterio

    # x descending: col 0 = east, last col = west (realistic UTM coords)
    x_vals_desc = [420120.0, 420090.0, 420060.0, 420030.0, 420000.0]

    # Place high depth in col 0 (east) of the source array; after X-flip
    # it should land in the west of the COG (col 0 of COG = west).
    hmax_east_high = np.zeros((4, 5), dtype="float32")
    hmax_east_high[:, 0] = 3.0   # col 0 in source = east (descending x)

    nc = _make_sfincs_nc(
        tmp_path,
        x_vals=x_vals_desc,
        y_vals=Y_VALS_DESC,  # Use descending y so Y-guard is no-op
        hmax_pattern=hmax_east_high,
    )

    cog_path, _ = _extract_peak_depth_geotiff(nc)
    try:
        with rasterio.open(cog_path) as src:
            data = src.read(1)
            width = src.width

        # After X-flip, col 0 of COG = west, col (width-1) = east.
        # The high-depth block (originally at east col in source) should now
        # be at col (width-1) of the COG.
        east_col = data[:, width - 1]
        west_col = data[:, 0]

        assert np.any(east_col > 2.5), (
            f"X-descending: expected east col (idx={width-1}) ≥ 2.5 m after X-flip; "
            f"got {east_col}"
        )
        assert np.all(np.isnan(west_col)), (
            f"X-descending: expected west col (idx=0) to be NaN/dry after X-flip; "
            f"got {west_col}"
        )
    finally:
        Path(cog_path).unlink(missing_ok=True)
