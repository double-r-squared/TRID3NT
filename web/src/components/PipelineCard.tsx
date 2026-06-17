// GRACE-2 web — PipelineCard (FR-WC-8; Invariant 8; job-0162 visual redesign).
//
// One card per tool dispatch rendered inline in the chat stream. The card
// transitions through lifecycle states via background tint + animated text
// rather than icons / borderline accents. Memory spec
// `feedback_pipeline_card_visual_states` (2026-06-08):
//
//   pending  → grey-subdued background, greyed text, no right-side indicator
//   running  → normal background, rainbow-gradient animated text + spinner
//   success  → full green-tinted background, normal text, no indicator
//   failure  → full red-tinted background, normal text, no indicator
//   cancelled→ full yellow-tinted background (Invariant 8 keeps it distinct
//              from failed; treated as a terminal non-success on success/fail
//              axis — visually closer to failure but with a yellow tint)
//
// Dropped elements (do NOT reintroduce): blue left-edge accent, checkmark on
// success, "..." running indicator, "completed/running/pending" text labels,
// borderlines between stacked steps. Vertical separation is provided by the
// parent stack's 12-16px gap + each card's own drop shadow + rounded corners.
//
// Accessibility:
//   - `aria-live="polite"` on the card so terminal transitions are announced
//   - Visually-hidden text prefix encodes state for screen readers
//   - `prefers-reduced-motion` falls the rainbow gradient + spinner back to a
//     static neutral colour and a static dot respectively
//
// This component receives a plain PipelineStepSummary prop (no subscription
// logic here). The caller (Chat.tsx) owns the replace-not-reconcile semantics
// + the merge-by-step_id dedupe and passes the current snapshot of each step.

import { useEffect, useRef, useState } from "react";
import { PipelineStepSummary, PipelineStepState } from "../contracts";

// --- Duration formatting + live ticker (job-0264) ------------------------ //
//
// ELEVATED tool-timer requirement (feedback_pipeline_card_humanized_labels):
//   (a) running cards show a live (mm:ss) ticker next to the spinner so the
//       user can see how long a tool has been running;
//   (b) completed / failed / cancelled cards show the AUTHORITATIVE duration
//       the agent stamped (`step.duration_ms`), so the displayed number is
//       deterministic — the client ticker is purely cosmetic between
//       envelopes.
//
// The "m:ss" format matches the memory spec's label table (e.g. "2:34").
// Hours roll into the minutes field (e.g. 75min → "75:00") — solver runs can
// exceed an hour and a leading-hours field would clutter the inline card.

