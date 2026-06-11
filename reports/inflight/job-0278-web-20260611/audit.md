# job-0278-web-20260611 — kickoff (frozen, verbatim)

You are a Fable specialist runner on GRACE-2 (job-0278, web specialist, MAX effort). Work in /home/nate/Documents/GRACE-2. Your kickoff here is authoritative and frozen.

MISSION (user-directed, verbatim): "make a mobile friendly version of the app (purely UI)". The user will open the app on their phone via Tailscale at http://100.92.163.46:5173 (Vite dev server, HMR live). Desktop layout must remain EXACTLY as it is today above the mobile breakpoint.

CONTEXT — the app (web/src/):
- App.tsx: two-pane shell — left rail (CasesPanel ⇄ Case detail with LayerPanel) over a full-viewport MapLibre map (Map.tsx), Chat.tsx as a right-side overlay panel (~470px), bottom-left [⚙ Settings][🔑 Secrets] pills, modals (SaveGateModal, ConfirmationDialog, popups) already centered with backdrop+Escape dismissal.
- Styling is predominantly INLINE style objects (React.CSSProperties), not CSS files — so responsive behavior should come from a viewport hook (window.matchMedia) driving conditional style branches, NOT media-query stylesheets bolted on. Add a small shared hook (e.g. web/src/hooks/useIsMobile.ts, breakpoint 768px, SSR-safe, listens for changes).
- The chat input is data-testid="chat-input"; per-Case streams + envelope case-tag routing (job-0277) just landed in Chat.tsx — do NOT restructure its stream logic, only its presentation.

MOBILE SHAPE (target: phone portrait ~390x844):
1. Map stays full-screen underneath everything.
2. Left rail becomes a slide-in drawer: hidden by default, opened via a hamburger/cases button (top-left, ≥44px touch target), full-height overlay with its own backdrop; tapping a Case or the backdrop closes it. The in-Case LayerPanel rides in the same drawer.
3. Chat becomes a bottom sheet: collapsed state = the composer pinned at the bottom (full width); expanded state = ~70% viewport height sheet with the conversation; a drag-handle / chevron toggles. Keep the existing scroll/auto-scroll behavior inside.
4. Touch targets ≥44px for all interactive elements on mobile (case rows, buttons, slider thumb already 16px box — bump hit area on mobile only if trivial).
5. Settings/Secrets pills: fold into a single overflow button or keep but ensure they don't collide with the drawer button.
6. Modals/popups: ensure they fit 390px width (max-width: calc(100vw - 32px)).
7. index.html: verify/add the viewport meta tag (width=device-width, initial-scale=1).

CONSTRAINTS:
- WEB ONLY. Do NOT touch services/agent, packages/contracts, infra, or docs/srs.
- Do NOT restart anything (Vite HMR delivers your changes; the agent process is live and serving the user — leave it alone).
- Do NOT run Playwright against the live agent or send any chat prompts (Gemini quota is user-reserved). Static screenshots of the UI shell at mobile viewport WITHOUT sending prompts are allowed (page load + drawer/sheet toggling only).
- Desktop (≥768px) renders pixel-identical to today — guard every mobile branch behind the hook.
- The working tree has unrelated uncommitted changes — `git add` ONLY files you created/edited; NEVER `git add -A`.

ACCEPTANCE:
- Full web vitest suite green: `cd web && npx vitest run` — currently 590 passing; ZERO regressions. Add tests for the hook + key mobile branches (drawer open/close state, bottom-sheet toggle) using the established pure-helper/component test patterns.
- Static mobile screenshots (390x844): root view with collapsed sheet, drawer open, chat sheet expanded — saved under reports/inflight/job-0278-web-20260611/evidence/.
- Write reports/inflight/job-0278-web-20260611/{audit.md,report.md,STATE} (STATE=DONE) and commit locally with a message starting "job-0278:" ending "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>".

Return as final text: commit hash, files changed, test counts, screenshot paths, and any risks/compromises the orchestrator must know.
