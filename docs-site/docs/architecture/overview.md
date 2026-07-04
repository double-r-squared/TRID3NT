# Architecture Overview

Source of truth: `reports/design/scale-to-zero-architecture-2026-07-04.md` (live-verified 2026-07-04,
AWS account 226996537797, us-west-2).

---

## Tier diagram

```mermaid
graph TD
    subgraph Browser["Browser (Vercel SPA)"]
        SPA["React + MapLibre GL JS"]
    end

    subgraph CDN["CDN / Edge"]
        CF["CloudFront E2L74AS56MVZ87\nd125yfbyjrpbre.cloudfront.net"]
        APIGW["API GW 9ib093sis6\n(serverless cold paths)"]
    end

    subgraph AlwaysOn["Always-On (tiny)"]
        TiTiler["TiTiler EC2 t3.small :8080\ni-06cfdd3d6c66b2126"]
        Broker["Broker Fargate\n0.5 vCPU / 1 GB :8081 (planned)\nor ALB :443 (current)"]
    end

    subgraph Serverless["Serverless Cold Paths"]
        Lambdas["Lambdas: case-list, case-view-url\nexport, demo-token, wake"]
        DDB_cold["DynamoDB on-demand"]
        S3_web["S3 web bucket\ngrace2-hazard-web-226996537797"]
    end

    subgraph PerSession["Per-Session (ephemeral)"]
        Agent["Agent Fargate\n2 vCPU / 8 GB\ngrace2-agent-session"]
    end

    subgraph Batch["AWS Batch (Spot, scale-to-zero)"]
        Queue["grace2-solvers queue\nSpot CE (order 1) + on-demand CE (order 2)"]
        Workers["Per-engine workers\nbuild + solve + postprocess"]
    end

    subgraph Persist["Persistence"]
        DDB["DynamoDB trid3nt_* tables"]
        S3runs["S3 grace2-hazard-runs-226996537797\nruns/, case-views/, case-manifests/"]
        S3cog["S3 grace-2-hazard-prod-cog\npublished COG rasters"]
    end

    SPA -- "/ws* WS" --> CF
    SPA -- "/cog* /tiles*" --> CF
    SPA -- "cold API" --> APIGW
    SPA -- "catalog/tool-catalog.json" --> S3_web

    CF -- "/cog* /tiles*" --> TiTiler
    CF -- "/ws*" --> Broker
    CF -- "/*" --> S3_web

    APIGW --> Lambdas
    Lambdas --> DDB_cold
    Lambdas --> S3runs

    Broker -- "ecs:RunTask" --> Agent
    Agent --> DDB
    Agent --> S3runs
    Agent -- "submit job" --> Queue
    Queue --> Workers
    Workers --> S3runs
    Workers --> S3cog
```

---

## The three scale-to-zero islands

The architecture is built around three **independent** scale-to-zero islands that interoperate
without depending on each other:

| Island | Components | Idle cost | Scale mechanism |
|--------|-----------|-----------|-----------------|
| **Data / Render** | TiTiler EC2, S3, CloudFront, Lambdas, DynamoDB | ~$20/mo | TiTiler is the single always-on exception; everything else is truly serverless |
| **Agents** | Broker + per-session Fargate tasks | ~$32/mo today (target ~$15) | Broker always-on (cheap); agents torn down after 30 min idle via reaper + self-idle-exit |
| **Engines** | AWS Batch Spot CE, worker containers | ~$0 between jobs | Both CEs at min/desired vCPU = 0; RunTask only on solver submit |

**Key design principle:** the client can render cold case views -- map layers with no live agent -- by
reading snapshot URLs from S3 via signed URLs from the cold-path Lambdas. Only interactive chat and
new simulation runs require a live agent.

---

## Request lifecycles

### Cold view path (agent offline)

```mermaid
sequenceDiagram
    participant C as Browser
    participant CF as CloudFront
    participant APIGW as API GW
    participant L as Lambda
    participant DDB as DynamoDB
    participant S3 as S3

    C->>CF: GET /case-list (cold API)
    CF->>APIGW: forward
    APIGW->>L: case-list Lambda
    L->>DDB: scan trid3nt_cases by user_id
    L-->>C: JSON case list (200)

    C->>CF: GET /case-view-url?case_id=...
    CF->>APIGW: forward
    APIGW->>L: case-view-url Lambda
    L->>S3: presign case-views/<case_id>/snapshot.json
    L-->>C: presigned URL (200)

    C->>S3: GET snapshot.json (presigned)
    S3-->>C: layer refs (tile URLs)

    Note over C: Map renders from tile URLs -- no agent needed
```

### Live session path (agent warm)

```mermaid
sequenceDiagram
    participant C as Browser
    participant CF as CloudFront
    participant B as Broker Fargate
    participant A as Agent Fargate (ephemeral)
    participant DDB as DynamoDB

    C->>CF: WS upgrade /ws
    CF->>B: WS upgrade
    B->>B: token verify (Cognito JWKS)
    B->>DDB: GetItem grace2_session_routes
    alt route exists (warm)
        B-->>A: forward frames
    else cold provision
        B->>B: ecs:RunTask grace2-agent-session
        B->>B: poll DescribeTasks 2s
        B->>B: health-probe :8766 poll 2s
        Note over B: ~40-48 s total
        B->>DDB: PutItem route row (TTL 24 h)
        B-->>A: forward frames
    end

    C->>B: auth-token (FIRST frame)
    B-->>A: forward
    A-->>C: auth-ack
    C->>B: session-resume
    A-->>C: session-state (case list + active layers)
```

### Solve path

```mermaid
sequenceDiagram
    participant C as Browser
    participant A as Agent
    participant Bedrock as AWS Bedrock
    participant Batch as AWS Batch
    participant W as Worker (Fargate Spot)
    participant S3 as S3

    C->>A: user-message
    A->>Bedrock: converse_stream (tools)
    Bedrock-->>A: tool_use: run_model_flood_scenario
    A->>C: solver-confirm gate (AskUserQuestion)
    C->>A: confirmation
    A->>Batch: SubmitJob (grace2-solvers queue)
    A-->>C: solve-progress updates
    loop poll every 10s (off asyncio loop)
        A->>Batch: DescribeJobs
    end
    W->>S3: write COGs + publish_manifest.json (BEFORE completion.json)
    W->>S3: write completion.json
    A->>S3: read publish_manifest.json
    A->>C: map-command (add layers with tile URLs)
    A->>Bedrock: resume turn (tool_result)
    Bedrock-->>A: final text
    A->>C: agent-message-chunk (done=True) + turn-complete
```

---

## Scale-to-zero migration phases

See `reports/design/scale-to-zero-architecture-2026-07-04.md` Section 2.6 for the full plan.

| Phase | Change | Savings |
|-------|--------|---------|
| 0 | Decommission stopped agent box + legacy Lambdas + orphan DynamoDB tables | ~$6/mo |
| 1 | Heartbeat-reaper: agent writes heartbeat to route row; reaper reads DynamoDB only; delete VPC interface endpoints | ~$29/mo |
| 2 | Move broker onto TiTiler t3.small; delete ALB + broker ECS service | ~$32/mo |
| 3 | Client hardening: route non-queued frame types through `sendOrQueue` | reliability |
| 4 | Agent diet: lazy imports, remove google-genai types dependency, Phase-2 fetcher offload; 8 GB -> 4 GB | ~4x session cost |
