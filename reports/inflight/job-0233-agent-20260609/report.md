# Report: code_exec_request envelope + agent sandbox dispatch + 0232 findings fixes

**Job ID:** job-0233-agent-20260609
**Sprint:** sprint-13 (Stage 2; adversarial-verify gated — Case 2 composer + Python sandbox)
**Specialist:** agent
**Status:** ready-for-audit

## Summary
Landed the LLM-facing entry point to the egress-denied Python sandbox: a new `code_exec_request` atomic tool, two agent->client envelopes (`code-exec-request` confirm card + `code-exec-result` outcome), and a server-side confirm gate that REUSES the existing `pending_payload_warnings` future seam (the `code_exec_id` rides back as the `tool-payload-confirmation.warning_id`) so the mandatory user-approval gate needed zero new client-control plumbing. Resolved all five job-0232 panel findings. Evidence is programmatic (local-subprocess sandbox, pytest, in-process gate) — no Gemini calls. 33 agent tests + 76 contracts tests + 199-test adapter/categories/server/registry regression all green.

## Changes
- packages/contracts/.../sandbox_contracts.py (NEW): CodeExecRequestPayload + CodeExecResultPayload + CodeExecStatus + SANDBOX_AGENT_TO_CLIENT_PAYLOADS. Both agent->client (A.4); confirm REPLY rides existing tool-payload-confirmation. No cost field.
- contracts __init__.py + ws.py: surgical exports + routing splat (chart-emission precedent).
- services/agent/.../tools/code_exec_tool.py (NEW): code_exec_request(python_code, layer_refs, rationale, *, confirmed=False, code_exec_id=None). Fail-closed (CodeExecConfirmationRequired). live-no-cache / cacheable=False. Returns compact summary + full payload under _code_exec_result.
- tools/__init__.py + categories.py: registration (geographic_primitives — see OQ-CODE-EXEC-CATEGORY).
- server.py (SURGICAL): _gate_on_code_exec (emit + block on payload-warning future seam + inject confirmed), wired after the payload-warning gate; CodeExecConfirmationCancelledError; _maybe_emit_code_exec_result detect-and-emit; CODE_EXEC_CONFIRM_TIMEOUT_SECONDS.
- adapter.py (SURGICAL): summarize_tool_result strips _code_exec_result from the function_response.
- executor.py (FINDING-1): MAX_RESULT_BYTES=2MiB total-byte cap; convert_result -> _bound_result_descriptor (oversized string hard-truncated w/ marker+truncated=true+original_bytes; oversized container -> too_large descriptor). Honest, never silent.
- sandbox_runner.py (FINDING-2 + OQ-SANDBOX-3): blind slice replaced w/ parse-then-bound (_bound_envelope truncates string fields inside parsed dict + sets *_truncated; absurd raw stdout rejected typed). SandboxCloudModeUnavailable + read_sandbox_result (raises it).
- Makefile: python-sandbox-build + -push targets (modflow-build mirror) + .PHONY + help.
- tests: test_code_exec_tool.py (13) + test_sandbox_contracts.py (12) + 2 ws-inventory factories.

## job-0232 findings — resolution
- FINDING-1: RESOLVED. 9MB string -> truncated json descriptor; 2M list -> too_large; envelope valid JSON; small results untouched.
- FINDING-2: RESOLVED. blind slice gone; parse-then-bound; 5MB stdout -> result intact, stdout_truncated, JSON round-trips.
- OQ-SANDBOX-3: RESOLVED v0.1 — typed SandboxCloudModeUnavailable (no GCS write on read-only SA). sprint-13.5 recommendation: option (b) Cloud Logging read of the structured-log envelope (zero new write identity; preserves the read-only-SA invariant). Local mode reads subprocess stdout directly today.
- OQ-SANDBOX-4: VERIFIED no drift. executor chart_emission dict {title, vega_lite_spec, caption} constructs the final ChartEmissionPayload once the agent injects a fresh chart_id ULID (intentionally agent-minted, same as chart_tools.py). No code change.
- Makefile target: ADDED; make -n resolves correctly.

## Invariants
- 1 (Determinism): preserves — narrated numbers come from the structured result descriptor fed back as function_response.
- 5 (Tier separation): preserves — OQ-SANDBOX-3 REFUSES to add a GCS write to the read-only sandbox SA.
- 9 (Confirm / no cost): preserves+extends — running Python is a new consequential action gated fail-closed via the reused payload-warning seam; no cost field (duration_s=latency, truncated=honesty flag).

## Open Questions
- OQ-CODE-EXEC-CATEGORY (TENTATIVE): kickoff named "data_analysis"; no such category exists; filed under geographic_primitives w/ the other conversational-analysis tools. A distinct category is a deliberate CATEGORIES re-bucketing, not a one-tool exception.
- OQ-SANDBOX-3-followup (sprint-13.5): cloud-result transport decision (recommend Cloud Logging read) needs orchestrator sign-off before job-0238 cloud Playwright acceptance.

## Dependencies / Impacts
- Depends on: job-0232 (substrate), job-0223 (ChartEmissionPayload), payload_warning seam (job-0127), 12-category registry (job-B5).
- Affects: web (render code-exec-request confirm card + reply tool-payload-confirmation w/ code_exec_id as warning_id; render code-exec-result card); job-0238 (sandbox Playwright acceptance + cloud transport decision); infra (python-sandbox-build ready; cloud transport touches sandbox SA/logging IAM at 13.5).

## Verification (all GREEN, agent .venv Python 3.12; NO Gemini, NO network)
- pytest test_code_exec_tool.py — 13 passed. evidence/pytest_code_exec_tool.log
- pytest test_sandbox_runner.py — 19 passed (job-0232 regression after FINDING fixes — unchanged).
- pytest test_sandbox_contracts.py — 12 passed. evidence/pytest_sandbox_contracts.log
- pytest test_ws.py + test_chart_contracts.py — 64 passed (ws inventory now includes both code-exec envelopes).
- pytest -k "adapter or categories or server or registry or tool_arg or summarize" — 199 passed, 2 skipped.
- py_compile on all 9 edited files — clean.
- Live E2E transcript (evidence/live_e2e_transcript.log) — 8 scenarios all PASS: (1) tool-body fail-closed, (2) server gate emit+approve via payload-warning future seam, (3) approved numpy ok result=25.0, (4) blocked egress, (5) timeout 5.0s, (6) FINDING-1 9MB string truncated+valid-JSON, (7) FINDING-2 5MB stdout result intact, (8) function_response strip.

BLOCKED-ENV (inherited from job-0232): the REAL Cloud Run Job dispatch + VPC egress-deny still needs docker/gcloud. This job's cloud path is wired to the typed NOT-YET-WIRED boundary (OQ-SANDBOX-3); the local path is fully live-verified.
