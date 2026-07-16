# TELEMAC river-dye - ready-to-go test prompts (natural, route-verified)

Realistic prompts a real user would type that reliably route to the SURFACE-water
dye engine (`run_telemac`) rather than the groundwater seepage tools. Use these
verbatim when testing; they carry the disambiguating signal naturally.

## Root cause this solves
The local agent runs `GRACE2_TOOL_RETRIEVAL=enforce`, K=8 - only the 8 top-retrieved
tools are shown to the model per prompt. `run_telemac` had ZERO entries in
`tool_query_corpus.yaml`, so river+spill queries retrieved the seepage tools and
`run_telemac` was never even a candidate. Fixed 2026-07-16 (commit 882f9bd): added
12 spill-scenario corpus queries -> deterministic check shows 6/6 realistic spill
prompts now surface `run_telemac` in the top-8; groundwater controls still get the
seepage tools (no regression).

## SURFACE-water dye/spill (route to run_telemac) - verified retrieval
1. "A tanker truck overturned on the bridge and spilled chemicals into the Snake River near Twin Falls. Show how the contamination travels downstream over the next few hours."
2. "There was a chemical spill directly into the river at Twin Falls, Idaho. Model how the plume moves down the river channel and where it ends up."
3. "A factory discharged a pollutant into the river near Twin Falls. Animate it flowing downstream with the current."
4. "Someone dumped a contaminant into the Snake River at Twin Falls. How far downstream does it travel?"
5. "Simulate an oil spill on the river near Twin Falls and show the slick drifting downstream."
6. "Simulate a contaminant dye spill in the river near Twin Falls and show how it travels downstream."

**Disambiguating signal (why these work):** "spilled INTO the river", "down the
river channel", "flowing DOWNSTREAM with the current", "how far downstream". Surface
transport, not aquifer/seepage. Avoid bare "dye in the river" (ambiguous vs
groundwater) and never hand-type coordinates (natural-prompts rule).

## GROUNDWATER control (route to seepage / MODFLOW - must NOT change)
- "How does contamination from a leaking underground storage tank spread through the aquifer near Twin Falls?"
- "Model groundwater contamination seeping down from the river into the aquifer near Twin Falls."

## General pattern for adding retrieval reliability to any new engine
When adding a tool under enforce K=8: add 8-12 realistic user-phrasing example
queries to `services/agent/src/grace2_agent/data/tool_query_corpus.yaml` keyed by the
tool name, using the vocabulary a real user would type (not the tool's jargon), and
verify with `retrieve_visible_tools(prompt, None, 8)` (model-free, deterministic).
This is the durable, cheap alternative to lowering the routing temperature.