/** Format whole milliseconds as "m:ss" (minutes uncapped, seconds 00-59). */
export function formatDuration(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

// Terminal states carry the authoritative duration; running shows a ticker.
const TERMINAL_STATES: ReadonlySet<PipelineStepState> = new Set([
  "complete",
  "failed",
  "cancelled",
]);

// --- Minimum running-state dwell (F70) ----------------------------------- //
//
// Fast-failing tools (and fast-succeeding ones) used to skip the running /
// rainbow treatment entirely: a tool that errored in ~0s would emit
// pending→failed (or running→failed in adjacent frames) and the card jumped
// straight to the red terminal tint — the user "never saw it run", it "just
// went straight to failing". That hid the fact that a tool actually executed.
//
// Fix: once a card has entered (or is about to skip past) the running phase,
// hold the RUNNING visual treatment for at least MIN_RUNNING_DWELL_MS before
// letting the card settle into its terminal (success/failure/cancelled) state.
// The *logical* state (data-state, the authoritative timer, screen-reader
// announcement) tracks `step.state` faithfully — only the VISUAL settle is
// deferred — so nothing is fabricated and the failure-terminates-animation
// behaviour still fires the moment the dwell elapses.
//
// 450ms is long enough that the rainbow gradient / spinner is unmistakably
// perceived (>~300ms perceptual threshold for "I saw a thing happen") yet
// short enough that it never feels like an artificial stall on a real error.
export const MIN_RUNNING_DWELL_MS = 450;

/**
 * The live elapsed-ms for a *running* step, ticking once per second.
 *
 * Anchor preference:
 *   1. ``step.started_at`` (server truth) — survives remounts / reconnects so
 *      the ticker reflects real elapsed time, not time-since-this-mount.
 *   2. a local mount timestamp fallback when ``started_at`` is absent (older
 *      agents, or the pending→running frame raced ahead of the stamp).
 *
 * Returns 0 and does not tick for non-running steps (the caller renders the
 * authoritative ``duration_ms`` instead). SSR-safe: ``Date.now`` only.
 *
 * Exported (job-0280) so the mobile collapsed-sheet active-tool strip shows
 * the SAME elapsed value as the card — one timer implementation, no fork.
 */
export function useRunningElapsedMs(step: PipelineStepSummary): number {
  const isRunning = step.state === "running";
  // Resolve the anchor (epoch ms) once per running span. started_at is an
  // ISO-8601 string with a literal Z; Date.parse handles it. NaN (unparseable)
  // falls back to the local mount time.
  const anchorRef = useRef<number | null>(null);
  if (isRunning && anchorRef.current === null) {
    const parsed = step.started_at ? Date.parse(step.started_at) : NaN;
    anchorRef.current = Number.isNaN(parsed) ? Date.now() : parsed;
  }
  if (!isRunning) {
    // Reset so a future re-run (same component instance) re-anchors cleanly.
    anchorRef.current = null;
  }

  const [elapsed, setElapsed] = useState<number>(() =>
    isRunning && anchorRef.current !== null
      ? Math.max(0, Date.now() - anchorRef.current)
      : 0,
  );

  useEffect(() => {
    if (!isRunning) {
      setElapsed(0);
      return;
    }
    const anchor = anchorRef.current ?? Date.now();
    // Tick immediately so the first paint isn't a stale 0, then every second.
    const tick = (): void => setElapsed(Math.max(0, Date.now() - anchor));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
    // started_at change re-arms the interval against the new anchor.
  }, [isRunning, step.started_at]);

  return isRunning ? elapsed : 0;
}

// --- Reduced-motion detection (SSR-safe) --------------------------------- //
// Exported (job-0280) for the collapsed-sheet strip's spinner fallback.

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

// --- Display-state dwell (F70) ------------------------------------------- //
//
// Maps the authoritative `step.state` to the state actually PAINTED, enforcing
// the minimum running dwell described above. The card component instance
// persists across a step's lifecycle (Chat.tsx keys cards by step_id), so this
// hook reliably observes the pending→running→terminal progression on a single
// instance.
//
// Behaviour:
//   - pending / running           → painted verbatim (no defer).
//   - terminal (complete/failed/   → if the running treatment has not yet been
//     cancelled)                     visible for MIN_RUNNING_DWELL_MS, paint
//                                     `running` until the remaining dwell
//                                     elapses, THEN flip to the terminal state.
//
// The dwell clock starts when the card first leaves `pending` (whether it
// passes through an explicit `running` snapshot or jumps straight to a terminal
// one). A tool that fast-fails from pending therefore still flashes the running
// treatment for the full dwell before turning red.
//
// SSR-safe (Date.now / window.setTimeout only). If a card mounts already in a
// terminal state (e.g. history replay, or a completed step rehydrated on
// reconnect), there is nothing to "show running" for retroactively, so it
// paints terminal immediately — the dwell only guards the LIVE transition.

export function useDisplayState(state: PipelineStepState): PipelineStepState {
  // When the running treatment first became (or would have become) visible.
  // null until the card leaves pending; set once and never moved earlier.
  const runningSinceRef = useRef<number | null>(null);
  // True only if this instance was born terminal — then we never feign running.
  const mountedTerminalRef = useRef<boolean | null>(null);
  if (mountedTerminalRef.current === null) {
    mountedTerminalRef.current = TERMINAL_STATES.has(state);
  }

  const [displayState, setDisplayState] = useState<PipelineStepState>(state);

  useEffect(() => {
    // Record the moment we first see any non-pending state — that's when the
    // running treatment starts (or would have started for a skip-straight fail).
    if (state !== "pending" && runningSinceRef.current === null) {
      runningSinceRef.current = Date.now();
    }

    // Non-terminal states paint verbatim, immediately.
    if (!TERMINAL_STATES.has(state)) {
      setDisplayState(state);
      return;
    }

    // Born terminal (history / rehydrate): nothing ran live to show — settle now.
    if (mountedTerminalRef.current) {
      setDisplayState(state);
      return;
    }

    const runningSince = runningSinceRef.current ?? Date.now();
    const elapsed = Date.now() - runningSince;
    const remaining = MIN_RUNNING_DWELL_MS - elapsed;

    if (remaining <= 0) {
      // Running treatment was already visible long enough — settle immediately.
      setDisplayState(state);
      return;
    }

    // Hold the running treatment for the remaining dwell, THEN flip to terminal.
    // This is what makes the rainbow / spinner perceivable on a fast-fail and
    // is the moment the failure-terminates-animation behaviour fires.
    setDisplayState("running");
    const id = window.setTimeout(() => setDisplayState(state), remaining);
    return () => window.clearTimeout(id);
  }, [state]);

  return displayState;
}

// --- State → visual mapping ---------------------------------------------- //
//
// Background tints are layered over the chat panel's (20,20,25,0.92) so the
// tint reads as a state cue without overwhelming the chat. The pending tint
// is a slight darken; the running state restores normal panel bg; success
// and failure carry a more saturated overlay.

interface CardVisual {
  background: string;
  textColor: string;
  // Screen-reader-only state name; rendered inside a visually-hidden span.
  ariaPrefix: string;
}

function cardVisual(state: PipelineStepState): CardVisual {
  switch (state) {
    case "pending":
      return {
        background: "rgba(255,255,255,0.04)",
        textColor: "#777",
        ariaPrefix: "pending: ",
      };
    case "running":
      return {
        background: "rgba(255,255,255,0.08)",
        textColor: "#eee",
        ariaPrefix: "running: ",
      };
    case "complete":
      return {
        background: "rgba(40, 200, 100, 0.18)",
        textColor: "#eee",
        ariaPrefix: "completed: ",
      };
    case "failed":
      return {
        background: "rgba(220, 60, 60, 0.22)",
        textColor: "#eee",
        ariaPrefix: "failed: ",
      };
    case "cancelled":
      return {
        background: "rgba(220, 180, 40, 0.22)",
        textColor: "#eee",
        ariaPrefix: "cancelled: ",
      };
  }
}

// --- Spinner ------------------------------------------------------------- //
//
// 14px circular spinner. Pure SVG so it inherits color via `currentColor` and
// no PNG/font dependency. Animation is a 1s linear rotation; falls back to a
// static dot under `prefers-reduced-motion`.
// Exported (job-0280) so the collapsed-sheet strip reuses the exact spinner.

export function Spinner({ reduced }: { reduced: boolean }): JSX.Element {
  if (reduced) {
    return (
      <span
        data-testid="pipeline-card-indicator"
        data-variant="static-dot"
        style={{
          width: 8,
          height: 8,
          borderRadius: 4,
          background: "#bbb",
          display: "inline-block",
          flexShrink: 0,
        }}
      />
    );
  }
  return (
    <span
      data-testid="pipeline-card-indicator"
      data-variant="spinner"
      style={{
        width: 14,
        height: 14,
        display: "inline-block",
        flexShrink: 0,
        animation: "grace2-spin 1s linear infinite",
        transformOrigin: "50% 50%",
      }}
      aria-hidden="true"
    >
      <svg
        viewBox="0 0 14 14"
        width="14"
        height="14"
        style={{ display: "block" }}
      >
        <circle
          cx="7"
          cy="7"
          r="5.5"
          stroke="rgba(255,255,255,0.18)"
          strokeWidth="1.5"
          fill="none"
        />
        <path
          d="M 7 1.5 A 5.5 5.5 0 0 1 12.5 7"
          stroke="#eee"
          strokeWidth="1.5"
          fill="none"
          strokeLinecap="round"
        />
      </svg>
    </span>
  );
}

// --- Humanized step label ------------------------------------------------ //
//
// Memory spec `feedback_pipeline_card_humanized_labels` + job-0294: every tool
// dispatch the agent emits (the verbatim `step.name`) gets a PLAIN-LANGUAGE
// label so the chat never shows raw snake_case (`fetch_dem`,
// `compute_hillshade`, …). "No internal terms in user-facing surfaces"
// (codified web-lesson #3 from job-0086 et al.).
//
// Labels are STATE-AWARE: a present-tense RUNNING form ("Fetching DEM…") and a
// terminal COMPLETE form ("Loaded DEM"). Pending uses the running form (the
// user reads "about to fetch"); failed / cancelled also use the running form
// (the verb describes the attempted action — the red/yellow tint + the error
// chip already carry the outcome, so "Modeling flood [SFINCS]" reads better
// than "Flood modeled" on a card that visibly failed).
//
// `state` is OPTIONAL: omitting it (or passing a non-complete state) yields the
// running/active phrasing, which keeps the single-arg call shape working for
// any caller that doesn't thread state. Unmapped tools fall back to a graceful
// Title-Case rendering of the raw name ("fetch_x" → "Fetch X"), NEVER the raw
// snake_case, and a trailing "…" while active.

interface HumanizedLabel {
  /** Present-tense, shown while pending / running / failed / cancelled. */
  running: string;
  /** Terminal phrasing, shown on a completed step. */
  complete: string;
}

// Keyed on the verbatim emitted `step.name` (tool registry names + the
// synthetic `llm_generation` reasoning step + the `run_model_*` / `run_solver`
// / `wait_for_completion` engine step names). Covers the full live tool set.
const HUMANIZED_STEP_NAMES: Record<string, HumanizedLabel> = {
  // Reasoning step (synthetic, not a registered tool).
  llm_generation: { running: "Thinking…", complete: "Thought through it" },

  // --- Geocoding / boundaries ------------------------------------------- //
  geocode_location: { running: "Locating place…", complete: "Located place" },
  fetch_administrative_boundaries: {
    running: "Fetching admin boundaries…",
    complete: "Loaded admin boundaries",
  },

  // --- Terrain / elevation ---------------------------------------------- //
  fetch_dem: { running: "Fetching DEM…", complete: "Loaded DEM" },
  fetch_3dep_extra: {
    running: "Fetching 3DEP elevation…",
    complete: "Loaded 3DEP elevation",
  },
  compute_hillshade: { running: "Computing hillshade…", complete: "Hillshade ready" },
  compute_slope: { running: "Computing slope…", complete: "Slope ready" },
  compute_aspect: { running: "Computing aspect…", complete: "Aspect ready" },
  compute_colored_relief: {
    running: "Computing colored relief…",
    complete: "Colored relief ready",
  },

  // --- Land cover / surfaces -------------------------------------------- //
  fetch_landcover: { running: "Fetching land cover…", complete: "Loaded land cover" },
  extract_landcover_class: {
    running: "Extracting land-cover class…",
    complete: "Land-cover class extracted",
  },
  compute_impervious_surface: {
    running: "Computing impervious surface…",
    complete: "Impervious surface ready",
  },
  fetch_landfire_fuels: { running: "Fetching LANDFIRE fuels…", complete: "Loaded LANDFIRE fuels" },
  fetch_usfs_canopy_fuels: { running: "Fetching canopy fuels…", complete: "Loaded canopy fuels" },

  // --- Population / buildings / infrastructure -------------------------- //
  fetch_population: { running: "Fetching population…", complete: "Loaded population" },
  fetch_hrsl_population: {
    running: "Fetching HRSL population…",
    complete: "Loaded HRSL population",
  },
  fetch_buildings: { running: "Fetching buildings…", complete: "Loaded buildings" },
  compute_building_density: {
    running: "Computing building density…",
    complete: "Building density ready",
  },
  fetch_roads_osm: { running: "Fetching roads…", complete: "Loaded roads" },
  fetch_usace_nsi: {
    running: "Fetching structure inventory…",
    complete: "Loaded structure inventory",
  },
  fetch_usace_dams: { running: "Fetching dams…", complete: "Loaded dams" },
  fetch_usace_levees: { running: "Fetching levees…", complete: "Loaded levees" },

  // --- Flood / hydrology data ------------------------------------------- //
  fetch_fema_nfhl_zones: {
    running: "Fetching FEMA flood zones…",
    complete: "Loaded FEMA flood zones",
  },
  fetch_river_geometry: { running: "Fetching river geometry…", complete: "Loaded river geometry" },
  fetch_nhdplus_nldi_navigate: {
    running: "Tracing the river network…",
    complete: "River network traced",
  },
  fetch_noaa_nwm_streamflow: {
    running: "Fetching streamflow…",
    complete: "Loaded streamflow",
  },
  fetch_cama_flood_discharge: {
    running: "Fetching flood discharge…",
    complete: "Loaded flood discharge",
  },
  fetch_gcn250_curve_numbers: {
    running: "Fetching curve numbers…",
    complete: "Loaded curve numbers",
  },
  lookup_precip_return_period: {
    running: "Looking up precip return period…",
    complete: "Precip return period ready",
  },
  fetch_mrms_qpe: { running: "Fetching MRMS precip…", complete: "Loaded MRMS precip" },

  // --- Weather / atmosphere --------------------------------------------- //
  fetch_nws_alerts_conus: {
    running: "Fetching weather alerts…",
    complete: "Loaded weather alerts",
  },
  fetch_nws_event: { running: "Fetching the weather event…", complete: "Loaded weather event" },
  fetch_hrrr_forecast: { running: "Fetching HRRR forecast…", complete: "Loaded HRRR forecast" },
  fetch_hrrr_smoke: { running: "Fetching HRRR smoke…", complete: "Loaded HRRR smoke" },
  fetch_era5_reanalysis: {
    running: "Fetching ERA5 reanalysis…",
    complete: "Loaded ERA5 reanalysis",
  },
  fetch_gridmet: { running: "Fetching gridMET…", complete: "Loaded gridMET" },
  fetch_asos_metar: { running: "Fetching station weather…", complete: "Loaded station weather" },
  fetch_raws_weather: { running: "Fetching RAWS weather…", complete: "Loaded RAWS weather" },
  fetch_nexrad_reflectivity: {
    running: "Fetching radar reflectivity…",
    complete: "Loaded radar reflectivity",
  },
  fetch_goes_satellite: { running: "Fetching GOES imagery…", complete: "Loaded GOES imagery" },

  // --- Coastal / tides -------------------------------------------------- //
  fetch_noaa_coops_tides: { running: "Fetching tide data…", complete: "Loaded tide data" },
  fetch_gtsm_tide_surge: { running: "Fetching tide & surge…", complete: "Loaded tide & surge" },
  fetch_noaa_slr_scenarios: {
    running: "Fetching sea-level-rise scenarios…",
    complete: "Loaded sea-level-rise scenarios",
  },

  // --- Soils ------------------------------------------------------------- //
  fetch_statsgo_soils: { running: "Fetching soils…", complete: "Loaded soils" },

  // --- Fire -------------------------------------------------------------- //
  fetch_firms_active_fire: { running: "Fetching active fires…", complete: "Loaded active fires" },
  fetch_nifc_fire_perimeters: {
    running: "Fetching fire perimeters…",
    complete: "Loaded fire perimeters",
  },
  fetch_mtbs_burn_severity: {
    running: "Fetching burn severity…",
    complete: "Loaded burn severity",
  },

  // --- Storm history ----------------------------------------------------- //
  fetch_storm_events_db: { running: "Fetching storm events…", complete: "Loaded storm events" },

  // --- Biodiversity / conservation -------------------------------------- //
  fetch_gbif_occurrences: {
    running: "Fetching species occurrences…",
    complete: "Loaded species occurrences",
  },
  fetch_inaturalist_observations: {
    running: "Fetching iNaturalist observations…",
    complete: "Loaded iNaturalist observations",
  },
  fetch_ebird_observations: {
    running: "Fetching eBird observations…",
    complete: "Loaded eBird observations",
  },
  fetch_iucn_red_list_range: {
    running: "Fetching IUCN ranges…",
    complete: "Loaded IUCN ranges",
  },
  fetch_wdpa_protected_areas: {
    running: "Fetching protected areas…",
    complete: "Loaded protected areas",
  },
  fetch_movebank_tracks: {
    running: "Fetching animal tracks…",
    complete: "Loaded animal tracks",
  },

  // --- Clipping / extent ------------------------------------------------- //
  clip_raster_to_bbox: { running: "Clipping raster to extent…", complete: "Raster clipped" },
  clip_raster_to_polygon: {
    running: "Clipping raster to boundary…",
    complete: "Raster clipped",
  },
  clip_vector_to_polygon: {
    running: "Clipping vectors to boundary…",
    complete: "Vectors clipped",
  },

  // --- Analysis / statistics -------------------------------------------- //
  compute_zonal_statistics: {
    running: "Computing zonal statistics…",
    complete: "Zonal statistics ready",
  },
  summarize_layer_statistics: {
    running: "Summarizing layer statistics…",
    complete: "Layer statistics ready",
  },
  aggregate_property_within_zone: {
    running: "Aggregating within zone…",
    complete: "Zone aggregation ready",
  },
  count_features_above_threshold: {
    running: "Counting features…",
    complete: "Feature count ready",
  },
  aggregate_claims_across_sources: {
    running: "Aggregating across sources…",
    complete: "Sources aggregated",
  },

  // --- Charts ------------------------------------------------------------ //
  generate_histogram: { running: "Building histogram…", complete: "Histogram ready" },
  generate_time_series: { running: "Building time series…", complete: "Time series ready" },
  generate_damage_distribution: {
    running: "Building damage distribution…",
    complete: "Damage distribution ready",
  },
  generate_choropleth_legend: {
    running: "Building map legend…",
    complete: "Map legend ready",
  },

  // --- Discovery / catalog ---------------------------------------------- //
  discover_dataset: { running: "Discovering datasets…", complete: "Datasets discovered" },
  catalog_search: { running: "Searching the catalog…", complete: "Catalog searched" },
  catalog_fetch: { running: "Fetching from catalog…", complete: "Loaded from catalog" },
  web_fetch: { running: "Fetching from the web…", complete: "Fetched from the web" },

  // --- QGIS / data plumbing --------------------------------------------- //
  publish_layer: { running: "Publishing layer…", complete: "Layer published" },
  estimate_payload_mb: { running: "Estimating payload size…", complete: "Payload size estimated" },
  qgis_process: { running: "Running QGIS process…", complete: "QGIS process done" },
  describe_qgis_algorithm: {
    running: "Describing the algorithm…",
    complete: "Algorithm described",
  },
  list_qgis_algorithms: {
    running: "Listing algorithms…",
    complete: "Algorithms listed",
  },
  mongo_query: { running: "Querying the database…", complete: "Database queried" },
  code_exec_request: { running: "Running analysis code…", complete: "Analysis code done" },

  // --- Engines / solvers ------------------------------------------------- //
  run_model_flood_scenario: {
    running: "Modeling flood [SFINCS]…",
    complete: "Flood modeled",
  },
  run_model_flood_habitat_scenario: {
    running: "Modeling flood + habitat…",
    complete: "Flood + habitat modeled",
  },
  run_model_nws_flood_event_scenario: {
    running: "Modeling NWS flood event…",
    complete: "NWS flood event modeled",
  },
  run_model_groundwater_contamination_scenario: {
    running: "Modeling groundwater plume…",
    complete: "Groundwater plume modeled",
  },
  run_model_news_event_ingest: {
    running: "Ingesting the event…",
    complete: "Event ingested",
  },
  run_modflow_job: {
    running: "Modeling groundwater [MODFLOW]…",
    complete: "Groundwater modeled",
  },
  run_pelicun_damage_assessment: {
    running: "Building damage estimate…",
    complete: "Damage estimate ready",
  },
  postprocess_pelicun: {
    running: "Post-processing damage…",
    complete: "Damage post-processed",
  },
  run_solver: { running: "Running the solver…", complete: "Solver finished" },
  wait_for_completion: { running: "Waiting for the job…", complete: "Job finished" },
};

/**
 * Title-case a raw snake_case tool name as a graceful fallback for any tool
 * not in the map: `fetch_river_widths` → `Fetch River Widths`. NEVER returns
 * the raw snake_case. A trailing "…" is appended by the caller when active.
 */
function titleCaseToolName(rawName: string): string {
  const words = rawName
    .split(/[_\s]+/)
    .filter((w) => w.length > 0)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1));
  // Empty / all-separator names degrade to the raw string rather than "".
  return words.length > 0 ? words.join(" ") : rawName;
}

