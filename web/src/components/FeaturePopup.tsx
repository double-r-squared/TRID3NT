// GRACE-2 web — FeaturePopup (F74b feature-click/tap-to-inspect).
//
// The agent advertises "click polygons to see name / designation / IUCN" but
// no such handler existed in the web client. This component is the popup half
// of that feature: Map.tsx runs queryRenderedFeatures on a click/tap against
// the rendered inline-GeoJSON vector layers (job-0175) and, on a hit, renders
// THIS popup at the screen point with the feature's key attributes.
//
// Design choices:
//   - React overlay (NOT maplibregl.Popup) so we get full control over mobile
//     positioning, our icon set (icons.tsx — NO raw glyphs per the project
//     policy), and a tap-anywhere/Esc/X dismiss model that works for touch.
//     maplibregl.Popup's anchor logic can clip off the small-screen viewport;
//     a self-positioned overlay lets us clamp into the visible canvas.
//   - Pure presentational + dismiss callbacks. All hit-testing, property
//     extraction and screen-point math live in Map.tsx (Invariant 1: the
//     client renders received values — here, feature.properties — it never
//     computes geography).
//
// Invariant 1: every value shown comes straight from feature.properties; no
// number is computed here.

import { useEffect } from "react";
import { IconClose } from "./icons";

/** One attribute row in the popup. `label` is the human-facing key. */
export interface FeatureAttribute {
  label: string;
  value: string;
}

/** Screen-space point (canvas-relative px) where the feature was hit. */
export interface PopupPoint {
  x: number;
  y: number;
}

/** Fully-resolved popup content + placement, produced by Map.tsx. */
export interface FeaturePopupData {
  /** Bold heading — the feature's best "name" (or a geometry-kind fallback). */
  title: string;
  /** Optional sub-heading — designation / type / layer name. */
  subtitle?: string;
  /** Ordered attribute rows (already humanized + stringified by the caller). */
  attributes: FeatureAttribute[];
  /** Canvas-relative pixel point of the click/tap. */
  point: PopupPoint;
}

export interface FeaturePopupProps {
  data: FeaturePopupData;
  /** Width/height of the map canvas (for off-screen clamping). */
  canvasSize: { width: number; height: number };
  /** Mobile viewport → center the card instead of anchoring to the point. */
  isMobile: boolean;
  /** Dismiss (X tap / Esc). Tap-elsewhere dismissal is wired in Map.tsx. */
  onClose: () => void;
}

// Card sizing. Kept narrow so it does not blanket a phone screen, and wide
// enough on desktop to show a name + a few attributes without wrapping hard.
const CARD_WIDTH_DESKTOP = 260;
const CARD_WIDTH_MOBILE = 280;
const EDGE_GAP = 12; // min px between the card and the canvas edge.
const POINT_OFFSET = 14; // px the card is nudged from the clicked point.
const EST_CARD_HEIGHT = 220; // rough height used only for vertical clamping.

/**
 * Resolve the absolute {left, top} for the card.
 *   - Mobile: pin near the bottom-center of the canvas so it never clips off a
 *     narrow screen and never sits under the user's finger.
 *   - Desktop: anchor just to the upper-right of the clicked point, then clamp
 *     into the canvas so it can't run off any edge.
 * Pure — exported so the placement math is unit-testable without rendering.
 */
export function resolvePopupPlacement(
  point: PopupPoint,
  canvasSize: { width: number; height: number },
  isMobile: boolean,
): { left: number; top: number; width: number } {
  const width = isMobile ? CARD_WIDTH_MOBILE : CARD_WIDTH_DESKTOP;
  const w = canvasSize.width || width + EDGE_GAP * 2;
  const h = canvasSize.height || EST_CARD_HEIGHT + EDGE_GAP * 2;

  if (isMobile) {
    // Centered horizontally; pinned toward the bottom but above the very edge
    // (and above where most bottom sheets/composers live).
    const left = Math.max(EDGE_GAP, (w - width) / 2);
    const top = Math.max(EDGE_GAP, h - EST_CARD_HEIGHT - 96);
    return { left, top, width };
  }

  // Desktop: place to the upper-right of the point, then clamp into the canvas.
  let left = point.x + POINT_OFFSET;
  let top = point.y - POINT_OFFSET;
  if (left + width + EDGE_GAP > w) {
    // Not enough room on the right — flip to the left of the point.
    left = point.x - width - POINT_OFFSET;
  }
  left = Math.min(Math.max(EDGE_GAP, left), Math.max(EDGE_GAP, w - width - EDGE_GAP));
  top = Math.min(
    Math.max(EDGE_GAP, top),
    Math.max(EDGE_GAP, h - EST_CARD_HEIGHT - EDGE_GAP),
  );
  return { left, top, width };
}

