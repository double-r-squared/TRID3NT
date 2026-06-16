# GCP Teardown

The GCP → AWS migration is complete and verified live (`https://d125yfbyjrpbre.cloudfront.net/app`). Nothing in the running product depends on GCP anymore, so the old project can be deleted to stop residual Vertex/Cloud-Run billing.

**Project to delete:** `grace-2-hazard-prod`
**Recovery:** project delete is a **30-day soft-delete** — restorable from the same screen (or `gcloud projects undelete`) until then, permanent after.

## Option A — GCP Console (no CLI needed; recommended)

1. **console.cloud.google.com** → select project **`grace-2-hazard-prod`** in the top project picker.
2. **IAM & Admin → Settings** → **SHUT DOWN** (Delete project).
3. Type the project ID to confirm. It enters the 30-day recovery window.
4. **Billing → Account management →** unlink the project (belt-and-suspenders; deletion stops charges regardless).

## Option B — gcloud CLI

```bash
gcloud auth login                       # interactive (your Google account)
gcloud projects list                    # confirm the project ID
gcloud projects delete grace-2-hazard-prod
# recover within 30 days if needed:
# gcloud projects undelete grace-2-hazard-prod
```
gcloud is not installed on the current dev box; install it user-local first if you go this route:
```bash
curl -O https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-x86_64.tar.gz
tar -xf google-cloud-cli-linux-x86_64.tar.gz && ./google-cloud-sdk/install.sh -q
export PATH="$PWD/google-cloud-sdk/bin:$PATH"
```

## After deletion
- Confirm in **Billing** that no GCP charges accrue on the next cycle.
- AWS is the sole environment; see `reports/PROJECT_STATE.md` (CURRENT TRUTH) for the live stack.
