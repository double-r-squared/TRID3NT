# Audit: Mode 2 `.gov`/`.edu` offer-to-add classifier

**Job ID:** job-0101-agent-20260608, **Sprint:** sprint-12-mega Wave 1, **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** agent

**Required reads:**
- `services/agent/src/grace2_agent/agent.py` — main agent surface
- `services/agent/src/grace2_agent/catalog.py` (tools/catalog.py) — Mode 1 catalog reference
- `docs/srs/03-functional-requirements.md` Mode 2 section if present

### Scope

NEW file `services/agent/src/grace2_agent/mode2_classifier.py`:

```python
"""Mode 2 .gov/.edu offer-to-add classifier (FR-DS-* Mode 2).

When the agent fetches a web page (via web_fetch tool) AND the page is on a
.gov or .edu domain AND it carries patterns consistent with structured data
(API hints, data download links, JSON-LD), this classifier flags it as a
candidate "Mode 2 source" the user might want to formally add to the catalog.

The classifier itself is deterministic + cheap. It surfaces a candidate envelope
that the web client renders as a "offer to add: <Source>" modal."""
from dataclasses import dataclass
from typing import Literal

@dataclass
class Mode2Candidate:
    candidate_id: str
    url: str
    domain: str
    domain_tld: Literal["gov", "edu", "mil", "int", "other"]
    confidence: float  # 0-1
    detected_patterns: list[str]  # e.g. ["json-ld", "openapi-spec-link", "rest-endpoint-pattern"]
    title: str | None = None
    suggested_tool_kind: Literal["fetcher", "endpoint", "reference"] = "reference"
    snippet: str | None = None

def classify_for_mode2(page_dict: dict) -> Mode2Candidate | None:
    """Take a web_fetch dict, decide if it's a Mode 2 candidate.

    Decision rules (deterministic):
      1. domain MUST end in .gov, .edu, .mil, or .int → else None
      2. detect at least 1 structural pattern from page content:
         - json-ld script tag → "json-ld"
         - "/api/" or "/v1/" or "rest" in body → "rest-endpoint-pattern"
         - openapi.json / swagger link → "openapi-spec-link"
         - "Download CSV" / "Download GeoJSON" → "data-download-link"
         - tabular `<table>` with dataset rows → "tabular-data"
      3. confidence = 0.5 + 0.1 per pattern (cap 0.95)
      4. suggested_tool_kind:
         - has openapi-spec → "endpoint"
         - has data-download → "fetcher"  
         - else → "reference"
    """
```

**Also**: integrate into agent's web_fetch result processor (after the tool returns the dict, run `classify_for_mode2(result)` and if non-None, emit a `mode2-candidate` envelope to the websocket. ENVELOPE shape:

```python
# emit via pipeline_emitter (in services/agent/src/grace2_agent/agent.py):
@dataclass
class Mode2CandidateEnvelope:
    envelope_type: Literal["mode2-candidate"] = "mode2-candidate"
    candidate: Mode2Candidate
```

This envelope reaches the web UI; the web UI work to render the modal is sprint-12-mega Wave 2 or Wave 3 (separate job).

**Audit log**: every emitted candidate written to `~/.grace2/mode2_audit.log` (1 JSONL line per emission) — persistent across sessions for review.

**Tests** (≥6 unit):
- .com domain → None
- .gov with no structural patterns → None
- .gov + json-ld → Candidate, confidence ≥0.6
- .edu + data-download link → Candidate with suggested_tool_kind='fetcher'
- Confidence cap: 5 patterns → confidence 0.95
- Audit log appends correctly

**Live verification**: feed a mocked weather.gov page through web_fetch (sibling tool, mock if not yet landed) → classifier returns Mode2Candidate with confidence ≥0.6; evidence/mode2_live.txt

### File ownership (exclusive)

- `services/agent/src/grace2_agent/mode2_classifier.py` (NEW)
- `services/agent/src/grace2_agent/agent.py` — ADD: emit `mode2-candidate` envelope after web_fetch results (≤20 lines)
- `services/agent/tests/test_mode2_classifier.py` (NEW)
- `reports/inflight/job-0101-agent-20260608/`

### FROZEN

- All tools/* files (especially web_fetch.py — sibling job-0092)
- All workflows/*
- `services/workers/`, `packages/contracts/`, `web/`, `infra/`, `docs/srs/`, `reports/complete/**`

### Acceptance

- [ ] mode2_classifier.py registered + importable
- [ ] agent.py emits mode2-candidate after web_fetch returns a .gov/.edu page with patterns
- [ ] Audit log writes work
- [ ] ≥6 unit tests pass
- [ ] No FROZEN edits
- [ ] Single commit prefix `job-0101:`; co-author line


### Codified lesson (job-0086, do not violate)

URL/render consistency != geographic correctness. In-COG axis mirrors and similar in-file orientation bugs are invisible to every consistency check (server, client, PIL composite all faithfully serve the mirrored array). If your tool emits geometry, your acceptance test MUST verify the output against the **known geography of the bbox** (e.g. "is the deep-flood pixel at the river mouth?"), not just "did the bytes round-trip?".

