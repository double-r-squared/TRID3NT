// GRACE-2 web — LayerPanel (FR-WC-4, FR-WC-6 partial).
//
// Renders the project's loaded layers from `session-state.loaded_layers`
// (Appendix D.2 / D.6) and applies live `map-command` updates from the
// agent. Each row has:
//
//   - drag handle (drag-and-drop reorder via @dnd-kit/sortable)
//   - visibility checkbox
//   - opacity slider (0..1)
//   - name + attribution
//
// The ▲/▼ nudge buttons were dropped in job-0173 Part 4 — they were
// redundant with @dnd-kit's drag-and-drop reorder (which also provides
// the keyboard reorder a11y path the nudge buttons were nominally for).
//
// job-0258 (LAYER CONTROLS DEAD root-cause fix): user-side clicks now emit
// real `map-command` payloads through the optional `onMapCommand` prop —
// App.tsx wires it to the shared LayerPanelBus so MapView applies them to
// the live MapLibre instance (setPaintProperty / setLayoutProperty /
// moveLayer). Before this job the handlers below ONLY dispatched to the
// panel's local reducer + console.debug "intent" logs (the M3 stubs), so
// the opacity slider and drag-reorder visibly did nothing on the map.
// Agent-side persistence of these intents remains future work (the bus is
// client-local; nothing is sent to the agent yet).
//
// The panel renders the layer list **top-of-stack-first** (top of list =
// rendered on top). `z_index` from ProjectLayerSummary is INTERPRETED:
// higher z_index = higher in the stack = earlier in the list. This matches
// MapLibre's add-layer-on-top semantics.
//
// Drag-and-drop library choice: @dnd-kit/sortable. Surfaced as Open Question
// — alternatives are hand-rolled HTML5 DnD or react-dnd. @dnd-kit chosen
// because: (a) actively maintained, (b) full keyboard a11y out of the box
// (the extra up/down buttons are belt+suspenders), (c) zero global state.

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  MapCommandPayload,
  ProjectLayerSummary,
  SessionStatePayload,
} from "./contracts";

// --- Reducer + state shape --------------------------------------------- //
//
// Internal view-model = the rendered ordered list of layers. The list is
// kept sorted top-of-stack-first; reducer actions translate from external
// session-state / map-command envelopes onto this representation.

interface LayerPanelState {
  layers: ProjectLayerSummary[]; // top-of-stack first
}

type LayerPanelAction =
  | { type: "session-state"; payload: SessionStatePayload }
  | { type: "map-command"; payload: MapCommandPayload }
  | { type: "local-reorder"; layer_ids: string[] }
  | { type: "local-visibility"; layer_id: string; visible: boolean }
  | { type: "local-opacity"; layer_id: string; opacity: number };

function sortTopFirst(layers: ProjectLayerSummary[]): ProjectLayerSummary[] {
  // Sort by z_index descending — top-of-stack first.
  return [...layers].sort((a, b) => b.z_index - a.z_index);
}

/**
 * ux-batch-1 J3 (F22) — collapse duplicate layer_ids, keeping the LAST
 * occurrence (a republish of the same layer_id appends the newer version).
 * Without this, an undeduped loaded_layers list (e.g. from a recompute that
 * re-published the same layer_id) rendered TWO rows sharing one React key —
 * a key collision that made their opacity sliders move together ("connected
 * sliders"). Order is preserved by last-seen position so the subsequent
 * sortTopFirst is stable. Exported for unit testing.
 */
export function dedupeByLayerId(
  layers: ProjectLayerSummary[],
): ProjectLayerSummary[] {
  const byId = new Map<string, ProjectLayerSummary>();
  for (const l of layers) byId.set(l.layer_id, l); // last write wins
  return Array.from(byId.values());
}

