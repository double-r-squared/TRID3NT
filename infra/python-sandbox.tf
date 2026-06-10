# python-sandbox.tf — Python sandbox Cloud Run Job + VPC egress-deny network.
#
# Sprint-13 Stage 2 / job-0232. The conversational-analysis Python sandbox: a
# containerized executor (infra/python-sandbox/) running as a Cloud Run Job named
# `grace-2-python-sandbox`, which the agent (job-0233 `code_exec_request`)
# submits executions against to run user-confirmed Python against layers already
# on the map. See memory `project_conversational_data_analysis_layer` + sprint-13
# manifest job-0232.
#
# This is the SECURITY-CRITICAL file of the job: it provisions the network egress
# boundary that contains arbitrary LLM-emitted / user-confirmed code. The
# in-process socket guard in executor.py is defense-in-depth ONLY; THIS file's VPC
# connector + egress firewall is the actual boundary.
#
# Resources:
#   1. Dedicated VPC `grace-2-sandbox-net` + subnet + Serverless VPC Access
#      connector. The Cloud Run Job routes ALL egress through the connector
#      (vpc_access.egress = ALL_TRAFFIC). A fresh isolated network (not the
#      default VPC) so the egress firewall ruleset is scoped to exactly this Job's
#      traffic and nothing else shares it.
#   2. Egress firewall rules: DENY all egress by default (priority 65534), then
#      ALLOW egress ONLY to:
#        - the restricted.googleapis.com Private Google Access range
#          (199.36.153.4/30) — the GCS-only PGA VIP, so the Job reaches Cloud
#          Storage WITHOUT a route to the public internet.
#        - the MongoDB Atlas cluster endpoint CIDR (var.sandbox_atlas_cidr —
#          supplied by the user; the Atlas Flex cluster's egress IP/CIDR).
#      Everything else (example.com, PyPI, arbitrary hosts) is dropped at the
#      network layer.
#   3. `python-sandbox-runtime` service account — the Cloud Run Job identity, with
#      objectViewer (READ-ONLY) on -cache + -runs and NOTHING ELSE. No objectAdmin
#      anywhere (the sandbox NEVER writes a layer — it reads layers, computes,
#      returns a result envelope to the agent which persists charts via Mongo).
#   4. `grace-2-python-sandbox` Cloud Run v2 Job — 2 GiB mem cap, 60s task timeout
#      (NOT 1800s like SFINCS — the sandbox is a fast analytical run, the 60s cap
#      is a HARD bound on user code per the kickoff), max_retries=1, wired to the
#      VPC connector with ALL_TRAFFIC egress.
#
# Image source-of-truth (mirror of infra/sfincs.tf + infra/modflow.tf digest
# discipline): the Cloud Run Job pins the image BY DIGEST below, not :latest, so
# `tofu plan` detects a newer image pushed without an IaC change. Bump-on-build:
#   1. `make python-sandbox-build` (Cloud Build) emits the new digest at AR.
#   2. Update `python_sandbox_image_digest` below.
#   3. `tofu apply` rolls the Job; `tofu plan` after returns "No changes".
#
# Invariant compliance:
#   - Invariant 5 (Tier separation): the sandbox reads -cache + -runs READ-ONLY;
#     it never writes a payload bucket and the client never reaches it directly
#     (the agent mediates: code in -> result envelope out).
#   - Invariant 6 (Metadata-payload pattern): the sandbox does NOT enumerate
#     buckets — the agent hands it explicit layer_refs (gs:// paths) in the
#     payload; objectViewer's list permission is incidental, not a discovery path.
#   - Invariant 9 (no cost theater): no cost field anywhere; scale-to-zero Job.
#   - NFR-S-2/S-3 (credentials posture): SA grants are BUCKET-SCOPED + READ-ONLY;
#     no project-wide storage role; no keys minted.
#   - NFR-C-2 (scale-to-zero): Cloud Run Jobs are inherently scale-to-zero.
#
# Labels (NFR-C-1 idle-cost breakdown): sprint=13 + component=python-sandbox.

# --- Variables (declared here — variables.tf is a separate ownership surface) ---
#
# These three CIDRs configure the isolated sandbox network. They are declared in
# THIS file (Terraform allows `variable` blocks in any .tf) rather than
# variables.tf because variables.tf is outside this job's file ownership
# (job-0232 owns infra/python-sandbox.tf, not infra/variables.tf). When the
# orchestrator lands this job it may choose to relocate these to variables.tf for
# tidiness — surfaced as an Open Question.

