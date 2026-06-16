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

## Sequencing (avoid cross-panel regressions — the panels share App.tsx / LayerPanel / Map.tsx)
**Stage 1 — layout shell (do first; everything else builds on it)**
- **J1 (web):** drag-to-resize chat (left border) + Layers panel (right border); remove the expansion toggle. (F10, F11)

**Stage 2 — viz/report subsystem (depends on J1)**
- **J2 (web):** new collapsible+scrollable **Visualizations/Reports section** under Layers; items named/timestamped/NEW-badged; selecting opens a full-screen popup overlay; **Pelicun report + charts persist here** instead of flashing in chat; retire in-chat viz. (F1–F6)

**Stage 3 — layers-panel content (after J2; same files → sequence, don't parallelize)**
- **J3 (web):** opacity slider value↔position fix (F8); layer-name readability (F7); layers list scrollable (F9); legend bundled-with-layer + hides-with-layer (F13).

**Stage 4 — isolated (parallel-safe; different files)**
- **J4 (web):** Sign out → Settings. (F12)
- **J5 (web):** reset AOI/bbox overlay on Case exit (client replace-not-reconcile). (F14)
- **J6 (agent):** strip emojis from narration (adapter SYSTEM_PROMPT + output guard). (F15)

## SRS-amendment-worthy (design decisions, not just bugfixes) — propose + land with NATE's go
- Resize-by-drag replaces panel-expansion (FR-WC / FR-MP-6 Case UX).
- New Visualizations/Reports panel + persisted-report popup pattern (new FR-WC requirement; reports are first-class alongside layers).
- Legend bundled-with-layer lifecycle (FR-WC).
- No-emoji narration (Appendix I harness / FR-AS narration discipline).

## Overlap / risk notes
J1→J2→J3 all touch the two-pane shell + LayerPanel → **strictly sequential**, re-verify the panel after each. J4/J5/J6 are isolated and safe to interleave. Re-demo after Stage 2 (the headline: report persistence) rather than waiting for all stages.
