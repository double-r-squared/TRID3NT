# UX feedback — batch 1 (post flood→Pelicun manual test)

**Source:** NATE ALMANZA live test of facet A (flood → Pelicun), 2026-06-16.
**Process:** accumulate → sequence carefully (cross-panel regression risk) → land a set → re-demo → next batch → coalesce overlaps. This file is the living accumulation for batch 1.

## Positives (no action — keep)
Flood sim ran clean; AOI bbox visible during the run; Case persistence solid (chat + layers rehydrate); Case auto-naming works well.

## Captured items
| # | Item | Area | Type |
|---|---|---|---|
| F1 | Pelicun ImpactPanel flashed then was unrecoverable after navigating away — must **persist** | viz/report | bug+feature |
| F2 | Persist the Pelicun **report** as a selectable **stub with preview**; opens a **full-screen popup overlay** to read fully (like the one-off graph pattern, but it's a report) | viz/report | feature |
| F3 | New **"Visualizations / Reports" section** under Layers — list of graphs/reports/supplementary visuals, selectable | viz panel | feature |
| F4 | **Drop in-chat visualizations**; route all graphs/reports to the new section (approach markdown-in-chat carefully so nothing breaks) | viz panel | feature |
| F5 | Viz items: **named + timestamped + "NEW" badge** until viewed | viz panel | feature |
| F6 | Viz section **collapsible** | viz panel | feature |
| F7 | 2nd layer name unreadable/truncated (`fort-myers-100yr-flood-depth…`); only flood-depth visible — naming/readability | layers panel | bug |
| F8 | **Opacity slider bug**: thumb starts centered but labeled 0% (value↔position mismatch; center reads 49%) | layers panel | bug |
| F9 | Layers list must become **scrollable** as layers accumulate | layers panel | feature |
| F10 | **Resizable chat**: drag the chat's **left border** to size it; **drop the large/normal expansion button** | layout | feature |
| F11 | **Resizable Layers panel**: drag its **right border** | layout | feature |
| F12 | Move **Sign out** into the **Settings** page | settings | bug |
| F13 | **Legend/key bundled with its layer**, shown at bottom of the AOI; **hides when the layer hides** (multi-key stacking: defer — use top-of-stack layer's key) | layers/map | feature |
| F14 | **AOI bbox persists after exiting the Case** (client-state not reset on Case exit) | client state | bug |
| F15 | **No emojis** in agent output | agent | bug |
| F16 | **Payload warning → opaque banner "hat"** pinned directly above the chat window (out of the scroll so you can't scroll away from it); same contents, not transparent; dismisses on feedback. Drop the top-of-chat overlay placement | chat/layout | feature |
| F17 | **Follow-ups in an existing Case recompute from scratch** — asked for a hillshade in Fort Myers, it re-ran the flood. ROOT CAUSE: on case-open the server resets the LLM conversation (`state.chat_history = []`, job-0245 cross-CASE fix) and re-seeds only layers + URI registry, NOT the model's memory of prior work. Fix: rehydrate the LLM history (text transcript) from the persisted PER-CASE messages on case-open (inherently case-correct → no job-0245 regression) + optionally inject a compact "layers already present" list | agent harness | bug (significant) |
| F18 | **New tool cards render behind the last prompt** instead of at the end of chat — the work-in-progress card sits above the latest user message, breaking the sense of continuity. New tool cards must append after the most recent prompt | chat ordering | bug |
| F19 | (refines F1-F3) Impact **report belongs in the new graphs/reports section in the BOTTOM HALF of the LEFT panel** (with the layers), and the popup opens **CENTERED in the browser window**; re-accessible from that section | viz/report | feature |
| F20 | **Follow-ups should reuse the original run's AOI bbox** for a cohesive picture (for data sources that support extent). Ties to F17: if the LLM remembered the run it would reuse the AOI. Mechanism: carry the Case's AOI bbox into the LLM context + nudge tools to reuse it | agent harness | feature |
| F21 | **Hillshade extent spills just outside the AOI bbox.** CORRECTED (NATE 2026-06-16): orientation is PERFECT — it aligns to the flood + basemap, NOT sideways. The only issue is the rendered area is slightly larger than the AOI: compute_hillshade ran on the full fetched DEM tile rather than being clipped to the AOI bbox/polygon first. Fix = clip DEM (or hillshade output) to the AOI before publish; ties to F20 (reuse AOI). Low urgency | engine/agent | bug (clip-to-AOI) |
| F22 | (refines F7/F8) **"Connected sliders"** — the recompute created a 2nd layer with the SAME NAME, and the opacity sliders moved together (keyed by name, not layer_id). J3 must key controls by layer_id; F17 fix reduces duplicate creation | layers panel | bug |
| F23 | **DEFERRED (later sprint):** "AOI-only mode" — show only the AOI extent, drop the basemap, swap in something like a land-cover backdrop. Should compose easily now that experiments are bbox-bound. Do NOT build now (don't inundate the in-flight batch) | map/viz | feature (deferred) |
| F24 | **DEFERRED (→ sprint-16 QGIS substrate):** layer **blending modes** exposed in the layer section / to the AI (QGIS Server WMS gives these free). Flagship: blend road overlay + flood map so submerged-road segments render RED while dry roads keep their color — precision opacity can't give. This is a core "why QGIS Server" capability. SRS-amendment-worthy | layers/render | feature (deferred) |
| F25 | **DEFERRED (→ post sprint-16):** **shelter/evacuation routing** — hazard-AGNOSTIC routing to shelters/hospitals/schools that flags roads likely to close (from blended flood+road data) vs clear, plots viable routes, surfaces bottlenecks. Uses QGIS network-analysis algorithms over the blended hazard data. Reusable across disaster types. SRS-amendment-worthy (new flagship use case) | engine/routing | feature (deferred) |

## Round-3 additions (2026-06-16, post-deploy)
- **F26a (BUG — case routing): retry message created a NEW Case instead of continuing.** After a gated execution timed out, NATE typed a retry into chat and it spawned a NEW Case rather than continuing the active one — i.e. he was dropped out of the active Case (so the message routed to Cases-root, which auto-creates a Case per the per-Case-stream design). Likely cause: the timeout/error reset client active-case state (same client-state-reset theme as [[feedback_wave48_known_bugs]]). Investigate: does a gate-timeout/error clear activeCaseId? Should NOT. Candidate for this batch or next.
- **F26b (FEATURE, NEXT SPRINT per user): retry mechanic + natural-language retry.** Want both (1) an explicit "retry this timed-out run and continue" control on the gate/card, AND (2) the ability to just ASK the agent to retry in chat and have it re-run the failed/timed-out step. Today: tool errors feed back to the agent (tool-retry-on-failure) and with J8 live re-sending is cheap (no recompute), but there's no first-class retry affordance. Need the repro (which gate/engine; gate-expiry vs solver-timeout). [[feedback_tool_retry_on_failure]]

- **F27 (gate UX): per-Case "Don't ask again" on the confirmation/payload gate.** The gate fires on every map-making run, interrupting AFK work. Want: keep "Proceed anyway" AND add a "Don't ask me again" button that sets a PER-CASE suppress flag (so the user controls where gating is on/off). Near-term = coarse per-Case boolean; fine-grained (per-tool / size-threshold / time-boxed) DEFERRED per user. Applies to the payload-warning gate and/or solver-confirm gate — confirm which the user means (the >25MB payload warning vs the solver confirm card). Good fit alongside J7 (the banner) since both touch the gate UI.

## DEMO-BLOCKING (user gate 2026-06-16)
NATE will run no further demos until the **duplicate-layer + LLM recompute** fixes land. Therefore **J8 (F17 LLM history)** and **J3 (F22 layer-id keying / dedupe)** are PROMOTED ahead of J2. These two unblock demos:
- J8 stops the recompute (so no duplicate same-name layer is created).
- J3 keys every layer control by layer_id (so even if duplicates exist, sliders don't sync) + dedupes same-name layers.

## Sequencing (avoid cross-panel regressions — the panels share App.tsx / LayerPanel / Map.tsx)
**Stage 1 — layout shell (do first; everything else builds on it)**
- **J1 (web):** drag-to-resize chat (left border) + Layers panel (right border); remove the expansion toggle. (F10, F11)

**Stage 2 — viz/report subsystem (depends on J1)**
- **J2 (web):** new collapsible+scrollable **Visualizations/Reports section** under Layers; items named/timestamped/NEW-badged; selecting opens a full-screen popup overlay; **Pelicun report + charts persist here** instead of flashing in chat; retire in-chat viz. (F1–F6)

**Stage 3 — layers-panel content (after J2; same files → sequence, don't parallelize)**
- **J3 (web):** opacity slider value↔position fix (F8); layer-name readability (F7); layers list scrollable (F9); legend bundled-with-layer + hides-with-layer (F13).

**Stage 4 — isolated (parallel-safe; different files)**
- **J4 (web):** Sign out → Settings. (F12) — DONE (committed)
- **J5 (web):** reset AOI/bbox overlay on Case exit (client replace-not-reconcile). (F14) — DONE (committed)
- **J6 (agent):** strip emojis from narration (adapter SYSTEM_PROMPT + output guard). (F15) — DONE (committed)

**Round-2 additions (from live testing 2026-06-16, after J1 started)**
- **J7 (web, F16):** payload-warning banner "hat" pinned above the chat window. Touches the chat-area shell → sequence right after J1 (shares the chat container + the lifted chatWidth). Rides on J1's chatWidth single-source-of-truth so the banner aligns to the chat column.
- **J8 (agent, F17):** rehydrate per-Case LLM history on case-open so follow-ups see prior work (no recompute). Agent-harness behavior change → gate with the 4-lens adversarial-verify panel (high-importance, routing-sensitive — job-0245 territory). Parallel-safe with web jobs. SRS-amendment-worthy (FR-AS context/continuity).
- **J9 (web, F18):** new tool cards append after the most recent prompt (fix the behind-last-prompt ordering). Chat stream/interleave (Chat.tsx) → sequence with the J1/J7 chat work to avoid churn on the same file.
- **F19** folds into **J2**: graphs/reports section lives in the BOTTOM HALF of the LEFT panel (below layers); report popup opens CENTERED; re-accessible. (Updates J2's spec — was "under Layers"; now explicitly bottom-half + centered popup.)

## SRS-amendment-worthy (design decisions, not just bugfixes) — propose + land with NATE's go
- Resize-by-drag replaces panel-expansion (FR-WC / FR-MP-6 Case UX).
- New Visualizations/Reports panel + persisted-report popup pattern (new FR-WC requirement; reports are first-class alongside layers).
- Legend bundled-with-layer lifecycle (FR-WC).
- No-emoji narration (Appendix I harness / FR-AS narration discipline).

## Overlap / risk notes
J1→J2→J3 all touch the two-pane shell + LayerPanel → **strictly sequential**, re-verify the panel after each. J4/J5/J6 are isolated and safe to interleave. Re-demo after Stage 2 (the headline: report persistence) rather than waiting for all stages.
