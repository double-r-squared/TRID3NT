# RUNBOOK — SFINCS AWS Batch cutover

Moves the SFINCS solver from always-on EC2 (local-docker backend) to AWS Batch
(scale-to-zero SPOT). After this runbook completes, the c7i.2xlarge agent box
can be downsized to t3.large (~$60/mo) or t3.medium (~$30/mo).

**Authorize-list: NATE ALMANZA only.** This runbook requires AWS admin/infra
credentials and SSH access to the EC2 agent. No agent or CI system runs
`tofu apply` or any AWS-mutating command — you run every step below with your
own credentials.

---

## Prerequisites

- AWS CLI configured with credentials that have admin/infra permissions on
  account 226996537797 (IAM, Batch, ECR, EC2, S3, CloudWatch, SSM full access).
  Verify: `aws sts get-caller-identity` should return the 226996537797 account.
- Docker (or docker buildx) installed locally for building the worker image.
  The image must target linux/amd64 — the SFINCS binary is amd64-only.
- OpenTofu >= 1.8.0: `tofu version`.
- The agent code already supports the aws-batch backend. The `GRACE2_SOLVER_BACKEND`
  env var in solver.py reads "aws-batch" and routes to `_run_solver_aws_batch()`.
  No code changes are required.

---

## Step 0 — Verify the Dockerfile installs boto3

The entrypoint at `services/workers/sfincs/entrypoint.py` uses lazy boto3 import
for S3 I/O. Boto3 must be present in the image. Check the current Dockerfile:

```
grep -n boto3 services/workers/sfincs/Dockerfile
```

If the line is missing, add it to the `pip3 install` block. Find this section:

```dockerfile
    && pip3 install --no-cache-dir \
        "google-cloud-storage>=2.18,<4" \
```

And change it to:

```dockerfile
    && pip3 install --no-cache-dir \
        "google-cloud-storage>=2.18,<4" \
        "boto3>=1.34,<2" \
```

Then verify the import at build time by adding to the smoke-run line
(or relying on the `tofu plan` + build step to catch it):

```dockerfile
    && python3 -c "import boto3; print('boto3 import OK')"
```

At the time this runbook was authored, boto3 was a lazy import (only called
when GRACE2_OBJECT_STORE=s3). The Dockerfile did not include boto3. You MUST
add it before the image build in Step 1.

---

## Step 1 — Build and push the SFINCS worker image to ECR

Run the tofu steps first (Step 2) so ECR exists before you try to push. Then
come back here.

**After `tofu apply` completes**, get the ECR URL from the output:

```sh
ECR_URL=$(tofu -chdir=infra/aws-batch output -raw ecr_repository_url)
# Example: 226996537797.dkr.ecr.us-west-2.amazonaws.com/grace2-sfincs
echo $ECR_URL
```

Authenticate Docker with ECR:

```sh
aws ecr get-login-password --region us-west-2 \
  | docker login --username AWS --password-stdin "${ECR_URL%/*}"
```

Build for linux/amd64 (required — SFINCS binary is x86_64):

```sh
# Run from the repo root
docker buildx build \
  --platform linux/amd64 \
  --file services/workers/sfincs/Dockerfile \
  --tag "${ECR_URL}:latest" \
  --push \
  .
```

If buildx is not set up for cross-platform builds, build natively on an x86_64
host (the agent EC2 box itself or any amd64 machine):

```sh
# SSH into the agent box or run on an amd64 machine
docker build \
  --file services/workers/sfincs/Dockerfile \
  --tag "${ECR_URL}:latest" \
  .
docker push "${ECR_URL}:latest"
```

Verify the image appears in ECR:

```sh
aws ecr describe-images \
  --repository-name grace2-sfincs \
  --region us-west-2 \
  --query 'imageDetails[*].{pushed:imagePushedAt,digest:imageDigest,tags:imageTags}' \
  --output table
```

---

## Step 2 — Provision the Batch infrastructure with OpenTofu

### State backend note

This module uses a **local backend** (the default). The state file
`infra/aws-batch/terraform.tfstate` will be created on your local machine.

To use an S3 backend instead (recommended if multiple people manage this infra
or you want state durability), add this block to `versions.tf` before `tofu init`:

```hcl
terraform {
  backend "s3" {
    bucket         = "grace2-tofu-state-226996537797"   # create this bucket first
    key            = "aws-batch/terraform.tfstate"
    region         = "us-west-2"
    encrypt        = true
  }
}
```

Create the state bucket (one-time, skip if it already exists):

```sh
aws s3api create-bucket \
  --bucket grace2-tofu-state-226996537797 \
  --region us-west-2 \
  --create-bucket-configuration LocationConstraint=us-west-2
aws s3api put-bucket-versioning \
  --bucket grace2-tofu-state-226996537797 \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption \
  --bucket grace2-tofu-state-226996537797 \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
```

