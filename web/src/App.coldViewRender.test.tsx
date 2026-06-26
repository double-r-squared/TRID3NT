// GRACE-2 web - COLD-OPEN render reproduction (LIVE BUG NATE 2026-06-22).
//
// THE BUG: box ASLEEP -> open Case "100-year Flash Flood Ellicott City"
// (01KVQ53K4K9RRDAEXWA3EX0Y10, 26 layers in DynamoDB AND its case-view S3
// snapshot) -> the map shows the EMPTY-state "No layers loaded yet. Ask the
// assistant to add data." (App.tsx:1532) i.e. ZERO of the 26 layers reached the
// App layer state. The data, signer, and CDN are all proven correct, so the
// drop is in the WEB cold-open render pipeline (or the user is on a stale
// bundle).
//
// THIS TEST is the most faithful reproduction of the cold-open render pipeline
// that runs in happy-dom (the full <App/> mounts maplibre/WebGL which happy-dom
// cannot run - the established App.test.tsx / App.impactEnvelope.test.tsx /
// App.coldViewAuth.test.tsx pattern is a minimal shell that wires the REAL
// modules under test). The harness here wires the EXACT cold-open pipeline:
//
//   1. The REAL `useCases` hook (hooks/useCases.ts) - its real onCaseOpen sets
//      activeSession + activeCaseId, exactly as the live WS case-open AND the
//      App cold-load effect (App.tsx:1155 useCases_onCaseOpen(payload)) do.
//   2. The REAL shared LayerCache (lib/layer_cache.ts) - the cache.activeCaseId
//      lockstep + mergeSnapshot + the #158 empty-frame guard.
//   3. The REAL LayerPanelBus (createLayerPanelBus from ./LayerPanel).
//   4. App.tsx's THREE relevant effects, COPIED VERBATIM (line refs noted):
//        a. the layerCache.activeCaseId lockstep effect (App.tsx ~896-925),
//           keyed [activeCaseId, layerCache];
//        b. the Case rehydration replay effect (App.tsx ~986-1087) - the
//           `bus.pushSessionState({loaded_layers, replace_layers:true})` push,
//           keyed [activeSession, bus];
//        c. the bus subscriber (App.tsx ~1259-1271) that reads
//           layerCache.activeCaseId, mergeSnapshot()s, and setLayers(merged).
//   5. The REAL LayerPanel mounted on the same bus, plus the App empty-state
//      text gated on `layers.length > 0` (App.tsx:1532), reproduced verbatim.
//
// The fixture is the REAL saved snapshot. We feed it through
// useCases.onCaseOpen (the cold-load entry) with the socket NOT connected and
// assert how many of the 26 layers reach the App layer state / LayerPanel.
// EXPECT 26; the bug = fewer (likely 0) + the empty-state text present.

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, act, cleanup, waitFor } from "@testing-library/react";
import { useEffect, useMemo, useRef, useState } from "react";

import { createLayerPanelBus, LayerPanel } from "./LayerPanel";
import { resolveBboxProgress } from "./lib/bbox_progress";
import type { ScreenRect } from "./lib/legend_snap";
import {
  LayerCache,
  setLayerCache,
  getLayerCache,
} from "./lib/layer_cache";
import { LS_ACTIVE_CASE, useCases } from "./hooks/useCases";
import type {
  CaseOpenEnvelopePayload,
  MapCommandPayload,
  ProjectLayerSummary,
} from "./contracts";

// The REAL saved cold snapshot: 1 vector (buildings, inline_geojson present)
// + 25 raster flood-depth-frame-NN (https CloudFront TiTiler tile templates).
import coldSnapshot from "./__fixtures__/ellicott_cold_snapshot.json";

const COLD_PAYLOAD = coldSnapshot as unknown as CaseOpenEnvelopePayload;
const EXPECTED_LAYERS = 26;
const EMPTY_STATE_TEXT = "No layers loaded yet. Ask the assistant to add data.";

