# job-0269 — root-deselect + stream-scoped turns + terrain gdaldem (live-demo batch)

**Specialist:** agent + web + schema cross-cut (orchestrator-direct Fable xhigh,
per the user's standing critical-batch authorization). **Opened:** 2026-06-10,
from three live user reports during multi-Case demo testing.

## Defects (all reproduced from /tmp/agent_demo6.log)

1. **Navigating to the Cases root was client-only.** `useCases.clearActive()`
   reset local state and sent nothing; the server's session-scoped
   `active_case_id` kept pointing at the last-opened Case. Consequences the
   user hit: a root prompt skipped auto-create ("a case was not generated"),
   the turn dispatched INTO the stale Case ("the terrain second prompt got
   sent to the first case"), and re-selecting that Case re-emitted case-open
   for a Case the web thought it had left ("cant go back into the original
   first case", 13 case-open emissions in 50 ms).

2. **M1 single-slot cancellation killed cross-Case work.** Log 17:01:30: the
   DEM user-message fired `inflight_task.cancel()` → `wait_for_completion
   CANCELLED` → `workflows.executions.cancel(...)` on the RUNNING cloud
   SFINCS execution, 60 ms after submission. The user's yellow flood card is
   the cancelled-state tint (Invariant 8). Under per-Case streams this policy
   is product-breaking: any new prompt killed minutes-long solves.

3. **`compute_colored_relief` invoked bare `gdaldem`** → FileNotFoundError
   (Boulder, 17:06:32). The batch-1 binary-resolution + job-0257 PROJ-env
   fixes were applied only to `compute_hillshade`; slope/aspect had binary
   resolution but no PROJ env (silent LOCAL_CS degradation); colored-relief
   had neither, plus an unauthenticated `/vsigs/` input path.

## Fix shape

1. `case-command(deselect)`: contracts Literal + server handler (clears the
   session active-Case binding + per-connection LLM context) + web
   `clearActive()` emits it. SRS FR-MP-6/A.3 amendment PROPOSED (user lands).
2. Stream-scoped turns: `SessionState.inflight_tasks` keyed by stream
   (case_id / `__root__`); a new user-message cancels only the SAME stream's
   turn. Safety under concurrency: context-reset sites REBIND (never
   `clear()`) `chat_history`; `_stream_gemini_reply` captures its history +
   narration lists in the synchronous prefix; the narration list is
   registered per-task (`_TURN_NARRATION_BY_TASK`, weak keys) so the dispatch
   wrapper's finally joins THIS turn's list. Persistence targeting already
   safe via the job-0268 pin. The `cancel` envelope targets the visible
   stream's turn, falling back to any live turn; disconnect cancels all.
   KNOWN v0.1 LIMITS (documented in-code): live envelope DISPLAY routes to
   the last-submitted stream until envelope case-tagging (13.5); the
   layer-id/pipeline-id accumulators are still session-shared (cosmetic
   attribution on the closing agent row only).
3. Terrain: colored-relief gets `_get_gdaldem_bin` + staged-local gs://
   download + `_gdaldem_subprocess_env`; slope + aspect get the PROJ env.

## Acceptance

1. deselect: clears binding; root prompt after deselect auto-creates a FRESH
   Case (old Case receives nothing); re-select reopens.
2. Cross-Case turn survives a new root prompt (its narration persists into
   its own Case); same-Case re-prompt still replaces; concurrent turns keep
   narration isolated.
3. `_run_colored_relief` succeeds against the EXACT failed Boulder DEM
   (gs://...bba3a5d3....tif) with EPSG-coded CRS output (live, Gemini-free).
4. Suites: contracts 391, web vitest 582, agent full suite — no new failures
   beyond the 5 proven-pre-existing.
