# SRS amendment PROPOSAL — QGIS Processing as the primary agentic compute substrate (Decision Q)

> **Status: PROPOSAL.** Per the SRS editing rule, specialists/orchestrator *propose*; only the **user lands** amendments into the narrow `docs/srs/*` files, then runs `make srs`. This doc gives paste-ready text + exact target files. Drafted by the orchestrator 2026-06-16 from the user's direction below. Nothing here is in the SRS until the user lands it.

**User direction (2026-06-16):** "we need QGIS Server and the compatible plugins were slotted to be tools the LLM could call to access other compute engines like HEC-HMS … this was the plan all along to utilize the built ecosystem around QGIS so we aren't hand-rolling tools that exist and are already documented and ready to use off the shelf as agentic tools." Plus: re-host QGIS on AWS first; first engine proof = HEC-HMS; engine selection criterion = headless+Linux (HEC-RAS deferred — Windows GUI).

**Version:** v0.3.22 → **v0.3.23**.

---

## 1. What changes (the posture shift)

Today the SRS treats QGIS-plugin/Processing invocation as **informational and equal-status with a Python-shim**, and Appendix E even leans toward "the cleanest integration is usually the underlying Python library called directly from a worker." That under-states the actual architecture intent. The amendment **elevates the QGIS Processing framework to the *primary* agentic compute surface**:

- The QGIS Processing catalog (GDAL/GRASS/SAGA/TauDEM + provider plugins, each with stable algorithm IDs, typed params, and existing docs) is the **default** way the agent reaches geospatial/hydrology compute — wrapped behind a generic discover→describe→run tool surface — **so we do not hand-roll tools that already exist off the shelf.**
- External compute engines (HEC-HMS, MODFLOW family, …) integrate as **headless solvers** with QGIS Processing doing GIS prep + result handling (the SFINCS/HydroMT shape).
- **QGIS Server is required** (rendering + OGC parity); **TiTiler is the scoped fast path for plain COG rasters**, not a replacement.
- New **engine-selection gate: cloud-deployability (headless + Linux)** ranks ahead of modeling power for sequencing. Python-shim becomes the *exception* (used when no Processing algorithm exists or license/runtime isolation requires it), not the default.

This does **not** change the data-fetch tools or the data endpoints they consume (OGC/ArcGIS/STAC) — those are independent of the rendering/compute substrate.

---

## 2. Section-by-section proposed edits

### 2.1 — `docs/srs/02-system-overview.md` §2.1 Decisions — ADD Decision Q

Append after Decision P-adjacent material (Decisions A–P are in use; Q is next):

> **Decision Q — QGIS Processing is the primary agentic compute substrate (v0.3.23).**
> The QGIS Processing framework (native QGIS + GDAL/GRASS/SAGA/TauDEM providers and curated provider plugins) is the **default** surface through which the agent reaches geospatial and hazard-modeling compute. Algorithms are exposed to the LLM behind a generic **discover → describe → run** tool surface (`list_qgis_algorithms` / `describe_qgis_algorithm` / `qgis_process`), sourcing typed parameters + documentation from each algorithm's own metadata. Rationale: the ecosystem is mature, documented, and battle-tested — enabling a provider yields dozens of ready agentic tools without hand-rolling bespoke wrappers (Invariant: don't reinvent documented, off-the-shelf tools). **Hand-rolled (Python-shim) atomic tools become the exception**, justified only when (a) no Processing algorithm covers the need, (b) license/runtime isolation requires an out-of-process container (NFR-L), or (c) a tight result contract (e.g. envelope-shaped output) is cleaner authored directly. External compute engines integrate as **headless solvers** (FR-CE handle contract) with QGIS Processing performing GIS pre-/post-processing — the SFINCS/HydroMT pattern generalized. **QGIS Server is a required component** for rendering + OGC parity (styled WMS/WFS, server-rendered vectors, layouts); a dynamic COG tiler (TiTiler) MAY serve plain single-band raster overlays as a lightweight fast path but does not replace QGIS Server. This Decision supersedes the equal-status framing of Appendix E.

### 2.2 — `docs/srs/02-system-overview.md` §2.3 Engine selection principle — ADD the cloud-deployability gate

Append a paragraph to §2.3:

> **Engine cloud-deployability gate (v0.3.23).** When sequencing which deferred engines to land, **cloud-deployability (headless + Linux, scriptable/CLI compute) ranks ahead of modeling sophistication.** Engines that run headless on Linux containers (HEC-HMS — Jython/CLI; the MODFLOW family — `mf6`; SFINCS) are landed first via the §E three-tier bake. Engines whose compute is Windows-native and/or GUI-driven (notably **HEC-RAS** — RAS Mapper / Windows controller) are **deferred** until a Windows-container or headless-controller path is justified; their QGIS plugins (RiverGIS geometry, Crayfish result rendering) remain useful for desktop pre/post but do not make the compute cloud-friendly. First substrate proof engine: **HEC-HMS** (public-domain, headless, Linux).

