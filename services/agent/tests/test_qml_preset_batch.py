"""tools-backlog #3 -- the per-tool QML/colormap presets that replace the generic
continuous_dem placeholder for the data rasters NOT shadowed by the F51 terrain
passthrough (impervious surface + population). slope/aspect/hillshade intentionally
stay on the grayscale terrain passthrough (a deliberate, tested F51 decision).

ASCII only.
"""

from __future__ import annotations

from grace2_agent.tools.publish_layer import (
    _TITILER_STYLE_REGISTRY,
    _is_terrain_token_preset,
    _registry_style_params,
)


def test_new_presets_registered():
    assert _TITILER_STYLE_REGISTRY["impervious_surface_pct"] == ("0,100", "reds")
    assert _TITILER_STYLE_REGISTRY["population_density"] == ("0,250", "magma")


def test_new_presets_resolve_to_expected_colormap():
    assert _registry_style_params("impervious_surface_pct") == "&rescale=0,100&colormap_name=reds"
    assert _registry_style_params("population_density") == "&rescale=0,250&colormap_name=magma"


def test_terrain_tools_still_passthrough_grayscale():
    # compute_slope / compute_aspect / compute_hillshade source_class URLs always
    # match the F51 terrain passthrough -> grayscale (unchanged by #3).
    for source in ("slope", "aspect", "hillshade"):
        uri = f"s3://b/cache/static-30d/{source}/x.tif"
        assert _is_terrain_token_preset("continuous_dem", uri) is True


def test_no_dead_terrain_colormap_keys():
    # the would-be slope/aspect/hillshade colormap keys must NOT linger as dead
    # entries (they are shadowed by the passthrough; flagged to NATE instead).
    for k in ("terrain_slope_deg", "terrain_aspect_deg", "terrain_hillshade"):
        assert k not in _TITILER_STYLE_REGISTRY
