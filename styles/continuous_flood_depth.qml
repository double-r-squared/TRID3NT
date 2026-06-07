<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<!--
  GRACE-2 continuous flood depth style preset (FR-QS-5 / job-0062).

  Applied by the PyQGIS worker (``_append_raster_layer``) to flood-depth
  COG layers produced by ``postprocess_flood`` (job-0058 / job-0063).
  Baked into the QGIS Server / worker container image by infra.

  Renderer: singleBandPseudoColor with a Blues color ramp (matplotlib
  Blues colormap reference points) interpolated over 0–3.5 m depth.
  The 3.5 m ceiling matches the M5 demo hmax baseline (3.52 m,
  job-0058 Fort Myers smoke run).

  Nodata: transparent (alpha=0 nodata entry so dry cells are invisible).

  Units: meters above local datum (matches SFINCS zs / zb output convention
  and the COG metadata tag ``units=meters`` from postprocess_flood).

  CRS: EPSG:32617 (UTM zone 17N, corrected by job-0063). Layer source
  URIs carry /vsigs/<runs-bucket>/<run_id>/flood_depth_peak.tif.
-->
<qgis version="3.40.3-Bratislava" styleCategories="Symbology|Rendering|CustomProperties">
  <pipe>
    <provider>
      <resampling enabled="false" maxOversampling="2" zoomedInResamplingMethod="bilinear" zoomedOutResamplingMethod="bilinear"/>
    </provider>
    <rasterrenderer type="singlebandpseudocolor" band="1" opacity="0.82" classificationMin="0" classificationMax="3.5" nodataColor="">
      <rasterTransparency>
        <singleValuePixelList>
          <pixelListEntry min="-9999" max="-9998" percentTransparent="100" label="nodata"/>
        </singleValuePixelList>
      </rasterTransparency>
      <minMaxOrigin>
        <limits>MinMax</limits>
        <extent>WholeRaster</extent>
        <statAccuracy>Estimated</statAccuracy>
        <cumulativeCutLower>0.02</cumulativeCutLower>
        <cumulativeCutUpper>0.98</cumulativeCutUpper>
        <stdDevFactor>2</stdDevFactor>
      </minMaxOrigin>
      <rastershader>
        <colorrampshader clip="0" classificationMode="1" minimumValue="0" maximumValue="3.5" colorRampType="INTERPOLATED" labelPrecision="2">
          <colorramp type="gradient" name="flood_depth_blues">
            <Option type="Map">
              <Option name="color1" value="247,251,255,255" type="QString"/>
              <Option name="color2" value="8,48,107,255" type="QString"/>
              <Option name="direction" value="ccw" type="QString"/>
              <Option name="discrete" value="0" type="QString"/>
              <Option name="rampType" value="gradient" type="QString"/>
              <Option name="stops" value="0.25;198,219,239,255;rgb;ccw:0.5;107,174,214,255;rgb;ccw:0.75;33,113,181,255;rgb;ccw" type="QString"/>
            </Option>
          </colorramp>
          <!--
            Color stops mirror matplotlib Blues colormap sampled at 7 levels.
            Value range: 0.0 m (dry, near-white) to 3.5 m (deep, dark navy).
            Dry areas (0 m) are nearly transparent via layer opacity=0.82 +
            a nodata transparent entry above; wet-near-zero still shows light
            blue so the flood extent is visible even at 0.01 m.
          -->
          <item value="0" color="#f7fbff" alpha="0" label="0.0 m (dry)"/>
          <item value="0.05" color="#deebf7" alpha="200" label="0.05 m"/>
          <item value="0.5" color="#c6dbef" alpha="220" label="0.5 m"/>
          <item value="1.0" color="#9ecae1" alpha="230" label="1.0 m"/>
          <item value="1.5" color="#6baed6" alpha="240" label="1.5 m"/>
          <item value="2.0" color="#4292c6" alpha="245" label="2.0 m"/>
          <item value="2.5" color="#2171b5" alpha="250" label="2.5 m"/>
          <item value="3.0" color="#08519c" alpha="255" label="3.0 m"/>
          <item value="3.5" color="#08306b" alpha="255" label="3.5 m"/>
        </colorrampshader>
      </rastershader>
    </rasterrenderer>
    <brightnesscontrast brightness="0" contrast="0" gamma="1"/>
    <huesaturation invertColors="0" colorizeStrength="100" saturation="0" colorizeOn="0" grayscaleMode="0" colorizeBlue="128" colorizeGreen="128" colorizeRed="255"/>
    <rasterresampler maxOversampling="2"/>
    <resamplingStage>resamplingFilter</resamplingStage>
  </pipe>
  <blendMode>0</blendMode>
</qgis>
