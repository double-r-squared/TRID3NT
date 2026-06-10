# job-0259-agent-20260610 — kickoff (verbatim)

You are a Fable-5 fix agent. Job job-0259-agent-20260610 — CASE LAYERS NOT REHYDRATING: reopening a Case shows none of the layers that were created in it (user theory: user-type related; verify or refute that).

## Rules (Fable-5 critical batch — maximum rigor)
Working dir: /home/nate/Documents/GRACE-2
You are running as the strongest available model on a user-demo-blocking bug. Diagnose to TRUE root cause with file:line evidence before fixing — no symptom patches. The Stage 3 campaign just found 13 bugs where mocks mirrored assumptions; distrust assumptions, verify against reality (live GCS / real DOM / real persisted files where possible).
- NO Gemini/Vertex generate calls. Do NOT restart the agent on :8765 or touch the running web dev server's behavior beyond HMR-safe edits — the USER is actively demoing; web/src edits are acceptable (HMR) but keep them atomic.
- gcloud at /home/nate/tools/google-cloud-sdk/bin (ADC live). GCP/network Bash: dangerouslyDisableSandbox:true. Python venv: services/agent/.venv.
- Commit ONLY your owned files: git add <files> && git commit -m "job-02XX: <title>". index.lock: wait 5s retry 5x.
- Write reports/inflight/<job-id>/{audit.md (kickoff verbatim), report.md, STATE}.
Return StructuredOutput.

## Known prior evidence (start here)
- Round-5 (job-0248): the round-3 plume Case's persisted record had loaded_layer_summaries=[] — publish happened but the Case record was never updated with the layer summary.
- job-0172 Part B: rehydration reads Case.loaded_layer_summaries -> CaseSessionState.loaded_layers -> LayerPanel. The READ path works; suspect the WRITE path.
## Diagnosis leads
1. Who writes Case.loaded_layer_summaries? grep services/agent/src/grace2_agent for loaded_layer_summaries writers. Expected: when PipelineEmitter.add_loaded_layer fires during a Case-scoped tool run, the Case record should be upserted with the new summary. Verify whether that write exists at all, fires only on some paths, or races (e.g., persisted BEFORE publish completes).
2. Check the user-type theory honestly: list_cases_for_user filter + anonymous user-id churn across windows (each new window may mint a new anonymous user -> case list/cases visible but layer writes keyed elsewhere?). server.py _emit_case_list uses session-scoped user placeholder — document what you find.
3. Reproduce Gemini-free: bind file persistence, simulate a Case-scoped LayerURI tool result through the emitter path (unit-level), then get_session_state -> assert loaded_layers populated. Then fix so EVERY add_loaded_layer in an active Case persists the summary (append, dedupe by layer_id, best-effort never-raise).
## Fix + prove
Fix (likely server.py/pipeline_emitter.py + persistence call), tests (write fires on layer add; rehydration round-trips; no write when no active case), evidence logs. NOTE: server.py is shared — re-read before edits; the agent on :8765 keeps running (restart is the orchestrator's, later).
