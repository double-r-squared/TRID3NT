// GRACE-2 web — SpatialDrawSurface (FR-WC-13 pick-mode + FR-WC-16 urban
// vector-draw). The on-MAP host for a paused `spatial-input-request`.
//
// Mounted INSIDE the Map container (so it overlays the live MapLibre canvas) by
// Map.tsx whenever the spatial-input bus carries an active request. It owns:
//
//   - A small banner (top-center) echoing the agent's title/description — the
//     reused FR-WC-13 pick-mode banner pattern.
//   - mode "point" / "bbox": a thin pick layer — the user clicks (point) or
//     drags a rectangle (bbox) on the map; the result feeds the bus on Submit.
//   - mode "vector_draw": a draw TOOLBAR (rectangle / line / polygon / select +
//     snip + clear) driving a DrawController (terra-draw), plus a per-segment
//     TAGGING popover (wall=red / flap_gate=green; flap direction in|out).
//   - A Submit + Cancel affordance pinned bottom-center.
//
// The component never touches the WebSocket — it relays the completed geometry
// (or a cancel) through the spatial-input bus to Chat.tsx, the reply owner.

import { useEffect, useMemo, useRef, useState } from "react";
import type { Map as MapLibreMap, MapMouseEvent, GeoJSONSource } from "maplibre-gl";
import type {
  BarrierType,
  SpatialInputRequestPayload,
} from "../contracts";
import type { SpatialInputResult } from "../lib/spatial_input_bus";
import {
  DrawController,
  type DrawControllerDeps,
  type DrawFeatureId,
  type DrawMode,
} from "../lib/draw_controller";
import {
  IconBbox,
  IconPolygon,
  IconLine,
  IconSnip,
  IconMapPin,
  IconClose,
  IconCheck,
  IconWarning,
  IconFlowArrow,
} from "./icons";

// --- Pick-mode (point / bbox) drawing layer ids -------------------------- //

const PICK_SOURCE_ID = "grace2-spatial-pick";
const PICK_FILL_LAYER_ID = "grace2-spatial-pick-fill";
const PICK_LINE_LAYER_ID = "grace2-spatial-pick-line";
const PICK_POINT_LAYER_ID = "grace2-spatial-pick-point";

const PICK_COLOR = "#3b82f6";

// --- Default discard threshold for tiny polygons (m²) -------------------- //

/** Lakes/ponds smaller than this (≈ a 16 m × 16 m square) are dropped by the
 * "discard tiny polygons" control by default; exposed as a slider in the UI. */
export const DEFAULT_DISCARD_AREA_M2 = 250;

export interface SpatialDrawSurfaceProps {
  /** The live MapLibre instance (Map.tsx's `map.current`). */
  map: MapLibreMap;
  /** The active spatial-input request. */
  request: SpatialInputRequestPayload;
  /** Relay a completed pick / draw to Chat (the WS reply owner) via the bus. */
  onSubmit: (result: SpatialInputResult) => void;
  /** Relay a cancellation to Chat via the bus. */
  onCancel: (requestId: string) => void;
  /** Injectable terra-draw factory for tests (defaults to the real lib). */
  drawDeps?: DrawControllerDeps;
}

interface TagTarget {
  id: DrawFeatureId;
}

