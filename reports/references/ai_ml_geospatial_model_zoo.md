# AI/ML Geospatial Model Zoo -- Candidate Inference Tier for GRACE-2

RESEARCH reference (2026-06-22). NO application code. Surveys reliable,
open AI/ML geospatial INFERENCE models/tools that could run on EC2 / AWS Batch
the same way our numerical engines do (scale-to-zero, containerized,
agent-driven) -- a tier of "AI using AI" tools the agent invokes alongside the
physics engines (SFINCS / MODFLOW / PySWMM / OpenQuake / GeoClaw / Landlab /
SWAN).

This is a SIBLING to the OpenGeoAI canopy-height spike (branch
`spike/canopy-height-tool`). That work scopes the Meta/OpenGeoAI canopy-height
model specifically and is the FIRST model of this tier. This doc does NOT
re-scope canopy height -- it references it as the proven first instance and
catalogs the OTHERS so we know what the tier looks like end to end.

--------------------------------------------------------------------------------
## 0. The headline infra question (answered up front)

**Our AWS Batch compute environment is CPU-only today.** `infra/aws-batch/` runs
SPOT with `instance_type = "optimal"` (Batch picks c4/c5/m4/m5/c7i/m7i) and
every job definition declares only `VCPU` + `MEMORY` resource requirements
(e.g. MODFLOW = 4 vCPU / 8 GB). There is NO `type = "GPU"` resource requirement,
no g4dn/g5/p3/p4 instance family, no NVIDIA anywhere in `infra/`. The agent box
and TiTiler box are CPU instances too.

So the tier splits cleanly into two infra classes:

- **CPU-only models -> ZERO new infra.** They drop into the existing Batch CPU
  compute environment as a new container + job definition, exactly like a
  solver. This is the cheap, fast-to-ship path.
- **GPU-preferred models -> ONE new infra unit gates them:** a GPU AWS Batch
  compute environment (a second managed CE on g4dn/g5 SPOT, scale-to-zero like
  the CPU one, with the ECS-GPU AMI + NVIDIA resource requirement on the job
  def). This is a single, reusable piece of IaC (mirror `infra/aws-batch/`)
  that unlocks the whole GPU column at once -- it is the gating dependency for
  the heavy foundation-model / SAM / canopy-satellite path.

**Recommendation: build the CPU-only models first (no new infra), and stand up
ONE GPU Batch CE as the single enabling investment for the GPU column.**
g4dn.xlarge SPOT (1x T4, 16 GB) is the natural floor; g5.xlarge (1x A10G,
24 GB) for the 600M-param foundation models. Both scale-to-zero, so idle cost
is ~$0 -- consistent with the scale-to-zero island architecture.

--------------------------------------------------------------------------------
## 1. What GRACE-2 already covers (NEW vs already-done flags)

Grepped `services/agent/src/grace2_agent/tools/`. The current stack has NO
learned-inference geospatial models -- everything geospatial today is either a
PUBLISHED-product fetch or a deterministic raster/vector compute:

| Existing tool | What it is | Relation to this zoo |
|---|---|---|
| `fetch_field_boundaries` | FTW/fiboa PUBLISHED GeoParquet ag field polygons (pruned read, NOT inference) | The OpenGeoAI `field_boundary_detection` model is the INFERENCE complement -- runs where no published vector exists. NEW (inference path). |
| `compute_impervious_surface` | Reclass of NLCD class codes to impervious fraction (pure numpy) | OpenGeoAI `impervious_surface_mapping` is a learned model from RGB. NEW (inference path). |
| `extract_landcover_class` / `fetch_landfire_fuels` / `fetch_usfs_canopy_fuels` | Published landcover/fuel rasters | Clay/Prithvi/WorldCover-style learned land cover is NEW. |
| OSM building footprints (via `fetch_roads_osm` Overpass pattern, `compute_building_density`) | OSM-published vector footprints | SAM / Mask-R-CNN building EXTRACTION from imagery (where OSM is absent/stale) is NEW. |
| `fetch_*` (GOES/VIIRS/FIRMS/MTBS/HRRR/MRMS...) | Published EO products | Prithvi burn-scar/flood are learned-segmentation complements. NEW. |