/**
 * Resolve the user-facing label for a step.
 *
 * @param rawName the verbatim emitted `step.name`.
 * @param state   optional lifecycle state; `complete` selects the terminal
 *                phrasing, everything else (incl. omitted) selects the
 *                active/present-tense phrasing.
 */
export function humanizeStepName(
  rawName: string,
  state?: PipelineStepState,
): string {
  const mapped = HUMANIZED_STEP_NAMES[rawName];
  if (mapped) {
    return state === "complete" ? mapped.complete : mapped.running;
  }
  // Graceful fallback: Title-Case, with a trailing "…" while active so it
  // reads as an in-progress action rather than a static noun.
  const titled = titleCaseToolName(rawName);
  return state === "complete" ? titled : `${titled}…`;
}

// --- Card ----------------------------------------------------------------- //

export interface PipelineCardProps {
  step: PipelineStepSummary;
}

export function PipelineCard({ step }: PipelineCardProps): JSX.Element {
  const reduced = prefersReducedMotion();

  // F70: the VISUAL state may lag the logical state by up to a few hundred ms
  // so the running / rainbow treatment is always perceivable, even when a tool
  // fast-fails (or fast-succeeds) in ~0s. `displayState` drives every visual
  // surface (tint, label phrasing, spinner, rainbow, error chip); the logical
  // `step.state` still drives `data-state`, the authoritative timer value, and
  // the screen-reader announcement so nothing user-truth is fabricated.
  const displayState = useDisplayState(step.state);

  const visual = cardVisual(displayState);
  const displayIsRunning = displayState === "running";
  const displayIsFailed = displayState === "failed";
  // Logical flags (truth from the wire), used for the timer + a11y prefix.
  const isRunning = step.state === "running";
  const isTerminal = TERMINAL_STATES.has(step.state);

  // job-0264 tool timer. While running: a cosmetic live (m:ss) ticker.
  // On a terminal state: the AUTHORITATIVE duration the agent stamped
  // (step.duration_ms). The ticker hook returns 0 for non-running steps.
  const liveElapsedMs = useRunningElapsedMs(step);
  const hasAuthoritativeDuration =
    isTerminal && step.duration_ms !== null && step.duration_ms !== undefined;
  // Timer text precedence: authoritative terminal duration > running ticker.
  // Pending / terminal-without-duration show no timer (nothing to count).
  const timerText: string | null = hasAuthoritativeDuration
    ? formatDuration(step.duration_ms as number)
    : isRunning
      ? formatDuration(liveElapsedMs)
      : null;

  // Label phrasing follows the painted state: the terminal "complete" phrasing
  // only appears once the card visually settles (so a fast-complete still reads
  // its present-tense running verb during the dwell). All other states already
  // use the running/active phrasing, so a failed/cancelled card is unaffected.
  const labelText = humanizeStepName(step.name, displayState);

  // The label uses an animated rainbow gradient when running (unless the
  // user prefers reduced motion). Background-clip:text is the gradient
  // technique; the fallback is the visual.textColor.
  const labelStyle: React.CSSProperties = displayIsRunning && !reduced
    ? {
        backgroundImage:
          "linear-gradient(90deg, #FF6B6B, #FFD93D, #6BCB77, #4D96FF, #B266FF, #FF6B6B)",
        backgroundSize: "300% 100%",
        WebkitBackgroundClip: "text",
        backgroundClip: "text",
        WebkitTextFillColor: "transparent",
        color: "transparent",
        animation: "grace2-hue-cycle 3s linear infinite",
      }
    : { color: visual.textColor };

  return (
    <div
      data-testid="pipeline-card"
      data-step-id={step.step_id}
      data-state={step.state}
      aria-live="polite"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        fontSize: 12,
        lineHeight: "1.4",
        padding: "8px 10px",
        borderRadius: 6,
        background: visual.background,
        boxShadow: "0 1px 3px rgba(0,0,0,0.25)",
        fontFamily: "ui-monospace, 'Cascadia Code', 'Fira Code', monospace",
        position: "relative",
        overflow: "hidden",
        transition: "background-color 200ms ease-in-out",
      }}
    >
      {/* Visually-hidden screen-reader state prefix. */}
      <span
        style={{
          position: "absolute",
          width: 1,
          height: 1,
          padding: 0,
          margin: -1,
          overflow: "hidden",
          clip: "rect(0,0,0,0)",
          whiteSpace: "nowrap",
          border: 0,
        }}
      >
        {visual.ariaPrefix}
      </span>
      <span
        data-testid="pipeline-card-name"
        style={{
          flex: 1,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          ...labelStyle,
        }}
        title={labelText}
      >
        {labelText}
      </span>
      {timerText !== null && (
        <span
          data-testid="pipeline-card-timer"
          data-authoritative={hasAuthoritativeDuration ? "true" : "false"}
          aria-hidden="true"
          style={{
            fontVariantNumeric: "tabular-nums",
            fontSize: 11,
            // Running: dimmed so the rainbow label stays the focus. Terminal:
            // slightly brighter since the spinner is gone and this is the
            // card's only right-side affordance. Keyed on the PAINTED state so
            // it reads as "running" during the F70 dwell.
            color: displayIsRunning
              ? "rgba(255,255,255,0.55)"
              : "rgba(255,255,255,0.7)",
            flexShrink: 0,
            // Lock min-width so the ticking digits don't jitter the layout.
            minWidth: 30,
            textAlign: "right",
          }}
        >
          {timerText}
        </span>
      )}
      {displayIsRunning && <Spinner reduced={reduced} />}
      {displayIsFailed && (step.error_code || step.error_message) && (
        <span
          data-testid="pipeline-card-error"
          style={{ color: "#fca5a5", fontSize: 11, marginLeft: 4 }}
          title={step.error_message ?? undefined}
        >
          {step.error_code ?? "error"}
        </span>
      )}
    </div>
  );
}

// --- Keyframes ----------------------------------------------------------- //
//
// Injected once into <head> on first import. CSS modules are not in use
// here, so we mount a global <style> with the two animations the card
// references. `prefers-reduced-motion` is handled per-render (above), not in
// CSS, so the keyframes remain unconditional.

const KEYFRAMES_ID = "grace2-pipeline-card-keyframes";

function ensureKeyframes(): void {
  if (typeof document === "undefined") return;
  if (document.getElementById(KEYFRAMES_ID)) return;
  const style = document.createElement("style");
  style.id = KEYFRAMES_ID;
  style.textContent = `
@keyframes grace2-hue-cycle {
  0%   { background-position:   0% 50%; }
  100% { background-position: 300% 50%; }
}
@keyframes grace2-spin {
  0%   { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
`;
  document.head.appendChild(style);
}

ensureKeyframes();
