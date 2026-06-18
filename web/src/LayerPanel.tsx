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
import { ConfirmationDialog } from "./components/ConfirmationDialog";
import { SequenceScrubber } from "./components/SequenceScrubber";
import {
  IconClose,
  IconDelete,
  IconDragHandle,
  IconEye,
  IconEyeOff,
  IconArrowLeft,
  IconArrowRight,
  IconChevronDown,
  IconChevronRight,
  IconWaves,
} from "./components/icons";

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
      // F55 (job-0325): re-apply the user's persisted visibility overrides on
      // top of the server `visible` so a layer the user hid stays hidden across
      // a panel unmount->remount (mobile drawer collapse re-seeds from a fresh
      // session-state). No override => server value verbatim.
      return {
        layers: sortTopFirst(
          applyVisibilityOverrides(dedupeByLayerId(incoming)),
        ),
      };
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

// --- Sequential-layer grouping (NATE: enumerated temporal raster stacks) --- //
//
// NATE's ask: enumerated temporal raster sequences — e.g. 3 HRRR forecast hours
// "...F+01h" / "...F+03h" / "...F+06h" — should COLLAPSE into ONE "sequential
// group" row you can step through (LEFT/RIGHT + a bottom scrubber) instead of N
// near-identical rows. Stepping shows ONE frame at a time by toggling layer
// visibility through the EXISTING LayerPanel visibility callback (client-side
// only; no backend, no Map.tsx edits).
//
// Detection is deliberately CONSERVATIVE: we only form a group when there is a
// CLEAR monotonic series of >=2 layers that (a) share a common source/tool +
// AOI and (b) carry a parseable lead-time / step / index token whose values are
// strictly increasing. Everything else stays an ordinary row.

/** A parsed frame token: the numeric position + the verbatim label to show. */
interface FrameToken {
  /** Monotonic numeric position (e.g. lead hours, step index). */
  value: number;
  /** Short human label for the frame, e.g. "F+03h" / "t+2" / "step 4". */
  label: string;
  /** The common "stem" (name with the token stripped) — the grouping key. */
  stem: string;
}

