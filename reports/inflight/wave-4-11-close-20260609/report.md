# Wave 4.11 Close — MongoDB MCP + Pelicun Impact

**Date**: 2026-06-09
**Outcome**: CLOSED with M4 + P5 deferred. Substantial substrate landed; both carry-overs documented for sprint-13.5.

## Headline

Wave 4.11 landed two full workstreams with the third (originally wildfire) folded into Wave 4.10:

**Pelicun impact post-processing — END-TO-END SHIPPED**:
- `ImpactEnvelope` Pydantic contract (Decision N / SRS B.6c.1) — 47 tests
- `postprocess_pelicun` atomic tool — 18 tests, takes Pelicun damage FGB → ImpactEnvelope
- `compute_impact_envelope` workflow composer — 13 tests, chains geocode → NSI → Pelicun → postprocess → narrative emission
- `ImpactPanel` web component — 23 tests, slide-out panel rendering envelope with headline cards + DS bars + occupancy table + provenance footer
- `impact-envelope` WS routing wire (follow-up) — connects composer output to ImpactPanel in production

**MongoDB MCP — substrate + headline telemetry use case shipped**:
- `infra/mongo-mcp/` ops scaffolding (README + start.sh + .env.example)
- 3 Pydantic collection schemas: `ToolCallTelemetryDocument`, `DescriptionAuditDocument`, `CaseTelemetryDocument`
- Telemetry writer routed through Persistence singleton → MongoDB MCP when bound, falls back to local-file otherwise (Wave 4.10 v0 path preserved)
- `discover_dataset` 4th channel — co-occurrence boost from `tool_call_telemetry` collection via RRF
- `get_dynamic_hot_set(user_id)` — M6 substrate reading top-K from telemetry, integrated into post-hoc validator (M6 wire-up) behind `GRACE2_DYNAMIC_HOT_SET=1` flag
- `RoutingQualityDashboard` web component + `/api/telemetry/summary` HTTP endpoint — Settings → "Routing quality" shows 4 KPIs + top-15 bar chart + per-tool table + recent chains pills + 30s auto-refresh

## Wave plan: per-job status