function reducer(state: LayerPanelState, action: LayerPanelAction): LayerPanelState {
  switch (action.type) {
    case "session-state": {
      const incoming = action.payload.loaded_layers ?? [];
      // F22: dedupe by layer_id BEFORE sorting so a duplicate-id republish
      // can never render two rows with the same React key (the connected-
      // sliders bug).
      return { layers: sortTopFirst(dedupeByLayerId(incoming)) };
    }
    case "map-command": {
      const cmd = action.payload;
      switch (cmd.command) {
        case "load-layer": {
          // Replace or append by layer_id.
          const without = state.layers.filter(
            (l) => l.layer_id !== cmd.layer.layer_id,
          );
          const next = [...without, cmd.layer];
          return { layers: sortTopFirst(next) };
        }
        case "remove-layer": {
          return {
            layers: state.layers.filter((l) => l.layer_id !== cmd.layer_id),
          };
        }
        case "set-layer-visibility": {
          return {
            layers: state.layers.map((l) =>
              l.layer_id === cmd.layer_id ? { ...l, visible: cmd.visible } : l,
            ),
          };
        }
        case "set-layer-opacity": {
          return {
            layers: state.layers.map((l) =>
              l.layer_id === cmd.layer_id
                ? { ...l, opacity: clamp01(cmd.opacity) }
                : l,
            ),
          };
        }
        case "set-layer-order": {
          // Agent-provided ordering, top-of-stack first. Reassign z_index
          // monotonically so the local view matches the order verbatim.
          const idToLayer = new Map(state.layers.map((l) => [l.layer_id, l]));
          const next: ProjectLayerSummary[] = [];
          cmd.layer_ids.forEach((id, idx) => {
            const layer = idToLayer.get(id);
            if (layer) {
              next.push({ ...layer, z_index: cmd.layer_ids.length - idx });
            }
          });
          // Preserve layers not named in the command (defensive — agent should
          // always send a full list, but the client should not lose state).
          state.layers.forEach((l) => {
            if (!cmd.layer_ids.includes(l.layer_id)) next.push(l);
          });
          return { layers: next };
        }
      }
      // exhaustive: MapCommandPayload union is the 5 M3-active sub-
      // discriminants only (zoom-to / set-temporal-config / start-animation /
      // stop-animation / invalidate-tiles deferred to M4–M5 per kickoff §6).
      return state;
    }
    case "local-reorder": {
      const idToLayer = new Map(state.layers.map((l) => [l.layer_id, l]));
      const next: ProjectLayerSummary[] = [];
      action.layer_ids.forEach((id, idx) => {
        const layer = idToLayer.get(id);
        if (layer) {
          next.push({ ...layer, z_index: action.layer_ids.length - idx });
        }
      });
      return { layers: next };
    }
    case "local-visibility": {
      return {
        layers: state.layers.map((l) =>
          l.layer_id === action.layer_id ? { ...l, visible: action.visible } : l,
        ),
      };
    }
    case "local-opacity": {
      return {
        layers: state.layers.map((l) =>
          l.layer_id === action.layer_id
            ? { ...l, opacity: clamp01(action.opacity) }
            : l,
        ),
      };
    }
    default:
      return state;
  }
}

function clamp01(x: number): number {
  if (Number.isNaN(x)) return 0;
  if (x < 0) return 0;
  if (x > 1) return 1;
  return x;
}

// --- Kind chip (job-0264 polish) --------------------------------------- //
//
// A short, color-coded chip that classifies the layer at a glance — the
// kickoff names flood / plume / hillshade / vector as the canonical examples.
// Derivation is presentation-only (no new data flow): the kind is inferred
// from `style_preset` first (most specific), then `layer_type`. Unknown
// presets fall back to the raster/vector type so every row still gets a chip.
// The label is a single lowercase word; the color tints the chip background.

interface LayerKind {
  label: string;
  color: string; // chip text + border accent (background is a faint tint of it)
}

// Ordered substring rules over style_preset — first match wins.
const KIND_RULES: ReadonlyArray<readonly [RegExp, LayerKind]> = [
  [/flood|inundation|depth|nfhl|slr|surge/, { label: "flood", color: "#4aa3ff" }],
  [/plume|dispersion|smoke|ash|concentration/, { label: "plume", color: "#c084fc" }],
  [/hillshade/, { label: "hillshade", color: "#b9a06a" }],
  [/relief|slope|aspect|dem|elevation/, { label: "terrain", color: "#b9a06a" }],
  [/fire|burn|firms|mtbs|nifc/, { label: "fire", color: "#ff7a45" }],
  [/damage|pelicun|hazus|impact/, { label: "damage", color: "#ff5d6c" }],
  [/precip|rain|qpe|streamflow|discharge|nwm/, { label: "water", color: "#36c5d6" }],
  [/population|building|impervious|density|nsi/, { label: "exposure", color: "#f6c453" }],
  [/landcover|nlcd|fuel|landfire/, { label: "landcover", color: "#5fc27e" }],
  [/gbif|inaturalist|ebird|iucn|wdpa|movebank|species|habitat/, { label: "biodiversity", color: "#5fc27e" }],
  [/admin|boundaries|roads|osm|levee|dam/, { label: "vector", color: "#9aa7b8" }],
  [/alert|storm|weather|metar|asos|raws/, { label: "weather", color: "#36c5d6" }],
];

