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

### F.1.1 Access pattern tiering — the "data stores in the wild" problem *(Forward-looking — v0.3.17 amendment. v0.1 operates within the 4-tier happy-path enumeration below; agent-mediated discovery + adaptation to uncatalogued patterns is a v0.2+ capability and is captured as OQ-AT-2 below.)*

**The principle.** A hazard-modeling agent in operation will encounter geospatial data **wherever it lives** — across an arbitrary variety of provider conventions, access patterns, and protocols. The architecture must accommodate that variety rather than assume a uniform interface. The §F.1 credential tiering is one axis (which providers need a key); access pattern tiering is the **orthogonal axis** (how do we actually retrieve bytes once we know the source).

**v0.1 happy-path enumeration — four access tiers.** Every data-fetch atomic tool is implemented against exactly one tier, chosen at implementation time by the engineer based on a live verification of the upstream provider. The tier is recorded in the tool's FR-TA-3 docstring "Access pattern" line and (forward-looking — see schema note at end of §F.1.1) in an `AtomicToolMetadata.access_tier` field when the schema gains it.

| Tier | Pattern | Byte-shape per call | Cache discipline | Example providers (v0.1) |
|---|---|---|---|---|
| **1** — STAC + COG | STAC catalog (`/api/stac/v1/`) with Cloud-Optimized GeoTIFF backend; bbox-aware item query; HTTP Range supported | byte-window (≤ MB per fetch) | single-stage cache key `(source, bbox-quantized, vintage)` per FR-DC-3 | NASA / USGS via Microsoft Planetary Computer (some collections); STAC-hosted ESA WorldCover; future NASA Earthdata STAC collections |
| **2** — OGC service (WMS/WMTS/WCS/WFS) | Provider hosts only a query-rendering service; layer reference IS the URL; bytes computed server-side per request | per-tile render (varies) | **layer not cached to GCS** — the WMS URL itself is the cached reference; QGIS Server proxies / re-renders downstream | FEMA NFHL (Flood zones via WMS), USFS Wildfire Hazard Potential (WMS), NOAA SLOSH outputs (WMS), USGS National Seismic Hazard Map (WMS) — the §F.2 Discovery-First lane sources |
| **3** — Direct HTTPS file with Range support | Provider exposes raw GeoTIFF / FlatGeobuf / NetCDF URLs; HTTP server honors Range requests (returns `206 Partial Content`); GDAL `/vsicurl/` windowed reads work | byte-window (≤ MB) | same as Tier 1 — single-stage `(source, bbox-quantized, vintage)` | USGS 3DEP DEM tiles, NHDPlus HR FlatGeobuf, some NOAA Atlas 14 endpoints, MS Building Footprints PMTiles |
| **4** — Region download + local clip | Provider exposes only whole-region / whole-country file URLs; no Range support OR no bbox-aware index; full-file download required followed by local windowed clip | full-file (MB–GB) per region, then byte-window per clip | **two-stage cache** per OQ-37-COUNTRY-FILE-CACHING-STRATEGY: country file at `cache/static-30d/<source>/_regions/<region>.<ext>` (downloaded once, shared across all clips inside that region); per-call clip at `cache/static-30d/<source>/<hash>.<ext>` | WorldPop (job-0037 substrate; 50 MB 1km Aggregated per country), some legacy USGS products, older NOAA gridded archives |

**Tier-selection discipline (v0.1).**

- The tier is chosen at tool-implementation time, NOT at runtime. A tool is implemented against one specific tier; runtime fallback between tiers is **deferred indefinitely** (OQ-AT-1 below).
- The tier choice requires live verification of the provider — not just "does the provider claim to publish STAC." Specifically: a STAC catalog must be live AND its backend must support HTTP Range AND the COG headers must be valid. Otherwise the source falls to Tier 3 (if direct HTTPS with Range) or Tier 4 (if not).
- The tier is recorded in the tool's docstring per FR-AS-3 / FR-TA-3 metadata discipline. When the `AtomicToolMetadata.access_tier` schema field lands (forward-looking — see note at end of §F.1.1), the tier ALSO populates that field and is validated at registration per FR-CE-8.
- Tier 2 (OGC services) is structurally different: the cache shim is bypassed; the layer reference IS the URL; QGIS Server is the rendering substrate. This is the §F.2 Discovery-First lane's primary access pattern and lives outside the FR-DC cache architecture for layer bytes. (Metadata about the OGC source IS still cached per FR-DC-2 `semi-static-7d` — capability lists, layer indices, etc.)

