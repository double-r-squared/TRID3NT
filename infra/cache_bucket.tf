# cache_bucket.tf — Atomic-tool data fetch cache bucket (sprint-06 / job-0031 / FR-DC-1..6).
#
# Decision O (SRS v0.3.15 draft) + §3.9 (FR-DC-1..6) establish cache-mediated
# atomic-tool data fetching as the M4 substrate: every atomic tool that hits
# an external public data source routes through a shared cache shim that
# writes here. This file provisions the bucket + lifecycle + IAM; the shim
# itself is agent-side code (job-0032).
#
# Bucket layout DECISION (kickoff Open Question):
#   FR-DC-1 as written: `gs://<bucket>/cache/<source-class>/<hash>.<ext>`
#   This file's pick:   `gs://<bucket>/cache/<ttl-class>/<source-class>/<hash>.<ext>`
#
#   Rationale for the deviation: GCS Object Lifecycle Management binds rules
#   to prefixes (matches_prefix). With FR-DC-1 as written, evicting per TTL
#   class requires one lifecycle_rule per source-class — N rules for N tools.
#   The bucket policy caps at 100 rules; we'd burn that budget fast as more
#   atomic tools register (already ~12 source classes in the SRS scope, with
#   the v0.2+ catalog expanding well past that). Nesting TTL class above
#   source class keeps it at FOUR rules — one per FR-DC-2 class — and the
#   shim's path-derivation logic stays trivially deterministic.
#
#   This is an FR-DC-1 deviation — surfaced as an Open Question in the
#   job-0031 report with TENTATIVE recommendation: propose an FR-DC-1
#   amendment for SRS v0.3.16 nesting by TTL class. User lands the SRS edit.
#
# Versioning is DISABLED on this bucket per the FR-DC-5 footnote ("Bucket
# versioning is off for the cache/ prefix to keep storage cost flat") —
# unlike the canonical .qgs/COG/FGB buckets in buckets.tf which keep
# versioning on for payload safety. Cache is rebuildable from upstream APIs;
# noncurrent versions add cost with no recoverability benefit.
#
# Invariant compliance:
#   - Invariant 5 (Tier separation): UBA + PAP enforced + no public IAM.
#     Client never reaches the cache bucket; the shim is server-side only.
#   - Invariant 6 (Metadata-payload pattern): the cache prefix is shim-only;
#     no flow enumerates the bucket — the shim derives keys content-addressed
#     and reads by exact path. MongoDB stays the discovery surface.
#   - NFR-S-2 / NFR-S-3 (credentials posture): SA grants are BUCKET-SCOPED
#     (mirror of job-0021 worker pattern); zero project-level storage roles
#     are added.
#
# Labels (NFR-C-1 idle-cost breakdown): sprint=06 + component=cache.

locals {
  cache_bucket_labels = merge(local.common_labels, {
    component = "cache"
    sprint    = "06"
  })
}

# --- Cache bucket ---------------------------------------------------------

resource "google_storage_bucket" "cache" {
  project  = google_project.grace2.project_id
  name     = "${google_project.grace2.project_id}-cache"
  location = var.gcp_region

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # FR-DC-5 footnote: cache/ prefix runs without versioning to keep storage
  # cost flat. Cache artifacts are reproducible from upstream APIs; noncurrent
  # versions add cost without recoverability value.
  versioning {
    enabled = false
  }

  # --- FR-DC-2 lifecycle rules (4 TTL classes) ---------------------------
  #
  # `days_since_custom_time` keys eviction off the per-object `customTime`
  # metadata field. The shim (FR-DC-3) sets `customTime = fetched_at` on
  # every write so the lifecycle policy can evict without the shim tracking
  # individual TTLs at read time. Eviction is asynchronous (a slightly stale
  # read between the bucket-boundary tick and the lifecycle pass is
  # acceptable per FR-DC-5; the next write through that key replaces it).
  #
  # The prefix nests TTL class above source class — see file-header decision
  # rationale. All four rules use `Delete` (FR-DC-5: hard-evict at the
  # bucket level; no archival tier for the cache prefix).
  #
  # Day counts come straight from FR-DC-2: 30 / 7 / 1 / 0.
  #   - static-30d:      30-day terrain, landcover snapshots, building
  #                      footprints, return-period tables.
  #   - semi-static-7d:  7-day post-season ATCF, historical catalogs,
  #                      periodic FEMA releases.
  #   - dynamic-1h:      1-day window for active advisories, recent NWIS
  #                      streamflow, news searches. (GCS lifecycle minimum
  #                      granularity is 1 day; sub-day TTLs are enforced by
  #                      the shim's `expires_at` check at read time, not by
  #                      the lifecycle policy.)
  #   - live-no-cache:   0-day (immediate eviction); reserved for tools
  #                      whose contract demands "right now" freshness.
  #                      The shim writes with `expires_at = fetched_at` so
  #                      every read misses; the rule purges stragglers.

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      matches_prefix         = ["cache/static-30d/"]
      days_since_custom_time = 30
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      matches_prefix         = ["cache/semi-static-7d/"]
      days_since_custom_time = 7
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      matches_prefix         = ["cache/dynamic-1h/"]
      days_since_custom_time = 1
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      matches_prefix         = ["cache/live-no-cache/"]
      days_since_custom_time = 0
    }
  }

  labels = local.cache_bucket_labels

  depends_on = [google_project_service.enabled]
}

# --- IAM: bucket-scoped objectAdmin for both SAs --------------------------
#
# Mirrors the job-0021 zero-project-grants pattern. Both the agent runtime
# (which runs the cache shim per FR-DC-3) and the PyQGIS worker runtime
# (which may read through the shim for already-cached inputs per the
# FR-DC-3 footnote) get `objectAdmin` BOUND AT BUCKET SCOPE — never the
# project-level role.
#
# Open Question (surfaced in report): pyqgis-worker could arguably hold
# `objectViewer` (read-only) instead of `objectAdmin` if it never writes
# derived cache entries. TENTATIVE pick is `objectAdmin` per the FR-DC-3
# footnote ("Tools that compute purely from already-cached inputs may read
# through the shim"); verify with workers as they land in Stage C
# (job-0033). If reads-only is sufficient, we'll narrow this binding in a
# follow-up.

resource "google_storage_bucket_iam_member" "agent_runtime_cache_admin" {
  bucket = google_storage_bucket.cache.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.agent_runtime.email}"
}

resource "google_storage_bucket_iam_member" "pyqgis_worker_cache_admin" {
  bucket = google_storage_bucket.cache.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pyqgis_worker.email}"
}
