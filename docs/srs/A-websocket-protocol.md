## Appendix A: WebSocket Protocol

> **Status: preemptive.** This appendix is a working specification drafted before implementation. Concrete schemas, message types, field names, and conventions are subject to revision once implementation surfaces real constraints (ADK behavior, Gemini streaming semantics, MapLibre client needs, MongoDB MCP responses, etc.). Treat as the starting point, not the contract — changes flow back into this appendix as they're learned.

### A.1 Envelope

All messages share a common envelope. JSON-encoded over a single WebSocket connection per session.

```typescript
{
  type: string,         // discriminator, kebab-case (see A.3, A.4)
  id: string,           // ULID, unique per message
  ts: string,           // ISO 8601 UTC timestamp when sent
  session_id: string,   // current session ULID
  case_id?: string,     // v0.3.23: the Case that OWNS the emitting turn (agent → client; absent/null = untagged)
  payload: object       // type-specific fields
}
```

**Conventions:**
- `type` uses kebab-case (e.g., `tool-call-start`, not `tool_call_start` or `toolCallStart`)
- `id` is a ULID (sortable by time, URL-safe, 26 characters)
- `ts` is ISO 8601 with `Z` suffix (UTC)
- `session_id` is required on every message; absent or mismatched session IDs cause the connection to close with an auth error
- `payload` is always an object, even when empty (`{}`)
- **v0.3.23 (job-0277):** `case_id` is OPTIONAL and tags agent → client envelopes with the Case that owns the emitting turn (the server pins the Case at dispatch and stamps every envelope the turn produces — streaming chunks, `pipeline-state`, `session-state`, charts, code-exec, errors). With per-Case chat streams and stream-scoped turn concurrency (FR-MP-6 v0.3.23 note), the client MUST route tagged envelopes to the owning Case's stream; untagged envelopes (`null`/absent — Case-lifecycle messages, root-dispatched turns, older builds) fall back to submit-time routing. Clients never send `case_id` at the envelope level; client → agent Case context rides in typed payloads (`case-command`).

### A.2 Encoding and transport

- WebSocket over WSS (TLS 1.2+) in production; WS allowed for local dev
- Text frames carrying JSON; binary frames are not used in v0.1
- One message per frame; messages are not chunked across frames
- Maximum message size: 1 MB (large payloads use storage URIs instead)

### A.3 Client → Agent messages

#### `user-message`
User-submitted text input.

```json
{
  "type": "user-message",
  "payload": {
    "text": "Model the flooding from Hurricane Ian in Fort Myers"
  }
}
```

#### `cancel`
Cancel the in-flight pipeline.

```json
{
  "type": "cancel",
  "payload": {
    "reason": "user-requested"
  }
}
```

`reason` is optional. The agent acknowledges via a `pipeline-state` message reflecting cancelled steps.

#### `confirm-response`
User response to a `confirmation-request`.

```json
{
  "type": "confirm-response",
  "payload": {
    "request_id": "01HX...",
    "approved": true
  }
}
```

#### `session-resume`
Resume an existing session by ID.

```json
{
  "type": "session-resume",
  "payload": {}
}
```

The session ID is in the envelope. The agent responds with a `session-state` message.

### A.4 Agent → Client messages

#### `agent-message-chunk`
A streamed token (or token group) from the LLM.

```json
{
  "type": "agent-message-chunk",
  "payload": {
    "message_id": "01HX...",
    "delta": "Fort Myers ",
    "done": false
  }
}
```

- `message_id` groups chunks belonging to one logical message
- `delta` is the new content since the last chunk (not accumulated)
- `done: true` indicates the message is complete; no further chunks for this `message_id`

#### `tool-call-start`
A tool invocation has begun.

```json
{
  "type": "tool-call-start",
  "payload": {
    "call_id": "01HX...",
    "step_id": "01HX...",
    "tool_name": "fetch_dem",
    "tool_category": "data-fetch",
    "params": { /* sanitized parameters */ }
  }
}
```

`params` may be sanitized to omit sensitive fields. The client uses `call_id` to correlate with later `tool-call-progress`, `tool-call-complete`, and `tool-call-failed` messages.

#### `tool-call-progress`
Optional progress update for an in-flight tool call.

