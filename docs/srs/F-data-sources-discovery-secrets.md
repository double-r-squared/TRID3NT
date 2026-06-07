## Appendix F: Data-Source Tiering, Discovery-First Lane, Deferred Secrets UX

> *(Forward-looking — v0.3.16 amendment. Tier-1 sources are operationally
> active from M4+; Tier-2 lands per-source as a user-provisioned key is
> wired (e.g. Census ACS after sprint-07); Tier-3 deferred indefinitely.
> The Discovery-First lane is operationally available when the public-
> hazard-catalog tools land (FR-PHC). The `request_secret` UX in §F.3 is
> deferred indefinitely until explicit user direction.)*

### F.1 Data-source tiering

The atomic-tool data fetchers in §3.3 FR-TA-2 draw from many public APIs. They are tiered by credential requirement so the agent has a default ordering when more than one source can answer the same question.

| Tier | Description | Routing rule | Examples |
|---|---|---|---|
| **1** — key-free public | No credential required; data is open. | Default for every atomic-tool fetcher when more than one source is available. | USGS 3DEP (DEM), NLCD / MRLC (landcover), ESA WorldCover (STAC), NHDPlus HR (river geometry), NOAA Atlas 14 PFDS (precipitation frequency), **WorldPop (STAC, population — Tier-1 default per F.1)**, Microsoft Open Maps Building Footprints, OSM Overpass, NOAA NHC ATCF (hurricane tracks), NOAA CO-OPS (tide gauges), NEXRAD Level II (Unidata S3), MRMS QPE (NOAA Open Data), USGS NWIS (streamflow), FEMA NFHL (flood zones), USFS Wildfire Hazard Potential, USDA Wildfire Risk to Communities. |
| **2** — key-required, free | Requires a one-time per-deployment (M4+) or per-user (deferred, see §F.3) provisioned API key, but the data itself is free. | Opt-in. Either the deployment ops sets the key once in Secret Manager, OR (post-§F.3) the user provisions per-user. | **US Census ACS B01003 (population, tract-level — Tier-2 alternative to WorldPop)**, NewsAPI free tier, NOAA api.weather.gov keyed endpoints, NASA Earthdata Login (for some GES DISC products), USGS EarthExplorer (some collections). |
| **3** — paid / commercial | Requires a paid subscription or per-request billing. | Opt-in only with explicit user consent + a future cost-surfacing follow-up (Invariant 9 currently bans cost-theater; expansion to "user-acknowledged paid sources" is a separate decision). | Mapbox geocoding (paid tier), PRISM 800 m subscription tier, premium news APIs (Reuters, Bloomberg). |

**Tier-1 preference rule.** When an atomic tool offers more than one source for the same data class, the Tier-1 source is the default and the Tier-2 / Tier-3 source is opt-in via an explicit parameter (e.g. `dataset="acs_2022"`, `source="mapbox"`). The per-tool docstring (FR-TA-3 metadata discipline) names the Tier-1 default and lists the available alternatives + their tier.

**WorldPop as the `fetch_population` Tier-1 default.** Updates the v0.1 default: `fetch_population(bbox)` defaults to WorldPop (Tier 1, no key). `fetch_population(bbox, dataset="acs_2022")` opts into the Census ACS B01003 tract-level Tier-2 source when the deployment has a Census key provisioned in Secret Manager. Job-0033's prior preference (ACS as default, WorldPop deferred) is reversed by this amendment — the no-friction default better serves new users; ACS opt-in better serves precision use cases.

**Per-deployment vs per-user provisioning of Tier-2 keys.** Until §F.3 lands, Tier-2 keys live at deployment scope in Secret Manager (one Census key per deployment, shared by all users). When §F.3 lands, Tier-2 keys can be provisioned per-user; deployment-scope provisioning remains as a fallback for ops-managed sources.

### F.2 Discovery-First lane (public hazard catalog)

For many user queries the right answer is to surface an authoritative pre-computed hazard layer rather than to run a solver. This is the Discovery-First lane — already scoped in §3.5.5 FR-PHC and FR-AS-9 Level 1b, formalized as a v0.1 design principle here.

