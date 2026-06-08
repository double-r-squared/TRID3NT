# Report: Tool payload-warning system (schema + agent + web cross-cut)

**Job ID:** job-0127-schema-20260608
**Sprint:** sprint-12-mega Wave 2
**Specialist:** schema (lead — cross-cuts agent + web)
**Status:** ready-for-audit

## Summary

End-to-end chat payload-warning system: schema envelopes, agent gate in
`_invoke_tool_via_emitter`, web inline card with proceed/cancel/narrow-scope.

## Changes Made

### Schema (contracts)
- NEW packages/contracts/src/grace2_contracts/payload_warning.py — PayloadWarningEnvelopePayload + PayloadConfirmationEnvelopePayload
- packages/contracts/src/grace2_contracts/__init__.py — register module
- packages/contracts/src/grace2_contracts/ws.py — register payloads in CLIENT_TO_AGENT_PAYLOADS / AGENT_TO_CLIENT_PAYLOADS / ALL_PAYLOADS
- NEW packages/contracts/tests/test_payload_warning.py — 17 tests
- packages/contracts/tests/test_ws.py — factory entries for both new envelopes

### Agent
- services/agent/src/grace2_agent/server.py — SessionState fields, _maybe_gate_on_payload_warning gate, integration in _invoke_tool_via_emitter, inbound tool-payload-confirmation handler, env-var threshold overrides
- NEW services/agent/tests/test_payload_warning_flow.py — 13 tests

### Web
- web/src/contracts.ts — TS mirror types
- web/src/ws.ts — onPayloadWarning handler + sendPayloadConfirmation method + inbound case routing
- NEW web/src/components/PayloadWarningInline.tsx — inline chat card with JSON clarifier
- NEW web/src/PayloadWarningInline.test.tsx — 9 tests
- web/src/App.tsx — payloadWarnings state + render block

## Verification

- contracts: 249/249 pass (17 new + 34 ws + others)
- agent: 13 new + 26 sibling = 39 pass
- web: 118/118 pass across 11 files including 9 new PayloadWarningInline tests
- Live E2E: real WebSocket round-trip confirmed tool-payload-warning emission
  (estimated_mb=60.0, threshold_mb=25.0, options=[proceed,cancel,narrow_scope])
  + tool-payload-confirmation(cancel) routes to USER_INPUT_CANCELLED error

## Open Questions

- OQ-0127-AUDIT-LOG-PERSISTENCE: per-session in-memory audit log; future job can persist to CaseChatMessage.tool_call_summaries
- OQ-0127-ESTIMATOR-MODULE-RESOLUTION: getattr(module, name) requires module-scope estimator
- OQ-0127-NARROW-SCOPE-VALIDATION: gate trusts revised_args without re-projection
- OQ-0127-SRS-AMENDMENT: docs/srs/A-websocket-protocol.md needs the two new envelope entries

## Results

pass.