### 2.3 — `docs/srs/03-functional-requirements.md` §3.2 FR-AS — ADD a requirement for the generic Processing surface

Add (next free FR-AS-N anchor):

> **FR-AS-N — QGIS Processing tool surface (primary compute discovery).** The agent SHALL reach QGIS Processing algorithms through a generic discover→describe→run surface rather than per-algorithm hand-written tools: `list_qgis_algorithms` (semantic discovery over the live provider catalog, filtered to a curated allowlist of relevant categories), `describe_qgis_algorithm` (typed parameters + help sourced from algorithm metadata), and `qgis_process`/`run_qgis_algorithm` (execution on the headless Processing worker, returning a `LayerURI`/`ExecutionHandle` per FR-CE). New geospatial compute capabilities SHALL prefer an existing Processing algorithm over a new bespoke atomic tool (Decision Q); a bespoke tool requires a one-line justification (no covering algorithm / license isolation / result-contract clarity). The curated allowlist keeps the LLM-visible catalog tractable (categories, not the full provider dump).

### 2.4 — `docs/srs/03-functional-requirements.md` FR-CE — NOTE headless-solver generalization

Add a sentence to the FR-CE intro (compute execution): the `(deck → solver → results)` handle contract (FR-CE-1/2/3) covers **headless external engines (SFINCS, MODFLOW, HEC-HMS)** uniformly; QGIS Processing supplies the deck-building (GIS prep) and result post-processing steps around the solver call.

### 2.5 — `docs/srs/E-qgis-plugins-inventory.md` — REVISE the posture (intro + E.5)

Replace the equal-status framing. In the **Purpose** paragraph, change the sentence beginning "It is informational… in most cases the cleanest GRACE-2 integration is the underlying Python library…" to:

> Per **Decision Q (§2.1)**, QGIS Processing is the **primary** agentic compute surface: the default integration is to reach an algorithm through the generic Processing tool surface (`qgis_process`), and `adapt-via-python` / `out-of-process` are the **exceptions** (chosen for license/runtime isolation or where no Processing algorithm exists). The integration-mode hints below rank those exceptions; they no longer imply Python-shim is the default.

In **E.5 "What this appendix does NOT do"**, soften "It does not bind the architecture to invoke any plugin" to acknowledge Decision Q: the *generic Processing surface* IS now the bound default; the per-plugin mode hints choose among exceptions. Also update **E.4** bake-tier references from GCP "Cloud Run Jobs / Cloud Workflows" to the AWS container reality (headless Processing worker container; dedicated solver containers on EC2/ECS), since sprint-14-aws moved compute off GCP.

The **QGIS-HMS row in E.1 stays as-is** — it already prescribes the correct path (terrain-prep via `qgis_process` native algorithms → `run_hec_hms_simulation` invoking the HEC-HMS CLI in a container). Only the GCP "Cloud Run Job" phrase updates to "headless solver container (AWS)".

### 2.6 — `docs/srs/07-milestones.md` — ADD milestone M12

> **M12 — QGIS compute substrate on AWS + first headless-engine proof (HEC-HMS).** QGIS Server + headless Processing worker re-hosted on AWS; generic Processing tool surface live as the primary compute discovery path; HEC-HMS landed as a headless solver via QGIS-prep → solve → render; deprecation pass folds overlapping hand-rolled tools into Processing algorithms. Depends on M5 (SFINCS/solver substrate) + the sprint-14-aws AWS stack. Sequenced by the cloud-deployability gate (§2.3).

### 2.7 — `docs/srs/08-document-history.md` — ADD row

> | v0.3.23 | 2026-06-?? | Decision Q (QGIS Processing as primary agentic compute substrate); §2.3 engine cloud-deployability gate (HEC-HMS first, HEC-RAS deferred); FR-AS-N generic Processing tool surface; FR-CE headless-solver generalization; Appendix E posture revision + AWS bake-tier update; M12. |

### 2.8 — `docs/srs/00-preamble.md` — bump version string to **v0.3.23**.

---

## 3. What this amendment does NOT change

- **Data endpoints + fetch tools** (MRLC WCS, 3DEP, NWS, ArcGIS REST, STAC, …) — unchanged; independent of the compute/render substrate.
- **TiTiler** — retained for plain COG raster overlays (the fast path); not removed.
- **Invariants 1/5/7/9** — unchanged; the Processing surface runs under the same determinism/sandbox/no-silent-wrong-answer/confirmation rules.
- **HEC-RAS / Windows-native engines** — explicitly out; this is a deferral note, not a commitment.

## 4. After landing

Run `make srs`; CI `make srs && git diff --exit-code docs/SRS_v0.3.md` should pass. The companion execution plan is `reports/sprints/sprint-16-qgis-substrate-manifest.md` (jobs 0308–0313), which cites Decision Q + FR-AS-N + M12.
