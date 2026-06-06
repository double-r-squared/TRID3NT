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

.PHONY: help run-agent run-web test \
        tofu-init tofu-plan tofu-apply tofu-bootstrap \
        atlas-allowlist-me secret-srv-show

help:
	@echo "GRACE-2 make targets (SRS v0.3):"
	@echo "  run-agent           launch the local ADK agent service (stub until job-0015)"
	@echo "  run-web             launch the local web client dev server (stub until job-0016)"
	@echo "  test                run the acceptance + conformance suites (stub until job-0017)"
	@echo ""
	@echo "  tofu-init           one-shot OpenTofu init in infra/"
	@echo "  tofu-plan           tofu plan against the GCS-backed state"
	@echo "  tofu-apply          tofu apply"
	@echo "  tofu-bootstrap      one-time: create the GCS state bucket"
	@echo "  atlas-allowlist-me  add the current dev IPv4 /32 to Atlas access list"
	@echo "  secret-srv-show     fetch the SRV from Secret Manager (printed; treat as secret)"

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

run-web:
	@echo "run-web: scaffold stub. The React/MapLibre client lands in job-0016;"
	@echo "  this target will then start its dev server (CONUS map + chat)."

# `test` runs the project test suites. No suites exist yet (job-0017 lands them;
# job-0013 lands packages/contracts round-trip tests). Until then this is a clean
# no-op so CI and reviewers get a green `make test` with zero tests collected.
test:
	@echo "test: scaffold stub — no test suites present yet."
	@echo "  packages/contracts tests land in job-0013; acceptance suite in job-0017."

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
