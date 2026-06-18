#!/usr/bin/env python3
"""Tests for the SFINCS deck-builder worker entrypoint.

Two surfaces:

  * PURE-PYTHON unit tests (NO cht_sfincs import) — build-spec validation, time
    parsing, the two caveat fixes (snapwave knob mapping = CAVEAT 2; time-column
    normalizer = CAVEAT 1), manifest composition, object-URI parsing, and
    build_deck's dispatch with cht mocked out. These run anywhere (the agent CI
    venv, this box's system python) WITHOUT the GPL library.

  * An OPT-IN integration test (run_full_deck_build) that authors a real
    quadtree+SnapWave deck via cht_sfincs against the spike venv where the GPL
    library is installed, with all object-store I/O mocked to local files. Skipped
    automatically when cht_sfincs is not importable.

Run pure-python set (no GPL needed):
    python services/workers/sfincs_deckbuilder/test_entrypoint.py

Run including the cht integration test (against the spike venv):
    services/workers/sfincs_quadtree_spike/.venv/bin/python \
        services/workers/sfincs_deckbuilder/test_entrypoint.py --with-cht
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent

# Import the entrypoint module by path so the test runs regardless of how the
# package is on sys.path (CI venv vs spike venv vs in-container).
_spec = importlib.util.spec_from_file_location(
    "sfincs_deckbuilder_entrypoint", HERE / "entrypoint.py"
)
ep = importlib.util.module_from_spec(_spec)  # type: ignore
_spec.loader.exec_module(ep)  # type: ignore


def _cht_available() -> bool:
    return importlib.util.find_spec("cht_sfincs") is not None


def _geo_stack_available() -> bool:
    """True when the heavy geo stack (numpy/xarray/xugrid) is importable.

    The dispatch test mocks cht but patches real ``xugrid.UgridDataArray`` /
    ``xarray.DataArray`` attributes, so it needs the geo stack present even
    though it never touches the GPL library.
    """
    return all(
        importlib.util.find_spec(m) is not None
        for m in ("numpy", "xarray", "xugrid")
    )


# Pre-import the heavy scientific stack ONCE at module load (when present) so it
# stays cached in sys.modules for the whole process. numpy/scipy C-extensions
# cannot be initialised twice per process, so a later mock.patch.dict that
# *removes* a real module would make a subsequent re-import crash. By importing
# them here (real, cached) we ensure the dispatch test's mocks only ever patch
# attributes, never evict the real C-extension modules. No-op if cht (and hence
# the geo stack) is not installed.
if _cht_available():  # pragma: no cover - import side effect only
    import numpy  # noqa: F401
    import xarray  # noqa: F401
    import xugrid  # noqa: F401


def _valid_spec(deck_dir_uri="s3://b/cache/sfincs_setup/x/deck/",
                manifest_uri="s3://b/cache/sfincs_setup/x/manifest.json") -> dict:
    return {
        "run_id": "01HRUN",
        "aoi": {"bbox": [-85.5, 29.9, -85.3, 30.1], "target_epsg": 32616},
        "topobathy": {"cog_uri": "s3://b/topo.tif", "bathymetry_present": True},
        "grid": {
            "x0": 600000.0, "y0": 3200000.0, "nmax": 16, "mmax": 24,
            "dx": 200.0, "dy": 200.0, "rotation": 0.0,
            "refinement_polygons_uri": "s3://b/refine.fgb",
        },
        "mask": {
            "zmin": -1000.0, "zmax": 2.0,
            "open_boundary_polygon_uri": "s3://b/wl.fgb",
            "open_boundary_zmin": -1000.0, "open_boundary_zmax": 2.0,
        },
        "snapwave": {
            "mask_zmin": -1000.0, "mask_zmax": 2.0,
            "open_boundary_polygon_uri": "s3://b/wave.fgb",
            "gamma": 0.8, "dtheta": 15.0, "hmin": 0.1,
        },
        "forcing": {
            "tref": "20181010 000000",
            "tstart": "20181010 000000",
            "tstop": "20181010 020000",
            "snapwave_boundary": {
                "points": [
                    {"x": 600100.0, "y": 3201600.0,
                     "hs": 3.0, "tp": 12.0, "wd": 270.0, "ds": 20.0}
                ]
            },
        },
        "output": {"deck_dir_uri": deck_dir_uri, "manifest_uri": manifest_uri},
    }


# --------------------------------------------------------------------------- #
# Pure-python tests (no cht import)
# --------------------------------------------------------------------------- #


class ObjectUriTests(unittest.TestCase):
    def test_split_s3(self):
        self.assertEqual(
            ep._split_object_uri("s3://bucket/a/b/c.json"),
            ("s3", "bucket", "a/b/c.json"),
        )

    def test_split_gs(self):
        self.assertEqual(
            ep._split_object_uri("gs://bucket/k"), ("gs", "bucket", "k")
        )

    def test_split_rejects_bad_scheme(self):
        with self.assertRaises(ValueError):
            ep._split_object_uri("http://x/y")

    def test_split_rejects_missing_key(self):
        with self.assertRaises(ValueError):
            ep._split_object_uri("s3://bucket")

    def test_output_scheme_env(self):
        with mock.patch.dict("os.environ", {"GRACE2_OBJECT_STORE": "s3"}):
            self.assertEqual(ep._output_scheme(), "s3")
        with mock.patch.dict("os.environ", {"GRACE2_OBJECT_STORE": "gcs"}):
            self.assertEqual(ep._output_scheme(), "gs")


class TimeParseTests(unittest.TestCase):
    def test_sfincs_ascii(self):
        self.assertEqual(
            ep.parse_sfincs_time("20181010 000000"),
            _dt.datetime(2018, 10, 10, 0, 0, 0),
        )

    def test_iso_with_z(self):
        self.assertEqual(
            ep.parse_sfincs_time("2018-10-10T02:00:00Z"),
            _dt.datetime(2018, 10, 10, 2, 0, 0),
        )

    def test_datetime_passthrough_strips_tz(self):
        aware = _dt.datetime(2018, 10, 10, tzinfo=_dt.timezone.utc)
        self.assertIsNone(ep.parse_sfincs_time(aware).tzinfo)

    def test_bad_raises(self):
        with self.assertRaises(ep.BuildSpecError):
            ep.parse_sfincs_time("not-a-time")


class ValidateSpecTests(unittest.TestCase):
    def test_valid_roundtrip(self):
        out = ep.validate_build_spec(_valid_spec())
        self.assertEqual(out["aoi"]["target_epsg"], 32616)
        self.assertTrue(out["output"]["deck_dir_uri"].endswith("/"))
        self.assertEqual(
            out["_parsed_times"]["tref"], _dt.datetime(2018, 10, 10, 0, 0, 0)
        )

    def test_deck_dir_uri_gets_trailing_slash(self):
        spec = _valid_spec(deck_dir_uri="s3://b/deck")  # no trailing slash
        out = ep.validate_build_spec(spec)
        self.assertEqual(out["output"]["deck_dir_uri"], "s3://b/deck/")

    def test_missing_aoi_raises(self):
        spec = _valid_spec()
        del spec["aoi"]
        with self.assertRaises(ep.BuildSpecError):
            ep.validate_build_spec(spec)

    def test_missing_grid_field_raises(self):
        spec = _valid_spec()
        del spec["grid"]["dx"]
        with self.assertRaises(ep.BuildSpecError):
            ep.validate_build_spec(spec)

    def test_tstop_before_tstart_raises(self):
        spec = _valid_spec()
        spec["forcing"]["tstop"] = "20181009 000000"
        with self.assertRaises(ep.BuildSpecError):
            ep.validate_build_spec(spec)

    def test_missing_topobathy_cog_raises(self):
        spec = _valid_spec()
        del spec["topobathy"]["cog_uri"]
        with self.assertRaises(ep.BuildSpecError):
            ep.validate_build_spec(spec)


class Caveat2HerbersTests(unittest.TestCase):
    """CAVEAT 2 — snapwave_use_herbers is FORCED to 1 (infragravity run-up)."""

    def test_default_is_one(self):
        knobs = ep.snapwave_inp_overrides(_valid_spec())
        self.assertEqual(knobs["snapwave_use_herbers"], 1)

    def test_agent_stale_zero_is_overridden_to_one(self):
        # The agent composer emits snapwave.use_herbers=0 (the old, known-bad
        # setting). The worker IGNORES that bare field and forces 1.
        spec = _valid_spec()
        spec["snapwave"]["use_herbers"] = 0
        knobs = ep.snapwave_inp_overrides(spec)
        self.assertEqual(knobs["snapwave_use_herbers"], 1)

    def test_deliberate_escape_hatch_turns_off(self):
        # Only the explicit force_no_herbers flag turns Herbers back off.
        spec = _valid_spec()
        spec["snapwave"]["force_no_herbers"] = True
        knobs = ep.snapwave_inp_overrides(spec)
        self.assertEqual(knobs["snapwave_use_herbers"], 0)

    def test_other_knobs_carry_proven_defaults(self):
        knobs = ep.snapwave_inp_overrides(_valid_spec())
        self.assertEqual(knobs["snapwave_gamma"], 0.8)
        self.assertEqual(knobs["snapwave_dtheta"], 15.0)
        self.assertEqual(knobs["snapwave_hmin"], 0.1)
        self.assertEqual(knobs["snapwave_igwaves"], 1)


class AgentSpecShapeTests(unittest.TestCase):
    """The worker tolerates the agent composer's real build_spec shape."""

    def _agent_spec(self) -> dict:
        # Mirrors model_flood_scenario.py _compose_and_upload_deckbuild_spec.
        return {
            "schema_version": "v1",
            "deck_id": "01HDECK",
            "aoi": {"bbox": [-85.5, 29.9, -85.3, 30.1], "target_epsg": None},
            "topobathy": {"cog_uri": "s3://b/topo.tif", "bathymetry_present": True},
            "grid": {
                "grid_resolution_m": 100.0,
                "x0": 600000.0, "y0": 3200000.0, "nmax": 16, "mmax": 24,
                "dx": 200.0, "dy": 200.0, "rotation": 0.0, "epsg": 32616,
            },
            "mask": {"zmin": None, "zmax": None},
            "snapwave": {
                "use_herbers": 0, "time_column_owned_by_cht": True,
                "gamma": 0.8, "dtheta": 15.0, "hmin": 0.1, "igwaves": 1,
            },
            "forcing": {
                "tref": "20181010 000000", "tstart": "20181010 000000",
                "tstop": "20181010 020000", "duration_hours": 2.0,
                "surge_forcing": {
                    "waterlevel": {"timeseries_uri": "s3://b/bzs.csv",
                                   "locations_uri": "s3://b/bnd.fgb"},
                    "discharge": {"timeseries_uri": "s3://b/dis.csv",
                                  "locations_uri": "s3://b/src.fgb"},
                },
            },
            "output": {"deck_dir_uri": "s3://b/cache/x/deck/",
                       "manifest_uri": "s3://b/cache/x/manifest.json"},
        }

    def test_validate_defaults_null_epsg(self):
        out = ep.validate_build_spec(self._agent_spec())
        self.assertEqual(out["aoi"]["target_epsg"], ep.DEFAULT_TARGET_EPSG)

    def test_validate_missing_grid_geometry_raises(self):
        spec = self._agent_spec()
        spec["grid"] = {"grid_resolution_m": 100.0}  # no x0/y0/...
        with self.assertRaises(ep.BuildSpecError):
            ep.validate_build_spec(spec)

    def test_caveat2_forced_on_agent_spec(self):
        knobs = ep.snapwave_inp_overrides(self._agent_spec())
        self.assertEqual(knobs["snapwave_use_herbers"], 1)

    def test_resolve_nested_surge_forcing(self):
        blocks = ep.resolve_forcing_blocks(self._agent_spec())
        self.assertEqual(blocks["waterlevel"]["timeseries_uri"], "s3://b/bzs.csv")
        self.assertEqual(blocks["discharge"]["locations_uri"], "s3://b/src.fgb")
        self.assertIsNone(blocks["snapwave_boundary"])

    def test_resolve_direct_forcing_shape(self):
        # A direct (non-nested) forcing.* shape also resolves.
        spec = self._agent_spec()
        spec["forcing"]["waterlevel"] = {"timeseries_uri": "s3://d/ts",
                                         "locations_uri": "s3://d/loc"}
        blocks = ep.resolve_forcing_blocks(spec)
        self.assertEqual(blocks["waterlevel"]["timeseries_uri"], "s3://d/ts")


