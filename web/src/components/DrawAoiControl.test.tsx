// GRACE-2 web - DrawAoiControl tests (NATE item 4 - always-on Draw AOI control).

import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { DrawAoiControl } from "./DrawAoiControl";
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
