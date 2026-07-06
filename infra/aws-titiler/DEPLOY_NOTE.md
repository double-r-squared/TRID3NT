# DEPLOY NOTE — isolated TiTiler tile box (`infra/aws-titiler`)

Self-contained OpenTofu root that stands up a **tiny, always-on EC2 box that
runs ONLY TiTiler** (the map's raster tile server, `:8080`), so the heavy agent
box (`i-0251879a278df797f`, t3.large) can scale to zero under the Wave-3
auto-stop (`infra/aws-autostop`) **without blanking the map**.

**Why (DECISION NATE 2026-06-17, option A):** the agent box today CO-HOSTS
`grace2-agent` (WS `:8765` + catalog/health HTTP `:8766`) **and** TiTiler
(`:8080`). Re-arming auto-stop would kill TiTiler with the box. This root
isolates TiTiler onto its own box and the CloudFront `/tiles*` + `/cog/*`
origin is repointed here. The **catalog/health `:8766` STAYS on the agent box**
(it reports the agent's WS-connection state — the signal the idle Lambda polls);
**only TiTiler moves.** COGs live durably in S3; TiTiler just serves them.

It provisions:

| Resource | Purpose |
|---|---|
| `grace2-titiler-box` EC2 (t3.small, x86_64, AL2023) | Always-on TiTiler tile server (`:8080`, plain uvicorn, systemd). |
| `grace2-titiler-eip` Elastic IP | Stable public DNS = the CloudFront origin address. |
| `grace2-titiler-ec2-role` + profile | Instance role: `AmazonS3ReadOnlyAccess` (COGs via `/vsis3`) + `AmazonSSMManagedInstanceCore`. **No S3 writes.** |
| `grace2-titiler-sg` security group | `:8080` from the CloudFront managed prefix list (preferred) or a fallback CIDR; all egress; no SSH by default. |
| `user_data.sh.tftpl` | Reproduces the agent box's `titiler.service` **verbatim** + the job-0314 watchdog. |

The TiTiler install is a **faithful clone** of the live box (Investigate
findings): same package pins (`titiler.application==2.0.4` → titiler-core/
extensions/mosaic/xarray 2.0.4 + rasterio 1.4.4 + fastapi 0.136.3 + uvicorn
0.49.0), same `ExecStart` (plain uvicorn `titiler.application.main:app`), same
GDAL/AWS/CPL/VSI `Environment=` lines, `Restart=always`, boot-enabled. The agent
box runs **4** workers on a t3.large; this tiny box defaults to **2** workers
(`var.titiler_workers`) sized to a 2 vCPU t3.small.

> **AUTHORED, NOT APPLIED.** Nothing here mutates the live agent box or the live
> CloudFront distribution. NATE / the orchestrator applies, verify-before-cutover.

---

## Prerequisites (NATE's interactive steps — agents must NOT script these)

1. **AWS SSO login** (the apply credential):
   ```
   aws sso login
   aws sts get-caller-identity     # confirm account 226996537797
   ```
2. **OpenTofu** ≥ 1.8 on PATH (`tofu version`).
3. **Resolve the CloudFront prefix list id** (region-specific) for the tightest
   `:8080` ingress, then put it in a tfvars file:
   ```
   aws ec2 describe-managed-prefix-lists --region us-west-2 \
     --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
     --query 'PrefixLists[0].PrefixListId' --output text
   # -> set cloudfront_prefix_list_id = "pl-..." in terraform.tfvars
   ```
   If left `""`, the SG falls back to `var.ingress_cidr` (default `0.0.0.0/0`,
   reproducing the agent box's broader `:8080` posture — acceptable since
   TiTiler serves only public tiles, but the prefix list is strongly preferred).

---

## Apply

```
cd infra/aws-titiler
tofu init
tofu fmt -check
tofu validate
tofu plan -out tfplan        # review: 1 EC2 + 1 EIP + 1 assoc + role/profile + SG (+rules)
tofu apply tfplan
```

Outputs of interest:
- `titiler_public_dns`  — the CloudFront origin address (the cutover target).
- `titiler_origin_url`  — `http://<dns>:8080` for the pre-cutover smoke test.
- `titiler_instance_id` — for SSM access / status checks.

---

## Verify the box BEFORE touching CloudFront

```
NEW=$(tofu -chdir=infra/aws-titiler output -raw titiler_public_dns)
# Pick a COG that EXISTS in the runs bucket, then:
curl -s -o /dev/null -w '%{http_code}\n' \
  "http://$NEW:8080/cog/info?url=s3://grace2-hazard-runs-226996537797/<known.tif>"
# Expect 200. (000/timeout = box not ready; do NOT cut over. 404 = box healthy,
# wrong key — pick a real key.)
```
If `:8080` is locked to the CloudFront prefix list, the curl above (from your
laptop) will NOT reach it — temporarily verify via SSM on the box:
```
aws ssm start-session --target $(tofu -chdir=infra/aws-titiler output -raw titiler_instance_id)
# on the box:
systemctl status titiler
curl -s -o /dev/null -w '%{http_code}\n' "http://localhost:8080/cog/info?url=s3://grace2-hazard-runs-226996537797/<known.tif>"
```

---

## CloudFront `/tiles` + `/cog` origin repoint (THE CUTOVER)

The full, exact procedure (aws-cli read-modify-write of the live config, plus a
reference OpenTofu form) is authored in **`cloudfront-tiles-origin.tf.docs`** in
this directory. Summary:

- Distribution `E2L74AS56MVZ87`, origin `origin-titiler`.
- Change **only** `origin-titiler.DomainName` from
  `ec2-54-185-114-233.us-west-2.compute.amazonaws.com` (agent box) to the new
  box's `titiler_public_dns`. Keep `HTTPPort=8080`, `http-only`,
  `OriginReadTimeout=30`.
- This moves **both** `/tiles*` and `/cog/*` (they share `origin-titiler`) —
  desired. The agent keeps `origin-agent-ws` (`/ws*`), `origin-catalog`
  (`/api/*`), and `origin-s3-web` (default).
- Always re-fetch the live `ETag` (`E23ZP02F085DFQ` rotates) before
  `update-distribution`. `wait distribution-deployed`, then curl a `/cog/info`
  and a `/tiles/...` through CloudFront — expect `200` on both.

---

## After cutover

Once tiles serve from this box through CloudFront, the agent box no longer needs
`:8080` and **`infra/aws-autostop` can be re-armed** — the map stays alive 24/7
while the heavy agent scales to zero. The health-COG watchdog
(`s3://grace2-agent-bundle-226996537797/health/titiler-health.tif`) is installed
on this box too (`var.install_watchdog=true`); that key is readable via
`AmazonS3ReadOnlyAccess`.

---

## Cost (est.)

| Item | Monthly (us-west-2, on-demand) |
|---|---|
| t3.small (2 vCPU/2 GiB), 24/7 | ~$15.2 (≈ $0.0208/hr × 730) |
| gp3 root 20 GiB | ~$1.6 |
| Elastic IP (attached to a running instance) | $0 |
| S3 GET / data transfer to CloudFront | already incurred today; no net new (tiles just originate from a different box) |
| **Total net-new** | **~$17/mo** |

This is the cost of keeping the map alive 24/7. It is **offset** by re-arming
auto-stop on the t3.large agent box (~$60/mo if it ran 24/7) — the agent now
scales to zero when idle. `t3.micro` (~$7.6/mo, `--workers 1`) is a cheaper
option if the always-on tile load proves light; `t3.small` is the safe default.

---

## 2026-07-06 — the box is now DUAL-ROLE (TiTiler + session broker)

Scale-to-zero Phase 2 put the session broker on this box as a docker/systemd
unit (`grace2-broker.service`, `:8081`, image `grace2-broker:latest` from ECR),
replacing the ALB + Fargate broker service. Grants live in
`infra/aws-agent-isolation/broker_on_box.tf` (cross-root: broker IAM policy +
ECR read on `grace2-titiler-ec2-role`; `grace2-broker-box` SG attached to the
box ENI for `:8081` from CloudFront). A future `tofu apply` in THIS root will
show the extra SG as drift on `vpc_security_group_ids` — KEEP IT. The box also
gained docker, a 2G swapfile, and `/etc/grace2/broker.env`. Broker deploys =
push image, then SSM: `docker pull ... && systemctl restart grace2-broker`.
