# Audit: Tool signature harness sweep — kwargs absorb + rename abbreviations

**Job ID:** job-0164-engine-20260608, **Sprint:** sprint-12-mega Wave 4.7, **Specialist:** engine (Opus)

## Why
Gemini routinely invents kwargs that don't exist on our tools (run_name, scenario_id, description, rainfall_event, return_period_years when ours is return_period_yr, etc.). Strict Python signatures fail loud on every one. We've been whack-a-mole patching; need a centralized sweep.

## Scope

**Part 1**: Add `**_extra_ignored: Any` to EVERY @register_tool function (~57 functions) — absorb-and-log unknown kwargs.

**Part 2**: Rename abbreviations + add backward-compat aliases:
- `_yr` → `_years`, `_hr` → `_hours`
- For each rename, keep old name as `<old>: int | None = None` and normalize at top of body

**Part 3**: NEW `services/agent/src/grace2_agent/tool_arg_normalizer.py`:
- `normalize_args(tool_name, raw_args)` — alias map + fuzzy match (e.g. `durationHours`→`duration_hours`) + string-form parsing
- Wire into server.py `_invoke_tool_via_emitter` BEFORE `entry.fn(**params)`

**Part 4**: Sanitize docstrings — example strings into a dedicated `Examples:` block (NOT inline as `forcing="atlas14_100yr"` which reads to Gemini as a real param).

**Part 5**: Pattern reference — `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` `run_model_flood_scenario` is the ground truth (already has `**_extra_ignored`, aliases, `forcing`/`rainfall_event` string parsing).

## Verify
Live test against running agent (PID 1833028 on port 8765): prompt "Model flood in Fort Myers" → confirm no `unexpected keyword argument` errors.

## File ownership
- All `services/agent/src/grace2_agent/tools/*.py` and `workflows/*.py` with @register_tool
- NEW `services/agent/src/grace2_agent/tool_arg_normalizer.py`
- `services/agent/src/grace2_agent/server.py` (wire normalizer)
- Tests
- `reports/inflight/job-0164-engine-20260608/`

## FROZEN
All other files. Codified lessons (job-0086 geographic gate, kickoff-front-loaded, MongoDB MCP persistence, pre-commit rebase). Single commit prefix `job-0164:`.
