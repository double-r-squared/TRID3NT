<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<!--
  GRACE-2 M2 basemap preset STUB (FR-QS-5 first preset).

  Matches the layer named "basemap-osm-conus" in
  services/workers/pyqgis/sample_project/grace2-sample.qgs.

  Status: STUB. The seven full FR-QS-5 presets (flood depth, flood velocity,
  flood arrival time, continuous DEM, categorical landcover, hurricane track,
  affected buildings) land in later milestones. This file exists only to
  prove the apply_style_preset codepath (job-0020) and the infra
  COPY styles/ -> /opt/styles/ bake mechanism (job-0018 Dockerfile).

  Renderer: raster opacity + brightness tweak on the OSM XYZ basemap, so the
  preset has a visible effect (slightly dimmed background so future overlays
  read against it) without coloring tile pixels. No colour ramp — single-band
  pseudocolour does not apply to a multi-band RGB tile source.
-->
<qgis version="3.40.3-Bratislava" styleCategories="Symbology|Rendering|CustomProperties">
  <pipe>
    <provider>
      <resampling enabled="false" maxOversampling="2" zoomedInResamplingMethod="nearestNeighbour" zoomedOutResamplingMethod="nearestNeighbour"/>
    </provider>
    <rasterrenderer opacity="0.85" type="multibandcolor" redBand="1" greenBand="2" blueBand="3" alphaBand="-1" nodataColor="">
      <rasterTransparency/>
      <minMaxOrigin>
        <limits>None</limits>
        <extent>WholeRaster</extent>
        <statAccuracy>Estimated</statAccuracy>
        <cumulativeCutLower>0.02</cumulativeCutLower>
        <cumulativeCutUpper>0.98</cumulativeCutUpper>
        <stdDevFactor>2</stdDevFactor>
      </minMaxOrigin>
    </rasterrenderer>
    <brightnesscontrast brightness="-15" contrast="0" gamma="1"/>
    <huesaturation invertColors="0" colorizeStrength="100" saturation="-20" colorizeOn="0" grayscaleMode="0" colorizeBlue="128" colorizeGreen="128" colorizeRed="255"/>
    <rasterresampler maxOversampling="2"/>
    <resamplingStage>resamplingFilter</resamplingStage>
  </pipe>
  <blendMode>0</blendMode>
</qgis>
