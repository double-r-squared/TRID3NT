// GRACE-2 web - AoiPickerCard tests (#170 J-WEB-2).
//
// Covers the request-free AOI capture card:
//   1. The COORDS fallback returns a valid, ordered bbox on Confirm.
//   2. Bad coords (out of range / non-finite / empty) are rejected - Confirm
//      stays disabled and "Preview on map" surfaces an honest error.
//   3. min/max are normalized (a user typing max < min still yields a valid box).
//   4. Skip / Cancel relay no bbox.
//
// The card draws onto a live MapLibre map; happy-dom has no WebGL, so we inject
// a minimal map stub covering only the methods the card + bbox_draw helpers
// touch (same shape SpatialDrawSurface.test.tsx uses).

import { describe, it, expect, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { AoiPickerCard, coordsToBbox } from "./AoiPickerCard";
import type { Map as MapLibreMap } from "maplibre-gl";

function makeFakeMap(): MapLibreMap {
  const canvas = { style: { cursor: "" } };
  return {
    fitBounds: vi.fn(),
    isStyleLoaded: () => true,
    getCanvas: () => canvas,
    getSource: () => undefined,
    addSource: vi.fn(),
    getLayer: () => undefined,
    addLayer: vi.fn(),
    removeLayer: vi.fn(),
    removeSource: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
    once: vi.fn(),
    dragPan: { enable: vi.fn(), disable: vi.fn() },
  } as unknown as MapLibreMap;
}

function renderCard(map: MapLibreMap | null = makeFakeMap()) {
  const onConfirm = vi.fn<(b: [number, number, number, number]) => void>();
  const onSkip = vi.fn();
  const onCancel = vi.fn();
  render(
    <AoiPickerCard
      map={map}
      onConfirm={onConfirm}
      onSkip={onSkip}
      onCancel={onCancel}
    />,
  );
  return { onConfirm, onSkip, onCancel };
}

function setCoords(minLon: string, minLat: string, maxLon: string, maxLat: string): void {
  fireEvent.change(screen.getByTestId("aoi-min-lon"), { target: { value: minLon } });
  fireEvent.change(screen.getByTestId("aoi-min-lat"), { target: { value: minLat } });
  fireEvent.change(screen.getByTestId("aoi-max-lon"), { target: { value: maxLon } });
  fireEvent.change(screen.getByTestId("aoi-max-lat"), { target: { value: maxLat } });
}

function confirmBtn(): HTMLButtonElement {
  return screen.getByTestId("aoi-confirm") as HTMLButtonElement;
}

describe("coordsToBbox (pure)", () => {
  it("returns a valid ordered bbox for in-range numbers", () => {
    expect(
      coordsToBbox({ minLon: "-85.31", minLat: "35.04", maxLon: "-85.30", maxLat: "35.05" }),
    ).toEqual([-85.31, 35.04, -85.3, 35.05]);
  });

  it("normalizes swapped min/max", () => {
    expect(
      coordsToBbox({ minLon: "-85.30", minLat: "35.05", maxLon: "-85.31", maxLat: "35.04" }),
    ).toEqual([-85.31, 35.04, -85.3, 35.05]);
  });

  it("rejects out-of-range longitude / latitude", () => {
    expect(
      coordsToBbox({ minLon: "-200", minLat: "35", maxLon: "-85", maxLat: "36" }),
    ).toBeNull();
    expect(
      coordsToBbox({ minLon: "-85", minLat: "-91", maxLon: "-84", maxLat: "35" }),
    ).toBeNull();
  });

  it("rejects non-finite / empty / degenerate input", () => {
    expect(
      coordsToBbox({ minLon: "abc", minLat: "35", maxLon: "-85", maxLat: "36" }),
    ).toBeNull();
    expect(
      coordsToBbox({ minLon: "", minLat: "35", maxLon: "-85", maxLat: "36" }),
    ).toBeNull();
    // Zero-area (min == max) is not a usable AOI.
    expect(
      coordsToBbox({ minLon: "-85", minLat: "35", maxLon: "-85", maxLat: "36" }),
    ).toBeNull();
  });
});

describe("AoiPickerCard - coords fallback", () => {
  it("Confirm is disabled until a valid bbox is captured, then forwards it", () => {
    const { onConfirm } = renderCard();
    expect(confirmBtn().disabled).toBe(true);

    setCoords("-85.31", "35.04", "-85.30", "35.05");
    // Preview validates + captures.
    fireEvent.click(screen.getByTestId("aoi-preview"));

    expect(screen.queryByTestId("aoi-coords-error")).toBeNull();
    expect(confirmBtn().disabled).toBe(false);
    // The captured bbox echo confirms what will be sent.
    expect(screen.getByTestId("aoi-bbox-echo")).toBeTruthy();

    fireEvent.click(confirmBtn());
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm.mock.calls[0]![0]).toEqual([-85.31, 35.04, -85.3, 35.05]);
  });

  it("surfaces an error and stays unconfirmable on bad coords", () => {
    const { onConfirm } = renderCard();
    setCoords("-200", "35.04", "-85.30", "35.05"); // lon out of range
    fireEvent.click(screen.getByTestId("aoi-preview"));

    expect(screen.getByTestId("aoi-coords-error")).toBeTruthy();
    expect(confirmBtn().disabled).toBe(true);
    fireEvent.click(confirmBtn());
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("normalizes swapped corners on preview", () => {
    const { onConfirm } = renderCard();
    setCoords("-85.30", "35.05", "-85.31", "35.04"); // max-then-min
    fireEvent.click(screen.getByTestId("aoi-preview"));
    fireEvent.click(confirmBtn());
    expect(onConfirm.mock.calls[0]![0]).toEqual([-85.31, 35.04, -85.3, 35.05]);
  });

  it("works with no map (coords-only headless path)", () => {
    const { onConfirm } = renderCard(null);
    setCoords("10", "20", "11", "21");
    fireEvent.click(screen.getByTestId("aoi-preview"));
    expect(confirmBtn().disabled).toBe(false);
    fireEvent.click(confirmBtn());
    expect(onConfirm.mock.calls[0]![0]).toEqual([10, 20, 11, 21]);
  });

  it("Skip and Cancel relay no bbox", () => {
    const { onConfirm, onSkip, onCancel } = renderCard();
    fireEvent.click(screen.getByTestId("aoi-skip"));
    expect(onSkip).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("aoi-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});

describe("AoiPickerCard - primary draw gesture", () => {
  it("arms a map drag gesture and captures the drawn bbox", () => {
    const map = makeFakeMap();
    const handlers: Record<string, (e: unknown) => void> = {};
    (map.on as ReturnType<typeof vi.fn>).mockImplementation(
      (ev: string, cb: (e: unknown) => void) => {
        handlers[ev] = cb;
      },
    );
    const { onConfirm } = renderCard(map);

    // The card armed the drag gesture (down/move/up listeners attached).
    expect(handlers.mousedown).toBeTruthy();
    expect(handlers.mouseup).toBeTruthy();

    // Simulate a drag: down at one corner, up at the other. The mouseup's
    // onComplete callback flips component state, so flush it inside act().
    act(() => {
      handlers.mousedown!({ lngLat: { lng: -85.31, lat: 35.04 } });
      handlers.mouseup!({ lngLat: { lng: -85.3, lat: 35.05 } });
    });

    expect(confirmBtn().disabled).toBe(false);
    fireEvent.click(confirmBtn());
    expect(onConfirm.mock.calls[0]![0]).toEqual([-85.31, 35.04, -85.3, 35.05]);
  });
});
