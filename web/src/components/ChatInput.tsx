// GRACE-2 web — Claude-Code-style merged send/stop chat input (job-0144).
//
// Replaces the prior split textarea + Send + Cancel buttons with a single
// rounded input wrapper containing:
//   1. A dynamic textarea that auto-expands as the user types
//      (min ~1 line / ~48px, max ~40vh — then scrolls internally).
//   2. A single square button anchored bottom-right of the wrapper:
//        - idle  : up-arrow (↑) on a blue ground, disabled when empty
//        - busy  : stop-square (■) on a grey ground, click emits cancel
//        - returns to idle when the pipeline completes/cancels
//
// Submission semantics (FR-WC-7, updated job-0153):
//   - Enter alone        → submit (clear text, send).
//   - Shift+Enter        → insert newline (multi-line input).
//   - Cmd+Enter/Ctrl+Enter → also submit (preserved as alternate hotkey for
//                            users coming from the prior job-0144 behavior).
// This flip matches Claude Code + user expectations; it resolves
// OQ-0144-CMD-ENTER-VS-PLAIN-ENTER-DEFAULT.
//
// Cancel semantics (FR-WC-9 / Invariant 8):
//   - Pressing the in-flight stop-square dispatches `cancel` via the
//     `onCancel` prop (which Chat.tsx wires to GraceWs.sendCancel).
//   - Cancellation leaves loaded layers in place — the input only emits
//     intent, never mutates map state.
//
// Wrapper presentation (kickoff Part 3 + Part 4):
//   - Subtle drop shadow + rounded corners + dark-theme aware background.
//   - The wrapper is positioned by its parent (Chat.tsx) as an overlay at
//     the bottom of the chat panel; the scrollable conversation area
//     applies the matching bottom-padding so messages aren't hidden
//     behind the input.
//
// Invariant 1 (determinism boundary): this component renders + emits intent
// only. It computes no user-facing number; the textarea text it submits is
// passed verbatim to `onSubmit`, which the parent feeds into
// GraceWs.sendUserMessage.