// ── In-memory override backend so the real LayerCache never touches IndexedDB
// (happy-dom has none; the default backend already no-ops, but inject one so
// the cache is deterministic + isolated per test). ──────────────────────── //
function memBackend() {
  return {
    async load() {
      return {};
    },
    async save() {
      /* no-op */
    },
  };
}

// ── Harness: App.tsx's cold-open render pipeline, the three effects verbatim.
// `activeSession === null` (the App socket is NOT connected, no live session
// has round-tripped) so the cold-load is what feeds onCaseOpen. ──────────── //
function ColdOpenHarness({
  onLayers,
}: {
  onLayers: (layers: ProjectLayerSummary[]) => void;
}): JSX.Element {
  const bus = useRef(createLayerPanelBus()).current;
  const layerCache = useRef(getLayerCache()).current;

  // The REAL useCases hook. sendCaseCommand is a no-op (the box is asleep - the
  // WS select only QUEUES; the cold-load drives onCaseOpen directly), exactly
  // like App when disconnected.
  const cases = useCases({
    sendCaseCommand: () => {
      /* no-op: box asleep, select would only queue */
    },
    isSignedIn: true,
  });
  const { activeSession, activeCaseId, onCaseOpen, selectCase, clearActive } =
    cases;

  // App's `layers` state (App.tsx:270) + the empty-state gate (App.tsx:1532).
  const [layers, setLayers] = useState<ProjectLayerSummary[]>([]);
  useEffect(() => {
    onLayers(layers);
  }, [layers, onLayers]);

  // BUG 2 harness - the AOI bbox overlay anchor (App.tsx:315 aoiScreenRect). The
  // test arms it directly via __test_setAoi to stand in for MapView's
  // onAoiScreenRectChange; the exit-to-root effect below must clear it.
  const [aoiScreenRect, setAoiScreenRect] = useState<ScreenRect | null>(null);

  // (a) layerCache.activeCaseId lockstep effect (App.tsx ~896-925), VERBATIM
  // (only the WS/scrubber side effects that need maplibre are dropped - they
  // are irrelevant to the layer-drop and don't run in this disconnected path).
  // BUG 2 - the exit-to-root clear (App.tsx ~1080) is reproduced verbatim.
  const activeCaseIdRef = useRef<string | null>(null);
  useEffect(() => {
    const prevCaseId = activeCaseIdRef.current;
    activeCaseIdRef.current = activeCaseId;
    if (prevCaseId !== null && prevCaseId !== activeCaseId) {
      layerCache.evictCase(prevCaseId);
    }
    // BUG 2 (NATE 2026-06-23) - exit-to-root is a CLEAR SLATE.
    if (activeCaseId === null) {
      setAoiScreenRect(null);
      setLayers([]);
    }
    layerCache.activeCaseId = activeCaseId;
  }, [activeCaseId, layerCache]);

  // (b) Case rehydration replay effect (App.tsx ~986-1087): the layer push.
  useEffect(() => {
    if (activeSession === null) {
      bus.pushSessionState({
        loaded_layers: [],
        chat_history: [],
        pipeline_history: [],
        current_pipeline: null,
        map_view: null,
        replace_layers: true,
      });
      return;
    }
    bus.pushSessionState({
      loaded_layers: activeSession.loaded_layers ?? [],
      chat_history: activeSession.chat_history ?? [],
      pipeline_history: activeSession.pipeline_history ?? [],
      current_pipeline: activeSession.current_pipeline ?? null,
      map_view: null,
      replace_layers: true,
    } as unknown as Parameters<typeof bus.pushSessionState>[0]);
  }, [activeSession, bus]);

  // (c) bus subscriber (App.tsx ~1259-1271), VERBATIM.
  useEffect(() => {
    const unsub = bus.subscribeSessionState((p) => {
      const incoming = p.loaded_layers ?? [];
      const authoritativeReplace =
        (p as { replace_layers?: boolean }).replace_layers !== false;
      const caseId = layerCache.activeCaseId;
      const merged = layerCache.mergeSnapshot(caseId, incoming, {
        authoritativeReplace,
      });
      setLayers(merged);
    });
    return unsub;
  }, [bus, layerCache]);

  // BUG 1 (NATE 2026-06-23) - the loading-scan settledness derivation, VERBATIM
  // from App.tsx (~1533). caseSelectedButUnsettled is FALSE once the ACTIVE
  // Case's layers are present (layers.length > 0), so the loading scan clears even
  // if the WS session-settle signal lags (the same-bbox switch / cold-view case).
  // activeSession.case.case_id matching is left out of this harness's settle
  // signal on purpose: useCases sets activeSession on cold-open, so to exercise
  // the "session lags but layers present" path we drive layers directly and keep
  // the session-match term TRUE-by-default (unsettled) via a forced flag.
  const sessionSettled =
    activeSession !== null && activeSession.case.case_id === activeCaseId;
  const caseSelectedButUnsettled =
    activeCaseId !== null && layers.length === 0 && !sessionSettled;
  const layersLoading = useMemo(
    () => activeCaseId !== null && caseSelectedButUnsettled,
    [activeCaseId, caseSelectedButUnsettled],
  );
  const bboxProgress = useMemo(
    () =>
      resolveBboxProgress({
        hasBbox: aoiScreenRect !== null,
        layerCount: layers.length,
        layersLoading,
        connecting: false,
        simRunning: false,
        animationsEnabled: true,
      }),
    [aoiScreenRect, layers.length, layersLoading],
  );

  // Expose the cold-load entry (App.tsx:1155 useCases_onCaseOpen(payload))
  // AND the user-tap entry (App.tsx:308 selectCase -> setActiveCaseId first).
  useEffect(() => {
    (window as unknown as Record<string, unknown>).__test_coldOpen = (
      p: CaseOpenEnvelopePayload,
    ) => onCaseOpen(p);
    (window as unknown as Record<string, unknown>).__test_selectCase = (
      id: string,
    ) => selectCase(id);
    (window as unknown as Record<string, unknown>).__test_setAoi = (
      r: ScreenRect | null,
    ) => setAoiScreenRect(r);
    (window as unknown as Record<string, unknown>).__test_clearActive = () =>
      clearActive();
    return () => {
      delete (window as unknown as Record<string, unknown>).__test_coldOpen;
      delete (window as unknown as Record<string, unknown>).__test_selectCase;
      delete (window as unknown as Record<string, unknown>).__test_setAoi;
      delete (window as unknown as Record<string, unknown>).__test_clearActive;
    };
  }, [onCaseOpen, selectCase, clearActive]);

  return (
    <div>
      <div data-testid="app-layer-count">{layers.length}</div>
      <div data-testid="app-bbox-mode">{bboxProgress.mode}</div>
      <div data-testid="app-has-aoi">{aoiScreenRect ? "yes" : "no"}</div>
      {/* ACTIVE-CASE RESTORE (NATE 2026-06-26) - App keys CasesPanel vs CaseView
          PURELY on activeCaseId===null (App.tsx). Reflect that branch so the
          reload-restore test can assert the open Case is RESTORED (case-view)
          on mount from a seeded localStorage key, not dropped to the list. */}
      <div data-testid="app-view">
        {activeCaseId === null ? "cases-panel" : "case-view"}
      </div>
      {/* The App empty-state, gated on layers.length (App.tsx:1532). */}
      {layers.length === 0 && <div>{EMPTY_STATE_TEXT}</div>}
      {/* The REAL LayerPanel on the same bus (App.tsx:1571 wiring). */}
      {layers.length > 0 && (
        <LayerPanel
          subscribeSessionState={bus.subscribeSessionState}
          onMapCommand={(c: MapCommandPayload) => bus.pushMapCommand(c)}
        />
      )}
    </div>
  );
}