export function layerKind(layer: ProjectLayerSummary): LayerKind {
  const preset = (layer.style_preset ?? "").toLowerCase();
  if (preset) {
    for (const [rx, kind] of KIND_RULES) {
      if (rx.test(preset)) return kind;
    }
  }
  // Fallback to the broad geometry type so every row carries a chip.
  switch (layer.layer_type) {
    case "vector":
    case "geojson":
      return { label: "vector", color: "#9aa7b8" };
    case "wms":
    case "wmts":
      return { label: "tiles", color: "#9aa7b8" };
    case "raster":
    default:
      return { label: "raster", color: "#9aa7b8" };
  }
}

function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// --- Subscription wiring ----------------------------------------------- //
//
// The App layer wires `subscribeSessionState` and `subscribeMapCommand` to
// the WebSocket; this component subscribes on mount and unsubscribes on
// unmount. Stays decoupled from the GraceWs class so tests can inject a
// stub bus.

export type SessionStateSubscriber = (p: SessionStatePayload) => void;
export type MapCommandSubscriber = (p: MapCommandPayload) => void;

// ux-batch-1 J1 (F11) — the Layers panel is user-resizable: grab its right
// border and drag to size it (the panel is left-anchored at 16, so it grows
// rightward). Width persists to localStorage. Mirrors the chat-width model.
const LAYERS_WIDTH_DEFAULT_PX = 288;
const LAYERS_WIDTH_MIN_PX = 240;
const LAYERS_WIDTH_MAX_PX = 560;
const LS_LAYERS_WIDTH = "grace2.layersWidthPx";

/** Clamp a desired layers-panel width to [min, max]; non-finite → default. */
export function clampLayersWidth(px: number): number {
  if (!Number.isFinite(px)) return LAYERS_WIDTH_DEFAULT_PX;
  return Math.max(
    LAYERS_WIDTH_MIN_PX,
    Math.min(LAYERS_WIDTH_MAX_PX, Math.round(px)),
  );
}

/** Read the persisted layers-panel width (px); default ~288 on unset/garbage. */
export function readLayersWidth(): number {
  try {
    const raw = localStorage.getItem(LS_LAYERS_WIDTH);
    if (raw === null) return LAYERS_WIDTH_DEFAULT_PX;
    return clampLayersWidth(Number(raw));
  } catch {
    return LAYERS_WIDTH_DEFAULT_PX;
  }
}

/** Persist the layers-panel width (px). Non-fatal on failure. */
export function writeLayersWidth(px: number): void {
  try {
    localStorage.setItem(LS_LAYERS_WIDTH, String(clampLayersWidth(px)));
  } catch {
    /* non-fatal */
  }
}

export interface LayerPanelProps {
  initialLayers?: ProjectLayerSummary[];
  subscribeSessionState?: (cb: SessionStateSubscriber) => () => void;
  subscribeMapCommand?: (cb: MapCommandSubscriber) => () => void;
  /** Called whenever the layer list changes (used by App.tsx to drive LayerLegend). */
  onLayersChange?: (layers: ProjectLayerSummary[]) => void;
  /** Called when the user clicks the × close button (job-0068). */
  onClose?: () => void;
  /**
   * job-0258: outbound map-command emission for user layer-control intents
   * (set-layer-opacity / set-layer-visibility / set-layer-order). App.tsx
   * wires this to `bus.pushMapCommand`, which fans out to MapView (applies
   * to the MapLibre instance) AND back into this panel's own reducer (an
   * idempotent echo — the local dispatch below already applied the same
   * change, so the echo is a no-op re-set of identical values).
   */
  onMapCommand?: (cmd: MapCommandPayload) => void;
  /**
   * ux-batch-1 J1 (F11) — optional controlled width (px). When provided it
   * seeds/mirrors the internal width; when omitted the panel reads/persists its
   * own width via localStorage.
   */
  width?: number;
  /** Fired with the new px width when the user drags the right border. */
  onWidthChange?: (widthPx: number) => void;
  /**
   * ux-batch-1 J1 — mobile drawer mode: the panel fills the drawer column at
   * the fixed default width and renders no resize handle (drag-sizing is a
   * desktop affordance only). Default false (desktop, draggable).
   */
  mobile?: boolean;
}