**Routing rule.** When the user asks "show me X" and a curated catalog entry exists for X, the agent invokes the discovery workflow (`show_hazard_layer` per FR-TA-1) which uses `hazard_catalog_search` + `fetch_public_hazard_layer` + `summarize_layer_in_bbox` (FR-TA-2) to retrieve and characterize the layer. No solver runs; no API key needed; layer renders through QGIS Server within seconds.

**Catalog candidates** (Tier-1 per §F.1, key-free):

| Source | Hazard domain | Tier | Forward-looking workflow |
|---|---|---|---|
| FEMA NFHL | Flood zones (SFHA, BFE) | 1 | `show_hazard_layer(topic="flood_zone", location=…)` |
| USFS Wildfire Hazard Potential | Wildfire | 1 | `show_hazard_layer(topic="wildfire_risk", location=…)` |
| USDA Wildfire Risk to Communities | Wildfire (community-scale) | 1 | `show_hazard_layer(topic="wildfire_community", location=…)` |
| NOAA SLOSH | Storm-surge inundation | 1 | `show_hazard_layer(topic="storm_surge", location=…)` |
| USGS National Seismic Hazard Map | Seismic shaking | 1 | `show_hazard_layer(topic="seismic", location=…)` |
| NOAA Storm Events DB | Historical event records | 1 | `hazard_catalog_search(query="historical events", bbox=…)` |
| USGS Earthquake Catalog | Historical seismic events | 1 | `hazard_catalog_search(query="quakes since 2000", bbox=…)` |
| NOAA Climate Data Online | Gridded climate products | 1 | `show_hazard_layer(topic="climate", location=…)` |

Cache class per FR-DC-2: `static-30d` for the layer geometries (catalog re-issues are annual or slower); `semi-static-7d` for the catalog index when the source publishes weekly.

**Modeling vs Discovery — agent intent.** Discovery answers "where is the risk?" Modeling answers "how bad will it be under conditions X?" Both are valid; the agent's tool selection (per Decision G, two-layer architecture; the LLM's choice of which workflow or atomic tool to invoke is the classification) picks. A combined response is also valid — e.g. discover the FEMA SFHA overlay for context, then model a specific event over it. The agent shall prefer Discovery when the user query maps cleanly to an existing catalog entry (faster, no solver cost), and offer Modeling as a follow-up ("would you also like to model a specific scenario?") when appropriate.

**Implementation status.** `hazard_catalog_search` / `fetch_public_hazard_layer` / `summarize_layer_in_bbox` were defined in FR-TA-2 but deferred from M4 (sprint-06). Recommended landing alongside M5 (sprint-07) SFINCS, or as a fast-follow mini-sprint after M5 — landing the Discovery atomic tools requires only the public-hazard-catalog content (`public_hazard_catalog.yaml`, currently NOT YET CREATED — engine owner) plus straightforward HTTP / vector-tile retrieval. No new substrate.

### F.3 Deferred Secrets UX (`request_secret` envelope) — pop-up form, NOT inline chat

**Status:** deferred indefinitely until explicit user direction. NOT in v0.1, NOT in v0.2. Requires M6+ user-identity machinery as prerequisite (per-user Secret Manager namespacing depends on user identity). This appendix documents the architecture so it does not get reinvented when the time comes.

**Design intent.** When an atomic tool needs a Tier-2 / Tier-3 credential that the deployment has not provisioned, the agent should be able to ask the user to provide one *without the secret ever transiting the WebSocket chat envelope* (which is logged to MongoDB per Decision F, where credentials must never land).

**Architecture sketch:**

1. The atomic tool catches a "missing credential" upstream error (e.g. Census ACS returning HTTP 200 with the "Missing Key" HTML body) and invokes the (NEW, forward-looking) `request_secret(secret_name, signup_url, description) → secret_handle` atomic tool.
2. The agent emits a (NEW, forward-looking) `secret-request` envelope through the existing FR-AS-7 WebSocket emission seam. Appendix A amendment when this lands. Payload shape:
   ```jsonc
   {
     "type": "secret-request",
     "id": "01K…",
     "ts": "…Z",
     "session_id": "01K…",
     "payload": {
       "request_id": "01K…",
       "secret_name": "census_acs_api_key",
       "signup_url": "https://api.census.gov/data/key_signup.html",
       "description": "Census ACS B01003 population queries require a free API key. Click the link, sign up by email, then paste the key into the pop-up below.",
       "ttl_seconds": 1800
     }
   }
   ```
