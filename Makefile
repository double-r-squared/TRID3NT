# GRACE-2 — developer task runner (SRS v0.3).
#
# GRACE-2 is a web-based AI workbench for multi-hazard modeling:
#   web/                React + MapLibre client            (web)
#   services/agent/     ADK + Gemini 3 agent (Cloud Run)   (agent)
#   services/workers/   PyQGIS + SFINCS Cloud Run Jobs      (engine code / infra image)
#   packages/contracts/ pydantic v2 contracts              (schema — lands in job-0013)
#   infra/              OpenTofu IaC for the GCP substrate  (infra)
#   styles/             QML presets                         (engine content / infra-baked)
#   tests/              acceptance + conformance suites     (testing)
#
# The `grace2` conda env (QGIS 3.40.3) is the LOCAL PyQGIS-worker dev environment
# only; the agent service and QGIS Server ship as their own containers. Env name
# from reports/PROJECT_STATE.md "Environment facts".
#
# Targets below are SCAFFOLD STUBS (job-0012). They are wired to real commands as
# each component lands: run-agent (job-0015), run-web (job-0016), test (job-0017).
#
# infra targets (job-0014) are real and operate on the live GCP project +
# Atlas Flex cluster; they require gcloud + atlas + tofu on PATH and the
# appropriate auth (gcloud ADC, atlas user account, MONGODB_ATLAS_*_KEY
# env vars for the mongodbatlas provider).

CONDA ?= conda
ENV   ?= grace2
RUN    = $(CONDA) run -n $(ENV)

GCP_PROJECT_ID ?= grace-2-hazard-prod
GCP_REGION     ?= us-central1
STATE_BUCKET   ?= grace-2-tfstate-$(GCP_PROJECT_ID)
ATLAS_PROJECT_ID ?= 6a234700a0e1295958d10cf9

.DEFAULT_GOAL := help

.PHONY: help run-agent run-web test test-m2 \
        tofu-init tofu-plan tofu-apply tofu-bootstrap \
        atlas-allowlist-me secret-srv-show \
        qgis-server-build qgis-server-push qgis-server-deploy \
        worker-build worker-push worker-deploy worker-run-job

help:
	@echo "GRACE-2 make targets (SRS v0.3):"
	@echo "  run-agent           launch the local ADK agent service (stub until job-0015)"
	@echo "  run-web             launch the local web client dev server (stub until job-0016)"
	@echo "  test                run the M1 acceptance + conformance suites (job-0017)"
	@echo "  test-m2             run the M2 acceptance suite (job-0023; live QGIS Server + Cloud Run Job)"
	@echo ""
	@echo "  tofu-init           one-shot OpenTofu init in infra/"
	@echo "  tofu-plan           tofu plan against the GCS-backed state"
	@echo "  tofu-apply          tofu apply"
	@echo "  tofu-bootstrap      one-time: create the GCS state bucket"
	@echo "  atlas-allowlist-me  add the current dev IPv4 /32 to Atlas access list"
	@echo "  secret-srv-show     fetch the SRV from Secret Manager (printed; treat as secret)"
	@echo ""
	@echo "  qgis-server-build   build the QGIS Server image via Cloud Build (linux/amd64)"
	@echo "  qgis-server-push    alias of qgis-server-build (Cloud Build pushes to AR)"
	@echo "  qgis-server-deploy  tofu apply the Cloud Run service + public-invoker binding"
	@echo ""
	@echo "  worker-build        build the PyQGIS worker image via Cloud Build (linux/amd64)"
	@echo "  worker-push         alias of worker-build (Cloud Build pushes to AR)"
	@echo "  worker-deploy       tofu apply the Cloud Run Job + SA + IAM bindings"
	@echo "  worker-run-job      execute the PyQGIS worker Cloud Run Job (QGS_URI=... LAYER=...)"

