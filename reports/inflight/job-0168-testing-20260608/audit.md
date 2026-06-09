# Audit: Cross-cutting Gemini-kwargs fuzz test

**Job ID:** job-0168-testing-20260608, **Sprint:** sprint-12-mega Wave 4.7, **Specialist:** testing (Sonnet)

## Scope
NEW `services/agent/tests/test_gemini_kwargs_fuzz.py`:
- Iterate every @register_tool function in TOOL_REGISTRY
- For each, generate 20 invented kwarg patterns (run_name, description, durationHours, scenario_id, rainfall_event="atlas14_100yr", etc.)
- Call via the normalizer (from job-0164) + verify no TypeError + result is reasonable (real call OR graceful default fallback)

Regression guard for the harness sweep.

## File ownership
- `services/agent/tests/test_gemini_kwargs_fuzz.py` (NEW)
- `reports/inflight/job-0168-testing-20260608/`

## FROZEN
All implementation files. Single commit prefix `job-0168:`.
