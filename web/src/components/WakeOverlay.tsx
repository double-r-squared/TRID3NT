// GRACE-2 web — "Wake up agent" overlay (auto-stop/wake infra, NATE 2026-06-17).
//
// The always-on AGENT box (EC2 t3.large) is now eligible to be STOPPED by an
// idle-check Lambda after N consecutive zero-connection polls. A stopped box
// answers nothing, so the WebSocket cannot connect until the box is started.
//
// This overlay is the WAKE-UP UIUX (per the reactivated `feedback_wake_up_agent
// _cold_start_ux` spec):
//
//   "'Wake up agent' rectangle over the chat panel when scaled to zero; tap ->
//    provision + shimmer/upward-wave + 'Waking up...' dots; fade on the ready
//    ping. Respect prefers-reduced-motion."
//
// Lifecycle (owned by the parent, App.tsx, which feeds `phase`):
//   - "hidden"  → connected (or below the failure threshold): render nothing.
//   - "asleep"  → the box appears stopped (consecutive WS failures past the
//                 threshold AND a wake endpoint is configured): show the
//                 idle rectangle with a "Wake up agent" call-to-action. Tapping
//                 it POSTs the wake endpoint (App wires `onWake`) and moves to…
//   - "waking"  → a wake POST is in flight OR the box is booting: shimmer /
//                 upward-wave animation + "Waking up…" animated dots. We also
//                 auto-enter this state when ws.ts has already fired its own
//                 wake POST (App passes phase="waking") so the user sees motion
//                 without having to tap.
//   - on the first successful WS frame / ready ping the parent flips `phase`
//     back to "hidden"; the overlay FADES OUT (opacity transition) rather than
//     hard-unmounting, so the transition reads as "the agent woke up".
//
// prefers-reduced-motion: the shimmer + upward-wave + dot animation are all
// suppressed; the overlay still shows static text ("Waking up…") so the state
// is communicated without motion.
//
// Pure presentational. No network I/O, no WebSocket coupling — the parent owns
// the wake POST (lib/wake.ts) and the phase machine. SSR-safe.

import { CSSProperties, useEffect, useRef, useState } from "react";

// --- Reduced-motion detection (SSR-safe) --------------------------------- //

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

// --- Keyframes (mounted once) -------------------------------------------- //
//
// Two motions per the spec:
//   - shimmer: a diagonal light sweep across the rectangle (the "provisioning"
//     feel), 2.4s linear.
//   - upward-wave: three stacked bars that rise + fade, staggered, evoking the
//     box "spinning up". 1.4s ease-in-out, staggered by child.
//   - dots: the classic "Waking up…" three-dot pulse, 1.4s steps.

const KEYFRAMES_ID = "grace2-wake-overlay-keyframes";

function ensureKeyframes(): void {
  if (typeof document === "undefined") return;
  if (document.getElementById(KEYFRAMES_ID)) return;
  const style = document.createElement("style");
  style.id = KEYFRAMES_ID;
  style.textContent = `
@keyframes grace2-wake-shimmer {
  0%   { background-position: -150% 0; }
  100% { background-position: 250% 0; }
}
@keyframes grace2-wake-rise {
  0%   { transform: translateY(6px) scaleY(0.55); opacity: 0.30; }
  50%  { transform: translateY(-2px) scaleY(1.0);  opacity: 1.00; }
  100% { transform: translateY(6px) scaleY(0.55); opacity: 0.30; }
}
@keyframes grace2-wake-dot {
  0%, 80%, 100% { opacity: 0.25; }
  40%           { opacity: 1.00; }
}
`;
  document.head.appendChild(style);
}

ensureKeyframes();

// --- Phase ---------------------------------------------------------------- //

export type WakePhase = "hidden" | "asleep" | "waking";

