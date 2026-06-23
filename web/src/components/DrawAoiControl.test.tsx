// GRACE-2 web - DrawAoiControl tests (NATE item 4 - always-on Draw AOI control).

import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { DrawAoiControl, drawAoiControlPosition } from "./DrawAoiControl";
import { aoiStageBus } from "../lib/aoi_stage_bus";
import type { Map as MapLibreMap } from "maplibre-gl";

interface FakeMap extends MapLibreMap {
  __handlers: Record<string, (e: unknown) => void>;
}

function makeFakeMap(): FakeMap {
  const canvas = { style: { cursor: "" } };
  const handlers: Record<string, (e: unknown) => void> = {};
  const m = {
    __handlers: handlers,
    isStyleLoaded: () => true,
    getCanvas: () => canvas,
    getSource: () => undefined,
    addSource: vi.fn(),
    getLayer: () => undefined,
    addLayer: vi.fn(),
    removeLayer: vi.fn(),
    removeSource: vi.fn(),
    on: vi.fn((ev: string, cb: (e: unknown) => void) => {
      handlers[ev] = cb;
    }),
    off: vi.fn(),
    once: vi.fn(),
    project: ({ 0: lng, 1: lat }: number[]) => ({ x: (lng ?? 0) * 10, y: (lat ?? 0) * 10 }),
    dragPan: { enable: vi.fn(), disable: vi.fn() },
  } as unknown as FakeMap;
  return m;
}

beforeEach(() => {
  aoiStageBus.clear();
});

