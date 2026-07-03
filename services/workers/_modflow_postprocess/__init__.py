"""Shared, agent-import-free MODFLOW UCN -> plume COG postprocess (Batch worker).

The worker-side LIFT of the pure ``flopy`` / ``numpy`` / ``rasterio`` / ``pyproj``
plume rasterize that used to run on the always-on agent box after a MODFLOW solve
(``grace2_agent.workflows.postprocess_modflow``). Moving it INTO the
``grace2-modflow`` Batch worker -- which already has the raw ``gwt_model.ucn``
local, the geo + flopy stack in-image, and a tear-down Spot box -- is the
scale-to-zero island pattern (mirrors the SFINCS ``_raster_postprocess`` split):
the agent collapses to "read a thin manifest, build the TiTiler URL, register".

Hard constraints (mirroring ``_raster_postprocess``):

  * AGENT-IMPORT-FREE -- never imports ``grace2_agent.*``. The COG write is
    vendored from ``cog_io.write_cog_4326_from_grid`` (rasterio-only); the upload +
    manifest reuse the engine-agnostic ``services.workers._raster_postprocess``
    helpers.
  * Pure + unit-testable -- runs against a synthetic UCN + deck with no Batch / S3.

Modules:
  * :mod:`postprocess`  the orchestrator the worker entrypoint calls: read the
                        LOCAL ``gwt_model.ucn`` (+ deck georegistration), reproject
                        to an EPSG:4326 plume COG in the deck dir, compute plume
                        metrics, build the ``publish_manifest.json`` dict, and apply
                        the empty-plume honesty gate.
"""

from __future__ import annotations

from .postprocess import (  # noqa: F401
    GWT_UCN_FILENAME,
    PLUME_DETECTION_FLOOR_MGL,
    PLUME_STYLE_PRESET,
    ModflowPostprocessResult,
    run_plume_postprocess,
)
