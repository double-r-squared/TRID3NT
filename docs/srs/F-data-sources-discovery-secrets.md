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

### F.1.2 Trust model for source discovery — three-mode framing *(v0.3.18 amendment; binding for v0.2+ catalog substrate, sprint-08 scope.)*

**The principle (refined from §F.1.1 OQ-AT-2).** The "data stores in the wild" capability (§F.1.1) requires a **trust model** that bounds where the agent can fetch from. Naïve autonomous discovery (probe any URL the agent encounters) creates an SSRF attack surface, provenance ambiguity, and license-attribution risk. Naïve catalog-only operation (no agent-mediated growth) means the catalog calcifies — new authoritative data products dropping don't reach users until an engineer manually curates them. The architecture splits the trust surface into three explicit modes, with the broad uncertain-source case deferred until the discipline matures.

**Mode 1 — Catalog-mediated (PRIMARY; v0.1 binding for sprint-08).** The curated `public_data_source_catalog.yaml` (and its MongoDB-collection successor `catalog_entries` per Decision F) is the single source of truth for vetted endpoints. Every entry is **research-driven and labeled** at curator time with:

- `id`, `name`, `description` — identification
- `url(s)` — primary endpoint + alternative mirrors when they exist
- `access_tier` per §F.1.1 (STAC, OGC service, HTTPS+Range, region-download)
- `ttl_class` per FR-DC-2
- `source_class` per FR-DC-1
- `credential_tier` per §F.1 (key-free, key-required, paid)
- `license`, `citation`, `vintage`, `last_verified`
- `status` — `active`, `deprecated`, `user_proposed_pending_curator_review` (see Mode 2)
- **"How to use" metadata** — invocation examples, parameter constraints, known quirks (e.g., "WorldPop returns HTTP 200 not 206 for Range requests — use region-download tier; specify country in `params.iso3`"). This labeling is the difference between a sterile URL list and an actionable catalog.

Atomic tools that consume the catalog (sprint-08 scope, FR-TA-2 additions):

- `catalog_search(topic, location?, source_filter?) → list[CatalogEntry]` — agent queries the catalog by domain (terrain, hydrology, weather, building, population, landcover, hazard, etc.) + optional spatial + filter. Returns ranked matches.
- `catalog_fetch(entry_id, params) → LayerURI | dict` — generic fetcher that dispatches to the entry's `access_tier` (Tier 1 STAC query / Tier 2 OGC WMS GetMap / Tier 3 `/vsicurl/` windowed read / Tier 4 region download + clip). Cache shim discipline per FR-DC-3 / FR-CE-8 applies; entry's `ttl_class` + `source_class` populate the cache key.

The existing hardcoded atomic tools (`fetch_dem`, `fetch_landcover`, `fetch_population`, `geocode_location`) coexist with catalog-driven access. Hardcoded tools remain the **friendly per-domain shortcuts** for the canonical sources (3DEP for DEM, NLCD for landcover); the catalog covers the long tail (state GIS portals, regional gauge networks, alternative providers). At engineer discretion, hardcoded tools may later be reimplemented as catalog-driven syntactic sugar (post-v0.1 consolidation).

**Mode 2 — Offer-to-add on `.gov` and `.edu` (v0.2+; bounded growth path).** When the agent encounters a candidate URL during research or user-query interpretation that is (a) not in the catalog AND (b) hosted on `.gov` or `.edu`, the agent shall NOT autonomously fetch. Instead:

1. The agent performs a **conformity probe** — HEAD request (check `Accept-Ranges`, content-type, TLS cert org subject), STAC root check (`<base>/api/stac/v1/` or `<base>/stac/catalog.json`), OGC `GetCapabilities` check, COG header inspection. Each probe respects the SSRF guardrails below.
2. The agent emits an `offer-catalog-addition` envelope (NEW Appendix A amendment — sprint-08 schema scope):
   ```jsonc
   {
     "type": "offer-catalog-addition",
     "id": "01K…", "ts": "…Z", "session_id": "01K…",
     "payload": {
       "request_id": "01K…",
       "url": "https://example.gov/data/foo",
       "discovered_via": "user-query | web-research | …",
       "probe_findings": {
         "tls_cert_org": "U.S. Department of …",
         "access_tier_inferred": 1,                 // §F.1.1 tier
         "supports_range_requests": true,
         "stac_root_found": false,
         "ogc_capabilities_found": true,
         "license_observed": "Public domain (U.S. Federal data)",
         "content_type": "application/json",
         "last_modified_header": "…"
       },
       "suggested_catalog_entry": {
         "id": "femanflp-discharge-…",
         "name": "FEMA NFHL discharge stations",
         "access_tier": 2,
         "ttl_class": "semi-static-7d",
         "source_class": "flood_zone",
         "credential_tier": 1,
         "license_claim": "Public domain (US Federal)",
         "how_to_use": "OGC WFS GetFeature; bbox in EPSG:4326; … "
       },
       "ttl_seconds": 600
     }
   }
   ```