**Net: every candidate below is NEW capability.** None duplicate an existing
tool; several are the on-demand-INFERENCE sibling of an existing published-data
fetch (the agent gains "compute it from imagery when the published layer does
not cover this AOI" -- our data-source-fallback norm, extended to ML).

There is also a latent **digitize-this-pond / digitize-water-body** product
intent (SAM/NDWI vectorize) noted in project memory -- SamGeo (row 1) is the
direct vehicle for it.

--------------------------------------------------------------------------------
## 2. Scoring method

Weighted 0-5 per axis, summed (max 30), then ranked. Axes:

- **Reliability/Maturity (R)** -- is it a real, cited, widely-used, maintained model with pretrained weights that just work.
- **Open license (L)** -- 5 = permissive (MIT/Apache/CC-BY commercial-OK); lower = restrictive/non-commercial/unclear.
- **AI-drivability (D)** -- how cleanly the agent can call it as one tool: bbox + imagery in, raster/vector layer out, few knobs, deterministic enough to narrate.
- **Batch-deployability (B)** -- containerizable + scale-to-zero fit. 5 = CPU-only, drops into existing CE; 3 = needs the new GPU CE; lower = heavy/stateful/awkward.
- **Demo value (V)** -- pull for GRACE-2's North Stars (coastal/urban flood, fire, conservation, contamination, FTW ag) and visible "wow".
- **Low marginal infra (I)** -- 5 = nothing new; 3 = shares the one new GPU CE; lower = needs more.

GPU-preferred models take B and I penalties UNTIL the GPU CE exists; after it
exists they rise (the column unlocks together). Scores below reflect TODAY
(no GPU CE yet) to make the infra gate visible in the ranking.

--------------------------------------------------------------------------------
## 3. Ranked catalog

Columns: model | does what | license | inputs -> outputs | GPU/CPU | maturity | Batch-fit | proposed GRACE-2 tool | micro-demo. Score = R+L+D+B+V+I (/30).

| # | Score | Model / pkg | Does what | License | Inputs -> Outputs | GPU/CPU | Maturity | Batch-fit | Proposed tool | Micro-demo |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **27** | **DeepForest** (weecology) | Individual tree-crown bounding boxes + (v2) crown masks in RGB | MIT | NAIP/UAV/Maxar RGB tile -> point/box/polygon vector | **CPU OK** (~7 min/km2 CPU, faster GPU); model ~100 MB RetinaNet R50 | High -- peer-reviewed (Meth. Ecol. Evol. 2020), pip/conda, pretrained weights ship | **Excellent** -- CPU, fits existing CE, no new infra | `detect_tree_crowns` | "How many trees / canopy crowns in this park AOI" -> point+box layer + count; pairs with canopy-height for per-tree height. Conservation + urban-forestry demo. |
| 2 | **27** | **segment-geospatial / SamGeo** (opengeos) SAM + SAM2/SAM3 | Promptable + automatic segmentation of buildings, water, fields, vegetation from imagery -> clean vectors | Apache-2.0 (pkg); SAM weights Apache-2.0, SAM2 Apache-2.0 | RGB/satellite tile + (optional) point/box/text prompt -> mask raster + GeoJSON polygons | GPU recommended (>=8 GB); **CPU fallback** (CUDA_VISIBLE_DEVICES=-1, slow). ViT-B ~375 MB / ViT-H ~2.5 GB | High -- the canonical geospatial SAM wrapper, huge user base, active | CPU-runnable now (slow) -> shines on GPU CE | `segment_features` (a.k.a. `digitize_water_body`) | The **digitize-this-pond** demo: click/lasso/"the pond" -> SAM mask -> editable polygon. Also building/field extraction where OSM/FTW is empty. Highest product leverage. |
| 3 | **26** | **OpenGeoAI canopy height** (Meta CHM, `compressed_SSLhuge_aerial` / `SSLhuge_satellite`) | Per-pixel canopy height in meters from RGB | Permissive commercial-OK (Meta+WRI release); DINOv2 backbone | NAIP/Maxar RGB -> float32 height raster (m) | Aerial variant runs CPU-ish; **satellite variant requires GPU**; DINOv2-Huge + DPT, multi-GB | High -- Meta/WRI global product, CHMv2 (DINOv3) out, OpenGeoAI example ships it | **ALREADY SCOPED** (sibling spike) | `estimate_canopy_height` (sibling) | **Owned by `spike/canopy-height-tool`.** Listed for completeness + scoring; first model of this tier. |
| 4 | **25** | **Prithvi-EO** (IBM + NASA) burn-scar / flood / crop fine-tunes | Segmentation foundation model fine-tuned for burn scars, flood extent, multi-temporal crop type | Apache-2.0 (open on HuggingFace) | HLS (Harmonized Landsat-Sentinel) 6-band chips -> class/segmentation raster | GPU-preferred (ViT, 100M / 600M params); 100M runs on modest GPU, CPU very slow | High -- NASA/IBM flagship, multiple task heads, well-documented, TerraTorch-supported | Needs GPU CE; clean containerize | `segment_burn_scar`, `map_flood_extent_ml`, `classify_crop_type` | Burn-scar map for a wildfire Case (complements MTBS/VIIRS); ML flood-extent from HLS as a cross-check on SFINCS; crop-type for the FTW contamination demo. Strong North-Star tie. |
| 5 | **24** | **GeoAI building-footprint segmentation** (opengeos, Mask R-CNN / DINOv3 heads; USA-NAIP, Africa, China, WHU, LiDAR) | Extract building footprints as regularized polygons from imagery (or nDSM) | MIT (pkg); open pretrained heads | NAIP/RGB (or LiDAR-derived nDSM) -> regularized building polygons | GPU-preferred (Mask R-CNN); CPU possible per-tile but slow | High -- multiple region-specific pretrained models + regularization utilities ship in GeoAI | Needs GPU CE for scale; CPU per-tile demo OK | `extract_building_footprints` | "Give me footprints for this town" where OSM is sparse/stale -> polygons feed PySWMM obstruction + Pelicun exposure + flood damage. Direct urban North-Star feed. |
| 6 | **23** | **Clay v1.5** foundation model | General geospatial EMBEDDINGS (768-d) for any downstream task (similarity search, few-shot classify, change) | Apache-2.0; model OpenRAIL-ish open | Sentinel-2/1, Landsat, NAIP 256x256 chips + lat/lon/time -> embedding vectors / COG of embeddings | GPU (632M ViT; AWS reference uses g5.xlarge); CPU impractical at scale | High -- well-funded foundation, AWS Open Data, active releases | Needs GPU CE; embeddings cache well to S3 | `embed_imagery` (+ `similarity_search`) | "Find areas that look like THIS (burned / flooded / built-up)" via embedding similarity; few-shot land classify without a labeled dataset. Enabling primitive, less of a standalone visual. |
| 7 | **23** | **Google Satellite Embedding V1 / AlphaEarth** (DeepMind) | Pre-computed 64-band annual global embeddings (2017-2025) -- a DATASET, not a model you run | CC-BY-4.0 (attribution) | Pre-baked COGs on GCS + AWS Open Data (`registry.opendata.aws/aef-source`) -> 64-band embedding raster per pixel/year | **No inference at all -- fetch only (CPU)** | High -- Google/DeepMind, on AWS Open Data | **Trivial -- it is a fetch, no GPU, no model** | `fetch_satellite_embedding` | Pull the 64-band embedding for an AOI -> feed a tiny CPU classifier head for instant land-cover / change without running a big model. Cheapest "foundation-model power" with ZERO inference infra. |
| 8 | **22** | **ESA WorldCover deep-learning land cover** (10 m, 11 class) | Global 10 m land-cover product (CatBoost on S1+S2) -- PUBLISHED raster, on AWS Open Data | CC-BY-4.0 | Pre-baked global COGs (AWS `esa-worldcover-vito`) -> class raster | **Fetch only (CPU)** | Very high -- ESA operational product | Trivial fetch | `fetch_worldcover_landcover` | A learned 10 m landcover backdrop / Manning-n + curve-number source where NLCD (US-only) does not reach (ex-US Cases / global coastal demos). |
| 9 | **21** | **torchange / ChangeStar(2)** (Z. Zheng; also in TorchGeo) | Bi-temporal (and single-temporal-supervised) change detection -> change mask (new/demolished buildings, deforestation, flood onset) | Apache-2.0 (also Microsoft TorchGeo MIT) | Two co-registered RGB/MS tiles (t1,t2) -> change-mask raster | GPU-preferred; CPU per-tile possible | Medium-high -- ICCV'21 / IJCV'24, in TorchGeo, OpenGeoAI `changestar` example | Needs GPU CE for scale | `detect_change` | Before/after a hazard: flood onset, building damage, burn progression, deforestation. Pairs with any time-stamped imagery; strong narrative ("what changed"). |
| 10 | **21** | **TorchGeo pretrained backbones** (Microsoft) ResNet/ViT/DOFA, Sentinel-2/Landsat MoCo/MAE | Library of multispectral pretrained encoders for transfer-learning classification/segmentation | MIT | S2 13-band / Landsat chips -> features / fine-tuned class maps | GPU-preferred; small backbones CPU-OK | High -- Microsoft, torchvision multi-weight API, very stable | CPU for small heads; GPU for ViT | `classify_landcover_ml` (head-dependent) | The substrate for custom small classifiers (e.g. impervious / wetland / solar) when no off-the-shelf head exists. More a toolkit than a one-shot tool. |
| 11 | **20** | **GeoAI object detectors** -- car / ship / solar-panel / parking-spot (RF-DETR / Mask R-CNN heads in opengeos/geoai) | Detect & count discrete objects in high-res imagery | MIT (pkg) | NAIP/Maxar RGB -> point/box/polygon vector + counts | GPU-preferred; CPU per-tile slow | Medium-high -- example notebooks + pretrained heads | Needs GPU CE for scale | `detect_objects` (typed: cars/ships/solar/parking) | "Count cars in this lot / ships in this harbor / solar panels on this AOI" -> exposure/impervious/economic-activity proxies. Nice visible counts; secondary to flood North Stars. |
| 12 | **19** | **Super-resolution** -- ESRGAN / Real-ESRGAN (OpenGeoAI `esrgan`, `super_resolution`) | 2-4x upscale of coarse imagery for sharper downstream extraction | Apache-2.0 / BSD (Real-ESRGAN) | Low-res RGB tile -> upscaled RGB tile | GPU-preferred; CPU slow | Medium -- generic SR adapted to RS; quality caveats on real EO | Needs GPU CE | `super_resolve_imagery` | Sharpen historical/low-res tiles before SAM/footprint extraction. ENABLER, not a standalone deliverable; watch hallucination risk (label clearly as enhanced). |
| 13 | **18** | **OmniWaterMask / water segmentation** (OpenGeoAI / QGIS GeoAI plugin) | Robust surface-water mask from imagery | Open (MIT-ish) | RGB/MS tile -> water mask raster + polygon | GPU-preferred; CPU possible | Medium -- newer, less battle-tested than SAM | Needs GPU CE | (folds into `segment_features` water mode) | Water-extent for flood validation / pond digitize. Largely SUBSUMED by SamGeo water-mode; list as a specialized alternative, not a separate build. |
| 14 | **16** | **Methane / plume detection** (CH4Net, AttMetNet, MARS-S2L; Sentinel-2 SWIR) | Detect & quantify methane super-emitter plumes | Mixed (CH4Net dataset open; some models research-grade) | Sentinel-2 SWIR bands -> plume mask + (some) rate estimate | GPU-preferred; research-grade pipelines | Lower/research -- promising but less turnkey, fragmented | Needs GPU CE + careful packaging | `detect_methane_plume` | Ties the MODFLOW-GWT contamination/plume narrative to an atmospheric analogue. Cool but research-grade reliability -- defer until the tier is proven. |
| 15 | **16** | **TESSERA embeddings** (OpenGeoAI `tessera` example) | Alternative open geospatial embedding product | Open (per OpenGeoAI) | S2 time series -> embeddings | GPU-preferred | Lower -- newer, niche | Needs GPU CE | (alt to `embed_imagery`) | Redundant with Clay/AlphaEarth for our purposes; track, do not build separately. |

Notes on near-duplicates intentionally collapsed: `instance_segmentation`,
`grounded_sam`, `text_prompt_segmentation` -> all SamGeo modes (row 2).
`field_boundary_detection` (OpenGeoAI) -> the inference complement to our
existing `fetch_field_boundaries`; high FTW-demo value, sits behind SamGeo's
field mode + a regularizer (build as a `detect_field_boundaries` mode of row 2
or a thin head, after the GPU CE exists). `cloud_detection`,
`road_network_simplification`, `building_regularization` -> useful
POST-PROCESS utilities (mostly CPU) that ride along with the segmentation
tools rather than standing alone.

--------------------------------------------------------------------------------
## 4. Recommended FIRST 3 to build

Chosen to (a) prove the tier with ZERO new infra, (b) deliver an immediate
visible demo, and (c) make the single GPU-CE investment pay for the most
models at once.

1. **DeepForest -> `detect_tree_crowns` (CPU, no new infra).** MIT, pretrained,
   runs on the existing Batch CPU CE exactly like a solver. Pairs with the
   canopy-height sibling for a complete "trees of this AOI: count + per-crown
   height" conservation/urban-forestry demo. Lowest risk, fastest proof that
   an ML inference tool slots into the engine pattern. **Build first.**

2. **Fetch-only foundation power -> `fetch_satellite_embedding` (AlphaEarth) +
   `fetch_worldcover_landcover` (CPU, no new infra).** These are NOT inference
   -- they are AWS-Open-Data COG fetches (CC-BY) of pre-baked
   foundation-model embeddings / learned land cover. They give "foundation
   model" capability (similarity, few-shot classify, global landcover) for the
   cost of a fetch tool, and a tiny CPU classifier head on the embeddings is a
   trivial follow-on. Maximum capability per infra dollar; ships alongside #1.

3. **SamGeo -> `segment_features` / `digitize_water_body` (gates + justifies the
   GPU CE).** This is THE product-leverage model (the long-standing
   digitize-this-pond intent, plus building/field extraction where
   OSM/FTW is empty). It is the right reason to stand up the ONE GPU Batch
   compute environment, which simultaneously unlocks Prithvi (#4), building
   footprints (#5), Clay (#6), change detection (#9) and the satellite canopy
   variant. Build the GPU CE for SamGeo; reuse it for the rest.

This sequence yields two shipped CPU tools with no infra change, then one
deliberate GPU-CE investment that opens the entire heavy column.

--------------------------------------------------------------------------------
## 5. Infra note (consolidated)

- **CPU-only -> NO new infra (drop into existing `infra/aws-batch/` CE):**
  DeepForest; the two fetch-only "foundation" tools (AlphaEarth embeddings,
  ESA WorldCover); the CPU post-process utilities (regularization, road
  simplification, cloud-mask); and the small-backbone TorchGeo heads. SamGeo
  and the object detectors *can* run CPU per-tile for a slow demo, but are not
  practical at scale on CPU.

- **GPU-preferred -> gated on ONE new infra unit (a GPU AWS Batch CE):**
  SamGeo (at scale), Prithvi, building-footprint segmentation, Clay,
  change detection, object detectors, super-resolution, water-seg, methane,
  TESSERA, and the satellite canopy-height variant. They share a SINGLE
  enabling dependency.

- **The gating infra need is exactly one thing: a scale-to-zero GPU AWS Batch
  compute environment** (mirror `infra/aws-batch/main.tf`: managed SPOT CE on
  `g4dn.xlarge` floor / `g5.xlarge` for 600M-param models, ECS GPU-optimized
  AMI, `min_vcpus = 0`, and `resourceRequirements` with `type = "GPU"` on the
  job defs). Idle cost ~$0 (scale-to-zero), consistent with the
  scale-to-zero island architecture. Building it once converts the entire
  GPU column from "blocked" to "available."

- **Container hygiene reminder:** these models pull multi-GB weights
  (DINOv2-Huge, SAM ViT-H ~2.5 GB, Clay 632M, Prithvi 600M). Bake weights into
  the image or stage from S3 at job start; multi-stage + minimal CUDA base +
  `.dockerignore`; inspect size before any ECR push (per container-hygiene
  norm). Several models share a torch+CUDA base -> a shared base image keeps
  the GPU column's images lean.

--------------------------------------------------------------------------------
## 6. Primary sources

- OpenGeoAI / opengeos `geoai`: https://opengeoai.org/ , https://github.com/opengeos/geoai (MIT). Examples catalog enumerated from `docs/examples/` (building footprints USA/Africa/China/WHU/LiDAR, canopy_height, car/ship/solar/parking detection, change_detection/changestar, esrgan/super_resolution, prithvi, tessera, AlphaEarth, samgeo/grounded_sam/text_prompt_segmentation, field_boundary_detection, impervious_surface_mapping, cloud_detection, road_network_simplification, building_regularization, foundation_models).
- Canopy height: https://opengeoai.org/examples/canopy_height/ (Meta `compressed_SSLhuge_aerial` / `SSLhuge_satellite`, DINOv2+DPT, satellite variant requires GPU); Meta+WRI CHM https://ai.meta.com/blog/world-resources-institute-dino-canopy-height-maps-v2/ ; CHMv2/DINOv3 arXiv 2603.06382.
- DeepForest: https://besjournals.onlinelibrary.wiley.com/doi/full/10.1111/2041-210X.13472 (MIT, RetinaNet R50, ~7 min/km2 CPU).
- segment-geospatial / SamGeo: https://samgeo.gishub.org/ , https://github.com/opengeos/segment-geospatial (Apache-2.0; SAM/SAM2 Apache-2.0; GPU>=8GB recommended, CPU fallback).
- Prithvi-EO (IBM+NASA): https://huggingface.co/ibm-nasa-geospatial ; https://github.com/NASA-IMPACT/hls-foundation-os ; https://github.com/NASA-IMPACT/Prithvi-EO-2.0 (Apache-2.0; 100M & 600M; burn-scar/flood/crop heads).
- Clay v1.5: https://clay-foundation.github.io/model/ ; https://huggingface.co/made-with-clay/Clay (Apache-2.0; 632M ViT; AWS ref ml.g5.xlarge). AWS GFM blog: https://aws.amazon.com/blogs/machine-learning/revolutionizing-earth-observation-with-geospatial-foundation-models-on-aws/ .
- Google Satellite Embedding V1 / AlphaEarth: https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_SATELLITE_EMBEDDING_V1_ANNUAL (CC-BY-4.0); AWS Open Data https://registry.opendata.aws/aef-source/ ; DeepMind https://deepmind.google/blog/alphaearth-foundations-helps-map-our-planet-in-unprecedented-detail/ .
- ESA WorldCover: https://registry.opendata.aws/esa-worldcover-vito/ ; https://developers.google.com/earth-engine/datasets/catalog/ESA_WorldCover_v200 (CC-BY-4.0; CatBoost on S1+S2).
- torchange / ChangeStar: https://github.com/Z-Zheng/pytorch-change-models ; https://github.com/Z-Zheng/ChangeStar (Apache-2.0; ICCV'21 / IJCV'24; in TorchGeo).
- TorchGeo (Microsoft): https://torchgeo.readthedocs.io/en/stable/tutorials/pretrained_weights.html (MIT; S2/Landsat ResNet/ViT/DOFA multi-weight API).
- Methane: CH4Net https://amt.copernicus.org/articles/17/2583/2024/ ; AttMetNet arXiv 2512.02751; MARS-S2L (Nature Comms 2024) https://www.nature.com/articles/s41467-024-47754-y .
- Super-resolution: Real-ESRGAN / ESRGAN (OpenGeoAI `esrgan` example).

GRACE-2 infra cross-refs: `infra/aws-batch/main.tf` (CPU SPOT "optimal" CE,
scale-to-zero), `infra/aws-batch/{modflow,swmm,sfincs,...}.tf` (VCPU/MEMORY-only
job defs, no GPU), existing tools `services/agent/src/grace2_agent/tools/`
(`fetch_field_boundaries`, `compute_impervious_surface`, `extract_landcover_class`).