3. The web client renders a **dedicated pop-up modal** (NOT an inline chat input; see "Pop-up over inline" below). The pop-up shows the signup link as a clickable URL that opens in a new tab + a password-field input + a "Submit" button + a "Cancel" button. The modal is focus-trapped and dismissable; the chat scrollback stays visible behind it but is not interactive while the modal is open.
4. The user clicks the signup URL in a new tab, completes the signup flow off-app (typically email verification → key arrives in inbox), returns to the GRACE-2 tab, and pastes the key into the password field.
5. The pop-up form POSTs the secret directly to a small "secret-receiver" Cloud Function (NEW, forward-looking infra surface) over HTTPS. **The POST is a separate HTTP request, NOT the WebSocket.** The Cloud Function writes the secret to per-user Secret Manager at the path `users/<user_id>/secrets/<secret_name>` and returns a confirmation token.
6. The client emits a `secret-response` envelope (NEW, forward-looking) carrying the confirmation token (NOT the secret value):
   ```jsonc
   {
     "type": "secret-response",
     "id": "01K…",
     "ts": "…Z",
     "session_id": "01K…",
     "payload": {
       "request_id": "01K…",
       "status": "stored",   // or "cancelled" or "timeout"
       "confirmation_token": "..."
     }
   }
   ```
7. The agent receives `secret-response`, retries the atomic tool — which now reads the secret from Secret Manager via the agent-runtime SA's ADC and proceeds normally.

**Pop-up over inline — deliberate choice.** Two reasons the pop-up modal is preferred over a styled inline chat input:

- **Wire-level isolation.** The chat envelope (`user-message` etc.) is logged to MongoDB per Decision F. Routing the secret through a separate HTTPS POST (not the WebSocket) keeps the secret out of every chat-history persistence path. An inline chat input that "looks like a password field" would still send the typed value through the chat envelope unless the client implements special-case routing — which is more code and more subtle bug surface than just rendering a separate modal.
- **User context discrimination.** Visual separation reinforces to the user that they are typing into a credential surface, not a conversation. Reduces the chance of accidentally typing the key into the main chat box. Accessibility per FR-WC-4 (focus-trap, dismissable, screen-reader announces "secret input modal").

**Prerequisites that must land before §F.3 is implementable:**

- M6+ user-identity machinery (anonymous-session-scoped secrets are an option for v0.1 but the long-term value of the secrets UX is tied to per-user persistence, which means user accounts).
- A small `secret-receiver` Cloud Function with the appropriate IAM bindings (writes to `users/<user_id>/secrets/*` paths in Secret Manager; reads only from a CSRF-protected origin).
- Per-user Secret Manager namespacing convention (`users/<user_id>/secrets/<secret_name>`; lifecycle policies; rotation discipline).
- Appendix A amendment to add the `secret-request` and `secret-response` envelope shapes.
- Web client pop-up modal component (NEW; lives in `web/src/SecretRequestModal.tsx` or similar).
- Cancel + timeout behavior — the agent must handle `status: "cancelled"` and `status: "timeout"` from the client by emitting a "tool failed" step in the pipeline-state per Appendix A.7 and the D.6 `PipelineStepSummary` error fields landed in job-0030.

**Out of scope for §F.3 even when it lands.** The agent does NOT attempt to drive the signup flow itself (headless browser puppetry against third-party signup forms is brittle, breaks on captchas / email verification / ToS acceptance, and is v0.2+ at the earliest — see §5 Out-of-Scope deferred capabilities). The user is in the loop for the off-app signup step; the agent only handles surfacing the link, collecting the resulting key, and retrying the failed tool.

**Until §F.3 lands.** Tier-2 keys are deployment-scope: one key per deployment, provisioned by the operator via OpenTofu + Secret Manager (mirrors the job-0014 + job-0031 patterns). Atomic tools read from Secret Manager via the agent-runtime SA's ADC. When a Tier-2 fetch fails with "missing key" pre-§F.3, the failure surfaces as an `UPSTREAM_API_ERROR` per the FR-AS-11 clarification surface; the agent narrates the operator-action needed ("the deployment doesn't have a Census API key provisioned; the operator needs to provision one — here is the signup link: …") rather than soliciting input from the user directly. This is the M4 / sprint-06 fallback behavior already in place.

---