let lastLayers: ProjectLayerSummary[] = [];

beforeEach(() => {
  // Fresh, isolated real LayerCache per test (in-memory backend, no IndexedDB).
  setLayerCache(new LayerCache({ maxCases: 4, backend: memBackend() }));
  lastLayers = [];
  // ACTIVE-CASE RESTORE (NATE 2026-06-26) - useCases now SEEDS activeCaseId from
  // localStorage (LS_ACTIVE_CASE). Clear it so the existing cold-open tests
  // start from the no-restore (null active Case) baseline; the restore test
  // seeds it explicitly.
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
});

afterEach(() => {
  cleanup();
  delete (window as unknown as Record<string, unknown>).__test_coldOpen;
});

describe("cold-open render pipeline (LIVE BUG 2026-06-22 repro)", () => {
  it("sanity: the fixture really carries 26 loaded_layers (1 vector + 25 raster)", () => {
    const ll = COLD_PAYLOAD.session_state?.loaded_layers ?? [];
    expect(ll.length).toBe(EXPECTED_LAYERS);
    const vectors = ll.filter((l) => l.layer_type === "vector");
    const rasters = ll.filter((l) => l.layer_type === "raster");
    expect(vectors.length).toBe(1);
    expect(rasters.length).toBe(25);
  });

  it("feeding the real cold snapshot through the cold-open pipeline reaches all 26 layers", async () => {
    render(
      <ColdOpenHarness
        onLayers={(l) => {
          lastLayers = l;
        }}
      />,
    );

    // Before the cold-open: no session -> empty-state present, zero layers.
    expect(screen.getByTestId("app-layer-count").textContent).toBe("0");
    expect(screen.queryByText(EMPTY_STATE_TEXT)).not.toBeNull();

    // Drive the EXACT cold-load entry: useCases.onCaseOpen(payload).
    await act(async () => {
      (
        window as unknown as {
          __test_coldOpen: (p: CaseOpenEnvelopePayload) => void;
        }
      ).__test_coldOpen(COLD_PAYLOAD);
    });

    // The bug: fewer than 26 (likely 0) reach the App layer state, leaving the
    // empty-state text. The fix: all 26 reach setLayers and the panel mounts.
    await waitFor(() => {
      expect(screen.getByTestId("app-layer-count").textContent).toBe(
        String(EXPECTED_LAYERS),
      );
    });

    expect(lastLayers.length).toBe(EXPECTED_LAYERS);
    // The empty-state must be GONE once layers arrive (App.tsx:1532 gate).
    expect(screen.queryByText(EMPTY_STATE_TEXT)).toBeNull();
    // Both kinds survived: the 1 vector + all 25 raster frames.
    expect(lastLayers.filter((l) => l.layer_type === "vector").length).toBe(1);
    expect(lastLayers.filter((l) => l.layer_type === "raster").length).toBe(25);
  });

  it("two-phase cold-open (tap selects case FIRST, cold-load arrives later) reaches all 26", async () => {
    // The REAL mobile sequence: user taps the case while the box is asleep, so
    // selectCase(id) sets activeCaseId LOCALLY (App.tsx:308) and the WS select
    // merely queues. A render later the cold-load's onCaseOpen arrives with the
    // session. This is the exact two-render ordering the prime-suspect race
    // (layerCache.activeCaseId stale when the rehydration push fires) would hit.
    const caseId = COLD_PAYLOAD.session_state!.case.case_id;

    render(
      <ColdOpenHarness
        onLayers={(l) => {
          lastLayers = l;
        }}
      />,
    );

    // Phase 1: tap -> selectCase sets activeCaseId; no session yet.
    await act(async () => {
      (
        window as unknown as { __test_selectCase: (id: string) => void }
      ).__test_selectCase(caseId);
    });
    // Still empty (no session has painted; the box is asleep).
    expect(screen.getByTestId("app-layer-count").textContent).toBe("0");

    // Phase 2: the cold-load resolves and feeds the snapshot through onCaseOpen.
    await act(async () => {
      (
        window as unknown as {
          __test_coldOpen: (p: CaseOpenEnvelopePayload) => void;
        }
      ).__test_coldOpen(COLD_PAYLOAD);
    });

    await waitFor(() => {
      expect(screen.getByTestId("app-layer-count").textContent).toBe(
        String(EXPECTED_LAYERS),
      );
    });
    expect(lastLayers.length).toBe(EXPECTED_LAYERS);
    expect(screen.queryByText(EMPTY_STATE_TEXT)).toBeNull();
  });
});

