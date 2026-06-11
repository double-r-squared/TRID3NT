# Report: run-local on Bedrock — agent boots + serves with zero GCP creds

**Job ID:** job-0287-agent-20260611
**Sprint:** sprint-14-aws (GCP → AWS migration)
**Specialist:** agent (orchestrator-direct)
**Status:** DONE — live-proven

## Goal
Make the project runnable **entirely locally off Google Cloud**: agent on AWS
Bedrock (job-0286), file persistence (no MongoDB), anonymous auth, no GCP
credentials anywhere.

## Changes
- `server.py` `_stream_gemini_reply`: two Vertex touchpoints guarded by the
  provider flag —
  1. `client = build_client(settings)` → `None` under `MODEL_PROVIDER=bedrock`
     (build_client needs GCP ADC; the bedrock branch of
     `stream_events_with_contents` ignores `client`).
  2. the Gemini CachedContent create/refresh block → skipped under bedrock
     (`state.gemini_cache_name = None`; Bedrock cachePoint caching is job-0288).
  The boot path was already GCP-free (`load_settings` only reads env;
  `init_persistence_from_env` falls back to `FileMCPClient` when no Mongo MCP
  is configured — `is_dev_persistence_enabled` defaults ON without Mongo env).
- `Makefile`: `run-local-agent` target + `AWS_REGION` / `BEDROCK_MODEL_ID`
  vars. Sets `MODEL_PROVIDER=bedrock`, `GRACE2_DEV_PERSISTENCE=1`,
  file-store dir, anonymous auth; no GOOGLE_* env. Pair with `make run-web`.

## Live proof (run_local_verify.py — evidence in this dir)
Spawned the real `grace2_agent.main` as a subprocess with **every GOOGLE_* env
var stripped**, `MODEL_PROVIDER=bedrock`, file persistence, on alt ports
(8865/8867 so the running Vertex demo on 8765 was untouched), then drove ONE
natural-language turn over the real WebSocket:

- `[boot] GOOGLE_* present in env? False` — zero GCP creds.
- `[boot] agent listening on ws://127.0.0.1:8865` — booted clean.
- 86 frames received. Claude-on-Bedrock did **multi-step tool reasoning**:
  pipeline steps `geocode_location → fetch_administrative_boundaries`, plus a
  `map-command` snap and 80+ `agent-message-chunk` streamed-narration frames,
  `cache-status`, auto-Case-creation (`case-open`/`case-list`).
- `[PASS] run-local on Bedrock: full server loop ran with zero GCP creds`.

The entire agent loop — model, tool dispatch, validator, pipeline emitter,
persistence, auto-Case — runs locally on AWS Bedrock with no Google Cloud and
no MongoDB.

## Known local limitations (documented, not blockers)
- Raster (flood/terrain) **rendering** still points at the cloud QGIS Server
  until job-0290 (ECS Fargate). Data fetches, chat, multi-step tool reasoning,
  vector layers (inline GeoJSON), and persistence work fully off-GCP.
- Bedrock prompt caching (cost parity with the Gemini CachedContent discount)
  is job-0288.

## How to run locally
```
make run-local-agent   # terminal 1 (needs AWS creds with Bedrock access)
make run-web           # terminal 2 → http://localhost:5173
```
