# job-0283 — web — desktop sleekness pass (kickoff, frozen)

Specialist: web (Fable runner, MAX effort). Dispatched 2026-06-11.

## Mission

Extend the job-0280 sleekness pass to DESKTOP: apply the same visual
de-clutter the mobile drawer received — "more sleek but retain 100%
functionality." Conservative polish ONLY; zero behavior changes.

## Scope (apply the job-0264/job-0280 design language)

1. Left rail (CasesPanel + CaseView/LayerPanel): consistent corner radii and
   spacing, remove double borders/nested boxes, panels lay flat into their
   surface, consistent typography scale. Keep every control, id, behavior.
2. Chat panel: same surface treatment — header, message area, composer share
   consistent radii/spacing; no redundant separators.
3. Bottom-row pills + LayerLegend + hamburgers: consistent
   sizing/radius/contrast family.
4. Modals/popups (Settings, Secrets, Tools, SaveGate, ConfirmationDialog):
   align radii/spacing with the same family — visual only.
5. Both themes (light/dark map theme): check contrast holds in both.

## DO NOT

- Move/remove ANY control; change layout structure; touch stream logic;
  alter data-testids; change mobile rendering (job-0280's mobile surfaces
  are the reference, not the target); restart anything (HMR live).

## Constraints

- Web only. `git add` only files touched. SettingsPopup.tsx carries a
  PRE-EXISTING uncommitted modification (Tools section) — leave it
  unstaged; if edited, stage surgically.
- No Gemini prompts; static screenshots only (dev-seam injections fine).

## Acceptance

- Full web vitest green (638 baseline; zero regressions; style-test updates
  deliberate + noted).
- Before/after desktop screenshots (1440x900): root, in-Case populated
  (session-state dev seam), chat with cards (pipeline-state seam), light +
  dark. Mobile control screenshot proving mobile unchanged. Under
  reports/inflight/job-0283-web-20260611/evidence/.
- audit.md + report.md + STATE=DONE; commit "job-0283: ..." with
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>.

## Design tokens adopted (the job-0264 LayerPanel polish = reference)

- Panel surface: linear-gradient(180deg, rgba(26,27,33,0.96), rgba(18,19,24,0.96))
- Panel border: 1px solid rgba(255,255,255,0.06); radius 12
- Panel shadow: 0 8px 32px rgba(0,0,0,0.5); backdrop blur 6px
- Inner rows: rgba(255,255,255,0.03) resting / hairline border / radius 8
- Active row: rgba(59,130,246,0.16) + border rgba(59,130,246,0.55)
- Modal border: 1px solid rgba(255,255,255,0.10); radius 12; dividers
  rgba(255,255,255,0.08); buttons radius 8
- Floating pills/buttons: rgba(18,19,24,0.92) + hairline rgba(255,255,255,0.08)
  + blur; pills radius 999; hamburgers radius 10

## Mobile-isolation strategy

Left-rail component restyles ride a NEW `.grace2-desktop-rail` CSS scope in
global.css (class applied only by App.tsx's desktop wrappers) — the exact
inverse of job-0280's `.grace2-mobile-touch` pattern — so CasesPanel /
CaseView / CaseRow inline styles stay byte-identical and the mobile drawer
renders pixel-identical. Chat desktop container + hamburgers + floating-pill
variant are desktop-only code paths edited inline. LayerLegend + modals are
form-factor-shared; their family alignment deliberately applies to both
(they are NOT job-0280 drawer/sheet surfaces) — noted in report.