export function LayerPanel({
  initialLayers,
  subscribeSessionState,
  subscribeMapCommand,
  onLayersChange,
  onClose,
  onMapCommand,
  width,
  onWidthChange,
  mobile = false,
}: LayerPanelProps): JSX.Element | null {
  const initial = useMemo<LayerPanelState>(
    () => ({ layers: sortTopFirst(dedupeByLayerId(initialLayers ?? [])) }),
    // intentionally only on mount; initialLayers is a seed, not a reactive source.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  const [state, dispatch] = useReducer(reducer, initial);

  // ux-batch-1 J1 (F11) — user-draggable panel width.
  const [panelWidth, setPanelWidth] = useState<number>(() =>
    width ?? readLayersWidth(),
  );
  useEffect(() => {
    if (typeof width === "number") setPanelWidth(clampLayersWidth(width));
  }, [width]);
  const panelWidthRef = useRef<number>(panelWidth);
  panelWidthRef.current = panelWidth;
  // The panel is left-anchored at 16, so width = pointerX - 16; clamped.
  const beginWidthDrag = useCallback(
    (e: React.PointerEvent): void => {
      e.preventDefault();
      const onMove = (ev: PointerEvent): void => {
        const next = clampLayersWidth(ev.clientX - 16);
        panelWidthRef.current = next;
        setPanelWidth(next);
        onWidthChange?.(next);
      };
      const onUp = (): void => {
        writeLayersWidth(panelWidthRef.current);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        document.body.style.userSelect = "";
        document.body.style.cursor = "";
      };
      document.body.style.userSelect = "none";
      document.body.style.cursor = "ew-resize";
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [onWidthChange],
  );
  const nudgeWidth = useCallback(
    (deltaPx: number): void => {
      setPanelWidth((prev) => {
        const next = clampLayersWidth(prev + deltaPx);
        panelWidthRef.current = next;
        writeLayersWidth(next);
        onWidthChange?.(next);
        return next;
      });
    },
    [onWidthChange],
  );

  useEffect(() => {
    const unsubs: Array<() => void> = [];
    if (subscribeSessionState) {
      unsubs.push(
        subscribeSessionState((p) => dispatch({ type: "session-state", payload: p })),
      );
    }
    if (subscribeMapCommand) {
      unsubs.push(
        subscribeMapCommand((p) => dispatch({ type: "map-command", payload: p })),
      );
    }
    return () => {
      unsubs.forEach((u) => u());
    };
  }, [subscribeSessionState, subscribeMapCommand]);

  // Notify parent of layer-list changes so App.tsx can drive LayerLegend.
  useEffect(() => {
    onLayersChange?.(state.layers);
  }, [state.layers, onLayersChange]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  function onDragEnd(event: DragEndEvent): void {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = state.layers.findIndex((l) => l.layer_id === active.id);
    const newIndex = state.layers.findIndex((l) => l.layer_id === over.id);
    if (oldIndex === -1 || newIndex === -1) return;
    const reorderedIds = arrayMove(state.layers, oldIndex, newIndex).map(
      (l) => l.layer_id,
    );
    dispatch({ type: "local-reorder", layer_ids: reorderedIds });
    // job-0258: emit the real map-command so MapView re-stacks the MapLibre
    // layers (moveLayer). `reorderedIds` is top-of-stack first — the
    // set-layer-order contract (contracts.ts SetLayerOrderCommand).
    onMapCommand?.({ command: "set-layer-order", layer_ids: reorderedIds });
    // eslint-disable-next-line no-console
    console.debug("[LayerPanel] reorder intent:", reorderedIds);
  }

  function onVisibilityToggle(layerId: string, visible: boolean): void {
    dispatch({ type: "local-visibility", layer_id: layerId, visible });
    // job-0258: emit so MapView flips layout visibility on the live map.
    onMapCommand?.({ command: "set-layer-visibility", layer_id: layerId, visible });
    // eslint-disable-next-line no-console
    console.debug("[LayerPanel] visibility intent:", { layerId, visible });
  }

  function onOpacityChange(layerId: string, opacity: number): void {
    const clamped = clamp01(opacity);
    dispatch({ type: "local-opacity", layer_id: layerId, opacity: clamped });
    // job-0258: emit so MapView updates the paint properties on the live map.
    onMapCommand?.({ command: "set-layer-opacity", layer_id: layerId, opacity: clamped });
    // eslint-disable-next-line no-console
    console.debug("[LayerPanel] opacity intent:", { layerId, opacity: clamped });
  }

  // Tweak 2 (job-0065): hide the panel entirely when no layers are loaded.
  // Hooks must all run before this conditional return.
  if (state.layers.length === 0) return null;

  return (
    <aside
      data-testid="grace2-layer-panel"
      style={{
        position: "absolute",
        left: 16,
        top: 16,
        bottom: 16,
        // Desktop: user-dragged width. Mobile drawer: fixed default (no drag).
        width: mobile ? LAYERS_WIDTH_DEFAULT_PX : clampLayersWidth(panelWidth),
        // Subtle gradient + hairline border + soft shadow for a sleeker,
        // more modern panel than the flat slab (job-0264 polish).
        background:
          "linear-gradient(180deg, rgba(26,27,33,0.96) 0%, rgba(18,19,24,0.96) 100%)",
        color: "#e8e8ec",
        borderRadius: 12,
        border: "1px solid rgba(255,255,255,0.06)",
        boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
        backdropFilter: "blur(6px)",
        WebkitBackdropFilter: "blur(6px)",
        display: "flex",
        flexDirection: "column",
        fontFamily:
          "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
        fontSize: 13,
        overflow: "hidden",
      }}
    >
      {/* ux-batch-1 J1 (F11) — right-border resize grab strip. The panel is
          left-anchored, so dragging this rightward widens it. role=separator +
          arrow-key nudge for keyboard a11y. Desktop only. */}
      {!mobile && (
      <div
        data-testid="grace2-layer-panel-resize-handle"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize layers panel (drag, or use arrow keys)"
        tabIndex={0}
        onPointerDown={beginWidthDrag}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") { e.preventDefault(); nudgeWidth(24); }
          else if (e.key === "ArrowLeft") { e.preventDefault(); nudgeWidth(-24); }
        }}
        style={{
          position: "absolute",
          right: 0,
          top: 0,
          bottom: 0,
          width: 6,
          cursor: "ew-resize",
          zIndex: 6,
          touchAction: "none",
        }}
      />
      )}
      <header
        style={{
          padding: "12px 14px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <strong
          style={{ fontSize: 13, letterSpacing: 0.3, fontWeight: 600 }}
        >
          Layers
        </strong>
        <span
          data-testid="grace2-layer-panel-count"
          style={{
            color: "#7d8794",
            fontSize: 11,
            background: "rgba(255,255,255,0.06)",
            borderRadius: 999,
            padding: "1px 8px",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {state.layers.length}
        </span>
        <span style={{ flex: 1 }} />
        {onClose && (
          <button
            data-testid="grace2-layer-panel-close"
            aria-label="Close layer panel"
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: "#8a929e",
              cursor: "pointer",
              fontSize: 18,
              lineHeight: 1,
              padding: "0 2px",
              display: "flex",
              alignItems: "center",
              transition: "color 120ms ease",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.color = "#e8e8ec")}
            onMouseLeave={(e) => (e.currentTarget.style.color = "#8a929e")}
          >
            ×
          </button>
        )}
      </header>
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: 8,
          display: "flex",
          flexDirection: "column",
          gap: 6,
        }}
      >
        {state.layers.length === 0 && (
          <p
            data-testid="grace2-layer-panel-empty"
            style={{
              color: "#6b7280",
              margin: "auto",
              fontSize: 12,
              fontStyle: "italic",
            }}
          >
            No layers yet
          </p>
        )}
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={onDragEnd}
        >
          <SortableContext
            items={state.layers.map((l) => l.layer_id)}
            strategy={verticalListSortingStrategy}
          >
            {state.layers.map((layer) => (
              <SortableRow
                key={layer.layer_id}
                layer={layer}
                onVisibilityToggle={onVisibilityToggle}
                onOpacityChange={onOpacityChange}
              />
            ))}
          </SortableContext>
        </DndContext>
      </div>
    </aside>
  );
}

// --- Sortable row ----------------------------------------------------- //

interface SortableRowProps {
  layer: ProjectLayerSummary;
  onVisibilityToggle: (layerId: string, visible: boolean) => void;
  onOpacityChange: (layerId: string, opacity: number) => void;
}

// Eye glyph — open (visible) / slashed (hidden). Inline SVG so it inherits
// currentColor and needs no asset. 14px to sit neatly inline.
function EyeIcon({ visible }: { visible: boolean }): JSX.Element {
  return (
    <svg
      viewBox="0 0 16 16"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ display: "block" }}
    >
      <path d="M1 8s2.5-4.5 7-4.5S15 8 15 8s-2.5 4.5-7 4.5S1 8 1 8z" />
      <circle cx="8" cy="8" r="2" />
      {!visible && <line x1="2.5" y1="2.5" x2="13.5" y2="13.5" />}
    </svg>
  );
}

