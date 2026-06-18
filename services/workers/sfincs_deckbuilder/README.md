# SFINCS deck-builder worker (`sfincs_deckbuilder`)

GPL-isolated AWS Batch worker that authors a **multi-level refined SFINCS
quadtree + SnapWave deck** from scratch via Deltares `cht_sfincs` (GPL-3.0), then
writes the deck + a `run_solver`-compatible `manifest.json` back to the object
store. This is the coastal North Star gate: `hydromt-sfincs` can only read/write
one quadtree; only `cht_sfincs` can *build* a refined multi-level connectivity
table (`mu1/mu2/nu1/nu2/md1/md2/nd1/nd2` + level flags).

## Why a separate worker (the GPL boundary)

`cht_sfincs` is **GPL-3.0**. It lives ONLY in this image and is imported ONLY by
`entrypoint.py` (lazily, inside `build_deck`). The GRACE-2 **agent venv and all
agent code never import `cht_sfincs`** — the agent reaches this worker
arms-length over the object-store + AWS-Batch-submit seam (same pattern as the
MIT-licensed solve worker `services/workers/sfincs/`). This is enforced by
`services/agent/tests/test_sfincs_deckbuild.py::test_agent_code_does_not_import_cht_sfincs`.

## I/O contract

- **Input**: `--build-spec-uri s3://.../build_spec.json` (composed by the agent's
  `model_flood_scenario._compose_and_upload_deckbuild_spec`). Carries the AOI
  bbox/EPSG, topobathy COG URI, base-grid params, mask + SnapWave windows,
  refinement/boundary polygons, surge/wave/discharge forcing, and the output
  deck-dir + manifest URIs.
- **Output**:
  1. the deck dir (`sfincs.nc` quadtree netcdf, `sfincs.inp`, `snapwave.*`,
     optional `sfincs.bnd/bzs`, `sfincs.src/dis`);
  2. `manifest.json` — **byte-compatible** with what `build_sfincs_model` emits,
     so the existing `run_solver("sfincs", model_setup_uri=<manifest_uri>)` solve
     path consumes it unchanged;
  3. `completion.json` at `{scheme}://$GRACE2_RUNS_BUCKET/$GRACE2_RUN_ID/` — the
     SAME schema the solve worker writes, so the agent's `wait_for_completion`
     polls it identically.

## The two fixed caveats (vs the spike's proven-but-flawed deck)

- **CAVEAT 1 — SnapWave time column is tref-RELATIVE** (0.0, 7200.0, ...), not
  the SnapWave-internal epoch seconds the spike emitted. Enforced two ways:
  (a) `tref`/`tstart`/`tstop` set as proper datetimes so cht's
  `(time - tref).total_seconds()` is already 0-anchored, and (b) a post-write
  normalizer (`normalize_snapwave_time_columns`) that re-bases any bhs/btp/bwd/bds
  whose first time value is not ~0. The agent spec's `time_column_owned_by_cht`
  flag is intentionally IGNORED — the worker is the authority on this fix.
- **CAVEAT 2 — `snapwave_use_herbers = 1`** (infragravity-wave run-up), not 0.
  The worker FORCES 1 in `snapwave_inp_overrides`, overriding the agent spec's
  stale `use_herbers: 0`. A deliberate opt-out exists: `snapwave.force_no_herbers:
  true`.

## Build (deferred — no docker on the dev box)

Multi-stage `Dockerfile`, built from the **repo root** (`docker build -f
services/workers/sfincs_deckbuilder/Dockerfile .`). Stage 1 compiles the full
`cht_sfincs` closure (~128 packages, `cht_sfincs` pinned to commit
`159df40d`) into `/opt/venv`; stage 2 is `python:3.12-slim` + runtime
GDAL/GEOS/PROJ/netcdf shared libs + the copied venv (no compilers, no -dev
headers, pruned `__pycache__`/tests). **Estimated runtime image ~1.3-1.5 GB**
(numba/llvmlite + GDAL stack dominate; under the 2 GB AgentCore ceiling). The
ECR build/push is a deferred EC2/SSM step.

## Tests

```bash
# Pure-python (no GPL library needed) — runs anywhere:
python services/workers/sfincs_deckbuilder/test_entrypoint.py

# Full suite incl. a real cht_sfincs deck build (against the spike venv):
services/workers/sfincs_quadtree_spike/.venv/bin/python \
    services/workers/sfincs_deckbuilder/test_entrypoint.py
```

The integration test builds a genuine multi-level quadtree (nr_levels=3) +
SnapWave deck and asserts BOTH caveat fixes in the emitted `sfincs.inp` +
`snapwave.*` files.
