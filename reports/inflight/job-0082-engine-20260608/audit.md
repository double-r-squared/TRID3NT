# Audit: `compute_aspect` atomic tool (terrain face direction)

**Job ID:** job-0082-engine-20260608, **Sprint:** sprint-11 Stage 1 parallel, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites:** all in-flight 0079 (`compute_hillshade`) + 0080 (`compute_colored_relief`) + 0081 (`compute_slope`) — same shape (gdaldem subprocess + FR-DC cache + LayerURI return).

**SRS references:** FR-TA-2, FR-CE-8, FR-DC.

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_dem.py` — pattern reference
- `reports/inflight/job-0081-engine-20260608/audit.md` — sibling kickoff (`compute_slope`); mirror its shape directly
- `services/agent/src/grace2_agent/tools/cache.py` — read_through cache contract

### Scope

NEW file: `services/agent/src/grace2_agent/tools/compute_aspect.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="aspect",
)
def compute_aspect(
    dem_uri: str,
    algorithm: Literal["Horn", "ZevenbergenThorne"] = "Horn",
    zero_for_flat: bool = True,
) -> LayerURI:
    """Compute terrain aspect (face direction) from a DEM.

    Aspect is the compass direction the terrain faces (0-360°; 0=N, 90=E,
    180=S, 270=W; flat areas typically -9999 or 0). Wraps `gdaldem aspect`.

    Parameters:
        algorithm: Horn (GDAL default; 3×3 gradient) or ZevenbergenThorne
            (alternative, smoother on rough terrain).
        zero_for_flat: if True (default), flat areas get value 0; if False,
            flat areas get -9999 (gdaldem default).

    LLM guidance:
        - Pick this when user asks about solar exposure, fire/wind direction,
          landslide aspect preferences, or "which way slopes face".
        - Default algorithm = Horn; ZevenbergenThorne for noisy DEMs.
    """
```

**Implementation:** mirror `compute_slope` (job-0081) exactly. Subprocess `gdaldem aspect -alg <algorithm> [-zero_for_flat] <input> <output>`. Cache key on (dem_uri, algorithm, zero_for_flat). Same FR-DC pattern.

**Tests:** synthetic 32×32 DEM with known facing (e.g., a south-facing slope) → verify aspect ≈ 180. Both algorithms. zero_for_flat true/false.

**Register:** `tools/__init__.py` + `main.py` (1 line each). Confirm via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/compute_aspect.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line
- `services/agent/src/grace2_agent/main.py` — 1 line
- `services/agent/tests/test_compute_aspect.py` (NEW)
- `reports/inflight/job-0082-engine-20260608/`

### FROZEN

All other tools/* (including the concurrent compute_hillshade / compute_colored_relief / compute_slope / compute_zonal_statistics from 0083); all workflows/, services/workers/, packages/contracts/, web/, infra/, docs/srs/, styles/. `reports/complete/**`.

### Concurrency note

5 concurrent engine jobs (0079/0080/0081/0082/0083) each add 1 line to `main.py` + `tools/__init__.py`. If git surfaces textual conflict, append your line at end; orchestrator reconciles in close.

### Acceptance criteria

- [ ] `compute_aspect` registered + visible at `--startup-only`
- [ ] 4 parameter combos work (Horn + ZT × zero_for_flat true/false)
- [ ] Cache integration verified
- [ ] Live verification on a real DEM
- [ ] No FROZEN edits
- [ ] Single commit