3. The client renders a **dedicated review modal** (mirrors §F.3 secret-form pattern — popup, focus-trapped, separate from chat envelope) showing the URL + probe findings + the suggested catalog entry. The user accepts, rejects, or edits.
4. On accept, the agent writes the entry to the catalog with `status: "user_proposed_pending_curator_review"`; the catalog query then includes this entry but a curator review is required (out-of-band) to flip status to `active`. This keeps the growth path bounded — entries that fail curator review are removed without ever having been part of the "active" surface.
5. On reject, the agent falls back to an alternative cataloged source for the user's query OR surfaces failure ("I couldn't find an active source for your query; the candidate I found at <url> was declined").
6. All offer-to-add events are recorded in an audit log (MongoDB collection `catalog_audit_log` per Decision F): URL, user, classification, probe findings, accept/reject, eventual curator-review outcome. Provenance per Decision M.

**Why `.gov` and `.edu`?** Both have registry-controlled policing (DotGov Registry / EDUCAUSE) sufficient to bound the agent's autonomous-probing surface. False positives (bad data on a `.gov` URL — press releases vs. structured data, deprecated endpoints, contractor content) are caught by the conformity probe — the OGC GetCapabilities check rejects a press release outright. Cross-confirmation with the existing catalog is the additional defense: if a `.gov` URL is wildly inconsistent with the catalog's other entries for the same domain (license, vintage, format conventions), the user's review modal surfaces that mismatch.

**Mode 3 — Anything else: DEFERRED INDEFINITELY (per user direction 2026-06-07).** Non-`.gov`/non-`.edu` URLs the agent encounters (general `.com`, `.org`, country TLDs, IP addresses, etc.) shall NOT be probed and shall NOT trigger an `offer-catalog-addition` flow. When the agent encounters such a URL during research or query interpretation, it narrates:

> "I found a candidate source at `<url>` that may be relevant, but it doesn't meet the v0.1 trustworthiness criteria for autonomous use. You can review it manually and add it to the catalog via the curator CLI if it's appropriate."

This is the muddier case the trust signal is hardest to read on — `.org` includes both OpenStreetMap and questionable advocacy sites; `.com` includes Microsoft Planetary Computer (excellent) and arbitrary commercial sites. The curator-only path keeps these sources reachable but **requires explicit human curation** rather than agent judgment. Revisit when:

- Decision M provenance discipline is fully operational across the agent
- User-identity machinery from §F.3 lands (per-user catalog adds become accountable)
- A more nuanced trustworthiness signal is implementable (cross-confirmation against NASA CMR + Microsoft Planetary Computer + USGS ScienceBase + academic citation graph)

OQ-AT-3 below captures the question.

**Curator-side validation criteria (applies to Mode 1 hand-curated AND Mode 2 user-accepted adds before they flip to `status: "active"`):** four orthogonal axes — domain provenance (TLD policing + cert org subject), protocol conformity (response matches a known geospatial standard), metadata sufficiency (declared license + citation + vintage), cross-confirmation (entry referenced by ≥1 of: existing catalog entries, SRS prose, vetted external aggregator like NASA CMR / Microsoft Planetary Computer / USGS ScienceBase). All four ideally; ≥2 of 4 + curator override is the minimum bar.

**SSRF guardrails (infra-side, NFR-S concern; binding for all modes):**

- Egress allowlist enforcement at the agent-service VPC perimeter (Cloud Run egress through VPC connector with explicit egress targets per Decision E).
- Private IP block: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8, 169.254.169.254/32 (GCE metadata) — agent egress to any of these returns a typed error per FR-AS-11.
- DNS rebinding defense: re-resolve domain at fetch time; fail closed if resolved IP differs from probe-time resolution OR enters a blocked range.
- Max response size: 100 MB default for probe responses; 4 GB default for cataloged Tier-4 region-download fetches.
- Per-domain rate limit: 10 requests/min default; per-entry override allowed.
- All outbound network operations audit-logged.

**Open Questions from §F.1.2.**

- **OQ-AT-1 (carried from §F.1.1)** — Runtime fallback between access tiers within a single catalog entry. Deferred. Out of scope for v0.2+ catalog substrate.
- **OQ-AT-2 (rescoped from §F.1.1)** — Agent-mediated data-source discovery. **Rescoped:** the original "agent crawls any URL" framing is replaced by Mode 2 (bounded to `.gov`/`.edu` with mandatory user surfacing + conformity probe + curator review pipeline). The narrower Mode 2 lands in sprint-08. Closed-by-rescoping at v0.3.18.
- **OQ-AT-3 (NEW)** — Mode 3 / wider trustworthiness signal. The deferred path: how do we eventually permit autonomous probing of non-`.gov`/`.edu` sources without the SSRF / provenance / license-attribution exposure? Requires the three prerequisites listed under Mode 3 above. Defer indefinitely until the prerequisites mature. Forward-looking marker pattern matching OQ-8 / OQ-9 / OQ-11.

**Sprint-08 scope implication.** Land Mode 1 (catalog substrate + Sonnet-driven 30–60-entry seed YAML + `catalog_search` / `catalog_fetch` atomic tools + generic Tier-2 OGC adapter + SSRF guardrails) as the headline. Mode 2 (offer-to-add on `.gov`/`.edu`) is a fast-follow within sprint-08 if scope permits, or sprint-09 if SSRF guardrails take longer than expected. Mode 3 remains deferred.

---

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

