# Report: web_fetch atomic tool

**Job ID:** job-0092-engine-20260608
**Sprint:** sprint-12-mega Wave 1
**Specialist:** engine
**Task:** NEW atomic tool `web_fetch(url, extract, timeout_s, user_agent)` — generic
web-page ingest with four content-extraction modes (`full_html`, `main_text`,
`json`, `metadata`), `dynamic-1h` cache class, BeautifulSoup + lxml-driven
boilerplate strip, typed errors. Per the frozen audit.md kickoff at
`reports/inflight/job-0092-engine-20260608/audit.md`.
**Status:** ready-for-audit

## Summary

Landed a new atomic tool `web_fetch` in `services/agent/src/grace2_agent/tools/web_fetch.py`
that fetches an arbitrary http/https URL via `httpx`, runs one of four
extraction modes (`full_html`, `main_text`, `json`, `metadata`) over the
response, and returns a typed dict per the kickoff schema. The result is
cached in GCS at `cache/dynamic-1h/web_fetch/<hash>.json` via the existing
`read_through` shim; the cache key inputs are (canonicalized URL, extract
mode, user_agent). Registered into `tools/__init__.py` + `main.py`
idempotently alongside the Wave 1 siblings; added `beautifulsoup4` + `lxml`
to `services/agent/pyproject.toml`. 22 unit tests + 1 env-guarded live test
pass; tool-registry startup count rose from the sprint-11-close baseline 24 to
30 once Wave 1 settled (web_fetch is one of the 6 new tools registered
during Wave 1).

## Changes Made

- **`services/agent/src/grace2_agent/tools/web_fetch.py`** (NEW, ~330 lines):
  the tool body. Uses `httpx` for the HTTP client, `bs4.BeautifulSoup` +
  `lxml` for `main_text` / `metadata` extraction, and the project's
  `register_tool` / `read_through` for registry + cache wiring. Defines three
  typed errors (`WebFetchError` base, `WebFetchInputError` = not retryable,
  `WebFetchUpstreamError` = retryable). `_canonicalize_url` lowercases
  scheme + host, strips default ports, adds a trailing slash to root paths
  so the cache key is stable across cosmetic variants.
  `_extract_main_text` strips `<script>`, `<style>`, `<nav>`, `<header>`,
  `<footer>`, `<aside>`, `<noscript>` and preferentially extracts from
  `<main>` -> `<article>` -> `<body>`. `_extract_metadata` walks every
  `<meta name|property=*>` tag plus `<title>` and `<html lang>`.
  `_fetch_and_extract_bytes` is the cache-miss body the shim invokes — it
  returns the result dict serialized as JSON bytes so the cache-hit /
  cache-miss return paths are symmetric. The top-level `web_fetch` function
  is `@register_tool`-decorated with the `AtomicToolMetadata(name="web_fetch",
  ttl_class="dynamic-1h", source_class="web_fetch", cacheable=True)` payload
  from the kickoff. The docstring is FR-TA-3-complete with "Use this when" +
  "Do NOT use this for" sections.
