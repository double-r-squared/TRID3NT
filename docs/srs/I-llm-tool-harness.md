## Appendix I: LLM Tool Harness Conventions

> *(Decision F harness conventions, adopted 2026-06-08 per user direction "we can now tighten the harness." Binding from sprint-12-mega Wave 4.7 forward — see job-0164 (engine sweep) and job-0165 (this appendix). The conventions formalize the lessons of ~57 atomic tools shipped through sprint-12 against a Gemini-3 frontier LLM that routinely invents kwargs, abbreviation variants, and natural-language parameter strings.)*

**Purpose.** This appendix codifies five conventions that every `@register_tool`-registered atomic tool and workflow in `services/agent/src/grace2_agent/{tools,workflows}/*.py` must conform to, plus the centralized normalization layer that backstops them. Together they harden the agent harness against the empirically observed failure mode where a frontier LLM emits well-intentioned but invented arguments — `run_name`, `scenario_id`, `description`, `rainfall_event="atlas14_100yr"`, `return_period_years` when the tool defines `return_period_yr`, `durationHours` when the tool defines `duration_hours` — and Python's strict signature-binding rejects every one with a `TypeError: unexpected keyword argument`. The conventions trade a small amount of per-tool boilerplate for end-to-end resilience: the harness absorbs noise, normalizes legitimate variants, fails loud only on substantive ambiguity, and surfaces unknown kwargs to logs without blocking the call.

**Scope.** These conventions apply to **every** function decorated with `@register_tool` — atomic tools (FR-TA-2), workflow exposures (FR-TA-1), pass-throughs to MongoDB MCP, and dispatchers (e.g. `run_solver`, `catalog_fetch`). They are out of scope for: schema models (Appendices A–D — those are pydantic v2 contracts, not tool signatures), client-side rendering, infra provisioning, and tests (which exercise the conventions but do not author them).

**Relationship to other contracts.** This appendix is a **convention layer**, not a schema. It does not introduce new Appendix A messages, Appendix B/C/D fields, or Appendix F catalog entries. It documents the discipline that the `agent` specialist enforces in code and that `engine` specialists conform to when registering tools. Tool-result shapes remain governed by Appendices A–D (e.g. `LayerURI` for layer-emitting tools, `AssessmentEnvelope` for workflows). Invariants 1 (determinism boundary) and 7 (claims carry provenance) are preserved verbatim — the harness silently absorbs *input* noise but never silently fabricates *output* values.

### I.1 Parameter naming convention — full words, with bounded backward-compat aliases

**Rule.** Parameter names use full unabbreviated English words separated by `_`. The two specific abbreviations historically embedded in the v0.1 atomic-tool surface are renamed and an alias is retained for backward-compatibility during the v0.1 transition.

- `_yr` (year, years) → `_years` (e.g. `return_period_yr` → `return_period_years`)
- `_hr` (hour, hours) → `_hours` (e.g. `duration_hr` → `duration_hours`)

**Why.** Gemini-3 (the v0.1 LLM per FR-AS-1) generates `return_period_years` and `duration_hours` by default; the abbreviated forms read as cryptic to both the model and to humans inspecting tool docstrings via `tool-call-start` envelopes (A.4). Standardizing on full words eliminates the most common observed `unexpected keyword argument` failure class without per-call normalization.

**Alias discipline.** For each renamed parameter, the old name is retained as `<old>: <type> | None = None` and normalized at the top of the function body. The alias never participates in the public docstring `Params:` section (it is implementation detail), but is retained until v0.2 to honor in-flight scripts and prompt-cached LLM examples that learned the v0.1 names. Example pattern (verbatim from `run_model_flood_scenario`):

```python
async def run_model_flood_scenario(
    ...,
    return_period_years: int = 100,
    duration_hours: int = 24,
    # Backward-compat aliases for legacy short forms
    return_period_yr: int | None = None,
    duration_hr: int | None = None,
    **_extra_ignored: Any,
) -> LayerURI | dict[str, Any]:
    """..."""
    effective_return_period = (
        return_period_yr if return_period_yr is not None else return_period_years
    )
    effective_duration = duration_hr if duration_hr is not None else duration_hours
    ...
```