```json
{
  "type": "tool-call-progress",
  "payload": {
    "call_id": "01HX...",
    "percent": 47,
    "status": "Downloading DEM tile 3/8"
  }
}
```

Either `percent` (0-100 integer), `status` (string), or both. Tools opt into emitting progress; not every tool emits.

#### `tool-call-complete`
A tool finished successfully.

```json
{
  "type": "tool-call-complete",
  "payload": {
    "call_id": "01HX...",
    "result_summary": "Fetched DEM: 487 MB, 10m resolution, 2.4 km²",
    "result_uri": "gs://bucket/path/dem_abc123.tif",
    "metrics": { /* tool-specific fields */ }
  }
}
```

- `result_summary` is a human-readable one-liner for chat display
- `result_uri` is optional; present when the result is a stored artifact
- `metrics` is tool-specific structured data (e.g., flood depth tool returns `{ max_depth_m, flooded_area_km2 }`)

Full result bodies are not transmitted; they live in GCS or MongoDB and are referenced by URI.

#### `tool-call-failed`
A tool errored out.

```json
{
  "type": "tool-call-failed",
  "payload": {
    "call_id": "01HX...",
    "error_code": "DEM_SOURCE_UNAVAILABLE",
    "message": "USGS 3DEP returned 503; retry suggested",
    "retryable": true
  }
}
```

- `error_code` is an enum-like string (defined per tool category in a future appendix)
- `message` is human-readable, surfaced in chat
- `retryable: true` indicates the agent may automatically retry; the user may also be offered manual retry

#### `pipeline-state`
Full snapshot of the current pipeline. Emitted on any state change.

```json
{
  "type": "pipeline-state",
  "payload": {
    "pipeline_id": "01HX...",
    "steps": [
      {
        "step_id": "01HX...",
        "name": "Geocode location",
        "tool_name": "geocode_location",
        "state": "complete",
        "started_at": "2026-06-04T20:14:01Z",
        "completed_at": "2026-06-04T20:14:02Z"
      },
      {
        "step_id": "01HX...",
        "name": "Fetch DEM",
        "tool_name": "fetch_dem",
        "state": "running",
        "started_at": "2026-06-04T20:14:02Z",
        "progress_percent": 47
      }
    ]
  }
}
```

`state` values: `pending`, `running`, `complete`, `failed`, `cancelled`.

The full snapshot replaces the client's pipeline view on each message. Deltas are not used.

#### `map-command`
Instruct the client to modify the map. One umbrella type with a `command` discriminator inside `payload`.

```json
{
  "type": "map-command",
  "payload": {
    "command": "load-layer",
    "args": { /* command-specific */ }
  }
}
```

v0.1 commands:

| `command` | `args` |
|---|---|
| `load-layer` | `{ layer_id, wms_url, style_preset, temporal?: { start, end, step_seconds } }` |
| `remove-layer` | `{ layer_id }` |
| `set-layer-visibility` | `{ layer_id, visible: boolean }` |
| `set-layer-opacity` | `{ layer_id, opacity: 0..1 }` |
| `set-layer-order` | `{ layer_ids: string[] }` (ordered, top to bottom) |
| `zoom-to` | `{ bbox: [minLon, minLat, maxLon, maxLat] }` |
| `set-temporal-config` | `{ layer_id, start, end, step_seconds, current? }` |
| `start-animation` | `{ layer_id, speed?: 0.5\|1\|2\|5\|10 }` |
| `stop-animation` | `{ layer_id }` |
| `invalidate-tiles` | `{ layer_id? }` (omit `layer_id` to invalidate all) |

#### `confirmation-request`
The agent needs user approval before proceeding.

```json
{
  "type": "confirmation-request",
  "payload": {
    "request_id": "01HX...",
    "title": "Run SFINCS simulation",
    "description": "Will run a flood simulation on a 180 km² domain. Estimated runtime ~8 minutes.",
    "estimated_duration_seconds": 480,
    "default_timeout_seconds": 60
  }
}
```

Cost figures are intentionally omitted from confirmation requests until the system can produce cent-level accurate estimates. Surfacing approximate or potentially-wrong cost numbers to users is worse than not showing any.

If no `confirm-response` arrives within `default_timeout_seconds`, the agent shall treat the request as denied and proceed accordingly (e.g., cancel the pending operation).

