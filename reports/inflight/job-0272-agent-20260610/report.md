# job-0272 — atomic publish_layer announces its layer to the map

**Defect (the third and final layer of the "no overlay" symptom):**
emit_tool_call only feeds add_loaded_layer / the session-state envelope when
a tool RETURNS a typed LayerURI. Composers do (floods/plumes always
rendered); atomic publish_layer returns a bare WMS string, so LLM-driven
fetch→compute→publish chains published server-side while the browser was
never told. **Fix:** the publish_layer tracking site in
_invoke_tool_via_emitter wraps the returned WMS URL in a LayerURI and hands
it to the emitter — session-state + per-Case layer persistence follow the
existing machinery.

**Evidence:** 2 regression tests (session-state announcement + accumulator);
agent suite 4317 passed / 5 known-pre-existing; LIVE Playwright session
(user-directed, real prompt, real Gemini turn, no inject seams) — overlay
renders on the map; WS capture + 4 screenshots under evidence/. The live
session also exposed the job-0273 case-open/case-list race (separate job).
