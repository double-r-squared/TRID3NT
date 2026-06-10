// GRACE-2 web — ChartStack (sprint-13, conversational analysis layer, job-0231).
//
// Renders a group of chart-emission payloads that share the same ``created_turn_id``
// as an inline stacked preview in the chat scroll. Layout:
//
//   - Top chart is fully visible (~200×150 px, Vega-Lite via vega-embed).
//   - Additional charts in the same stack appear as offset card "shadows" behind
//     the visible one (4 px offset each), giving a tangible "N charts here" cue.
//   - When the stack has more than 3 charts total, a "+N more" badge appears in the
//     bottom-right corner of the top card.
//   - Clicking anywhere on the stack (card or shadows) opens ChartGallery.
//
// A singleton stack (1 chart with no siblings) renders identically to the top card
// of a multi-chart stack — the shadows simply do not appear.
//
// Stack grouping is performed by the parent (App.tsx / Chat.tsx) via
// ``created_turn_id``; this component receives an already-grouped array and renders
// it. It does NOT perform grouping itself (single-responsibility).
//
// Vega-embed note: we let the library render into a ref'd div. On ``created_turn_id``
// or ``charts`` change we re-embed. This is idiomatic for vega-embed in React — the
// library is not React-native, so we use the DOM seam cleanly.

import { useCallback, useEffect, useRef, useState } from "react";
import type { Result as VegaEmbedResult } from "vega-embed";

/** Minimal wire shape — matches ChartEmissionPayload from chart_contracts.py. */
export interface ChartPayload {
  chart_id: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vega_lite_spec: Record<string, any>;
  title: string;
  caption?: string | null;
  source_layer_uri?: string | null;
  created_turn_id?: string | null;
}

export interface ChartStackProps {
  /** One or more charts that share the same ``created_turn_id`` (or are singletons). */
  charts: ChartPayload[];
  /** Called when the user clicks the stack, to open the gallery at ``initialIndex``. */
  onOpenGallery: (charts: ChartPayload[], initialIndex: number) => void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Maximum shadow cards rendered (the rest are counted in the +N badge). */
const MAX_SHADOW_CARDS = 2;
/** Pixel offset between stacked shadow cards. */
const SHADOW_OFFSET_PX = 4;
/** Top-card chart area dimensions. */
const CHART_WIDTH = 200;
const CHART_HEIGHT = 150;

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const containerStyle: React.CSSProperties = {
  position: "relative",
  // Height is the chart + header + caption + padding, PLUS shadow card offsets
  // so they peek out below and to the right.
  display: "inline-block",
  cursor: "pointer",
};

function shadowStyle(index: number): React.CSSProperties {
  // index 0 = first shadow (second chart in stack), etc.
  const offset = (index + 1) * SHADOW_OFFSET_PX;
  return {
    position: "absolute",
    top: offset,
    left: offset,
    width: CHART_WIDTH + 16, // same as top card
    height: CHART_HEIGHT + 40, // header + caption area
    background: "rgba(30,32,42,0.75)",
    border: "1px solid #3a3d49",
    borderRadius: 8,
    // Shadows sit BELOW the top card (negative z-index relative to parent).
    zIndex: -(index + 1),
  };
}

const topCardStyle: React.CSSProperties = {
  position: "relative",
  background: "rgba(20,22,30,0.96)",
  border: "1px solid #444",
  borderRadius: 8,
  padding: "8px 8px 6px",
  boxShadow: "0 4px 16px rgba(0,0,0,0.35)",
  zIndex: 1,
  width: CHART_WIDTH + 16, // inner chart + 2×8 padding
  boxSizing: "border-box",
};

const titleStyle: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  color: "#dde5f5",
  marginBottom: 4,
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  maxWidth: CHART_WIDTH,
};