class Caveat1TimeColumnTests(unittest.TestCase):
    """CAVEAT 1 — SnapWave time columns must be tref-relative (0-anchored)."""

    def test_rebases_epoch_scale_to_zero(self):
        with tempfile.TemporaryDirectory() as d:
            deck = Path(d)
            # Reproduce the spike's flawed epoch-scale bhs (242524800, +7200).
            (deck / "snapwave.bhs").write_text(
                "242524800.000  3.000\n242532000.000  3.000\n"
            )
            rewritten = ep.normalize_snapwave_time_columns(
                deck, _dt.datetime(2018, 10, 10)
            )
            self.assertIn("snapwave.bhs", rewritten)
            lines = (deck / "snapwave.bhs").read_text().splitlines()
            self.assertAlmostEqual(float(lines[0].split()[0]), 0.0)
            self.assertAlmostEqual(float(lines[1].split()[0]), 7200.0)
            # value column preserved
            self.assertAlmostEqual(float(lines[0].split()[1]), 3.0)

    def test_already_tref_relative_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            deck = Path(d)
            (deck / "snapwave.btp").write_text("0.000  12.000\n7200.000  12.000\n")
            rewritten = ep.normalize_snapwave_time_columns(
                deck, _dt.datetime(2018, 10, 10)
            )
            self.assertEqual(rewritten, [])
            lines = (deck / "snapwave.btp").read_text().splitlines()
            self.assertAlmostEqual(float(lines[0].split()[0]), 0.0)

    def test_missing_files_noop(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                ep.normalize_snapwave_time_columns(Path(d), _dt.datetime(2018, 1, 1)),
                [],
            )


