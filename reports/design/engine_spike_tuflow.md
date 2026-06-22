# Engine Spike: TUFLOW (premium 2D hydraulics) -> GRACE-2 Batch seam

Status: research spike (task #173). Author: engine specialist (orchestrator-dispatched).
Date: 2026-06-22. Primary-source grounded (TUFLOW official docs + wiki + whitepaper).

## TL;DR verdict: NO-GO

TUFLOW is technically runnable headless on Linux (2025 ships native Linux
command-line binaries), so the *compute* gate is passable. But the **licensing
model is structurally incompatible with every load-bearing invariant of the
GRACE-2 execution architecture**, and it is so by design, not by oversight.
The blockers are not "hard to do" -- they are "the vendor contract forbids the
exact shape of our system." Specifically:

- WIBU CodeMeter **node-locked** software licenses bind to a *specific host* on
  first import; ephemeral / Spot / auto-scaling Batch instances cannot consume a
  node-locked container. (TUFLOW: "a software-based dongle will be bound to it
  when it is first imported. If over time you decide you want to move to another
  computer, we will need to re-issue you with a new software licence.")
- Cloud execution **mandates a Network license** ("use of 'Local' licences is
  not permitted on the cloud") served by a **persistently-running CodeMeter
  license server** the VM must reach over the network at all times -- i.e. an
  always-on, stateful, network-reachable license daemon. That directly violates
  the scale-to-zero island architecture.
- The contract **prohibits multi-tenant resale**: "companies cannot on-sell
  access to TUFLOW licences hosted in the cloud or otherwise" and usage "is
  confined to Authorised Users within the Licensee's Network." GRACE-2 is a
  hosted workbench serving arbitrary end users -- that is exactly the on-sold
  access the EULA forbids.
- The HPC performance story is **GPU** (10-100x vs Classic single-core); the GPU
  Module is a paid add-on at +50% of the engine price, and the agent fleet is
  x86 CPU Spot. On CPU, HPC is *slower than Classic*.

None of this is a wrapper-engineering problem. Even if we wrote a perfect worker
image, we would be running unlicensed-by-EULA on every Spot job, or we would
have to bolt an always-on commercial license server onto the system and accept
that we cannot lawfully expose it to our users. **Recommendation: do not
integrate. Use the open engines we already have (SFINCS / GeoClaw) plus the
already-shelved-then-revived HEC-RAS public-domain Linux binaries if regulatory
riverine hydraulics is ever required.** TUFLOW belongs in the D-tier
(commercial / GUI-and-license-gated) bucket of the engine-drivability ranking,
alongside FEFLOW and FLO-2D.

---

## 1. What TUFLOW is, and where it would sit

TUFLOW (BMT, commercial) is the dominant *commercial* 2D/1D-2D hydraulic
modelling suite in the flood-engineering industry (esp. AU/UK/regulatory work).
Product family relevant here:

- **TUFLOW Classic** -- implicit fixed-grid 2D solver (CPU).
- **TUFLOW HPC** -- explicit, heavily-parallelised 2D solver. Runs on CPU
  (`CPU Threads == N`) OR on GPU via the GPU Module. Supports the **Quadtree**
  module (variable cell size -- the same nested-resolution idea SFINCS gives us
  for free). HPC + Quadtree is the modern flagship.
- **TUFLOW FV** -- finite-volume *unstructured* mesh solver (flexible mesh;
  the unstructured-mesh competitor to TELEMAC / SFINCS-quadtree).

Hazard-class fit: TUFLOW overlaps almost entirely with capability we **already
have** in open engines:

| Use case                         | TUFLOW          | GRACE-2 already has                |
|----------------------------------|-----------------|------------------------------------|
| Urban / overland 2D flood        | HPC / Classic   | **PySWMM** (quasi-2D), **SFINCS**  |
| Coastal / compound flood         | HPC + FV        | **SFINCS** (quadtree + SnapWave)   |
| Riverine / 1D-2D channel         | Classic 1D-2D   | (gap; HEC-RAS shelved is the fit)  |
| Dam-break / shallow-water        | HPC             | **GeoClaw** (BSD)                  |
| Unstructured flexible mesh       | FV              | (gap; **TELEMAC** is the open fit) |

The single genuine gap TUFLOW would fill that an open engine does not -- FEMA /
regulatory-grade riverine 1D-2D with structures (bridges/culverts/gates) -- is
**already assigned to HEC-RAS** in the canonical engine backlog (HEC-RAS ships
US-public-domain Linux compute binaries; see
`project_hecras_engine_research`). So TUFLOW does not even uniquely fill the one
hole it could justify its license cost on.

Net: TUFLOW is a *premium substitute* for engines we run for free, not a
*new hazard class*. That alone makes the license gate fail a cost-benefit test
before we get to the legal/architectural blockers.

---

## 2. The hard gate: can it run HEADLESS on Linux on a Batch container?

**Compute: YES (with caveats). License: effectively NO.**

### 2.1 Headless Linux compute -- PASSES the binary-availability bar
TUFLOW 2025 ships **native Linux command-line builds** of Classic/HPC and FV.
You launch a sim from the shell against a `.tcf`/`.fvc` control file; logs go to
`.log`/`.tlf`. No GUI is required to *run* a solve (the GUI/QGIS-plugin pre/post
authoring is separate, like RAS Mapper vs the RAS compute binaries). So the
"no GUI, no Windows-only binary" half of the GRACE-2 engine gate is met.

### 2.2 The licensing wall -- FAILS, and fails by design
The compute binary is useless without a valid WIBU CodeMeter license. The
mechanics (all confirmed from TUFLOW's own docs/wiki):

- **Node-locked software container.** A software license is a WIBU container
  (`.WibuCmLif`) that **binds to the host machine on first import**. You generate
  a request file (`.WibuCmRaC`), email it to TUFLOW sales, and they return an
  activation file (`.WibuCmRaU`). Moving to a different host = TUFLOW must
  **re-issue** the license (manual, fee-bearing). An AWS Batch Spot instance is a
  *fresh, throwaway host every job* -> a node-locked container cannot be used.
- **CodeMeter daemon must run continuously.** The image must run the CodeMeter
  service (`systemctl status codemeter.service`; WebAdmin on 22352/22353).
  That is an always-on, stateful sidecar -- the opposite of a one-shot solver
  container.
- **Cloud requires a Network license + a reachable license server.** TUFLOW's
  own cloud-execution guidance: "the VMs will always need to have CodeMeter
  installed, configured to find the licence you plan to use", and on the cloud
  "use of 'Local' licences is not permitted" -- you must run a **Network**
  license, served by a CodeMeter license **server** that the compute VM reaches
  over the network for the entire duration of the solve. So the architecture
  TUFLOW supports is: [always-on license server VM] <- network -> [compute VM].
  An ephemeral Spot job that loses license-server reachability mid-solve dies.
- **The EULA forbids the thing we are.** "companies cannot on-sell access to
  TUFLOW licences hosted in the cloud or otherwise (excluding TUFLOW vendor
  contract arrangements)"; usage "is confined to Authorised Users within the
  Licensee's Network." GRACE-2 is a hosted workbench whose whole purpose is to
  give arbitrary end users (not "authorised users within our network") access to
  run models. Exposing TUFLOW solves to GRACE-2 users IS on-selling access. This
  is a legal blocker, not an engineering one.

### 2.3 Performance shape -- mismatched even if licensed
- HPC's headline 10-100x speedup is **GPU**. The GRACE-2 Batch fleet is x86 CPU
  Spot (c7i/m7i). On CPU, TUFLOW HPC is *slower than Classic*.
- The **GPU Module is a paid add-on at +50% of the engine license price**, and
  would force a GPU compute environment (g-family instances) into our otherwise
  CPU-only, cost-disciplined fleet -- contradicting the cost-driver remediation
  work (`project_aws_cost_drivers_and_fixes`).

---

## 3. Where it fits vs SFINCS / GeoClaw -- and is it worth the gate?

**It is not worth it.** The role TUFLOW would play is already covered:

- **vs SFINCS:** SFINCS gives us coastal/compound flood with a quadtree
  (variable resolution) + SnapWave, GPL but Docker-runnable, BMI-exposed, $0
  license. TUFLOW HPC + Quadtree is the *commercial* equivalent of exactly this.
  We would be paying (annual maintenance + per-instance + GPU module) for a
  capability we already ship at zero license cost.
- **vs GeoClaw:** GeoClaw (BSD, pip) covers dam-break / shallow-water /
  tsunami / surge run-up -- TUFLOW HPC's dam-break niche -- with no license and a
  clean compile-at-runtime Batch worker already built
  (`services/workers/geoclaw/`).
- **vs the genuine gap (regulatory riverine 1D-2D w/ structures):** that gap is
  assigned to **HEC-RAS** (US public-domain Linux binaries, redistributable,
  bake-into-ECR clean -- the polar opposite of TUFLOW's license posture). HEC-RAS
  also carries the FEMA-acceptance credibility TUFLOW would be bought for.

The only thing TUFLOW would add over the open stack is *brand recognition with
regulators* in some jurisdictions (AU/UK). That is a marketing argument, not an
engineering one, and it does not survive the EULA's no-resale clause: we could
not lawfully offer it to users anyway.

**Decision input for the backlog:** classify TUFLOW **D-tier** (commercial /
license-and-GPU-gated) in `reference_engine_cloud_ai_drivability_ranking`,
alongside FEFLOW (-> use MODFLOW) and FLO-2D. The agent-best substitutes already
in or planned for the stack are SFINCS (compound/coastal), GeoClaw (dam-break),
HEC-RAS (regulatory riverine), TELEMAC (open unstructured mesh).

---

## 4. Minimal integration sketch -- IF it were ever viable

Recorded only so a future re-evaluation (e.g. a customer who *owns* TUFLOW
licenses and wants to bring their own) has the shape. **Do not build this
speculatively.** The seam is engine-agnostic, so the *code* cost is small; the
cost is entirely the license posture. If a license-holding customer scenario
ever arises (the ONLY viable path), it would be a *bring-your-own-license*,
single-tenant, dedicated-instance integration -- never the shared Spot fleet.

The seam mirrors the GeoClaw/SFINCS pattern exactly:

1. **Worker image** `services/workers/tuflow/` (Dockerfile + entrypoint.py):
   - Base: a Linux image with the TUFLOW 2025 Linux binaries + CodeMeter runtime.
     (Both are licensed assets -- cannot be baked into a public/redistributable
     ECR image. This alone breaks the bake-into-ECR hygiene norm; a BYOL customer
     would supply them.)
   - Entrypoint: the same object-store-IN -> run -> object-store-OUT envelope as
     `services/workers/geoclaw/entrypoint.py` -- stage `inputs[]` from
     `s3://...-cache`, author the `.tcf` control file from a `build_spec`
     (deterministic, library-free, like `setrun_builder.build_geoclaw_deck`),
     run the TUFLOW CLI, glob outputs (`.xmdf`/`.dat`/`.tif` result grids),
     upload to `s3://...-runs/<run_id>/`, write the byte-identical
     `completion.json` schema (`tuflow_stdout_uri`/`tuflow_stderr_uri`/...).
   - Licensing: the container would need CodeMeter pointed at a reachable
     **Network license server** -- i.e. an always-on license-server box outside
     the scale-to-zero island, reachable from the (necessarily non-ephemeral,
     non-Spot, single-tenant) compute instance. This is the part that cannot be
     made to fit the architecture.

2. **Agent registration** (1-line each, the proven MODFLOW/GeoClaw path):
   - `SOLVER_WORKFLOW_REGISTRY["tuflow"] = "model_flood_tuflow_scenario"` in
     `services/agent/src/grace2_agent/tools/solver.py`.
   - A per-solver Batch job-def resolved by the existing `_resolve_batch_job_def`
     (`GRACE2_AWS_BATCH_JOB_DEF_TUFLOW`), kept INERT until provisioned -- exactly
     how SWMM/deck-builder stay inert.
   - For local-exec, a `LocalSolverSpec` (`exec_kind="exec"`) mirroring the
     MODFLOW spec (direct-binary launch + `os.killpg` cancel).

3. **Postprocessor**: a `postprocess_tuflow` that rasterizes the TUFLOW result
   grids -> depth COG, reusing the existing publish_layer / TiTiler path. TUFLOW
   `.xmdf`/`.dat` mesh results are readable by QGIS MDAL / Crayfish (the same
   engine-agnostic mesh-result path noted for HEC-RAS HDF + SFINCS NetCDF), so
   the per-frame rasterize -> COG -> scrubber animation is reusable.

The code is ~1 worker + 1 entrypoint + 1 job-def + 1 registry line + 1
postprocessor -- genuinely cheap. **The architecture and the license contract,
not the code, are the blockers.**

---

## 5. Blockers (load-bearing, in priority order)

1. **EULA prohibits multi-tenant / on-sold access.** A hosted workbench serving
   arbitrary users IS on-selling access, which the TUFLOW cloud license terms
   forbid. This is fatal and legal, not technical.
2. **Node-locked license cannot ride ephemeral Spot.** Software containers bind
   to a host on first import; re-issue is manual + fee-bearing. Incompatible with
   scale-to-zero, per-job-fresh-instance Batch.
3. **Mandatory always-on Network license server.** Cloud use requires a Network
   license served by a persistently-reachable CodeMeter daemon -- a stateful,
   always-on dependency that violates the scale-to-zero island architecture.
4. **Cost.** Annual maintenance + engine licenses + GPU Module (+50%) for
   capability we already ship for $0 (SFINCS/GeoClaw). Fails the
   cost-discipline + decommission-predecessors norms.
5. **GPU-shaped performance.** The 10-100x speed story is GPU-only; CPU HPC is
   slower than Classic. Would force g-family instances into a CPU-only fleet.
6. **Licensed binaries cannot be baked into ECR.** TUFLOW binaries + CodeMeter
   are licensed assets; cannot go into a redistributable image, breaking the
   container-hygiene + reproducible-image norms.

## 6. Recommendation
Mark TUFLOW **D-tier, NO-GO** in the engine backlog. Do not integrate. Cover its
use cases with the open stack: **SFINCS** (compound/coastal, quadtree), **GeoClaw**
(dam-break/shallow-water), **HEC-RAS** (regulatory riverine, public-domain Linux),
**TELEMAC** (open unstructured mesh -- the right next mesh engine, task #174).
Re-evaluate ONLY if a license-holding customer requests a single-tenant,
bring-your-own-license, dedicated-instance deployment -- which is outside the
current product's shared-fleet architecture.

---

## Sources (primary)
- TUFLOW Classic/HPC User Manual 2025.2 -- Running Simulations, Hardware/OS,
  HPC (incl. Quadtree): https://docs.tuflow.com/classic-hpc/manual/2025.2/RunningSims-2.html ,
  https://docs.tuflow.com/classic-hpc/manual/2025.2/TUFLOWHPC-2.html
- TUFLOW wiki -- WIBU Licence for Linux (node-lock + CodeMeter daemon):
  https://wiki.tuflow.com/WIBU_Licence_for_Linux
- TUFLOW wiki -- Organisation Cloud Software Execution (Network-only on cloud,
  reachable license server, no on-selling):
  https://wiki.tuflow.com/Organisation_Cloud_Software_Execution
- TUFLOW FV 2025.0.0 Release Notes -- Licensing/Installation:
  https://docs.tuflow.com/fv/release/2025.0.0/LicensingInstallation-1.html
- TUFLOW Pricing (GPU Module +50%, license types):
  https://www.tuflow.com/pricing/
- "Running TUFLOW on the Cloud" (Van der Velde et al., TUFLOW whitepaper, 2021):
  https://www.tuflow.com/media/6619/2021-running-tuflow-on-the-cloud-van-der-velde-et-al-tuflow-whitepaper.pdf
- Internal: `reference_engine_cloud_ai_drivability_ranking`,
  `project_hecras_engine_research`, `services/workers/geoclaw/`,
  `services/agent/src/grace2_agent/tools/solver.py`.
