// GRACE-2 web — Mode2OfferModal + suppression-list tests (job-0126, sprint-12-mega Wave 2).
//
// Coverage (kickoff acceptance):
//   1. Renders modal when high-confidence (>=0.7) envelope arrives.
//   2. Renders toast for low-confidence (<0.7) envelopes.
//   3. "Add" button triggers an `add` action carrying the candidate.
//   4. "Don't ask again" adds the candidate domain to local-storage suppression list.
//   5. An audit-like action is emitted on every user action.
//   6. Suppressed-domain candidates do NOT surface.
//   7. ws.ts emits `mode2-add-confirmed` + `mode2-audit-event` envelopes through
//      App's bridge (verified by GraceWs unit test on the send path).
//
// Tests stub the subscription seam directly so we don't need a real WebSocket;
// the App.tsx integration is covered by the App test suite + Playwright.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import {
  Mode2OfferModal,
  type Mode2OfferAction,
} from "./components/Mode2OfferModal";
import {
  MODE2_SUPPRESSION_STORAGE_KEY,
  Mode2Candidate,
  Mode2CandidatePayload,
  clearSuppressions,
  isSuppressed,
  listSuppressed,
  suppressDomain,
} from "./lib/mode2_suppression";
import { GraceWs } from "./ws";

// --- Test helpers -------------------------------------------------------- //

function makeCandidate(overrides: Partial<Mode2Candidate> = {}): Mode2Candidate {
  return {
    candidate_id: "01HFAKECANDIDATE0000000001",
    url: "https://water.weather.gov/ahps/",
    domain: "water.weather.gov",
    domain_tld: "gov",
    confidence: 0.85,
    detected_patterns: ["openapi-spec-link", "data-download-link"],
    title: "NWS AHPS Water",
    suggested_tool_kind: "endpoint",
    snippet: '<a href="/openapi.json">Download CSV</a>',
    ...overrides,
  };
}

function makeEnvelope(c: Mode2Candidate): Mode2CandidatePayload {
  return { envelope_type: "mode2-candidate", candidate: c };
}

interface SubscribeHarness {
  subscribe: (cb: (p: Mode2CandidatePayload) => void) => () => void;
  emit: (p: Mode2CandidatePayload) => void;
}

function createSubscribeHarness(): SubscribeHarness {
  const subscribers = new Set<(p: Mode2CandidatePayload) => void>();
  return {
    subscribe: (cb) => {
      subscribers.add(cb);
      return () => {
        subscribers.delete(cb);
      };
    },
    emit: (p) => {
      subscribers.forEach((cb) => cb(p));
    },
  };
}

// --- Test bookkeeping ---------------------------------------------------- //

beforeEach(() => {
  // Each test starts with an empty suppression list so order doesn't matter.
  try {
    window.localStorage.removeItem(MODE2_SUPPRESSION_STORAGE_KEY);
  } catch {
    // ignore
  }
});

afterEach(() => {
  cleanup();
  try {
    window.localStorage.removeItem(MODE2_SUPPRESSION_STORAGE_KEY);
  } catch {
    // ignore
  }
});

// --- Test cases ---------------------------------------------------------- //