// Ordered token patterns over a layer name. First match wins. Each captures a
// numeric position and yields a normalized short label + the stem (name minus
// the matched token) so layers in one series share a stem. Tokens are matched
// near the END of the name (where enumerations live) but anywhere is accepted.
const FRAME_PATTERNS: ReadonlyArray<{
  rx: RegExp;
  label: (m: RegExpMatchArray) => string;
}> = [
  // Forecast lead hour: "F+01h", "f+12h", "F+1 h", "+06h"
  { rx: /\bf?\+?\s*(\d{1,3})\s*h\b/i, label: (m) => `F+${pad2(m[1])}h` },
  // Hour token: "hour 3", "hr 06", "h12"
  { rx: /\bh(?:ou)?r?\s*\+?(\d{1,3})\b/i, label: (m) => `hr ${stripZeros(m[1])}` },
  // Step/frame/index: "step 4", "frame 02", "t+2", "t2", "#3"
  { rx: /\b(?:step|frame|idx|index)\s*\+?(\d{1,4})\b/i, label: (m) => `step ${stripZeros(m[1])}` },
  { rx: /\bt\s*\+\s*(\d{1,4})\b/i, label: (m) => `t+${stripZeros(m[1])}` },
  { rx: /#\s*(\d{1,4})\b/i, label: (m) => `#${stripZeros(m[1])}` },
  // Day token: "day 1", "d+3"
  { rx: /\bd(?:ay)?\s*\+?(\d{1,3})\b/i, label: (m) => `day ${stripZeros(m[1])}` },
];

function pad2(s: string | undefined): string {
  const n = Number(s ?? "0");
  return Number.isFinite(n) ? String(n).padStart(2, "0") : (s ?? "");
}
function stripZeros(s: string | undefined): string {
  const n = Number(s ?? "0");
  return Number.isFinite(n) ? String(n) : (s ?? "");
}

/**
 * Parse a frame token out of a layer name. Returns null when no monotonic
 * lead-time / step / index token is present. Exported for unit testing.
 */
export function parseFrameToken(name: string): FrameToken | null {
  if (!name) return null;
  for (const { rx, label } of FRAME_PATTERNS) {
    const m = name.match(rx);
    if (m && m[1] != null) {
      const value = Number(m[1]);
      if (!Number.isFinite(value)) continue;
      // Stem = the name with the matched token removed + whitespace collapsed.
      // Series members differ ONLY in the token, so they share a stem.
      const stem = name
        .slice(0, m.index)
        .concat(name.slice((m.index ?? 0) + m[0].length))
        .replace(/\s+/g, " ")
        .replace(/[\s,(\-–—]+$/g, "")
        .replace(/^[\s,(\-–—]+/g, "")
        .trim()
        .toLowerCase();
      return { value, label: label(m), stem };
    }
  }
  return null;
}

/** A detected sequential group: the ordered member layers + their frame labels. */
export interface SequentialGroup {
  /** Stable key for the group (shared stem + bbox signature). */
  key: string;
  /** Human label for the group, derived from the shared stem / first member. */
  label: string;
  /** Member layers in series order (ascending frame value). */
  layers: ProjectLayerSummary[];
  /** Per-member short frame labels, parallel to `layers`. */
  frameLabels: string[];
}

/** Round a bbox-ish signature so near-identical AOIs group together. */
function bboxSignature(layer: ProjectLayerSummary): string {
  // ProjectLayerSummary has no bbox field; the URI prefix (run/source dir) is
  // the best available AOI/source proxy — same run dir => same AOI + tool. We
  // strip the final path segment (the per-frame filename) so sibling frames in
  // one run share a signature.
  const uri = layer.uri ?? "";
  const lastSlash = uri.lastIndexOf("/");
  return lastSlash >= 0 ? uri.slice(0, lastSlash) : uri;
}

/** Titleize a lowercased stem for display ("hrrr forecast" → "Hrrr Forecast"). */
function titleizeStem(stem: string, fallback: string): string {
  const s = stem.trim();
  if (!s) return fallback;
  return s.replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Detect sequential groups among an ordered layer list. CONSERVATIVE: a group
 * forms only when >=2 layers share (stem + source/AOI signature + style_preset)
 * AND carry strictly-increasing, DISTINCT frame values. Members are returned in
 * ascending frame order. Layers that don't qualify are simply absent from the
 * result (the caller renders them as ordinary rows). Exported for unit testing.
 */
export function detectSequentialGroups(
  layers: ProjectLayerSummary[],
): SequentialGroup[] {
  const buckets = new Map<
    string,
    { token: FrameToken; layer: ProjectLayerSummary }[]
  >();
  for (const layer of layers) {
    const token = parseFrameToken(layer.name);
    if (!token) continue;
    // Group key: stem + source/AOI signature + preset. All three must match so
    // we never fuse two unrelated series that happen to share a token shape.
    const key = [
      token.stem,
      bboxSignature(layer),
      (layer.style_preset ?? layer.layer_type ?? "").toLowerCase(),
    ].join("§");
    const arr = buckets.get(key) ?? [];
    arr.push({ token, layer });
    buckets.set(key, arr);
  }

  const groups: SequentialGroup[] = [];
  for (const [key, members] of buckets) {
    if (members.length < 2) continue;
    // Sort by frame value ascending; require strictly-increasing DISTINCT values
    // (a clear monotonic series — reject duplicates / non-monotonic noise).
    const sorted = [...members].sort((a, b) => a.token.value - b.token.value);
    let monotonic = true;
    for (let i = 1; i < sorted.length; i++) {
      const cur = sorted[i];
      const prev = sorted[i - 1];
      if (!cur || !prev || cur.token.value <= prev.token.value) {
        monotonic = false;
        break;
      }
    }
    if (!monotonic) continue;
    const first = sorted[0];
    if (!first) continue;
    groups.push({
      key,
      label: titleizeStem(first.token.stem, first.layer.name),
      layers: sorted.map((m) => m.layer),
      frameLabels: sorted.map((m) => m.token.label),
    });
  }
  // Stable order: by the group's first member's z_index (top-of-stack first),
  // matching the rest of the panel's ordering.
  groups.sort(
    (a, b) => (b.layers[0]?.z_index ?? 0) - (a.layers[0]?.z_index ?? 0),
  );
  return groups;
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

// --- F55 (job-0325): per-layer visibility persistence ------------------- //
//
// Root cause being fixed: on MOBILE the LayerPanel lives inside a MobileDrawer
// that returns null when collapsed (MobileDrawer.tsx). Collapsing the drawer
// UNMOUNTS the panel, discarding the useReducer state (each layer's `visible`).
// Re-opening re-seeds from session-state where `visible` is the SERVER value
// (always true from add_loaded_layer), so a layer the user had hidden snapped
// back to visible. Desktop collapse also unmounts, so the fix must be
// unmount-proof rather than relying on component lifetime.
//
// Fix: persist the user's explicit visibility toggles to localStorage keyed by
// layer_id, and apply that override on top of the incoming server `visible`
// whenever we (re-)seed the reducer. The override is PURELY ADDITIVE — it only
// exists for a layer_id the user explicitly toggled. When no override exists
// the server value is used verbatim, so a never-toggled layer (the desktop
// resting case) renders byte-identically to before this change.
const LS_LAYER_VISIBILITY = "grace2.layerVisibility";

/** Read the full {layer_id: visible} override map; {} on unset/garbage. */
export function readLayerVisibilityOverrides(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(LS_LAYER_VISIBILITY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: Record<string, boolean> = {};
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof v === "boolean") out[k] = v;
    }
    return out;
  } catch {
    return {};
  }
}

/** Persist one layer's user-chosen visibility into the override map. Non-fatal. */
export function writeLayerVisibilityOverride(layerId: string, visible: boolean): void {
  try {
    const map = readLayerVisibilityOverrides();
    map[layerId] = visible;
    localStorage.setItem(LS_LAYER_VISIBILITY, JSON.stringify(map));
  } catch {
    /* non-fatal */
  }
}

/**
 * Apply persisted visibility overrides on top of a server-provided layer list.
 * For each layer: if the user explicitly toggled it before (an override key
 * exists), use the stored value; otherwise keep the server `visible` verbatim.
 * Pure (returns a new list) so it is safe to call inside a reducer / useMemo.
 * Exported for unit testing.
 */
export function applyVisibilityOverrides(
  layers: ProjectLayerSummary[],
  overrides: Record<string, boolean> = readLayerVisibilityOverrides(),
): ProjectLayerSummary[] {
  if (Object.keys(overrides).length === 0) return layers;
  return layers.map((l) => {
    // Under noUncheckedIndexedAccess `overrides[id]` is `boolean | undefined`;
    // the hasOwnProperty guard guarantees presence, so coerce to a strict
    // boolean for the `visible` field.
    const override = overrides[l.layer_id];
    return Object.prototype.hasOwnProperty.call(overrides, l.layer_id) &&
      typeof override === "boolean"
      ? { ...l, visible: override }
      : l;
  });
}

// --- F53 (job-0326): mobile swipe-right-to-delete gesture REMOVED ------- //
//
// An earlier iteration (job-0322) added a mobile swipe-RIGHT-to-delete gesture
// alongside the per-row trash control. NATE reversed that call: the swipe
// gesture is dropped ENTIRELY (swipeStartRef / swipeDx state, the touch/pointer
// swipe handlers, the visual swipe nudge, and the isHorizontalSwipeRight
// predicate are all gone). The EXPLICIT trash (delete) icon control on each row
// is now the sole delete affordance on BOTH desktop and mobile. It still opens
// the ConfirmationDialog (setPendingDeleteId path); only confirm deletes.

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
   * F53 (job-0325) — per-layer delete. Fired with the layer_id when the user
   * clicks a row's delete (trash) control. App.tsx wires this to
   * `wsRef.current.sendDeleteLayer(id)`, which emits the `layer-delete`
   * envelope; the server removes the layer from the session's loaded_layers,
   * persists authoritatively, and echoes a fresh session-state (which removes
   * the map overlay via replace-not-reconcile). Optional so existing callers
   * that haven't wired it yet don't break — without it the row still removes
   * itself optimistically via the local remove-layer dispatch below, but the
   * deletion would not survive a reload (no server round-trip).
   */
  onDeleteLayer?: (layerId: string) => void;
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
  onDeleteLayer,
  width,
  onWidthChange,
  mobile = false,
}: LayerPanelProps): JSX.Element | null {
  const initial = useMemo<LayerPanelState>(
    // F55 (job-0325): apply persisted visibility overrides at first mount too,
    // so a remount (mobile drawer reopen) that seeds via initialLayers — not
    // the bus — also restores the user's last visibility choice.
    () => ({
      layers: sortTopFirst(
        applyVisibilityOverrides(dedupeByLayerId(initialLayers ?? [])),
      ),
    }),
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
    // F55 (job-0325): persist the explicit choice so it survives a panel
    // unmount->remount (mobile drawer collapse). Reads back in
    // applyVisibilityOverrides at the next seed.
    writeLayerVisibilityOverride(layerId, visible);
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

  // F53 (job-0326): delete is gated behind a ConfirmationDialog ("Confirmation
  // before consequence" Memory invariant — matches CasesPanel's delete UX).
  // `pendingDeleteId` holds the layer awaiting confirmation; the per-row trash
  // button (the sole delete affordance, desktop + mobile) sets it to open the
  // dialog. The actual destructive path only runs on confirm.
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  // The layer object behind the pending delete (for the dialog's name in copy).
  const pendingDeleteLayer = useMemo(
    () => state.layers.find((l) => l.layer_id === pendingDeleteId) ?? null,
    [state.layers, pendingDeleteId],
  );

  /** Open the confirm dialog for a layer (trash button — desktop + mobile). */
  const requestDelete = useCallback((layerId: string): void => {
    setPendingDeleteId(layerId);
  }, []);

  /** Cancel a pending delete — clears the dialog, layer stays. */
  const cancelDelete = useCallback((): void => {
    setPendingDeleteId(null);
  }, []);

  /**
   * Run the (previously immediate) delete path AFTER the user confirms.
   *
   * F53 (job-0325): optimistic local removal so the row disappears instantly
   * without waiting for the server round-trip. The authoritative session-state
   * echo (sans the deleted layer) then confirms it; the local map-command also
   * tells MapView to drop the overlay immediately. Then send the server-
   * authoritative delete (persists + emits new session-state).
   */
  const confirmDelete = useCallback((): void => {
    const layerId = pendingDeleteId;
    setPendingDeleteId(null);
    if (!layerId) return;
    dispatch({ type: "map-command", payload: { command: "remove-layer", layer_id: layerId } });
    onMapCommand?.({ command: "remove-layer", layer_id: layerId });
    onDeleteLayer?.(layerId);
    // eslint-disable-next-line no-console
    console.debug("[LayerPanel] delete confirmed:", { layerId });
  }, [pendingDeleteId, onMapCommand, onDeleteLayer]);

  // --- Sequential-layer grouping (NATE) -------------------------------- //
  //
  // Detect enumerated temporal raster sequences among the active layers and
  // collapse each into ONE "sequential group" row + drive a bottom scrubber.
  // All stepping goes through the EXISTING visibility callback (show frame i,
  // hide the rest) — no Map.tsx edits, client-side only.
  const groups = useMemo(
    () => detectSequentialGroups(state.layers),
    [state.layers],
  );
  // Set of layer_ids that belong to SOME group (so ordinary-row render skips
  // them — they live in the group row instead).
  const groupedIds = useMemo(() => {
    const s = new Set<string>();
    for (const g of groups) for (const l of g.layers) s.add(l.layer_id);
    return s;
  }, [groups]);

  // Per-group active frame index, keyed by group.key. Defaults to the LAST
  // frame (the latest forecast hour reads as "current") on first sight.
  const [frameByGroup, setFrameByGroup] = useState<Record<string, number>>({});
  // Which groups are expanded (collapsible). Collapsed by default — the whole
  // point is to shrink N rows to one tidy row.
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  // Which group the bottom scrubber drives + whether it is auto-playing. The
  // scrubber follows the FIRST group that exists; a user can pin it by stepping.
  const [activeGroupKey, setActiveGroupKey] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);

  // Keep activeGroupKey valid as groups appear/disappear. Default to the first.
  useEffect(() => {
    if (groups.length === 0) {
      if (activeGroupKey !== null) setActiveGroupKey(null);
      if (playing) setPlaying(false);
      return;
    }
    const first = groups[0];
    if (first && (!activeGroupKey || !groups.some((g) => g.key === activeGroupKey))) {
      setActiveGroupKey(first.key);
    }
  }, [groups, activeGroupKey, playing]);

  /** The resolved active frame index for a group (default = last frame). */
  const frameIndexFor = useCallback(
    (g: SequentialGroup): number => {
      const raw = frameByGroup[g.key];
      const idx = typeof raw === "number" ? raw : g.layers.length - 1;
      return Math.max(0, Math.min(g.layers.length - 1, idx));
    },
    [frameByGroup],
  );

  /**
   * Step a group to frame `index`: show that member, hide every sibling. Drives
   * the SAME `onVisibilityToggle` the row checkbox uses, so the map, the panel
   * reducer, and the persisted override all stay consistent. No-ops on members
   * already in the desired visibility to avoid redundant emissions.
   */
  const stepGroupTo = useCallback(
    (g: SequentialGroup, index: number): void => {
      const clamped = Math.max(0, Math.min(g.layers.length - 1, index));
      setFrameByGroup((prev) => ({ ...prev, [g.key]: clamped }));
      g.layers.forEach((layer, i) => {
        const wantVisible = i === clamped;
        if (layer.visible !== wantVisible) {
          onVisibilityToggle(layer.layer_id, wantVisible);
        }
      });
    },
    // onVisibilityToggle is a stable closure over dispatch + props (not memoized
    // but defined once per render — listing it would re-create on every render;
    // it only reads refs/props so omitting it is safe and matches the existing
    // handler-call sites below).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  // When a group first becomes active (or its frame changes externally), make
  // sure exactly ONE frame is visible. Runs once per group key appearing — it
  // collapses a freshly-detected N-visible stack down to a single visible frame
  // so the map doesn't show all N overlays stacked. Guarded so it only fires
  // when the group is NOT already single-framed (>1 visible).
  const initializedGroupsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const g of groups) {
      if (initializedGroupsRef.current.has(g.key)) continue;
      initializedGroupsRef.current.add(g.key);
      const visibleCount = g.layers.filter((l) => l.visible).length;
      if (visibleCount !== 1) {
        stepGroupTo(g, frameIndexFor(g));
      }
    }
    // Drop keys for groups that no longer exist so re-formed groups re-init.
    const live = new Set(groups.map((g) => g.key));
    for (const k of Array.from(initializedGroupsRef.current)) {
      if (!live.has(k)) initializedGroupsRef.current.delete(k);
    }
  }, [groups, stepGroupTo, frameIndexFor]);

  const activeGroup = useMemo(
    () => groups.find((g) => g.key === activeGroupKey) ?? null,
    [groups, activeGroupKey],
  );

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
              lineHeight: 1,
              padding: "0 2px",
              display: "flex",
              alignItems: "center",
              transition: "color 120ms ease",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.color = "#e8e8ec")}
            onMouseLeave={(e) => (e.currentTarget.style.color = "#8a929e")}
          >
            <IconClose size={16} />
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
          {/* Only UNGROUPED layers are individually drag-sortable; a sequential
              group renders as one consolidated (non-sortable) row. */}
          <SortableContext
            items={state.layers
              .filter((l) => !groupedIds.has(l.layer_id))
              .map((l) => l.layer_id)}
            strategy={verticalListSortingStrategy}
          >
            {/* Sequential group rows, top-of-stack first. Each collapses N
                near-identical temporal frames into ONE row with LEFT/RIGHT
                stepping (drives the existing visibility callback). */}
            {groups.map((g) => (
              <SequentialGroupRow
                key={g.key}
                group={g}
                activeIndex={frameIndexFor(g)}
                expanded={!!expandedGroups[g.key]}
                isScrubberTarget={g.key === activeGroupKey}
                onToggleExpand={() =>
                  setExpandedGroups((prev) => ({
                    ...prev,
                    [g.key]: !prev[g.key],
                  }))
                }
                onStep={(idx) => {
                  setActiveGroupKey(g.key);
                  stepGroupTo(g, idx);
                }}
                onOpacityChange={onOpacityChange}
                onRequestDelete={requestDelete}
              />
            ))}
            {state.layers
              .filter((l) => !groupedIds.has(l.layer_id))
              .map((layer) => (
                <SortableRow
                  key={layer.layer_id}
                  layer={layer}
                  onVisibilityToggle={onVisibilityToggle}
                  onOpacityChange={onOpacityChange}
                  onRequestDelete={requestDelete}
                />
              ))}
          </SortableContext>
        </DndContext>
      </div>
      {/* Bottom-center SCRUBBER for the active sequential group. Rendered from
          within LayerPanel (not App/Map) so it shares the frame state; it pins
          itself to the viewport bottom-center and only appears when a group is
          active. Stepping it drives the SAME visibility toggling as the row. */}
      {activeGroup && (
        <SequenceScrubber
          label={activeGroup.label}
          frameLabels={activeGroup.frameLabels}
          activeIndex={frameIndexFor(activeGroup)}
          onStep={(idx) => stepGroupTo(activeGroup, idx)}
          playing={playing}
          onPlayToggle={() => setPlaying((p) => !p)}
        />
      )}
      {/* F53 (job-0326): confirm-before-delete. The per-row trash control (the
          sole delete affordance, desktop + mobile) opens this dialog; the
          destructive path runs only on confirm. The dialog itself portals to
          document.body (ConfirmationDialog) so it overlays full-screen above
          this absolutely-positioned, backdrop-filtered panel. Distinct testId
          so tests + screen readers don't collide with the Cases delete dialog. */}
      {pendingDeleteLayer && (
        <ConfirmationDialog
          testId="grace2-layer-delete-dialog"
          title="Delete layer?"
          message={`Remove "${pendingDeleteLayer.name}" from this case? This cannot be undone.`}
          confirmLabel="Delete"
          cancelLabel="Cancel"
          onConfirm={confirmDelete}
          onCancel={cancelDelete}
        />
      )}
    </aside>
  );
}