variable "sandbox_subnet_cidr" {
  description = "CIDR for the isolated sandbox VPC subnet (job-0232). A small private range; must not overlap any other subnet in the project."
  type        = string
  default     = "10.180.0.0/24"
}

variable "sandbox_connector_cidr" {
  description = "CIDR (/28) for the Serverless VPC Access connector serving the sandbox Job (job-0232). Must be a /28 and not overlap the subnet."
  type        = string
  default     = "10.180.1.0/28"
}

variable "sandbox_atlas_cidr" {
  description = "MongoDB Atlas cluster endpoint CIDR the sandbox egress firewall allows (job-0232). USER MUST SET the real Atlas egress CIDR before `tofu apply` for the Atlas path to work; the placeholder default is non-routable (TEST-NET-3, RFC 5737) so validate/plan are green but no real egress is opened to it. Drop the Atlas allow rule entirely for a GCS-only posture (see report Open Questions)."
  type        = string
  default     = "203.0.113.0/32"
}

# --- APIs this file needs that gcp.tf's for_each set does not enable -------
#
# compute.googleapis.com (VPC/subnet/firewall) + vpcaccess.googleapis.com
# (Serverless VPC Access connector) are NOT in gcp.tf's `enabled_apis` list.
# gcp.tf is a separate ownership surface (job-0232 owns python-sandbox.tf only),
# so rather than edit that for_each set we enable these two with dedicated
# resources here. No resource-name collision (gcp.tf uses
# google_project_service.enabled[<api>]; these use distinct names). Surfaced as
# an Open Question — the orchestrator may fold these into gcp.tf's set on landing.