#### `session-state`
Sent automatically on connection and on `session-resume`. Lets the client reconstruct the session.

```json
{
  "type": "session-state",
  "payload": {
    "chat_history": [ /* list[ChatMessage] — see Appendix D.6 */ ],
    "loaded_layers": [ /* list[ProjectLayerSummary] — see Appendix D.2 */ ],
    "pipeline_history": [ /* list[PipelineSnapshot] — see Appendix D.6 */ ],
    "current_pipeline": null,
    "map_view": { /* MapView — see Appendix D.6 */ }
  }
}
```

The exact schemas of `chat_history`, `loaded_layers`, `pipeline_history`, and `map_view` are defined in **Appendix D.6** (the `sessions` collection schema). The wire form is the JSON serialization of those Pydantic models.

#### `error`
Global error not tied to a specific tool call (auth, rate limit, internal).

```json
{
  "type": "error",
  "payload": {
    "error_code": "RATE_LIMITED",
    "message": "Too many requests. Retry in 30s.",
    "retryable": true,
    "retry_after_seconds": 30
  }
}
```

#### `location-resolved`
Emitted whenever the agent identifies a meaningful location during a query — extracted from news, parsed from a user prompt, returned from geocoding, or selected via disambiguation. The client auto-snaps the map to the resolved bbox to give the user spatial context without manual navigation.

```json
{
  "type": "location-resolved",
  "payload": {
    "resolved_id": "01HX...",
    "label": "Fort Myers, Florida",
    "bbox": [-82.10, 26.40, -81.60, 26.90],
    "granularity": "city",
    "source": "news_extraction",
    "animate": true
  }
}
```

- `resolved_id`: unique per resolution; clients deduplicate by ID within a session
- `label`: human-readable, displayed as a subtle "Showing: ..." overlay
- `bbox`: target extent in EPSG:4326
- `granularity`: `country | region | state | city | facility | bbox`; drives client-side padding rules
- `source`: `news_extraction | user_prompt | disambiguation | geocoding | tool_result`
- `animate`: `true` for smooth `flyTo`, `false` for instant jump (used for rapid sequences)