### Apply

```sh
cd infra/aws-batch

tofu init

# Review what will be created (no resources are modified yet).
# Expected: ~14 new resources (ECR repo, lifecycle policy, 4 IAM roles,
# 1 IAM profile, 2 IAM managed-policy attachments, 2 IAM inline policies,
# 1 SG, 1 CW log group, 1 Batch CE, 1 Batch job queue, 1 Batch job definition).
tofu plan

# Apply — creates all resources. Estimated time: 3-5 minutes.
# The Batch compute environment creation takes the longest (~2 min).
tofu apply
```

**Capture the outputs** immediately after apply:

```sh
tofu output
```

Example output:

```
job_queue_name        = "grace2-solvers"
job_definition_name   = "grace2-sfincs"
ecr_repository_url    = "226996537797.dkr.ecr.us-west-2.amazonaws.com/grace2-sfincs"
compute_environment_arn = "arn:aws:batch:us-west-2:226996537797:compute-environment/grace2-solvers-spot"
batch_service_role_arn = "arn:aws:batch:..."
job_task_role_arn      = "arn:aws:iam::226996537797:role/grace2-batch-job-task-role"
cloudwatch_log_group_name = "/grace2/batch"
```

---

## Step 3 — Env-flip on the agent EC2 box

SSH into the agent instance (i-0251879a278df797f) or use SSM:

```sh
aws ssm start-session --target i-0251879a278df797f --region us-west-2
```

Edit the systemd service drop-in to set the new env vars. The existing service
unit is at `/etc/systemd/system/grace2-agent.service` (or a drop-in directory).
Use `systemctl edit` to create a drop-in that overrides only the Environment lines:

```sh
sudo systemctl edit grace2-agent
```

In the editor that opens, add (replace the placeholder values with the actual
tofu outputs):

```ini
[Service]
Environment="GRACE2_SOLVER_BACKEND=aws-batch"
Environment="GRACE2_AWS_BATCH_QUEUE=grace2-solvers"
Environment="GRACE2_AWS_BATCH_JOB_DEF=grace2-sfincs"
Environment="GRACE2_RUNS_BUCKET=grace2-hazard-runs-226996537797"
Environment="AWS_REGION=us-west-2"
```

Save and close the editor. The drop-in is written to
`/etc/systemd/system/grace2-agent.service.d/override.conf`.

Reload systemd and restart the agent:

```sh
sudo systemctl daemon-reload
sudo systemctl restart grace2-agent
sudo systemctl status grace2-agent
```

Verify the agent picked up the new env:

```sh
sudo systemctl show grace2-agent -p Environment
# Should show GRACE2_SOLVER_BACKEND=aws-batch among the vars.
```

Also verify the agent IAM role has the new batch permissions (no restart needed
— instance-role policies take effect within ~1 minute of the IAM propagation):

```sh
# From the agent box:
aws batch describe-job-queues --job-queues grace2-solvers --region us-west-2
# Should return the queue details without an AccessDenied error.
```

---

## Step 4 — Verify a Batch job runs end-to-end

From the GRACE-2 web app, send a prompt that triggers a SFINCS solve (e.g. a
flood modeling request for a small AOI). Confirm:

1. The agent narrates "Submitting SFINCS run..." and emits a pipeline card with
   status running.

2. A Batch job appears in the queue:

   ```sh
   aws batch list-jobs \
     --job-queue grace2-solvers \
     --job-status RUNNING \
     --region us-west-2
   ```

3. The job completes (status transitions RUNNING -> SUCCEEDED):

   ```sh
   aws batch list-jobs \
     --job-queue grace2-solvers \
     --job-status SUCCEEDED \
     --region us-west-2
   ```

4. The completion manifest lands on S3:

   ```sh
   # Replace <run_id> with the ULID shown in the agent logs or pipeline card.
   aws s3 ls s3://grace2-hazard-runs-226996537797/<run_id>/
   aws s3 cp s3://grace2-hazard-runs-226996537797/<run_id>/completion.json - | jq .
   # Expect: {"status": "ok", "exit_code": 0, ...}
   ```

5. The flood inundation layer renders on the map.

If the job fails, check CloudWatch Logs:

```sh
aws logs tail /grace2/batch --follow
```

Or inspect the specific log stream:

```sh
aws logs describe-log-streams \
  --log-group-name /grace2/batch \
  --order-by LastEventTime \
  --descending \
  --max-items 5 \
  --region us-west-2
```

---

## Step 5 — Downsize the agent EC2 box

**Do this ONLY after Step 4 passes.** The agent box no longer needs compute
headroom for SFINCS — it only runs the WebSocket server, the Gemini SDK client,
and tool dispatch coordination.