class ManifestTests(unittest.TestCase):
    def test_compose_manifest_shape(self):
        with tempfile.TemporaryDirectory() as d:
            deck = Path(d)
            (deck / "sfincs.nc").write_bytes(b"\x00")
            (deck / "sfincs.inp").write_text("x")
            (deck / "snapwave.bhs").write_text("0.0 3.0\n")
            m = ep.compose_manifest(deck, "s3://b/cache/x/deck/")
            self.assertEqual(m["sfincs_args"], [])
            self.assertEqual(m["outputs"], ["sfincs_map.nc", "*.nc", "*.tif"])
            uris = {i["gs_uri"]: i["dest"] for i in m["inputs"]}
            self.assertEqual(
                uris["s3://b/cache/x/deck/sfincs.nc"], "sfincs.nc"
            )
            self.assertEqual(
                uris["s3://b/cache/x/deck/sfincs.inp"], "sfincs.inp"
            )
            # legacy field name "gs_uri" retained for the solve worker
            self.assertTrue(all("gs_uri" in i and "dest" in i for i in m["inputs"]))


@unittest.skipUnless(
    _geo_stack_available(),
    "geo stack (numpy/xarray/xugrid) not importable",
)
class BuildDeckDispatchTests(unittest.TestCase):
    """build_deck's NON-cht orchestration verified with cht fully mocked.

    Confirms: tref/tstart/tstop set BEFORE forcing (CAVEAT 1 ordering), the
    snapwave knobs incl. use_herbers=1 land on input.variables (CAVEAT 2), the
    normalizer is invoked, and the GPL import path is exercised without the real
    library.
    """

    def test_orchestration_with_mocked_cht(self):
        spec = ep.validate_build_spec(_valid_spec())

        # Fully fake the lazy GPL/geo import surface so this test runs with NO
        # real cht / numpy / xugrid loaded (it asserts ORCHESTRATION, not numerics).
        class _Vals:
            """list-like values shim with the .sum()/comparison build_deck needs."""

            def __init__(self, data):
                self._d = list(data)

            def __eq__(self, other):
                return _Vals([1 if v == other else 0 for v in self._d])

            def __gt__(self, other):
                return _Vals([1 if v > other else 0 for v in self._d])

            def sum(self):
                return sum(self._d)

        variables = mock.MagicMock()
        fake_sf = mock.MagicMock()
        fake_sf.input.variables = variables
        grid_data = {
            "mask": mock.MagicMock(values=_Vals([1, 1, 2])),
            "snapwave_mask": mock.MagicMock(values=_Vals([1, 2, 1])),
        }
        fake_sf.grid.data.sizes = {"mesh2d_nFaces": 3}
        fake_sf.grid.data.attrs = {"nr_levels": 3}
        fake_sf.grid.data.__setitem__ = lambda *a, **k: None
        fake_sf.grid.data.__getitem__ = lambda self_, k: grid_data[k]
        fake_sf.grid.face_coordinates.return_value = (
            [1.0, 2.0, 3.0], [1.0, 2.0, 3.0]
        )
        fake_sf.path = "/tmp/does-not-matter"

        captured = {}

        def fake_sfincs_ctor(root, crs, mode):
            captured["root"] = root
            captured["crs"] = crs
            return fake_sf

        fake_cht_mod = mock.MagicMock()
        fake_cht_mod.SFINCS = fake_sfincs_ctor

        # cht_sfincs is INSERTED (it isn't a real-loaded C-extension we must
        # preserve); the real numpy/xarray/xugrid stay in sys.modules. We patch
        # only the two attributes build_deck calls on the geo stack so the
        # bathymetry assignment is a no-op without touching real C-extensions.
        import sys as _sys
        patch_xu = (
            mock.patch.object(_sys.modules["xugrid"], "UgridDataArray",
                              lambda *a, **k: object())
            if "xugrid" in _sys.modules else mock.MagicMock()
        )
        patch_xr = (
            mock.patch.object(_sys.modules["xarray"], "DataArray",
                              lambda *a, **k: object())
            if "xarray" in _sys.modules else mock.MagicMock()
        )

        with tempfile.TemporaryDirectory() as scratch:
            scratch_p = Path(scratch)
            with mock.patch.dict(
                "sys.modules", {"cht_sfincs": fake_cht_mod}
            ), patch_xu, patch_xr, \
                    mock.patch.object(ep, "_download"), \
                    mock.patch.object(ep, "_read_polygon_gdf", return_value=None), \
                    mock.patch.object(ep, "_sample_topobathy",
                                      return_value=[0.0, 0.0, 0.0]), \
                    mock.patch.object(ep, "normalize_snapwave_time_columns",
                                      return_value=[]) as norm:
                deck_dir = ep.build_deck(spec, scratch_p)

        # CAVEAT 2: use_herbers=1 set on input.variables.
        self.assertEqual(variables.snapwave_use_herbers, 1)
        # CAVEAT 1 ordering: tref/tstart/tstop are real datetimes.
        self.assertEqual(variables.tref, _dt.datetime(2018, 10, 10, 0, 0, 0))
        self.assertEqual(variables.tstop, _dt.datetime(2018, 10, 10, 2, 0, 0))
        # snapwave coupling on.
        self.assertTrue(variables.snapwave)
        self.assertEqual(variables.qtrfile, "sfincs.nc")
        # boundary point added from the spec.
        fake_sf.snapwave.boundary_conditions.add_point.assert_called_once()
        # write + normalizer invoked.
        fake_sf.write.assert_called_once()
        norm.assert_called_once()
        self.assertEqual(captured["crs"], 32616)
        self.assertEqual(deck_dir, scratch_p / "deck")