Client behavior:
- Apply padding based on `granularity` (country ~10%, state to fill ~60% of viewport, city +20%, facility ~5km radius)
- Animate using MapLibre `flyTo({essential: true, duration: 1500})` with `prefers-reduced-motion` respected
- Display a dismissible "Showing: {label}" overlay
- Suppress re-snap to the same `resolved_id` if the user has manually navigated since the last snap (don't fight the user)
- Suppress redundant duplicate snaps to recently-resolved IDs within a 30-second window

#### `spatial-input-request`
Agent needs the user to specify a spatial geometry (a point or a bbox) before continuing. The map switches into pick-mode for the duration of the request.

```json
{
  "type": "spatial-input-request",
  "payload": {
    "request_id": "01HX...",
    "mode": "point",
    "title": "Where exactly is the factory?",
    "description": "The article mentions a chemical spill at a factory near the Mississippi River in Cancer Alley, Louisiana, but doesn't name the facility. Drop a pin at the spill site so the model can use the right location.",
    "suggested_view": {
      "bbox": [-91.3, 30.2, -90.5, 30.8],
      "zoom": 11
    },
    "reference_layers": [
      {
        "layer_id": "epa_facilities",
        "wms_url": "https://qgis-server/wms?MAP=ref&LAYERS=epa_facilities",
        "style_preset": "facilities-points"
      }
    ],
    "default_timeout_seconds": 300
  }
}
```

- `mode`: `point` or `bbox` (polygon mode is deferred to a later version)
- `suggested_view`: where the client zooms the map to make the picking easier
- `reference_layers`: optional helper layers shown only during this request (e.g., facility locations to help the user find a candidate)
- `default_timeout_seconds`: if no response arrives in time, the agent treats it as cancelled (300s default; spatial picks take time)

If no `spatial-input-response` arrives within `default_timeout_seconds`, the agent treats the request as cancelled and aborts the pending operation.

#### `disambiguation-request`
Agent has multiple plausible candidates for an extracted entity (typically a location) and needs the user to pick one. Distinct from `spatial-input-request` because the candidates are already enumerated.

```json
{
  "type": "disambiguation-request",
  "payload": {
    "request_id": "01HX...",
    "title": "Which Springfield?",
    "description": "The article mentions 'Springfield' but there are several. Pick one.",
    "candidates": [
      {
        "id": "springfield-il",
        "label": "Springfield, Illinois",
        "bbox": [-89.78, 39.70, -89.55, 39.85],
        "context": "Capital of Illinois"
      },
      {
        "id": "springfield-mo",
        "label": "Springfield, Missouri",
        "bbox": [-93.42, 37.10, -93.18, 37.30],
        "context": "Largest city in southwest Missouri"
      }
    ],
    "default_timeout_seconds": 120
  }
}
```

The client may render candidates as a list, as markers on the map at each candidate's bbox center, or both. Selection sends `disambiguation-response`. Timeout: 120s default (faster decision than spatial picking).

#### `clarification-request`
Agent needs the user to choose between substantively different response paths and can't infer the right one from context. Distinct from `disambiguation-request` because the options aren't a list of equivalent candidates — they're different *paths* the agent could take. See FR-AS-11.

```json
{
  "type": "clarification-request",
  "payload": {
    "request_id": "01HX...",
    "question": "Are you looking for existing wildfire risk maps in Washington, or do you want to simulate a specific fire scenario?",
    "options": [
      {
        "id": "discovery",
        "label": "Show existing risk maps",
        "description": "Display USFS wildfire hazard potential and USDA risk-to-communities layers for the area."
      },
      {
        "id": "modeling",
        "label": "Simulate a fire",
        "description": "Run a wildfire spread simulation. I'll need an ignition point and weather inputs."
      }
    ],
    "default_timeout_seconds": 60
  }
}
```

Options are 2-4 substantively different paths. The `description` field is required (not optional) — it shows the user what each path will produce. Timeout: 60s default (typical path choice).

#### `recovery-choice`
*(sprint-08 amendment, FR-FR-1 — landed by job-0045-schema-20260607.)*

Agent emits when an atomic-tool step fails with a *recoverable* error class (per FR-FR-2 routing). The web client renders a small out-of-chat modal (mirrors §F.3 popup discipline) offering deny / retry / chat actions. Substrate-integrity / user-initiated / budget-overrun error codes fail closed without gating.

```json
{
  "type": "recovery-choice",
  "payload": {
    "request_id": "01HX...",
    "failed_step_id": "01HX...",
    "error_code": "UPSTREAM_API_ERROR",
    "error_message": "USGS 3DEP returned HTTP 503 — service unavailable",
    "context": "fetching DEM at Fort Myers bbox for flood scenario",
    "options": ["deny", "retry", "chat"],
    "ttl_seconds": 300
  }
}
```

- `error_code` is `SCREAMING_SNAKE_CASE` per §A.6 (open set; shape-validated)
- `error_message` and `context` are each capped at 512 chars
- `options` is a non-empty subset of `["deny", "retry", "chat"]`; the routing table per FR-FR-2 may narrow it (e.g. omit `"retry"` for `GEOCODE_NO_MATCH` where retry is futile)
- `ttl_seconds` defaults to 300; on expiry the gate becomes a typed failure

#### `offer-catalog-addition`
*(sprint-08 amendment, §F.1.2 Mode 2 — landed by job-0045-schema-20260607.)*

Agent encountered a candidate `.gov` / `.edu` URL during research or user-query interpretation, performed a conformity probe per §F.1.2 Mode 2, and is offering to add it to the catalog. The web client renders a dedicated review modal (popup, focus-trapped, separate from chat envelope) showing the URL + probe findings + the suggested catalog entry. User accepts, rejects, or edits before accepting.

```json
{
  "type": "offer-catalog-addition",
  "payload": {
    "request_id": "01HX...",
    "url": "https://hazards.fema.gov/nfhlv2/services/public/NFHL/MapServer/WFSServer",
    "discovered_via": "user-query",
    "probe_findings": {
      "tls_cert_org": "U.S. Department of Homeland Security",
      "access_tier_inferred": 2,
      "supports_range_requests": false,
      "stac_root_found": false,
      "ogc_capabilities_found": true,
      "license_observed": "Public domain (US Federal)",
      "content_type": "application/xml",
      "last_modified_header": "Wed, 01 Jun 2026 12:00:00 GMT"
    },
    "suggested_catalog_entry": {
      "id": "femanflp-discharge-stations",
      "name": "FEMA NFHL discharge stations",
      "description": "Discharge stations from the FEMA NFHL WFS feed.",
      "urls": ["https://hazards.fema.gov/nfhlv2/services/public/NFHL/MapServer/WFSServer"],
      "access_tier": 2,
      "credential_tier": 1,
      "ttl_class": "semi-static-7d",
      "source_class": "flood_zone",
      "license_claim": "Public domain (US Federal)",
      "how_to_use": "OGC WFS GetFeature; bbox in EPSG:4326; layer NFHL:DischargeStations"
    },
    "ttl_seconds": 600
  }
}
```

- `discovered_via` is a closed `Literal`: `"user-query"` / `"web-research"` / `"catalog-cross-reference"` / `"other"`
- `probe_findings` sub-fields are all optional (probe may not be able to determine every axis)
- `suggested_catalog_entry` is a permissive draft (fields the agent could infer from the probe); the user may edit any field; agent service round-trips an accepted draft through the full `CatalogEntry` model before writing to `catalog_entries` (D.11)
- `license_claim` (not `license`) signals the probe's *observation* vs. the curator-attested value
- `ttl_seconds` defaults to 600 (review modals get more time than retry gates because the user is reading + sanity-checking provenance)

### A.4b Client → Agent (user input responses)

#### `spatial-input-response`
User has picked a geometry in response to a `spatial-input-request`.

```json
{
  "type": "spatial-input-response",
  "payload": {
    "request_id": "01HX...",
    "geometry_type": "point",
    "coordinates": [-91.087, 30.435]
  }
}
```

For `mode: point`, `coordinates` is `[lon, lat]`. For `mode: bbox`, `coordinates` is `[minLon, minLat, maxLon, maxLat]`.

The user may also send a cancellation in place of a geometry:

```json
{
  "type": "spatial-input-response",
  "payload": {
    "request_id": "01HX...",
    "cancelled": true
  }
}
```

The agent then aborts the pending operation gracefully.

#### `disambiguation-response`
User has chosen a candidate.

```json
{
  "type": "disambiguation-response",
  "payload": {
    "request_id": "01HX...",
    "candidate_id": "springfield-mo"
  }
}
```

Cancellation is the same pattern: `cancelled: true` instead of `candidate_id`.

#### `clarification-response`
User has chosen one of the clarification options.

```json
{
  "type": "clarification-response",
  "payload": {
    "request_id": "01HX...",
    "option_id": "discovery"
  }
}
```

Cancellation: `cancelled: true` instead of `option_id`. The agent then aborts the pending operation.

#### `recovery-choice-response`
*(sprint-08 amendment, FR-FR-1 — landed by job-0045-schema-20260607.)*

User has picked one of the three actions OR cancelled the modal.

```json
{
  "type": "recovery-choice-response",
  "payload": {
    "request_id": "01HX...",
    "choice": "chat",
    "chat_text": "try the WCS endpoint instead of WMS"
  }
}
```

- `choice` is `"deny"` / `"retry"` / `"chat"` or `null` when cancelled
- `chat_text` is populated only when `choice == "chat"`; carries the focused single-line nudge the user typed; capped at 4096 chars
- Cancellation: `cancelled: true` instead of `choice` (mirrors the existing A.4b response shapes)

#### `catalog-addition-response`
*(sprint-08 amendment, §F.1.2 Mode 2 — landed by job-0045-schema-20260607.)*

User has accepted / rejected the offered catalog addition. On accept, the agent writes the entry to `catalog_entries` (D.11) with `status: "user_proposed_pending_curator_review"` and logs to `catalog_audit_log` (D.12) with `event_type: "user_proposed"`. Reject events are also audited.

```json
{
  "type": "catalog-addition-response",
  "payload": {
    "request_id": "01HX...",
    "decision": "accept",
    "edited_catalog_entry": {
      "id": "femanflp-discharge-stations",
      "name": "FEMA NFHL Discharge Stations (curator-edited)",
      "urls": ["https://hazards.fema.gov/nfhlv2/services/public/NFHL/MapServer/WFSServer"],
      "access_tier": 2,
      "credential_tier": 1,
      "ttl_class": "semi-static-7d",
      "source_class": "flood_zone",
      "license_claim": "Public domain (US Federal)",
      "how_to_use": "OGC WFS GetFeature; bbox in EPSG:4326; layer NFHL:DischargeStations"
    }
  }
}
```

- `decision` is `"accept"` / `"reject"` or `null` when cancelled
- `edited_catalog_entry` (same permissive shape as the offer's `suggested_catalog_entry`) is populated only when the user edited any field; when None on accept the agent writes the original `suggested_catalog_entry`
- `reject_reason` is populated only when `decision == "reject"`; optional; capped at 512 chars
- Cancellation: `cancelled: true` instead of `decision`

### A.5 Connection lifecycle

1. **Connect**: client opens WSS connection to the agent's endpoint with a session token (cookie or query parameter; auth scheme defined in a future round)
2. **Authenticate**: agent validates the session; on failure, closes with code 4401 (unauthorized)
3. **Initial state**: agent sends `session-state` with current context
4. **Active**: client and agent exchange messages per the schemas above
5. **Disconnect**: either side may close the connection; agent persists session state to MongoDB on close
6. **Reconnect / resume**: client reconnects with the same session token; receives a fresh `session-state`; in-flight pipelines continue uninterrupted

### A.6 Error codes (initial)

Codes use `SCREAMING_SNAKE_CASE`. The list will grow as tools and failure modes are added.

| Code | Meaning |
|---|---|
| `AUTH_FAILED` | Session token invalid or expired |
| `RATE_LIMITED` | Client exceeded request rate limit |
| `INTERNAL_ERROR` | Unexpected agent-side failure |
| `LLM_UNAVAILABLE` | Gemini API failed or rate-limited |
| `TOOL_NOT_FOUND` | LLM called a tool that doesn't exist |
| `TOOL_PARAMS_INVALID` | LLM called a tool with invalid parameters |
| `TOOL_TIMEOUT` | Tool exceeded its time budget |
| `DEM_SOURCE_UNAVAILABLE` | DEM data source returned an error |
| `SOLVER_FAILED` | Solver container exited non-zero |
| `CONFIRMATION_TIMEOUT` | User did not respond to confirmation in time |
| `SPATIAL_INPUT_TIMEOUT` | User did not respond to a spatial-input-request in time |
| `DISAMBIGUATION_TIMEOUT` | User did not respond to a disambiguation-request in time |
| `CLARIFICATION_TIMEOUT` | User did not respond to a clarification-request in time |
| `USER_INPUT_CANCELLED` | User explicitly cancelled a spatial input, disambiguation, or clarification request |
| `CANCELLED` | Operation was cancelled by user |

### A.7 Design rationale

- **Discriminated envelope, single connection**: simpler than multiple channels; the `type` discriminator handles routing client-side
- **Streaming deltas, not accumulated text**: matches Gemini's native streaming output; lets the client render incrementally without recomputing
- **Snapshot pipeline state, not deltas**: pipelines are small (typically 5-15 steps); replace-not-reconcile is simpler client-side and avoids ordering bugs
- **Full results stay in storage; messages carry summaries**: keeps message sizes bounded regardless of result size; aligns with the metadata-payload pattern (§3.7)
- **`map-command` as one type with internal discriminator**: ten near-identical map operations as sibling top-level types would create churn; a single type with `command` inside is cleaner
- **Confirmation has a default timeout**: avoids zombie sessions if the user closes the tab mid-confirmation
- **ULIDs everywhere**: time-sortable, URL-safe, no central coordination; better than UUIDs for log correlation
- **Location auto-snap as side-effect of resolution, not explicit tool calls**: `location-resolved` is emitted from inside resolution tools, not invoked separately by the LLM. The map follows the agent's understanding without the LLM having to think about navigation.
- **Spatial input and disambiguation as distinct messages**: different UX patterns — "draw something" vs "pick from a list" — deserve distinct types rather than overloading one
- **Reference layers in spatial input requests**: helping the user find the right pin location is a key UX win; the agent passes through reference data without it being a separate map-command

---