export function SpatialDrawSurface({
  map,
  request,
  onSubmit,
  onCancel,
  drawDeps,
}: SpatialDrawSurfaceProps): JSX.Element {
  const isVectorDraw = request.mode === "vector_draw";

  // --- vector_draw: DrawController lifecycle ----------------------------- //
  const controllerRef = useRef<DrawController | null>(null);
  const [activeMode, setActiveMode] = useState<DrawMode>("rectangle");
  const [counts, setCounts] = useState({
    aoi: 0,
    barrier: 0,
    untaggedBarrier: 0,
    point: 0,
  });
  const [tagTarget, setTagTarget] = useState<TagTarget | null>(null);
  const [flapDirection, setFlapDirection] = useState<"in" | "out">("out");
  const [discardArea, setDiscardArea] = useState<number>(DEFAULT_DISCARD_AREA_M2);
  const [discardNotice, setDiscardNotice] = useState<string | null>(null);

  // --- point / bbox pick state ------------------------------------------ //
  // coordinates carried back: point=[lon,lat]; bbox=[minLon,minLat,maxLon,maxLat]
  const [pickCoords, setPickCoords] = useState<number[] | null>(null);

  // Frame the suggested view so picking is easy (mirrors region-choice fitBounds).
  useEffect(() => {
    const view = request.suggested_view;
    if (!view) return;
    try {
      const [minLon, minLat, maxLon, maxLat] = view.bbox;
      map.fitBounds(
        [
          [minLon, minLat],
          [maxLon, maxLat],
        ],
        { padding: 64, duration: 600, maxZoom: 17 },
      );
    } catch {
      /* degenerate bbox — leave the camera */
    }
    // Re-frame only when the request changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request.request_id]);

  // Mount / unmount the DrawController for vector_draw.
  useEffect(() => {
    if (!isVectorDraw) return;
    const controller = new DrawController(map, drawDeps);
    controllerRef.current = controller;
    controller.start();
    controller.setMode("rectangle");
    setActiveMode("rectangle");
    const refresh = (): void => setCounts(controller.counts());
    const unsubChange = controller.onChanged(refresh);
    const unsubSelect = controller.onSelected((id) => {
      // Only barrier LineStrings get the tag popover; AOIs/points are untyped.
      const snap = controller.getSnapshot().find((f) => f.id === id);
      if (snap && snap.geometry.type === "LineString") {
        setTagTarget({ id });
      } else {
        setTagTarget(null);
      }
    });
    refresh();
    return () => {
      unsubChange();
      unsubSelect();
      controller.stop();
      controllerRef.current = null;
      setTagTarget(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isVectorDraw, request.request_id]);

  // --- point / bbox pick-mode handlers ----------------------------------- //
  useEffect(() => {
    if (isVectorDraw) return;
    ensurePickLayers(map);

    if (request.mode === "point") {
      const onClick = (e: MapMouseEvent): void => {
        const coords = [e.lngLat.lng, e.lngLat.lat];
        setPickCoords(coords);
        drawPickPoint(map, coords);
      };
      map.on("click", onClick);
      const prevCursor = setCursor(map, "crosshair");
      return () => {
        map.off("click", onClick);
        setCursor(map, prevCursor);
        clearPickLayers(map);
      };
    }

    // bbox: drag a rectangle. We track a down → move → up gesture with dragPan
    // disabled during the drag so the rectangle, not the map, moves.
    let anchor: [number, number] | null = null;
    const onDown = (e: MapMouseEvent): void => {
      anchor = [e.lngLat.lng, e.lngLat.lat];
      map.dragPan.disable();
    };
    const onMove = (e: MapMouseEvent): void => {
      if (!anchor) return;
      const cur: [number, number] = [e.lngLat.lng, e.lngLat.lat];
      const bbox = orderBbox(anchor, cur);
      drawPickBbox(map, bbox);
    };
    const onUp = (e: MapMouseEvent): void => {
      if (!anchor) return;
      const cur: [number, number] = [e.lngLat.lng, e.lngLat.lat];
      const bbox = orderBbox(anchor, cur);
      setPickCoords(bbox);
      drawPickBbox(map, bbox);
      anchor = null;
      map.dragPan.enable();
    };
    map.on("mousedown", onDown);
    map.on("mousemove", onMove);
    map.on("mouseup", onUp);
    const prevCursor = setCursor(map, "crosshair");
    return () => {
      map.off("mousedown", onDown);
      map.off("mousemove", onMove);
      map.off("mouseup", onUp);
      map.dragPan.enable();
      setCursor(map, prevCursor);
      clearPickLayers(map);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isVectorDraw, request.mode, request.request_id]);

  // --- Actions ----------------------------------------------------------- //

  function handleSetMode(mode: DrawMode): void {
    const c = controllerRef.current;
    if (!c) return;
    c.setMode(mode);
    setActiveMode(mode);
    if (mode !== "select") setTagTarget(null);
  }

  function handleTag(barrierType: BarrierType): void {
    const c = controllerRef.current;
    if (!c || !tagTarget) return;
    c.tagBarrier(tagTarget.id, barrierType, {
      flapDirection: barrierType === "flap_gate" ? flapDirection : undefined,
    });
    setCounts(c.counts());
    setTagTarget(null);
  }

  function handleSnip(): void {
    const c = controllerRef.current;
    if (!c || !tagTarget) return;
    c.snipFeature(tagTarget.id);
    setCounts(c.counts());
    setTagTarget(null);
  }

  function handleClear(): void {
    const c = controllerRef.current;
    if (!c) return;
    c.clear();
    setCounts(c.counts());
    setTagTarget(null);
    setDiscardNotice(null);
  }

  function handleDiscardSmall(): void {
    const c = controllerRef.current;
    if (!c) return;
    const dropped = c.discardSmallPolygons(discardArea);
    setCounts(c.counts());
    setDiscardNotice(
      dropped.length > 0
        ? `Discarded ${dropped.length} polygon${dropped.length === 1 ? "" : "s"} under ${discardArea} m²`
        : `No polygons under ${discardArea} m²`,
    );
  }

  function handleSubmit(): void {
    if (isVectorDraw) {
      const c = controllerRef.current;
      if (!c) return;
      const features = c.getFeatureCollection();
      onSubmit({
        requestId: request.request_id,
        geometryType: "vector_draw",
        coordinates: null,
        features,
      });
    } else {
      if (!pickCoords) return;
      onSubmit({
        requestId: request.request_id,
        geometryType: request.mode === "point" ? "point" : "bbox",
        coordinates: pickCoords,
        features: null,
      });
    }
  }

  function handleCancel(): void {
    onCancel(request.request_id);
  }

  // --- Submit-enabled gate ----------------------------------------------- //
  const canSubmit = useMemo(() => {
    if (isVectorDraw) return counts.aoi + counts.barrier + counts.point > 0;
    return pickCoords !== null;
  }, [isVectorDraw, counts, pickCoords]);

  // --- Render ------------------------------------------------------------ //
  return (
    <div data-testid="spatial-draw-surface" style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      {/* Banner (reused FR-WC-13 pick-mode banner pattern). */}
      <div data-testid="spatial-draw-banner" style={bannerStyle}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <IconBbox size={15} color={PICK_COLOR} />
          <span style={{ fontWeight: 600 }}>{request.title}</span>
        </span>
        <span style={{ color: "#cbd5e1", fontSize: 12 }}>{request.description}</span>
      </div>

      {/* vector_draw toolbar. */}
      {isVectorDraw && (
        <div data-testid="spatial-draw-toolbar" style={toolbarStyle}>
          <ToolbarBtn
            label="Rectangle (AOI)"
            active={activeMode === "rectangle"}
            onClick={() => handleSetMode("rectangle")}
            icon={<IconBbox size={16} />}
            testid="draw-mode-rectangle"
          />
          <ToolbarBtn
            label="Polygon (AOI)"
            active={activeMode === "polygon"}
            onClick={() => handleSetMode("polygon")}
            icon={<IconPolygon size={16} />}
            testid="draw-mode-polygon"
          />
          <ToolbarBtn
            label="Line (barrier)"
            active={activeMode === "linestring"}
            onClick={() => handleSetMode("linestring")}
            icon={<IconLine size={16} />}
            testid="draw-mode-linestring"
          />
          <ToolbarBtn
            label="Select / edit"
            active={activeMode === "select"}
            onClick={() => handleSetMode("select")}
            icon={<IconMapPin size={16} />}
            testid="draw-mode-select"
          />
          <div style={{ width: 1, background: "rgba(255,255,255,0.12)", margin: "2px 4px" }} />
          <ToolbarBtn
            label="Discard tiny polygons"
            onClick={handleDiscardSmall}
            icon={<IconWarning size={16} />}
            testid="draw-discard-small"
          />
          <ToolbarBtn
            label="Clear all"
            onClick={handleClear}
            icon={<IconClose size={16} />}
            testid="draw-clear"
          />
          <span data-testid="draw-counts" style={countsStyle}>
            {counts.aoi} AOI · {counts.barrier} barrier
            {counts.untaggedBarrier > 0 ? ` (${counts.untaggedBarrier} untagged)` : ""}
          </span>
        </div>
      )}

      {/* Tagging popover (vector_draw select a barrier segment). */}
      {isVectorDraw && tagTarget && (
        <div data-testid="spatial-draw-tag-popover" style={tagPopoverStyle}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Tag barrier segment</div>
          <div style={{ display: "flex", gap: 6 }}>
            <button
              type="button"
              data-testid="tag-wall"
              onClick={() => handleTag("wall")}
              style={tagBtnStyle("#e53935")}
            >
              Wall (red)
            </button>
            <button
              type="button"
              data-testid="tag-flap-gate"
              onClick={() => handleTag("flap_gate")}
              style={tagBtnStyle("#43a047")}
            >
              Flap gate (green)
            </button>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 8 }}>
            <IconFlowArrow size={14} color="#cbd5e1" />
            <span style={{ fontSize: 11, color: "#cbd5e1" }}>Flap direction:</span>
            <button
              type="button"
              data-testid="flap-dir-out"
              onClick={() => setFlapDirection("out")}
              style={dirBtnStyle(flapDirection === "out")}
            >
              out
            </button>
            <button
              type="button"
              data-testid="flap-dir-in"
              onClick={() => setFlapDirection("in")}
              style={dirBtnStyle(flapDirection === "in")}
            >
              in
            </button>
          </div>
          <button
            type="button"
            data-testid="tag-snip"
            onClick={handleSnip}
            style={{ ...dirBtnStyle(false), marginTop: 8, display: "inline-flex", alignItems: "center", gap: 4 }}
          >
            <IconSnip size={13} /> Snip this segment
          </button>
        </div>
      )}

      {/* discard-area control + notice. */}
      {isVectorDraw && (
        <div data-testid="spatial-draw-discard-control" style={discardControlStyle}>
          <label style={{ fontSize: 11, color: "#cbd5e1" }}>
            Min polygon area: {discardArea} m²
            <input
              type="range"
              data-testid="draw-discard-slider"
              min={0}
              max={5000}
              step={50}
              value={discardArea}
              onChange={(e) => setDiscardArea(Number(e.target.value))}
              style={{ display: "block", width: 160 }}
            />
          </label>
          {discardNotice && (
            <span data-testid="draw-discard-notice" style={{ fontSize: 11, color: "#fbbf24" }}>
              {discardNotice}
            </span>
          )}
        </div>
      )}

      {/* Submit + Cancel (pinned bottom-center). */}
      <div data-testid="spatial-draw-actions" style={actionsStyle}>
        <button
          type="button"
          data-testid="spatial-draw-cancel"
          onClick={handleCancel}
          style={cancelBtnStyle}
        >
          <IconClose size={14} /> Cancel
        </button>
        <button
          type="button"
          data-testid="spatial-draw-submit"
          onClick={handleSubmit}
          disabled={!canSubmit}
          style={submitBtnStyle(canSubmit)}
        >
          <IconCheck size={14} /> Submit
        </button>
      </div>
    </div>
  );
}

// --- Toolbar button ------------------------------------------------------- //

function ToolbarBtn({
  label,
  active,
  onClick,
  icon,
  testid,
}: {
  label: string;
  active?: boolean;
  onClick: () => void;
  icon: JSX.Element;
  testid: string;
}): JSX.Element {
  return (
    <button
      type="button"
      data-testid={testid}
      data-active={active ? "true" : "false"}
      aria-label={label}
      title={label}
      onClick={onClick}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        border: active ? "1px solid #3b82f6" : "1px solid rgba(255,255,255,0.12)",
        background: active ? "rgba(59,130,246,0.22)" : "rgba(28,28,34,0.92)",
        color: "#e5e7eb",
        borderRadius: 6,
        padding: "6px 9px",
        fontSize: 12,
        cursor: "pointer",
        pointerEvents: "auto",
      }}
    >
      {icon}
    </button>
  );
}

// --- Pick-layer helpers (point / bbox) ----------------------------------- //

function ensurePickLayers(map: MapLibreMap): void {
  if (!safeStyleLoaded(map)) {
    map.once("idle", () => ensurePickLayers(map));
    return;
  }
  if (!map.getSource(PICK_SOURCE_ID)) {
    map.addSource(PICK_SOURCE_ID, {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
  }
  if (!map.getLayer(PICK_FILL_LAYER_ID)) {
    map.addLayer({
      id: PICK_FILL_LAYER_ID,
      type: "fill",
      source: PICK_SOURCE_ID,
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "fill-color": PICK_COLOR, "fill-opacity": 0.15 },
    });
  }
  if (!map.getLayer(PICK_LINE_LAYER_ID)) {
    map.addLayer({
      id: PICK_LINE_LAYER_ID,
      type: "line",
      source: PICK_SOURCE_ID,
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "line-color": PICK_COLOR, "line-width": 2 },
    });
  }
  if (!map.getLayer(PICK_POINT_LAYER_ID)) {
    map.addLayer({
      id: PICK_POINT_LAYER_ID,
      type: "circle",
      source: PICK_SOURCE_ID,
      filter: ["==", ["geometry-type"], "Point"],
      paint: {
        "circle-radius": 7,
        "circle-color": PICK_COLOR,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 2,
      },
    });
  }
}

function setPickData(map: MapLibreMap, data: GeoJSON.FeatureCollection): void {
  const src = map.getSource(PICK_SOURCE_ID) as GeoJSONSource | undefined;
  if (src && typeof src.setData === "function") src.setData(data);
}

function drawPickPoint(map: MapLibreMap, coords: number[]): void {
  setPickData(map, {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: { type: "Point", coordinates: coords },
        properties: {},
      },
    ],
  });
}

