// GRACE-2 web — useSaveGate hook (job-0143, sprint-12-mega Wave 4).
//
// Intercepts save-triggering actions for anonymous users and surfaces a
// one-shot inline disclaimer at the moment of attempt rather than blanket
// "Sign in to save" copy on every render. Replaces the always-visible
// "Sign in to save" PersistenceChip removed in job-0143.
//
// The hook returns:
//   - `gateAction(actionFn)`: wraps an action so it either runs immediately
//     (signed-in) or surfaces the save-gate modal (anonymous).
//   - `pendingAction`, `isOpen`: drive the modal render.
//   - `confirmContinue()`: dismiss the modal and run the pending action.
//   - `requestSignIn()`: invokes the sign-in callback and clears the modal.
//   - `dismiss()`: cancel the gated action.
//
// Invariants honored:
//   - 8 (cancellation is first-class): every gated action can be cancelled
//     without consequence — `dismiss()` runs no callback.
//   - 9 (no cost theater): copy refers to persistence only, never cost.

import { useCallback, useState } from "react";

export interface UseSaveGateOptions {
  /** Whether the active user can persist work (Firebase non-anonymous). */
  isSignedIn: boolean;
  /** Invoked when the user clicks "Sign in" inside the gate. */
  onSignInRequest: () => void;
}

export interface UseSaveGateReturn {
  /** Wrap an action so it gates on save-capability. */
  gateAction: (action: () => void, kind?: string) => () => void;
  /** True while the modal is visible. */
  isOpen: boolean;
  /** Friendly label for the action being gated (e.g. "Create a new Case"). */
  pendingKind: string | null;
  /** Cancel the gate (run nothing). */
  dismiss: () => void;
  /** Dismiss the gate AND run the pending action ("Continue anyway"). */
  confirmContinue: () => void;
  /** Dismiss the gate AND invoke `onSignInRequest`. */
  requestSignIn: () => void;
}

/**
 * Intercept save-triggering actions for anonymous users.
 *
 * Usage in App.tsx:
 *
 *   const saveGate = useSaveGate({ isSignedIn, onSignInRequest: handleSignIn });
 *   <CasesPanel onCreate={saveGate.gateAction(createCase, "Create a new Case")} />
 *   {saveGate.isOpen && <SaveGateModal {...saveGate} />}
 */
export function useSaveGate(opts: UseSaveGateOptions): UseSaveGateReturn {
  const { isSignedIn, onSignInRequest } = opts;
  const [pendingAction, setPendingAction] = useState<(() => void) | null>(null);
  const [pendingKind, setPendingKind] = useState<string | null>(null);

  const gateAction = useCallback(
    (action: () => void, kind: string = "Save your work") =>
      () => {
        if (isSignedIn) {
          action();
          return;
        }
        // Anonymous user — defer the action behind the gate.
        setPendingAction(() => action);
        setPendingKind(kind);
      },
    [isSignedIn],
  );

  const dismiss = useCallback(() => {
    setPendingAction(null);
    setPendingKind(null);
  }, []);

  const confirmContinue = useCallback(() => {
    const a = pendingAction;
    setPendingAction(null);
    setPendingKind(null);
    if (a) a();
  }, [pendingAction]);

  const requestSignIn = useCallback(() => {
    setPendingAction(null);
    setPendingKind(null);
    onSignInRequest();
  }, [onSignInRequest]);

  return {
    gateAction,
    isOpen: pendingAction !== null,
    pendingKind,
    dismiss,
    confirmContinue,
    requestSignIn,
  };
}
