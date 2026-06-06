# Project State

**Last updated:** 2026-06-06 (sprint-05 / M3 — jobs 0025, 0027 approved; 0029 CORS fix in flight)
**Current sprint:** sprint-05 (active) — M3 Web client skeleton. Stage A ✅: job-0025 (QGIS Server WMS basemap + LayerPanel drag-and-drop) + job-0027 (Playwright + AFK loop operational). NEW mid-sprint: job-0029 infra (CORS fix on QGIS Server — blocks tile rendering until landed). Stage B queued: job-0026 (PipelineStrip onto 0025's App.tsx shell). Stage C queued: job-0028 (M3 acceptance). LayerPanel + interaction surface verified live via Playwright; basemap tiles pending CORS fix.

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

- `docs/srs/*.md` — **SRS v0.3.14+** (canonical, section-addressed; see `docs/srs/INDEX.md`); `docs/SRS_v0.3.md` is the regenerated monolith preserved for `reports/complete/` line-reference compatibility — the authority for scope. Product: a web-based AI workbench for multi-hazard modeling — React/MapLibre client, Google ADK + Gemini 3 agent on Cloud Run, QGIS Server rendering `.qgs` via WMS/WMTS/WFS, PyQGIS workers (Cloud Run Jobs), MongoDB Atlas + MCP, Cloud Workflows + GCS, SFINCS flood engine, Hazard Event Pipeline with ClaimSet provenance, Public Hazard Catalog discovery mode. Appendices A–D are preemptive contract specs; amendments flow back to the user.
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

**OpenTofu IaC** under `infra/`: backend.tf (GCS), providers.tf (google + mongodbatlas ~> 1.27 + random), gcp.tf, atlas.tf, secrets.tf, variables.tf, terraform.tfvars.example, plus job-0018 additions buckets.tf, pubsub.tf, qgis-server.tf, qgis-server/{Dockerfile,cloudbuild.yaml}. `terraform.tfvars` gitignored.

**QGIS Server Cloud Run service `grace-2-qgis-server`** (job-0018, sprint-04):
- URL: `https://grace-2-qgis-server-425352658356.us-central1.run.app`
- Region: us-central1; min-instances=0 (scale-to-zero per NFR-C-2)
- Image: digest-pinned `@sha256:7d8a338…` in `infra/qgis-server.tf` (FROM `qgis/qgis-server` 3.40 LTR base, `qgis_process` CLI baked in)
- Service account `qgis-server-runtime` scoped to `roles/storage.objectViewer` at bucket level only (zero project-level roles)
- Live: `GetCapabilities` returns valid `<ServerException>` XML (FCGI alive, awaiting MAP=)

**GCS buckets** (job-0018, sprint-04, all UBLA + PAP-enforced + 90-day noncurrent lifecycle):
- `grace-2-hazard-prod-qgs` — canonical `.qgs` storage (M2 sample lands in job-0019)
- `grace-2-hazard-prod-cog` — raster outputs (COG)
- `grace-2-hazard-prod-fgb` — vector outputs (FlatGeobuf)

**Pub/Sub topic `grace-2-worker-events`** (job-0018) — FR-QS-6 step 5 substrate for worker completion notifications; provisioned but unconsumed in sprint-04 (subscriber wiring deferred to M3/M4 when agent integrates).

**Artifact Registry repo `grace-2-containers`** (job-0018, us-central1) — holds the QGIS Server image and will hold the PyQGIS worker image (job-0021).

**Local grace2 conda env** (job-0022) at `~/miniforge3/envs/grace2`: QGIS 3.40.3-Bratislava + Python 3.12.13 + GDAL 3.10.2 + google-cloud-storage 3.11.0 + google-cloud-pubsub 2.38.0 + pytest 9.0.3. Reproducible from `infra/conda/environment.yml`. Local PyQGIS worker dev only; production worker is the job-0021 container.

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
- **SRS v0.3.15 amendment DRAFTED 2026-06-06 — NOT APPLIED.** Workflow `wodu3a4xm` synthesized 9 edits across Decision O (cache-mediated data fetching, 4 TTL classes, FR-DC-1..N), Decision P (multi-agent specialization migration path), 9–10 new Deferred engines rows (HEC-HMS, SWMM, pysheds, pywatershed, ParFlow, Delft-FEWS, wrfxpy, OpenWFM, QUIC-Fire+FastFuels+pyretechnics, PyTorchFire), conservation/biodiversity post-processing sub-catalog (Maxent, inVEST, Circuitscape, ConservationImpactEnvelope), and new Appendix E (QGIS plugins inventory). Adversarial verify REFUTED with 11 issues — most critical: new §3.9 caching body truncated mid-sentence (FR-DC-3..6 missing); OQ-5 not actually closed despite Decision O claim; OQ-10/OQ-11 referenced but not added to §6; no §8 Document History row for v0.3.15; pysheds/wrfxpy mis-categorized as engines vs atomic tools; bucket-naming inconsistency with Appendix B; Appendix E referenced without creating edit; conservation paragraph pre-commits despite OQ-11 being open; FR-AS-3 misattribution; ~30-tool heuristic unsourced. **Per user direction 2026-06-06, Decision P (multi-agent specialization) is dropped from this amendment** — defer the migration-path question to v0.2+ when single-agent topology actually hinders. Amendment carries forward; relaunch with the 11 verdict fixes + Decision P removal once next research/research pass completes.
- **Atlas cluster created out-of-band** (via UI, not OpenTofu) — job-0014 must `tofu import` rather than `tofu apply create`.
- `agents/web.md:89` contains the banned-vocabulary governance line (intentionally names the forbidden terms in order to forbid them) — sweep-grep AC2 will return this single hit; documented and accepted (job-0012 OQ-C).
- **Outstanding SRS amendment pile** (carry-forward; user lands at convenience): A1–A5 from job-0013 (Appendix amendments), NFR-C-1 cost line, NFR-P-1 first-token budget, FR-AS-1 Gemini-3 substitution, OQ-1 (Cloud Run + WS resolved by job-0015), FR-QS-2 `/mnt/qgs/` contract change, gitignore Lever A/B/C identifier exposures, OQ-W-26-PIPELINE-STEP-FIELDS (Appendix D.6 needs `progress_percent`/`error_code`/`error_message` — BLOCKS sprint-06 M4 real pipeline-state emission).

## Next up

**Sprint-05 CLOSED 2026-06-06** — M3 (Web client skeleton) achieved. 5 jobs approved (job-0025 basemap pivot + LayerPanel + App shell; job-0026 PipelineStrip + cancel button; job-0027 Playwright tooling + AFK loop; job-0028 M3 acceptance suite; mid-sprint job-0029 CORS fix). NFR-P-3 qualified at p50≈300ms / p95≈360ms (~7× margin under 2000ms target). Cross-browser visual smokes + cancel-chain end-to-end + Tier separation all verified live on deployed substrate. See `reports/sprints/sprint-05.md` Retrospective.

**Sprint-06 (M4 — agent service tools + atomic-tool starter set) — PLANNING.** Proposed scope (per user direction 2026-06-06 "small catalog to start with"):

1. **Tool registry skeleton** (`services/agent/tools/__init__.py`) — ADK FunctionTool registration boilerplate that downstream atomic tools plug into.
2. **`fetch_dem`** — USGS 3DEP / SRTM via py3dep or rasterio+GCS; returns LayerURI to a COG in the cache bucket.
3. **`fetch_buildings`** — Microsoft Building Footprints (MS Open Maps) via FlatGeobuf bbox query; returns LayerURI.
4. **`fetch_population`** — WorldPop or US Census via API; returns LayerURI to a tabular layer.
5. **`geocode_location`** — Mapbox / OpenStreetMap Nominatim REST; returns bbox + canonical name.
6. **`list_qgis_algorithms`** — wraps `qgis_process list` over the deployed PyQGIS worker; returns the discovered Layer-1 plugin catalog.
7. **`describe_qgis_algorithm`** — wraps `qgis_process help <alg>`; returns the algorithm's input/output schema.
8. **`mongo_query` / `qgis_process` registry** — pass-through atomic tools for the schema/plugin discovery surfaces.

**Demo target:** "what's the population of Fort Myers below 3m elevation?" → geocode_location → fetch_dem(bbox) → fetch_population(bbox) → qgis_process(`native:reclassifybytable` <3m mask) → qgis_process(`native:zonalstatistics` mask × population) → ImpactEnvelope returned.

**Prerequisites that BLOCK sprint-06 (M4):**
- **OQ-W-26-PIPELINE-STEP-FIELDS** — Appendix D.6 `PipelineStepSummary` needs `progress_percent` / `error_code` / `error_message` before M4 emits real `pipeline-state` envelopes. Route to schema; resolve before sprint-06 starts.
- **SRS v0.3.15 amendment (data endpoints + caching + engines + plugins + conservation)** — Decision O caching architecture is the substrate the atomic-tool fetchers depend on. If the amendment doesn't land before M4, the M4 fetchers will pick TTL classes and bucket layouts ad-hoc and the SRS lags. Relaunch with the 11 verdict fixes + Decision P dropped.
- **OQ-1 / FR-AS-1 / FR-QS-2 / NFR-C-1 / NFR-P-1 / gitignore Lever A/B/C** outstanding amendment pile — user lands at convenience.

**Held research workflows** (deferred while sprint-05 ran; relaunch after sprint-06 opens):
- Tool architecture deep-dive (plugins + tool refinement + breadth)
- Physics solvers + data sources deep-dive (forward-look — SFINCS, TELEMAC daemons, etc.)

**Awaiting user input:**
- **Sprint-05 close sign-off.** M3 milestone achieved; ready to open sprint-06 (M4 atomic-tool starter set) per the orchestrator-driven sprint loop.
- **SRS v0.3.15 amendment posture.** Either (a) authorize relaunch with verdict fixes + Decision P stripped, or (b) hand-author by user, or (c) defer until M4 surfaces hard requirements that force resolution.
- **OQ-W-26-PIPELINE-STEP-FIELDS routing.** Confirm schema-extension of Appendix D.6 with the three optional fields before sprint-06 emits real pipeline-state.