function drawPickBbox(map: MapLibreMap, bbox: number[]): void {
  const minLon = bbox[0] ?? 0;
  const minLat = bbox[1] ?? 0;
  const maxLon = bbox[2] ?? 0;
  const maxLat = bbox[3] ?? 0;
  setPickData(map, {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [minLon, minLat],
              [maxLon, minLat],
              [maxLon, maxLat],
              [minLon, maxLat],
              [minLon, minLat],
            ],
          ],
        },
        properties: {},
      },
    ],
  });
}

function clearPickLayers(map: MapLibreMap): void {
  try {
    for (const id of [PICK_POINT_LAYER_ID, PICK_LINE_LAYER_ID, PICK_FILL_LAYER_ID]) {
      if (map.getLayer(id)) map.removeLayer(id);
    }
    if (map.getSource(PICK_SOURCE_ID)) map.removeSource(PICK_SOURCE_ID);
  } catch {
    /* map torn down / style swapped */
  }
}

function orderBbox(a: [number, number], b: [number, number]): number[] {
  return [
    Math.min(a[0], b[0]),
    Math.min(a[1], b[1]),
    Math.max(a[0], b[0]),
    Math.max(a[1], b[1]),
  ];
}

function safeStyleLoaded(map: MapLibreMap): boolean {
  try {
    return map.isStyleLoaded() === true;
  } catch {
    return false;
  }
}