const captionStyle: React.CSSProperties = {
  fontSize: 10,
  color: "#9aa0ad",
  marginTop: 4,
  lineHeight: 1.3,
  maxWidth: CHART_WIDTH,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const badgeStyle: React.CSSProperties = {
  position: "absolute",
  bottom: 8,
  right: 8,
  background: "rgba(60,80,120,0.92)",
  border: "1px solid #4a6096",
  borderRadius: 10,
  padding: "1px 6px",
  fontSize: 10,
  color: "#9fcfff",
  fontWeight: 600,
  pointerEvents: "none",
};

const chartAreaStyle: React.CSSProperties = {
  width: CHART_WIDTH,
  height: CHART_HEIGHT,
  overflow: "hidden",
  borderRadius: 4,
  background: "rgba(12,14,20,0.8)",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/** Dynamically import vega-embed to avoid bloating the initial bundle. */
async function embedChart(
  el: HTMLElement,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  spec: Record<string, any>,
): Promise<VegaEmbedResult> {
  const { default: embed } = await import("vega-embed");
  return embed(el, spec as Parameters<typeof embed>[1], {
    actions: false,
    renderer: "svg",
    width: CHART_WIDTH - 8,
    height: CHART_HEIGHT - 8,
    padding: 4,
    config: {
      background: "transparent",
      axis: { labelColor: "#9aa0ad", titleColor: "#9aa0ad", gridColor: "#2a2d35" },
      title: { color: "#dde5f5", fontSize: 11 },
      legend: { labelColor: "#9aa0ad", titleColor: "#9aa0ad" },
      view: { stroke: "transparent" },
    },
  });
}

export function ChartStack({ charts, onOpenGallery }: ChartStackProps): JSX.Element | null {
  const chartAreaRef = useRef<HTMLDivElement | null>(null);
  const vegaResultRef = useRef<VegaEmbedResult | null>(null);
  const [embedError, setEmbedError] = useState<string | null>(null);

  const topChart = charts[0];

  // Embed or re-embed whenever the top spec changes.
  useEffect(() => {
    if (!chartAreaRef.current || !topChart) return;
    let cancelled = false;
    setEmbedError(null);

    // Finalize the previous embed before starting a new one.
    void (async () => {
      if (vegaResultRef.current) {
        try { vegaResultRef.current.finalize(); } catch { /* ignore */ }
        vegaResultRef.current = null;
      }
      if (!chartAreaRef.current || cancelled) return;
      try {
        const result = await embedChart(chartAreaRef.current, topChart.vega_lite_spec);
        if (!cancelled) {
          vegaResultRef.current = result;
        } else {
          try { result.finalize(); } catch { /* ignore */ }
        }
      } catch (err) {
        if (!cancelled) {
          setEmbedError(err instanceof Error ? err.message : "chart render error");
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [topChart?.chart_id, topChart?.vega_lite_spec]); // re-embed only when the top chart actually changes

  // Finalize on unmount.
  useEffect(() => {
    return () => {
      if (vegaResultRef.current) {
        try { vegaResultRef.current.finalize(); } catch { /* ignore */ }
        vegaResultRef.current = null;
      }
    };
  }, []);

  const handleClick = useCallback(() => {
    onOpenGallery(charts, 0);
  }, [charts, onOpenGallery]);

  if (!topChart) return null;

  // Shadows: render at most MAX_SHADOW_CARDS behind the top card.
  const shadowCount = Math.min(charts.length - 1, MAX_SHADOW_CARDS);
  // Badge: "+N" where N = total charts beyond the MAX_SHADOW_CARDS + 1 visible.
  const hiddenCount = charts.length - (MAX_SHADOW_CARDS + 1);
  const showBadge = hiddenCount > 0;

  // Total horizontal/vertical space occupied by the shadow cards.
  const shadowSpread = shadowCount * SHADOW_OFFSET_PX;

  return (
    <div
      data-testid="chart-stack"
      data-chart-count={charts.length}
      data-top-chart-id={topChart.chart_id}
      style={{
        ...containerStyle,
        // Expand outer container to accommodate shadow cards.
        paddingRight: shadowSpread,
        paddingBottom: shadowSpread,
      }}
      onClick={handleClick}
      role="button"
      aria-label={`Chart: ${topChart.title}${charts.length > 1 ? ` (+${charts.length - 1} more)` : ""}. Click to open gallery.`}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          handleClick();
        }
      }}
    >
      {/* Shadow cards behind the top card (reversed so shadow[0] is closest to top) */}
      {Array.from({ length: shadowCount }, (_, i) => (
        <div
          key={`shadow-${i}`}
          data-testid="chart-stack-shadow"
          style={shadowStyle(shadowCount - 1 - i)}
          aria-hidden="true"
        />
      ))}

      {/* Top card */}
      <div
        data-testid="chart-stack-top-card"
        style={topCardStyle}
      >
        <div style={titleStyle} title={topChart.title}>
          {topChart.title}
        </div>

        <div
          ref={chartAreaRef}
          data-testid="chart-embed-area"
          style={chartAreaStyle}
        >
          {embedError && (
            <div
              style={{
                color: "#f9c1c1",
                fontSize: 10,
                padding: 4,
                lineHeight: 1.4,
              }}
            >
              Chart render error: {embedError}
            </div>
          )}
        </div>

        {topChart.caption && (
          <div style={captionStyle} title={topChart.caption}>
            {topChart.caption}
          </div>
        )}

        {showBadge && (
          <div
            data-testid="chart-stack-badge"
            style={badgeStyle}
          >
            +{hiddenCount} more
          </div>
        )}
      </div>
    </div>
  );
}