Current instance: c7i.2xlarge (8 vCPU, 16 GiB) — ~$257/mo On-Demand in us-west-2.

Target options:
- **t3.large** (2 vCPU, 8 GiB) — ~$60/mo. Recommended if MongoDB MCP server,
  TiTiler (port 8080), or QGIS Server will run on the same box. x86_64, no
  rebuild needed.
- **t3.medium** (2 vCPU, 4 GiB) — ~$30/mo. Adequate if only the agent process
  and MongoDB MCP run locally; TiTiler and QGIS Server are separated or deferred.

Decision note: QGIS Server is currently deferred (per project_qgis_processing_as_agentic_compute_substrate.md).
TiTiler (:8080) is already running on the agent box. 8 GiB (t3.large) is the
safe choice. t3.medium risks OOM if TiTiler + the agent + MCP peak simultaneously.

Steps:

```sh
# 1. Stop the instance (~30 seconds; client reconnects via disconnect-resilience).
aws ec2 stop-instances --instance-ids i-0251879a278df797f --region us-west-2

# 2. Wait for the instance to reach stopped state.
aws ec2 wait instance-stopped --instance-ids i-0251879a278df797f --region us-west-2

# 3. Change the instance type.
aws ec2 modify-instance-attribute \
  --instance-id i-0251879a278df797f \
  --instance-type t3.large \
  --region us-west-2

# 4. Start the instance.
aws ec2 start-instances --instance-ids i-0251879a278df797f --region us-west-2

# 5. Wait for the instance to reach running state.
aws ec2 wait instance-running --instance-ids i-0251879a278df797f --region us-west-2

# 6. Verify the agent recovered (the Elastic IP / EIP is preserved across
#    instance type changes since the EIP is attached to the ENI, not the
#    instance type).
aws ec2 describe-instances \
  --instance-ids i-0251879a278df797f \
  --query 'Reservations[0].Instances[0].{Type:InstanceType,State:State.Name,PublicIP:PublicIpAddress}' \
  --region us-west-2

# 7. Check the agent service came back up.
aws ssm start-session --target i-0251879a278df797f --region us-west-2
# then: sudo systemctl status grace2-agent
```

The agent WebSocket clients reconnect automatically within a few seconds of
the instance coming back — this is the disconnect-resilience path documented in
feedback_per_case_layer_durability.md.

Note: t3 is x86_64, same as c7i. No Docker image rebuild is needed for the
agent or any other service that was already running on the c7i box.

---

## Cost summary

| Resource              | Before              | After              |
|----------------------|---------------------|--------------------|
| Agent EC2 (always-on) | c7i.2xlarge ~$257/mo | t3.large ~$60/mo OR t3.medium ~$30/mo |
| SFINCS compute        | included in EC2     | Batch SPOT: ~$0.04-0.08/vCPU-hr, scale-to-zero; idle cost = $0 |
| ECR storage           | $0 (none)           | ~$0.10/GB-month; <1 GB image = ~$0.10/mo |
| CloudWatch Logs       | $0                  | ~$0.50/GB ingested; low volume |
| **Net saving**        |                     | **~$197-227/mo** (c7i downsize alone) |

Batch SPOT costs are workload-dependent. A standard-class (8 vCPU) SFINCS run
typically completes in 5-15 minutes; at $0.06/vCPU-hr SPOT the per-run cost is
roughly $0.04-0.12. For a demo-frequency workload (< 10 runs/day) the monthly
Batch cost is under $5.

---

## Rollback

If Batch verification fails or a regression is detected, revert to the
local-docker backend in under 5 minutes:

```sh
# SSH or SSM into the agent box:
sudo systemctl edit grace2-agent
# Change the drop-in to:
#   Environment="GRACE2_SOLVER_BACKEND=local-docker"
#   (remove or comment out GRACE2_AWS_BATCH_QUEUE and GRACE2_AWS_BATCH_JOB_DEF)

sudo systemctl daemon-reload
sudo systemctl restart grace2-agent
sudo systemctl status grace2-agent
```

To roll back the instance type as well (if already downsized):

```sh
aws ec2 stop-instances --instance-ids i-0251879a278df797f --region us-west-2
aws ec2 wait instance-stopped --instance-ids i-0251879a278df797f --region us-west-2
aws ec2 modify-instance-attribute \
  --instance-id i-0251879a278df797f \
  --instance-type c7i.2xlarge \
  --region us-west-2
aws ec2 start-instances --instance-ids i-0251879a278df797f --region us-west-2
```

The Batch infrastructure (CE, queue, job definition, ECR, IAM) can be left in
place — it costs nothing when idle. Run `tofu destroy` from `infra/aws-batch/`
only if you want to fully tear it down.