describe("DrawAoiControl", () => {
  it("renders an always-on Draw AOI button", () => {
    render(<DrawAoiControl map={makeFakeMap()} />);
    expect(screen.getByTestId("grace2-draw-aoi-button")).toBeInTheDocument();
  });

  it("tapping the button ARMS the draw gesture (aria-pressed + bus)", () => {
    render(<DrawAoiControl map={makeFakeMap()} />);
    const btn = screen.getByTestId("grace2-draw-aoi-button");
    expect(btn).toHaveAttribute("aria-pressed", "false");
    act(() => {
      fireEvent.click(btn);
    });
    expect(aoiStageBus.getState().armed).toBe(true);
    expect(screen.getByTestId("grace2-draw-aoi-button")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("completing a drag STAGES the bbox (disarms) and shows a Clear affordance", () => {
    const m = makeFakeMap();
    render(<DrawAoiControl map={m} />);
    act(() => {
      fireEvent.click(screen.getByTestId("grace2-draw-aoi-button"));
    });
    // Simulate the drag gesture: down -> up (the bbox_draw attach listens on the
    // map's mousedown / mouseup with lngLat).
    act(() => {
      m.__handlers["mousedown"]?.({ lngLat: { lng: -100, lat: 40 } });
      m.__handlers["mouseup"]?.({ lngLat: { lng: -99, lat: 41 } });
    });
    const staged = aoiStageBus.getState();
    expect(staged.armed).toBe(false);
    expect(staged.bbox).toEqual([-100, 40, -99, 41]);
    // The Clear affordance appears once a box is staged.
    expect(screen.getByTestId("grace2-draw-aoi-clear")).toBeInTheDocument();
  });

  it("the Clear button drops the staged extent", () => {
    render(<DrawAoiControl map={makeFakeMap()} />);
    act(() => {
      aoiStageBus.setBbox([1, 2, 3, 4]);
    });
    expect(screen.getByTestId("grace2-draw-aoi-clear")).toBeInTheDocument();
    act(() => {
      fireEvent.click(screen.getByTestId("grace2-draw-aoi-clear"));
    });
    expect(aoiStageBus.getState().bbox).toBeNull();
    expect(screen.queryByTestId("grace2-draw-aoi-clear")).toBeNull();
  });

  it("while armed, tapping the button again CANCELS the draw (clears)", () => {
    render(<DrawAoiControl map={makeFakeMap()} />);
    const btn = screen.getByTestId("grace2-draw-aoi-button");
    act(() => {
      fireEvent.click(btn); // arm
    });
    expect(aoiStageBus.getState().armed).toBe(true);
    act(() => {
      fireEvent.click(screen.getByTestId("grace2-draw-aoi-button")); // cancel
    });
    expect(aoiStageBus.getState().armed).toBe(false);
  });

  it("does NOT arm a draw on its own (no ambient free-draw / no-clobber)", () => {
    const m = makeFakeMap();
    render(<DrawAoiControl map={m} />);
    // Without any tap, no mousedown handler should be attached for our gesture
    // (the bus stays disarmed, and a stray map drag can't stage anything).
    expect(aoiStageBus.getState().armed).toBe(false);
    act(() => {
      m.__handlers["mousedown"]?.({ lngLat: { lng: -100, lat: 40 } });
      m.__handlers["mouseup"]?.({ lngLat: { lng: -99, lat: 41 } });
    });
    expect(aoiStageBus.getState().bbox).toBeNull();
  });
});

// --- ITEM 1 (NATE 2026-06-22): controls ONLY during new-case AOI setup ------ //

describe("DrawAoiControl - ITEM 1 gate (only when starting a case, no AOI yet)", () => {
  it("renders the whole control group when the case has NO AOI yet", () => {
    render(<DrawAoiControl map={makeFakeMap()} caseHasAoi={false} />);
    expect(screen.getByTestId("grace2-draw-aoi-control")).toBeInTheDocument();
    expect(screen.getByTestId("grace2-draw-aoi-button")).toBeInTheDocument();
  });

  it("renders NOTHING once the case already HAS a bounding box (caseHasAoi)", () => {
    // Even with a staged bbox, the whole group is gone when the case has an AOI.
    act(() => {
      aoiStageBus.setBbox([1, 2, 3, 4]);
    });
    render(<DrawAoiControl map={makeFakeMap()} caseHasAoi={true} />);
    expect(screen.queryByTestId("grace2-draw-aoi-control")).toBeNull();
    expect(screen.queryByTestId("grace2-draw-aoi-button")).toBeNull();
    expect(screen.queryByTestId("grace2-draw-aoi-confirm")).toBeNull();
    expect(screen.queryByTestId("grace2-draw-aoi-clear")).toBeNull();
  });

  it("defaults to rendering (caseHasAoi undefined = fresh start)", () => {
    render(<DrawAoiControl map={makeFakeMap()} />);
    expect(screen.getByTestId("grace2-draw-aoi-control")).toBeInTheDocument();
  });
});

// --- ITEM 4 (NATE 2026-06-22): the green "+" seeds the case (agent path) ----- //

describe("DrawAoiControl - ITEM 4 confirm (+ surfaces the AOI to the agent)", () => {
  it("shows a green '+' confirm alongside the red-X clear once a bbox is staged", () => {
    render(<DrawAoiControl map={makeFakeMap()} onConfirmAoi={vi.fn()} />);
    expect(screen.queryByTestId("grace2-draw-aoi-confirm")).toBeNull();
    act(() => {
      aoiStageBus.setBbox([1, 2, 3, 4]);
    });
    expect(screen.getByTestId("grace2-draw-aoi-confirm")).toBeInTheDocument();
    expect(screen.getByTestId("grace2-draw-aoi-clear")).toBeInTheDocument();
  });

  it("'+' fires onConfirmAoi with the staged bbox (seed-the-case path) + fits", () => {
    const onConfirmAoi = vi.fn();
    const m = makeFakeMap();
    const fitBounds = vi.fn();
    (m as unknown as { fitBounds: typeof fitBounds }).fitBounds = fitBounds;
    render(<DrawAoiControl map={m} onConfirmAoi={onConfirmAoi} />);
    act(() => {
      aoiStageBus.setBbox([-100, 40, -99, 41]);
    });
    act(() => {
      fireEvent.click(screen.getByTestId("grace2-draw-aoi-confirm"));
    });
    // Agent path: the confirmed bbox is surfaced for createCase(null, bbox).
    expect(onConfirmAoi).toHaveBeenCalledWith([-100, 40, -99, 41]);
    // draw-and-fit path: the camera framed the extent.
    expect(fitBounds).toHaveBeenCalled();
    // The staged box is cleared (the case now owns the AOI).
    expect(aoiStageBus.getState().bbox).toBeNull();
  });

  it("without onConfirmAoi, '+' is draw-and-fit only (keeps the staged box)", () => {
    const m = makeFakeMap();
    const fitBounds = vi.fn();
    (m as unknown as { fitBounds: typeof fitBounds }).fitBounds = fitBounds;
    render(<DrawAoiControl map={m} />);
    act(() => {
      aoiStageBus.setBbox([-100, 40, -99, 41]);
    });
    act(() => {
      fireEvent.click(screen.getByTestId("grace2-draw-aoi-confirm"));
    });
    expect(fitBounds).toHaveBeenCalled();
    // No agent wiring -> the staged box stays (next-prompt analysis extent).
    expect(aoiStageBus.getState().bbox).toEqual([-100, 40, -99, 41]);
  });
});

// --- FIX 2 (NATE 2026-06-22): position to the LEFT of the chat panel -------- //
//
// The control must NOT overlap the chat box. Expanded desktop -> railed to the
// LEFT of the chat panel's left edge, at the panel top, tracking the dragged
// width. Collapsed -> tucked UNDER the top-right chat-expand hamburger. Mobile
// -> plain top-right (the chat is a bottom sheet; nothing to clear).

describe("drawAoiControlPosition (FIX 2 - tracks the chat panel)", () => {
  it("expanded desktop: rails to the LEFT of the panel left edge at the panel top", () => {
    // panel: right 16, width W -> left edge is (16 + W) from the viewport right.
    // control sits one gap (8) further out, at the panel top (16).
    const W = 384;
    const pos = drawAoiControlPosition({ chatWidthPx: W });
    expect(pos.top).toBe(16);
    expect(pos.right).toBe(16 + W + 8);
  });

  it("TRACKS the dragged width: a wider panel pushes the control further right", () => {
    const narrow = drawAoiControlPosition({ chatWidthPx: 320 });
    const wide = drawAoiControlPosition({ chatWidthPx: 600 });
    expect(wide.right).toBeGreaterThan(narrow.right);
    // The delta matches the width delta exactly (1:1 tracking).
    expect(wide.right - narrow.right).toBe(600 - 320);
  });

  it("collapsed: tucks UNDER the top-right chat-expand hamburger", () => {
    const pos = drawAoiControlPosition({ chatWidthPx: 384, chatCollapsed: true });
    // hamburger: top 12, height 40, right 12 -> control below it (12+40+8) at right 12.
    expect(pos.top).toBe(12 + 40 + 8);
    expect(pos.right).toBe(12);
  });

  it("mobile: keeps the plain top-right placement (bottom sheet, nothing to clear)", () => {
    const pos = drawAoiControlPosition({ chatWidthPx: 384, mobile: true });
    expect(pos.top).toBe(12);
    expect(pos.right).toBe(12);
  });

  it("undefined width (legacy callers) falls back to the collapsed/top-right tuck", () => {
    const pos = drawAoiControlPosition({});
    expect(pos.right).toBe(12);
    expect(pos.top).toBe(12 + 40 + 8);
  });
});

describe("DrawAoiControl render position (FIX 2)", () => {
  it("applies the expanded left-of-panel position to the wrapper", () => {
    render(<DrawAoiControl map={makeFakeMap()} chatWidthPx={384} />);
    const wrap = screen.getByTestId("grace2-draw-aoi-control");
    // 16 (panel right) + 384 (width) + 8 (gap) = 408px from the right edge.
    expect(wrap.style.right).toBe("408px");
    expect(wrap.style.top).toBe("16px");
  });

  it("applies the collapsed under-hamburger position to the wrapper", () => {
    render(
      <DrawAoiControl map={makeFakeMap()} chatWidthPx={384} chatCollapsed />,
    );
    const wrap = screen.getByTestId("grace2-draw-aoi-control");
    expect(wrap.style.right).toBe("12px");
    expect(wrap.style.top).toBe("60px");
  });
});
