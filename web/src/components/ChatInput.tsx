// GRACE-2 web — Claude-Code-style merged send/stop chat input (job-0144).
//
// Replaces the prior split textarea + Send + Cancel buttons with a single
// rounded input wrapper containing:
//   1. A dynamic textarea that auto-expands as the user types
//      (min ~1 line / ~48px, max ~40vh — then scrolls internally).
//   2. A LEFT button row of auxiliary controls (below the textarea):
//        - Paperclip (attach) — disabled stub; title "coming soon"
//        - Microphone (voice) — disabled stub; title "coming soon"
//        - Mode toggle       — disabled stub; title "coming soon"
//        - Model selector    — Brain icon; opens an inline popover to swap the
//                             active Bedrock model between turns
//   3. A single square button anchored bottom-right of the wrapper:
//        - idle  : up-arrow (↑) on a blue ground, disabled when empty
//        - busy  : stop-square (■) on a grey ground, click emits cancel
//        - returns to idle when the pipeline completes/cancels
//
// Model selector details (NATE 2026-06-17):
//   - The selected model id is persisted to localStorage via modelRegistry.ts.
//   - The chat wrapper border is tinted to the active provider's accent color.
//   - `onSubmit` receives the selected model id alongside the text so the
//     caller (Chat.tsx) can include `model_id` on the user-message envelope.
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

import {
  IconPaperclip,
  IconMic,
  IconModel,
} from "./icons";
import {
  SELECTABLE_MODELS,
  getModelById,
  loadPersistedModelId,
  persistModelId,
  type ModelEntry,
} from "../lib/modelRegistry";

export type ChatInputState = "idle" | "in-flight";

