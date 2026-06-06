# Project State

**Last updated:** 2026-06-05 (sprint-04 / M2 opened)
**Current sprint:** sprint-04 (active, opened 2026-06-05) — M2 Foundation: QGIS Server in cloud + PyQGIS worker prototype

## Resume note (read first)

Project resumed on a **new Debian Linux dev machine** (`Linux maturin 6.12.74+deb13`) cloned from GitHub. The original Mac's machine-local state did not travel (gcloud/atlas auth sessions, the OpenTofu install, the `grace2` conda env). On this machine:

- gcloud 571.0.0 installed + authed (user + ADC)
- atlas CLI 1.55.0 installed + authed (user account)
- OpenTofu 1.12.1 installed
- Atlas Flex cluster `grace-2-dev` provisioned manually via Atlas UI (the prior Mac never reached this point) — OpenTofu in job-0014 will `import` it rather than create it
- No `grace2` conda env yet — created when worker code lands (job-0015 doesn't need it; job-0014 doesn't need it)

Sprint-03 Stage A status: **job-0012 closed approved (this audit)**. job-0013 (contracts) has 10 modules written under `packages/contracts/src/grace2_contracts/` but no tests, schema export, or report — still `in-progress`, awaiting user go to launch a finish-and-verify runner (not a redo). No code being written until the user says go.

## Repo layout (fact, promoted from tentative in job-0012)

| Path | Owner | State |
|---|---|---|
| `web/` | web | scaffold + README, empty client |
| `services/agent/` | agent (code); infra (container/deploy) | scaffold + README |
| `services/workers/` | engine (code); infra (image/Jobs) | scaffold + README |
| `packages/contracts/` | schema | **installed** — 10 pydantic v2 modules + 91/91 tests + 35 JSON schemas (idempotent export); `grace2-contracts` v0.1.0; commit `2ce9272` |
| `infra/` | infra | scaffold + README; OpenTofu code lands in job-0014 |
| `styles/` | engine (QML); infra (QGIS Server image bake) | scaffold + README |
| `tests/` | testing (code); infra (CI) | scaffold + README |
| root | infra | `Makefile`, `.gitignore`, `README.md`, `LICENSE` (MIT) |
| `public_hazard_catalog.yaml` | engine | NOT YET CREATED (engine authors at HEP milestone) |

Repo is a git repository on branch `main`, root-commit `6fd37e6`. Remote: `https://github.com/double-r-squared/GRACE-2`. MIT `LICENSE` at root (GitHub-detectable per NFR-L-1).

## What exists

- `docs/SRS_v0.3.md` — **SRS v0.3.12** (2026-06-04), the authority for scope. Product: a web-based AI workbench for multi-hazard modeling — React/MapLibre client, Google ADK + Gemini 3 agent on Cloud Run, QGIS Server rendering `.qgs` via WMS/WMTS/WFS, PyQGIS workers (Cloud Run Jobs), MongoDB Atlas + MCP, Cloud Workflows + GCS, SFINCS flood engine, Hazard Event Pipeline with ClaimSet provenance, Public Hazard Catalog discovery mode. Appendices A–D are preemptive contract specs; amendments flow back to the user.
- `agents/` — workflow convention (`AGENTS.md`), orchestrator definition (10 invariants from Decisions A–M), six specialist definitions: `schema`, `web`, `agent`, `engine`, `infra`, `testing`.
- `CLAUDE.md` — session bootstrap (machine-portable resume instructions).
- `reports/` — sprints 01 and 02 aborted (SRS pivots; retrospectives record salvage); sprint-03 active.
- `reports/complete/job-0012-infra-20260605/` — repo realignment, approved 2026-06-05.
- Job counter at 17 (next: 0018, when a new follow-up job is opened).

## Contracts in force

**`grace2-contracts` v0.1.0** (`packages/contracts/`) — installed, 91/91 tests pass in fresh venv, 35 JSON schemas exported idempotently. Pins these orchestrator-named seams in code:

- `research_mode: Literal["research", "deep_research"] = "research"` on `user-message` payload (FR-WC-15 toggle carrier — Appendix A amendment **A1** pending user landing in SRS)
- `ExecutionHandle.workflows_execution_id` (Cloud Workflows cancellation chain — Invariant 8)
- `ClaimSet` / `NumericClaim` (Decision M / Invariant 7) — every intensity field is `ClaimSet | None`, never a bare float
- `EMBEDDING_DIMENSIONS_DEFAULT = 768` (documented constant, NOT a locked Atlas Vector Search index config — `infra` performs validation gate before locking)
- `LoadLayerArgs` ≡ `ResultLayer` field-for-field (postprocess → map with zero translation)
- `extra="forbid"` everywhere; ULIDs via `python-ulid`; UTC ISO-8601 with literal `Z` suffix

