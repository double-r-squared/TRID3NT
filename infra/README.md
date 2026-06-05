# infra/ — Infrastructure as code (OpenTofu / Terraform)

**Owner:** `infra` specialist.

The GCP substrate everything else deploys onto (SRS v0.3 Decision E, NFR-PO-3,
NFR-C-*, NFR-S-*). Declared as OpenTofu (`tofu`) configuration — the MPL-2.0,
drop-in-compatible fork chosen over BUSL Terraform (PROJECT_STATE decision
2026-06-05; NFR-PO-3 permits "or equivalent", and all-OSI tooling matches the
NFR-L posture).

What lives here (provisioned incrementally across infra jobs):

- GCP project bootstrap, enabled APIs, service accounts + Workload Identity
- Cloud Run services (agent, QGIS Server) and Cloud Run Jobs (workers, solver)
- Cloud Workflows definitions (multi-step runs; the `terminate` cancel path)
- GCS buckets + lifecycle (`.qgs`, COG/FlatGeobuf/GeoParquet, cache)
- MongoDB Atlas provisioning + the three Vector Search indexes + MCP hosting
- Secret Manager (connection strings; never in code/repo/images)
- WSS/TLS termination, web hosting / CDN, CI plumbing, budget labels

**IaC is the source of truth** — no console-clicked resource that the code does
not capture (infra domain discipline). Every resource is labeled so the
NFR-C-1 idle-cost breakdown can be produced mechanically.

The GCP project + Atlas M0 land in `job-0014` (toolchain install +
`! gcloud auth login` / `! atlas auth login` user-auth checkpoints).