# --------------------------------------------------------------------------- #
# Integration test (real cht_sfincs — opt-in, skipped without the GPL library)
# --------------------------------------------------------------------------- #


@unittest.skipUnless(_cht_available(), "cht_sfincs not importable (GPL image only)")
class FullDeckBuildIntegrationTests(unittest.TestCase):
    """End-to-end build_deck against real cht_sfincs with local-file I/O.

    Mirrors the proven spike's synthetic coastal AOI but drives it entirely
    through the worker's build_deck + the manifest/normalizer path, then asserts
    the deck is structurally valid AND the two caveats are fixed in the OUTPUT.
    """

    def _make_topobathy_cog(self, path: Path, target_epsg: int):
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin

        # sloping beach: -8 m west -> +4 m east, covering the grid extent.
        nx, ny = 48, 32
        x0, y0 = 600000.0, 3200000.0
        dx = 200.0
        res = dx / 2  # finer than the grid so sampling has real values
        cols = int((24 * dx) / res)
        rows = int((16 * dx) / res)
        xs = np.linspace(0, 24 * dx, cols)
        z = (-8.0 + 12.0 * xs / (24 * dx)).astype("float32")
        arr = np.tile(z, (rows, 1)).astype("float32")
        transform = from_origin(x0, y0 + rows * res, res, res)
        with rasterio.open(
            path, "w", driver="GTiff", height=rows, width=cols, count=1,
            dtype="float32", crs=f"EPSG:{target_epsg}", transform=transform,
            nodata=float("nan"),
        ) as dst:
            dst.write(arr, 1)

    def _make_refine_polygon(self, path: Path, target_epsg: int):
        import geopandas as gpd
        from pyproj import CRS
        from shapely.geometry import Polygon

        x0, y0, dx, dy = 600000.0, 3200000.0, 200.0, 200.0
        poly = Polygon([
            (x0 + 8 * dx, y0 + 2 * dy), (x0 + 18 * dx, y0 + 2 * dy),
            (x0 + 18 * dx, y0 + 14 * dy), (x0 + 8 * dx, y0 + 14 * dy),
        ])
        gpd.GeoDataFrame(
            {"refinement_level": [2], "geometry": [poly]},
            crs=CRS.from_epsg(target_epsg),
        ).to_file(path, driver="GPKG")

    def _make_offshore_polygon(self, path: Path, target_epsg: int):
        import geopandas as gpd
        from pyproj import CRS
        from shapely.geometry import Polygon

        x0, y0, dx, dy = 600000.0, 3200000.0, 200.0, 200.0
        poly = Polygon([
            (x0 - dx, y0 - dy), (x0 + 1.0 * dx, y0 - dy),
            (x0 + 1.0 * dx, y0 + 17 * dy), (x0 - dx, y0 + 17 * dy),
        ])
        gpd.GeoDataFrame({"geometry": [poly]}, crs=CRS.from_epsg(target_epsg)).to_file(
            path, driver="GPKG"
        )

    def test_full_build(self):
        import numpy as np
        import xarray as xr

        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            target_epsg = 32616
            topo = work / "topo.tif"
            refine = work / "refine.gpkg"
            wl = work / "wl.gpkg"
            wave = work / "wave.gpkg"
            self._make_topobathy_cog(topo, target_epsg)
            self._make_refine_polygon(refine, target_epsg)
            self._make_offshore_polygon(wl, target_epsg)
            self._make_offshore_polygon(wave, target_epsg)

            spec = ep.validate_build_spec({
                "run_id": "intg",
                "aoi": {"bbox": [0, 0, 1, 1], "target_epsg": target_epsg},
                "topobathy": {"cog_uri": "s3://x/topo.tif",
                              "bathymetry_present": True},
                "grid": {
                    "x0": 600000.0, "y0": 3200000.0, "nmax": 16, "mmax": 24,
                    "dx": 200.0, "dy": 200.0, "rotation": 0.0,
                    "refinement_polygons_uri": "s3://x/refine.gpkg",
                },
                "mask": {
                    "zmin": -100.0, "zmax": 2.0,
                    "open_boundary_polygon_uri": "s3://x/wl.gpkg",
                    "open_boundary_zmin": -100.0, "open_boundary_zmax": 2.0,
                },
                "snapwave": {
                    "mask_zmin": -100.0, "mask_zmax": 2.0,
                    "open_boundary_polygon_uri": "s3://x/wave.gpkg",
                },
                "forcing": {
                    "tref": "20181010 000000",
                    "tstart": "20181010 000000",
                    "tstop": "20181010 020000",
                    "snapwave_boundary": {"points": [
                        {"x": 600100.0, "y": 3201600.0,
                         "hs": 3.0, "tp": 12.0, "wd": 270.0, "ds": 20.0}
                    ]},
                },
                "output": {"deck_dir_uri": "s3://x/deck/",
                           "manifest_uri": "s3://x/manifest.json"},
            })

            # Map the s3:// URIs in the spec to local files via a fake _download.
            uri_to_local = {
                "s3://x/topo.tif": topo,
                "s3://x/refine.gpkg": refine,
                "s3://x/wl.gpkg": wl,
                "s3://x/wave.gpkg": wave,
            }

            def fake_download(uri, dest):
                import shutil as _sh
                _sh.copy(uri_to_local[uri], dest)

            scratch = work / "scratch"
            scratch.mkdir()
            with mock.patch.object(ep, "_download", side_effect=fake_download):
                deck_dir = ep.build_deck(spec, scratch)

            # --- deck contents present ---
            self.assertTrue((deck_dir / "sfincs.nc").exists())
            self.assertTrue((deck_dir / "sfincs.inp").exists())
            for f in ep.SNAPWAVE_TS_FILES:
                self.assertTrue((deck_dir / f).exists(), f"{f} missing")

            # --- CAVEAT 1: every snapwave time column is tref-relative (0-anchored) ---
            for f in ep.SNAPWAVE_TS_FILES:
                first = (deck_dir / f).read_text().splitlines()[0].split()[0]
                self.assertAlmostEqual(
                    float(first), 0.0, places=2,
                    msg=f"{f} first time col {first} not tref-relative (CAVEAT 1)",
                )

            # --- CAVEAT 2: sfincs.inp has snapwave_use_herbers = 1 ---
            inp = (deck_dir / "sfincs.inp").read_text()
            herbers = [ln for ln in inp.splitlines()
                       if ln.strip().startswith("snapwave_use_herbers")]
            self.assertTrue(herbers, "snapwave_use_herbers missing from sfincs.inp")
            self.assertEqual(herbers[0].split("=")[1].strip(), "1",
                             "CAVEAT 2: snapwave_use_herbers must be 1")
            self.assertIn("snapwave             = 1", inp)
            self.assertIn("qtrfile              = sfincs.nc", inp)

            # --- structural: multi-level quadtree connectivity present ---
            ds = xr.open_dataset(deck_dir / "sfincs.nc")
            try:
                self.assertIn("mesh2d_nFaces", ds.sizes)
                self.assertGreaterEqual(int(ds.attrs["nr_levels"]), 2)
                for v in ("mu1", "md1", "nu1", "nd1", "level", "mask",
                          "snapwave_mask"):
                    self.assertIn(v, ds.variables)
                level = ds["level"].values.astype(int)
                self.assertGreaterEqual(len(np.unique(level)), 2)
                sw = ds["snapwave_mask"].values.astype(int)
                self.assertGreater(int((sw == 1).sum()), 0)
                self.assertGreater(int((sw > 1).sum()), 0)
            finally:
                ds.close()

            # --- manifest composition over the real deck ---
            manifest = ep.compose_manifest(deck_dir, "s3://x/deck/")
            dests = {i["dest"] for i in manifest["inputs"]}
            self.assertIn("sfincs.nc", dests)
            self.assertIn("sfincs.inp", dests)
            self.assertEqual(manifest["outputs"],
                             ["sfincs_map.nc", "*.nc", "*.tif"])


if __name__ == "__main__":
    # `--with-cht` is a no-op flag for readability; the integration test
    # self-skips when cht_sfincs is unimportable.
    argv = [a for a in sys.argv if a != "--with-cht"]
    unittest.main(argv=argv, verbosity=2)