// ── Direct pipeline unit: the prime-suspect ordering race in isolation. ──── //
// Replays the bus subscriber's exact logic (App.tsx:1259-1271) against the real
// shared LayerCache to prove what happens when layerCache.activeCaseId is STALE
// (null) at the instant the 26-layer authoritative push fires - the failure
// mode the bug context names. mergeSnapshot(null, ...) passes the list through
// verbatim (root behavior), so even a stale-null caseId does NOT drop layers;
// and once the caseId is correctly set the merge tracks all 26.
describe("mergeSnapshot under a stale/correct activeCaseId (race isolation)", () => {
  const incoming = COLD_PAYLOAD.session_state!.loaded_layers ?? [];

  function subscriberMerge(
    cache: LayerCache,
    layers: ProjectLayerSummary[],
  ): ProjectLayerSummary[] {
    // App.tsx:1259-1271 verbatim, replace_layers:true (authoritative).
    const authoritativeReplace = true;
    const caseId = cache.activeCaseId;
    return cache.mergeSnapshot(caseId, layers, { authoritativeReplace });
  }

  it("STALE null activeCaseId: pass-through keeps all 26 (no drop)", () => {
    const cache = new LayerCache({ maxCases: 4, backend: memBackend() });
    cache.activeCaseId = null; // the race: lockstep effect hasn't run yet.
    const merged = subscriberMerge(cache, incoming);
    expect(merged.length).toBe(EXPECTED_LAYERS);
  });

  it("CORRECT activeCaseId set first: all 26 tracked under the case", () => {
    const cache = new LayerCache({ maxCases: 4, backend: memBackend() });
    cache.activeCaseId = COLD_PAYLOAD.session_state!.case.case_id;
    const merged = subscriberMerge(cache, incoming);
    expect(merged.length).toBe(EXPECTED_LAYERS);
    expect(cache.layersFor(cache.activeCaseId).length).toBe(EXPECTED_LAYERS);
  });
});