export interface ChatInputProps {
  /** Current pipeline / input state. Drives idle vs in-flight rendering. */
  state: ChatInputState;
  /**
   * Called when the user submits text in idle state (Cmd/Ctrl+Enter or
   * up-arrow click). The component clears its own draft on submit.
   * `modelId` carries the currently-selected Bedrock model id so the caller
   * can include it on the `user-message` envelope.
   */
  onSubmit: (text: string, modelId: string) => void;
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

// ---------------------------------------------------------------------------
// Model selector popover
// ---------------------------------------------------------------------------

interface ModelPopoverProps {
  anchorRef: React.RefObject<HTMLButtonElement>;
  selectedId: string;
  onSelect: (model: ModelEntry) => void;
  onClose: () => void;
}

function ModelPopover({
  anchorRef,
  selectedId,
  onSelect,
  onClose,
}: ModelPopoverProps): JSX.Element {
  const popoverRef = useRef<HTMLDivElement | null>(null);

  // Close on click-outside.
  useEffect(() => {
    function handlePointerDown(e: PointerEvent): void {
      const target = e.target as Node | null;
      if (!target) return;
      if (popoverRef.current?.contains(target)) return;
      if (anchorRef.current?.contains(target)) return;
      onClose();
    }
    document.addEventListener("pointerdown", handlePointerDown, { capture: true });
    return () => document.removeEventListener("pointerdown", handlePointerDown, { capture: true });
  }, [anchorRef, onClose]);

  // Close on Escape.
  useEffect(() => {
    function handleKey(e: globalThis.KeyboardEvent): void {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  // Group models by provider for display.
  const grouped = SELECTABLE_MODELS.reduce<Record<string, ModelEntry[]>>((acc, m) => {
    const list = acc[m.provider] ?? [];
    list.push(m);
    acc[m.provider] = list;
    return acc;
  }, {});

  const popoverStyle: CSSProperties = {
    position: "absolute",
    bottom: "calc(100% + 8px)",
    left: 0,
    zIndex: 2000,
    background: "#1e1e26",
    border: "1px solid rgba(255,255,255,0.1)",
    borderRadius: 10,
    padding: "6px 0",
    minWidth: 240,
    boxShadow: "0 8px 24px rgba(0,0,0,0.5)",
  };

  return (
    <div
      ref={popoverRef}
      data-testid="model-popover"
      role="listbox"
      aria-label="Select model"
      style={popoverStyle}
    >
      {Object.entries(grouped).map(([provider, models]) => (
        <div key={provider}>
          <div
            style={{
              padding: "4px 14px 2px",
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              color: "rgba(255,255,255,0.35)",
              userSelect: "none",
            }}
          >
            {provider}
          </div>
          {models.map((m) => {
            const isSelected = m.id === selectedId;
            return (
              <button
                key={m.id}
                role="option"
                aria-selected={isSelected}
                data-testid={`model-option-${m.id}`}
                onClick={() => { onSelect(m); onClose(); }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 8,
                  width: "100%",
                  padding: "6px 14px",
                  background: isSelected ? "rgba(255,255,255,0.05)" : "transparent",
                  border: "none",
                  cursor: "pointer",
                  color: isSelected ? "#fff" : "rgba(255,255,255,0.7)",
                  fontSize: 13,
                  textAlign: "left",
                  transition: "background 100ms ease",
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.background = "rgba(255,255,255,0.07)";
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.background =
                    isSelected ? "rgba(255,255,255,0.05)" : "transparent";
                }}
              >
                <span>{m.label}</span>
                {isSelected && (
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      background: m.accentColor,
                      flexShrink: 0,
                    }}
                  />
                )}
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small icon button used for left-row stubs and the model selector trigger.
// ---------------------------------------------------------------------------
interface IconButtonProps {
  onClick?: () => void;
  disabled?: boolean;
  title: string;
  "data-testid"?: string;
  children: React.ReactNode;
  active?: boolean;
  accentColor?: string;
}

function LeftIconButton({
  onClick,
  disabled,
  title,
  "data-testid": testId,
  children,
  active = false,
  accentColor,
}: IconButtonProps): JSX.Element {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={title}
      data-testid={testId}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: 28,
        height: 28,
        borderRadius: 6,
        border: "none",
        padding: 0,
        background: active ? "rgba(255,255,255,0.08)" : "transparent",
        color: active && accentColor
          ? accentColor
          : disabled
          ? "rgba(255,255,255,0.2)"
          : "rgba(255,255,255,0.5)",
        cursor: disabled ? "default" : "pointer",
        transition: "background 120ms ease, color 120ms ease",
        flexShrink: 0,
      }}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

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

  // Model selection — load from localStorage on mount.
  const [selectedModel, setSelectedModel] = useState<ModelEntry>(() =>
    getModelById(loadPersistedModelId()),
  );
  const [popoverOpen, setPopoverOpen] = useState(false);
  const modelButtonRef = useRef<HTMLButtonElement | null>(null);

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

  function handleModelSelect(model: ModelEntry): void {
    setSelectedModel(model);
    persistModelId(model.id);
  }

  function handleSubmit(): void {
    const text = draft.trim();
    if (!text) return;
    if (state !== "idle" || disabled) return;
    onSubmit(text, selectedModel.id);
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

  // The wrapper border is tinted to the active model's provider accent color.
  // Use a low-opacity tint at rest and a higher-opacity tint on focus (via
  // CSS variable approach with inline style). The provider tint also provides
  // ambient "which model is active" signal at a glance.
  const accentColor = selectedModel.accentColor;
  const wrapperStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 0,
    background: "#1a1a20",
    border: `1.5px solid ${accentColor}55`,
    borderRadius: 14,
    boxShadow: "0 2px 12px rgba(0,0,0,0.35)",
    padding: "10px 10px 8px 14px",
    transition: "box-shadow 160ms ease, border-color 160ms ease",
    position: "relative",
  };

  return (
    <div
      ref={wrapperRef}
      data-testid="chat-input-wrapper"
      data-state={state}
      data-model-id={selectedModel.id}
      style={wrapperStyle}
    >
      {/* Model popover (rendered inside wrapper so it participates in the
          stacking context; positioned absolute relative to wrapper) */}
      {popoverOpen && (
        <ModelPopover
          anchorRef={modelButtonRef as React.RefObject<HTMLButtonElement>}
          selectedId={selectedModel.id}
          onSelect={handleModelSelect}
          onClose={() => setPopoverOpen(false)}
        />
      )}

      {/* Textarea (full width) — all controls live on the bottom row below */}
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
          width: "100%",
          boxSizing: "border-box",
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

      {/* Left button row: stubs + model selector */}
      <div
        data-testid="chat-input-left-row"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 2,
          marginTop: 4,
        }}
      >
        {/* Attach — disabled stub */}
        <LeftIconButton
          disabled
          title="Attach file (coming soon)"
          data-testid="chat-input-attach"
        >
          <IconPaperclip size={15} />
        </LeftIconButton>

        {/* Voice — disabled stub */}
        <LeftIconButton
          disabled
          title="Voice input (coming soon)"
          data-testid="chat-input-mic"
        >
          <IconMic size={15} />
        </LeftIconButton>

        {/* Mode toggle — disabled stub */}
        <LeftIconButton
          disabled
          title="Research mode toggle (coming soon)"
          data-testid="chat-input-mode"
        >
          {/* A small "M" label so the stub looks intentional without a dedicated icon */}
          <span
            aria-hidden="true"
            style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.03em" }}
          >
            Mode
          </span>
        </LeftIconButton>

        {/* Subtle divider */}
        <span
          aria-hidden="true"
          style={{
            width: 1,
            height: 14,
            background: "rgba(255,255,255,0.1)",
            margin: "0 4px",
            flexShrink: 0,
          }}
        />

        {/* Model selector trigger — we need the button's ref for popover
            positioning, so render the button directly here rather than
            through LeftIconButton (which is not a forwardRef component). */}
        <button
          ref={modelButtonRef}
          onClick={() => setPopoverOpen((o) => !o)}
          title={`Model: ${selectedModel.label}`}
          aria-label={`Model: ${selectedModel.label}`}
          data-testid="chat-input-model"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 28,
            height: 28,
            borderRadius: 6,
            border: "none",
            padding: 0,
            background: popoverOpen ? "rgba(255,255,255,0.08)" : "transparent",
            color: popoverOpen ? accentColor : "rgba(255,255,255,0.5)",
            cursor: "pointer",
            transition: "background 120ms ease, color 120ms ease",
            flexShrink: 0,
          }}
        >
          <IconModel size={15} />
        </button>

        {/* Active model label (dim, right of the icon) */}
        <span
          style={{
            fontSize: 11,
            color: accentColor,
            opacity: 0.75,
            fontWeight: 500,
            letterSpacing: "0.01em",
            userSelect: "none",
            pointerEvents: "none",
          }}
        >
          {selectedModel.label}
        </span>

        {/* Spacer pushes the send button to the right edge so the left
            controls sit INLINE with send (Claude-Code composer), NATE 2026-06-17. */}
        <div style={{ flex: 1 }} />

        {/* Send / stop — inline on the controls row, right-aligned. */}
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
    </div>
  );
}