export function FeaturePopup({
  data,
  canvasSize,
  isMobile,
  onClose,
}: FeaturePopupProps): JSX.Element {
  // Esc dismisses (desktop + bluetooth-keyboard mobile). Tap-elsewhere is wired
  // in Map.tsx (it owns the map canvas + document listeners).
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const { left, top, width } = resolvePopupPlacement(
    data.point,
    canvasSize,
    isMobile,
  );

  return (
    <div
      data-testid="grace2-feature-popup"
      role="dialog"
      aria-label={data.title}
      // The popup must capture pointer events even though it sits over the map.
      style={{
        position: "absolute",
        left,
        top,
        width,
        maxWidth: "calc(100% - 24px)",
        maxHeight: "60%",
        overflowY: "auto",
        background: "rgba(17,18,23,0.92)",
        backdropFilter: "blur(8px)",
        WebkitBackdropFilter: "blur(8px)",
        border: "1px solid rgba(255,255,255,0.10)",
        borderRadius: 10,
        boxShadow: "0 6px 24px rgba(0,0,0,0.55)",
        color: "#eee",
        fontFamily: "system-ui, sans-serif",
        zIndex: 20, // above the legend (zIndex 10), below modals (>=2000).
        pointerEvents: "auto",
      }}
      // Stop taps inside the card from bubbling to the map's tap-elsewhere
      // dismissal (Map.tsx listens on the document).
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    >
      {/* Header: title + subtitle + close button. */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: 8,
          padding: "10px 10px 8px 12px",
          borderBottom:
            data.attributes.length > 0
              ? "1px solid rgba(255,255,255,0.08)"
              : "none",
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            data-testid="feature-popup-title"
            style={{
              fontSize: 13,
              fontWeight: 600,
              lineHeight: 1.25,
              wordBreak: "break-word",
              color: "#fff",
            }}
          >
            {data.title}
          </div>
          {data.subtitle ? (
            <div
              data-testid="feature-popup-subtitle"
              style={{
                fontSize: 11,
                color: "#9aa3b2",
                marginTop: 2,
                wordBreak: "break-word",
              }}
            >
              {data.subtitle}
            </div>
          ) : null}
        </div>
        <button
          type="button"
          data-testid="feature-popup-close"
          aria-label="Close"
          onClick={onClose}
          style={{
            flex: "0 0 auto",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 28,
            height: 28,
            // Generous touch target for mobile.
            padding: 0,
            background: "transparent",
            border: "none",
            borderRadius: 6,
            color: "#aab2c0",
            cursor: "pointer",
          }}
        >
          <IconClose size={16} />
        </button>
      </div>

      {/* Attribute list — compact key/value rows. */}
      {data.attributes.length > 0 ? (
        <div
          data-testid="feature-popup-attributes"
          style={{ padding: "8px 12px 12px" }}
        >
          {data.attributes.map((attr, i) => (
            <div
              key={`${attr.label}-${i}`}
              style={{
                display: "flex",
                justifyContent: "space-between",
                gap: 12,
                padding: "3px 0",
                fontSize: 12,
                lineHeight: 1.35,
              }}
            >
              <span
                style={{
                  color: "#8b93a3",
                  flex: "0 0 auto",
                  maxWidth: "45%",
                  wordBreak: "break-word",
                }}
              >
                {attr.label}
              </span>
              <span
                style={{
                  color: "#e8eaee",
                  textAlign: "right",
                  wordBreak: "break-word",
                  minWidth: 0,
                }}
              >
                {attr.value}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <div
          data-testid="feature-popup-empty"
          style={{ padding: "8px 12px 12px", fontSize: 12, color: "#8b93a3" }}
        >
          No additional attributes.
        </div>
      )}
    </div>
  );
}