// ── BUG 1 (NATE 2026-06-23): the loading SCAN must clear once the active Case's
// layers are PRESENT, even if the WS session-settle signal lags (same-bbox
// switch / cold-view case). resolveBboxProgress maps layersLoading -> a scan
// over already-loaded layers; the App settledness fix makes layersLoading FALSE
// the instant layers.length > 0, so the scan stops. ──────────────────────── //
const setAoi = (r: ScreenRect | null): void =>
  (window as unknown as { __test_setAoi: (r: ScreenRect | null) => void })
    .__test_setAoi(r);
const clearActiveCase = (): void =>
  (window as unknown as { __test_clearActive: () => void }).__test_clearActive();
const coldOpen = (p: CaseOpenEnvelopePayload): void =>
  (window as unknown as { __test_coldOpen: (p: CaseOpenEnvelopePayload) => void })
    .__test_coldOpen(p);

describe("BUG 1 - loading scan clears when the active Case's layers are present", () => {
  it("with an AOI armed but ZERO layers, the bbox overlay shows a FILL scan (loading)", async () => {
    render(<ColdOpenHarness onLayers={(l) => (lastLayers = l)} />);
    // Arm the AOI + select a case (activeCaseId set, no session yet -> unsettled).
    await act(async () => {
      (
        window as unknown as { __test_selectCase: (id: string) => void }
      ).__test_selectCase(COLD_PAYLOAD.session_state!.case.case_id);
      setAoi({ left: 100, top: 100, right: 300, bottom: 200 });
    });
    // Unsettled + zero layers -> loading -> FILL shimmer (first fetch).
    expect(screen.getByTestId("app-bbox-mode").textContent).toBe("fill");
  });

  it("once the layers PAINT, the loading scan CLEARS (mode none) even before session-settle", async () => {
    render(<ColdOpenHarness onLayers={(l) => (lastLayers = l)} />);
    await act(async () => {
      setAoi({ left: 100, top: 100, right: 300, bottom: 200 });
      coldOpen(COLD_PAYLOAD);
    });
    await waitFor(() => {
      expect(screen.getByTestId("app-layer-count").textContent).toBe(
        String(EXPECTED_LAYERS),
      );
    });
    // Layers present -> caseSelectedButUnsettled FALSE -> layersLoading FALSE ->
    // resolveBboxProgress returns "none": NO scan running over loaded layers.
    expect(screen.getByTestId("app-bbox-mode").textContent).toBe("none");
  });
});

