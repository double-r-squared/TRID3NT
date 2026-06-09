# Opus parallel diagnostic — job-0154 cross-check (a7a32d4a90c4c40f1)

**Verdict**: high confidence. Opus independently arrived at hypothesis B (registration disconnect) with direct file:line evidence and confirmed Sonnet's working-tree diff addresses all 3 required cross-checks.

## Root cause

`register_with_adk()` in `tools/__init__.py:190` is defined but **never called**. The server-side LLM path goes `server.py: _stream_gemini_reply` → `adapter.stream_reply` which calls `genai.Client` directly with:

```python
config=genai_types.GenerateContentConfig(
    temperature=0.7,         # no tools=, no system_instruction
)
```

Gemini was **provably blind** to the TOOL_REGISTRY. The HEAD comment was the smoking gun: "Tool config / function declarations land when the ADK tool registry comes online" — that migration never happened.

## Cross-checks for Sonnet's commit (all PRESENT in working tree)

1. `automatic_function_calling=AutomaticFunctionCallingConfig(disable=True)` — required to keep Python callables from being auto-invoked (preserves FR-AS-3 emitter contract) ✓
2. `FunctionDeclaration.from_callable_with_api_option(callable=entry.fn, api_option="VERTEX_AI")` — preserves docstring discipline as the only Gemini matching signal ✓
3. Dispatch through `_invoke_tool_via_emitter` (NOT direct `entry.fn(**args)`) — preserves pipeline-state envelopes + payload-warning + per-Case .qgs lazy-init ✓

## NEW issues surfaced by Opus diagnostic (not in original kickoff)

- **OQ-0154-FUNCTION-RESPONSE-FEEDBACK** (REAL BUG): single-shot dispatch — no multi-turn function_call → function_response loop. User sees tool emissions but NOT Gemini's natural-language narration of the result. The agent calls the tool then stops; doesn't feed the result back for prose.
- **OQ-0154-CHAT-HISTORY-ASYMMETRY** (REAL BUG): agent's reply never appended to chat_history; only user-side turns persist. On turn N, Gemini sees `[user_1, user_2, ..., user_N]` — no prior agent context. Compounds across turns.
- OQ-0154-ADK-DEAD-CODE: `register_with_adk()` should be deleted
- OQ-0154-TOOL-CATALOG-SIZE: 50+ tools may exceed Gemini's per-request tool-declaration budget; need pruning/two-stage classifier strategy
- OQ-0154-EMPTY-AGENT-MESSAGE-CHUNK: stray empty bubble when function call returns no text
- OQ-0154-SDK-PART-SHAPE-DRIFT: pin google-genai version in pyproject.toml

## Cost

96,569 Opus tokens.