**Forward extension.** Any future abbreviation hits this rule by construction: a new tool author writes the full-word form from the start. Aliases are only introduced for renames of *already-shipped* parameters, never for new ones.

**Pluralization.** The full-word form is plural when the underlying quantity is a count or a span (`years`, `hours`, `meters`, `kilometers`). Singular forms (`year`, `hour`) are reserved for the rare case where a single discrete unit is meant (e.g. a calendar year, not a duration). Default to plural; if uncertain, prefer plural.

**Non-goals.** This rule does *not* mandate fully verbose names where the abbreviation is universally understood and not a unit-bearing suffix (e.g. `bbox` stays `bbox`, not `bounding_box`; `dem` stays `dem`, not `digital_elevation_model`; `crs` stays `crs`, not `coordinate_reference_system`). The renames target unit-suffix abbreviations that the LLM has no prior reason to expect.

### I.2 `**_extra_ignored` absorb-and-log policy

**Rule.** Every `@register_tool`-registered function shall accept `**_extra_ignored: Any` as its final parameter, after all positional-or-keyword parameters and any backward-compat aliases.

**Rationale.** Strict Python signature binding rejects unknown keyword arguments with `TypeError`. Frontier LLMs routinely emit kwargs that look reasonable from the prompt or examples but do not exist on the target tool — `run_name`, `scenario_id`, `description`, `notes`, `mode`, `version`, `priority`. With strict binding, each invented kwarg fails the entire call; absorb-and-log accepts the call, ignores the noise, and surfaces the kwargs to structured logs for harness telemetry.

**Naming.** The parameter name is exactly `_extra_ignored` (underscore prefix marks it as deliberately unused per PEP 8 convention; the name `_extra_ignored` is the project convention so harness tooling and reviewers can grep for it). Do not rename it per-tool.

**Type annotation.** `**_extra_ignored: Any`. The `Any` annotation is deliberate — the values are not validated and not consumed.

**Logging.** When the function body receives a non-empty `_extra_ignored`, the harness shall log at `INFO` level a structured record `{tool_name, ignored_keys, session_id?}` so the operations side can monitor which kwargs the LLM is inventing most often. Logging is the responsibility of the centralized normalizer (§I.4) when it executes before the tool body; tools themselves do not need to add a log line. (Per §I.4, the normalizer logs only the *unknown-after-normalization* residue, not the raw input keys — keys consumed by alias normalization are not "ignored.")

**Interaction with `extra="forbid"` on pydantic models.** The absorb policy applies to **tool signatures only**, not to pydantic models. Schema models (Appendices A–D) retain `extra="forbid"` per the `grace2-contracts` v0.1.0 discipline — wire shapes are strict, tool signatures are permissive. The two layers serve different purposes: schemas enforce wire-level integrity (Invariant 7); tool signatures protect against LLM-side noise.

**Reference pattern.** `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` `run_model_flood_scenario` is the canonical example (already in production, sprint-07 substrate); job-0164 propagates the pattern across the remaining ~57 functions.

### I.3 Docstring discipline — no inline param-syntax-looking example strings

**Rule.** Docstrings shall not embed example values that *look like* valid Python keyword arguments inside the prose. Specifically:

- Do NOT write inline as prose: `... pass forcing="atlas14_100yr" to invoke ...`
- Do NOT write inline as prose: `... use return_period_years=100 for ...`

Such strings are visually indistinguishable from real parameter definitions when Gemini-3 reads the docstring via the ADK FunctionTool surface — the model frequently re-emits them verbatim as call arguments, even when no parameter by that name exists on the tool. This is the empirically observed root cause of invented kwargs like `forcing` and `rainfall_event` on the flood-scenario workflow.