import {
  CSSProperties,
  KeyboardEvent,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

export type ChatInputState = "idle" | "in-flight";

export interface ChatInputProps {
  /** Current pipeline / input state. Drives idle vs in-flight rendering. */
  state: ChatInputState;
  /**
   * Called when the user submits text in idle state (Cmd/Ctrl+Enter or
   * up-arrow click). The component clears its own draft on submit.
   */
  onSubmit: (text: string) => void;
  /**
   * Called when the user clicks the stop-square in in-flight state.
   * Wired to GraceWs.sendCancel by Chat.tsx.
   */
  onCancel: () => void;
  /**
   * Optional: hard-disable the input wrapper (e.g. WS disconnected). When
   * true, both the textarea and the action button are disabled regardless
   * of state. The button still shows the idle/in-flight icon so users see
   * the current pipeline phase.
   */
  disabled?: boolean;
  /** Optional placeholder; defaults to the multi-line hint. */
  placeholder?: string;
  /** Maximum height the textarea grows to before it scrolls internally (vh). */
  maxVh?: number;
  /**
   * Called whenever the wrapper's measured pixel height changes (job-0153
   * Part 4). The parent uses this to grow the chat scroll's bottom-padding
   * so the floating input never clips messages.
   */
  onHeightChange?: (heightPx: number) => void;
  /**
   * job-0278 — textarea font size in px (default 14, the historical desktop
   * value). The mobile bottom sheet passes 16: iOS Safari auto-zooms the
   * page when focusing an input whose font-size is < 16px, which would
   * wreck the fixed-shell layout on phones. Desktop callers omit it.
   */
  fontSizePx?: number;
}

const MIN_HEIGHT_PX = 48;
const DEFAULT_MAX_VH = 40;

/** Square action button stack — up-arrow (↑) idle or stop-square (■) in-flight. */
function ActionGlyph({ state }: { state: ChatInputState }): JSX.Element {
  if (state === "in-flight") {
    // Stop-square — small solid square centered in the button.
    return (
      <span
        data-testid="chat-input-glyph"
        data-glyph="stop"
        aria-hidden="true"
        style={{
          display: "inline-block",
          width: 12,
          height: 12,
          background: "#fff",
          borderRadius: 2,
        }}
      />
    );
  }
  // Up-arrow — SVG so it renders crisply across platforms and matches the
  // Claude Code look (a centered chevron-up).
  return (
    <svg
      data-testid="chat-input-glyph"
      data-glyph="up-arrow"
      aria-hidden="true"
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      style={{ display: "block" }}
    >
      <path
        d="M8 3.5L8 12.5"
        stroke="#fff"
        strokeWidth="1.75"
        strokeLinecap="round"
      />
      <path
        d="M3.75 7.75L8 3.5L12.25 7.75"
        stroke="#fff"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function ChatInput({
  state,
  onSubmit,
  onCancel,
  disabled = false,
  placeholder = "Reply to GRACE-2",
  maxVh = DEFAULT_MAX_VH,
  onHeightChange,
  fontSizePx = 14,
}: ChatInputProps): JSX.Element {
  const [draft, setDraft] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  // Auto-grow: measure scrollHeight against the configured maxHeight every
  // time the draft changes. Falls back to MIN_HEIGHT when empty.
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    // Reset height so we measure the natural scrollHeight (not the previous
    // expanded height).
    el.style.height = "auto";
    const maxPx = Math.round(window.innerHeight * (maxVh / 100));
    const target = Math.min(Math.max(el.scrollHeight, MIN_HEIGHT_PX), maxPx);
    el.style.height = `${target}px`;
    el.style.overflowY = el.scrollHeight > maxPx ? "auto" : "hidden";
    // job-0153 Part 4: report total wrapper height so the parent can grow
    // its scroll-area bottom-padding when the textarea expands. We measure
    // the wrapper (not the textarea) so wrapper padding + border are
    // included in the reported pixel value.
    if (onHeightChange && wrapperRef.current) {
      const h = wrapperRef.current.getBoundingClientRect().height;
      onHeightChange(h);
    }
  }, [draft, maxVh, onHeightChange]);

  // When transitioning back from in-flight to idle, focus the textarea so
  // the user can immediately type their next message — matches the Claude
  // Code interaction model.
  useEffect(() => {
    if (state === "idle") {
      // Microtask delay so React commits the new disabled state first.
      const t = window.setTimeout(() => textareaRef.current?.focus(), 0);
      return () => window.clearTimeout(t);
    }
    return undefined;
  }, [state]);

  function handleSubmit(): void {
    const text = draft.trim();
    if (!text) return;
    if (state !== "idle" || disabled) return;
    onSubmit(text);
    setDraft("");
    // Reset textarea height immediately on clear so the wrapper doesn't
    // briefly retain the prior expanded height.
    const el = textareaRef.current;
    if (el) {
      el.style.height = `${MIN_HEIGHT_PX}px`;
    }
  }

  function handleCancel(): void {
    if (state !== "in-flight" || disabled) return;
    onCancel();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>): void {
    // job-0153 Part 6 — Enter alone submits; Shift+Enter inserts a newline.
    // Cmd+Enter / Ctrl+Enter also submit (kept as alternate hotkey for users
    // who learned the prior job-0144 behavior).
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }

  function onButtonClick(): void {
    if (state === "in-flight") {
      handleCancel();
    } else {
      handleSubmit();
    }
  }

  const hasText = draft.trim().length > 0;

  // Button state derivation:
  //   - idle + empty             → disabled, blue (faded)
  //   - idle + has text          → enabled, blue
  //   - in-flight                → enabled, grey
  //   - disabled (prop)          → forced-disabled (e.g. WS down)
  const buttonDisabled =
    disabled ||
    (state === "idle" && !hasText);
  const buttonStyle: CSSProperties =
    state === "in-flight"
      ? {
          background: buttonDisabled ? "#4a4a52" : "#6b6b76",
          cursor: buttonDisabled ? "default" : "pointer",
          opacity: buttonDisabled ? 0.6 : 1,
        }
      : {
          background: hasText && !disabled ? "#3b82f6" : "#1e3a8a",
          cursor: buttonDisabled ? "default" : "pointer",
          opacity: buttonDisabled ? 0.45 : 1,
        };

  const wrapperStyle: CSSProperties = {
    display: "flex",
    alignItems: "flex-end",
    gap: 8,
    background: "#1a1a20",
    border: "1px solid rgba(255,255,255,0.06)",
    borderRadius: 14,
    boxShadow: "0 2px 12px rgba(0,0,0,0.35)",
    padding: "10px 10px 10px 14px",
    transition: "box-shadow 160ms ease, border-color 160ms ease",
  };

  return (
    <div
      ref={wrapperRef}
      data-testid="chat-input-wrapper"
      data-state={state}
      style={wrapperStyle}
    >
      <textarea
        ref={textareaRef}
        data-testid="chat-input"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        rows={1}
        style={{
          flex: 1,
          minHeight: MIN_HEIGHT_PX,
          maxHeight: `${maxVh}vh`,
          resize: "none",
          background: "transparent",
          color: "#eee",
          border: "none",
          outline: "none",
          fontFamily: "inherit",
          fontSize: fontSizePx,
          lineHeight: 1.4,
          padding: "6px 2px",
        }}
      />
      <button
        data-testid="chat-input-action"
        data-action-state={state}
        aria-label={state === "in-flight" ? "Stop response" : "Send message"}
        onClick={onButtonClick}
        disabled={buttonDisabled}
        style={{
          flex: "0 0 auto",
          width: 32,
          height: 32,
          borderRadius: 8,
          border: "none",
          color: "#fff",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 0,
          transition:
            "background 180ms ease, opacity 180ms ease, transform 120ms ease",
          ...buttonStyle,
        }}
      >
        <ActionGlyph state={state} />
      </button>
    </div>
  );
}
