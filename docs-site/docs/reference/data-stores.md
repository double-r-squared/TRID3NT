# Data Stores

---

## DynamoDB tables

AWS region: `us-west-2`. All application tables use `PAY_PER_REQUEST` billing.

### Live prefix: `trid3nt_`

The live Fargate agents have env var `GRACE2_DYNAMO_TABLE_PREFIX=trid3nt_` (set in
`infra/aws-agent-isolation/ecs.tf`). The default in code is `grace2_` -- local dev uses the
default, production agents use `trid3nt_`.

| Table | PK | SK | GSIs | Notes |
|---|---|---|---|---|
| `trid3nt_cases` | `_id` (String) | -- | `user_id-index`, `session_id-index` | Case metadata; user_id is Cognito sub |
| `trid3nt_chat` | `case_id` (String) | `msg_id` (String) | -- | Per-case chat history |
| `trid3nt_sessions` | `_id` (String) | -- | `user_id-index` | Session -> user mappings |
| `trid3nt_users` | `_id` (String) | -- | `firebase_uid-index` (legacy name) | User profiles |
| `trid3nt_secrets` | `_id` (String) | -- | `user_id-index` | Per-user soft-revokable credentials vault |
| `trid3nt_telemetry` | (per-invocation) | -- | -- | Per-tool invocation records |

### Special-purpose tables (not prefixed)

| Table | PK | SK | Notes |
|---|---|---|---|
| `grace2_session_routes` | `user_ulid` (String) | `session_id` (String) | Broker route table; TTL 24 h; PITR on; `PAY_PER_REQUEST` |
| `grace2-autostop-state` | (per-task) | -- | Reaper idle-streak counters |

### Legacy tables (pre-rename, zero cost, dead data)

The following tables predate the `trid3nt_` rename and contain only historical data. They are
candidates for archival/deletion (Phase 0 migration):

| Table | Approx items |
|---|---|
| `grace2_chat` | 1735 |
| `grace2_cases` | 167 |
| `grace2_users` | 209 |
| `grace2_sessions` | 182 |

---

## S3 buckets

AWS region: `us-west-2`, account `226996537797`.

| Bucket | BPA | Purpose | Key layout |
|---|---|---|---|
| `grace2-hazard-runs-226996537797` | On (signer only) | Run outputs, case views, manifests, Terraform state | See layout below |
| `grace-2-hazard-prod-cog` | On | Published COG rasters (TiTiler source) | `/cog/` prefix; per-run subdirs |
| `grace2-hazard-web-226996537797` | Off (cold catalog public-read) | SPA static assets + cold tool catalog | `catalog/tool-catalog.json` |
| `grace2-hazard-cache-226996537797` | On | Agent tool cache (rasterio outputs, etc.) | keyed by tool+args hash |
| `grace-2-hazard-prod-qgs` | On | QGIS project files (.qgs) per case | per-case key |
| `grace2-agent-bundle-226996537797` | On | Agent deploy bundle | `engine-build/agent_deploy_src.tgz` |

### `grace2-hazard-runs-226996537797` key layout

```
runs/
  <run_id>/                      ULID per engine run
    job_spec.json                Input parameters (written by agent at submit)
    publish_manifest.json        Layer refs (written by worker BEFORE completion)
    completion.json              Final status (written by worker LAST)
    <engine_outputs>/            COGs, NetCDF, logs

case-views/
  <case_id>/
    snapshot.json                Cold-view snapshot (layer refs, no live agent needed)

case-manifests/
  <case_id>/
    latest.json                  Most recent published manifest for the case

catalog/
  tool-catalog.json              (also mirrored to web bucket with public-read)
```

---

## CORS configuration

The runs bucket has a scoped CORS policy (applied in `infra/aws-autostop/cors.tf`). This was added
to fix the "box-off, no layers" bug (S3 CORS blocked browser cross-origin presigned URL fetches).

The web bucket has no CORS restriction (public-read cold catalog).

---

## Terraform state backend

Terraform/OpenTofu state files for all `infra/` stacks are stored in the runs bucket:

```
s3://grace2-hazard-runs-226996537797/terraform/
  <stack-name>/
    terraform.tfstate
```

---

## Legacy GCP storage (decommissioned)

The following GCS buckets were used pre-AWS migration. They are defined in `infra/buckets.tf`
(legacy GCP IaC) and are no longer used by the live system:

- `grace-2-hazard-prod-cog` (GCS)
- `grace-2-hazard-prod-fgb` (GCS, FlatGeobuf vectors)
- `grace-2-hazard-prod-qgs` (GCS, QGIS projects)
- `grace-2-hazard-prod-runs` (GCS, solver outputs)