function setCursor(map: MapLibreMap, cursor: string): string {
  try {
    const c = map.getCanvas();
    const prev = c.style.cursor;
    c.style.cursor = cursor;
    return prev;
  } catch {
    return "";
  }
}

// --- Styles --------------------------------------------------------------- //

const bannerStyle: React.CSSProperties = {
  position: "absolute",
  top: 12,
  left: "50%",
  transform: "translateX(-50%)",
  background: "rgba(20,20,26,0.92)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 8,
  boxShadow: "0 4px 14px rgba(0,0,0,0.4)",
  color: "#e5e7eb",
  padding: "8px 14px",
  display: "flex",
  flexDirection: "column",
  gap: 3,
  fontSize: 13,
  fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
  maxWidth: "70%",
  pointerEvents: "auto",
  zIndex: 5,
};

const toolbarStyle: React.CSSProperties = {
  position: "absolute",
  top: 70,
  left: "50%",
  transform: "translateX(-50%)",
  display: "flex",
  alignItems: "center",
  gap: 4,
  background: "rgba(20,20,26,0.85)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 8,
  padding: 5,
  pointerEvents: "auto",
  zIndex: 5,
};

const countsStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#cbd5e1",
  padding: "0 6px",
  fontFamily: "system-ui, sans-serif",
};

