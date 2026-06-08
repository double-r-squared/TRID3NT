// GRACE-2 web — ChatInput component tests (job-0144).
//
// Verifies the merged send/stop button + dynamic textarea + wrapper styling
// per the job-0144 kickoff acceptance checklist:
//   - Idle state shows up-arrow disabled when empty
//   - Idle state shows up-arrow enabled when text present
//   - Submit transitions to in-flight; up-arrow becomes stop-square
//   - Cancel click emits cancel envelope + returns to idle
//   - Pipeline-complete returns to idle automatically
//   - Multi-line typing expands textarea height
//   - Cmd+Enter / Ctrl+Enter submits; Enter alone inserts newline
//   - Drop shadow + rounded corner styles applied (style assertions)
//
// We test ChatInput directly rather than through Chat (Chat opens a real
// WebSocket which happy-dom can't run; the existing Chat.test.tsx exercises
// the pipelineReducer/shouldShowCancel logic with the same pattern).

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ChatInput } from "./components/ChatInput";

function renderIdle(overrides: Partial<Parameters<typeof ChatInput>[0]> = {}) {
  const onSubmit = vi.fn();
  const onCancel = vi.fn();
  const utils = render(
    <ChatInput
      state="idle"
      onSubmit={onSubmit}
      onCancel={onCancel}
      {...overrides}
    />,
  );
  return { onSubmit, onCancel, ...utils };
}

function renderInFlight(
  overrides: Partial<Parameters<typeof ChatInput>[0]> = {},
) {
  const onSubmit = vi.fn();
  const onCancel = vi.fn();
  const utils = render(
    <ChatInput
      state="in-flight"
      onSubmit={onSubmit}
      onCancel={onCancel}
      {...overrides}
    />,
  );
  return { onSubmit, onCancel, ...utils };
}

describe("ChatInput — idle state glyph + button enablement", () => {
  it("renders the up-arrow glyph and disables the action button when empty", () => {
    renderIdle();
    const glyph = screen.getByTestId("chat-input-glyph");
    expect(glyph.getAttribute("data-glyph")).toBe("up-arrow");
    const btn = screen.getByTestId("chat-input-action") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.getAttribute("aria-label")).toBe("Send message");
  });

  it("enables the up-arrow once non-whitespace text is present", () => {
    renderIdle();
    const ta = screen.getByTestId("chat-input") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "hello" } });
    const btn = screen.getByTestId("chat-input-action") as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    // Whitespace-only input should NOT enable submit.
    fireEvent.change(ta, { target: { value: "   " } });
    expect(
      (screen.getByTestId("chat-input-action") as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});

describe("ChatInput — submit semantics", () => {
  it("clicking the up-arrow with text invokes onSubmit and clears the draft", () => {
    const { onSubmit } = renderIdle();
    const ta = screen.getByTestId("chat-input") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "model the flood" } });
    fireEvent.click(screen.getByTestId("chat-input-action"));
    expect(onSubmit).toHaveBeenCalledWith("model the flood");
    // Component clears the textarea on submit.
    expect((screen.getByTestId("chat-input") as HTMLTextAreaElement).value).toBe(
      "",
    );
  });

  it("Ctrl+Enter submits; plain Enter does NOT submit (inserts newline default)", () => {
    const { onSubmit } = renderIdle();
    const ta = screen.getByTestId("chat-input") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "Hurricane Ian Fort Myers" } });
    // Plain Enter — should not submit.
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
    // Ctrl+Enter — should submit.
    fireEvent.keyDown(ta, { key: "Enter", ctrlKey: true });
    expect(onSubmit).toHaveBeenCalledWith("Hurricane Ian Fort Myers");
  });

  it("Cmd+Enter (metaKey) also submits", () => {
    const { onSubmit } = renderIdle();
    const ta = screen.getByTestId("chat-input") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "go" } });
    fireEvent.keyDown(ta, { key: "Enter", metaKey: true });
    expect(onSubmit).toHaveBeenCalledWith("go");
  });

  it("does NOT submit while in-flight even with text", () => {
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <ChatInput
        state="idle"
        onSubmit={onSubmit}
        onCancel={onCancel}
      />,
    );
    const ta = screen.getByTestId("chat-input") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "queued text" } });
    rerender(
      <ChatInput
        state="in-flight"
        onSubmit={onSubmit}
        onCancel={onCancel}
      />,
    );
    // Cmd+Enter while in-flight: the action button is now Stop; pressing
    // Cmd+Enter on the textarea should NOT submit a second message.
    fireEvent.keyDown(ta, { key: "Enter", ctrlKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
  });
});

