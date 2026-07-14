# Live feedback - 2026-06-26 - DESKTOP / NOVA LITE (NATE, frustrated)

## CRITICAL / "NEVER SHOULD HAPPEN" + architectural
- **Bedrock crash**: starting with a bbox + "model a flood within the bbox" -> `LLM_UNAVAILABLE: ValidationException ... A conversation must start with a user message.` (Nova ConverseStream). The message array sent to Bedrock did not start with a user message. It self-recovered but the harness must guarantee a user-first message array. ALSO: NATE wants a PREVIEW of exactly what the LLM was fed (system prompt + message array) to trace pain points.
- **Too much on the LLM is breaking the system**: snapshots + layer publishing are LLM-enforced -> they fail/skip -> loading loops, no snapshot, can't see case. DIRECTIVE: make snapshot-taking + layer-publishing DETERMINISTIC (no LLM intervention). This is the root-cause theme.
- **Agent busy / can't stop / messages dropped (NEVER NEVER NEVER)**: woke agent, sent messages, NONE went through (no indication received); tried to sleep -> "busy"; no way to stop it. Need: hard force-stop/sleep that always works + messages never silently dropped + clear "received" indication.
- **Lower-end model ordering**: tried compute_hillshade BEFORE the DEM loaded; out-of-order tool calls. Harness should be tighter (enforce prerequisites) for weak models.
- **Loading loop on refresh**: refresh once = endless loading; refresh AGAIN = cases/everything appear. Recurring. (snapshot likely never taken)

## UI / scrubber (DIRECTION CHANGE)
- **Just make the scrubber STATIC at the bottom of the screen.** Fighting all the movement is killing him. Supersedes the snap-rule saga.
- Hiding the animation layer should ALSO hide the scrubber.
- Autoplay after a successful render: frame NUMBER changes but the scrubber handle does NOT move.
- (earlier, still valid) scrubber<->chat padding too tight; snap above wake-up/chat when covered.

## UI / desktop (asked MANY times)
- **LIFT THE DESKTOP CHAT PANEL BOTTOM BORDER UP** so it is NOT touching the bottom, and align it HORIZONTALLY with the settings button. (repeatedly requested - high frustration)
- Bottom of chat history too close to the text input form; text/buttons hidden behind the form.

## Tools / cards
- Can't see the TOOLS CATALOG to test anything. (clarified: he CAN see agent tools; thinks he could not when agent OFF)
- Tool cards don't appear until refresh.
- "sfincs solve" -> capitalize SFINCS; the active solve does NOT need a dropdown.
- Python sandbox cards (still): fold inline after completion/approval; FAILED -> red; acceptance gates fold inline (desktop+mobile); folded reuse dims+accent + GLOW while running; humanize "Layer statistics ready" -> "Code completed".

## Rendering / data
- GOES not always displayed esp. during animation (single-frame works); low resolution.
- Building footprints: storing full metadata (osm id/type/building/...) in the FRONTEND GeoJSON -> slow. Hypothesis: keep only ID in the GeoJSON, fetch the rest on CLICK to enrich the popup.
- Building vertices render as DOTS with CIRCLES around them -> just want the OUTLINE; the dots/circles obscure it.
- 3D terrain too aggressive -> layers extremely pixelated (~9 px).
- Sentinel-2 guardrail 0.5 deg^2 too strict for ~0.77 deg^2 AOI; no resolution gate offered; suggestion deck.gl for unified rendering + big-file/3D.
- True-color deletion deletes BOTH rasters; deleting one layer momentarily drops many (refresh restores).

## DELEGATED TO TOOLS SESSION
- goes18 vs goes-18 identifier; "Fetch Goes Archive Animation" label -> "Fetched GOES file" + capitalize GOES; tool descriptions carry all selection info, no extraneous. (see goes-tool-desc-feedback-2026-06-25.md)
