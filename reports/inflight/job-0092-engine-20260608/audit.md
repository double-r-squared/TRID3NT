# Audit: `web_fetch` atomic tool

**Job ID:** job-0092-engine-20260608, **Sprint:** sprint-12-mega Wave 1, **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/tools/data_fetch.py` — existing simple HTTP pattern
- `services/agent/src/grace2_agent/tools/cache.py` — read_through

### Scope

NEW file `services/agent/src/grace2_agent/tools/web_fetch.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="dynamic-1h",  # web pages change; agent should NOT over-cache
    source_class="web_fetch",
)
def web_fetch(
    url: str,
    extract: Literal["full_html", "main_text", "json", "metadata"] = "main_text",
    timeout_s: float = 30.0,
    user_agent: str = "grace2-agent/0.1 (research; contact: grace2-ops@local)",
) -> dict:
    """Generic web-page ingest with content extraction modes.

    Returns dict — NOT a LayerURI. Schema:
        {
          "url": str (final after redirects),
          "status_code": int,
          "fetched_at": ISO-8601 str,
          "extract_mode": str,
          "content": str | dict | None,
          "title": str | None,
          "lang": str | None,
          "content_length": int,
        }

    extract modes:
        "full_html"  — entire response.text
        "main_text"  — boilerplate-stripped readable text via BeautifulSoup + readability heuristic
        "json"       — parse response as JSON (fails typed if Content-Type isn't json-ish)
        "metadata"   — Open Graph + meta tags + title only (no body content)

    LLM guidance:
        - Prefer "main_text" for news articles
        - "metadata" is cheapest — use to confirm page subject before fetching body
        - "json" for API endpoints that aren't structured fetchers
    """
```

**Implementation**:
- Dependencies: `httpx` (already present), `beautifulsoup4`, `lxml`. Pre-flight: bs4 + lxml are NOT in `.venv-agent`; ADD to `services/agent/pyproject.toml` `[project] dependencies = ["beautifulsoup4>=4.12", "lxml>=5.0", ...]` and `.venv-agent/bin/pip install -e services/agent[dev]` to install before running tests. SURFACE THIS DEP ADD CLEARLY in the commit message + report (it's a real schema/infra concern).
- "main_text": Use BeautifulSoup with `lxml` parser → strip `<script>`, `<style>`, `<nav>`, `<header>`, `<footer>`, `<aside>` → extract `<main>` if present, else `<article>`, else `<body>` minus nav/footer
- "metadata": Extract `<title>`, `<meta name|property=*>` tags (especially `og:*`, `twitter:*`, `description`, `lang`)
- "json": `response.json()` after Content-Type check
- robots.txt: SKIP robots check for v0.1 (acceptable for research; surface OQ-92-ROBOTS-RESPECT for sprint-13)
- Cache key: SHA-256 of (url canonicalized, extract mode, user_agent) — DOES NOT include time, so the 1h TTL is the only freshness boundary
- Output: dict in cache as JSON blob; cache_prefix `cache/dynamic-1h/web_fetch/<hash>.json`
- Typed errors: `WebFetchUpstreamError(retryable=True)` for 5xx/timeout, `WebFetchInputError(retryable=False)` for bad URL

**Tests** (≥6 unit + 1 live, env-guarded):
- Mocked httpx: each of 4 extract modes against synthetic HTML
- BeautifulSoup boilerplate strip removes `<nav>` and `<script>`
- Cache miss + hit
- Bad URL (no scheme) → typed input error
- 5xx → typed upstream error
- Live (env GRACE2_TEST_LIVE_WEB=1): fetch `https://www.weather.gov/` metadata mode

**Live verification**: `web_fetch("https://www.weather.gov/", extract="metadata")` → real dict with title; evidence/web_fetch_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each. Verify via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/web_fetch.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line
- `services/agent/src/grace2_agent/main.py` — 1 line
- `services/agent/tests/test_web_fetch.py` (NEW)
- `services/agent/pyproject.toml` — add bs4 + lxml to dependencies (1-2 lines)
- `reports/inflight/job-0092-engine-20260608/`


### FROZEN

All other `tools/*` (each Wave 1 sibling has its own file ownership); all `workflows/`, `services/workers/`, `packages/contracts/`, `web/`, `infra/`, `docs/srs/`, `styles/`, `reports/complete/**`.


### Concurrency note (Wave 1 fan-out)

~15 Wave 1 jobs run concurrently. Each owns its own NEW tool file but ALL share `tools/__init__.py` + `main.py` registration sites. The idempotent-append pattern from sprint-11 Stage 1 (which handled 6 concurrent additions cleanly) applies: ADD your import line at the end of each file; if your line conflicts with a sibling's, do `git pull --rebase` style re-apply; do NOT remove other tool imports.


### Codified lesson (job-0086, do not violate)

URL/render consistency != geographic correctness. In-COG axis mirrors and similar in-file orientation bugs are invisible to every consistency check (server, client, PIL composite all faithfully serve the mirrored array). If your tool emits geometry, your acceptance test MUST verify the output against the **known geography of the bbox** (e.g. "is the deep-flood pixel at the river mouth?"), not just "did the bytes round-trip?".


### Acceptance criteria

- [ ] New tool registered + visible at `--startup-only` (count = entering_count + 1)
- [ ] ≥4 unit tests + 1 live test (with appropriate env-var guard)
- [ ] Live verification with real upstream response captured to evidence/
- [ ] Geography correctness check per the codified job-0086 lesson (where applicable)
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence paths + any OQs