# Agent service (job-0015). Launches the Appendix-A WebSocket server. Uses the
# repo-local virtualenv at .venv-agent/ (created with `virtualenv -p python3`
# because Debian's python3-venv is not installed — see PROJECT_STATE.md). ADC
# credentials at ~/.config/gcloud/application_default_credentials.json
# authenticate both Vertex AI and Secret Manager.
#
# Override the port with GRACE2_AGENT_PORT; override the Gemini model id with
# GRACE2_GEMINI_MODEL.
AGENT_VENV ?= .venv-agent
run-agent:
	@if [ ! -x $(AGENT_VENV)/bin/grace2-agent ]; then \
	  echo "agent venv missing or stale. Bootstrap:"; \
	  echo "  virtualenv -p python3 $(AGENT_VENV)"; \
	  echo "  $(AGENT_VENV)/bin/pip install -e packages/contracts -e services/agent"; \
	  exit 1; \
	fi
	GOOGLE_GENAI_USE_VERTEXAI=True \
	GOOGLE_CLOUD_PROJECT=$(GCP_PROJECT_ID) \
	GOOGLE_CLOUD_LOCATION=$(GCP_REGION) \
	$(AGENT_VENV)/bin/grace2-agent

# Web client (job-0016). React + Vite + MapLibre. Installs dependencies the
# first time web/node_modules/ is missing, then runs the Vite dev server on
# 0.0.0.0:5173 so the page is reachable from headless browsers + LAN spot
# checks. Override the WebSocket endpoint with VITE_GRACE2_WS_URL (default:
# ws://localhost:8765 — the local job-0015 agent).
run-web:
	@if [ ! -d web/node_modules ]; then \
	  echo "web/node_modules missing — running npm install"; \
	  cd web && npm install; \
	fi
	cd web && npm run dev

# `test` runs the M1 acceptance suite (job-0017). The harness lives under
# tests/, drives the real grace2-agent WebSocket transport with the Gemini
# adapter stubbed (the only permitted mock boundary per the kickoff), and
# collects the packages/contracts unit suite end-to-end through pytest.
#
# Uses the existing agent venv (.venv-agent) because grace2_agent is installed
# there; pytest + pytest-asyncio are installed alongside. The acceptance
# suite imports grace2_agent and grace2_contracts directly — virtualenv
# fallback for Debian (no python3-venv) per PROJECT_STATE.md.
TEST_VENV ?= .venv-agent
test:
	@if [ ! -x $(TEST_VENV)/bin/python ]; then \
	  echo "test venv missing or stale ($(TEST_VENV)). Bootstrap:"; \
	  echo "  virtualenv -p python3 $(TEST_VENV)"; \
	  echo "  $(TEST_VENV)/bin/pip install -e packages/contracts -e services/agent"; \
	  echo "  $(TEST_VENV)/bin/pip install pytest pytest-asyncio websockets"; \
	  exit 1; \
	fi
	@if ! $(TEST_VENV)/bin/python -c "import pytest" 2>/dev/null; then \
	  echo "pytest missing in $(TEST_VENV); installing pytest pytest-asyncio..."; \
	  $(TEST_VENV)/bin/pip install --quiet pytest pytest-asyncio; \
	fi
	@echo "==> packages/contracts/tests (unit suite)"
	cd packages/contracts && $(CURDIR)/$(TEST_VENV)/bin/python -m pytest tests -q
	@echo "==> tests/ (M1 acceptance suite — protocol conformance + negative controls + integration)"
	$(TEST_VENV)/bin/python -m pytest tests -v -m "not live_gemini"

# `test-m2` runs the M2 acceptance suite (job-0023). Live QGIS Server +
# Cloud Run Job + GCS + Pub/Sub. Reuses .venv-agent for pytest. Markers
# (live_qgis_server, live_worker, live_tofu) auto-skip when their substrate
# is unreachable; set GRACE2_SKIP_LIVE_WORKER=1 / GRACE2_SKIP_LIVE_TOFU=1
# to opt out explicitly.
test-m2:
	@if [ ! -x $(TEST_VENV)/bin/python ]; then \
	  echo "test venv missing or stale ($(TEST_VENV)). Bootstrap:"; \
	  echo "  virtualenv -p python3 $(TEST_VENV)"; \
	  echo "  $(TEST_VENV)/bin/pip install -e packages/contracts -e services/agent"; \
	  echo "  $(TEST_VENV)/bin/pip install pytest pytest-asyncio websockets"; \
	  exit 1; \
	fi
	$(TEST_VENV)/bin/python -m pytest tests/m2 -v --tb=short

