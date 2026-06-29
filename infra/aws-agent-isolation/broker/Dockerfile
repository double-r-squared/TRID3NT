# GRACE-2 session broker image -- the thin always-on connection broker.
#
# SCOPE: RUNNABLE. The route-decision + cognito_verify reuse + routing + the WS
# byte-proxy (proxy.py) + the server entry (server.py: serve() loop + /healthz
# HTTP) are all concrete + unit-tested. The image serves on :8080. What remains is
# DEPLOY-stage only (the broker-builder CodeBuild project + the canary apply).
#
# ZERO-DRIFT COGNITO REUSE (load-bearing): the broker's cognito_verify.py imports
# the agent's REAL ``grace2_agent.auth_handshake.cognito_verify`` when available,
# so there is ONE verifier in the process. To make that import succeed in the
# broker image, this image INSTALLS THE AGENT PACKAGE (services/agent) -- the
# SAME dependency set the agent task uses, so the verify logic + its PyJWT[crypto]
# transitive deps come from the single source of truth. (The vendored fallback in
# cognito_verify.py only fires if that import ever fails; the drift-guard test
# keeps it honest.)
#
# Because it installs the agent package, the broker image is heavy like the agent
# image. A LATER optimization (documented, not done): a SLIM broker image that
# installs ONLY PyJWT[crypto] + boto3 + websockets and relies on the vendored
# cognito_verify (drift-guarded) -- smaller + faster broker cold start. The
# zero-drift import path is the safer default for the foundation.
#
# Build OFF-BOX via CodeBuild (same reasons as the agent image). A broker-builder
# CodeBuild project mirrors grace2-agent-builder; TODO(live): add it to
# codebuild.tf when the proxy plumbing lands (the scaffold leaves broker_image
# empty so the broker ECS service is inert until then).
#
# Base: ECR Public mirror (no Docker Hub 429), python 3.13 (matches the agent).

FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_PREFER_BINARY=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip setuptools wheel

# Install the agent package (zero-drift cognito source) + the broker's own runtime
# deps (websockets for the WS proxy; boto3 comes via the agent package). The
# websockets pin MATCHES the agent (>=13,<16) -- the broker proxy + server use the
# SAME ``websockets.asyncio`` API the agent serves on, so the frame surface is
# identical and the agent-package install does not trigger a resolver conflict.
COPY services/agent/pyproject.toml /build/services/agent/pyproject.toml
COPY services/agent/README.md /build/services/agent/README.md
COPY services/agent/src /build/services/agent/src
RUN pip install --no-cache-dir /build/services/agent "websockets>=13,<16"

RUN find /opt/venv -depth -type d -name '__pycache__' -exec rm -rf {} + \
    && find /opt/venv -type f -name '*.pyc' -delete

# --------------------------------------------------------------------------- #
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="grace-2-session-broker" \
      org.opencontainers.image.source="https://github.com/double-r-squared/GRACE-2" \
      org.opencontainers.image.description="GRACE-2 session broker: verifies Cognito (reusing the agent's cognito_verify), resolves sub->ULID + (user,session)->task, RunTasks on a miss, proxies WSS. Foundation skeleton (byte-proxy TODO)." \
      org.opencontainers.image.licenses="proprietary"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# The broker package (the route-decision + cognito reuse + proxy skeleton).
COPY infra/aws-agent-isolation/broker /opt/grace2/broker
ENV PYTHONPATH=/opt/grace2

RUN useradd --create-home --uid 10002 broker
USER broker
WORKDIR /home/broker

# Broker port (ALB target). /healthz is the ALB target-group health check; every
# other connection upgrades to WS and is proxied to the per-session agent task.
EXPOSE 8080

# The server entry: serve(handle_connection) on BROKER_PORT (default 8080) with a
# GET /healthz HTTP short-circuit (broker/server.py). PYTHONPATH=/opt/grace2 makes
# ``broker`` importable as a top-level package.
ENTRYPOINT ["python", "-m", "broker.server"]
