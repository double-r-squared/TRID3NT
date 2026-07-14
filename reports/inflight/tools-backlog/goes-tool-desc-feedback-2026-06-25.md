# Tools-session backlog: GOES tool-description fixes (NATE delegated 2026-06-25)

NATE routed these to the tools session explicitly ("for stuff like the goes identifier error let's delegate that to the tools agent").

## Items
1. **GOES satellite identifier**: the agent repeatedly emits `goes18` instead of the correct `goes-18` (and likely `goes19` vs `goes-19`). The EXACT, literal accepted identifier(s) must be stated explicitly in the tool description / arg schema (e.g. "satellite: one of 'goes-18' (GOES-West), 'goes-19' (GOES-East) - use the hyphen"). Audit fetch_goes_animation / fetch_goes_archive_animation / fetch_goes_satellite / fetch_glm_lightning arg docs.
2. **Tool-card label "Fetch Goes Archive Animation"** is false/inconsistent (per-frame fetch mislabeled). Replace with a generic, honest label like "Fetched GOES file"; capitalize "GOES" correctly everywhere.
3. **Principle (apply across tool descriptions)**: each tool description must carry ALL the info the LLM needs to SELECT + CALL it correctly (exact enums/identifiers, units), and NO extraneous info that dilutes selection. Sweep the satellite/animation tools for this.

## Why it matters
Wrong identifiers cause failed/retried tool calls + bad selection; the demos (fire/lightning) depend on correct GOES identifiers. This is the Class-B explicitly-defined-tool surface (hand-written tool+bridge), the tools session's domain.
