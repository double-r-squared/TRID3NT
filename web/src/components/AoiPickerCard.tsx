// GRACE-2 web - AoiPickerCard (#170 J-WEB-2).
//
// The AOI-first capture card shown when a user MANUALLY creates a Case, BEFORE
// the first prompt (see reports/design/aoi_first_case_creation.md). The user
// sets the analysis bbox up front so the agent reuses the exact extent and does
// NOT re-geocode from the prompt.
//
// Two ways to set the bbox:
//   - PRIMARY: DRAW. Drag a rectangle on the live MapLibre map (the same gesture
//     the spatial-input pick mode uses, extracted into lib/bbox_draw.ts so this
//     card can reuse it). The in-progress + final rectangle paints onto the map.
//   - SECONDARY: COORDS fallback. Four numeric inputs (minLon / minLat / maxLon /
//     maxLat). Validated (finite + lon[-180,180] + lat[-90,90], min/max
//     normalized) the same way the server's _is_finite_bbox4 does; "Preview on
//     map" paints the typed box (and frames it) via the same draw helper.
//
// REQUEST-FREE by construction: this card does NOT touch the spatial-input bus,
// SpatialDrawSurface, or any spatial-input-response wire. There is no active
// agent turn when a Case is being created (the box may be asleep). It only
// reports the chosen bbox up to its parent via onConfirm; the case-command(create)
// it ultimately drives rides the durable sendOrQueue path. Confirm yields a
// [minLon, minLat, maxLon, maxLat]; Skip yields no bbox (the current behavior).

import { useEffect, useMemo, useRef, useState } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import {
  attachBboxDrag,
  clearPickLayers,
  drawPickBbox,
  ensurePickLayers,
  validateBbox,
  type BBox,
} from "../lib/bbox_draw";
import { IconBbox, IconCheck, IconClose, IconWarning } from "./icons";

const ACCENT = "#3b82f6";

export interface AoiPickerCardProps {
  /** The live MapLibre instance (Map.tsx's `map.current`). May be absent in
   *  headless tests / before the map is ready - the coords fallback still works. */
  map?: MapLibreMap | null;
  /** Confirm with the chosen AOI bbox [minLon, minLat, maxLon, maxLat]. */
  onConfirm: (bbox: BBox) => void;
  /** Skip the AOI step - create the Case with NO bbox (the current behavior). */
  onSkip: () => void;
  /** Dismiss the overlay without creating a Case. */
  onCancel: () => void;
}

/** A 4-tuple of the raw input strings, kept as strings so partial / mid-type
 *  values (e.g. "-" or "") do not get coerced to 0 / NaN prematurely. */
type CoordFields = {
  minLon: string;
  minLat: string;
  maxLon: string;
  maxLat: string;
};

const EMPTY_FIELDS: CoordFields = {
  minLon: "",
  minLat: "",
  maxLon: "",
  maxLat: "",
};

/** Parse the four string fields into a validated, ordered BBox or null. */
export function coordsToBbox(fields: CoordFields): BBox | null {
  const minLon = Number(fields.minLon);
  const minLat = Number(fields.minLat);
  const maxLon = Number(fields.maxLon);
  const maxLat = Number(fields.maxLat);
  // Treat empty / whitespace-only as not-yet-entered (Number("") === 0 would
  // otherwise read as a valid 0). Require all four to be non-empty.
  if (
    fields.minLon.trim() === "" ||
    fields.minLat.trim() === "" ||
    fields.maxLon.trim() === "" ||
    fields.maxLat.trim() === ""
  ) {
    return null;
  }
  return validateBbox(minLon, minLat, maxLon, maxLat);
}

