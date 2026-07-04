# TRID3NT Developer Documentation

**TRID3NT** (internal codename **GRACE-2**) is a web-based AI workbench for multi-hazard geospatial
modeling. A user types a natural-language prompt; the system composes a pipeline of data-fetch tools,
numerical simulation engines, and rendering steps; results appear as map layers with an AI-generated
narrative.

---

!!! warning "SRS vs. As-Built"
    The SRS under `docs/srs/` is the **frozen original requirements specification** written for the
    GCP/Gemini era. It is the user's document and is not updated by engineers.

    **This site documents the system as built, running on AWS with Bedrock.** Where the SRS and the
    live code differ, **this site and the source code are authoritative.**

---

## Who this documentation is for

| Reader | Relevant sections |
|--------|------------------|
| **LLM / AI contributor** | All sections -- start with Architecture Overview, then the protocol and worker-contract references |
| **Backend engineer** | Session Tier, Agent Process, Compute Tier, WS Protocol, Worker Contract |
| **Frontend engineer** | Edge and Web, WS Protocol |
| **Ops / DevOps** | Operations (Deploy, Runbook, Verification) |
| **Engine integrator** | Compute Tier, Engines, Worker Contract, Contributing |

## How the system works in one paragraph

The user's browser (a Vercel-hosted React/MapLibre SPA) connects to the backend over a persistent
WebSocket routed through CloudFront -> ALB -> a long-lived **broker** Fargate container. On first
connect the broker provisions a **per-session ephemeral agent** (Fargate, 2 vCPU / 8 GB) via
`ecs:RunTask`. The agent is an asyncio process that holds the WS connection, calls
**AWS Bedrock** (Sonnet default, Haiku/Nova selectable) to drive tool selection, and submits heavy
compute to **AWS Batch** (Spot-first, scale-to-zero). Workers run the full build+solve+postprocess
pipeline, write COG rasters and a `publish_manifest.json` to S3, and the agent pushes tile-URL
map-command envelopes back to the client. Cold case views (agent offline) are served from S3
snapshots via serverless Lambdas; raster tiles stream from a tiny always-on **TiTiler** EC2.

## Stack at a glance

| Component | Technology | Notes |
|-----------|-----------|-------|
| SPA | React + MapLibre GL JS, Vercel | auto-deploys on push to `origin/main` |
| CDN | CloudFront `E2L74AS56MVZ87`, `d125yfbyjrpbre.cloudfront.net` | routes WS, tiles, API, web |
| Session entry | ALB + broker Fargate (0.5 vCPU / 1 GB, always-on) | WS proxy + RunTask |
| Agent | Per-session Fargate (2 vCPU / 8 GB, ephemeral) | asyncio, ~160 tools |
| LLM | AWS Bedrock (`us.anthropic.claude-sonnet-4-6` default) | `bedrock_adapter.py` |
| Compute | AWS Batch (`grace2-solvers` queue, Spot CE + on-demand CE) | scale-to-zero |
| Tiles | TiTiler EC2 `t3.small` (`i-06cfdd3d6c66b2126`, always-on) | COG /vsis3 |
| Auth | Amazon Cognito `us-west-2_mIpKrr727` | JWT, 4401 close code |
| Persistence | DynamoDB (`trid3nt_*` tables) + S3 (`grace2-hazard-runs-*`) | on-demand pricing |

## Idle cost model

Current idle bill: **~$92/mo** (ALB + broker + VPC endpoints + TiTiler EC2 + legacy stopped box).
Target after scale-to-zero migration phases: **~$22-25/mo** (single t3.small for tiles + broker).
Per-session active cost: **~$0.34/hr** per active 8 GB Fargate agent. Solvers on Spot: near-zero
between jobs.

See `reports/design/scale-to-zero-architecture-2026-07-04.md` for the full cost breakdown and
migration plan.
