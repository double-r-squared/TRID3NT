# job-0257-engine-20260610 — kickoff (verbatim)

You are a Fable-5 fix agent. Job job-0257-engine-20260610 — HILLSHADE NO-RENDER: the user ran DEM->hillshade live; the tool card completed "successfully" but nothing appeared on the map.

## Rules (Fable-5 critical batch — maximum rigor)
Working dir: /home/nate/Documents/GRACE-2
You are running as the strongest available model on a user-demo-blocking bug. Diagnose to TRUE root cause with file:line evidence before fixing — no symptom patches. The Stage 3 campaign just found 13 bugs where mocks mirrored assumptions; distrust assumptions, verify against reality (live GCS / real DOM / real persisted files where possible).
- NO Gemini/Vertex generate calls. Do NOT restart the agent on :8765 or touch the running web dev server's behavior beyond HMR-safe edits — the USER is actively demoing; web/src edits are acceptable (HMR) but keep them atomic.
- gcloud at /home/nate/tools/google-cloud-sdk/bin (ADC live). GCP/network Bash: dangerouslyDisableSandbox:true. Python venv: services/agent/.venv.
- Commit ONLY your owned files: git add <files> && git commit -m "job-02XX: <title>". index.lock: wait 5s retry 5x.
- Write reports/inflight/<job-id>/{audit.md (kickoff verbatim), report.md, STATE}.
Return StructuredOutput.

## Diagnosis leads (verify, don't assume)
1. Does compute_hillshade (services/agent/src/grace2_agent/tools/compute_hillshade.py) return a LayerURI and dispatch publish_layer? Check the layer-emission contract (PipelineEmitter isinstance(result, LayerURI) gate -> add_loaded_layer -> map-command).
2. QGIS Server project-cache staleness (known: reports/inflight/job-0245-testing-20260610/USER_UNBLOCK.md) — BUT the user's flood layer DID render in the same demo session, so check whether hillshade's publish actually reached the .qgs: gcloud storage cat the served gs://grace-2-hazard-prod-qgs/grace2-sample.qgs | grep hillshade (unsandboxed Bash).
3. Style preset: does the hillshade QML exist server-side (GRACE2_STYLES_DIR /etc/qgis/styles in the qgis-server image)? A missing QML renders transparent/empty.
4. WMS GetMap probe: request the hillshade layer directly (the round-5 harness pattern in web/tools has GetMap code) — does it return pixels or LayerNotDefined or blank?
5. Check the agent log (/tmp/agent_demo_ready.log) for the user's hillshade run: did publish_layer succeed? Did a map-command fire? Is the bbox sane (hillshade over what region)?
## Fix + prove
Root-cause, fix in your ownership (tools/compute_hillshade.py + publish path + styles as needed; infra/qgis-server/** if a QML must be added — note image rebuild needs Cloud Build, prepare but document), and PROVE Gemini-free: publish a hillshade from an existing DEM in the cache programmatically -> GetMap returns non-blank pixels (save the PNG as evidence).
