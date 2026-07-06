# Scale-to-zero Phase 3 — client hardening (blueprint 2.5.1 + 2.5.7)

Date: 2026-07-06

## 1. WS frame-queueing (web, 065c6f7)

The 7 user-answer frame types (secret-add, secret-revoke, credential-provided,
region-choice-provided, spatial-input-response, tool-payload-confirmation,
layer-delete) were sent via sendEnvelope, which silently DROPS the frame if the
socket is not OPEN -- a mid-wake answer to a credential card / region picker /
granularity gate vanished. All 7 now route through sendOrQueue (same 50-frame
FIFO the chat path uses) and flush on reconnect. auth-token stays unqueued
(connection-scoped); the two mode2 audit frames stay best-effort.
tsc clean, 43 ws tests pass. Deployed via Vercel push; Playwright verified the
authed /app renders (case open, layers panel, map paint).

## 2. Cold-snapshot bare-s3 leak (agent, bac7b5d)

Explore pass traced the blueprint's "bare s3:// layers are invisible box-off"
item: publish_layer's raster path already fails fast / mints http templates,
and all mesh/MODFLOW vector paths inline GeoJSON before emission. The one real
leak: persistence.build_case_view_snapshot's cross-case vector inline
(_resolve_inline_geojson) -- ONE transient S3 read failure skipped the layer,
baking its bare s3:// uri into the snapshot = layer invisible in the cold
box-off view until the next rebuild. Fix: retry the read once (0.5s backoff)
+ a louder WARN naming the consequence. Durable fix (persist vector GeoJSON +
thin manifest, retire the snapshot) remains task #165.
52 persistence/snapshot tests pass. Shipped in the agent image (CodeBuild
SUCCEEDED); new sessions pull :latest.

## Gate

Flood smoke PASS on a fresh session running the new image via the box broker:
sfincs_pluvial_flood, case 01KWW158EZNXAVN8S0Y5M666TP, 282.7s.

## Remaining

Phase 4 (agent diet 8->4GB) is the last blueprint phase.