function SortableRow({
  layer,
  onVisibilityToggle,
  onOpacityChange,
}: SortableRowProps): JSX.Element {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: layer.layer_id });
  // Hover (or active drag) reveals the compact opacity slider — keeps the
  // resting row clean while controls stay one gesture away. The row also
  // stays "expanded" while the pointer is over it.
  const [hovered, setHovered] = useState(false);
  const showOpacity = hovered || isDragging;
  const kind = layerKind(layer);
  const dimmed = !layer.visible;
  // ux-batch-1 J3 (F8) — a layer with undefined/NaN opacity left the range
  // input uncontrolled, so the browser parked the thumb at its default CENTRE
  // (0.5) while the label still read 0% (value↔position mismatch the user
  // reported). Resolve to a finite [0,1] value once, defaulting to fully
  // opaque, and feed it to BOTH the slider value and the % label so they
  // always agree. A real 0 (transparent) is preserved.
  const safeOpacity =
    typeof layer.opacity === "number" && Number.isFinite(layer.opacity)
      ? clamp01(layer.opacity)
      : 1;

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition: transition ?? "background 140ms ease, border-color 140ms ease",
    background: isDragging
      ? "rgba(70,110,170,0.28)"
      : hovered
        ? "rgba(255,255,255,0.06)"
        : "rgba(255,255,255,0.03)",
    border: `1px solid ${
      isDragging ? "rgba(120,160,220,0.5)" : "rgba(255,255,255,0.06)"
    }`,
    borderRadius: 8,
    padding: "7px 9px",
    display: "flex",
    flexDirection: "column",
    gap: showOpacity ? 7 : 0,
    opacity: isDragging ? 0.9 : 1,
    boxShadow: isDragging ? "0 6px 18px rgba(0,0,0,0.45)" : "none",
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      data-testid="layer-row"
      data-layer-id={layer.layer_id}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        <button
          aria-label={`drag handle for ${layer.name}`}
          {...attributes}
          {...listeners}
          title="Drag to reorder"
          style={{
            cursor: "grab",
            background: "transparent",
            border: "none",
            color: hovered ? "#8a929e" : "#5a626d",
            width: 16,
            height: 22,
            padding: 0,
            fontSize: 13,
            lineHeight: "22px",
            flexShrink: 0,
            transition: "color 120ms ease",
            touchAction: "none",
          }}
          data-testid="layer-drag-handle"
        >
          ⠿
        </button>
        {/* Eye toggle. The checkbox input is visually hidden (overlaid) so the
            existing data-testid + a11y contract are preserved while the
            user sees the polished eye glyph. */}
        <label
          style={{
            position: "relative",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 22,
            height: 22,
            flexShrink: 0,
            cursor: "pointer",
            color: layer.visible ? "#cfd4db" : "#5a626d",
            transition: "color 120ms ease",
          }}
          title={layer.visible ? "Hide layer" : "Show layer"}
        >
          <input
            type="checkbox"
            checked={layer.visible}
            onChange={(e) => onVisibilityToggle(layer.layer_id, e.target.checked)}
            aria-label={`visibility for ${layer.name}`}
            data-testid="layer-visibility"
            style={{
              position: "absolute",
              inset: 0,
              margin: 0,
              opacity: 0,
              cursor: "pointer",
            }}
          />
          <EyeIcon visible={layer.visible} />
        </label>
        <span
          style={{
            flex: 1,
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            fontSize: 12.5,
            color: dimmed ? "#8a929e" : "#e8e8ec",
            transition: "color 120ms ease",
          }}
          title={layer.name}
        >
          {layer.name}
        </span>
        <span
          data-testid="layer-kind-chip"
          data-kind={kind.label}
          title={layer.style_preset ?? kind.label}
          style={{
            flexShrink: 0,
            fontSize: 9.5,
            fontWeight: 600,
            letterSpacing: 0.3,
            textTransform: "uppercase",
            color: kind.color,
            background: hexToRgba(kind.color, 0.14),
            border: `1px solid ${hexToRgba(kind.color, 0.32)}`,
            borderRadius: 5,
            padding: "1px 6px",
            lineHeight: "15px",
          }}
        >
          {kind.label}
        </span>
      </div>
      {/* Opacity row: collapses to 0-height when not hovered for a clean
          resting state, expands smoothly on hover. Always mounted so the
          slider's data-testid + value are stable for tests + screen readers. */}
      <div
        data-testid="layer-opacity-row"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          overflow: "hidden",
          maxHeight: showOpacity ? 24 : 0,
          opacity: showOpacity ? 1 : 0,
          transition: "max-height 160ms ease, opacity 160ms ease",
        }}
      >
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={safeOpacity}
          onChange={(e) =>
            onOpacityChange(layer.layer_id, Number(e.target.value))
          }
          aria-label={`opacity for ${layer.name}`}
          data-testid="layer-opacity"
          style={{
            flex: 1,
            // 16px box so the native thumb (~14-16px) fits INSIDE the
            // element — at 4px it overflowed and the row's overflow:hidden
            // clipped the dot top+bottom (user-reported). The track itself
            // still renders thin; only the hit/box height grows.
            height: 16,
            accentColor: kind.color,
            cursor: "pointer",
          }}
        />
        <span
          style={{
            fontSize: 10,
            color: "#9aa1ab",
            width: 30,
            textAlign: "right",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {(safeOpacity * 100).toFixed(0)}%
        </span>
      </div>
    </div>
  );
}