export interface WakeOverlayProps {
  /**
   * Overlay state, owned by App.tsx:
   *   - "hidden": connected / below failure threshold → renders nothing
   *     (after a brief fade-out if it was previously visible).
   *   - "asleep": box looks stopped → show the tap-to-wake rectangle.
   *   - "waking": a wake POST is in flight / the box is booting → shimmer +
   *     upward-wave + "Waking up…" dots.
   */
  phase: WakePhase;
  /**
   * Called when the user TAPS the "Wake up agent" rectangle. App.tsx wires this
   * to its AgentWaker (resets the debounce so a manual tap always fires) and
   * flips `phase` to "waking". No-op-safe: the overlay also calls it on Enter /
   * Space for keyboard users.
   */
  onWake: () => void;
}

/**
 * Fade duration (ms) for the opacity transition. Kept in sync with the inline
 * `transition` below; exported so tests can assert the value if needed.
 */
export const WAKE_FADE_MS = 420;

export function WakeOverlay({ phase, onWake }: WakeOverlayProps): JSX.Element | null {
  const reduced = prefersReducedMotion();

  // Keep the overlay mounted through the fade-out: when phase flips to "hidden"
  // we render one last frame at opacity 0, then unmount after the transition.
  const [mounted, setMounted] = useState<boolean>(phase !== "hidden");
  const unmountTimer = useRef<number | null>(null);

  useEffect(() => {
    if (unmountTimer.current !== null) {
      window.clearTimeout(unmountTimer.current);
      unmountTimer.current = null;
    }
    if (phase !== "hidden") {
      setMounted(true);
      return;
    }
    // phase === "hidden": fade out, then unmount. With reduced motion (no
    // transition) unmount immediately so we don't leave a click-blocking layer.
    if (reduced) {
      setMounted(false);
      return;
    }
    unmountTimer.current = window.setTimeout(() => {
      setMounted(false);
      unmountTimer.current = null;
    }, WAKE_FADE_MS);
    return () => {
      if (unmountTimer.current !== null) {
        window.clearTimeout(unmountTimer.current);
        unmountTimer.current = null;
      }
    };
  }, [phase, reduced]);

  if (!mounted) return null;

  const visible = phase !== "hidden";
  const waking = phase === "waking";

  // Full-cover overlay anchored to the chat panel container (App.tsx positions
  // the wrapper). We blur/dim the chat behind it so the rectangle reads as the
  // foreground call-to-action.
  const overlayStyle: CSSProperties = {
    position: "absolute",
    inset: 0,
    zIndex: 50,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
    background: "rgba(14,15,20,0.72)",
    backdropFilter: reduced ? undefined : "blur(2px)",
    WebkitBackdropFilter: reduced ? undefined : "blur(2px)",
    opacity: visible ? 1 : 0,
    transition: reduced ? undefined : `opacity ${WAKE_FADE_MS}ms ease`,
    // While fading out (not visible) stop intercepting clicks immediately so the
    // chat behind is interactive the moment the agent is back.
    pointerEvents: visible ? "auto" : "none",
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
  };

  // The rectangle. In "asleep" it's a button (tap to wake); in "waking" it's a
  // non-interactive status surface with the shimmer + upward-wave.
  const rectShimmer: CSSProperties =
    waking && !reduced
      ? {
          backgroundImage:
            "linear-gradient(110deg, rgba(56,140,255,0.10) 0%, rgba(56,140,255,0.10) 35%, rgba(120,180,255,0.32) 50%, rgba(56,140,255,0.10) 65%, rgba(56,140,255,0.10) 100%)",
          backgroundSize: "220% 100%",
          animation: "grace2-wake-shimmer 2.4s linear infinite",
        }
      : {};

  const rectStyle: CSSProperties = {
    position: "relative",
    overflow: "hidden",
    width: "min(320px, 84%)",
    minHeight: 132,
    borderRadius: 16,
    border: "1px solid rgba(120,170,255,0.35)",
    background: waking ? "rgba(24,32,52,0.92)" : "rgba(22,24,32,0.94)",
    boxShadow: "0 8px 30px rgba(0,0,0,0.45)",
    color: "#e7ecf5",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: 12,
    padding: "20px 18px",
    cursor: waking ? "default" : "pointer",
    textAlign: "center",
    ...rectShimmer,
  };

  const handleWake = (): void => {
    if (waking) return; // already waking — tapping again is a no-op
    onWake();
  };

  return (
    <div
      data-testid="wake-overlay"
      data-phase={phase}
      style={overlayStyle}
      // Block click-through to the (dead) chat while asleep/waking.
      aria-hidden={!visible}
    >
      <div
        data-testid="wake-overlay-rect"
        role={waking ? "status" : "button"}
        aria-live={waking ? "polite" : undefined}
        tabIndex={waking ? -1 : 0}
        aria-label={waking ? "Waking up agent" : "Wake up agent"}
        onClick={waking ? undefined : handleWake}
        onKeyDown={
          waking
            ? undefined
            : (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  handleWake();
                }
              }
        }
        style={rectStyle}
      >
        {waking ? (
          <>
            <UpwardWave reduced={reduced} />
            <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: 0.2 }}>
              Waking up
              <WakingDots reduced={reduced} />
            </div>
            <div style={{ fontSize: 12, color: "#9aa6bd", maxWidth: 240 }}>
              Starting the agent — this can take a minute on a cold start.
            </div>
          </>
        ) : (
          <>
            <div aria-hidden="true" style={{ fontSize: 26, lineHeight: 1 }}>
              {/* simple "sleeping" glyph, no external icon dep */}
              <PowerGlyph />
            </div>
            <div style={{ fontSize: 16, fontWeight: 700 }}>Wake up agent</div>
            <div style={{ fontSize: 12, color: "#9aa6bd", maxWidth: 240 }}>
              The agent went to sleep to save costs. Tap to start it back up.
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// --- Upward-wave (three rising bars) ------------------------------------- //

function UpwardWave({ reduced }: { reduced: boolean }): JSX.Element {
  const bar = (i: number): CSSProperties => ({
    width: 6,
    height: 22,
    borderRadius: 3,
    background: "linear-gradient(180deg, #8fc0ff 0%, #4f8fff 100%)",
    transformOrigin: "bottom center",
    animation: reduced
      ? undefined
      : `grace2-wake-rise 1.4s ease-in-out ${i * 0.18}s infinite`,
    // Static fallback under reduced motion: mid-height, full opacity.
    opacity: reduced ? 0.85 : undefined,
  });
  return (
    <div
      data-testid="wake-upward-wave"
      aria-hidden="true"
      style={{ display: "flex", alignItems: "flex-end", gap: 6, height: 26 }}
    >
      <span style={bar(0)} />
      <span style={bar(1)} />
      <span style={bar(2)} />
    </div>
  );
}

// --- "Waking up…" dots --------------------------------------------------- //

function WakingDots({ reduced }: { reduced: boolean }): JSX.Element {
  if (reduced) {
    // Static ellipsis under reduced motion.
    return <span aria-hidden="true">…</span>;
  }
  const dot = (i: number): CSSProperties => ({
    animation: `grace2-wake-dot 1.4s ${i * 0.2}s infinite`,
  });
  return (
    <span aria-hidden="true" style={{ marginLeft: 2 }}>
      <span style={dot(0)}>.</span>
      <span style={dot(1)}>.</span>
      <span style={dot(2)}>.</span>
    </span>
  );
}

// --- Power glyph (inline SVG; no icon-module dep) ------------------------ //

function PowerGlyph(): JSX.Element {
  return (
    <svg
      width="26"
      height="26"
      viewBox="0 0 24 24"
      fill="none"
      stroke="#8fc0ff"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 3v9" />
      <path d="M6.5 6.5a8 8 0 1 0 11 0" />
    </svg>
  );
}