const tagPopoverStyle: React.CSSProperties = {
  position: "absolute",
  top: 120,
  left: "50%",
  transform: "translateX(-50%)",
  background: "rgba(20,20,26,0.95)",
  border: "1px solid rgba(255,255,255,0.1)",
  borderRadius: 8,
  boxShadow: "0 4px 14px rgba(0,0,0,0.45)",
  color: "#e5e7eb",
  padding: 12,
  pointerEvents: "auto",
  zIndex: 6,
  fontFamily: "system-ui, sans-serif",
};

const discardControlStyle: React.CSSProperties = {
  position: "absolute",
  top: 70,
  right: 12,
  background: "rgba(20,20,26,0.85)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 8,
  padding: 8,
  display: "flex",
  flexDirection: "column",
  gap: 4,
  pointerEvents: "auto",
  zIndex: 5,
  fontFamily: "system-ui, sans-serif",
};

const actionsStyle: React.CSSProperties = {
  position: "absolute",
  bottom: 18,
  left: "50%",
  transform: "translateX(-50%)",
  display: "flex",
  gap: 10,
  pointerEvents: "auto",
  zIndex: 6,
};

const cancelBtnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  border: "1px solid rgba(255,255,255,0.14)",
  background: "rgba(28,28,34,0.95)",
  color: "#cbd5e1",
  borderRadius: 8,
  padding: "8px 16px",
  fontSize: 13,
  fontWeight: 500,
  cursor: "pointer",
  fontFamily: "system-ui, sans-serif",
};

