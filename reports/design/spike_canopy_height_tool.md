# Engine Spike: Canopy Height (OpenGeoAI "AI-using-AI" inference tool)

Research/scope spike for wiring the **OpenGeoAI canopy-height example**
(https://opengeoai.org/examples/canopy_height/) into GRACE-2 as a NEW agent tool:
an "AI using other AI models" inference tool that runs a pretrained deep-learning
canopy-height model on an AOI and publishes a canopy-height raster to the map.
NATE wants to mirror the OpenGeoAI "AI invokes a model" orchestration pattern,
running the inference on EC2 / AWS Batch the same scale-to-zero way our numerical
engines (SFINCS, SWMM, MODFLOW, OpenQuake, GeoClaw, SWAN, Landlab) do.

Grounded against primary sources (the OpenGeoAI / `geoai` package and its
`canopy.py` module + canopy_height example, the upstream Meta
`facebookresearch/HighResCanopyHeight` repo + the Tolan et al. 2024 paper, and the
Meta `dataforgood-fb-data` weights bucket) AND against the live GRACE-2 Batch
worker seam (`services/workers/openquake/*` + `services/agent/.../tools/solver.py`
`run_solver` / `wait_for_completion` are the closest structural analogues and are
cited throughout as the integration template).

ASCII only. No em/en dashes, no unicode arrows; "->" for arrows. Status: design +
verdict only, no code in this doc.

---

## 0. Verdict

**GO_WITH_CAVEATS - CPU-feasible Batch worker, no GPU compute environment needed
for v1.**

The canopy-height model is the cleanest "AI-using-AI" candidate we have looked at:
it is fully pretrained (no training, no labels), Apache-2.0 (code AND weights),
takes a single RGB GeoTIFF and emits a single-band float32 canopy-height-in-metres
GeoTIFF that drops straight into our existing `publish_layer` raster path with a
trivial new style preset. The OpenGeoAI `geoai` package already ships a turnkey
`CanopyHeightEstimation` class wrapping Meta's model with a CPU-friendly quantized
variant, so the inference glue is small.

The one real decision is GPU-vs-CPU, and the answer for v1 is **CPU**: Meta ships
a quantized `compressed_SSLhuge.pth` (749 MB) explicitly "tested on CPU only", and
geoai loads it with auto CUDA/CPU device selection. That means the canopy worker
runs on the SAME CPU SPOT Batch compute environment our solvers already use
(`c7i.xlarge..c7i.12xlarge`), with NO new GPU compute environment, NO `g4dn`/`g5`
instance ladder, and NO new IAM/quota work. A GPU compute environment is a
plausible **v2** optimization (the full `SSLhuge_satellite.pth` 2.9 GB model wants
a GPU, and CPU tiling is slow for large AOIs) but it is NOT a v1 prerequisite.

The caveats - none fatal:

1. **It is its OWN container, not a thin layer over an upstream image.** Unlike
   SFINCS (a wrapper over `deltares/sfincs-cpu`), there is no canonical
   "canopy-height" base image. We build from `python:slim` + torch CPU wheels +
   rasterio + the geoai canopy module (or a vendored copy of Meta's inference
   code), exactly the way the OpenQuake worker builds from `python:slim` +
   `openquake-engine`. The torch CPU wheel + the 749 MB weights make this our
   HEAVIEST worker image; image hygiene (multi-stage, CPU-only torch index,
   weights baked-in vs fetched-at-runtime) is the main build risk (section 4).

2. **Input imagery is an AOI-to-RGB-COG fetch we partly already have.** The model
   eats a plain RGB GeoTIFF. The OpenGeoAI example uses high-resolution aerial /
   NAIP / Maxar RGB. We already fetch NAIP-class imagery elsewhere in the tool set
   (imagery fetchers feed the fire/animation and digitize demos); the canopy tool
   needs a "give me an RGB COG for this bbox at sub-metre-to-metre resolution"
   input, which is either an existing imagery fetcher's output handle or a thin
   new NAIP fetch. This is the only data-spine gap and it is small.

3. **Truthfulness floor.** A canopy-height raster is a MODEL ESTIMATE (MAE ~2.5 m
   aerial / ~3.15 m satellite per Tolan et al.), not a measurement. The layer name
   + style key + result envelope must say "estimated canopy height (m)" and the
   honesty floor (an empty/all-zero output never reads status=ok) applies exactly
   as it does for the solvers.

---

## S1. The model + license + weights source

### The model

The OpenGeoAI canopy-height example wraps **Meta AI's "High-resolution canopy
height maps" model** (Tolan et al. 2024, *"Very high resolution canopy height maps
from RGB imagery using self-supervised vision transformer and convolutional
decoder trained on Aerial Lidar"*, Remote Sensing of Environment). Upstream repo:
**`facebookresearch/HighResCanopyHeight`** (Apache-2.0).

Architecture: a **DINOv2 self-supervised Vision Transformer (ViT) backbone** ("SSL"
= self-supervised) + a **DPT (Dense Prediction Transformer) convolutional decoder
head**, trained against aerial-LiDAR canopy heights. It predicts per-pixel canopy
top height in metres directly from 3-band RGB imagery (no multispectral, no LiDAR
at inference time). A small companion network
(`aerial_normalization_quantiles_predictor.ckpt`) does color/quantile balancing for
aerial inputs.

OpenGeoAI's `geoai` package (the `opengeos/geoai` repo, **MIT** licensed) provides
the turnkey wrapper: a **`geoai/canopy.py`** module exposing a
**`CanopyHeightEstimation`** class and a **`canopy_height_estimation()`** function
(plus `list_canopy_models()`), which downloads the Meta weights, builds the
`_SSLVisionTransformer` backbone + `_DPTHead` decoder, and runs tiled inference
over an input GeoTIFF. This is the "AI-using-AI" entrypoint NATE referenced.

### License

- **Meta HighResCanopyHeight code AND weights: Apache License 2.0.** Redistributable
  in a container, no registration, no key, no fee. This is the green light for a
  Batch worker (same posture as the GPL-but-redistributable SWAN/SFINCS images,
  but cleaner - Apache-2.0 has no copyleft, so unlike the GPL `cht_sfincs`
  deck-builder we do NOT need a GPL-isolation arms-length boundary; the model can
  live in the worker image freely).
- **OpenGeoAI `geoai` package: MIT.** Also clean. We can either depend on `geoai`
  directly in the worker image or vendor the ~1 module of inference code.

### Weights source

Meta hosts the weights in a **public, no-sign-request S3 bucket**:
`s3://dataforgood-fb-data/forests/v1/models/saved_checkpoints/`. The geoai module
pulls them over HTTPS from
`https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/models/saved_checkpoints/<file>.pth`
and caches to `~/.cache/geoai/canopy/`. Variants (`MODEL_VARIANTS` in
`geoai/canopy.py`):

| variant | file | size | note |
|---|---|---|---|
| `compressed_SSLhuge` (default) | `compressed_SSLhuge.pth` | 749 MB | quantized, **CPU-friendly** |
| `compressed_SSLhuge_aerial` | `compressed_SSLhuge_aerial.pth` | 749 MB | aerial-tuned decoder (NAIP/NEON) |
| `compressed_SSLlarge` | `compressed_SSLlarge.pth` | 400 MB | smaller ablation model |
| `SSLhuge_satellite` | `SSLhuge_satellite.pth` | 2.9 GB | full, **GPU required** |

Plus `aerial_normalization_quantiles_predictor.ckpt` (the color-balance net).

For GRACE-2 v1 we pin **`compressed_SSLhuge_aerial`** (best for NAIP/aerial RGB,
CPU-runnable, 749 MB) and bake the weights INTO the worker image at build time
(no runtime download on the SPOT box -> deterministic, no cold-start fetch of
749 MB on every job, no dependency on Meta's bucket staying public at run time).

---

## S2. Inputs / outputs / compute

### Inputs

- **Imagery**: a 3-band **RGB GeoTIFF** (the model is RGB-only; no NIR/multispectral
  needed). The OpenGeoAI example uses high-res aerial / NAIP (1 m and finer) or
  Maxar satellite RGB. Pixel values are normalized to [0,1] then standardized with
  the model's fixed mean `[0.420, 0.411, 0.296]` / std `[0.213, 0.156, 0.143]`.
- **AOI**: specified as a bbox (the GRACE-2 convention: `[min_lon, min_lat,
  max_lon, max_lat]`). The bbox -> RGB-COG step is the imagery fetch; the model
  itself just consumes the resulting GeoTIFF.
- **Tiling**: geoai cuts the input into **256x256 tiles** with a default **128 px
  overlap**, runs each tile through the ViT+DPT, and blends overlaps with
  raised-cosine weights for a seamless mosaic. Batch size default 4.

### Outputs

- A single-band **float32 canopy-height-in-metres** GeoTIFF, same georeferencing /
  CRS / extent as the input RGB (LZW-compressed). This is the published layer.
- No native uncertainty band (the model emits a point estimate; reported accuracy
  is dataset-level MAE ~2.5 m aerial / ~3.15 m satellite). v1 surfaces the
  accuracy caveat in the result envelope text, not as a per-pixel band.
- Optional downstream: feed the height raster into our existing
  `compute_zonal_statistics` (mean/max canopy height per admin polygon or per FTW
  ag field) - a natural "how tall is the canopy in this field" follow-up that
  reuses the conversational-analysis layer with zero new code.

### Compute

- **GPU vs CPU**: **CPU for v1.** The default `compressed_SSLhuge` /
  `compressed_SSLhuge_aerial` are quantized and explicitly CPU-runnable; geoai
  auto-selects CUDA-if-available else CPU. The full 2.9 GB `SSLhuge_satellite`
  wants a GPU - deferred to v2.
- **Model size / RAM**: 749 MB weights on disk; figure ~4-8 GB RAM resident for the
  ViT-huge backbone + tile batches on CPU. Comfortably inside our `standard`
  (8 vCPU / 16 GiB) or `large` (16 vCPU / 32 GiB) Batch sizing buckets
  (`AWS_BATCH_COMPUTE_CLASS_SIZING` in `solver.py`).
- **Runtime**: CPU ViT-huge inference is the slow part. A small AOI (a few
  km2 at 1 m -> a few thousand 256-tiles) runs in minutes on a `c7i` box; a large
  AOI (county-scale at 1 m) is tens of minutes to hours on CPU - which is exactly
  why scale-to-zero Batch (not in-agent) is the right home, and why a GPU CE is the
  v2 lever for big AOIs. The agent's existing autoscale (`select_compute_class`)
  can pick `large`/`xlarge` from the tile count.
- **Python deps**: `torch` + `torchvision` (CPU wheels), `rasterio`, `numpy`, and
  `geoai` (or vendored Meta inference code). torch CPU + 749 MB weights make this
  our largest worker image; multi-stage + the CPU-only torch wheel index keeps it
  as lean as possible.

### The OpenGeoAI orchestration pattern ("AI using AI")

OpenGeoAI's `geoai` exposes the model as a Python object you instantiate and call:
load `CanopyHeightEstimation(model="compressed_SSLhuge_aerial")`, then
`predict(input_geotiff, output_geotiff)` runs the tiled ViT+DPT inference and
writes the height raster. The "agent" in the OpenGeoAI examples is just code (a
notebook / a thin caller) that picks the model variant + input image and invokes
`predict()`. **We mirror this exactly**: our agent's LLM picks the AOI + variant
and emits a tool call (`compute_canopy_height`); the tool stages an RGB COG and
dispatches a Batch worker whose entrypoint instantiates `CanopyHeightEstimation`
and calls `predict()` - the same "outer AI selects + invokes an inner model"
shape, dropped onto our scale-to-zero substrate.

---

## S3. Recommended GRACE-2 integration

### One-sentence recommendation

Add a `compute_canopy_height` agent tool that stages an RGB COG for the AOI to S3,
submits a NEW CPU Batch worker (`services/workers/canopy/`, modeled on the
OpenQuake worker) which runs Meta's quantized HighResCanopyHeight via geoai's
`CanopyHeightEstimation.predict()` and writes a single-band metres GeoTIFF +
`completion.json` to `s3://<runs_bucket>/<run_id>/`, then publishes that COG via
the existing `publish_layer` path with a new "canopy height (m)" style preset - on
the EXISTING CPU SPOT compute environment, no GPU CE required for v1.

### In-agent vs Batch-worker: Batch worker

In-agent is NOT viable: torch + a 749 MB ViT-huge + minutes-to-hours of CPU
inference would block the asyncio loop and starve the WS heartbeat (the exact
loop-block class MEMORY warns about), and the agent venv must stay light. This is
heavy, sandboxed, emit-free, scale-to-zero compute - the textbook Batch-worker
profile, identical to how OpenQuake (RAM-hungry, never in-process) is handled.

### GPU-or-CPU: CPU (v1), GPU optional (v2)

v1 runs the quantized model on the existing CPU SPOT CE. NO new infra. v2 (if big
AOIs prove too slow) adds a SEPARATE GPU compute environment (`g4dn`/`g5` SPOT) +
its own queue + a GPU job-def routed via the existing per-solver
`GRACE2_AWS_BATCH_JOB_DEF_<SOLVER>` resolver - additive, behind the same seam,
flag this as a known future infra need but DO NOT build it for v1.

### The flow (mirrors run_solver + the OpenQuake worker, end to end)

1. **Agent tool `compute_canopy_height(bbox, imagery_uri=None, model_variant=...)`**
   - registered exactly like `compute_zonal_statistics`
   (`@register_tool(AtomicToolMetadata(name="compute_canopy_height", ...))`),
   categorized under `land_cover_development` (or `conservation_ecology`).
   Uncacheable-by-construction like the solver dispatchers (it spends SPOT), or
   cache-keyed on `(bbox, variant, imagery_uri)` if we want re-run dedup - lean
   uncacheable for v1.
2. **Stage the RGB COG** to `s3://<cache>/...`: either resolve a caller-provided
   `imagery_uri` handle (preferred - reuse an existing imagery fetcher's NAIP/Maxar
   output) or, if absent, fetch a NAIP RGB COG for the bbox (small new fetch). The
   model needs an `s3://` input because the Batch worker has no agent-box FS access
   (the same honesty guard `_run_solver_aws_batch` enforces on `model_setup_uri`).
3. **Write a build_spec JSON** (`{bbox, imagery_uri, model_variant, output_glob}`)
   to S3 - the SAME pattern OpenQuake/SWMM use for their manifest.
4. **Dispatch the Batch job.** Cleanest path: ADD `"canopy"` to
   `SOLVER_WORKFLOW_REGISTRY` and call `run_solver("canopy",
   model_setup_uri=<build_spec s3 uri>, compute_class=select_compute_class(tiles))`.
   This reuses `_run_solver_aws_batch` (mints run_id, `batch.submit_job`, stashes
   jobId in the handle), the per-solver job-def routing
   (`GRACE2_AWS_BATCH_JOB_DEF_CANOPY`), and `wait_for_completion` (S3 completion
   poll + progress ramp + Invariant-8 cancel) **verbatim**. No new dispatch code -
   the canopy worker just needs to write the SAME `completion.json` schema the
   OpenQuake/SWMM workers write (run_id / status / exit_code / *_stdout_uri /
   output_uris / started_at / finished_at / error).
5. **The worker** (`services/workers/canopy/entrypoint.py`, near-copy of the
   OpenQuake entrypoint): read build_spec by URI scheme -> download the RGB COG ->
   `CanopyHeightEstimation(model=variant).predict(in_tif, out_tif)` -> upload
   `canopy_height.tif` + stdout/stderr -> ALWAYS write `completion.json`. Weights
   baked into the image at build time.
6. **Publish**: the agent reads the output COG handle from the `RunResult` and
   calls `publish_layer(layer_uri=<canopy_height.tif handle>, layer_id="canopy
   height (m)")`. Add ONE entry to the `_resolve_titiler_style_params` preset map
   (e.g. `&rescale=0,40&colormap_name=viridis` or a greens ramp; metres 0..~40)
   so TiTiler renders it correctly instead of grey auto-rescale. publish_layer is
   otherwise unchanged.

### New infra (all small, mostly a clone of the OpenQuake lane)

- `services/workers/canopy/` (Dockerfile + entrypoint + a pure deck/spec helper),
  cloned from `services/workers/openquake/`.
- `infra/aws-batch/canopy.tf` - a new job DEFINITION only (clone of `openquake.tf`),
  pointing at the new ECR image, sized at the `standard`/`large` bucket. **Reuses
  the existing `grace2-solvers-spot` CPU compute environment + the existing
  `solvers` job queue + the existing IAM** (the runs/cache bucket access the agent
  + task roles already have). No GPU CE, no new queue for v1.
- ECR repo + CodeBuild entry for the new image (mirror existing workers).
- Env on the box: `GRACE2_AWS_BATCH_JOB_DEF_CANOPY` (the per-solver job-def knob).
- A NAIP/RGB-COG imagery fetcher IF no existing fetcher's output is reusable
  (verify against the current imagery fetch tools first - likely a thin wrapper).

---

## S4. Build outline + effort / risk

### Build outline (ordered)

1. **Worker image** (`services/workers/canopy/`): Dockerfile from `python:slim`,
   multi-stage, CPU-only torch wheel index
   (`pip install torch torchvision --index-url .../cpu`), `rasterio`, `geoai`;
   bake `compressed_SSLhuge_aerial.pth` +
   `aerial_normalization_quantiles_predictor.ckpt` into the image; build-time smoke
   that imports the model and runs a tiny inference. **Image hygiene is the main
   risk here** (torch CPU + 749 MB weights is ~2-3 GB+; pre-push inspect per the
   container-hygiene norm).
2. **Worker entrypoint** (near-copy of `openquake/entrypoint.py`): build_spec read
   -> imagery download -> `CanopyHeightEstimation.predict()` -> upload -> SAME
   `completion.json`. Pure spec-helper unit-tested in isolation.
3. **Agent tool** `compute_canopy_height` (`tools/compute_canopy_height.py`):
   register, validate bbox/variant, stage imagery + build_spec, call
   `run_solver("canopy", ...)` + `wait_for_completion`, return the output handle.
   Add `"canopy"` to `SOLVER_WORKFLOW_REGISTRY` and the categories map.
4. **publish_layer preset**: one entry in `_resolve_titiler_style_params` for the
   metres ramp. Register the tool import in `tools/__init__.py`.
5. **Infra**: `canopy.tf` job-def + ECR + CodeBuild + the env flip
   (`GRACE2_AWS_BATCH_JOB_DEF_CANOPY`); `tofu apply` (NATE-gated live mutation).
6. **E2E**: drive "estimate canopy height for <small forested AOI>" -> Batch job ->
   green completion -> canopy COG painted on the map; optional `compute_zonal_
   statistics` follow-up for mean height per polygon.

### Effort

- Worker image + entrypoint: ~1 engineer-day (the OpenQuake clone is the template;
  the torch/weights image is the new bit).
- Agent tool + registration + preset: ~0.5 day (the dispatch is reused wholesale).
- Imagery fetch (if a new NAIP fetcher is needed): ~0.5-1 day; ~0 if reusable.
- Infra (job-def + ECR + CodeBuild): ~0.5 day (clone of the OpenQuake lane).
- Total: ~2-3 engineer-days to a live CPU E2E, GPU CE explicitly out of scope.

### Risk

- **Image size / build time (medium).** Heaviest worker image we have. Mitigate
  with CPU-only torch wheels, multi-stage, baked weights, pre-push inspect.
- **CPU runtime on large AOIs (medium).** Minutes-to-hours; cap the v1 AOI size
  (granularity gate / payload-warning) and lean on `select_compute_class` to grab
  `large`/`xlarge`; GPU CE is the v2 escape hatch.
- **Imagery availability / resolution (medium).** Quality tracks input resolution;
  NAIP is CONUS-only and ~0.6-1 m. Outside CONUS or without sub-metre RGB the
  estimate degrades - apply the data-source fallback norm (NAIP -> Maxar/other ->
  honest typed error), never silently produce a garbage layer.
- **Weights bucket permanence (low).** Mitigated by baking weights into the image
  (no run-time dependence on `dataforgood-fb-data` staying public).
- **Truthfulness (low, but mandatory).** Layer/result text must read "estimated"
  with the MAE caveat; the empty-output honesty floor applies.

---

## S5. What a v1 cuts

- **No GPU compute environment.** CPU quantized model only. The full 2.9 GB
  `SSLhuge_satellite` GPU path + a `g4dn`/`g5` SPOT CE + GPU queue/job-def is v2,
  gated on big-AOI runtime pain. Flagged as a known future infra need, NOT built.
- **No per-pixel uncertainty band.** Point estimate + a dataset-level MAE caveat in
  the envelope only.
- **No multi-model ensemble / variant auto-selection.** Pin
  `compressed_SSLhuge_aerial`; expose `model_variant` as an advanced override but
  do not auto-choose.
- **No new generic NAIP/imagery fetcher IF one is reusable.** Prefer a caller-
  supplied `imagery_uri` handle from an existing fetcher; only add a thin NAIP
  fetch if nothing fits.
- **No bespoke time-series / multi-date canopy-change.** Single-date height only;
  change detection over two dates is an obvious but deferred follow-up.
- **No promotion to a generic "run any geoai pretrained model" abstraction.** Ship
  canopy height concretely first; the "AI-using-AI inference worker" generalization
  (other geoai/torchgeo/HuggingFace geospatial models on the same Batch substrate)
  is the second-engine-generalize moment, not v1 - do not pre-abstract.

---

## Appendix: structural mapping to the live seam

| GRACE-2 seam | canopy-height use |
|---|---|
| `run_solver(solver, model_setup_uri, compute_class)` | dispatch the Batch job; add `"canopy"` to `SOLVER_WORKFLOW_REGISTRY` |
| `_run_solver_aws_batch` + `_resolve_batch_job_def` | reused verbatim; new `GRACE2_AWS_BATCH_JOB_DEF_CANOPY` |
| `wait_for_completion` (S3 completion poll + Invariant-8 cancel) | reused verbatim; worker writes the SAME completion.json schema |
| `select_compute_class(tiles)` | pick `standard`/`large`/`xlarge` from tile count (CPU) |
| `services/workers/openquake/*` | clone -> `services/workers/canopy/*` |
| `infra/aws-batch/openquake.tf` | clone -> `canopy.tf` (job-def only; reuse CPU CE + queue + IAM) |
| `publish_layer` + `_resolve_titiler_style_params` | publish the metres COG + one new preset entry |
| `compute_zonal_statistics` | optional follow-up: mean/max canopy height per polygon/FTW field |
| `@register_tool(AtomicToolMetadata(...))` + `categories.py` | register `compute_canopy_height` under `land_cover_development` |