**5 SRS amendment proposals pending user landing** (A1 research_mode, A2 event_type→intensity mapping, A3 v0.2+ subtype payload typing, A4 RunDocument.assessment storage-layer note, A5 cancel-path error codes in A.6) — see `reports/complete/job-0013-schema-20260605/audit.md` § Follow-up Actions.

## Environment facts

> **Machine portability:** this repo is on GitHub; coordination state (`agents/`, `reports/`, `docs/`, `CLAUDE.md`) travels. Machine-local state (auth sessions, installed CLIs, conda env) does NOT — any new session must re-verify per the resume note above.

- **Machine:** Debian 13 (trixie) on `Linux maturin 6.12.74+deb13+1-amd64`, x86_64. NOT the original Mac. Most paths in the job-0012/0013 kickoffs that assumed macOS/Homebrew need Linux adaptation (apt/per-user installs); jobs should adapt and surface the substitution in their report.
- **Node v20.20.2 + npm 10.8.2** (web client dev ready). Note: original Mac had Node 24; v20 is the current Debian-stable Node and is fine for `react`/`vite`/`maplibre-gl` (v0.3 README mentions Node 24 — informational, not a hard requirement; revisit if a dep needs 22+).
- **Docker 29.3.1** (container builds ready).
- **Python 3.13.5** system; no venv yet. `packages/contracts/` is pydantic-v2 — needs `pip install -e packages/contracts && pip install pytest` in a fresh venv during job-0013 finish-and-verify.
- **gcloud 571.0.0** installed at `~/tools/google-cloud-sdk/`, on PATH via `~/.bashrc`. User authed; ADC creds at `~/.config/gcloud/application_default_credentials.json`. **No GCP project created yet** — job-0014 creates the new dedicated project.
- **atlas CLI 1.55.0** installed at `~/tools/mongodb-atlas-cli_1.55.0_linux_x86_64/`, symlinked to `~/.local/bin/atlas`. User authed (user-account flow).
- **OpenTofu 1.12.1** installed at `~/tools/tofu_1.12.1/`, symlinked to `~/.local/bin/tofu`.
- **gh CLI** authenticated as `double-r-squared` (HTTPS, token in keyring).
- **No `grace2` conda env on this machine** — that env (QGIS 3.40.3-Bratislava) was Mac-local for PyQGIS worker dev. Recreate when worker code lands (not blocking M1).
- **AWS / Ollama / `llama3.2:3b`** — historically referenced in v0.2; **no longer relevant** under SRS v0.3 (Decision E — GCP only; FR-AS-1 — Gemini 3 only).

## Live cloud substrate (job-0014 landed)

**GCP project `grace-2-hazard-prod`** (number `425352658356`):
- Billing linked (account `01212A-92BE96-BB3841`)
- 12 APIs enabled (cloudresourcemanager, serviceusage, iam, iamcredentials, run, workflows, storage, aiplatform, secretmanager, artifactregistry, logging, monitoring)
- OpenTofu state bucket: `gs://grace-2-tfstate-grace-2-hazard-prod` (uniform BLA + PAP + versioning + 90d noncurrent lifecycle)
- Artifact bucket: `grace-2-hazard-prod-artifacts`
- Service account: `agent-runtime` with `roles/secretmanager.secretAccessor`
- Secret Manager: `projects/425352658356/secrets/mongodb-srv-dev` holds the SRV-with-credentials

**MongoDB Atlas Flex cluster `grace-2-dev`** (project `6a234700a0e1295958d10cf9` in org `6a234700a0e1295958d10c99`, cluster ID `6a234a45e40bf4c4a1177833`):
- GCP `CENTRAL_US`, MongoDB 8.0.24, disk 5 GB, backups enabled, state IDLE
- SRV: `mongodb+srv://grace-2-dev.tszeckl.mongodb.net`
- DB user `grace2-worker` (SCRAM, `readWrite` on `grace2_dev`, CLUSTER-scoped) — credentials in Secret Manager
- IP access list: dev `/32` only (no 0.0.0.0/0) — note: 7-day expiry deliberately not set, follow-up tracked
- Imported into OpenTofu state via `mongodbatlas_flex_cluster.dev`; `tofu plan` clean

