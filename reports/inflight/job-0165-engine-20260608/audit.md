# Audit: SRS Appendix I — LLM tool harness conventions

**Job ID:** job-0165-engine-20260608, **Sprint:** sprint-12-mega Wave 4.7, **Specialist:** schema (Opus)

## Scope
NEW `docs/srs/I-llm-tool-harness.md`:
- §I.1 Param naming convention (full words; aliases for legacy)
- §I.2 **kwargs absorb policy (every @register_tool absorbs `**_extra_ignored`)
- §I.3 Docstring discipline (`Examples:` block; no inline param-syntax-looking example strings)
- §I.4 Normalization layer (`tool_arg_normalizer.py` — sibling job-0164)
- §I.5 Per-tool tests (cross-cutting fuzz)
- §I.6 Decision F: harness conventions adopted 2026-06-08 per user direction "we can now tighten the harness"

Update `docs/srs/INDEX.md` to add Appendix I.

## File ownership
- `docs/srs/I-llm-tool-harness.md` (NEW)
- `docs/srs/INDEX.md`
- `reports/inflight/job-0165-engine-20260608/`

## FROZEN
docs/SRS_v0.3.md monolith (regenerated only). Single commit prefix `job-0165:`.