// ── BUG 2 (NATE 2026-06-23): exit-to-root is a CLEAR SLATE - the AOI bbox
// overlay anchor (aoiScreenRect) AND the layers must both clear when
// activeCaseId becomes null, so nothing lingers on the Cases root. ───────── //
describe("BUG 2 - exit-to-root clears the AOI overlay and the layers", () => {
  it("clearActive() drops aoiScreenRect + layers so the Cases root is blank", async () => {
    render(<ColdOpenHarness onLayers={(l) => (lastLayers = l)} />);
    // Open a case with layers + an armed AOI overlay.
    await act(async () => {
      setAoi({ left: 100, top: 100, right: 300, bottom: 200 });
      coldOpen(COLD_PAYLOAD);
    });
    await waitFor(() => {
      expect(screen.getByTestId("app-layer-count").textContent).toBe(
        String(EXPECTED_LAYERS),
      );
    });
    expect(screen.getByTestId("app-has-aoi").textContent).toBe("yes");

    // Exit to the Cases root.
    await act(async () => {
      clearActiveCase();
    });

    // CLEAR SLATE: no AOI overlay anchor, no layers, no bbox scan on the root.
    await waitFor(() => {
      expect(screen.getByTestId("app-has-aoi").textContent).toBe("no");
    });
    expect(screen.getByTestId("app-layer-count").textContent).toBe("0");
    expect(screen.getByTestId("app-bbox-mode").textContent).toBe("none");
  });
});

// ── ACTIVE-CASE RESTORE (NATE 2026-06-26): on RELOAD (felt most on mobile) the
// app must STAY in the open Case (CaseView), not drop to the Cases LIST. App
// keys CasesPanel vs CaseView PURELY on activeCaseId===null; useCases now SEEDS
// activeCaseId from a persisted localStorage key (LS_ACTIVE_CASE) so a fresh
// mount (a reload) restores the open Case before any WS round-trip. ───────── //
describe("ACTIVE-CASE RESTORE - reload stays in the open Case (not the list)", () => {
  it("a seeded localStorage active-Case id restores CaseView on mount (simulated reload)", () => {
    // Simulate the prior session having left a Case open: the persisted key is
    // present at the instant the app (re)mounts, exactly as after a reload.
    const restoredId = COLD_PAYLOAD.session_state!.case.case_id;
    localStorage.setItem(LS_ACTIVE_CASE, restoredId);

    render(<ColdOpenHarness onLayers={(l) => (lastLayers = l)} />);

    // The hook seeded activeCaseId from localStorage on mount -> App renders
    // CaseView, NOT the Cases list. This is the whole bug: without the seed the
    // view would be "cases-panel" (activeCaseId === null) after every reload.
    expect(screen.getByTestId("app-view").textContent).toBe("case-view");
  });

  it("with NO persisted id the app mounts to the Cases list (baseline unchanged)", () => {
    // No LS_ACTIVE_CASE key (cleared in beforeEach) -> activeCaseId null -> the
    // Cases list, proving the restore is opt-in on a persisted id only.
    render(<ColdOpenHarness onLayers={(l) => (lastLayers = l)} />);
    expect(screen.getByTestId("app-view").textContent).toBe("cases-panel");
  });
});