resource "google_project_service" "sandbox_compute" {
  project            = google_project.grace2.project_id
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "sandbox_vpcaccess" {
  project            = google_project.grace2.project_id
  service            = "vpcaccess.googleapis.com"
  disable_on_destroy = false
}

locals {
  python_sandbox_labels = merge(local.common_labels, {
    component = "python-sandbox"
    sprint    = "13"
  })

  # Pinned image digest (bump-on-build per the workflow above).
  #
  # PLACEHOLDER until the first `make python-sandbox-build` resolves a real
  # digest. This box has no reachable docker daemon + no gcloud, so the image has
  # NOT been built/pushed in this job (BLOCKED-ENV — see
  # reports/inflight/job-0232-infra-20260609/report.md § "User unblock steps").
  # `tofu validate` passes with a placeholder digest (validate does not touch the
  # registry); `tofu apply` will fail to pull until the real digest is recorded
  # here after the Cloud Build. The Dockerfile + the host harness smoke
  # (evidence/*.log, run via the local-subprocess fallback) prove the image
  # contents are sound; only the AR push + digest pin remain, gated on the user's
  # gcloud/docker unblock.
  python_sandbox_image_digest = "sha256:0000000000000000000000000000000000000000000000000000000000000000"

  # restricted.googleapis.com Private Google Access VIP range. This is the
  # GCS-restricted PGA endpoint: a route to it reaches Cloud Storage (+ the
  # restricted set of Google APIs) but NOT the public internet. Pinned to the
  # well-known /30 Google publishes for restricted.googleapis.com.
  restricted_googleapis_cidr = "199.36.153.4/30"
}

# --- Dedicated VPC + subnet (isolated egress-controlled network) ----------
#
# A fresh VPC (NOT the default network) so the egress firewall ruleset applies to
# exactly this Job's traffic. auto_create_subnetworks=false so the ONLY subnet is
# the one we declare; no implicit per-region subnets widen the surface.

resource "google_compute_network" "sandbox" {
  project                 = google_project.grace2.project_id
  name                    = "grace-2-sandbox-net"
  auto_create_subnetworks = false
  description             = "Isolated VPC for the Python sandbox Cloud Run Job. Egress firewall allows ONLY restricted.googleapis.com (GCS) + the Atlas endpoint; everything else is denied (job-0232)."

  depends_on = [
    google_project_service.enabled,
    google_project_service.sandbox_compute,
  ]
}

resource "google_compute_subnetwork" "sandbox" {
  project       = google_project.grace2.project_id
  name          = "grace-2-sandbox-subnet"
  region        = var.gcp_region
  network       = google_compute_network.sandbox.id
  ip_cidr_range = var.sandbox_subnet_cidr

  # Private Google Access ON so traffic to the restricted.googleapis.com VIP is
  # routed through Google's private backbone (no external IP, no internet hop).
  private_ip_google_access = true

  depends_on = [
    google_project_service.enabled,
    google_project_service.sandbox_compute,
  ]
}

# --- Serverless VPC Access connector --------------------------------------
#
# The Cloud Run Job attaches to this connector and routes ALL egress through it
# (vpc_access.egress = ALL_TRAFFIC on the Job). The connector needs its own /28.
# Min/max instances kept tiny — the sandbox is low-throughput, scale-to-zero.

resource "google_vpc_access_connector" "sandbox" {
  project = google_project.grace2.project_id
  name    = "grace-2-sandbox-vpc"
  region  = var.gcp_region
  network = google_compute_network.sandbox.name

  ip_cidr_range = var.sandbox_connector_cidr

  # Smallest supported sizing (e2-micro throughput). The sandbox is a low-volume
  # analytical surface; min_instances=2 is the Serverless VPC Access floor.
  min_instances = 2
  max_instances = 3
  machine_type  = "e2-micro"

  depends_on = [
    google_project_service.enabled,
    google_project_service.sandbox_vpcaccess,
    google_compute_subnetwork.sandbox,
  ]
}

# --- Egress firewall: DENY all by default ---------------------------------
#
# Priority 65534 (just above the implied 65535 allow-egress default, which we
# override). direction=EGRESS, deny all protocols to 0.0.0.0/0. The ALLOW rules
# below sit at lower priority numbers (higher precedence) and carve out the two
# sanctioned destinations.

resource "google_compute_firewall" "sandbox_deny_all_egress" {
  project   = google_project.grace2.project_id
  name      = "grace-2-sandbox-deny-egress"
  network   = google_compute_network.sandbox.name
  direction = "EGRESS"
  priority  = 65534

  deny {
    protocol = "all"
  }

  destination_ranges = ["0.0.0.0/0"]

  description = "DENY all egress from the sandbox network by default. The ALLOW rules (GCS PGA + Atlas) at higher precedence carve out the only sanctioned destinations (job-0232)."

  depends_on = [google_compute_network.sandbox]
}

# --- Egress firewall: ALLOW restricted.googleapis.com (GCS via PGA) -------
#
# Priority 1000 (higher precedence than the deny). Allows TCP 443 to the
# restricted.googleapis.com VIP /30 ONLY. This is how the Job reaches Cloud
# Storage (download layer_refs + the payload staging file) without any route to
# the public internet.

resource "google_compute_firewall" "sandbox_allow_gcs_egress" {
  project   = google_project.grace2.project_id
  name      = "grace-2-sandbox-allow-gcs"
  network   = google_compute_network.sandbox.name
  direction = "EGRESS"
  priority  = 1000

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }

  destination_ranges = [local.restricted_googleapis_cidr]

  description = "ALLOW egress to restricted.googleapis.com (199.36.153.4/30) on 443 — GCS-only Private Google Access. Reaches Cloud Storage without an internet route (job-0232)."

  depends_on = [google_compute_network.sandbox]
}

# --- Egress firewall: ALLOW MongoDB Atlas endpoint ------------------------
#
# Priority 1000. Allows TCP to the Atlas cluster's egress CIDR on the standard
# MongoDB SRV port range. var.sandbox_atlas_cidr is supplied by the user (the
# Atlas Flex cluster's IP / CIDR — surfaced as an Open Question + a tfvars var
# below). Defaults to a non-routable placeholder so `tofu validate` is green; the
# user MUST set the real CIDR before `tofu apply` for the Atlas path to work.
#
# NOTE: if the sandbox does NOT need Mongo connectivity at v0.1 (the agent
# persists charts via the MongoDB MCP server, not the sandbox), this rule can be
# DROPPED entirely — surfaced as an Open Question. It is provisioned (and
# default-placeholdered) per the kickoff's "GCS + MongoDB Atlas endpoint"
# allowlist, but the tighter posture is GCS-only.