# --- infra targets (job-0014) ----------------------------------------------

tofu-init:
	tofu -chdir=infra init

tofu-plan:
	tofu -chdir=infra plan

tofu-apply:
	tofu -chdir=infra apply

# One-time bootstrap of the GCS state bucket BEFORE `tofu init`. Idempotent
# enough that re-runs after the bucket exists do nothing destructive (gcloud
# storage buckets create errors out cleanly; the lifecycle/versioning calls
# are no-ops if already set).
tofu-bootstrap:
	@echo "Bootstrapping GCS state bucket gs://$(STATE_BUCKET) in $(GCP_REGION)..."
	gcloud storage buckets create gs://$(STATE_BUCKET) \
	  --project=$(GCP_PROJECT_ID) --location=$(GCP_REGION) \
	  --uniform-bucket-level-access --public-access-prevention || true
	gcloud storage buckets update gs://$(STATE_BUCKET) --versioning
	@printf '{"rule":[{"action":{"type":"Delete"},"condition":{"daysSinceNoncurrentTime":90,"isLive":false}}]}\n' \
	  > /tmp/grace2-tfstate-lifecycle.json
	gcloud storage buckets update gs://$(STATE_BUCKET) \
	  --lifecycle-file=/tmp/grace2-tfstate-lifecycle.json
	@rm -f /tmp/grace2-tfstate-lifecycle.json