// --- Test-injectable global bus ---------------------------------------- //
//
// The browser console uses this hook to inject a session-state envelope for
// local-dev verification:
//
//   window.__grace2InjectSessionState({ loaded_layers: [...] })
//
// This is debug-only; production builds remove it via the strip-comments
// path (vite minification preserves the function but the App.tsx attaches
// it only in dev — see App.tsx).

export interface LayerPanelBus {
  pushSessionState: (p: SessionStatePayload) => void;
  pushMapCommand: (p: MapCommandPayload) => void;
}

export function createLayerPanelBus(): LayerPanelBus & {
  subscribeSessionState: (cb: SessionStateSubscriber) => () => void;
  subscribeMapCommand: (cb: MapCommandSubscriber) => () => void;
} {
  const sessionSubs = new Set<SessionStateSubscriber>();
  const mapSubs = new Set<MapCommandSubscriber>();
  return {
    pushSessionState: (p) => sessionSubs.forEach((s) => s(p)),
    pushMapCommand: (p) => mapSubs.forEach((s) => s(p)),
    subscribeSessionState: (cb) => {
      sessionSubs.add(cb);
      return () => sessionSubs.delete(cb);
    },
    subscribeMapCommand: (cb) => {
      mapSubs.add(cb);
      return () => mapSubs.delete(cb);
    },
  };
}