function submitBtnStyle(enabled: boolean): React.CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 5,
    border: "1px solid #3b82f6",
    background: enabled ? "#3b82f6" : "rgba(59,130,246,0.35)",
    color: enabled ? "#0b0b0e" : "rgba(255,255,255,0.55)",
    borderRadius: 8,
    padding: "8px 18px",
    fontSize: 13,
    fontWeight: 600,
    cursor: enabled ? "pointer" : "not-allowed",
    fontFamily: "system-ui, sans-serif",
  };
}

function tagBtnStyle(color: string): React.CSSProperties {
  return {
    border: `1px solid ${color}`,
    background: color,
    color: "#0b0b0e",
    borderRadius: 6,
    padding: "6px 10px",
    fontSize: 12,
    fontWeight: 600,
    cursor: "pointer",
    fontFamily: "system-ui, sans-serif",
  };
}

function dirBtnStyle(active: boolean): React.CSSProperties {
  return {
    border: active ? "1px solid #3b82f6" : "1px solid rgba(255,255,255,0.14)",
    background: active ? "rgba(59,130,246,0.22)" : "transparent",
    color: "#e5e7eb",
    borderRadius: 5,
    padding: "3px 8px",
    fontSize: 11,
    cursor: "pointer",
    fontFamily: "system-ui, sans-serif",
  };
}