**Forward-looking — Agent-mediated data-source discovery and adaptation (v0.2+).**

The v0.1 enumeration is exhaustive for the providers we anticipate registering tools against. But the design principle is broader: **the agent shall be able to handle an arbitrary new data source it encounters at runtime** — surfaced via user request, web research per FR-AS-9 capability discovery, or a §F.2 catalog amendment — and characterize its access pattern without engineer intervention.

The forward-looking capability mirrors FR-AS-9's solver discovery pattern, applied to data sources:

- **Discovery** (Level 1b analog): user query mentions a data source not in any registered atomic tool. Agent searches the web / official catalogs, finds the provider's access documentation.
- **Characterization**: agent probes the source to determine its tier — `HEAD` request to check for `Accept-Ranges: bytes`; `GET` to a STAC root URL to check for `/api/stac/v1/`; check for OGC `GetCapabilities` response shape. The agent records the discovered tier in a dynamic source registry.
- **Adaptation**: agent constructs a one-shot fetch using the discovered tier — Tier 1 STAC item query, Tier 2 WMS GetMap, Tier 3 `/vsicurl/` windowed read, or Tier 4 region-download fallback. The fetch routes through the same cache shim per FR-CE-8; the dynamic source gets an auto-registered `AtomicToolMetadata` (or equivalent) with the discovered tier + TTL class.

This capability requires several things the v0.1 architecture does NOT yet have:
- Agent-side autonomous network probing discipline (timeout, retry, error classification — needs Invariant 8 cancellation hooks).
- Dynamic tool registration at runtime (current `@register_tool` decorator runs at import; the FR-CE-8 fail-fast validation assumes startup-time discovery).
- A dynamic source registry that survives session restarts (likely a new MongoDB collection per Decision F).
- User-facing surfacing of "the agent discovered a new source" so the user can verify provenance per Decision M (claim provenance).

**Status: DEFERRED to v0.2+.** OQ-AT-2 below captures the question. v0.1 operates within the 4-tier happy-path enumeration with engineer-curated, implementation-time tier selection.

**Forward-looking schema note.** `AtomicToolMetadata` (`grace2_contracts.tool_registry`) does NOT currently carry an `access_tier` field. The forward-looking schema bump that adds it is intentionally deferred — for v0.1 the tier is recorded in the tool's docstring per FR-AS-3 / FR-TA-3. The schema bump lands in a future schema sprint when (a) downstream consumers actually need to introspect the tier (e.g., a tool-router that picks differently based on tier), or (b) the v0.2+ agent-mediated discovery capability above starts auto-populating the field for newly-characterized sources. Until then, the access tier is documentation-discipline, not enforced.

**Open Questions from §F.1.1.**

- **OQ-AT-1: Runtime fallback between access tiers** *(TENTATIVE: defer indefinitely)*. Should an atomic tool whose primary tier (e.g., Tier 1 STAC) goes down attempt a secondary tier (e.g., Tier 3 direct HTTPS) automatically? Adds latency + complexity to every fetch path; v0.1 prefers fail-fast `UPSTREAM_API_ERROR` so the agent's FR-AS-11 clarification surface decides next steps. Revisit if upstream reliability becomes a load-bearing problem (post-M9).
- **OQ-AT-2: Agent-mediated data-source discovery and adaptation** *(TENTATIVE: v0.2+)*. The forward-looking capability described above. Decision affects: agent capability surface (new FR-AS-* requirements), MongoDB schema (new dynamic-source-registry collection), Cloud Workflows orchestration (autonomous probing has timeout + retry needs that look like a mini-workflow), security posture (NFR-S — agent-initiated outbound requests to URLs from user queries are an attack surface; needs SSRF guardrails). Same shape as OQ-8 / OQ-9 / OQ-11 (forward-looking, decide before the capability ships).

---

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