export function AoiPickerCard({
  map,
  onConfirm,
  onSkip,
  onCancel,
}: AoiPickerCardProps): JSX.Element {
  // The currently-captured bbox (from draw OR a previewed coords entry). This is
  // the single value Confirm sends.
  const [bbox, setBbox] = useState<BBox | null>(null);
  const [fields, setFields] = useState<CoordFields>(EMPTY_FIELDS);
  // Surfaced when "Preview on map" is pressed with invalid coords.
  const [coordsError, setCoordsError] = useState<string | null>(null);

  // Keep the live map in a ref so the draw effect can read the current instance
  // without re-arming the gesture on every render.
  const mapRef = useRef<MapLibreMap | null>(map ?? null);
  mapRef.current = map ?? null;

  // PRIMARY draw: arm the drag-rectangle gesture on the live map. The pick
  // layers are this card's own (the BBOX_* set in bbox_draw), so they never
  // collide with a draw surface. Re-arms only when the map instance changes.
  useEffect(() => {
    const m = map;
    if (!m) return;
    ensurePickLayers(m);
    const detach = attachBboxDrag(m, {
      onProgress: (b) => drawPickBbox(m, b),
      onComplete: (b) => {
        setBbox(b);
        drawPickBbox(m, b);
        setCoordsError(null);
      },
    });
    return () => {
      detach();
      clearPickLayers(m);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  function setField(key: keyof CoordFields, value: string): void {
    setFields((prev) => ({ ...prev, [key]: value }));
  }

  function handlePreview(): void {
    const parsed = coordsToBbox(fields);
    if (!parsed) {
      setCoordsError(
        "Enter four finite numbers - longitude in [-180, 180], latitude in [-90, 90].",
      );
      return;
    }
    setCoordsError(null);
    setBbox(parsed);
    const m = mapRef.current;
    if (m) {
      ensurePickLayers(m);
      drawPickBbox(m, parsed);
      try {
        m.fitBounds(
          [
            [parsed[0], parsed[1]],
            [parsed[2], parsed[3]],
          ],
          { padding: 64, duration: 600, maxZoom: 17 },
        );
      } catch {
        /* degenerate bbox - leave the camera */
      }
    }
  }

  function handleConfirm(): void {
    if (!bbox) return;
    onConfirm(bbox);
  }

  const canConfirm = bbox !== null;

  // Echo the captured bbox compactly so the user can confirm what will be sent.
  const bboxLabel = useMemo<string | null>(() => {
    if (!bbox) return null;
    const f = (n: number): string => n.toFixed(4);
    return `[${f(bbox[0])}, ${f(bbox[1])}, ${f(bbox[2])}, ${f(bbox[3])}]`;
  }, [bbox]);

  return (
    <div data-testid="aoi-picker-card" style={cardStyle}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <IconBbox size={16} color={ACCENT} />
        <span style={{ fontWeight: 600, fontSize: 14 }}>Set the area of interest</span>
      </div>
      <p style={{ margin: 0, fontSize: 12, color: "#cbd5e1" }}>
        Drag a rectangle on the map, or enter coordinates. You can also skip this
        and let the agent pick the extent from your first prompt.
      </p>

      {/* PRIMARY - draw hint. */}
      <div style={hintStyle} data-testid="aoi-draw-hint">
        <IconBbox size={13} color={ACCENT} />
        <span>Drag on the map to draw the bounding box.</span>
      </div>

      {/* SECONDARY - coords fallback. */}
      <div data-testid="aoi-coords" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={{ fontSize: 11, color: "#94a3b8", fontWeight: 600 }}>
          Or enter coordinates (EPSG:4326)
        </span>
        <div style={coordsGridStyle}>
          <CoordInput
            label="min lon"
            testid="aoi-min-lon"
            value={fields.minLon}
            onChange={(v) => setField("minLon", v)}
          />
          <CoordInput
            label="min lat"
            testid="aoi-min-lat"
            value={fields.minLat}
            onChange={(v) => setField("minLat", v)}
          />
          <CoordInput
            label="max lon"
            testid="aoi-max-lon"
            value={fields.maxLon}
            onChange={(v) => setField("maxLon", v)}
          />
          <CoordInput
            label="max lat"
            testid="aoi-max-lat"
            value={fields.maxLat}
            onChange={(v) => setField("maxLat", v)}
          />
        </div>
        <button
          type="button"
          data-testid="aoi-preview"
          onClick={handlePreview}
          style={previewBtnStyle}
        >
          Preview on map
        </button>
        {coordsError && (
          <span data-testid="aoi-coords-error" role="status" style={errorStyle}>
            <IconWarning size={12} color="#fbbf24" />
            {coordsError}
          </span>
        )}
      </div>

      {/* Captured bbox echo. */}
      {bboxLabel && (
        <div data-testid="aoi-bbox-echo" style={echoStyle}>
          AOI set: <span style={{ fontFamily: "monospace" }}>{bboxLabel}</span>
        </div>
      )}

      {/* Actions. */}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 4 }}>
        <button
          type="button"
          data-testid="aoi-cancel"
          onClick={onCancel}
          style={ghostBtnStyle}
        >
          <IconClose size={13} /> Cancel
        </button>
        <button
          type="button"
          data-testid="aoi-skip"
          onClick={onSkip}
          style={ghostBtnStyle}
        >
          Skip
        </button>
        <button
          type="button"
          data-testid="aoi-confirm"
          onClick={handleConfirm}
          disabled={!canConfirm}
          title={canConfirm ? undefined : "Draw or enter an AOI first"}
          style={confirmBtnStyle(canConfirm)}
        >
          <IconCheck size={13} /> Create with AOI
        </button>
      </div>
    </div>
  );
}

// --- Coord input ---------------------------------------------------------- //

function CoordInput({
  label,
  testid,
  value,
  onChange,
}: {
  label: string;
  testid: string;
  value: string;
  onChange: (v: string) => void;
}): JSX.Element {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 10, color: "#94a3b8" }}>
      {label}
      <input
        type="number"
        inputMode="decimal"
        step="any"
        data-testid={testid}
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={inputStyle}
      />
    </label>
  );
}