- **`services/agent/src/grace2_agent/tools/__init__.py`** — appended
  `from . import web_fetch  # noqa: E402,F401 — job-0092` at the bottom
  alongside the parallel Wave 1 sibling imports (re-applied twice as
  siblings landed concurrently per the kickoff's idempotent-append guidance).
- **`services/agent/src/grace2_agent/main.py`** — appended
  `from .tools import web_fetch  # noqa: F401` inside
  `_import_tools_registry()`.
- **`services/agent/pyproject.toml`** — added `beautifulsoup4>=4.12,<5` and
  `lxml>=5.0,<7` to `[project] dependencies`. `lxml` was already present
  transitively via the rasterio chain; the explicit pin guards against
  silent dep-graph churn. `beautifulsoup4` is the genuinely-new dep.
- **`services/agent/tests/test_web_fetch.py`** (NEW, ~400 lines): 22 unit
  tests + 1 env-guarded (`GRACE2_TEST_LIVE_WEB=1`) live test. Coverage:
  registration + metadata; URL canonicalization happy + reject cases; each
  of four extraction modes against synthetic HTML; cache-miss writes /
  cache-hit skips fetcher; 5xx -> upstream error; 4xx -> input error;
  timeout -> upstream error; bad URL / bad timeout / unknown mode -> input
  error; `extract='json'` with non-JSON content-type -> input error.
- **`reports/inflight/job-0092-engine-20260608/evidence/web_fetch_live.txt`**
  — live-invocation transcript fetching `https://www.weather.gov/` in
  `metadata` mode.
- **`reports/inflight/job-0092-engine-20260608/evidence/startup_only.txt`**
  — `--startup-only` transcript showing 30-tool registry including
  `web_fetch`.

## Decisions Made

- **Cache the rendered dict, not raw HTML.** Each extract mode produces a
  distinct shape — caching the rendered dict makes the hit path trivial.
- **4xx -> WebFetchInputError; 5xx -> WebFetchUpstreamError.** The retryable
  flag drives FR-AS-11 routing; 404 at a stable URL won't get better on
  retry.
- **Skip robots.txt for v0.1; surface OQ-0092-WEB-FETCH-ROBOTS for
  sprint-13.** Matches the kickoff explicitly.
- **Cache key inputs = (canonical URL, extract, user_agent); time NOT in
  the key.** The `dynamic-1h` TTL boundary in `compute_cache_key` is the
  only freshness gate.
- **`extract='json'` Content-Type check.** Refuse to parse manifestly
  non-JSON bodies (image/png etc) — surfaces as `WebFetchInputError` rather
  than letting `json.JSONDecodeError` bubble up.

## Invariants Touched

- **1. Determinism boundary** — preserves. Typed dict with `url`,
  `status_code`, `fetched_at`, `extract_mode`, `content`, `title`, `lang`,
  `content_length`. No prose for the LLM to read numbers out of.
- **2. Deterministic workflows** — preserves. Atomic tool, not a workflow;
  pure Python, zero LLM in the loop.
- **6. Metadata-payload pattern** — preserves. Cache write goes through
  `read_through` to the existing `cache/<ttl-class>/<source-class>/<key>.<ext>`
  layout.
- **7. Claims carry provenance** — extends. `fetched_at` + final `url` +
  `status_code` give downstream tools (e.g. `extract_event_metadata`,
  `fetch_news_article`) the per-fetch provenance bag they need.

## Open Questions

- **OQ-0092-WEB-FETCH-ROBOTS** (sprint-13): per-host robots cache + allow
  check before HTTP GET. Disallow -> `WebFetchInputError("robots.txt
  disallows this path")`, not retryable. Tentative: respect robots unless
  `force_ignore_robots=True` explicitly passed.
- **OQ-0092-WEB-FETCH-DOMAIN-ALLOWLIST** (sprint-13): `engine.md` calls out
  a domain allowlist (NFR-S-4) this version does NOT implement. Tentative:
  env-var-driven `GRACE2_WEB_FETCH_ALLOWED_HOSTS` CSV; default unset = open
  (current behavior).
- **OQ-0092-WEB-FETCH-JS-RENDER**: current implementation does NOT execute
  JavaScript; SPA shells return empty `main_text`. The docstring's "Do NOT
  use this for" calls this out. Future: a `render_js=True` flag routing
  through a headless-browser tool.
- **OQ-0092-LIVE-TEST-ENV-COUPLING**: the live test requires
  `GOOGLE_CLOUD_PROJECT` set even though the response-shape assertion is
  what's interesting. A future revision could wrap the live test in a
  fake-storage fixture too. Tentative resolution: leave as-is; live test is
  env-guarded and the verification artifact is what the kickoff asked for.

## Dependencies and Impacts

- **Depends on:** job-0030 (`AtomicToolMetadata`), job-0031 (cache bucket +
  lifecycle), job-0032 (registry + `read_through`).
- **Affects:**
  - `agent` — `web_fetch` joins the ADK FunctionTool surface automatically
    via `register_with_adk`. Future `extract_event_metadata` (FR-HEP-1..9)
    will consume `web_fetch` outputs to build `EventMetadata.article_ids`
    provenance.
  - `engine` siblings — none in Wave 1; only shared touchpoints are
    `tools/__init__.py` + `main.py` registration appends (handled
    idempotently per kickoff concurrency note).
  - `infra` — cache bucket already exists; no new grants needed.

## Verification

### Tests run

- `pytest services/agent/tests/test_web_fetch.py` — **22 passed, 1 skipped**
  (live test is `GRACE2_TEST_LIVE_WEB`-gated).
- `pytest services/agent/tests/test_web_fetch.py
  services/agent/tests/test_main_startup.py
  services/agent/tests/test_tools_registry.py` — **31 passed, 1 skipped,
  4 deprecation warnings** (warnings are pre-existing ADK `BaseAgentConfig
  is deprecated` from a different test — unrelated to this job).
- Live test (`GRACE2_TEST_LIVE_WEB=1`): **1 passed** — successful fetch of
  `https://www.weather.gov/` in `metadata` mode, title=`National Weather
  Service`.

### Live E2E evidence

- **`--startup-only` transcript** (`evidence/startup_only.txt`): 30 tools
  registered, `web_fetch` in sorted position. Sprint-11-close baseline was
  24; Wave 1 added 6 (web_fetch + 5 siblings landed concurrently).
- **Live invocation transcript** (`evidence/web_fetch_live.txt`):
  `web_fetch("https://www.weather.gov/", extract="metadata")` returned
  status_code=200, content_length=122743, title="National Weather Service",
  extract_mode="metadata", 11 Dublin Core meta-tag keys. Shape exactly
  matches the kickoff-declared 8-field schema.

### Geography correctness check (job-0086 lesson)

N/A. `web_fetch` emits no geometry — it returns text / dict content
extracted from web pages, not COG/FlatGeobuf/GeoParquet layers. The
codified-lesson acceptance applies to "if your tool emits geometry"; this
tool deliberately does not (per the kickoff: "Returns dict — NOT a
LayerURI").

### Results

**pass.** All unit + live tests green; tool visible at `--startup-only`;
live-invocation transcript captured; cache write through the existing
`read_through` shim (verified via the fake-storage fixture's blob-store
inspection).
