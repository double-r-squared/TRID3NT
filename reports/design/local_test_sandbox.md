# Local test sandbox — verify operability without Bedrock spend

Goal (NATE 2026-06-23): iterate on tools / plugin-wraps / engines and verify they are
OPERABLE through the real agent pipeline WITHOUT paying for (or depending on) a live
Bedrock model. Three independent tiers; pick the cheapest that proves what you need.

## Tier 1 — direct tool/chain pytest (FREE, already exists)
Tools and workflows are plain async functions returning typed envelopes. Call them with
fixed args and assert — no model, no WS, no web. This is the first gate for any new tool.
Example: today's SWAN bathy fix was proven by calling `_build_depth_fn` against the real
DEM and asserting `fallback 0/1600`. See `services/agent/tests/test_run_swan_chain.py`,
`services/workers/swan/test_entrypoint_depth.py`. Cost: $0. Speed: sub-second.

## Tier 2 — scripted/replay agent adapter (NEW — the full-loop sandbox)
`MODEL_PROVIDER=scripted` (aliases `replay` / `fake`) replays a canned transcript of tool
calls instead of calling Bedrock, so the FULL loop runs deterministically with no LLM cost:
agent loop -> tool dispatch -> pipeline cards -> WS -> web render. Doubles as a regression
harness (record a good transcript once, replay forever). Module:
`services/agent/src/grace2_agent/scripted_adapter.py` (a drop-in third provider on the
existing `MODEL_PROVIDER` seam, next to `bedrock_adapter.stream_bedrock`).

**Transcript** = a list of TURNS; each turn optionally emits assistant text and optionally
ONE tool call. The adapter picks the turn by counting prior assistant (`model`-role) turns
in `contents`, so it advances one turn per loop iteration as tool results feed back. A turn
with no `tool_call` is terminal. Sample: `services/agent/sandbox/transcripts/swan_mexico_beach.json`.

Sources (precedence): `set_script(turns)` (in-process, tests) > `GRACE2_SCRIPTED_TRANSCRIPT_JSON`
(inline JSON) > `GRACE2_SCRIPTED_TRANSCRIPT` (file path) > a graceful fallback turn.

Run the agent locally against a transcript:
```
export MODEL_PROVIDER=scripted
export GRACE2_SCRIPTED_TRANSCRIPT=services/agent/sandbox/transcripts/swan_mexico_beach.json
# start the agent server (no GCP/AWS model creds needed; client is None on this path)
# then drive it with the Playwright harness (web/tools/drive_solve.mjs) or any WS client
```
In tests, just `scripted_adapter.set_script([...])` and call `stream_events_with_contents(
client=None, ...)` — see `services/agent/tests/test_scripted_adapter.py`.

**HONESTY BOUNDARY:** Tier 2 removes the *LLM* cost only. The tool calls in the transcript
still EXECUTE for real — `run_swan_waves` still dispatches an AWS Batch job, fetchers still
hit data sources / S3. So a scripted run is free of Bedrock but not free of engine/data cost
(Spot Batch is cheap, not $0). For a fully-offline operability check, pair Tier 2 with Tier 3.

## Tier 3 — local docker worker runs (engine dev without Batch)
The engines run in worker containers (`services/workers/*`, images in ECR). For iteration,
`docker run` the worker image against a local manifest + a scratch S3 prefix (or local fs)
instead of dispatching Batch — removes Batch dispatch latency + cost for the solve/postprocess.

## (Optional) a real but free model
The same `MODEL_PROVIDER` seam points at a local Ollama / llama.cpp small model if you want a
*non-scripted* model in the loop for free — this is also the on-ramp to the offline build
(see memory: project_local_offline_build_stretch). For pure operability testing the scripted
adapter is preferred: deterministic, no GPU, no flakiness.

## What this unlocks
Every future tool / QGIS-plugin-wrap / engine gets a $0, deterministic E2E gate: author a
transcript that calls it, run the loop under `MODEL_PROVIDER=scripted`, assert the card +
layer + envelope. No Bedrock turn burned to find out a wiring bug.
