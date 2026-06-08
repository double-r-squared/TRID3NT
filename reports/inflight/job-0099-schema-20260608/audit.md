# Audit: Case schema (FR-MP-6) + Case-persistence envelopes

**Job ID:** job-0099-schema-20260608, **Sprint:** sprint-12-mega Wave 1 (PRIORITY-FIRST), **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** schema

**Required reads:**
- `docs/srs/03-functional-requirements.md` FR-MP-6 (existing v0.3.21 amendment)
- `packages/contracts/src/grace2_contracts/execution.py` — existing LayerURI / session-state envelope
- `packages/contracts/src/grace2_contracts/__init__.py` — module-level exports
- `services/agent/src/grace2_agent/main.py` — see how envelopes are consumed

### Scope

**THIS JOB MUST LAND FIRST in Wave 1.** Wave 2 jobs (Case UX agent, Case UX web) depend on this envelope shape.

NEW file `packages/contracts/src/grace2_contracts/case.py`:

```python
"""Case persistence envelope contracts (FR-MP-6)."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

CaseStatus = Literal["active", "archived", "deleted"]

@dataclass
class CaseSummary:
    """Top-level Case record (the "left rail" entity)."""
    case_id: str                # ULID
    title: str                  # user-edited
    created_at: str             # ISO-8601 UTC
    updated_at: str             # ISO-8601 UTC
    status: CaseStatus = "active"
    bbox: tuple[float, float, float, float] | None = None
    primary_hazard: str | None = None  # "flood" | "wildfire" | "groundwater" | etc.
    layer_summary: list[str] = field(default_factory=list)  # layer_ids
    qgs_project_uri: str | None = None  # gs://...{case_id}.qgs (lazy-init)

@dataclass
class CaseChatMessage:
    """Persisted single chat exchange in a Case session."""
    message_id: str             # ULID
    case_id: str
    role: Literal["user", "agent", "system"]
    content: str
    pipeline_id: str | None     # link to PipelineRecord if this turn dispatched one
    layer_emissions: list[str] = field(default_factory=list)  # layer_ids emitted this turn
    map_command_emissions: list[dict] = field(default_factory=list)  # zoom-to events
    created_at: str = ""

@dataclass
class CaseSessionState:
    """What rehydrates when a user opens a Case (replay envelope)."""
    case: CaseSummary
    chat_history: list[CaseChatMessage]
    loaded_layers: list[dict]   # session-state.loaded_layers shape (existing)
    pipeline_history: list[dict]
    current_pipeline: dict | None = None

# WebSocket envelopes for case lifecycle (A.6/A.7 amendments):
@dataclass
class CaseListEnvelope:
    """Server → client: list of all Cases (left rail)."""
    envelope_type: Literal["case-list"] = "case-list"
    cases: list[CaseSummary] = field(default_factory=list)

@dataclass
class CaseOpenEnvelope:
    """Server → client: rehydrate selected Case."""
    envelope_type: Literal["case-open"] = "case-open"
    session_state: CaseSessionState | None = None

@dataclass
class CaseCommandEnvelope:
    """Client → server: case lifecycle commands."""
    envelope_type: Literal["case-command"] = "case-command"
    command: Literal["create", "select", "rename", "archive", "delete"] = "create"
    case_id: str | None = None
    args: dict = field(default_factory=dict)
```

**Also**:
- Export the new types from `packages/contracts/src/grace2_contracts/__init__.py`
- Add a narrow SRS amendment to `docs/srs/03-functional-requirements.md` FR-MP-6 noting the envelope locked at v0.3.22 (1-paragraph addition; do NOT touch SRS_v0.3.md monolith — run `make srs` if available, else surface as OQ-99-SRS-MAKE-RERUN)

**Tests** (in packages/contracts/tests/):
- Round-trip serialization for each new dataclass via `dataclasses.asdict` + reconstruction
- ULID format validation
- ISO-8601 datetime validation
- envelope_type literal validation (refuses wrong values)

**Concurrency note**: this job touches `packages/contracts/src/grace2_contracts/__init__.py` which is shared with job-0100 (Secrets schema). Append imports idempotently — DO NOT remove existing imports.

### File ownership (exclusive)

- `packages/contracts/src/grace2_contracts/case.py` (NEW)
- `packages/contracts/src/grace2_contracts/__init__.py` — append imports (idempotent w/ job-0100)
- `packages/contracts/tests/test_case.py` (NEW)
- `docs/srs/03-functional-requirements.md` — narrow FR-MP-6 amendment
- `reports/inflight/job-0099-schema-20260608/`

### FROZEN

- All other `packages/contracts/*` (especially `execution.py`, `pipeline.py`, `secrets.py` — job-0100's)
- All `services/`, `web/`, `tools/` — implementation lands in Wave 2
- `docs/SRS_v0.3.md` monolith (regenerated only)
- `reports/complete/**`

### Acceptance

- [ ] Case dataclasses + envelopes round-trip serialize cleanly
- [ ] Exports in `__init__.py` (idempotent-append with sibling job-0100)
- [ ] `packages/contracts` test suite stays green
- [ ] FR-MP-6 SRS amendment narrow + 1-paragraph; surfaces OQ-99-SRS-MAKE-RERUN if can't `make srs` locally
- [ ] No FROZEN edits
- [ ] Single commit prefix `job-0099:`; co-author line


### Codified lesson (job-0086, do not violate)

URL/render consistency != geographic correctness. In-COG axis mirrors and similar in-file orientation bugs are invisible to every consistency check (server, client, PIL composite all faithfully serve the mirrored array). If your tool emits geometry, your acceptance test MUST verify the output against the **known geography of the bbox** (e.g. "is the deep-flood pixel at the river mouth?"), not just "did the bytes round-trip?".

