# Kickoff (frozen)

Job job-0233-agent-20260609 — code_exec_request envelope + agent sandbox dispatch (sprint-13 Stage 2).

## Common rules (GRACE-2 sprint-13 Stage 2 tail)
Working dir: /home/nate/Documents/GRACE-2
Read first: agents/AGENTS.md, your specialist file, reports/sprints/sprint-13-manifest.md (your job scope), reports/inflight/job-0232-infra-20260609/report.md (sandbox substrate + runbook).
FIRST ACTION: mkdir -p reports/inflight/<job-id>/ ; write audit.md (this kickoff verbatim under "# Kickoff (frozen)"); STATE "RUNNING".
- NO Gemini/Vertex generate_content calls. Live evidence is programmatic (local-subprocess sandbox mode, vitest, pytest).
- NEVER git push. Commit locally: git add <only your files> && git commit -m "<job-id>: <title>". index.lock: wait 5s retry 5x.
- Python venv: services/agent/.venv. Web: npx vitest in web/.
- Report honestly; AT JOB END write report.md + STATE "READY_FOR_AUDIT".
Return StructuredOutput.

## Substrate in force (job-0232, commit f4573c8, panel 4/4 ADVANCE)
- infra/python-sandbox/executor.py — harness (60s cap, net guard, chart auto-convert)
- services/agent/src/grace2_agent/sandbox_runner.py — submit shim + GRACE2_SANDBOX_LOCAL=1 local-subprocess fallback
- 19 tests at services/agent/tests/test_sandbox_runner.py

## MANDATORY: resolve the 0232 panel findings (verbatim from the panel)
1. FINDING 1 [major, data-loss]: executor convert_result bounds DataFrame rows / array size / PNG bytes but NOT total serialized bytes of JSON-native str/list/dict results — a 9MB string result silently corrupts/fails. Add a total-byte cap (e.g. 2MB) with an explicit truncated=true marker in the result envelope (honest truncation, never silent).
2. FINDING 2 [major]: sandbox_runner.py:216 does a blind MAX_ENVELOPE_BYTES string slice -> corrupt JSON. Replace with parse-then-bound (truncate INSIDE the parsed envelope fields with markers) or reject-with-typed-error.
3. OQ-SANDBOX-3: cloud-mode result transport — executor writes the result envelope to stdout (Cloud Run logs); runtime SA is objectViewer-only (cannot write GCS). DECIDE + implement the v0.1 contract: local mode reads the subprocess stdout directly (works today); cloud mode declared NOT-YET-WIRED with a typed SandboxCloudModeUnavailable error (honest) — do NOT bake a GCS write into the read-only SA. Document the sprint-13.5 identity decision in report.md.
4. OQ-SANDBOX-4: verify the chart auto-convert dict constructs the FINAL chart_contracts.ChartEmissionPayload (job-0223 panel-cleared) — reconcile field names if drifted.
5. GRANTED out-of-0232-ownership line: add the python-sandbox-build Makefile target (mirror modflow-build) per the 0232 runbook.

## Scope (manifest job-0233)
1. packages/contracts/src/grace2_contracts/sandbox_contracts.py (NEW): CodeExecRequestPayload(code_exec_id, python_code, layer_refs dict, rationale str|None) + CodeExecResultPayload(code_exec_id, status ok|error|timeout|blocked, stdout_tail, stderr_tail, result dict|None, truncated bool, duration_s) + envelope-type registration for "code-exec-request" + "code-exec-result" (server->client) following the chart-emission registration pattern. Surgical contracts __init__ export.
2. services/agent/src/grace2_agent/tools/code_exec_tool.py (NEW): atomic tool code_exec_request(python_code, layer_refs=None, rationale=None) — LLM-visible. Flow: emit "code-exec-request" envelope (user-facing card payload) -> WAIT for user confirmation via the EXISTING payload-warning confirm mechanism (read server.py CONFIRMATION_TRIGGERS + payload-warning pause flow and reuse that seam — same UX contract; the user-confirm gate is MANDATORY, no bypass except the documented confirmed=True programmatic arg) -> dispatch via sandbox_runner -> emit "code-exec-result" envelope -> return a compact result summary as the tool result (function_response to Gemini: status + result + stdout_tail capped, never the full payload).
3. Registration: tools/__init__.py + catalog.py + categories.py (data_analysis category). ttl_class live-no-cache.
4. Tests services/agent/tests/test_code_exec_tool.py: confirm-gate blocks without approval; approved path runs local sandbox end-to-end (benign numpy); blocked-egress script returns status=blocked honestly; timeout path; FINDING-1 cap (oversized string result -> truncated=true, valid JSON); FINDING-2 (envelope never corrupted by bounding); function_response summary shape.

## File ownership
packages/contracts sandbox_contracts.py + test + surgical __init__; tools/code_exec_tool.py + test; sandbox_runner.py + executor.py (findings fixes); server.py SURGICAL (envelope emit + confirm-gate wiring only — re-read before each edit, M4 job-0203 landed same-day edits); Makefile (one target); registration lines.