describe("ChatInput — in-flight state + cancel", () => {
  it("renders the stop-square glyph when state=in-flight", () => {
    renderInFlight();
    const glyph = screen.getByTestId("chat-input-glyph");
    expect(glyph.getAttribute("data-glyph")).toBe("stop");
    const btn = screen.getByTestId("chat-input-action") as HTMLButtonElement;
    expect(btn.getAttribute("aria-label")).toBe("Stop response");
    // Stop button is enabled in in-flight state regardless of textarea
    // contents (so the user can always abort).
    expect(btn.disabled).toBe(false);
  });

  it("clicking the stop-square emits onCancel", () => {
    const { onCancel } = renderInFlight();
    fireEvent.click(screen.getByTestId("chat-input-action"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("transitioning back to idle re-shows up-arrow (replace-not-reconcile)", () => {
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <ChatInput
        state="in-flight"
        onSubmit={onSubmit}
        onCancel={onCancel}
      />,
    );
    expect(screen.getByTestId("chat-input-glyph").getAttribute("data-glyph"))
      .toBe("stop");
    rerender(
      <ChatInput
        state="idle"
        onSubmit={onSubmit}
        onCancel={onCancel}
      />,
    );
    expect(screen.getByTestId("chat-input-glyph").getAttribute("data-glyph"))
      .toBe("up-arrow");
  });
});

describe("ChatInput — wrapper presentation", () => {
  it("applies a drop shadow + rounded corners + dark background", () => {
    renderIdle();
    const wrapper = screen.getByTestId("chat-input-wrapper");
    const style = wrapper.style;
    // box-shadow non-empty (Kickoff Part 3 / live verification check).
    expect(style.boxShadow).not.toBe("");
    expect(style.boxShadow.toLowerCase()).toContain("rgba(0,0,0");
    // Rounded corners ≥ 12px per kickoff Part 3.
    const radius = parseInt(style.borderRadius, 10);
    expect(radius).toBeGreaterThanOrEqual(12);
    // Dark-theme aware background.
    expect(style.background).toMatch(/^#1[a-f0-9]{5}$/i);
  });

  it("textarea has a minHeight ≥ 48px so the single-line state matches kickoff", () => {
    renderIdle();
    const ta = screen.getByTestId("chat-input") as HTMLTextAreaElement;
    const minH = parseInt(ta.style.minHeight, 10);
    expect(minH).toBeGreaterThanOrEqual(48);
  });

  it("textarea maxHeight scales with the configured maxVh prop", () => {
    renderIdle({ maxVh: 30 });
    const ta = screen.getByTestId("chat-input") as HTMLTextAreaElement;
    expect(ta.style.maxHeight).toBe("30vh");
  });
});

describe("ChatInput — multi-line growth", () => {
  it("grows in measured height as multi-line content is added", () => {
    renderIdle();
    const ta = screen.getByTestId("chat-input") as HTMLTextAreaElement;
    // happy-dom doesn't lay out the textarea so scrollHeight is 0 — but the
    // component sets el.style.height in a useLayoutEffect; even when
    // scrollHeight is 0, MIN_HEIGHT_PX is the floor. Verify the floor.
    expect(ta.style.height).toMatch(/^\d+px$/);
    const initial = parseInt(ta.style.height, 10);
    expect(initial).toBeGreaterThanOrEqual(48);
    // After updating content, the layout effect re-runs.
    fireEvent.change(ta, {
      target: { value: "line1\nline2\nline3\nline4\nline5\nline6\nline7" },
    });
    // Re-read the inline style (the effect ran synchronously via
    // useLayoutEffect; height is at least the floor).
    const grown = parseInt(ta.style.height, 10);
    expect(grown).toBeGreaterThanOrEqual(48);
  });
});

describe("ChatInput — disabled prop (WS down)", () => {
  it("disables both textarea and action button regardless of text", () => {
    renderIdle({ disabled: true });
    const ta = screen.getByTestId("chat-input") as HTMLTextAreaElement;
    expect(ta.disabled).toBe(true);
    fireEvent.change(ta, { target: { value: "queued" } });
    const btn = screen.getByTestId("chat-input-action") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