// --- Styles --------------------------------------------------------------- //

const cardStyle: React.CSSProperties = {
  position: "absolute",
  top: 16,
  left: "50%",
  transform: "translateX(-50%)",
  width: 320,
  maxWidth: "90%",
  background: "rgba(20,20,26,0.96)",
  border: "1px solid rgba(255,255,255,0.1)",
  borderRadius: 10,
  boxShadow: "0 8px 28px rgba(0,0,0,0.5)",
  color: "#e5e7eb",
  padding: 14,
  display: "flex",
  flexDirection: "column",
  gap: 10,
  fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
  pointerEvents: "auto",
  zIndex: 7,
};

const hintStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  fontSize: 11,
  color: "#cbd5e1",
  background: "rgba(59,130,246,0.12)",
  border: "1px solid rgba(59,130,246,0.3)",
  borderRadius: 6,
  padding: "5px 8px",
};

const coordsGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: 6,
};

const inputStyle: React.CSSProperties = {
  background: "rgba(0,0,0,0.35)",
  border: "1px solid rgba(255,255,255,0.14)",
  borderRadius: 5,
  color: "#e5e7eb",
  padding: "6px 8px",
  fontSize: 12,
  width: "100%",
  boxSizing: "border-box",
};

const previewBtnStyle: React.CSSProperties = {
  alignSelf: "flex-start",
  border: "1px solid rgba(255,255,255,0.16)",
  background: "rgba(28,28,34,0.95)",
  color: "#cbd5e1",
  borderRadius: 6,
  padding: "5px 10px",
  fontSize: 12,
  cursor: "pointer",
};

const errorStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  fontSize: 11,
  color: "#fbbf24",
};

const echoStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#86efac",
  background: "rgba(34,197,94,0.1)",
  border: "1px solid rgba(34,197,94,0.3)",
  borderRadius: 6,
  padding: "5px 8px",
};

const ghostBtnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  border: "1px solid rgba(255,255,255,0.14)",
  background: "transparent",
  color: "#cbd5e1",
  borderRadius: 7,
  padding: "7px 12px",
  fontSize: 12,
  fontWeight: 500,
  cursor: "pointer",
};

function confirmBtnStyle(enabled: boolean): React.CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 5,
    border: "1px solid #3b82f6",
    background: enabled ? "#3b82f6" : "rgba(59,130,246,0.35)",
    color: enabled ? "#0b0b0e" : "rgba(255,255,255,0.55)",
    borderRadius: 7,
    padding: "7px 14px",
    fontSize: 12,
    fontWeight: 600,
    cursor: enabled ? "pointer" : "not-allowed",
  };
}
