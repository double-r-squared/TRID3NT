# Model extensibility via OpenRouter - design 2026-07-19

NATE PRIORITY: pick free/paid models from OpenRouter (and other OpenAI-compatible
providers) like opencode. KEY FINDING: the agent adapter is ALREADY env-driven and
a per-turn model_id already flows end-to-end. v1 = config + plugin picker.

## Already built (agent side, no code needed for a basic connection)
- openai_adapter.py: openai_base_url() reads GRACE2_OPENAI_BASE_URL (L138); api_key
  reads GRACE2_OPENAI_API_KEY (L149); openai_model() precedence = per-turn model arg
  -> GRACE2_OPENAI_MODEL -> raise (L152). AsyncOpenAI(base_url, api_key) at L713.
- Per-turn model seam EXISTS: UserMessagePayload.model_id (ws.py L199) -> server
  resolve_selected_model -> stream_openai(model=...). resolve_selected_model passes
  ANY openai model id verbatim (bedrock_adapter L168). So an OpenRouter model id
  flows per-turn TODAY with zero agent changes, once base_url/key point at OpenRouter.
- start_agent.sh ollama keep-alive is documented harmless for remote providers.

## The gaps
- num_ctx: /api/show is ollama-only; OpenRouter falls to GRACE2_OPENAI_NUM_CTX
  fallback (default 16384 = too small for 128k models -> over-compaction). FIX =
  set GRACE2_OPENAI_NUM_CTX per provider preset (env, no code).
- headers: OpenRouter's optional HTTP-Referer / X-Title not sent (AsyncOpenAI has no
  default_headers). Optional polish - OpenRouter works without them.
- plugin has NO model/provider picker; send_chat does NOT send model_id (it sends
  show_thinking the same way - mirror it).

## Restart semantics (important)
- Change MODEL within a provider = LIVE per-turn (model_id rides user-message).
- Change PROVIDER (base_url/api_key) = AGENT RESTART (process env, no per-turn seam).

## Provider presets (static table; plugin combo + .env docs)
| preset | base_url | key env | example tool-capable model | num_ctx |
| local-ollama | http://127.0.0.1:11434/v1 | not-needed | qwen3:8b-24k | suffix |
| openrouter-free | https://openrouter.ai/api/v1 | OPENROUTER_API_KEY | meta-llama/llama-3.3-70b-instruct:free | 32768 |
| openrouter-paid | https://openrouter.ai/api/v1 | OPENROUTER_API_KEY | deepseek/deepseek-chat | 65536 |
| openai | https://api.openai.com/v1 | OPENAI_API_KEY | gpt-4o-mini | 128000 |
| groq | https://api.groq.com/openai/v1 | GROQ_API_KEY | llama-3.3-70b-versatile | 32768 |

## Plan
A. Agent polish (small, GRACE-2, no plugin collision): default_headers at
   openai_adapter L713 (guarded on GRACE2_OPENAI_HTTP_REFERER / _X_TITLE env, no-op
   otherwise). num_ctx handled by env per preset (no code). temperature env
   (GRACE2_OPENAI_TEMPERATURE) optional.
B. Turnkey config: a documented OpenRouter .env preset so NATE proves the path with
   his key + agent restart, ZERO code (build-order step 1).
C. Plugin picker (AFTER the chat-UI-notes batch lands - both edit dock.py):
   - plugin_settings.py: provider, model_id, api_key properties.
   - SettingsDialog (dock.py): provider preset combo + api-key field (password,
     "restart to apply" note - existing pattern) + model-id combo (static per
     provider). apply-on-Save.
   - Wire model_id: send_chat payload["model_id"] = settings.model_id
     (trid3nt_client.py L1277) + ws_bridge threading + dock call site - mirror
     show_thinking exactly. Model switch = live, no restart.

## Risks
- TOOL-CALLING (biggest): agent is tool-heavy (tool_choice=auto every round). Many
  free models ignore tools + narrate a fake answer (the _TOOL_DISCIPLINE_SYSTEM
  failure class). Curate the model list to tool-capable ids: deepseek/deepseek-chat,
  meta-llama/llama-3.3-70b-instruct, qwen/qwen-2.5-72b-instruct, mistralai/*. Treat
  free as best-effort.
- num_ctx must be set per preset or the clip guard false-trips.
- OPENROUTER_API_KEY is a live secret: keep out of git (.env.local gitignored),
  password echo in QSettings, never log.
- :free rate limits (per-min + daily) can 429 mid tool-heavy turn.

## v1 vs deferred
v1: agent default_headers + OpenRouter .env preset doc + plugin provider/model
picker + model_id wiring. Deferred: live /api/v1/models list route, in-chat model
switcher, cost/usage display, one-click provider switch (auto-write .env + restart).

## Build order
1. NATE proves the path: OpenRouter .env preset + his key + restart + one tool turn (zero code).
2. Agent: default_headers (only if step 1 needs it).
3. Plugin picker (after UI-notes batch) + model_id wiring.