describe("Mode2OfferModal — high-confidence modal", () => {
  it("renders the modal with snippet + patterns when a >=0.7 envelope arrives", () => {
    const harness = createSubscribeHarness();
    const onAction = vi.fn();
    render(
      <Mode2OfferModal
        subscribeCandidate={harness.subscribe}
        onAction={onAction}
      />,
    );
    expect(screen.queryByTestId("grace2-mode2-modal")).toBeNull();
    act(() => {
      harness.emit(makeEnvelope(makeCandidate()));
    });
    expect(screen.getByTestId("grace2-mode2-modal")).toBeInTheDocument();
    expect(screen.getByTestId("grace2-mode2-modal-domain")).toHaveTextContent(
      "water.weather.gov",
    );
    expect(
      screen.getByTestId("grace2-mode2-modal-pattern-openapi-spec-link"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("grace2-mode2-modal-pattern-data-download-link"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("grace2-mode2-modal-kind")).toHaveTextContent(
      "API endpoint",
    );
    expect(screen.getByTestId("grace2-mode2-modal-snippet")).toHaveTextContent(
      "Download CSV",
    );
    expect(
      screen.getByTestId("grace2-mode2-modal-confidence"),
    ).toHaveTextContent("85%");
  });
});

describe("Mode2OfferModal — low-confidence toast", () => {
  it("renders a toast (not a modal) when confidence < 0.7", () => {
    const harness = createSubscribeHarness();
    const onAction = vi.fn();
    const candidate = makeCandidate({
      candidate_id: "01HFAKECANDIDATELOW00000001",
      confidence: 0.6,
      detected_patterns: ["rest-endpoint-pattern"],
    });
    render(
      <Mode2OfferModal
        subscribeCandidate={harness.subscribe}
        onAction={onAction}
      />,
    );
    act(() => {
      harness.emit(makeEnvelope(candidate));
    });
    expect(screen.queryByTestId("grace2-mode2-modal")).toBeNull();
    expect(
      screen.getByTestId(`grace2-mode2-toast-${candidate.candidate_id}`),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId(`grace2-mode2-toast-domain-${candidate.candidate_id}`),
    ).toHaveTextContent("water.weather.gov");
  });
});

describe("Mode2OfferModal — Add action", () => {
  it("emits an `add` action with the candidate when Add is clicked", () => {
    const harness = createSubscribeHarness();
    const onAction = vi.fn();
    render(
      <Mode2OfferModal
        subscribeCandidate={harness.subscribe}
        onAction={onAction}
      />,
    );
    const candidate = makeCandidate();
    act(() => {
      harness.emit(makeEnvelope(candidate));
    });
    fireEvent.click(screen.getByTestId("grace2-mode2-modal-add"));
    expect(onAction).toHaveBeenCalledWith({
      kind: "add",
      candidate,
    } satisfies Mode2OfferAction);
    expect(screen.queryByTestId("grace2-mode2-modal")).toBeNull();
  });
});

describe("Mode2OfferModal — suppression", () => {
  it("Don't-ask-again adds the domain to local-storage suppression and emits action", () => {
    const harness = createSubscribeHarness();
    const onAction = vi.fn();
    render(
      <Mode2OfferModal
        subscribeCandidate={harness.subscribe}
        onAction={onAction}
      />,
    );
    const candidate = makeCandidate({ domain: "data.usgs.gov" });
    act(() => {
      harness.emit(makeEnvelope(candidate));
    });
    expect(isSuppressed("data.usgs.gov")).toBe(false);
    fireEvent.click(screen.getByTestId("grace2-mode2-modal-suppress"));
    expect(isSuppressed("data.usgs.gov")).toBe(true);
    expect(listSuppressed()).toContain("data.usgs.gov");
    expect(onAction).toHaveBeenCalledWith({
      kind: "suppress",
      candidate,
    } satisfies Mode2OfferAction);
    // Re-emitting the same domain must now be silent (no modal, no toast).
    act(() => {
      harness.emit(
        makeEnvelope(
          makeCandidate({
            candidate_id: "01HFAKECANDIDATE0000000002",
            domain: "data.usgs.gov",
          }),
        ),
      );
    });
    expect(screen.queryByTestId("grace2-mode2-modal")).toBeNull();
  });

  it("low-confidence candidate on a suppressed domain does not surface as a toast", () => {
    const harness = createSubscribeHarness();
    const onAction = vi.fn();
    render(
      <Mode2OfferModal
        subscribeCandidate={harness.subscribe}
        onAction={onAction}
      />,
    );
    suppressDomain("water.weather.gov");
    act(() => {
      harness.emit(
        makeEnvelope(
          makeCandidate({
            candidate_id: "01HFAKELOWSUPPRESSED00000001",
            confidence: 0.5,
            detected_patterns: ["rest-endpoint-pattern"],
          }),
        ),
      );
    });
    expect(
      screen.queryByTestId(
        "grace2-mode2-toast-01HFAKELOWSUPPRESSED00000001",
      ),
    ).toBeNull();
  });
});

describe("Mode2OfferModal — Maybe later", () => {
  it("Maybe-later dismisses without suppression and emits a `dismiss` action", () => {
    const harness = createSubscribeHarness();
    const onAction = vi.fn();
    render(
      <Mode2OfferModal
        subscribeCandidate={harness.subscribe}
        onAction={onAction}
      />,
    );
    const candidate = makeCandidate({ domain: "data.gov" });
    act(() => {
      harness.emit(makeEnvelope(candidate));
    });
    fireEvent.click(screen.getByTestId("grace2-mode2-modal-dismiss"));
    expect(onAction).toHaveBeenCalledWith({
      kind: "dismiss",
      candidate,
    } satisfies Mode2OfferAction);
    // Domain stays UNSUPPRESSED — a future emit on the same domain still surfaces.
    expect(isSuppressed("data.gov")).toBe(false);
    act(() => {
      harness.emit(
        makeEnvelope(
          makeCandidate({
            candidate_id: "01HFAKECANDIDATE0000000003",
            domain: "data.gov",
          }),
        ),
      );
    });
    expect(screen.getByTestId("grace2-mode2-modal")).toBeInTheDocument();
  });
});

describe("Mode2OfferModal — toast Add button", () => {
  it("Add on a toast emits add action and removes the toast", () => {
    const harness = createSubscribeHarness();
    const onAction = vi.fn();
    render(
      <Mode2OfferModal
        subscribeCandidate={harness.subscribe}
        onAction={onAction}
      />,
    );
    const candidate = makeCandidate({
      candidate_id: "01HFAKETOASTADD0000000001",
      confidence: 0.55,
      detected_patterns: ["tabular-data"],
    });
    act(() => {
      harness.emit(makeEnvelope(candidate));
    });
    fireEvent.click(
      screen.getByTestId(`grace2-mode2-toast-add-${candidate.candidate_id}`),
    );
    expect(onAction).toHaveBeenCalledWith({
      kind: "add",
      candidate,
    } satisfies Mode2OfferAction);
    expect(
      screen.queryByTestId(`grace2-mode2-toast-${candidate.candidate_id}`),
    ).toBeNull();
  });
});

// --- Suppression helper unit tests --------------------------------------- //

describe("mode2_suppression helpers", () => {
  it("suppressDomain is case-insensitive and idempotent", () => {
    suppressDomain("Water.Weather.GOV");
    expect(isSuppressed("water.weather.gov")).toBe(true);
    expect(isSuppressed("WATER.WEATHER.GOV")).toBe(true);
    suppressDomain("water.weather.gov");
    expect(listSuppressed()).toHaveLength(1);
  });

  it("clearSuppressions empties the list", () => {
    suppressDomain("a.gov");
    suppressDomain("b.edu");
    expect(listSuppressed()).toHaveLength(2);
    clearSuppressions();
    expect(listSuppressed()).toHaveLength(0);
  });
});

// --- ws.ts mode2-add-confirmed emission ---------------------------------- //
//
// Verifies the GraceWs side of the bridge: the modal hands an `add` action up
// to App.tsx, which calls `sendMode2AddConfirmed` on the active GraceWs. The
// test stubs WebSocket directly, opens a connection, then calls the send
// methods and asserts the wire shapes match the OQ-tracked envelope.

interface FakeSocket {
  readyState: number;
  sent: string[];
  triggerOpen(): void;
}

function installFakeWebSocket(): { current: () => FakeSocket | null } {
  let lastSocket: FakeSocket | null = null;
  class FakeWS {
    static OPEN = 1;
    static CONNECTING = 0;
    readyState = FakeWS.CONNECTING;
    sent: string[] = [];
    listeners: Record<string, ((ev: unknown) => void)[]> = {};
    constructor(_url: string) {
      lastSocket = this as unknown as FakeSocket;
    }
    addEventListener(type: string, cb: (ev: unknown) => void): void {
      (this.listeners[type] ??= []).push(cb);
    }
    send(data: string): void {
      this.sent.push(data);
    }
    close(): void {
      this.readyState = 3;
      (this.listeners["close"] ?? []).forEach((cb) => cb({}));
    }
    triggerOpen(): void {
      this.readyState = FakeWS.OPEN;
      (this.listeners["open"] ?? []).forEach((cb) => cb({}));
    }
  }
  // @ts-expect-error replacing the global WebSocket for this test only
  globalThis.WebSocket = FakeWS;
  return { current: () => lastSocket };
}

describe("ws.ts — mode2-add-confirmed + mode2-audit-event emission (job-0126)", () => {
  let originalWS: typeof WebSocket;
  beforeEach(() => {
    originalWS = globalThis.WebSocket;
  });
  afterEach(() => {
    globalThis.WebSocket = originalWS;
  });

  it("emits a `mode2-add-confirmed` envelope with the candidate fields", async () => {
    const fake = installFakeWebSocket();
    const ws = new GraceWs("ws://test", {
      onStatus: vi.fn(),
      onAgentChunk: vi.fn(),
      onPipelineState: vi.fn(),
      onSessionState: vi.fn(),
      onError: vi.fn(),
    });
    ws.connect();
    fake.current()?.triggerOpen();
    ws.sendMode2AddConfirmed({
      candidate_id: "01HFAKECANDIDATEXYZ",
      url: "https://water.weather.gov/openapi.json",
      domain: "water.weather.gov",
      suggested_tool_kind: "endpoint",
    });
    const sent = fake.current()?.sent ?? [];
    const env = sent
      .map((s) => JSON.parse(s))
      .find((e: { type: string }) => e.type === "mode2-add-confirmed");
    expect(env).toBeDefined();
    expect(env.payload).toEqual({
      envelope_type: "mode2-add-confirmed",
      candidate_id: "01HFAKECANDIDATEXYZ",
      url: "https://water.weather.gov/openapi.json",
      domain: "water.weather.gov",
      suggested_tool_kind: "endpoint",
    });
    ws.close();
  });

  it("emits a `mode2-audit-event` envelope on dispatch", async () => {
    const fake = installFakeWebSocket();
    const ws = new GraceWs("ws://test", {
      onStatus: vi.fn(),
      onAgentChunk: vi.fn(),
      onPipelineState: vi.fn(),
      onSessionState: vi.fn(),
      onError: vi.fn(),
    });
    ws.connect();
    fake.current()?.triggerOpen();
    ws.sendMode2AuditEvent({
      candidate_id: "01HAUDIT0001",
      domain: "weather.gov",
      action: "add",
      confidence: 0.82,
      surface: "modal",
    });
    const sent = fake.current()?.sent ?? [];
    const env = sent
      .map((s) => JSON.parse(s))
      .find((e: { type: string }) => e.type === "mode2-audit-event");
    expect(env).toBeDefined();
    expect(env.payload).toMatchObject({
      envelope_type: "mode2-audit-event",
      candidate_id: "01HAUDIT0001",
      domain: "weather.gov",
      action: "add",
      confidence: 0.82,
      surface: "modal",
    });
    ws.close();
  });

  it("routes a `mode2-candidate` server frame to the onMode2Candidate handler", () => {
    const fake = installFakeWebSocket();
    const onMode2Candidate = vi.fn();
    const ws = new GraceWs("ws://test", {
      onStatus: vi.fn(),
      onAgentChunk: vi.fn(),
      onPipelineState: vi.fn(),
      onSessionState: vi.fn(),
      onError: vi.fn(),
      onMode2Candidate,
    });
    ws.connect();
    const sock = fake.current()!;
    sock.triggerOpen();
    // Simulate an inbound mode2-candidate frame as if from the Wave 1 emitter.
    const candidate = makeCandidate();
    // @ts-expect-error reach into the fake socket listener registry
    sock.listeners["message"][0]({
      data: JSON.stringify({
        type: "mode2-candidate",
        session_id: "test-session",
        payload: makeEnvelope(candidate),
      }),
    });
    expect(onMode2Candidate).toHaveBeenCalledTimes(1);
    expect(onMode2Candidate.mock.calls[0]?.[0]).toEqual(makeEnvelope(candidate));
    ws.close();
  });
});