**Format.** Per-tool docstrings shall conform to the following sectioned format (already mandated in part by FR-AS-3 / FR-TA-3 — this appendix tightens it):

```
<one-sentence summary on a single line>

Use this when: <natural-language usage triggers, with example USER PROMPTS in
quotes — these are demonstrations of *what the user might say*, not example
parameter values>. Multiple sentences allowed.

Do NOT use this for: <complementary exclusions>.

Params:
    <param_name>: <one-line description>. Default <default>.
    ...

Returns:
    <one-line description of the success return type>.

    <Optional block for failure / partial-failure shape.>

Examples:
    >>> result = await <tool_name>(<keyword>=<value>, <keyword>=<value>)
    >>> <follow-up call illustrating composition>

<Optional FR-XX / Appendix-X anchor notes.>
```

**The `Examples:` block.** All call-shaped examples — anything that looks like Python syntax for calling the tool — live exclusively under a dedicated `Examples:` block at the end of the docstring. The block uses doctest-style `>>>` prefixes so the LLM has an unambiguous signal that the lines are demonstration code, not prose. Free-text "use this when" examples (in the `Use this when:` block) are framed as user prompts in quotes, never as parameter expressions.

**Why this works.** Gemini-3 treats prose `key=value` substrings as if they were function-signature examples and re-emits them. Quoted user prompts (`"model the flood from a 100-year storm in Fort Myers, FL"`) are read as natural-language utterances. A dedicated `Examples:` block isolates call syntax to one labeled region the model can parse without confusion.

**Migration discipline.** Existing tool docstrings that embed inline `key="value"` substrings shall be rewritten under this convention. The mechanical pass is part of job-0164's Part 4. New tools (sprint-12-mega Wave 5 onward) shall conform from the first commit.

**Cross-reference to FR-AS-3 / FR-TA-3.** Those FRs mandate the existence of docstring metadata (one-sentence summary, "Use this when:", "Do NOT use this for:", param + return descriptions). This appendix specifies the **discipline of example placement** within that structure — the prior FRs did not anticipate the inline-`key=value`-as-prose hazard.

### I.4 Normalization layer — `tool_arg_normalizer.py`

**Rule.** All tool invocations dispatched by the agent's `_invoke_tool_via_emitter` (or equivalent ADK FunctionTool callsite in `services/agent/src/grace2_agent/server.py`) shall route through a centralized normalizer **before** the underlying `entry.fn(**params)` call.

**Module location.** `services/agent/src/grace2_agent/tool_arg_normalizer.py` (sibling to `server.py`, owned by `agent` specialist; job-0164 lands the initial implementation in code).

**Surface.** A single entry point:

```python
def normalize_args(
    tool_name: str,
    raw_args: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (normalized_args, dropped_unknown_args).

    normalized_args is suitable for `entry.fn(**normalized_args)`.
    dropped_unknown_args is logged at INFO; callers should not
    inject it back into the call.
    """
```

**Behaviors (in order).**

1. **Alias map** — a tool-name-keyed registry of `{old_param: new_param}` mappings. Maintained alongside the §I.1 backward-compat aliases; when an old name appears in `raw_args`, the value is moved to the new name (if the new name is absent) and the old key is removed. Conflicts (both old and new keys supplied with different values) are logged at WARNING and the new-name value wins.
2. **Fuzzy match** — for keys that exactly match neither a real param nor a known alias, attempt a bounded Levenshtein / camelCase-to-snake_case normalization (e.g. `durationHours` → `duration_hours`, `returnPeriodYears` → `return_period_years`, `bbx` → `bbox` only if edit distance ≤ 1 and the candidate is unambiguous). Multiple ambiguous candidates → no rewrite; key falls through to the absorb-policy bucket. Fuzzy match is conservative by default; the matcher has a hard cap of one rewrite per call key.
3. **String-form parsing** — for parameters with documented string-form shorthands (e.g. `forcing="atlas14_100yr"` parsing to `return_period_years=100` per `run_model_flood_scenario`), apply per-tool string parsers registered alongside the alias map. The reference implementation is in `run_model_flood_scenario` body (job-0042 substrate); the centralized normalizer hoists this into a reusable layer.
4. **Drop-and-collect unknowns** — any key remaining after steps 1–3 that does not match a real parameter name on the target function shall be collected into `dropped_unknown_args` (the second tuple member), logged at INFO with `{tool_name, dropped_keys, session_id}`, and absent from `normalized_args`. The `**_extra_ignored` absorb at the function level (§I.2) is the safety net for any kwargs that escape the normalizer (e.g. when the registry has not yet seen the tool).

