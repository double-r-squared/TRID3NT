"""Deterministic workflows that compose atomic tools (FR-TA-1, Decision G).

Per the SRS two-layer tool architecture (Decision G + FR-TA-1, §2.3 Engine
catalog), workflows are **orchestrator-style Python functions** that compose
the engine's atomic tools (defined under ``services/agent/src/grace2_agent/
tools/``) into deterministic chains.

Workflows are **not atomic tools** — they don't use ``@register_tool`` and they
don't have an ``AtomicToolMetadata`` of their own. The cache shim
(``tools/cache.py``) only mediates atomic-tool calls; workflows compose
already-cached + already-emitted atomic tools.

LLM exposure: a thin atomic-tool wrapper (``run_model_flood_scenario`` in
``model_flood_scenario.py``) lives in the registry so the LLM sees a single
invocable tool that triggers the workflow. The wrapper:

- declares ``cacheable=False`` + ``ttl_class="live-no-cache"`` +
  ``source_class="workflow_dispatch"`` (a new FR-DC-6 source class for the
  workflow exposure surface — same shape as job-0041's ``solver_dispatch``);
- forwards its arguments verbatim to the workflow body;
- returns the workflow's ``AssessmentEnvelope`` shape directly.

Invariant 2 (Deterministic workflows): workflows are LLM-free, stable-signature
Python composing atomic tools in tested sequences. Same inputs → byte-identical
SFINCS deck per the HydroMT determinism cited in
``docs/decisions/oq-4-hydromt-depth.md`` §3.

Workflows authored under this package (job-0042 lands the M5 capstone):

- ``model_flood_scenario(bbox?, location_query?, event_id?, return_period_yr=100,
   duration_hr=24, compute_class="medium") → AssessmentEnvelope`` — composes
  geocode → fetch_dem → fetch_landcover → fetch_river_geometry →
  lookup_precip_return_period → build_sfincs_model → run_solver →
  wait_for_completion → postprocess_flood. The OQ-4 §4 Invariant-7 NLCD
  validation gate (``LULC_MAPPING_MISMATCH``) fires inside
  ``build_sfincs_model`` (``sfincs_builder.py``) before HydroMT's roughness
  component runs.

- ``model_flood_habitat_scenario(bbox, species_keys?, rainfall_event?,
   protected_area_designation?, place_clip_polygon_uri?, place_label?,
   *, pipeline_emitter?, project_id?, session_id?) → CaseOneResult`` —
  Case 1 higher-order composer (Everglades / Big Cypress / Apalachicola
  flood + habitat exposure). Sequences fetch_wdpa_protected_areas → per-species
  fetch_gbif_occurrences → model_flood_scenario → compute_zonal_statistics →
  (optional) clip_raster_to_polygon + clip_vector_to_polygon →
  deterministic case_summary_text. LLM exposure as
  ``run_model_flood_habitat_scenario`` (workflow_dispatch metadata).
"""

from __future__ import annotations

# Import the workflow modules so their @register_tool decorators fire at
# package import time and the LLM-facing wrappers land in TOOL_REGISTRY.
from . import model_flood_habitat_scenario as _model_flood_habitat_scenario  # noqa: F401
from . import model_flood_scenario as _model_flood_scenario  # noqa: F401
from . import model_news_event_ingest as _model_news_event_ingest  # noqa: F401  — job-0119 Case 2 composer

__all__: list[str] = []