**OpenTofu IaC** under `infra/`: backend.tf (GCS), providers.tf (google + mongodbatlas ~> 1.27 + random), gcp.tf, atlas.tf, secrets.tf, variables.tf, terraform.tfvars.example. `terraform.tfvars` gitignored.

**Programmatic Atlas API key flow:** GROUP_OWNER scope keys minted for import/apply and revoked after (least-privilege ritual documented in `infra/README.md`).

## Decisions log

| Date | Decision | Decided by | Rationale |
|------|----------|-----------|-----------|
| 2026-06-05 | SRS v0.3 pivot: web client, GCP/ADK/Gemini 3, QGIS Server, MongoDB Atlas+MCP, 2-layer tools, HEP with claims, discovery mode | user (SRS §2.1 Decisions A–M) | More tractable; no desktop install; AI as the GIS abstraction |
| 2026-06-05 | New dedicated GCP project; git init + GitHub remote + MIT license | user | Sprint-03 foundation choices |
| 2026-06-05 | **Atlas tier: Flex for pre-MVP (M1–M9), M10 by milestone M10** (supersedes earlier "Atlas free-tier M0") | user + orchestrator (post-research) | M0's 0.5 GB cap + UI-only vector-index management + auto-pause-after-30-days break FR-HEP-* (news embeddings, SRS NFR-P-6 vector search target). M2/M5 EOL 2026-01-22 (no intermediate tier). Flex ($8–30/mo) gives 5 GB + programmatic vector-index management; M10 (~$170/mo for 3-node replica set) reserved for first public users. **SRS NFR-C-1 says "M10 idle <$100/mo" — that line is numerically inaccurate; amendment to be proposed.** |
| 2026-06-05 | `research_mode` field on `user-message` is the FR-WC-15 toggle carrier (Appendix A amendment, schema proposes in job-0013) | orchestrator | Pins the web→agent→engine strategy path before anyone invents a second one |
| 2026-06-05 | OpenTofu (MPL-2.0) as the IaC tool, not BUSL Terraform | orchestrator + user | Terraform left homebrew-core after BUSL relicense; OpenTofu is drop-in; NFR-PO-3 says "or equivalent"; all-OSI tooling matches NFR-L posture |
| 2026-06-05 | Roster stays six; `plugin` → `web`; `engine` keeps the whole tool body incl. PyQGIS worker code | orchestrator | v0.3 surfaces map 1:1 onto existing roster; avoids fragmentation per user's standing guidance |
| 2026-06-05 | **Linux (Debian 13) is both dev AND prod substrate.** No macOS dev branch; Cloud Run runs Linux containers; container builds are `linux/amd64` only (no Apple Silicon multi-arch). Project-wide invariant — every kickoff inherits this | user | User switched from Mac to Debian dev box; production has always been Linux via Cloud Run. Eliminates an entire cross-platform branch. |
| 2026-06-05 | Repo layout: `web/` · `services/{agent,workers}/` · `packages/contracts/` · `infra/` · `styles/` · `tests/` · root scaffold (validated in job-0012 audit) | orchestrator + infra (job-0012) | TENTATIVE → fact; matches Tier-separation invariant taxonomy (services = deployable Cloud Run units) |
| 2026-06-04 | pydantic v2 for contracts | SRS-anchored (Appendix D) | Was tentative; now codified in the SRS itself |
| 2026-06-04 | Specialist roster consolidated to six | user + orchestrator | Avoid fragmenting work |
| 2026-06-04 | Sprint scaffolding stays lightweight until an SRS revision survives a sprint | orchestrator (retrospectives 01, 02) | Two pivots in two days; cheap-abort discipline validated |

## Known issues / debt

