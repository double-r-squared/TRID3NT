# Design: AOI-First Manual Case Creation (#170)

Status: design (read-only trace 2026-06-22). Implement AFTER the session-durability fix
lands (same-file sequencing on server.py + App.tsx; see collision note).

## Goal
When a user MANUALLY creates a Case, let them set the AOI bbox BEFORE the first prompt
(draw-on-map primary, numeric coords fallback). Persist it on CaseSummary.bbox at create
time and surface it to the agent via the EXISTING aoi-pin / _turn_case_bbox machinery so
the agent reuses the extent and does NOT re-geocode from the prompt.

## Key finding
Every primitive exists. The draw surface (terra-draw rectangle + drag-bbox), the agent AOI
anchor (CaseSummary.bbox -> state.case_bbox -> _turn_case_bbox -> the "REUSE this exact
extent, do NOT re-geocode" note in _format_aoi_bbox_line), and the open case-command(create)
args dict all already exist. No contract schema break.

## Agent change (one file, ~6 lines) - J-AGENT-1
services/agent/src/grace2_agent/server.py, the case-command(create) handler (~:3658-3671, :3698):
- coerce/validate raw_bbox = (cmd.args or {}).get("bbox") via existing _coerce_bbox4 (:524) /
  _is_finite_bbox4 (:508).
- pass bbox=coerced into CaseSummary(...) at ~:3665-3671 (persists via upsert_case automatically;
  flows into the cold snapshot + thin manifest unchanged).
- seed state.case_bbox = list(coerced) after setting active (~:3698), mirroring
  _pin_case_aoi_from_solve (:4266), so the FIRST turn's _turn_case_bbox returns it.
Downstream consumers (build_layers_present_note/_format_aoi_bbox_line at adapter.py:1192-1216,
_maybe_default_fetch_bbox_to_pinned_aoi :4317+, scenario-reuse) need NO change.
Test: create-path test asserts case.bbox persisted + state.case_bbox seeded.

## Web jobs (disjoint from agent)
- J-WEB-1: extract the bbox drag-rectangle gesture (orderBbox + drawPickBbox + down/move/up) from
  components/SpatialDrawSurface.tsx (~:173-208, :596+) into a reusable lib/bbox_draw.ts /
  useBboxDraw(map) hook; SpatialDrawSurface calls the extracted util (behavior-preserving).
- J-WEB-2: new components/AoiPickerCard.tsx = draw (primary, via DrawController/J-WEB-1) + coords
  fallback (4 numeric inputs minLon/minLat/maxLon/maxLat, finite+range validate mirroring the
  server _is_finite_bbox4, "Preview on map" via drawPickBbox); returns a [minLon,minLat,maxLon,maxLat].
  REQUEST-FREE: does NOT route through spatialInputBus / SpatialDrawSurface / spatial-input-response
  (the box may be asleep, no active turn). Works because case-command rides sendOrQueue.
- J-WEB-3: hooks/useCases.ts createCase(title?, bbox?) (~:287-295) puts bbox into args when present;
  update UseCasesReturn.createCase signature (~:155).
- J-WEB-4: App.tsx replace the immediate onCreateGated (~:594-597) with open-overlay; on confirm call
  createCase(title?, bbox). Confine App.tsx edits to the create-action seam (NOT the loading-stub
  blocks owned by durability Job E).
- J-WEB-5: mount AoiPickerCard from Map.tsx near the SpatialDrawSurface mount (~:3679-3687) gated on a
  NEW local "aoi-capture active" signal driven by App state, NOT spatialRequest.
Order: J-WEB-1 -> 2 -> 3 -> 4 -> 5; J-AGENT-1 in parallel (different file).

## Contract
NO breaking change. case-command(create).args is an open dict (case.py:514; contracts.ts:881).
Optionally document args.bbox = [minLon,minLat,maxLon,maxLat] in the create-handler + envelope docstrings.

## Persistence
CaseSummary.bbox is written by Persistence.upsert_case (persistence.py:442-501, model_dump) with zero
extra code; the create handler already calls _persist_case_view_snapshot + _persist_case_manifest, so
the pre-set AOI lands in DynamoDB grace2_cases.bbox, the fat snapshot, and the thin manifest. On reopen
_cache_case_bbox_from_session_state (:4194) re-seeds state.case_bbox, so the AOI survives close/reopen
+ box sleep/wake.

## Collision with the in-flight session-durability fix (SEQUENCE #170 AFTER it)
- server.py: #170 edits the create handler (~:3658-3671, :3698) = a DIFFERENT region than durability
  Jobs B/C (:8765 register, :2730/:2778 resume, :867 active-case pointer, ~:4080 user-message rebind,
  :2846 replay). Logically disjoint but SAME FILE -> apply J-AGENT-1 after B/C land.
- App.tsx: #170 J-WEB-4 edits the create-action seam (~:594-597); durability Job E edits the
  loading-stub blocks (~:1515-1534, :1839-1862) + derivation near :1354. Different blocks -> apply
  J-WEB-4 after Job E; keep #170 out of the loading-stub blocks.
- ws.ts / Chat.tsx / ChatInput.tsx / auth.ts / useAuth / AuthGuard / EntryRouter: #170 touches NONE ->
  no collision with durability Jobs A/D.
Net: only server.py (vs B/C) and App.tsx (vs E) are same-file contenders; everything else
(AoiPickerCard, bbox_draw, useCases.ts, Map.tsx mount, SpatialDrawSurface extraction) is disjoint and
can proceed immediately once durability is committed.
