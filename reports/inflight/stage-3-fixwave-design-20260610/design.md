# Stage-3 Fix-Wave Design (read-only diagnosis — NO code changed)

**Job:** stage-3-fixwave-design-20260610 (agent specialist)
**Diagnosed against:** job-0235 Case 2 live run, commit 696a95d
**Evidence base:** `reports/inflight/job-0235-testing-20260609/evidence/{findings.json, ws_frames.json, agent_log_excerpt_run2.log, run_console2.log}`

This is a DESIGN doc. Diffs below are proposed (unified-diff sketches), **not applied** — Stage 3 live acceptance is running against :8765 / :5173 right now. Owners apply in the fix wave.

---

## Executive summary

| # | Bug | Severity | Real root cause (differs from kickoff hypothesis where noted) | Owner file |
|---|-----|----------|---------------------------------------------------------------|------------|
| 1 | Confirmation-gate bypass | **critical** | The LLM-facing wrapper hardcodes `confirmed=True, confirmation_hook=None`; the server dispatch path has **no gate** for MODFLOW (only payload-warning + code_exec). Gate is doubly unreachable in production. | `workflows/model_groundwater_contamination_scenario.py` + `server.py` |
| 2 | Plume layer non-render (file://) | high | `fsspec` is **not installed** in the live agent `.venv` (declared in pyproject but venv is stale). `_upload_cog` import fails → file:// fallback → `_dispatch_publish_layer` skips publish for non-gs:// URI. NOT "local mode skips GCS." | venv sync + `postprocess_modflow.py` (hardening) |
| 3 | OQ-227-FLOPY-DEP | low | `flopy` is undeclared in pyproject (installed transitively, 3.10.0). | `pyproject.toml` |
| 4 | MCP sidecar leak | low | `MCPClient.start` spawns `npx` via `create_subprocess_exec` with no `start_new_session` / PDEATHSIG; the Node grandchild survives abnormal agent exit. | `mcp.py` |
| 5 | bubble_count:0 anomaly | n/a (test bug) | Harness stopped polling at +166s, BEFORE the final narration stream (+237s in agent log). `mentions_*:true` read the user-echo/tool text, not an agent bubble. **Test-harness timing bug, not a web bug.** | acceptance harness only |

---

## Bug 1 — confirmation-gate bypass (CRITICAL)

### What the live run showed

WS frames (`findings.json.ordering`): `gate_seen_rel_ms: null`, `modflow_dispatch_rel_ms: 170345`, `gate_before_dispatch: false`. The `run_model_groundwater_contamination_scenario` pipeline step went `pending → running` at t+170.3s and `complete` at t+226.2s in **one shot**, with **no `tool-payload-warning` envelope anywhere in the frame log**. The agent log confirms the composer ran straight through:

```
00:08:00  iter=7 tool=run_model_groundwater_contamination_scenario  (dispatch)
00:08:55  case2 extracted contaminant='trichloroethylene' ... rate=3.07039 kg/s
00:08:56  run_modflow_local mf6 ... (SOLVER RAN)
00:08:57  case2 complete ... max_concentration_mgl=2946.32
```

No pause. The solver executed without any user confirmation.

### Root cause (evidence: file:line)

There are **two independent reasons** the gate is unreachable on the production (LLM-driven) path. Both must be fixed; either alone leaves a hole.

**(A) The LLM-facing wrapper hardcodes the bypass.**
`workflows/model_groundwater_contamination_scenario.py:1060-1068` — the `@register_tool`-decorated `run_model_groundwater_contamination_scenario` (the ONLY surface Gemini can call; `source_class="workflow_dispatch"`) calls the inner composer with:

```python
result = await model_groundwater_contamination_scenario(
    article_text=article_text,
    source_url=source_url,
    confirmed=True,              # <-- gate bypassed
    ...
    pipeline_emitter=None,       # <-- no emitter, no progress, no pause channel
)
```

The wrapper's docstring (lines 1040-1044, 1055-1059) asserts the gate is enforced "by the server confirmation hook around `run_modflow_job`" as a fail-closed backstop. **That backstop does not exist** — see (B). So `confirmed=True` here is an unconditional bypass.

**(B) The server dispatch path has no gate for this tool (matches kickoff hypothesis (c)).**
`server.py:_invoke_tool_via_emitter` (lines 1935-2057) gates exactly two things before dispatch:
- `_maybe_gate_on_payload_warning` (line 2010) — fires only when the tool declares a `payload_mb_estimator_name` AND the estimate exceeds the MB threshold. The composer declares no estimator; its own confirm envelope is `estimated_mb=0.0` (`_build_confirmation_envelope`, line 723). So this gate is a no-op for MODFLOW.
- `code_exec_request` confirm gate (line 2032) — guarded by `if tool_name == "code_exec_request"`. Never matches `run_model_groundwater_contamination_scenario` or `run_modflow_job`.

`CONFIRMATION_TRIGGERS: set[str] = set()` (`server.py:138`) is empty and is **never consulted in the dispatch path** (only referenced in comments + the chat-message-write carveout, lines 304/1546). There is no per-solver hook around `run_modflow_job`. Nothing in `server.py` / `pipeline_emitter.py` injects a `confirmation_hook` into composer params (grep returns zero hits outside the composer module itself).

**Net:** the inner composer's gate logic is correct (`model_groundwater_contamination_scenario`, lines 857-873: fail-closed when `confirmed=False` and no hook). But the production path reaches it only through the wrapper, which passes `confirmed=True`, and the server provides no alternate gate. The gate is dead code on the live path.

### Why job-0228's programmatic E2E "showed the gate working"

The unit tests in `tests/test_model_groundwater_contamination_scenario.py:241-358` call the **inner** `model_groundwater_contamination_scenario(...)` directly with `confirmation_hook=` / `confirmed=False` and assert `ConfirmationDeniedError` + no solver dispatch. They prove the inner gate logic. **No test invokes the registered wrapper `run_model_groundwater_contamination_scenario` through `_invoke_tool_via_emitter`** (the path Gemini drives). The wrapper's `confirmed=True` line and the server's missing gate were never exercised together. That is the divergence: tested the composer, never the dispatch path.

### Proposed fix

Mirror the proven, fail-closed `_gate_on_code_exec` seam (`server.py:1814-1914`, job-0233): emit a confirm card on the `pending_payload_warnings` future seam, block with a TTL, fail closed on cancel/timeout/disconnect. The composer ALREADY builds the right envelope (`_build_confirmation_envelope` → `PayloadWarningEnvelopePayload`, the `tool-payload-warning` shape the web client renders inline). Two coordinated edits:

**Edit 1 — server.py: add a MODFLOW/solver confirm gate in the dispatch path.**
Add a gate keyed on the tool name, placed right after the code-exec gate (`server.py:~2037`). It reuses `_build_confirmation_envelope` from the composer by extracting the derived params first — but cleaner: have the gate run the composer's *extraction* (pure, no solver) to build the envelope, then pass the user's decision down as `confirmed`. The minimal, lowest-risk version gates the wrapper itself:

```diff
--- a/services/agent/src/grace2_agent/server.py
+++ b/services/agent/src/grace2_agent/server.py
@@ a set of solver/consequence tools that MUST confirm before dispatch.
+# Tools whose dispatch is a "consequence" (a solver run, FR-AS-8 / Invariant 9)
+# and MUST pass a parameter-confirmation gate on the LLM path. The composer
+# builds the confirm envelope from its pure extraction; the gate blocks on the
+# same pending_payload_warnings seam as code-exec, fails closed.
+SOLVER_CONFIRM_TOOLS: set[str] = {
+    "run_model_groundwater_contamination_scenario",
+}
@@ def _invoke_tool_via_emitter(...)
     if tool_name == "code_exec_request" and not params.get("confirmed"):
         should_run, params = await _gate_on_code_exec(websocket, state, params)
         if not should_run:
             raise CodeExecConfirmationCancelledError(
                 params.get("code_exec_id", "unknown")
             )
+
+    # Confirmation-before-consequence for solver composers (Invariant 9).
+    if tool_name in SOLVER_CONFIRM_TOOLS and not params.get("confirmed"):
+        should_run, params = await _gate_on_solver_confirm(
+            websocket, state, tool_name, params
+        )
+        if not should_run:
+            raise SolverConfirmationCancelledError(tool_name)
```

`_gate_on_solver_confirm` (new, modeled on `_gate_on_code_exec`): build the envelope by calling the composer's pure extractor (`extract_spill_parameters(article_text, geocode=True)` + `_build_confirmation_envelope`), emit `tool-payload-warning`, await the `tool-payload-confirmation` future with `CODE_EXEC_CONFIRM_TIMEOUT_SECONDS` TTL, return `(True, params | {"confirmed": True})` on proceed else `(False, params)`. Add `SolverConfirmationCancelledError(RuntimeError)` alongside the existing cancelled-error classes (`server.py:~195-230`) so Gemini sees a typed, non-retryable envelope and narrates the decline.

**Edit 2 — composer wrapper: stop hardcoding the bypass; honor an injected `confirmed`.**
`workflows/model_groundwater_contamination_scenario.py:1060-1068`. The wrapper must NOT force `confirmed=True`. The server's gate injects `confirmed=True` into params only after the user approves; the wrapper should pass that through (default False = fail-closed):

```diff
@@ async def run_model_groundwater_contamination_scenario(
     article_text: str | None = None,
     source_url: str | None = None,
     aquifer_k_ms: float | None = None,
     porosity: float | None = None,
     compute_class: str = "standard",
+    confirmed: bool = False,
     **_extra_ignored: Any,
 ) -> dict[str, Any]:
@@
-    result = await model_groundwater_contamination_scenario(
-        article_text=article_text,
-        source_url=source_url,
-        confirmed=True,
-        aquifer_k_ms=aquifer_k_ms,
-        porosity=porosity,
-        compute_class=compute_class,
-        pipeline_emitter=None,
-    )
+    # confirmed is injected as True by the server-side solver-confirm gate ONLY
+    # after the user approves the derived parameters. Default False = fail closed.
+    result = await model_groundwater_contamination_scenario(
+        article_text=article_text,
+        source_url=source_url,
+        confirmed=confirmed,
+        aquifer_k_ms=aquifer_k_ms,
+        porosity=porosity,
+        compute_class=compute_class,
+        pipeline_emitter=None,
+    )
```

Note `normalize_args` (`server.py:2048`) inspects `entry.fn`'s signature; adding `confirmed` as a real kwarg means the injected `confirmed=True` survives the sweep instead of being absorbed into `**_extra_ignored`.

**Design choice — gate at the wrapper, not inside `run_modflow_job`.** The kickoff floated gating around `run_modflow_job`. Gating at the wrapper is better here because the *parameters the user confirms* (derived contaminant / rate / duration / spill point + the demo-aquifer caveat) only exist after the composer's extraction. `run_modflow_job` receives already-derived forcing; a gate there could not show the user the "we read 12,000 gal TCE over 6 h → 3.07 kg/s" derivation the confirm card needs. The `SOLVER_CONFIRM_TOOLS` set is extensible — `run_model_flood_scenario` / `run_model_flood_habitat_scenario` can be added in a follow-up once their confirm-envelope builders exist (out of scope here; they currently have no gate either, noted as a carry-over).

**Invariant check:** fail-closed (cancel/timeout/disconnect → `SolverConfirmationCancelledError`, no run); cancel-first-class (the `await fut` is a cancel-propagation site, like `_gate_on_code_exec`); determinism boundary untouched (envelope built from typed extraction). Satisfies Invariant 9.

---

## Bug 2 — plume layer non-render (file:// fallback)

### What the live run showed

`findings.json.final_map.layer_ids: ["qgis-basemap","osm-fallback-basemap"]` — basemaps only, no plume. `plume.materialized: false`. The agent log is explicit:

```
00:08:57  postprocess_modflow run_id=... max_concentration_mgl=2946.32 plume_area_km2=0.0125
00:08:57  WARNING plume COG upload to gs://grace-2-hazard-prod-runs/.../plume_concentration_4326.tif
          failed (No module named 'fsspec'); using local file:// URI
00:08:57  run_modflow_job complete ... uri=file:///tmp/tmphf2fc3qz_4326.tif
```

The solver ran, metrics are valid (2946 mg/L, 0.0125 km²), the COG was written locally — but it never reached GCS or QGIS.

### Root cause (evidence: file:line) — DIFFERS FROM KICKOFF HYPOTHESIS

The kickoff hypothesized "local mode writes a local file, skips GCS upload + publish dispatch." **That is not what happens.** `postprocess_modflow` is unconditionally called with `publish=True` (`tools/run_modflow_tool.py:238-243`, no `publish=` override → default True), and `_upload_cog` ALWAYS attempts the GCS put regardless of local mode. The actual failure chain:

1. `postprocess_modflow._upload_cog` (`workflows/postprocess_modflow.py:351-378`) does `import fsspec` → raises `ModuleNotFoundError: No module named 'fsspec'`.
2. The `except` (lines 369-378) logs the warning and returns `file://{local_cog}`.
3. `_dispatch_publish_layer` (`workflows/postprocess_modflow.py:391-396`) early-returns `None` because `if not cog_uri.startswith("gs://")`. No `publish_layer` call → no QGIS WMS → no map layer.

**The real cause is a missing dependency in the live agent venv.** `fsspec[gcs]>=2024.6` IS declared in `services/agent/pyproject.toml:73`, but the running `.venv` does not have it installed (verified: `.venv/bin/python -c "import fsspec"` → `ModuleNotFoundError`; `gcsfs` likewise missing). The venv is stale relative to pyproject. ADC is live on this box, so once `fsspec`+`gcsfs` import, the GCS put to `grace-2-hazard-prod-runs` succeeds and the publish dispatch fires exactly like the SFINCS path.

Comparison with the working SFINCS path: `postprocess_flood._upload_cog_to_runs_bucket` (`workflows/postprocess_flood.py:458-475`) uses the identical `import fsspec; fsspec.filesystem("gcs"); fs.put(...)` but has **no file:// fallback** — it raises `COG_UPLOAD_FAILED`. SFINCS only runs in the cloud (where fsspec is present), so it never hits this locally. MODFLOW's local mode + the masking file:// fallback is what surfaced the missing dep as a silent non-render instead of a hard error.

### Proposed fix

**Edit 1 (primary) — sync the agent venv so the declared dep is installed.** This is the actual fix; the code already does the right thing once fsspec imports. Infra/owner step (not a source edit):

```bash
cd services/agent && .venv/bin/pip install -e .   # or: uv pip sync
# verify: .venv/bin/python -c "import fsspec, gcsfs; print(fsspec.__version__, gcsfs.__version__)"
```

`gcsfs` is pulled by `fsspec[gcs]`. Do this when the agent is NOT mid-acceptance (it requires an agent restart to pick up the new modules). Coordinate with the live-gate owner.

**Edit 2 (hardening) — make the local→GCS upload explicit + the failure loud.** Two complementary hardening changes in `workflows/postprocess_modflow.py`:

(a) Make `_dispatch_publish_layer` log a WARNING (not a silent skip) when handed a non-gs:// URI, so a future missing-dep regression is visible in logs as "plume not published because URI is file://", not just a debug-level skip:

```diff
@@ def _dispatch_publish_layer(cog_uri: str, layer_id: str) -> str | None:
     if not cog_uri.startswith("gs://"):
+        logger.warning(
+            "publish_layer SKIPPED for %s: COG URI is not gs:// (%s); "
+            "the plume will NOT render as a map layer. Check fsspec[gcs] is "
+            "installed and the GCS upload succeeded.",
+            layer_id, cog_uri,
+        )
         return None
```

(b) Make the upload failure surface its root cause distinctly (an import error is a deploy/env bug, not a transient GCS error). Keep the file:// fallback for true offline dev, but classify the import-missing case so it is unmistakable in logs:

```diff
@@ def _upload_cog(local_cog: Path, run_id: str, runs_bucket: str | None) -> str:
     try:
         import fsspec  # type: ignore[import-not-found]
         fs = fsspec.filesystem("gcs")
         fs.put(str(local_cog), dest)
         logger.info("uploaded plume COG to %s", dest)
         return dest
+    except ImportError as exc:
+        logger.error(
+            "plume COG upload to %s SKIPPED — fsspec[gcs] not importable (%s). "
+            "This is a deploy/env defect: fsspec is a declared dependency. The "
+            "plume will fall back to file:// and NOT render. Install fsspec[gcs].",
+            dest, exc,
+        )
+        return f"file://{local_cog}"
     except Exception as exc:  # noqa: BLE001
         logger.warning(
             "plume COG upload to %s failed (%s); using local file:// URI",
             dest, exc,
         )
         return f"file://{local_cog}"
```

Hardening (Edit 2) does not fix the render on its own — Edit 1 (the venv sync) is what makes the plume render. Edit 2 ensures the next missing-dep regression is diagnosable in one log line instead of a silent basemap-only map.

**Do NOT** rewrite local mode to "skip upload + publish" (the kickoff's framing). The opposite is correct: local mode SHOULD upload to the runs bucket and publish (ADC reaches GCS from this box), and the code already attempts exactly that. The fix is making the dependency present.

---

## Bug 3 — OQ-227-FLOPY-DEP (pin flopy)

`flopy` is imported by `workflows/postprocess_modflow.py` (`flopy.utils.HeadFile`, `flopy.mf6.MFSimulation`) and the gwt adapter, but is NOT declared in `services/agent/pyproject.toml` (grep: no `flopy` line). It is present in the venv (3.10.0) only transitively. Pin it.

```diff
--- a/services/agent/pyproject.toml
+++ b/services/agent/pyproject.toml
@@ dependencies = [
+    # job-0227 sprint-13: flopy is the MODFLOW 6 deck builder + UCN reader for
+    # the GWT groundwater-transport postprocess (run_modflow / postprocess_modflow).
+    # 3.9 is the first series with the MF6-GWT API used here; <4 guards the next major.
+    "flopy>=3.9,<4",
```

Installed 3.10.0 satisfies `>=3.9,<4`, so no reinstall churn — just makes the dep explicit/reproducible.

---

## Bug 4 — MCP sidecar leak hardening

`mcp.py:96-104` — `MCPClient.start` launches `npx -y mongodb-mcp-server` via `asyncio.create_subprocess_exec` with no session/parent-death wiring. `close()` (lines 116-128) terminates only the direct `npx` PID. But `npx -y` spawns a Node grandchild (the actual `mongodb-mcp-server`); on abnormal agent exit (SIGKILL / crash, common across Wave 4.11 restarts) neither the npx child nor the Node grandchild is reaped → the 43 orphans observed. (Current count is 0 — prior restarts' orphans were since cleaned — but the leak path is live.)

Fix: detach into a new session/process-group and (on Linux) install PDEATHSIG so the child dies when the agent dies, AND kill the whole group in `close()`.

```diff
--- a/services/agent/src/grace2_agent/mcp.py
+++ b/services/agent/src/grace2_agent/mcp.py
@@
 import asyncio
 import json
 import os
+import signal
 import shutil
@@ async def start(cls, srv, *, database="grace2_dev"):
+        def _preexec() -> None:
+            # New session/process-group so we can signal the whole tree, and
+            # PDEATHSIG so the npx child + Node grandchild die if the agent dies
+            # abnormally (SIGKILL/crash) — no orphaned mongodb-mcp-server leak.
+            os.setsid()
+            try:
+                import ctypes  # Linux-only; harmless to import lazily
+                PR_SET_PDEATHSIG = 1
+                ctypes.CDLL("libc.so.6").prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
+            except Exception:  # noqa: BLE001 — best-effort; setsid still helps
+                pass
+
         proc = await asyncio.create_subprocess_exec(
             npx, "-y", "mongodb-mcp-server",
             stdin=asyncio.subprocess.PIPE,
             stdout=asyncio.subprocess.PIPE,
             stderr=asyncio.subprocess.PIPE,
             env=env,
+            start_new_session=True,   # detach into its own session/pgid
+            preexec_fn=_preexec,      # POSIX-only; this server is Linux/Cloud Run
         )
@@ async def close(self):
         if self._proc.returncode is None:
             try:
-                self._proc.terminate()
+                # Kill the whole process group (npx + Node grandchild), not just npx.
+                try:
+                    os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
+                except (ProcessLookupError, PermissionError):
+                    self._proc.terminate()
             except ProcessLookupError:
                 pass
             try:
                 await asyncio.wait_for(self._proc.wait(), timeout=5.0)
             except asyncio.TimeoutError:
-                self._proc.kill()
+                try:
+                    os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
+                except (ProcessLookupError, PermissionError):
+                    self._proc.kill()
                 await self._proc.wait()
```

`start_new_session=True` and `preexec_fn` are POSIX-only; this agent runs Linux locally and Cloud Run, so that is fine. PDEATHSIG is the parent-death backstop for SIGKILL/crash (where `close()` never runs); `killpg` is the clean-shutdown path that reaps the grandchild.

---

## Bug 5 — bubble_count:0 anomaly (classification: TEST-HARNESS bug, not web)

### Evidence

`findings.json.narration.bubble_count: 0`, yet `scroll_text_tail` is fully populated and `narration_geo` shows `mentions_idaho/concentration/area: true`. Reconciling with the timeline:

- `run_console2.log`: the harness polled to `t=166s frames=21`, saw the modflow card running, then `[STEP4] idle, no plume layer materialized, stopping` and wrote findings at ~STEP5.
- `agent_log_excerpt_run2.log`: the modflow tool completed at 00:08:57 (≈ t+218s), and the FINAL narration turn (`streamGenerateContent`) fired at **00:09:17 (≈ t+237s)** — ~70s AFTER the harness stopped polling.

So the agent's narration bubble had not streamed yet when the harness measured. `bubble_count: 0` is correct *for the moment it sampled* — there were zero `[data-testid="agent-message"]` bubbles because the agent's only completed output so far was tool cards + the user echo. The `mentions_*:true` values came from `scroll_text_tail`, which captured the user-pasted article text (it ends "...prioritized for sampling." — verbatim article tail) plus tool-card labels — NOT an agent narration bubble. The geo-mention check matched the article's own "Idaho"/"concentration"/"area" words.

### Classification

**Test-harness bug (timing + selector semantics), not a web bug.** The web `AgentMessage` component renders correctly with `data-testid="agent-message"` (`web/src/components/AgentMessage.tsx:197-216`) and is wired into the interleaved stream (`web/src/Chat.tsx:1413-1420`). There is no web defect here. Two harness deficiencies:

1. **Stopped polling before narration.** STEP4 declared idle as soon as the modflow card stopped running, but the agent still owed a narration turn (the function_response → final generate_content round-trip takes ~20s after tool completion). The harness must wait for an agent-message bubble with `done=true` to appear AFTER the last tool card, not stop at tool completion.
2. **Geo-mention check read the wrong source.** It scanned `scroll_text_tail` (which includes the user echo) rather than the text of `[data-testid="agent-message"][data-done="true"]` bubbles. With a real article that names Idaho, this gives a false "narration mentions Idaho" even when no narration exists.

### Proposed harness fix (acceptance script, not app code)

- After the last solver card reaches `complete`, poll until `page.locator('[data-testid="agent-message"][data-done="true"]')` count increases (or a max-wait, e.g. 90s), THEN sample.
- Compute `bubble_count` and `narration_geo` from the agent-message bubbles' `innerText` only, excluding `[data-testid="user-bubble"]` / tool cards.
- Minor: the harness pasted the fixture starting at line 3 ("contamination composer...") — the first two SYNTHETIC-FIXTURE lines were dropped. Cosmetic, not load-bearing (extraction still found Twin Falls/TCE), but paste the full fixture for fidelity.

No app-side change is warranted for Bug 5.

---

## Test plan — which programmatic tests would have caught each, and why they did not

### Bug 1 (gate bypass)
**Would-have-caught:** an integration test that drives the **registered wrapper** `run_model_groundwater_contamination_scenario` through `_invoke_tool_via_emitter` (or at least asserts the wrapper does NOT pass `confirmed=True` unconditionally) and asserts a `tool-payload-warning` is emitted before any `run_modflow_job` dispatch.
**Why it didn't:** `tests/test_model_groundwater_contamination_scenario.py:241-358` test the INNER composer with explicit `confirmation_hook`/`confirmed`. They never call the wrapper, and never go through the server dispatch path where the (missing) gate lives. The gate's two failure points — wrapper `confirmed=True` and absent server gate — were in code no test reached together.
**New tests to add (with the fix):**
- `test_wrapper_does_not_force_confirmed` — call the registered wrapper with a denying/absent gate context and assert it does NOT run the solver (raises ConfirmationDenied / blocks). Today this would FAIL (wrapper forces True) — exactly the regression guard wanted.
- `test_server_gates_solver_tools` — a server-level test that pushes a `run_model_groundwater_contamination_scenario` function-call through `_invoke_tool_via_emitter` against a fake websocket, asserts a `tool-payload-warning` envelope goes out and dispatch blocks on the future; on a `cancel` decision asserts `SolverConfirmationCancelledError` and no solver call.

### Bug 2 (plume non-render / fsspec)
**Would-have-caught:** a smoke test asserting the declared deps are importable in the venv (`import fsspec, gcsfs`), OR an integration test of `postprocess_modflow` that asserts the returned `PlumeLayerURI.uri` startswith `gs://` (or that `publish_layer` was dispatched) when GCS is reachable — instead of accepting a file:// URI as success.
**Why it didn't:** the existing `postprocess_modflow` tests mock fsspec / publish_layer, so the real import was never exercised; and the file:// fallback makes a missing-dep run return a valid-looking `PlumeLayerURI` (metrics correct), so nothing asserted the URI scheme. A passing unit suite coexisted with a non-rendering live path.
**New tests to add:**
- `test_declared_deps_importable` (env smoke) — `importlib.import_module("fsspec"); import_module("gcsfs")`. Fast, catches stale-venv class of bugs.
- `test_postprocess_modflow_publishes_when_gs` — with fsspec/publish mocked to SUCCEED, assert `_dispatch_publish_layer` is called and the returned URI is the WMS/gs:// URI, not file://. Add a negative: when `_upload_cog` returns file://, assert the new WARNING is logged (Bug 2 Edit 2a).

### Bug 3 (flopy pin)
**Would-have-caught:** the same `test_declared_deps_importable` (add `flopy`) + a lockfile/`pip check` in CI. Why it didn't: flopy was transitively present, so imports worked; nothing asserted it was a first-class declared dep.

### Bug 4 (MCP leak)
**Would-have-caught:** a test that starts an `MCPClient`, SIGKILLs the parent test process's child, and asserts no `mongodb-mcp-server` survives — hard to do in unit scope. Realistically a manual/integration check. Why it didn't: no process-lifecycle test exists. The fix is verifiable by: start agent → `os.killpg`/kill -9 the agent → assert `pgrep -f mongodb-mcp-server` returns nothing (PDEATHSIG path).

### Bug 5 (bubble_count)
**Would-have-caught:** N/A for app code — it's a harness assertion timing bug. The fix is in the harness (wait-for-done-bubble + bubble-scoped text). A web vitest already covers `AgentMessage` rendering (`AgentMessage.test.tsx`), confirming the component is fine.

---

## Re-verification plan (ONE Gemini session, Case 2 only)

After the fix wave lands AND the agent venv is synced + agent restarted:

1. **Pre-flight (no Gemini):** `.venv/bin/python -c "import fsspec, gcsfs, flopy"` → all import. `pgrep -f mongodb-mcp-server` baseline. Confirm `GRACE2_MODFLOW_LOCAL=1` for the local mf6 path.
2. **Single live session** (Playwright, real chat input, NO inject seams per project memory): new Case → paste the FULL `case2_news_article.txt` → "Model the groundwater contamination from this spill: <article>".
3. **Assert, in order:**
   - a `tool-payload-warning` (parameter-confirmation) envelope appears in the WS frames BEFORE any `run_model_groundwater_contamination_scenario`/`run_modflow_job` dispatch (`gate_before_dispatch: true`, `gate_seen_rel_ms` non-null). Screenshot the confirm card showing derived params (TCE, ~3.07 kg/s, 0.25 d, Twin Falls point) + demo-aquifer caveat.
   - Approve. MODFLOW runs (local mf6). Agent log shows `uploaded plume COG to gs://grace-2-hazard-prod-runs/<run_id>/plume_concentration_4326.tif` (NOT the fsspec file:// warning) and `publish_layer succeeded`.
   - `final_map.layer_ids` includes a `plume-concentration-<run_id>` layer; screenshot with LayerPanel open and the plume visible over Idaho (`in_idaho_bbox: true`, `in_florida_bbox: false`).
   - Wait for the agent narration bubble (`[data-testid="agent-message"][data-done="true"]`) to appear AFTER the solver card; assert `bubble_count >= 1` and that the BUBBLE text (not the article echo) mentions a non-zero concentration + area + Idaho.
4. **Negative gate check (no extra Gemini turn):** in the same session, on a second prompt, cancel at the confirm card → assert `SolverConfirmationCancelledError` envelope reaches the client, no solver runs, agent narrates the decline. (Optional; only if Vertex quota allows — otherwise defer to the next acceptance.)
5. **Quota discipline:** one session, ≤8 turns. On any 429 RESOURCE_EXHAUSTED, STOP and record BLOCKED with partial evidence (per Stage 3 rules). Sleep 300s before the next acceptance job if one follows.

A single approved Case 2 run that shows gate-before-dispatch + a rendered gs://-backed plume layer + a real narration bubble proves Bugs 1, 2, 3 simultaneously. Bug 4 is verified out-of-band (kill agent, check no orphan). Bug 5 is verified by the harness now counting a done-bubble.

---

## Open questions / carry-overs

- **OQ-FIXWAVE-FLOOD-GATE:** `run_model_flood_scenario` + `run_model_flood_habitat_scenario` also have NO confirmation gate (same dispatch-path gap). Out of scope for this Case 2 wave; add them to `SOLVER_CONFIRM_TOOLS` once their confirm-envelope builders exist. Flag for a follow-up job.
- **OQ-FIXWAVE-SFINCS-FALLBACK:** `postprocess_flood` has no file:// fallback and would hard-fail `COG_UPLOAD_FAILED` if run locally without fsspec — currently masked because SFINCS is cloud-only. If local SFINCS is ever wanted, mirror the MODFLOW upload pattern.
- **Confirm-envelope shape decision (OQ-0228-CONFIRM-ENVELOPE-CHOICE):** the fix reuses `tool-payload-warning`/`tool-payload-confirmation` (the inline-rendered pair). Confirm with schema owner that a parameter-gate riding the payload-warning envelope (estimated_mb=0) is acceptable long-term vs. promoting a dedicated `confirmation-request` pair.
