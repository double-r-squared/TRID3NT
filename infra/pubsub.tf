# pubsub.tf — worker-events notification topic (sprint-04 / FR-QS-6 step 5).
#
# The canonical PyQGIS worker round-trip (FR-QS-6) ends with the worker
# publishing a typed completion message that downstream consumers (the agent
# service, future workflows) subscribe to. M2 provisions ONLY the topic; no
# subscriber exists yet. The agent service wires its consumer in M3/M4.
#
# Co-located in infra/qgis-server/ because:
#   - The topic IS the QGIS-side substrate for the FR-QS-6 round trip
#     (workers mutate .qgs in -qgs bucket, then notify here).
#   - Keeps qgis-server/ as the one place to read for the M2 substrate state.
#
# Discipline:
#   - No public subscribers; consumer SAs are bound per-consumer when the
#     consumer lands (agent / cloud workflows). Worker SA gets `publisher`
#     role in job-0021 when the worker SA is created.

resource "google_pubsub_topic" "worker_events" {
  project = google_project.grace2.project_id
  name    = "grace-2-worker-events"

  labels = merge(local.common_labels, {
    component = "qgis-server"
    # M2 substrate; track separately from M1.
    sprint = "04"
  })

  # No message ordering required for FR-QS-6 — events are independent. If a
  # consumer needs strict ordering later, flip `message_ordering_enabled`
  # then; not a M2 requirement.

  depends_on = [google_project_service.enabled]
}

# --- Subscription binding deferred ---------------------------------------
# No subscribers in M2. Agent consumer subscription lands when the agent's
# worker-completion handler does (M3/M4); the worker-SA publisher binding
# lands in job-0021. Provision-now-bind-later is intentional: the topic
# substrate is what FR-QS-6 step 5 requires, and adding subscribers without
# consumers would leave undeliverable messages accumulating against the
# 7-day default retention.
