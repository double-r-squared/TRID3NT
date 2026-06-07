"""Unit tests for the PyQGIS worker raster publish path (job-0062).

These tests exercise the pure-Python helpers introduced in job-0062 without
requiring a live QGIS installation (the QGIS imports are mocked out).

Coverage:
1. ``test_build_wms_url_format`` — ``_build_wms_url`` produces the correct
   MAP= + LAYERS= query string matching the Map.tsx convention.
2. ``test_resolve_style_preset_path_by_name_container_first`` — when the
   container path exists, it is returned in preference to the repo path.
3. ``test_resolve_style_preset_path_by_name_repo_fallback`` — when the
   container path does not exist, the repo path is returned.
4. ``test_resolve_style_preset_path_by_name_missing`` — when neither path
   exists, None is returned (non-fatal; caller decides severity).
5. ``test_worker_result_to_dict_includes_wms_url`` — ``WorkerResult.to_dict``
   includes ``wms_url`` when populated (Pub/Sub envelope carries the URL).
6. ``test_append_raster_layer_calls_addmaplayer`` — ``_append_raster_layer``
   calls ``project.addMapLayer`` + ``_apply_style_preset`` with the correct
   arguments (QgsRasterLayer mocked out).
7. ``test_publish_raster_round_trip_regression_polygon_path`` — calling
   ``worker_round_trip`` still dispatches the polygon path and NOT
   ``_append_raster_layer`` (regression-guard: existing path untouched).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Shared: stub out all qgis.* imports so tests run in pure-Python envs.
# ---------------------------------------------------------------------------

_QGIS_STUBS = {
    "qgis": MagicMock(),
    "qgis.core": MagicMock(),
    "qgis.PyQt": MagicMock(),
    "qgis.PyQt.QtCore": MagicMock(),
}


def _patch_qgis():
    """Return a context manager that stubs all qgis imports."""
    return patch.dict(sys.modules, _QGIS_STUBS)


# ---------------------------------------------------------------------------
# Test 1 — _build_wms_url
# ---------------------------------------------------------------------------


def test_build_wms_url_format(tmp_path: Path) -> None:
    """``_build_wms_url`` produces the correct MAP= + LAYERS= URL."""
    with _patch_qgis():
        from services.workers.pyqgis.worker import _build_wms_url

    with patch.dict(os.environ, {"QGIS_SERVER_URL": "https://qgis.test.example.com/ogc/wms"}):
        url = _build_wms_url("grace2-sample.qgs", "flood-depth-peak-abc")

    assert url == (
        "https://qgis.test.example.com/ogc/wms"
        "?MAP=/mnt/qgs/grace2-sample.qgs"
        "&LAYERS=flood-depth-peak-abc"
    ), f"unexpected WMS URL: {url}"


def test_build_wms_url_default_server() -> None:
    """``_build_wms_url`` uses the default QGIS Server URL when env is unset."""
    with _patch_qgis():
        from services.workers.pyqgis.worker import _build_wms_url, DEFAULT_QGIS_SERVER_URL

    # Ensure no override env var is set.
    env = {k: v for k, v in os.environ.items() if k != "QGIS_SERVER_URL"}
    with patch.dict(os.environ, env, clear=True):
        url = _build_wms_url("grace2-sample.qgs", "flood-depth-peak-abc")

    assert url.startswith(DEFAULT_QGIS_SERVER_URL), (
        f"URL should start with default server URL; got {url!r}"
    )
    assert "MAP=/mnt/qgs/grace2-sample.qgs" in url
    assert "LAYERS=flood-depth-peak-abc" in url


# ---------------------------------------------------------------------------
# Test 2 — _resolve_style_preset_path_by_name: container path takes precedence
# ---------------------------------------------------------------------------


def test_resolve_style_preset_path_by_name_container_first(tmp_path: Path) -> None:
    """Container path is returned when it exists."""
    with _patch_qgis():
        import importlib
        import services.workers.pyqgis.worker as worker_module
        importlib.reload(worker_module)  # refresh after stub

        # Create a fake container dir and repo dir.
        container_dir = tmp_path / "container_styles"
        repo_dir = tmp_path / "repo_styles"
        container_dir.mkdir()
        repo_dir.mkdir()

        # Create the preset in both locations.
        (container_dir / "continuous_flood_depth.qml").write_text("container")
        (repo_dir / "continuous_flood_depth.qml").write_text("repo")

        with (
            patch.object(worker_module, "STYLE_PRESET_CONTAINER_DIR", container_dir),
            patch.object(worker_module, "STYLE_PRESET_REPO_DIR", repo_dir),
        ):
            result = worker_module._resolve_style_preset_path_by_name("continuous_flood_depth")

    assert result is not None
    assert result.read_text() == "container", (
        f"expected container path to win; got {result}"
    )


# ---------------------------------------------------------------------------
# Test 3 — _resolve_style_preset_path_by_name: repo fallback
# ---------------------------------------------------------------------------


def test_resolve_style_preset_path_by_name_repo_fallback(tmp_path: Path) -> None:
    """Repo path is returned when container path does not exist."""
    with _patch_qgis():
        import importlib
        import services.workers.pyqgis.worker as worker_module
        importlib.reload(worker_module)

        container_dir = tmp_path / "container_styles_empty"
        repo_dir = tmp_path / "repo_styles"
        container_dir.mkdir()
        repo_dir.mkdir()

        # Only create in repo.
        (repo_dir / "continuous_flood_depth.qml").write_text("repo_only")

        with (
            patch.object(worker_module, "STYLE_PRESET_CONTAINER_DIR", container_dir),
            patch.object(worker_module, "STYLE_PRESET_REPO_DIR", repo_dir),
        ):
            result = worker_module._resolve_style_preset_path_by_name("continuous_flood_depth")

    assert result is not None
    assert result.read_text() == "repo_only"


# ---------------------------------------------------------------------------
# Test 4 — _resolve_style_preset_path_by_name: missing → None
# ---------------------------------------------------------------------------


def test_resolve_style_preset_path_by_name_missing(tmp_path: Path) -> None:
    """None is returned when neither container nor repo path exists."""
    with _patch_qgis():
        import importlib
        import services.workers.pyqgis.worker as worker_module
        importlib.reload(worker_module)

        container_dir = tmp_path / "no_container"
        repo_dir = tmp_path / "no_repo"
        container_dir.mkdir()
        repo_dir.mkdir()

        with (
            patch.object(worker_module, "STYLE_PRESET_CONTAINER_DIR", container_dir),
            patch.object(worker_module, "STYLE_PRESET_REPO_DIR", repo_dir),
        ):
            result = worker_module._resolve_style_preset_path_by_name("nonexistent_preset")

    assert result is None, f"expected None for missing preset; got {result}"


# ---------------------------------------------------------------------------
# Test 5 — WorkerResult.to_dict includes wms_url
# ---------------------------------------------------------------------------


def test_worker_result_to_dict_includes_wms_url() -> None:
    """WorkerResult.to_dict carries wms_url when populated."""
    with _patch_qgis():
        from services.workers.pyqgis.types import WorkerResult

    result = WorkerResult(
        qgs_uri="gs://test-qgs/grace2-sample.qgs",
        layers_before=["basemap"],
        layers_after=["basemap", "flood-depth-peak-abc"],
        notify_message_id=None,
        status="ok",
        error=None,
        qgs_version="3.40.3",
        wms_url=(
            "https://qgis.example.com/ogc/wms"
            "?MAP=/mnt/qgs/grace2-sample.qgs"
            "&LAYERS=flood-depth-peak-abc"
        ),
    )

    d = result.to_dict()
    assert "wms_url" in d, "wms_url must appear in to_dict() when populated"
    assert d["wms_url"] == (
        "https://qgis.example.com/ogc/wms"
        "?MAP=/mnt/qgs/grace2-sample.qgs"
        "&LAYERS=flood-depth-peak-abc"
    )


def test_worker_result_to_dict_omits_wms_url_when_none() -> None:
    """WorkerResult.to_dict does NOT include wms_url when None."""
    with _patch_qgis():
        from services.workers.pyqgis.types import WorkerResult

    result = WorkerResult(
        qgs_uri="gs://test-qgs/grace2-sample.qgs",
        layers_before=["basemap"],
        layers_after=["basemap", "demo-polygon"],
        notify_message_id=None,
        status="ok",
        error=None,
        qgs_version="3.40.3",
        wms_url=None,
    )

    d = result.to_dict()
    assert "wms_url" not in d, (
        "wms_url must be omitted from to_dict() when None "
        "(backward-compat: polygon path consumers do not expect this key)"
    )


# ---------------------------------------------------------------------------
# Test 6 — _append_raster_layer: addMapLayer + _apply_style_preset called
# ---------------------------------------------------------------------------


def test_append_raster_layer_calls_addmaplayer(tmp_path: Path) -> None:
    """``_append_raster_layer`` calls ``project.addMapLayer`` + style application."""
    # We need to import with QgsRasterLayer mocked so QGIS doesn't actually load.
    mock_layer = MagicMock()
    mock_layer.isValid.return_value = True
    mock_layer.name.return_value = "flood-depth-peak-test"

    mock_project = MagicMock()

    style_path = tmp_path / "continuous_flood_depth.qml"
    style_path.write_text("<qgis/>")

    qgis_core_mock = MagicMock()
    qgis_core_mock.QgsRasterLayer.return_value = mock_layer

    with patch.dict(sys.modules, {**_QGIS_STUBS, "qgis.core": qgis_core_mock}):
        import importlib
        import services.workers.pyqgis.worker as worker_module
        importlib.reload(worker_module)

        # Patch _apply_style_preset to observe calls.
        with patch.object(worker_module, "_apply_style_preset") as mock_style:
            result_layer = worker_module._append_raster_layer(
                project=mock_project,
                raster_uri="/vsigs/grace-2-hazard-prod-runs/run-abc/flood_depth_peak.tif",
                layer_id="flood-depth-peak-abc",
                style_qml_path=style_path,
            )

    # addMapLayer must be called once with the created layer.
    mock_project.addMapLayer.assert_called_once_with(mock_layer)
    # _apply_style_preset must be called with the layer + the style path.
    mock_style.assert_called_once_with(mock_layer, style_path)
    # Return value is the layer.
    assert result_layer is mock_layer


def test_append_raster_layer_raises_on_invalid_layer(tmp_path: Path) -> None:
    """``_append_raster_layer`` raises ``WorkerError`` when the layer is invalid."""
    mock_layer = MagicMock()
    mock_layer.isValid.return_value = False

    mock_project = MagicMock()

    qgis_core_mock = MagicMock()
    qgis_core_mock.QgsRasterLayer.return_value = mock_layer

    with patch.dict(sys.modules, {**_QGIS_STUBS, "qgis.core": qgis_core_mock}):
        import importlib
        import services.workers.pyqgis.worker as worker_module
        importlib.reload(worker_module)
        from services.workers.pyqgis.types import WorkerError

        try:
            worker_module._append_raster_layer(
                project=mock_project,
                raster_uri="/vsigs/bad-bucket/nonexistent.tif",
                layer_id="bad-layer",
                style_qml_path=None,
            )
            assert False, "Expected WorkerError was not raised"
        except WorkerError as exc:
            assert "QgsRasterLayer failed to initialize" in str(exc)
            assert "bad-bucket" in str(exc) or "nonexistent.tif" in str(exc)

    # addMapLayer must NOT have been called since the layer is invalid.
    mock_project.addMapLayer.assert_not_called()


# ---------------------------------------------------------------------------
# Test 7 — polygon path regression: worker_round_trip does NOT call
#           _append_raster_layer
# ---------------------------------------------------------------------------


def test_polygon_path_does_not_call_append_raster_layer() -> None:
    """``worker_round_trip`` still calls the polygon path; raster function untouched.

    Regression-guard: the existing M2 polygon round-trip must be completely
    unaffected by the raster path additions in job-0062. Adding
    ``publish_raster_round_trip`` must not change ``worker_round_trip``'s
    behavior.
    """
    with _patch_qgis():
        import importlib
        import services.workers.pyqgis.worker as worker_module
        importlib.reload(worker_module)

        mock_project = MagicMock()
        mock_layer = MagicMock()
        mock_layer.name.return_value = "demo-polygon"

        with (
            patch.object(worker_module, "_parse_qgs_uri", return_value=("local", None, None, "/tmp/test.qgs")),
            patch.object(worker_module, "_qgis_app") as mock_app_cm,
            patch.object(worker_module, "_append_memory_polygon_layer", return_value=mock_layer) as mock_polygon,
            patch.object(worker_module, "_append_raster_layer") as mock_raster,
            patch.object(worker_module, "_resolve_style_preset_path", return_value=None),
            patch.object(worker_module, "_layer_names", side_effect=[["basemap"], ["basemap", "demo-polygon"]]),
        ):
            # Mock the context manager for _qgis_app.
            mock_app_cm.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_app_cm.return_value.__exit__ = MagicMock(return_value=False)

            # QgsProject is mocked so read/write calls go to mocks.
            from services.workers.pyqgis.types import LayerSpec
            spec = LayerSpec(name="demo-polygon")

            # Patch QgsProject.instance to return our mock project.
            with patch.object(worker_module, "QgsProject") as mock_qgsproject_cls:
                mock_qgsproject_cls.instance.return_value = mock_project
                mock_project.read.return_value = True
                mock_project.write.return_value = True

                result = worker_module.worker_round_trip(
                    "/tmp/test.qgs",
                    spec,
                    publish=False,
                )

    # Polygon path was called.
    mock_polygon.assert_called_once()
    # Raster path was NOT called.
    mock_raster.assert_not_called()
    assert result.status == "ok"
