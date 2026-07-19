# Persistent per-case bbox (cloud parity) - design 2026-07-19

NATE PRIORITY: every case has a bbox the AGENT always references + the USER can
edit. Scoped read-only against real seams. KEY FINDING: the agent + contract +
persistence halves ALREADY EXIST and ship. The gap is entirely plugin-side
(overlay + draw tool) + one small case-command to persist a user edit.

## Already built (no work)
- Contract: CaseSummary.bbox = [minLon,minLat,maxLon,maxLat] EPSG:4326
  (packages/contracts/.../case.py:110); mirrored on CaseManifest/CaseManifestLayer.
- Persistence round-trips it (case_lifecycle.py:1310; upsert_case bbox=).
- Plugin already PARSES it: CaseInfo.bbox, CaseOpenInfo.bbox (trid3nt_client.py:309/530).
- AGENT INJECTS IT EVERY TURN: state.case_bbox (server.py:2180) ->
  _turn_case_bbox (5907) -> build_layers_present_note(case_bbox=...) appended as
  the last user turn on EVERY live turn (server.py:3090-3096); the line is
  _format_aoi_bbox_line (adapter.py:1318): "Case AOI bbox [...]. REUSE this exact
  extent ... do NOT re-derive or re-geocode." PLUS dispatch-time snapping of
  fetch bbox params to the pin (_maybe_default_fetch_bbox_to_pinned_aoi,
  server.py:6158). => the "model spins on which bbox" problem is ALREADY solved
  IF state.case_bbox is populated. It was empty because the case had no bbox.

## The gap (what to build)
1. Nothing populates CaseSummary.bbox for a plugin case until a bbox-taking tool
   runs and the agent pins it (_pin_case_aoi_from_tool_bbox, server.py:6052). A
   fresh case = empty AOI = model guesses.
2. No visible overlay of the case bbox in the plugin.
3. No user draw/edit tool.
4. No command to persist a user edit of an EXISTING case's bbox (CaseCommand enum
   is closed: create/select/deselect/rename/archive/delete, case.py:494). The web
   only STAGES a drawn box into the next prompt; it persists only when a tool runs.

## Plan (Option B - immediate persist, the honest fit for "user edits + persists")
AGENT+CONTRACT (small, gated behind empty-retry wf since both touch server.py):
- Add "set-bbox" to CaseCommand literal (case.py:494). args dict already free-form
  (CaseCommandEnvelopePayload.args, case.py:525) - no other contract change.
- set-bbox branch in _handle_case_command (server.py:5310), clone rename
  (5508-5561): update={"bbox": _coerce_bbox4(args.bbox), updated_at}; set
  state.case_bbox = list(bbox); re-snapshot + _emit_case_list. _coerce_bbox4
  exists (server.py:778).

PLUGIN (trid3nt-local/qgis-plugin/trid3nt/ - the bulk, no server.py collision):
- Transport: thread args through trid3nt_client.case_command (1218) + ws_bridge
  .case_command (257).
- State: self._case_bbox + self._aoi_rubber (dock keeps none today, 1622-1655);
  clear on case-switch/disconnect (mirror _clear_messages 2766 / disconnect 2213).
- Overlay: QgsRubberBand(PolygonGeometry), DASHED pen, transparent fill (match web
  drawAnalysisExtent Map.tsx:1682-1741); render in _on_case_open_event (2733)
  beside _zoom_after_case_open; EPSG:4326 -> canvas CRS via existing transforms.
- Draw tool: checkable header button (1719) + QgsMapToolExtent (extentChanged);
  install/restore cloned VERBATIM from the release-point _toggle_release_pick
  (972-991). On extent -> aoi.extent_to_bbox4326 -> update state + rubber band +
  case_command("set-bbox", case_id, {"bbox":[...]}) + restore prior tool.
- Default-on-create: header new_case (2330) currently sends create with NO bbox
  (client can't carry one) - pass canvas extent via create_case(title, bbox) or a
  set-bbox right after create.
- Keep attach_aoi_to_text send-path (2925) as per-turn belt-and-suspenders.

## v1 vs deferred
v1: overlay + draw/edit tool + set-bbox persist + default-on-create.
Deferred: sim-running purple recolor (cosmetic), numeric coord entry, non-rect
(ring) AOI (agent only carries a 4-num bbox - keep rectangle).

## Risks
- state.case_bbox must be set for the pin/snap paths to fire; Option A (text-attach
  only) leaves CaseSummary.bbox None until a tool runs -> reverts to empty AOI on
  reconnect (the exact spin). Option B avoids it. => do Option B.
- CaseCommand + envelope are extra="forbid": set-bbox MUST be added to the enum on
  both sides or the agent rejects it.
- CRS: case bbox is 4326; canvas may be 3857/other - reuse extent_to_bbox4326 /
  zoom_to_bbox4326, never hand-roll (non-4326/3857 returns None on purpose).
