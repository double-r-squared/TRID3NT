# Kickoff (frozen)

job-0231-web-20260609 — chart inline stacked preview + gallery popup (sprint-13 Stage 2)

## Common rules (GRACE-2 sprint-13 Stage 2)
Working dir: /home/nate/Documents/GRACE-2
Read first: agents/AGENTS.md, your specialist file in agents/, reports/sprints/sprint-13-manifest.md (your job scope), reports/PROJECT_STATE.md.
FIRST ACTION: mkdir -p reports/inflight/<job-id>/ ; write audit.md with this kickoff verbatim under "# Kickoff (frozen)"; STATE file "RUNNING".
- NO Gemini/Vertex generate_content calls. Hard rule. All live evidence is produced programmatically (direct Python invocation, local mf6 binary, vitest/pytest) — never through the chat loop.
- NEVER git push. Commit locally at job end: git add <only your files> && git commit -m "<job-id>: <title>". index.lock conflicts: wait 5s, retry 5x.
- SHARED REGISTRATION FILES WARNING: other Stage 2 agents are concurrently editing services/agent/src/grace2_agent/tools/__init__.py, catalog.py, categories.py, and adapter.py. Re-read each shared file IMMEDIATELY before editing it; keep edits surgical (single anchor); if an Edit fails on a stale anchor, re-read and retry.
- Environment: no docker daemon, no gcloud on this box; mf6 6.5.0 static binary is downloadable and runs locally (see reports/inflight/job-0220-infra-20260609/evidence/mf6_smoke.log); tofu validate only.
- Python venv: services/agent/.venv. Web: npx vitest in web/.
- Report honestly; PARTIAL with documented blockers beats fake success.
- AT JOB END: write reports/inflight/<job-id>/report.md, STATE "READY_FOR_AUDIT".
Return StructuredOutput.

## Inputs in force
- chart-emission WS envelope now emitted by the agent (job-0230 just landed — read its evidence/ Vega-Lite specs as fixtures)
- ChartEmissionPayload shape in packages/contracts chart_contracts.py
- ws.ts envelope-routing convention (see the impact-envelope case added today) + SESSION_SCOPED_TYPES fan-out set

## Scope
1. npm install vega-embed (+ vega, vega-lite peers) in web/.
2. web/src/components/ChartStack.tsx (NEW): inline stacked preview in the chat scroll — top chart ~200x150px, subsequent charts stacked behind with ~4px offset, "+N" badge when >3 in stack. Stack grouping key: created_turn_id (same turn -> same stack). Click opens gallery.
3. web/src/components/ChartGallery.tsx (NEW): full-viewport overlay, prev/next arrows, save-as-PNG (vega-embed export API), Esc/backdrop close. Match the dark theme + existing modal conventions (read RoutingQualityDashboard.tsx for the modal pattern).
4. Routing: add chart-emission to ws.ts envelope union + SESSION_SCOPED_TYPES, onChartEmission handler, App.tsx state (charts accumulate per session; Case switch resets per the replace-not-reconcile client-side rule), render hook in the chat message flow alongside tool cards (read how impact-envelope/tool cards interleave — ChatMessage/Chat component).
5. Rehydration: charts array on session rehydrate repopulates the stacks.

## Acceptance
- vitest: ws routing (well-formed -> handler; malformed dropped with console.warn), stack grouping by created_turn_id, +N badge logic, gallery open/nav/close, rehydration repopulation. Use job-0230's evidence specs as fixtures.
- Playwright SCREENSHOT (UI-only, dev seam PERMITTED per the bundle-UI-verification rule — this is a snapshot, not live verification): inject 4 chart payloads (2 same-turn stacked + 2 single) via a __grace2InjectChartEmission dev seam you add (mirror __grace2InjectImpactEnvelope), capture (a) inline stacks in chat, (b) gallery open. Save to reports/inflight/<job-id>/evidence/. Live Gemini-driven verification is Stage 3 scope (job-0237) — do NOT drive Gemini.

## File ownership
web/src/components/ChartStack.tsx, ChartGallery.tsx, their tests, ws.ts + App.tsx + chat-render surgical wiring, web/package.json (vega deps).
