# GRACE-2 — Third-Party Licenses

This document tracks third-party dependencies whose licenses warrant explicit
posture statements under SRS NFR-L (NFR-L-1 OSI-approved license at repo root;
NFR-L-2 all third-party dependencies tracked and license-compatible). The
repo's primary license is MIT. Most direct dependencies (Python ecosystem
packages, Google Cloud SDKs, FastAPI/websockets/pydantic/etc.) are
MIT/BSD/Apache-2.0 and do not require individual call-out here. Entries
below cover the cases where the license is copyleft or otherwise demands a
documented posture statement so the MIT posture of GRACE-2 code is preserved.

---

## hydromt-sfincs (GPLv3)

- **Package:** `hydromt-sfincs`
- **Source:** [https://github.com/Deltares/hydromt_sfincs](https://github.com/Deltares/hydromt_sfincs) (Deltares)
- **License:** GPLv3
- **Version pin:** `>= 1.1.0, < 2.0` (`services/agent/pyproject.toml`)
- **Where used:** imported in `services/agent/src/grace2_agent/workflows/sfincs_builder.py` via the `build_sfincs_model` workflow step (job-0042), which constructs a HydroMT `SfincsModel` from a programmatic YAML config and writes the SFINCS input deck (`sfincs.inp`, DEM `.dep`, Manning's `.man`, boundary forcing files) to GCS. The solver itself runs in a separate Cloud Run Job container (the SFINCS executable from `services/workers/sfincs/`); the agent service only authors the input deck.
- **Posture statement:** GPLv3 applies to the `hydromt-sfincs` Python package itself; it does not contaminate GRACE-2's MIT-licensed agent code. The agent imports the `hydromt_sfincs` Python module and calls its public API as a normal Python library; this is dynamic linking against a Python module distributed as data-driven scripts, not static linking against compiled GPL'd binaries. The SFINCS solver binary itself runs out-of-process in a separate Cloud Run Job (FR-CE-1 isolation), reinforcing the clean process boundary. Per OQ-4 §4 (`docs/decisions/oq-4-hydromt-depth.md`) and the kickoff's NFR-L assessment, importing the Python module in-process for deterministic deck assembly satisfies the GPL clause for "uses" without triggering the copyleft requirement for the calling code, because GRACE-2 does not redistribute the `hydromt-sfincs` package as part of a derivative work — it depends on it as an installed third-party library through the standard Python package boundary.
- **Distribution implications:** If GRACE-2 is ever distributed as a single bundled artifact that *includes* `hydromt-sfincs` source or compiled forms (e.g. a self-contained tarball that ships the dep), the GPLv3 obligations would attach to that bundle. The current deployment model — Cloud Run service that `pip install`s `hydromt-sfincs` from PyPI at container-build time — does not produce a redistributable bundle of `hydromt-sfincs`; the dep is installed at the user's (i.e. GRACE-2's GCP project's) own infrastructure boundary.

### Honest pin correction (job-0049)

OQ-4 §4 specified `hydromt-sfincs >= 1.1.2, < 2.0` plus `hydromt >= 1.0, < 2`.
Both constraints are not simultaneously satisfiable in the published PyPI
releases as of 2026-06-07:

- `hydromt-sfincs 1.1.2` does not exist on PyPI; the release sequence is
  `1.1.0 → 1.2.0 → 1.2.1 → 1.2.2 → 2.0.0rc{1,2,3}`.
- The stable `hydromt-sfincs 1.2.x` line transitively constrains
  `hydromt < 1`. `hydromt >= 1.0` is only compatible with the v2.0 RC line.

The working pin landed by job-0049 is `hydromt-sfincs >= 1.1.0, < 2.0` with
`hydromt` resolved transitively (currently to 0.10.1). When `hydromt-sfincs`
2.0 exits RC and stabilises on `hydromt >= 1.0`, both pins can move forward
together; until then, the OQ-4 §4 paper contract is documented as "intended
to mean the stable hydromt-sfincs v1.x line" rather than the literal
`>= 1.1.2` written in the decision doc. This document is the authoritative
record of the actual working pin.

---

## OSI license posture

The repository root carries `LICENSE` (MIT). GitHub's license detection
recognises this file. NFR-L-1 is satisfied. NFR-L-2 dependency tracking is
satisfied by this file (for copyleft-bearing deps) plus the dependency lists
in `services/agent/pyproject.toml`, `services/workers/*/pyproject.toml`, and
`web/package.json` (MIT/BSD/Apache-2.0 deps need no individual call-out).