- SRS v0.3 §6 Open Questions 1–7 unresolved. Surfacing owners: OQ-1 agent (Cloud Run WS vs Agent Engine — needed before M2), OQ-2 infra (MCP hosting), OQ-3 engine (news API mix), OQ-4 engine (HydroMT depth), OQ-5 engine (forcing cache design, due M4/M5), OQ-6 engine (pre-baked demos), OQ-7 schema/infra (embedding dimension, verify before locking Atlas index).
- **SRS NFR-C-1 cost line ("M10 cluster idle <$100/month") is numerically inaccurate** — real M10 3-node replica set on GCP is ~$170/mo base + backup. Amendment proposal to be drafted by schema or infra and surfaced to user.
- ~~Pelicun impact postprocessor planned as future engine addition~~ → **LANDED in SRS v0.3.13 (2026-06-05)** as forward-looking architecture. Decision N + §2.3 post-processing tool-class + FR-CE-5/6/7 + FR-TA-1 `run_pelicun_impact` + Appendix B.6c `ImpactEnvelope` + B.6d Hurricane Ian example + Appendix D.3 `run_type` extension + FR-MP-5 row + Milestone M5.5 + OQ-8 (fragility sourcing). All additions explicitly marked **(Forward-looking — not in M1 / not in sprint-03; targeted post-M5)** so current in-flight work is undisturbed.
- **SRS v0.3.14 (2026-06-05): openTELEMAC-MASCARET added** as forward-looking multi-solver hydrodynamic engine. §2.3 Deferred engines row (Python-shim, target v0.3) + clarification distinguishing TELEMAC from OpenFOAM-class indefinitely-deferred set + FR-TA-1 forward-looking workflow group (`run_coastal_storm_surge_telemac`, `run_coupled_surge_wave`, `run_river_hydraulics_mascaret`, `run_sediment_transport_gaia`) + Milestone M11 + OQ-9 (mesh-generation toolchain). License posture: GPL/LGPL boundary at the Docker image; GRACE-2 stays MIT (out-of-process invocation, no source linkage).
- **Atlas cluster created out-of-band** (via UI, not OpenTofu) — job-0014 must `tofu import` rather than `tofu apply create`.
- `agents/web.md:89` contains the banned-vocabulary governance line (intentionally names the forbidden terms in order to forbid them) — sweep-grep AC2 will return this single hit; documented and accepted (job-0012 OQ-C).

## Next up

**Stage A finish, then Stage B:**

1. **job-0013 ✅ closed approved 2026-06-05** — contracts package installed and verified.
2. **job-0014 ✅ closed approved 2026-06-05** — `grace-2-hazard-prod` GCP project (425352658356) + 12 APIs + GCS OpenTofu state + Atlas Flex import + Secret Manager SRV + MCP smoke pass + OQ-7 gate qualified-pass (lock 768) + OQ-2 = Cloud Run sidecar. Commit `5c0ab56`.
3. **job-0015 ✅ closed approved 2026-06-05 [1 revision]** — `services/agent/` `grace2-agent` v0.1.0 runs Gemini 2.5-pro on Vertex AI (Gemini 3 returns 404 — single-constant flip path documented); Appendix-A WebSocket server via `grace2_contracts.ws`; MCP stdio sidecar with SRV from Secret Manager via ADC. Cancel-to-cancelled-pipeline in 502ms (vs 30s budget). OQ-1 = Cloud Run + WebSocket (`--use-http2 --session-affinity --min-instances=1`). NFR-P-1 (2s first-token) escalated — current 3-8s warm. Commits `0742c06`, `cc8b2a7`.
4. **job-0016 ✅ closed approved 2026-06-05 [1 revision]** — `web/` ships React 18 + Vite 5 + TS strict + MapLibre 4.7 with CONUS OSM basemap (Decision I camera-lock) and chat box streaming `agent-message-chunk` deltas. `make run-web` runs Vite dev; cross-browser headless screenshots verified on Chromium 148 + Firefox-ESR 140 (Safari deferred). Contracts hand-mirror M1 subset (codegen tigger at ~20 payloads). Disconnect→reconnect in ~4s. Commits `778fe6c`, `06d9d1a`.
5. **job-0017 ✅ closed approved 2026-06-05 [1 revision]** — `tests/` pytest harness + Makefile test target; 91 contracts + 23 acceptance = 114 tests green in ~36s; live_gemini PASSED 4.42s; live MCP 17.66s; sprint-03 exit-criteria 5 pass + 1 qualified (EC4 Gemini-3 substitution). Commits `c24b9b1`, `9815dcb`.

**Sprint-03 CLOSED 2026-06-05** — M1 (Foundation) achieved. See `reports/sprints/sprint-03.md` Retrospective. Next: sprint-04 (M2 — QGIS Server in cloud + PyQGIS worker prototype) planning pending user go on sprint-03 close package.
3. **job-0015 (agent ADK skeleton) ∥ job-0016 (web stub)** — parallel after 0014.
4. **job-0017 (acceptance suite)** — gates sprint-03 close.

**Awaiting user input:**
- Permission to launch job-0013 finish-and-verify (involves writing code).
- Confirmation of revised Atlas Flex decision and the SRS NFR-C-1 amendment-proposal path.
