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

import { useEffect, useMemo, useReducer } from "react";
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

function reducer(state: LayerPanelState, action: LayerPanelAction): LayerPanelState {
  switch (action.type) {
    case "session-state": {
      const incoming = action.payload.loaded_layers ?? [];
      return { layers: sortTopFirst(incoming) };
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

// --- Subscription wiring ----------------------------------------------- //
//
// The App layer wires `subscribeSessionState` and `subscribeMapCommand` to
// the WebSocket; this component subscribes on mount and unsubscribes on
// unmount. Stays decoupled from the GraceWs class so tests can inject a
// stub bus.

export type SessionStateSubscriber = (p: SessionStatePayload) => void;
export type MapCommandSubscriber = (p: MapCommandPayload) => void;

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
}

export function LayerPanel({
  initialLayers,
  subscribeSessionState,
  subscribeMapCommand,
  onLayersChange,
  onClose,
  onMapCommand,
}: LayerPanelProps): JSX.Element | null {
  const initial = useMemo<LayerPanelState>(
    () => ({ layers: sortTopFirst(initialLayers ?? []) }),
    // intentionally only on mount; initialLayers is a seed, not a reactive source.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  const [state, dispatch] = useReducer(reducer, initial);

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
        width: 280,
        background: "rgba(20,20,25,0.92)",
        color: "#eee",
        borderRadius: 8,
        boxShadow: "0 4px 24px rgba(0,0,0,0.4)",
        display: "flex",
        flexDirection: "column",
        fontFamily: "system-ui, sans-serif",
        fontSize: 13,
        overflow: "hidden",
      }}
    >
      <header
        style={{
          padding: "10px 12px",
          borderBottom: "1px solid #333",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <strong style={{ fontSize: 14 }}>Layers</strong>
        <span style={{ color: "#888", fontSize: 11 }}>
          {state.layers.length} loaded
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
              color: "#888",
              cursor: "pointer",
              fontSize: 16,
              lineHeight: 1,
              padding: "0 2px",
              display: "flex",
              alignItems: "center",
            }}
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
          <p style={{ color: "#888", margin: 8 }}>
            No layers loaded. Ask the agent to load one.
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

function SortableRow({
  layer,
  onVisibilityToggle,
  onOpacityChange,
}: SortableRowProps): JSX.Element {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: layer.layer_id });
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    background: isDragging ? "#2a3b55" : "#222",
    border: "1px solid #333",
    borderRadius: 6,
    padding: 8,
    display: "flex",
    flexDirection: "column",
    gap: 6,
    opacity: isDragging ? 0.85 : 1,
  };
  return (
    <div
      ref={setNodeRef}
      style={style}
      data-testid="layer-row"
      data-layer-id={layer.layer_id}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <button
          aria-label={`drag handle for ${layer.name}`}
          {...attributes}
          {...listeners}
          style={{
            cursor: "grab",
            background: "transparent",
            border: "1px solid #444",
            borderRadius: 4,
            color: "#aaa",
            width: 22,
            height: 22,
            padding: 0,
            lineHeight: "20px",
            fontSize: 14,
          }}
          data-testid="layer-drag-handle"
        >
          ⠿
        </button>
        <input
          type="checkbox"
          checked={layer.visible}
          onChange={(e) => onVisibilityToggle(layer.layer_id, e.target.checked)}
          aria-label={`visibility for ${layer.name}`}
          data-testid="layer-visibility"
        />
        <span
          style={{
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={layer.name}
        >
          {layer.name}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ fontSize: 10, color: "#888", width: 38 }}>opacity</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={layer.opacity}
          onChange={(e) =>
            onOpacityChange(layer.layer_id, Number(e.target.value))
          }
          aria-label={`opacity for ${layer.name}`}
          data-testid="layer-opacity"
          style={{ flex: 1 }}
        />
        <span
          style={{ fontSize: 10, color: "#aaa", width: 32, textAlign: "right" }}
        >
          {(layer.opacity * 100).toFixed(0)}%
        </span>
      </div>
      {layer.attribution && (
        <div
          style={{
            fontSize: 10,
            color: "#888",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
          title={layer.attribution}
        >
          {layer.attribution}
        </div>
      )}
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