// --- Sortable row ----------------------------------------------------- //

interface SortableRowProps {
  layer: ProjectLayerSummary;
  onVisibilityToggle: (layerId: string, visible: boolean) => void;
  onOpacityChange: (layerId: string, opacity: number) => void;
  /** Open the delete-confirm dialog for this row (the trash control). */
  onRequestDelete: (layerId: string) => void;
}

function SortableRow({
  layer,
  onVisibilityToggle,
  onOpacityChange,
  onRequestDelete,
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

  // dnd-kit's transform drives the row's position during a vertical reorder
  // drag; null for a row at rest.
  const dndTransform = CSS.Transform.toString(transform) || undefined;

  const style: React.CSSProperties = {
    transform: dndTransform,
    transition:
      transition ?? "background 140ms ease, border-color 140ms ease, transform 160ms ease",
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
          <IconDragHandle size={14} />
        </button>
        {/* Eye toggle. The checkbox input is visually hidden (overlaid) so the
            existing data-testid + a11y contract are preserved while the
            user sees the shared Phosphor eye icon (IconEye / IconEyeOff). */}
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
          {layer.visible ? <IconEye size={15} /> : <IconEyeOff size={15} />}
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
        {/* F53 (job-0326): per-row delete control — the SOLE delete affordance
            on BOTH desktop and mobile (the mobile swipe gesture was dropped).
            Revealed on hover (like the opacity row) to keep the resting row
            clean. `onPointerDown` stopPropagation guards against the dnd-kit
            PointerSensor treating a delete press as the start of a drag. Clicking
            OPENS the confirm dialog (onRequestDelete); only confirm deletes. */}
        <button
          aria-label={`delete layer ${layer.name}`}
          title="Delete layer"
          data-testid="layer-delete"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            onRequestDelete(layer.layer_id);
          }}
          style={{
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 22,
            height: 22,
            padding: 0,
            background: "transparent",
            border: "none",
            borderRadius: 5,
            color: hovered ? "#a8616b" : "transparent",
            cursor: "pointer",
            transition: "color 120ms ease, background 120ms ease",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = "#ff5d6c";
            e.currentTarget.style.background = "rgba(255,93,108,0.12)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = hovered ? "#a8616b" : "transparent";
            e.currentTarget.style.background = "transparent";
          }}
        >
          <IconDelete size={14} />
        </button>
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

// --- Sequential group row --------------------------------------------- //
//
// One consolidated row standing in for N enumerated temporal frames (e.g. the
// 3 HRRR forecast hours). Shows the active frame's label + position, LEFT/RIGHT
// step arrows, and a frame-count chip. Collapsible: expand to reveal each
// member frame as a compact sub-row (visibility/opacity/delete still work on
// the individual frames via the same callbacks). Stepping toggles visibility
// through the existing onVisibilityToggle (show frame i, hide the rest).

interface SequentialGroupRowProps {
  group: SequentialGroup;
  activeIndex: number;
  expanded: boolean;
  isScrubberTarget: boolean;
  onToggleExpand: () => void;
  onStep: (index: number) => void;
  onOpacityChange: (layerId: string, opacity: number) => void;
  onRequestDelete: (layerId: string) => void;
}

function SequentialGroupRow({
  group,
  activeIndex,
  expanded,
  isScrubberTarget,
  onToggleExpand,
  onStep,
  onOpacityChange,
  onRequestDelete,
}: SequentialGroupRowProps): JSX.Element {
  const n = group.layers.length;
  const idx = Math.max(0, Math.min(n - 1, activeIndex));
  const frameLabel = group.frameLabels[idx] ?? "";
  // The active member's kind drives the chip accent (same family as the rows).
  // Falls back to the first member then a synthetic raster so the row never
  // crashes if `idx` momentarily outruns the (always >=2) member list.
  const activeLayer = group.layers[idx] ?? group.layers[0];
  const kind = activeLayer
    ? layerKind(activeLayer)
    : { label: "raster", color: "#9aa7b8" };
  // Per-group opacity readout (drives every member together). Resolve to a
  // finite [0,1] once — same defaulting rule as the per-row slider.
  const groupOpacity =
    activeLayer &&
    typeof activeLayer.opacity === "number" &&
    Number.isFinite(activeLayer.opacity)
      ? clamp01(activeLayer.opacity)
      : 1;

  return (
    <div
      data-testid="layer-group-row"
      data-group-key={group.key}
      data-frame-count={n}
      data-active-index={idx}
      style={{
        background: isScrubberTarget
          ? "rgba(74,163,255,0.10)"
          : "rgba(255,255,255,0.03)",
        border: `1px solid ${
          isScrubberTarget ? "rgba(74,163,255,0.35)" : "rgba(255,255,255,0.08)"
        }`,
        borderRadius: 8,
        padding: "7px 9px",
        display: "flex",
        flexDirection: "column",
        gap: expanded ? 6 : 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        {/* Collapse/expand chevron. */}
        <button
          type="button"
          data-testid="layer-group-expand"
          aria-label={expanded ? "Collapse sequence" : "Expand sequence"}
          aria-expanded={expanded}
          title={expanded ? "Collapse sequence" : "Expand sequence"}
          onClick={onToggleExpand}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 18,
            height: 22,
            flexShrink: 0,
            padding: 0,
            background: "transparent",
            border: "none",
            color: "#8a929e",
            cursor: "pointer",
          }}
        >
          {expanded ? <IconChevronDown size={13} /> : <IconChevronRight size={13} />}
        </button>
        {/* Sequence glyph — signals this row is a temporal stack. */}
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            color: kind.color,
            flexShrink: 0,
          }}
          title="Sequential layer group"
        >
          <IconWaves size={15} />
        </span>
        {/* Group label + active frame readout. */}
        <span
          style={{
            flex: 1,
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            fontSize: 12.5,
            color: "#e8e8ec",
          }}
          title={group.label}
        >
          {group.label}{" "}
          <span
            data-testid="layer-group-frame-label"
            style={{ color: "#9aa1ab", fontVariantNumeric: "tabular-nums" }}
          >
            {frameLabel} ({idx + 1}/{n})
          </span>
        </span>
        {/* Frame-count chip. */}
        <span
          data-testid="layer-group-count-chip"
          title={`${n} frames`}
          style={{
            flexShrink: 0,
            fontSize: 9.5,
            fontWeight: 600,
            letterSpacing: 0.3,
            color: kind.color,
            background: hexToRgba(kind.color, 0.14),
            border: `1px solid ${hexToRgba(kind.color, 0.32)}`,
            borderRadius: 5,
            padding: "1px 6px",
            lineHeight: "15px",
          }}
        >
          {n}f
        </span>
        {/* LEFT / RIGHT step arrows. Wrap at the ends so the series loops. */}
        <button
          type="button"
          data-testid="layer-group-prev"
          aria-label="Previous frame"
          title="Previous frame"
          onClick={() => onStep((idx - 1 + n) % n)}
          style={groupArrowStyle}
        >
          <IconArrowLeft size={13} />
        </button>
        <button
          type="button"
          data-testid="layer-group-next"
          aria-label="Next frame"
          title="Next frame"
          onClick={() => onStep((idx + 1) % n)}
          style={groupArrowStyle}
        >
          <IconArrowRight size={13} />
        </button>
      </div>
      {/* Expanded: each member frame as a compact sub-row. The radio-like dot
          shows + selects the active frame; the trash deletes that one frame. */}
      {expanded && (
        <div
          data-testid="layer-group-frames"
          style={{ display: "flex", flexDirection: "column", gap: 4, paddingLeft: 25 }}
        >
          {group.layers.map((layer, i) => (
            <div
              key={layer.layer_id}
              data-testid="layer-group-frame"
              data-layer-id={layer.layer_id}
              data-active={i === idx}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 7,
                fontSize: 11.5,
                color: i === idx ? "#e8e8ec" : "#8a929e",
              }}
            >
              <button
                type="button"
                data-testid="layer-group-frame-select"
                aria-label={`show frame ${group.frameLabels[i]}`}
                aria-pressed={i === idx}
                onClick={() => onStep(i)}
                style={{
                  width: 14,
                  height: 14,
                  flexShrink: 0,
                  borderRadius: "50%",
                  padding: 0,
                  cursor: "pointer",
                  background: i === idx ? kind.color : "transparent",
                  border: `1px solid ${i === idx ? kind.color : "rgba(255,255,255,0.25)"}`,
                }}
              />
              <span
                style={{
                  flex: 1,
                  minWidth: 0,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  fontVariantNumeric: "tabular-nums",
                }}
                title={layer.name}
              >
                {group.frameLabels[i]}
              </span>
              <button
                type="button"
                data-testid="layer-group-frame-delete"
                aria-label={`delete frame ${group.frameLabels[i]}`}
                title="Delete this frame"
                onClick={() => onRequestDelete(layer.layer_id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: 20,
                  height: 20,
                  flexShrink: 0,
                  padding: 0,
                  background: "transparent",
                  border: "none",
                  color: "#7d8794",
                  cursor: "pointer",
                }}
              >
                <IconDelete size={12} />
              </button>
            </div>
          ))}
          {/* Per-group opacity — drives ALL frames together so the sequence
              reads at one transparency as you scrub. Applies to every member. */}
          <div
            data-testid="layer-group-opacity-row"
            style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 2 }}
          >
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={groupOpacity}
              onChange={(e) => {
                const v = Number(e.target.value);
                group.layers.forEach((l) => onOpacityChange(l.layer_id, v));
              }}
              aria-label={`opacity for ${group.label} sequence`}
              data-testid="layer-group-opacity"
              style={{ flex: 1, height: 16, accentColor: kind.color, cursor: "pointer" }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

const groupArrowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: 22,
  height: 22,
  flexShrink: 0,
  padding: 0,
  background: "rgba(255,255,255,0.06)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 6,
  color: "#cfd4db",
  cursor: "pointer",
};

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
