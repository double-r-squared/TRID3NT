# Design Spike: Cross-Section / Profile Tool

Design spike for a "draw-a-line, see-a-profile" capability: the user (or the
agent) supplies a line, the server samples ANY height/depth raster layer along
that line at N evenly-spaced stations, and the resulting elevation-or-depth
profile is surfaced as a chart. NATE flagged this for discussion BEFORE any
build; this doc surfaces the two OPEN DESIGN CALLS for his sign-off.

ASCII only. No em/en dashes, no unicode arrows. Status: design + recommendation
only, no code in this doc. Read-only research; nothing committed.

---

## 0. The feature in one paragraph

A cross-section (a.k.a. profile or transect) tool answers a question a map
cannot: "how does this surface vary ALONG a line?" The user draws a polyline on
the map (or the agent derives one, e.g. perpendicular to a river or down a valley
axis), names a height/depth raster already in the Case (a DEM, a SFINCS flood
depth, a HRRR/MRMS field, a MODFLOW head surface, a bathymetry COG), and the
server samples that raster at N stations spaced evenly along the line. The result
is a (distance, value) series rendered as a Vega-Lite line chart: x = distance
along the line in metres, y = elevation or depth in the raster's native units.
This is the canonical hydraulics/terrain "long profile" or "section view", and it
is a near-pure GLUE feature for GRACE-2 because all three load-bearing pieces
already exist: the terra-draw line-draw surface (#129-132), the chart-in-chat
emission path (chart_tools.py), and the COG raster reader (rasterio over s3://
staging). The only genuinely new code is a single deterministic line-sampler tool.

---

## 1. Data flow

End-to-end, the happy path is:

```
  USER (or AGENT) supplies a line
        |
        |  (a) user-drawn: agent calls request_spatial_input(mode="vector_draw"),
        |      user draws a LineString on the terra-draw surface, it round-trips
        |      back as a role-tagged GeoJSON FeatureCollection (a LineString feature).
        |  (b) agent-derived: agent constructs the LineString itself (two endpoints
        |      it reasons about, or perpendicular-to-a-river) and passes it inline.
        v
  NEW TOOL: compute_cross_section(layer_uri, line, n_stations=200)
        |
        |  1. Resolve `line` to a shapely LineString in EPSG:4326.
        |  2. Open `layer_uri` with rasterio (s3:// staged via read_object_bytes_s3,
        |     same MemoryFile pattern as clip_raster_to_polygon._get_source_crs).
        |  3. Reproject the line into the raster CRS (transform_geom), so distances
        |     and sample points are in the raster's own grid.
        |  4. Densify: interpolate N stations at equal arc-length along the line
        |     (line.interpolate(d) for d in linspace(0, line.length, n_stations)).
        |  5. Sample the raster at those station coordinates in ONE vectorized call
        |     (rasterio src.sample([(x,y), ...]) -> one value per station, nodata
        |     masked to null). Optional bilinear interpolation for a smooth profile.
        |  6. Build a profile series: [{distance_m, value, lon, lat}, ...], where
        |     distance_m is cumulative geodesic distance from the start vertex.
        v
  RETURNS a profile series -> wrapped as a Vega-Lite v5 LINE chart via
  build_chart_payload(...) -> a ChartEmissionPayload dict (envelope_type=
  "chart-emission").
        |
        v
  SERVER turn loop (server.py ~line 2339) already detects
  is_chart_emission_result(result) -> emits the chart-emission WS envelope +
  persists a SessionChartRecord (replays on Case rehydration) + feeds a COMPACT
  summary back to the model for narration ("the profile drops 14 m over 480 m,
  steepest near station 120").
        |
        v
  WEB client renders the line chart inline (the existing stacked chart preview /
  gallery), OR (DESIGN CALL A) a dedicated bottom overlay.
```

The crucial structural fact: from step 6 onward the path is ALREADY BUILT. A
profile is just another Vega-Lite line chart, identical in shape to
`generate_time_series` (x ordinal/quantitative, y quantitative, mark line). It
rides the same envelope, the same persistence, the same narration feedback. The
only net-new server code is steps 1-6 (the sampler), and they are a strict subset
of patterns already present in `clip_raster_to_polygon` (CRS detect, s3 staging,
transform_geom) plus `compute_zonal_statistics` (rasterio read) plus one new idea
(arc-length station interpolation + `src.sample`).

---

## 2. Thin-glue assessment

### HAVE (reuse verbatim, no new code)

1. **terra-draw line surface.** `web/src/lib/draw_controller.ts` already wires
   `TerraDrawLineStringMode` (editable, vertex midpoints, drag) into the same
   surface used for SWMM barriers. A drawn LineString round-trips back to the
   agent today via `getFeatureCollection()` -> `spatial_input_bus` ->
   `spatial-input-response`. NO new draw mode is required; a profile line is just
   a LineString with role left untagged (or a new role "profile" to disambiguate
   from a barrier - a one-line contract addition, see build plan).

2. **The agent-side draw request.** `tools/spatial_input_tool.py`
   `request_spatial_input(mode="vector_draw")` is the existing seam that PAUSES
   the turn, opens the terra-draw surface, and returns the drawn geometry. It
   already supports `point` / `bbox` / `vector_draw`. The user-drawn-line path is
   this tool, unchanged, plus a small parse step to pull the LineString out of the
   returned FeatureCollection. (Today it splits by role for AOI/barrier; we add a
   "extract the profile LineString" branch.)

3. **The chart-in-chat path.** `tools/chart_tools.py` `build_chart_payload(...)`
   wraps any Vega-Lite v5 spec into a validated `ChartEmissionPayload` dict;
   `server.py` detects `is_chart_emission_result(result)` and does ALL the wire +
   persistence + narration-feedback work (`_maybe_emit_chart`, `SessionChartRecord`,
   `summarize_tool_result` strips the inline spec). A profile chart reuses this
   end to end - it is the `generate_time_series` line-chart shape with a distance
   x-axis instead of a time x-axis.

4. **COG raster read.** `clip_raster_to_polygon._get_source_crs` /
   `_download_raster_bytes` and `compute_zonal_statistics` already establish the
   canonical "open an s3:// COG with rasterio via MemoryFile + boto3 staging"
   pattern (the /vsis3/ credential-chain workaround is documented in the code:
   stage bytes with `read_object_bytes_s3`, open in-memory). CRS detection +
   `rasterio.warp.transform_geom` for reprojecting a geometry into the raster CRS
   is also already in `clip_raster_to_polygon`.

### NET-NEW (the actual build)

1. **The line-sampler tool** `compute_cross_section(layer_uri, line, n_stations)`.
   ~150-200 lines, mirroring `clip_raster_to_polygon`'s structure: typed
   `CrossSectionError` (LAYER_OPEN_FAILED, DOWNLOAD_FAILED, LINE_INVALID,
   LINE_OUTSIDE_RASTER, NO_DATA), s3 staging, CRS detect + line reproject, then the
   genuinely new ~30 lines: arc-length station interpolation
   (`line.interpolate`), `src.sample(...)` vectorized read, cumulative geodesic
   distance, nodata -> null. Deterministic (zero LLM calls, Invariant 2),
   `read_only_hint=True`. Caching: this is cheap and line-specific, so
   `cacheable=False` / `live-no-cache` like the other in-process emit tools, OR a
   cache key over (layer_uri, rounded line WKT, n_stations) if we later want it -
   not worth it for v0.1.

2. **The profile chart spec builder.** ~30 lines inside the tool: the same
   Vega-Lite line-chart spec shape as `generate_time_series`, x = `distance_m`
   (quantitative), y = `value` (quantitative, titled with the layer's units),
   tooltip on. For the multi-layer case (DESIGN CALL B), add a `color` encoding on
   a `layer` field and concatenate the per-layer series. This is the ONLY part
   that differs between the two design calls.

3. **The surfacing surface.** If DESIGN CALL A = chat-card: ZERO new web code (it
   is a chart-emission, the renderer exists). If DESIGN CALL A = bottom overlay: a
   new web component + a new (or reused) envelope and a docked-panel layout slot.

4. **An agent-derivable line option.** Two parts: (i) let `compute_cross_section`
   accept an inline `line` arg (a list of [lon,lat] vertices or a GeoJSON
   LineString) so the agent can pass a self-constructed line WITHOUT a user draw;
   (ii) prompt/guidance so the agent knows when to draw-its-own (e.g. "profile
   across this river" -> derive a perpendicular) vs ask-the-user
   (`request_spatial_input`). The inline-line path is a few lines of arg parsing;
   the perpendicular-derivation heuristic is optional polish for v1.

NET: one new server tool (mostly assembled from existing patterns), one chart
spec, and a surfacing decision (the only part that can balloon - see DESIGN CALL
A). The line draw, the draw round-trip, the chart envelope, the persistence, and
the COG read are all already in the tree.

---

## 3. The two OPEN DESIGN CALLS (need NATE sign-off)

### DESIGN CALL A: chat-card vs bottom overlay for v1

**The question.** Where does the profile chart appear? Option (1) a chat-card -
the existing stacked chart-emission preview inline in the conversation, click to
expand into the gallery. Option (2) a dedicated bottom overlay - a docked,
persistent panel pinned to the bottom edge of the map, profile drawn left-to-right
under the map, that updates as the user re-draws the line.

| Axis | Chat-card (reuse chart path) | Bottom overlay (new surface) |
|------|------------------------------|------------------------------|
| Net-new web code | ZERO (chart-emission exists) | A new docked panel component + envelope/layout slot |
| Time to v1 | Days (server tool only) | Weeks (new web surface, layout, resize, dismiss) |
| Spatial intuition | Lower (chart divorced from the map line) | Higher (profile sits under the map, reads like a section view) |
| Persistence/replay | Free (SessionChartRecord) | Needs new persistence wiring |
| Re-draw responsiveness | Re-run = new card | Natural live-update home |
| Risk | Low (proven path) | Higher (a fresh layout surface; bottom edge competes with the time-scrubber per project_timeseries_animation_and_overlay_layout) |

**My recommendation: CHAT-CARD for v1, reusing the existing chart-emission path.**
It ships the actual capability (the sampler + the science) in days instead of
weeks, with zero web risk, and it is fully consistent with how every other chart
in the app already behaves (histogram, time-series, damage distribution all land
as chat-cards). The bottom overlay is the better END STATE for spatial intuition -
a section view under the map is genuinely the "right" home for a profile - but it
is a SECOND, separable job that can reuse the SAME tool output (the profile series
is surface-agnostic). Build the engine first as a chat-card; promote to a bottom
overlay as a follow-up once the sampler is proven live. NOTE the layout collision:
the bottom edge is already spoken for by the planned time-scrubber
(project_timeseries_animation_and_overlay_layout), so the overlay needs a layout
decision NATE owns anyway - another reason to defer it.

### DESIGN CALL B: multi-layer overlay (one profile per layer on a shared axis)

**The question.** Can a single profile chart show MULTIPLE layers' profiles on the
SAME line, on a shared distance axis (e.g. ground elevation from the DEM AND flood
depth from SFINCS, or pre- and post-event bathymetry), each a colored line in one
chart? Yes/no for v1.

**My recommendation: YES - it is the high-value differentiator.**
A single-layer profile is useful but commodity (any GIS does a terrain profile).
The compounding insight in GRACE-2's hazard work is the RELATIONSHIP between
surfaces along a line: ground vs water surface (freeboard / inundation depth),
DEM vs bathymetry (the bank-to-channel transition), head surface vs land surface
(MODFLOW seepage), pre vs post event. Overlaying N layers on one shared
distance-x axis is exactly the "section view" hydraulic engineers draw by hand,
and it is CHEAP given the architecture: the sampler runs once per layer over the
SAME stations (identical x-values), and Vega-Lite renders multiple lines from one
`layer` field via a `color` encoding - no new envelope, no new persistence, just a
list of layer_uris instead of one and a concatenated series. The cost is small and
the payoff is the thing that makes this OUR profile tool rather than a generic one.
Caveat to bound v1 scope: cap at ~3-4 layers, and require the layers to be
co-located (the sampler returns null for stations a layer does not cover, surfaced
honestly rather than dropped - same honesty floor as the rest of the app). Units:
if layers share units (all metres of elevation/depth) put them on one y-axis; if
they differ, either a dual-axis (Vega-Lite layer with independent y scales) or a
normalized overlay - default to single-y when units match and fall back to
dual-axis only when they do not (a v1 nicety, not a blocker).

---

## 4. Ordered build plan

A profile is a chat-card chart powered by one new sampler tool; the overlay and
the agent-derived-line polish are separable follow-ons.

**Phase 1 - the sampler tool (the core, ships the capability)**
1. Add `tools/compute_cross_section.py`: `compute_cross_section(layer_uri, line,
   n_stations=200)`. Reuse `clip_raster_to_polygon`'s s3-staging + CRS-detect +
   `transform_geom` helpers; add arc-length station interpolation
   (`line.interpolate`) + vectorized `src.sample` + cumulative geodesic distance +
   typed `CrossSectionError`. Accept `line` as EITHER a GeoJSON LineString / a
   list of [lon,lat] vertices (the agent-derivable inline path) OR resolve it from
   a prior `request_spatial_input` result.
2. Build the Vega-Lite line spec inline (x=distance_m, y=value, units in the
   y-title) and wrap with `build_chart_payload(...)`. Single-layer first.
3. Register the tool (read_only, deterministic, `live-no-cache`); place it in the
   analysis/chart category alongside `generate_time_series`; write the LLM-facing
   docstring (when to use / when not - "profile ALONG a line" vs "distribution"
   `generate_histogram` vs "over time" `generate_time_series`).
4. Unit tests: synthetic ramp DEM -> known linear profile; nodata -> null
   station; line outside raster -> typed LINE_OUTSIDE_RASTER; CRS mismatch path.

**Phase 2 - the line input wiring**
5. User-drawn path: extend the `request_spatial_input` result-parse to extract a
   profile LineString (add an optional role "profile" to the spatial-draw
   contract, OR accept an untagged LineString in profile context). Agent guidance:
   call `request_spatial_input(mode="vector_draw")` then feed the LineString to
   `compute_cross_section`.
6. Agent-derived path: confirm the inline `line` arg works end to end; add prompt
   guidance for when the agent should derive its own line (two named endpoints,
   or perpendicular-to-a-river - the perpendicular heuristic is optional v1
   polish).

**Phase 3 - multi-layer overlay (DESIGN CALL B = YES)**
7. Accept `layer_uris: list[str]` (or keep `layer_uri` + add `extra_layer_uris`);
   sample each layer over the SAME stations; concatenate to a series with a
   `layer` field; add the Vega-Lite `color` encoding. Cap ~3-4 layers. Single-y
   when units match, dual-axis fallback when they do not.
8. Tests: two synthetic rasters (ground + water) on one line -> two-line chart;
   mismatched-coverage layer -> null stations surfaced, not dropped.

**Phase 4 (FOLLOW-ON, separable) - the bottom overlay (DESIGN CALL A end state)**
9. Only if NATE elects the overlay END state: a docked bottom-panel web component
   that renders the SAME profile series (the tool output is surface-agnostic),
   with the time-scrubber layout collision resolved
   (project_timeseries_animation_and_overlay_layout). Live re-draw -> live update.
   This reuses Phase 1-3 output entirely; it is a rendering surface, not new
   science.

Phases 1-3 deliver the full capability as chat-cards. Phase 4 is the optional
spatial-intuition upgrade NATE can green-light later without reworking the engine.

---

## 5. Cross-links

- `web/src/lib/draw_controller.ts` - the terra-draw LineString surface (HAVE).
- `web/src/lib/spatial_input_bus.ts` - the draw round-trip to the agent (HAVE).
- `services/agent/src/grace2_agent/tools/spatial_input_tool.py` /
  `spatial_input.py` / `server.py` `_handle_request_spatial_input` - the
  agent-side draw request + turn pause/resume (HAVE; the user-drawn-line seam).
- `services/agent/src/grace2_agent/tools/chart_tools.py` `build_chart_payload` /
  `is_chart_emission_result` - the chart-in-chat wrap (HAVE; reuse verbatim).
- `services/agent/src/grace2_agent/server.py` (~line 2339, `_maybe_emit_chart`,
  `SessionChartRecord`) - chart-emission wire + persistence + narration feedback
  (HAVE; the profile chart rides this).
- `services/agent/src/grace2_agent/tools/clip_raster_to_polygon.py` - the
  s3-staging COG read + CRS detect + `transform_geom` pattern the sampler copies.
- `services/agent/src/grace2_agent/tools/compute_zonal_statistics.py` - rasterio
  read precedent; the profile is the 1-D line analogue of zonal aggregation.
- Memory `project_conversational_data_analysis_layer` - the chart-emission /
  gallery architecture this feature extends (a profile is a new chart kind).
- Memory `project_timeseries_animation_and_overlay_layout` - the bottom-edge
  layout the overlay (DESIGN CALL A end state) must share with the time-scrubber.
- Memory `feedback_data_source_fallback_norm` / honesty floor - null stations for
  un-covered line segments are surfaced honestly, never silently dropped.
- Memory `project_river_to_shapefile_tool` - the auto/medium/strict interactivity
  dial; the user-drawn vs agent-derived line choice is the same dial applied here.
```

---

## 6. Summary of recommendations

1. Ship the sampler + chart-card FIRST (Phases 1-3); it is days of work and pure
   glue over existing surfaces.
2. DESIGN CALL A: chat-card for v1; bottom overlay as a separable follow-on
   (Phase 4) once the sampler is proven and the time-scrubber layout is settled.
3. DESIGN CALL B: YES to multi-layer overlay - it is the differentiator, it is
   cheap given the architecture, and it is what makes a hazard profile worth more
   than a generic terrain profile.