**Where it wires.** `services/agent/src/grace2_agent/server.py` in `_invoke_tool_via_emitter` (or the renamed equivalent) shall call `normalized, dropped = normalize_args(tool_name, raw_args)` immediately before `await entry.fn(**normalized)`. Dropped kwargs are logged via the existing structured logger; they are not transmitted on the `tool-call-start` or `tool-call-complete` envelope (they are harness-internal telemetry, not user-visible).

**Failure handling.** The normalizer never raises on unknown keys (that is the whole point). It MAY raise on internal-consistency errors (registry malformation, mismatched alias map). Raised errors are routed through the agent's existing error envelope (A.6 `error` message, code `TOOL_ARG_NORMALIZER_FAILED` — a new code to be added to A.6 in a sibling schema amendment).

**Telemetry.** Per-tool counters of `{normalized_count, dropped_count, fuzzy_rewrite_count}` are emitted to the agent's structured-log stream so operations can spot tools that disproportionately attract invented kwargs — those are candidates for docstring tightening or signature redesign.

**Out of scope for the normalizer.** It does **not** validate types (pydantic does that at the schema layer where applicable). It does **not** auto-fix substantively wrong values (e.g. a negative `return_period_years`). It does **not** silently fabricate defaults — defaults remain the responsibility of the tool's function signature. It is a *naming-noise* shim, not a *value-validation* shim.

### I.5 Per-tool tests — cross-cutting fuzz

**Rule.** Every `@register_tool`-registered function shall have a cross-cutting test that exercises the §I.2 absorb policy and §I.1 alias acceptance.

**Test surface (recommended).** A single parametrized test file `tests/test_tool_harness_conventions.py` (owned by `testing`; out of scope for this appendix to author) that iterates over the live registry and asserts, for each tool:

1. The function accepts an empty kwargs dict via the absorb policy (i.e. calling with `**{"run_name": "smoke"}` does not raise `TypeError`).
2. Each documented alias resolves to its new-name parameter without error (when applicable per §I.1).
3. The docstring conforms to the §I.3 sectioned format — `Use this when:`, `Do NOT use this for:`, `Params:`, `Returns:`, `Examples:` sections exist; no inline `key="value"` prose in `Use this when:` / `Do NOT use this for:` / `Params:` regions.
4. The signature ends with `**_extra_ignored: Any`.

**Fuzz layer (recommended).** A property-based test that feeds tools a kwargs dict including 0–5 randomly generated unknown keys (alphanumeric, length ≤ 12, plus snake_case noise) and asserts the call returns without raising signature-binding `TypeError`. Coverage target: every tool returned by the registry, run with a fixed seed for reproducibility.

**CI gate.** The per-tool conformance tests run on every PR touching `services/agent/src/grace2_agent/{tools,workflows}/*.py` and gate merge. The fuzz tests run on every PR touching the normalizer or any registered tool.

**Test ownership.** `testing` specialist authors the test file and CI hooks; `engine` and `agent` specialists ensure their tools pass.

### I.6 Decision F (harness conventions) — adopted 2026-06-08