| Job | Workstream | Status | Notes |
|---|---|---|---|
| P1 | ImpactEnvelope schema | ✓ | landed via doc track yesterday |
| P2 | `postprocess_pelicun` | ✓ | 18 tests, ImpactEnvelope output validates |
| P3 | `compute_impact_envelope` composer | ✓ | 13 tests, narrative format locked |
| P4 | ImpactPanel web | ✓ | 23 tests, dev seam + production WS wire (follow-up) |
| P5 | Live demo acceptance | ⏸ deferred | Gemini-burner; defer to sprint-13.5 or run alongside it |
| M1 | MongoDB MCP infra | ✓ | discovered substrate was already more wired than manifest assumed |
| M2 | Atlas index/validator design | ✓ | landed transitively via M1 (schemas + tests) |
| M3 | Telemetry → Mongo writer | ✓ | 20 tests, Persistence-singleton routing, fixed pre-existing test flakiness |
| M4 | Cases/sessions/users/secrets_refs CRUD | ⏸ deferred | Adversarial-gated, high-stakes (touches every chat session's persistence) — defer to sprint-13.5 alongside auth migration |
| M5 | `discover_dataset` live-Mongo | ✓ | 4th channel + 5-min refresh + graceful fallback |
| M6 | Hot-set live-Mongo query | ✓ | substrate from M5, wire-up follow-up under `GRACE2_DYNAMIC_HOT_SET=1` flag |
| M7 | Routing-quality dashboard | ✓ | Settings → "Routing quality"; auto-refresh + Mongo→file fallback |

## What didn't go cleanly

1. **M1 manifest assumption was wrong.** The wave plan said "provision MCP server + Atlas connection wiring" — but the infra was already mostly wired. M1 became scaffolding + schemas instead of bootstrap. Net: less code than budgeted, but Wave 4.11 lost no real ground.

2. **`AtomicToolMetadata` doesn't carry `category`.** P3 wanted `category="damage_assessment"` on the composer's metadata; the registry's convention is to surface category via MCP annotation hints + the separate `tool_category` vocabulary in `grace2_contracts`. Resolved by setting the annotation hints exactly; category-on-registry remains a future schema amendment if needed.

3. **`compute_impact_envelope` ttl_class deviation.** Design doc said `static-30d`; landed `live-no-cache` to match the workflow_dispatch pattern used by every other composer. Caching is delivered transitively by `static-30d` ttls on the underlying atomic steps (NSI, Pelicun damage, postprocess_pelicun). Documented inline.

## Architectural composition (Wave 4.10 + 4.11)

Combined effect of both waves: the agent now has the full Pelicun chain reachable from a single LLM-visible tool, with the impact rendered as a structured UI panel, all routed through cache-backed Gemini (90% input discount empirically validated), routed via the post-hoc allowed-set validator (telemetry-aware via M5/M6), and surfaced in a routing-quality dashboard.

End-to-end demo flow now possible (P5 acceptance):
1. User: "What's the flood damage and impact for Hurricane Ian on Fort Myers?"
2. Agent: dispatch `compute_impact_envelope(flood_layer_uri=..., location_query="Fort Myers, FL")`
3. Composer chains: `geocode_location` → `fetch_usace_nsi` → `run_pelicun_damage_assessment` → `postprocess_pelicun`
4. Backend emits `impact-envelope` WS frame
5. Web client routes through `ws.ts` → `onImpactEnvelope` → `setImpactEnvelope` state
6. `ImpactPanel` slides out with headline stats + per-occupancy breakdown
7. Telemetry writer logs every dispatch → MongoDB tool_call_telemetry collection
8. `discover_dataset` 4th channel picks up the new co-occurrence pattern for future routing improvement
9. `RoutingQualityDashboard` reflects the run in next refresh

P5 acceptance run remains deferred to avoid burning Vertex quota until needed.

## Carry-overs to sprint-13.5

| Carry-over | Where it lands |
|---|---|
| **M4** Cases/sessions/users/secrets_refs CRUD migration to MCP | sprint-13.5 alongside Firebase Auth migration (both touch persistence path, both adversarial-gated) |
| **P5** Live Pelicun chain acceptance gate | sprint-13.5 production acceptance (single Gemini call, runs alongside auth E2E) |
| Compose `discover_dataset` live-Mongo backend with real telemetry corpus | runtime accumulation; no code work needed, just data |

## Token spend

| Workflow | Tokens |
|---|---|
| Wave 4.11 dispatches today (P2 / P3 / P4 / M1 / M3 / M5 / M7 / follow-ups) | ~1.5M |
| ImpactEnvelope schema (landed yesterday via doc track) | already in Wave 4.10 ledger |
| **Wave 4.11 total** | **~1.5M** |

Reduced from manifest estimate ~2.5M because M4 + P5 deferred. Substantial substrate for the budget.

Sprint-12 cumulative at this point: ~27.3M.

## Related artifacts

- `reports/inflight/wave-4-11-p2-postprocess-pelicun-design-20260609/design.md` — P2 design (now implemented)
- `reports/inflight/wave-4-11-p4-impact-panel-20260609/evidence/impact_panel.png` — P4 screenshot
- `reports/inflight/wave-4-11-m7-dashboard-20260609/evidence/dashboard.png` — M7 screenshot
- `infra/mongo-mcp/README.md` — M1 ops doc
- `packages/contracts/src/grace2_contracts/impact_envelope.py` — schema
- `packages/contracts/src/grace2_contracts/mongo_collections.py` — telemetry schemas
- `services/agent/src/grace2_agent/workflows/compute_impact_envelope.py` — composer
- `services/agent/src/grace2_agent/tools/postprocess_pelicun.py` — atomic tool
- `web/src/components/ImpactPanel.tsx` — UI consumer
- `web/src/components/RoutingQualityDashboard.tsx` — observability surface

## Status

Wave 4.11 CLOSED. M4 + P5 punted to sprint-13.5. Ready to move to sprint-13 (MODFLOW + Cases 2+3 + conversational analysis) or sprint-13.5 (deployment + auth + the deferred items).