resource "google_compute_firewall" "sandbox_allow_atlas_egress" {
  project   = google_project.grace2.project_id
  name      = "grace-2-sandbox-allow-atlas"
  network   = google_compute_network.sandbox.name
  direction = "EGRESS"
  priority  = 1000

  allow {
    protocol = "tcp"
    ports    = ["27015-27017"]
  }

  destination_ranges = [var.sandbox_atlas_cidr]

  description = "ALLOW egress to the MongoDB Atlas cluster endpoint CIDR (var.sandbox_atlas_cidr) on the SRV port range. User MUST set the real Atlas CIDR before apply (job-0232). Can be dropped for a GCS-only posture — see report Open Questions."

  depends_on = [google_compute_network.sandbox]
}

# --- Service account: python-sandbox-runtime ------------------------------
#
# Dedicated runtime SA for the sandbox Cloud Run Job. objectViewer (READ-ONLY) on
# -cache + -runs; NOTHING else. No objectAdmin anywhere — the sandbox is a pure
# reader. No keys minted (Cloud Run attaches the identity via the metadata
# server). This is the tightest SA in the project: a sandbox running hostile code
# must NOT be able to write any bucket.

resource "google_service_account" "python_sandbox_runtime" {
  project      = google_project.grace2.project_id
  account_id   = "python-sandbox-runtime"
  display_name = "GRACE-2 Python sandbox Cloud Run Job runtime"
  description  = "Cloud Run Job identity for the Python sandbox. objectViewer (READ-ONLY) on -cache + -runs; NO write role anywhere; no project-wide roles. The sandbox reads layers + returns a result envelope; it never writes."

  depends_on = [google_project_service.enabled]
}

# --- IAM: READ-ONLY objectViewer on -cache --------------------------------
#
# The agent's code_exec dispatch (job-0233) hands the sandbox layer_refs that may
# point at cache-bucket objects (cached fetches). objectViewer grants
# storage.objects.get + .list on THIS bucket only — READ, never write.

resource "google_storage_bucket_iam_member" "python_sandbox_cache_viewer" {
  bucket = google_storage_bucket.cache.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.python_sandbox_runtime.email}"
}

# --- IAM: READ-ONLY objectViewer on -runs ---------------------------------
#
# Layer refs often point at solver outputs in the runs bucket (a flood-depth COG,
# a MODFLOW concentration raster). objectViewer grants READ only — the sandbox
# CANNOT overwrite a solver output even if hostile code tried.

resource "google_storage_bucket_iam_member" "python_sandbox_runs_viewer" {
  bucket = google_storage_bucket.runs.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.python_sandbox_runtime.email}"
}

# NOTE: NO objectAdmin / objectCreator / objectUser grant ANYWHERE for this SA.
# This is deliberate and load-bearing (Invariant 5 + the security posture): the
# sandbox is a reader. Charts/results are persisted by the AGENT (via the Mongo
# MCP path), not by the sandbox. The payload-staging file the sandbox reads is
# WRITTEN by the agent-runtime SA, not this one.

# --- Cloud Run v2 Job: grace-2-python-sandbox -----------------------------