**Decision.** The five conventions §I.1–§I.5 above are adopted as the LLM tool-harness discipline for GRACE-2 v0.1 forward, binding from sprint-12-mega Wave 4.7. The decision is recorded here (Appendix I) and in §2.1 Decisions (Decision F slot — already pinned to "harness conventions" by orchestrator update; this appendix is the canonical reference for the convention body).

**Rationale.**

- **Empirical signal.** Across ~57 atomic tools and ~5 workflows shipped through sprint-12-mega, the single most-common reason an LLM tool call fails before any application logic runs is signature-binding mismatch — invented kwargs, abbreviation variants, natural-language string-form parameter values. Whack-a-mole patching of individual tools has been the pattern through sprint-12; centralized conventions stop the cycle.
- **No backward-compat for *new* shapes.** Per the AGENTS.md pre-MVP cross-cutting principle, no backward-compat shims for new shapes; the §I.1 aliases are the **only** backward-compat layer, and they are bounded to already-shipped parameter names (`_yr` / `_hr`) being renamed. New tools (sprint-12 Wave 5 forward) ship with full-word names from the first commit and do not get aliases.
- **Convention, not schema.** This appendix does not introduce new wire fields or pydantic models; it tightens *implementation discipline*. Schemas (Appendices A–D) remain strict (`extra="forbid"`); tool signatures become permissive (`**_extra_ignored`). The two layers serve different purposes.
- **Forward-looking surface.** As new tools land — Pelicun (M5.5), TELEMAC (M11), conservation utilities (OQ-11 pending), Tier-2 fetchers, Mode-2 catalog adds — the conventions apply uniformly without further amendment.

**Alternatives considered.**

- **Strict signature with whitelist auto-strip** — reject the call early on unknown kwargs and let the agent re-prompt. Rejected: re-prompting is expensive in turns and tokens; the LLM frequently re-invents the same kwargs the second time.
- **Pydantic models in every signature** — define a model per tool, use `model_construct` with `extra="allow"`. Rejected: too heavy for atomic tools; pydantic v2's per-tool overhead dwarfs the call body for simple fetchers; harms readability and makes ADK FunctionTool registration less direct.
- **Pure normalizer with no `**_extra_ignored`** — rely on the centralized normalizer alone to strip unknown kwargs. Rejected: the normalizer cannot know about every tool at all times (registry races during reload), and `**_extra_ignored` is a one-line per-tool safety net with no downside. Belt-and-suspenders.

**Forward path.**

- **v0.2 alias retirement.** When v0.2 ships, the §I.1 backward-compat aliases (`return_period_yr`, `duration_hr`) are removed; the full-word names are the only accepted form. Doctring `Use this when:` / `Examples:` blocks updated to reference only full-word names.
- **Normalizer evolution.** The normalizer's fuzzy-match cap may relax as telemetry shows which rewrites are safe. The string-form parser registry grows per-tool as new shorthand patterns emerge from production usage.
- **Convention propagation to non-tool surfaces.** If the agent later exposes resource-typed handles (MCP resources, tool-class sub-types), the absorb policy may extend to those surfaces under a sibling appendix.

**Cross-references.**

- §2.1 Decision F (harness conventions row) — this appendix is the body.
- FR-AS-3 (atomic-tool metadata discipline) — extended by §I.3.
- FR-TA-3 (tool-docstring discipline) — extended by §I.3.
- Invariant 1 (determinism boundary) — preserved; harness absorbs *input* noise but never silently fabricates *output* values.
- Invariant 7 (claims carry provenance) — preserved; tool result shapes are unchanged.
- Invariant 10 (minimal parameter surface) — preserved and reinforced; full-word names + no inline example syntax = smaller cognitive parameter surface.
- AGENTS.md pre-MVP "no legacy support" — honored; the only backward-compat is the bounded §I.1 alias set for already-shipped parameter names.
- Job-0164 (engine sweep) — the implementation companion to this appendix; lands `**_extra_ignored`, renames, docstring fixes, and wires the normalizer.
- Job-0165 (this appendix) — authors the appendix itself.

---

