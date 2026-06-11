# job-0282 — durable publish paths (kills the poison-by-eviction class)

**Root cause class (third occurrence, Seattle relief 2026-06-11):** published
layers referenced 30-day-TTL cache objects. TTL eviction or an operator purge
breaks the layer; content-addressed keys mean regeneration reuses the SAME
path, and warm QGIS Server processes negative-cache the missing file — so the
layer stays transparent even after the artifact returns.

**Fix:** `publish_layer` now server-side-copies the source to
`gs://<GRACE2_PUBLISH_BUCKET|cog-bucket>/published/<layer_id>-<ulid>.<ext>`
— a unique, never-reused, TTL-free path — and the `.qgs` references that.
Copy failure degrades to the source path with a WARNING (a missing IAM grant
cannot break publishing).

**Operator prerequisite (user command):** pyqgis-worker SA needs
objectViewer on the publish bucket; QGIS server SA already has it.

Tests: publish suites 21/21.