resource "google_cloud_run_v2_job" "python_sandbox" {
  project  = google_project.grace2.project_id
  name     = "grace-2-python-sandbox"
  location = var.gcp_region

  labels = local.python_sandbox_labels

  template {
    labels = local.python_sandbox_labels

    parallelism = 1
    task_count  = 1

    template {
      service_account = google_service_account.python_sandbox_runtime.email

      # 60s task timeout — HARD bound on a sandbox run (kickoff: 60s wallclock
      # cap). The in-container SIGALRM watchdog fires at 60s; this Job-level
      # timeout is the outer hard kill if the alarm is defeated. Much tighter
      # than the SFINCS/MODFLOW solver timeouts (those are minute-to-hour runs;
      # the sandbox is a fast analytical query). max_retries=1: a transient GCS
      # read error is worth one retry; the harness is deterministic so a retry
      # re-runs identical code (acceptable — the result envelope is idempotent).
      timeout     = "60s"
      max_retries = 1

      # --- VPC egress: ALL traffic through the connector ----------------
      # ALL_TRAFFIC (not PRIVATE_RANGES_ONLY) so EVERY outbound packet — including
      # any attempt to reach the public internet — is forced through the
      # connector, where the egress firewall drops everything except GCS PGA +
      # Atlas. This is the actual containment boundary (the executor's in-process
      # guard is defense-in-depth only).
      vpc_access {
        connector = google_vpc_access_connector.sandbox.id
        egress    = "ALL_TRAFFIC"
      }

      containers {
        # Digest-pinned (mirror of infra/sfincs.tf + infra/modflow.tf). Bump per
        # the file-header workflow when `make python-sandbox-build` produces a new
        # digest. PLACEHOLDER until the first Cloud Build (BLOCKED-ENV on this box
        # — no docker daemon + no gcloud).
        image = "${var.gcp_region}-docker.pkg.dev/${google_project.grace2.project_id}/${google_artifact_registry_repository.containers.repository_id}/grace-2-python-sandbox@${local.python_sandbox_image_digest}"

        # 2 GiB / 1 vCPU (kickoff: 2GB mem cap). The analytical toolkit
        # (rasterio/geopandas/sklearn) is memory-bounded; 2 GiB is the ceiling.
        resources {
          limits = {
            cpu    = "1"
            memory = "2Gi"
          }
        }

        # --- sandbox env --------------------------------------------------
        # GRACE2_SANDBOX_PAYLOAD_URI is set per-execution by the agent's dispatch
        # (gs:// staging file with python_code + layer_refs). Empty default keeps
        # the IaC declarative.
        env {
          name  = "GRACE2_SANDBOX_PAYLOAD_URI"
          value = ""
        }

        env {
          name  = "GCP_PROJECT"
          value = google_project.grace2.project_id
        }
        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = google_project.grace2.project_id
        }
        env {
          name  = "GRACE2_CACHE_BUCKET"
          value = google_storage_bucket.cache.name
        }
        env {
          name  = "GRACE2_RUNS_BUCKET"
          value = google_storage_bucket.runs.name
        }
        # Wallclock cap mirrored into the harness (the container default is also
        # 60s; set explicitly here so the IaC is the source of truth).
        env {
          name  = "GRACE2_SANDBOX_TIMEOUT"
          value = "60"
        }
        # In-process net-guard allowlist (defense-in-depth; the VPC firewall is
        # the real boundary). GCS + Atlas host suffixes + loopback.
        env {
          name  = "GRACE2_SANDBOX_NET_ALLOW"
          value = "googleapis.com,google.internal,mongodb.net,localhost,127.0.0.1,::1"
        }
      }
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_artifact_registry_repository.containers,
    google_storage_bucket.cache,
    google_storage_bucket.runs,
    google_vpc_access_connector.sandbox,
    google_compute_firewall.sandbox_deny_all_egress,
    google_compute_firewall.sandbox_allow_gcs_egress,
    google_storage_bucket_iam_member.python_sandbox_cache_viewer,
    google_storage_bucket_iam_member.python_sandbox_runs_viewer,
  ]
}

# --- IAM: agent-runtime can invoke the sandbox Job ------------------------
#
# The agent service (job-0233 code_exec dispatch) submits executions against this
# Job under its own agent-runtime SA. run.invoker grants run.jobs.run;
# run.developer grants run.jobs.runWithOverrides (the agent sets
# GRACE2_SANDBOX_PAYLOAD_URI per execution). Both bound at the Job RESOURCE scope
# (mirror of infra/sfincs.tf zero-project-grants discipline) — the agent SA can
# only invoke THIS Job, not any other Job/Service.

resource "google_cloud_run_v2_job_iam_member" "agent_invokes_sandbox" {
  project  = google_project.grace2.project_id
  location = google_cloud_run_v2_job.python_sandbox.location
  name     = google_cloud_run_v2_job.python_sandbox.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.agent_runtime.email}"
}

resource "google_cloud_run_v2_job_iam_member" "agent_invokes_sandbox_developer" {
  project  = google_project.grace2.project_id
  location = google_cloud_run_v2_job.python_sandbox.location
  name     = google_cloud_run_v2_job.python_sandbox.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.agent_runtime.email}"
}

# --- IAM: agent-runtime can actAs the sandbox-runtime SA ------------------
#
# Calling run.jobs.run with overrides requires the caller to actAs the Job's
# runtime SA. Bound at the runtime SA resource scope (mirror of infra/sfincs.tf)
# — the agent can only actAs THIS SA, not any other in the project.

resource "google_service_account_iam_member" "agent_actas_python_sandbox_runtime" {
  service_account_id = google_service_account.python_sandbox_runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.agent_runtime.email}"
}
