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

CONDA ?= conda
ENV   ?= grace2
RUN    = $(CONDA) run -n $(ENV)

.DEFAULT_GOAL := help

.PHONY: help run-agent run-web test

help:
	@echo "GRACE-2 make targets (SRS v0.3):"
	@echo "  run-agent   launch the local ADK agent service (stub until job-0015)"
	@echo "  run-web     launch the local web client dev server (stub until job-0016)"
	@echo "  test        run the acceptance + conformance suites (stub until job-0017)"

run-agent:
	@echo "run-agent: scaffold stub. The ADK agent service lands in job-0015;"
	@echo "  this target will then launch it over the Appendix-A WebSocket core."

run-web:
	@echo "run-web: scaffold stub. The React/MapLibre client lands in job-0016;"
	@echo "  this target will then start its dev server (CONUS map + chat)."

# `test` runs the project test suites. No suites exist yet (job-0017 lands them;
# job-0013 lands packages/contracts round-trip tests). Until then this is a clean
# no-op so CI and reviewers get a green `make test` with zero tests collected.
test:
	@echo "test: scaffold stub — no test suites present yet."
	@echo "  packages/contracts tests land in job-0013; acceptance suite in job-0017."
