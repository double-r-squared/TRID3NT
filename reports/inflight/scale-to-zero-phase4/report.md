# Scale-to-zero Phase 4 — agent memory diet on live evidence (blueprint 2.4)

Date: 2026-07-06

## Evidence

Added peak/current RSS to the 60s route-row heartbeat log line (d7de207,
`hb-rss peak_mb=... cur_mb=...` in /grace2/agent-isolation/agent). A full
judge-code Haiku flood turn on the new image (offloaded SFINCS solve, in-agent
deck/postprocess) measured:

    hb-rss peak_mb=981  cur_mb=956   (post-import baseline, turn starting)
    hb-rss peak_mb=1209 cur_mb=1181  (mid-turn)
    hb-rss peak_mb=1377 cur_mb=1377  (solve polling)
    hb-rss peak_mb=1919 cur_mb=1832  (deck build / publish peak)
    hb-rss peak_mb=1919 cur_mb=1745  (idle after the turn)

## Decision

Task memory 8192 -> **6144** MiB (task-def rev 15), NOT the blueprint's 4096:
peak 1.9 GB on the SMALLEST case leaves 4 GB too tight for a large-AOI
deck/postprocess (the 2 GB Chattanooga OOM, exit 137, is the precedent).
6144 = 3.2x observed peak and ~25% off the per-session memory rate. 4096
stays the target once the heavy-compute-offload track moves the deck
BUILD/postprocess off-agent; the hb-rss line now provides the continuous
evidence stream to re-check.

## Gate

Flood smoke PASS on the 6 GB task-def: sfincs_pluvial_flood, case
01KWW28PFF0AD5YGB43JGYVRA1, 279.4s. Rollback = revert variables.tf + apply
(previous rev 14 task-def is retained by ECS).