# Helper: fetch the dev box's current public IPv4 and add it to the Atlas
# project access list as a /32 with a 'nate-debian-dev' comment. Adding the
# same CIDR twice is a no-op error; review and prune stale entries periodically.
atlas-allowlist-me:
	@MYIP=$$(curl -4 -s https://ifconfig.me) ; \
	echo "Adding $$MYIP/32 to Atlas project $(ATLAS_PROJECT_ID) access list..." ; \
	atlas accessLists create $$MYIP/32 --type ipAddress \
	  --projectId $(ATLAS_PROJECT_ID) \
	  --comment 'nate-debian-dev'

# Print the SRV connection string (with creds). Use sparingly; redirect into
# environment, never write to disk.
secret-srv-show:
	@gcloud secrets versions access latest \
	  --secret=mongodb-srv-dev \
	  --project=$(GCP_PROJECT_ID)

# --- QGIS Server (job-0018) ------------------------------------------------
#
# Builds linux/amd64 only (Linux is both substrate and prod — sprint-03
# decision; PROJECT_STATE Environment facts). Cloud Build is the canonical
# path because the dev box's local docker requires sudo and Cloud Build runs
# inside GCP next to Artifact Registry — zero local credential surface, image
# arrives in AR ready for Cloud Run. The Dockerfile lives in
# infra/qgis-server/Dockerfile; the build context is the repo root so the
# `COPY styles/ /opt/grace2/styles/` step pulls QML presets from the
# engine-owned styles/ directory.
#
# The image tag is :latest (Cloud Run resolves to digest at deploy). Cloud
# Build also writes the digest to its log — capture it in the report for
# provenance. AR repo + region match infra/qgis-server.tf.

QGIS_AR_REPO   ?= grace-2-containers
QGIS_IMAGE     ?= grace-2-qgis-server
QGIS_IMAGE_URI ?= $(GCP_REGION)-docker.pkg.dev/$(GCP_PROJECT_ID)/$(QGIS_AR_REPO)/$(QGIS_IMAGE):latest

qgis-server-build:
	@echo "Building $(QGIS_IMAGE_URI) via Cloud Build (linux/amd64)..."
	gcloud builds submit \
	  --project=$(GCP_PROJECT_ID) \
	  --config=infra/qgis-server/cloudbuild.yaml \
	  --substitutions=_REGION=$(GCP_REGION),_AR_REPO=$(QGIS_AR_REPO),_IMAGE=$(QGIS_IMAGE) \
	  .

# qgis-server-push is an alias: Cloud Build pushes as part of `submit --tag`.
# Kept as a separate target so the kickoff's three-target contract holds
# (build / push / deploy) — running it twice is idempotent (a no-op second
# build is fine; layer cache hits).
qgis-server-push: qgis-server-build

qgis-server-deploy:
	tofu -chdir=infra apply -auto-approve \
	  -target=google_cloud_run_v2_service.qgis_server \
	  -target=google_cloud_run_v2_service_iam_member.qgis_server_public_invoker

# --- PyQGIS worker (job-0021) ----------------------------------------------
#
# Builds linux/amd64 only (project-wide Linux substrate decision; see
# qgis-server section above for rationale). Cloud Build is canonical for the
# same reason: zero local credential surface, image pushed to AR by GCP.
# Dockerfile lives at infra/worker/Dockerfile; build context is repo root so
# `COPY services/workers/pyqgis/ ...` and `COPY styles/ ...` pull current HEAD.
#
# After a build, the new digest is logged by Cloud Build and printed by
# `gcloud artifacts docker images list ... | grep pyqgis-worker`. Update
# infra/worker.tf's `image = ...` line to that digest, then `make worker-deploy`.
#
# `worker-run-job` invokes the Cloud Run Job with QGS_URI and LAYER as
# task args (--qgs-uri and --layer-to-add). Reads from /mnt/qgs/<file>.qgs
# (the writable bucket mount provisioned in infra/worker.tf).

WORKER_AR_REPO   ?= grace-2-containers
WORKER_IMAGE     ?= grace-2-pyqgis-worker
WORKER_IMAGE_URI ?= $(GCP_REGION)-docker.pkg.dev/$(GCP_PROJECT_ID)/$(WORKER_AR_REPO)/$(WORKER_IMAGE):latest
WORKER_JOB_NAME  ?= grace-2-pyqgis-worker

worker-build:
	@echo "Building $(WORKER_IMAGE_URI) via Cloud Build (linux/amd64)..."
	gcloud builds submit \
	  --project=$(GCP_PROJECT_ID) \
	  --config=infra/worker/cloudbuild.yaml \
	  --substitutions=_REGION=$(GCP_REGION),_AR_REPO=$(WORKER_AR_REPO),_IMAGE=$(WORKER_IMAGE) \
	  .

# Cloud Build pushes during `submit`; this alias preserves the
# build/push/deploy three-target shape symmetric with qgis-server-*.
worker-push: worker-build

worker-deploy:
	tofu -chdir=infra apply -auto-approve \
	  -target=google_service_account.pyqgis_worker \
	  -target=google_storage_bucket_iam_member.pyqgis_worker_qgs_admin \
	  -target=google_pubsub_topic_iam_member.pyqgis_worker_publisher \
	  -target=google_cloud_run_v2_job.pyqgis_worker

# Execute the Cloud Run Job. Required overrides: QGS_URI=/mnt/qgs/<file>.qgs LAYER=<name>
# Example:
#   make worker-run-job QGS_URI=/mnt/qgs/grace2-sample.qgs LAYER=demo
# Use --wait so the Make target's exit code reflects the Job execution status.
worker-run-job:
	@if [ -z "$(QGS_URI)" ] || [ -z "$(LAYER)" ]; then \
	  echo "Usage: make worker-run-job QGS_URI=/mnt/qgs/<file>.qgs LAYER=<layer-name>"; \
	  exit 2; \
	fi
	gcloud run jobs execute $(WORKER_JOB_NAME) \
	  --project=$(GCP_PROJECT_ID) \
	  --region=$(GCP_REGION) \
	  --args="--qgs-uri,$(QGS_URI),--layer-to-add,$(LAYER)" \
	  --wait
