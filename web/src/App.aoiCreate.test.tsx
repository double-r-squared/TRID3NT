// GRACE-2 web - AOI-first create-action seam tests (#170 J-WEB-4 / J-WEB-5).
//
// The full App mounts Chat (WebSocket) + MapView (WebGL), neither of which runs
// in happy-dom, so - mirroring App.test.tsx's CollapseShell pattern - we exercise
// the create-action SEAM here through a minimal harness that wires the SAME
// pieces App.tsx wires:
//
//   - the real useCases hook (with a stubbed sendCaseCommand),
//   - the "+ New Case" button -> onCreate opens the AOI-capture overlay (it does
//     NOT create immediately),
//   - the real AoiPickerCard rendered while the overlay is open,
//   - confirm -> createCase(null, bbox); skip -> createCase() (no bbox).
//
// Asserts:
//   1. "+ New Case" opens the overlay (no case-command fired yet).
//   2. Confirm forwards the captured bbox into the create command.
//   3. Skip preserves the no-bbox path (create command with empty args).

import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { AoiPickerCard } from "./components/AoiPickerCard";
import { useCases, type CaseCommandEmitter } from "./hooks/useCases";
import type { BBox } from "./lib/bbox_draw";

// Minimal harness replicating App.tsx's create-action seam (~App.tsx create
// seam) + the Map.tsx AoiPickerCard mount gate, sans WS/WebGL.
function CreateSeamHarness({
  sendCaseCommand,
}: {
  sendCaseCommand: CaseCommandEmitter;
}): JSX.Element {
  const { createCase } = useCases({ sendCaseCommand, isSignedIn: true });
  const [aoiCaptureOpen, setAoiCaptureOpen] = useState(false);

  // The create-action seam: open the overlay instead of creating immediately.
  const onCreate = (): void => setAoiCaptureOpen(true);
  const onConfirm = (bbox: BBox): void => {
    setAoiCaptureOpen(false);
    createCase(null, bbox);
  };
  const onSkip = (): void => {
    setAoiCaptureOpen(false);
    createCase();
  };
  const onCancel = (): void => setAoiCaptureOpen(false);

  return (
    <div>
      <button data-testid="new-case" onClick={onCreate}>
        + New Case
      </button>
      {aoiCaptureOpen ? (
        <AoiPickerCard
          map={null}
          onConfirm={onConfirm}
          onSkip={onSkip}
          onCancel={onCancel}
        />
      ) : null}
    </div>
  );
}

function setCoords(minLon: string, minLat: string, maxLon: string, maxLat: string): void {
  fireEvent.change(screen.getByTestId("aoi-min-lon"), { target: { value: minLon } });
  fireEvent.change(screen.getByTestId("aoi-min-lat"), { target: { value: minLat } });
  fireEvent.change(screen.getByTestId("aoi-max-lon"), { target: { value: maxLon } });
  fireEvent.change(screen.getByTestId("aoi-max-lat"), { target: { value: maxLat } });
}

describe("AOI-first create-action seam (#170)", () => {
  it("opens the AOI overlay on + New Case WITHOUT creating immediately", () => {
    const send = vi.fn();
    render(<CreateSeamHarness sendCaseCommand={send} />);
    // No overlay until the button is pressed.
    expect(screen.queryByTestId("aoi-picker-card")).toBeNull();
    fireEvent.click(screen.getByTestId("new-case"));
    expect(screen.getByTestId("aoi-picker-card")).toBeTruthy();
    // Crucially: no case-command fired just from opening the overlay.
    expect(send).not.toHaveBeenCalled();
  });

  it("Confirm creates the Case WITH the captured bbox", () => {
    const send = vi.fn();
    render(<CreateSeamHarness sendCaseCommand={send} />);
    fireEvent.click(screen.getByTestId("new-case"));
    setCoords("-85.31", "35.04", "-85.30", "35.05");
    fireEvent.click(screen.getByTestId("aoi-preview"));
    fireEvent.click(screen.getByTestId("aoi-confirm"));

    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0]).toEqual([
      "create",
      null,
      { bbox: [-85.31, 35.04, -85.3, 35.05] },
    ]);
    // Overlay closed after confirm.
    expect(screen.queryByTestId("aoi-picker-card")).toBeNull();
  });

  it("Skip preserves the no-bbox path (create with empty args)", () => {
    const send = vi.fn();
    render(<CreateSeamHarness sendCaseCommand={send} />);
    fireEvent.click(screen.getByTestId("new-case"));
    fireEvent.click(screen.getByTestId("aoi-skip"));

    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0]).toEqual(["create", null, {}]);
    expect(screen.queryByTestId("aoi-picker-card")).toBeNull();
  });

  it("Cancel dismisses the overlay and creates nothing", () => {
    const send = vi.fn();
    render(<CreateSeamHarness sendCaseCommand={send} />);
    fireEvent.click(screen.getByTestId("new-case"));
    fireEvent.click(screen.getByTestId("aoi-cancel"));
    expect(send).not.toHaveBeenCalled();
    expect(screen.queryByTestId("aoi-picker-card")).toBeNull();
  });
});
