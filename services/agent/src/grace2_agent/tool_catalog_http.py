"""HTTP catalog endpoint (Wave 4.10 Stage 3 — job C1).

Exposes two read-only JSON endpoints:

- ``GET /api/tool-catalog`` — the agent's atomic-tool surface (Wave 4.10 C1).
- ``GET /api/telemetry/summary`` — aggregated routing-quality stats over the
  most recent 30 sessions, backing the Wave 4.11 M7 routing-quality
  dashboard (this module is the only HTTP seam — adding a second endpoint
  keeps the listener as a single asyncio TCP server).

Why a dedicated HTTP endpoint when the rest of the agent talks WebSockets?

- The catalog is a **discovery surface** for human users browsing what the
  agent can do. It is not part of the chat envelope contract (Appendix A) —
  it does not stream, does not maintain session state, and does not require
  an authenticated user. A plain HTTP GET is the right shape.
- The catalog payload is small (~71 tools × ~1.5 KB each ≈ 100 KB) and
  cacheable. Routing it through the WS path would couple a static catalog
  read to session lifecycle.

The endpoint runs on its own asyncio TCP listener (default port 8766;
override via ``GRACE2_AGENT_HTTP_PORT``). It is mounted as a sibling of the
WebSocket server in ``server.run_server``, NOT in its own process — single
process, single asyncio loop, no thread sharing.

Backed entirely by:
- ``grace2_agent.categories.CATEGORIES`` / ``PRIMARY_CATEGORY`` /
  ``SECONDARY_CATEGORIES`` — the 12 categories landed by job-B5.
- ``grace2_agent.tools.TOOL_REGISTRY`` — every registered tool's
  ``AtomicToolMetadata`` carries the MCP annotation hints
  (``read_only_hint``, ``open_world_hint``, ``destructive_hint``,
  ``idempotent_hint``) + ``supports_global_query`` +
  ``payload_mb_estimator_name``.
- ``data/tool_query_corpus.yaml`` — example sample-queries keyed by tool name.

CORS: ``Access-Control-Allow-Origin: *`` so the Vite dev server (5173) and
production builds on any origin can hit the endpoint without preflight
friction. The endpoint is read-only and unauthenticated; permissive CORS is
the correct posture.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("grace2_agent.tool_catalog_http")

__all__ = [
    "build_catalog_payload",
    "load_query_corpus",
    "serve_catalog_http",
    "build_telemetry_summary",
    "DEFAULT_HTTP_PORT",
]


DEFAULT_HTTP_PORT = 8766

# Module-level cache: loaded once on the first request, retained until the
# agent process restarts. Matches the "reset on agent restart" requirement
# in the C1 kickoff (no hot-reload semantics needed for an internal
# discovery endpoint).
_CORPUS_CACHE: dict[str, list[str]] | None = None
_PAYLOAD_CACHE: dict[str, Any] | None = None


def _default_corpus_path() -> Path:
    """Resolve ``data/tool_query_corpus.yaml`` under the package's ``data/`` dir.

    Mirrors the resolution logic in ``discover_dataset._default_corpus_path``
    so both consumers read the same file by default. Honours the
    ``GRACE2_TOOL_CORPUS_YAML`` env override for test/dev pinning.
    """
    env_path = os.environ.get("GRACE2_TOOL_CORPUS_YAML")
    if env_path:
        return Path(env_path).expanduser().resolve()
    here = Path(__file__).resolve()
    return here.parent / "data" / "tool_query_corpus.yaml"


def load_query_corpus(path: Path | None = None) -> dict[str, list[str]]:
    """Load + cache the synthetic example-query corpus YAML.

    Returns a mapping ``tool_name -> [sample_query, ...]``. Cached for the
    lifetime of the process; the cache reset is implicit on agent restart
    (process-level state, no persistence).

    Missing files / parse errors return an empty dict — the catalog still
    renders, just without sample queries. Failure to load the corpus must
    not block the discovery surface.
    """
    global _CORPUS_CACHE
    if _CORPUS_CACHE is not None:
        return _CORPUS_CACHE
    p = path if path is not None else _default_corpus_path()
    if not p.exists():
        logger.warning(
            "tool_catalog_http: corpus YAML missing at %s — catalog will "
            "render without sample queries",
            p,
        )
        _CORPUS_CACHE = {}
        return _CORPUS_CACHE
    try:
        with p.open() as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception(
            "tool_catalog_http: failed to parse corpus YAML at %s", p
        )
        _CORPUS_CACHE = {}
        return _CORPUS_CACHE
    if not isinstance(data, dict):
        _CORPUS_CACHE = {}
        return _CORPUS_CACHE
    parsed: dict[str, list[str]] = {}
    for k, v in data.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, list):
            parsed[k] = [str(q) for q in v if isinstance(q, str)]
    _CORPUS_CACHE = parsed
    logger.info(
        "tool_catalog_http: loaded %d tool query entries from %s",
        len(parsed),
        p,
    )
    return _CORPUS_CACHE


def _reset_caches_for_tests() -> None:
    """Drop module-level caches. ONLY for tests."""
    global _CORPUS_CACHE, _PAYLOAD_CACHE
    _CORPUS_CACHE = None
    _PAYLOAD_CACHE = None


def build_catalog_payload(
    *,
    corpus: dict[str, list[str]] | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Assemble the ``/api/tool-catalog`` JSON payload.

    Shape::

        {
          "categories": [
            {"id": "...", "name": "...", "description": "...", "tool_count": N},
            ...12...
          ],
          "tools": [
            {
              "name": "fetch_dem",
              "description": "...",        # first-line/short docstring
              "description_full": "...",   # full docstring
              "category_id": "terrain_elevation",
              "secondary_category_ids": [],
              "supports_global_query": false,
              "annotations": {
                "read_only_hint": true,
                "open_world_hint": true,
                "destructive_hint": false,
                "idempotent_hint": true
              },
              "estimate_payload_mb_default": null,
              "ttl_class": "static-30d",
              "source_class": "dem",
              "cacheable": true,
              "sample_queries": ["show me elevation data for the Grand Canyon", ...]
            },
            ...
          ]
        }

    A tool registered without a primary category falls back to
    ``geographic_primitives`` (the catch-all for platform plumbing). The
    full description carries the complete docstring so the UI can show a
    short snippet by default and let the user expand the entry for the
    full text.
    """
    # Import here to avoid an import cycle: categories.py imports from
    # ``tools``, ``tools`` imports submodules that register decorators.
    # Importing categories at module load time is fine, but we want the
    # payload to reflect whatever the registry holds AT BUILD TIME, so we
    # snapshot here.
    from .categories import (
        CATEGORIES,
        PRIMARY_CATEGORY,
        SECONDARY_CATEGORIES,
    )
    from .tools import TOOL_REGISTRY

    global _PAYLOAD_CACHE
    if use_cache and _PAYLOAD_CACHE is not None:
        return _PAYLOAD_CACHE

    corpus_map = corpus if corpus is not None else load_query_corpus()

    # First pass: build the tools list.
    tools_out: list[dict[str, Any]] = []
    for name in sorted(TOOL_REGISTRY.keys()):
        entry = TOOL_REGISTRY[name]
        meta = entry.metadata
        doc_full = (entry.fn.__doc__ or "").strip()
        description = _first_paragraph(doc_full)
        primary_cat = PRIMARY_CATEGORY.get(name, "geographic_primitives")
        secondaries = list(SECONDARY_CATEGORIES.get(name, ()))
        sample_queries = list(corpus_map.get(name, []))
        # Cap to 3 sample queries in the payload — the UI shows 2-3; sending
        # all 5-10 wastes bandwidth on a discovery surface.
        sample_queries = sample_queries[:3]
        tools_out.append(
            {
                "name": name,
                "description": description,
                "description_full": doc_full,
                "category_id": primary_cat,
                "secondary_category_ids": secondaries,
                "supports_global_query": bool(meta.supports_global_query),
                "annotations": {
                    "read_only_hint": bool(meta.read_only_hint),
                    "open_world_hint": bool(meta.open_world_hint),
                    "destructive_hint": bool(meta.destructive_hint),
                    "idempotent_hint": bool(meta.idempotent_hint),
                },
                "estimate_payload_mb_default": None,
                "ttl_class": str(meta.ttl_class),
                "source_class": meta.source_class,
                "cacheable": bool(meta.cacheable),
                "sample_queries": sample_queries,
            }
        )

    # Second pass: count tools per category. Counted from PRIMARY_CATEGORY +
    # SECONDARY_CATEGORIES so a cross-listed tool shows up in both. Tools
    # without an explicit primary category fall through to
    # ``geographic_primitives`` — match the per-tool fallback above.
    category_counts: dict[str, int] = {c.id: 0 for c in CATEGORIES}
    for name in TOOL_REGISTRY:
        primary = PRIMARY_CATEGORY.get(name, "geographic_primitives")
        if primary in category_counts:
            category_counts[primary] += 1
        for sec in SECONDARY_CATEGORIES.get(name, ()):
            if sec in category_counts:
                category_counts[sec] += 1

    categories_out = [
        {
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "tool_count": category_counts.get(c.id, 0),
        }
        for c in CATEGORIES
    ]

    payload = {"categories": categories_out, "tools": tools_out}
    if use_cache:
        _PAYLOAD_CACHE = payload
    return payload


def _first_paragraph(doc: str, *, max_chars: int = 400) -> str:
    """Return a short snippet from a docstring.

    Strategy: take the first non-empty line, then continue until a blank
    line OR ``max_chars`` is reached. The full docstring is also surfaced
    on the wire (``description_full``) so the UI can click-to-expand.
    """
    if not doc:
        return ""
    lines = doc.splitlines()
    out: list[str] = []
    started = False
    for line in lines:
        stripped = line.strip()
        if not started:
            if not stripped:
                continue
            started = True
        if started and not stripped:
            break
        out.append(stripped)
        if sum(len(s) + 1 for s in out) >= max_chars:
            break
    snippet = " ".join(out)
    if len(snippet) > max_chars:
        snippet = snippet[: max_chars - 1].rstrip() + "…"
    return snippet


# ---------------------------------------------------------------------------
# Telemetry summary (Wave 4.11 M7 — routing-quality dashboard backend).
# ---------------------------------------------------------------------------


_DEFAULT_TELEMETRY_PATH = "/tmp/grace2_tool_call_telemetry.jsonl"


def _get_telemetry_path() -> Path:
    """Resolve the JSONL fallback path (env override + default)."""
    return Path(
        os.environ.get("GRACE2_TELEMETRY_PATH", _DEFAULT_TELEMETRY_PATH)
    )


def _normalize_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Coerce a single telemetry record into the summary's canonical shape.

    The local-file (Wave 4.10) writer uses ``success`` + ``ts``; the MCP
    writer (Wave 4.11 M3) uses ``result_ok`` + ``called_at_utc``. We accept
    either form so the summary builder doesn't care which substrate
    produced the data.
    """
    out: dict[str, Any] = {}
    out["session_id"] = rec.get("session_id") or ""
    out["tool_name"] = rec.get("tool_name") or ""
    out["source"] = rec.get("source") or "llm"
    # Either ``success`` (local file) or ``result_ok`` (Mongo).
    if "result_ok" in rec:
        out["result_ok"] = bool(rec.get("result_ok"))
    else:
        out["result_ok"] = bool(rec.get("success", True))
    out["latency_ms"] = float(rec.get("latency_ms") or 0.0)
    out["error_code"] = rec.get("error_code")
    out["retry_attempt"] = int(rec.get("retry_attempt") or 0)
    out["cached_content_token_count"] = rec.get("cached_content_token_count")
    # Timestamp: prefer the Mongo field name; fall back to the file form.
    out["called_at_utc"] = rec.get("called_at_utc") or rec.get("ts") or ""
    return out


def _empty_summary() -> dict[str, Any]:
    """Return the zero-state summary shape (no telemetry recorded yet)."""
    return {
        "total_dispatches": 0,
        "session_count": 0,
        "error_rate_overall": 0.0,
        "cache_hit_rate": 0.0,
        "average_latency_ms": 0.0,
        "dispatches_by_tool": [],   # [{name, count, error_rate, avg_latency_ms}]
        "dispatches_by_source": {}, # {llm: int, workflow: int, manual: int}
        "error_rate_by_tool": [],   # [{name, error_rate, error_count, total}]
        "top_routing_chains": [],   # [{chain: [a, b], count}]
        "source": "empty",
    }


def _aggregate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the dashboard summary over a list of normalized records.

    Returns a JSON-serializable dict; called by both the MCP-backed and
    file-fallback code paths so the aggregation logic stays in one place.
    """
    if not records:
        return _empty_summary()

    total = len(records)
    # Sessions present
    sessions = {r["session_id"] for r in records if r["session_id"]}
    session_count = len(sessions)

    # Per-tool aggregation
    by_tool_count: dict[str, int] = {}
    by_tool_errors: dict[str, int] = {}
    by_tool_latency_sum: dict[str, float] = {}
    by_source_count: dict[str, int] = {}
    total_errors = 0
    total_latency = 0.0
    cache_hit_count = 0
    cache_total = 0

    for r in records:
        tool = r["tool_name"] or "unknown"
        by_tool_count[tool] = by_tool_count.get(tool, 0) + 1
        by_tool_latency_sum[tool] = (
            by_tool_latency_sum.get(tool, 0.0) + float(r["latency_ms"])
        )
        if not r["result_ok"]:
            by_tool_errors[tool] = by_tool_errors.get(tool, 0) + 1
            total_errors += 1
        total_latency += float(r["latency_ms"])
        src = r["source"] or "llm"
        by_source_count[src] = by_source_count.get(src, 0) + 1
        # Cache hit rate: presence of a non-zero cached_content_token_count
        # treated as a "cache hit" since the Gemini SDK reports the cached
        # token count when the cached content path engaged.
        cct = r.get("cached_content_token_count")
        if cct is not None:
            cache_total += 1
            if isinstance(cct, (int, float)) and cct > 0:
                cache_hit_count += 1

    by_tool_sorted: list[dict[str, Any]] = []
    error_rate_by_tool: list[dict[str, Any]] = []
    for tool, cnt in sorted(by_tool_count.items(), key=lambda kv: (-kv[1], kv[0])):
        errs = by_tool_errors.get(tool, 0)
        avg_latency = by_tool_latency_sum.get(tool, 0.0) / cnt if cnt else 0.0
        rate = (errs / cnt) if cnt else 0.0
        by_tool_sorted.append(
            {
                "name": tool,
                "count": cnt,
                "error_count": errs,
                "error_rate": round(rate, 4),
                "avg_latency_ms": round(avg_latency, 2),
            }
        )
        error_rate_by_tool.append(
            {
                "name": tool,
                "error_rate": round(rate, 4),
                "error_count": errs,
                "total": cnt,
            }
        )

    # Routing chains: most common 2-tool sequences within a single session.
    # Group records by session_id then by their called_at_utc to walk pairs.
    chains: dict[tuple[str, str], int] = {}
    sess_buckets: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        sid = r["session_id"]
        if not sid:
            continue
        sess_buckets.setdefault(sid, []).append(r)
    for sid, recs in sess_buckets.items():
        # Sort by timestamp (ISO strings sort lexicographically when in UTC Z).
        recs_sorted = sorted(recs, key=lambda r: str(r.get("called_at_utc") or ""))
        for a, b in zip(recs_sorted[:-1], recs_sorted[1:]):
            ta = a.get("tool_name") or ""
            tb = b.get("tool_name") or ""
            if not ta or not tb or ta == tb:
                continue
            chains[(ta, tb)] = chains.get((ta, tb), 0) + 1
    top_chains = sorted(chains.items(), key=lambda kv: -kv[1])[:5]
    chains_out = [
        {"chain": [a, b], "count": cnt} for (a, b), cnt in top_chains
    ]

    error_rate_overall = (total_errors / total) if total else 0.0
    cache_hit_rate = (cache_hit_count / cache_total) if cache_total else 0.0
    avg_latency_ms = (total_latency / total) if total else 0.0

    return {
        "total_dispatches": total,
        "session_count": session_count,
        "error_rate_overall": round(error_rate_overall, 4),
        "cache_hit_rate": round(cache_hit_rate, 4),
        "average_latency_ms": round(avg_latency_ms, 2),
        "dispatches_by_tool": by_tool_sorted,
        "dispatches_by_source": by_source_count,
        "error_rate_by_tool": error_rate_by_tool,
        "top_routing_chains": chains_out,
        "source": "telemetry",
    }


def _load_recent_records_from_file(
    path: Path,
    *,
    last_n_sessions: int = 30,
) -> list[dict[str, Any]]:
    """Read the JSONL fallback file and return records from the most-recent
    ``last_n_sessions`` distinct sessions (newest first).

    Returns an empty list when the file is missing or unreadable — the
    dashboard renders an empty state in that case.
    """
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    out.append(_normalize_record(rec))
    except OSError:
        return []
    if not out:
        return out
    # Newest-first, then keep only records belonging to the last N sessions.
    out.sort(key=lambda r: str(r.get("called_at_utc") or ""), reverse=True)
    seen_sessions: list[str] = []
    keep: list[dict[str, Any]] = []
    for r in out:
        sid = r.get("session_id") or ""
        if sid and sid not in seen_sessions:
            if len(seen_sessions) >= last_n_sessions:
                break
            seen_sessions.append(sid)
        keep.append(r)
    return keep


async def _load_recent_records_from_mongo(
    persistence: Any,
    *,
    last_n_sessions: int = 30,
) -> list[dict[str, Any]]:
    """Query the ``tool_call_telemetry`` collection via the MCP client.

    Best-effort: any failure falls back to an empty list so the dashboard
    can still render the file-backed path or an empty state.
    """
    try:
        from grace2_contracts.mongo_collections import TELEMETRY_COLLECTION
        from .persistence import DEFAULT_DATABASE
    except Exception:  # noqa: BLE001
        return []
    try:
        # Fetch newest 2000 records, then narrow to the last N sessions.
        # The cap keeps a runaway collection from stalling the dashboard.
        raw = await persistence._mcp.call_tool(
            "find",
            {
                "database": DEFAULT_DATABASE,
                "collection": TELEMETRY_COLLECTION,
                "filter": {},
                "sort": {"called_at_utc": -1},
                "limit": 2000,
            },
        )
    except Exception:  # noqa: BLE001 — never break the dashboard on MCP error
        logger.warning("telemetry summary: mongo find failed", exc_info=True)
        return []
    # Unwrap the MCP result envelope (mirrors Persistence._unwrap_mcp_result).
    docs: Any = raw
    if isinstance(raw, dict):
        if "documents" in raw:
            docs = raw["documents"]
        elif "content" in raw and isinstance(raw["content"], list) and raw["content"]:
            first = raw["content"][0]
            if isinstance(first, dict) and isinstance(first.get("text"), str):
                try:
                    docs = json.loads(first["text"])
                except json.JSONDecodeError:
                    docs = []
    if isinstance(docs, dict):
        docs = [docs]
    if not isinstance(docs, list):
        return []
    normalized = [_normalize_record(d) for d in docs if isinstance(d, dict)]
    # Constrain to last N sessions.
    normalized.sort(key=lambda r: str(r.get("called_at_utc") or ""), reverse=True)
    seen_sessions: list[str] = []
    keep: list[dict[str, Any]] = []
    for r in normalized:
        sid = r.get("session_id") or ""
        if sid and sid not in seen_sessions:
            if len(seen_sessions) >= last_n_sessions:
                break
            seen_sessions.append(sid)
        keep.append(r)
    return keep


async def build_telemetry_summary(
    *,
    last_n_sessions: int = 30,
) -> dict[str, Any]:
    """Build the routing-quality summary served by /api/telemetry/summary.

    Routing order:

    1. If the Persistence singleton is bound, query the MongoDB
       ``tool_call_telemetry`` collection via MCP. If that returns records,
       we aggregate against them.
    2. Otherwise (or on MCP failure / empty), fall back to the
       ``/tmp/grace2_tool_call_telemetry.jsonl`` file written by the M3
       file-backed path.

    Returns the empty-summary shape (all-zero counts) if nothing is found.
    """
    persistence = None
    try:
        from .server import get_persistence as _server_get_persistence
        persistence = _server_get_persistence()
    except Exception:  # noqa: BLE001 — early-startup ImportError tolerated
        persistence = None

    records: list[dict[str, Any]] = []
    used_source = "empty"
    if persistence is not None:
        records = await _load_recent_records_from_mongo(
            persistence, last_n_sessions=last_n_sessions
        )
        if records:
            used_source = "mongo"
    if not records:
        records = _load_recent_records_from_file(
            _get_telemetry_path(), last_n_sessions=last_n_sessions
        )
        if records:
            used_source = "file"

    summary = _aggregate_records(records)
    summary["source"] = used_source
    return summary


# ---------------------------------------------------------------------------
# HTTP server (asyncio, stdlib only)
# ---------------------------------------------------------------------------


_HTTP_VERSION = b"HTTP/1.1"
_CRLF = b"\r\n"


def _format_response(
    status: int,
    body: bytes,
    *,
    content_type: str = "application/json; charset=utf-8",
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    """Assemble a minimal HTTP/1.1 response."""
    reason = {
        200: "OK",
        204: "No Content",
        400: "Bad Request",
        404: "Not Found",
        405: "Method Not Allowed",
        500: "Internal Server Error",
        502: "Bad Gateway",
    }.get(status, "OK")
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
        # CORS — see module docstring.
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Cache-Control": "no-cache",
        "Connection": "close",
    }
    if extra_headers:
        headers.update(extra_headers)
    header_lines = (
        _HTTP_VERSION
        + b" "
        + str(status).encode()
        + b" "
        + reason.encode()
        + _CRLF
    )
    for k, v in headers.items():
        header_lines += f"{k}: {v}".encode() + _CRLF
    return header_lines + _CRLF + body


async def _handle_http(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Handle one HTTP request.

    The wire-protocol implementation is intentionally minimal — we only need
    to serve GET ``/api/tool-catalog`` and respond to CORS preflights. Any
    other path returns 404; any other method returns 405. Body is read until
    Content-Length OR end-of-stream so a stray POST doesn't hang.
    """
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
    except asyncio.TimeoutError:
        writer.close()
        return
    if not request_line:
        writer.close()
        return
    try:
        method, path, _version = request_line.decode("ascii", "replace").split()
    except ValueError:
        body = _format_response(400, b'{"error":"bad request line"}')
        writer.write(body)
        await writer.drain()
        writer.close()
        return

    # Drain headers; we don't need them, but the socket must be advanced past
    # them before we close so the client sees our response cleanly.
    while True:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        except asyncio.TimeoutError:
            break
        if not line or line == b"\r\n" or line == b"\n":
            break

    if method == "OPTIONS":
        # CORS preflight.
        writer.write(_format_response(204, b""))
        await writer.drain()
        writer.close()
        return

    if method != "GET":
        writer.write(
            _format_response(405, b'{"error":"method not allowed"}')
        )
        await writer.drain()
        writer.close()
        return

    # job-0255: streaming WMS proxy. Handled BEFORE the buffered
    # ``_format_response`` paths because it writes a chunked/streamed response
    # directly to ``writer`` (whole tiles are never buffered in agent memory —
    # contract lens). Env-gated: when ``QGIS_PROXY_ENABLED`` is off (default),
    # the route is treated as absent and falls through to the 404 below, so
    # TODAY'S behavior is unchanged until job-0257 flips the flag in prod.
    proxy_path, _, proxy_qs = path.partition("?")
    if proxy_path == "/qgis-proxy":
        from .qgis_proxy import qgis_proxy_enabled

        if not qgis_proxy_enabled():
            # Route absent when disabled — 404 exactly like an unknown path.
            writer.write(_format_response(404, b'{"error":"not found"}'))
            await writer.drain()
            writer.close()
            return
        await _handle_qgis_proxy(proxy_qs, writer)
        # ``_handle_qgis_proxy`` owns draining + closing the writer.
        return

    if path == "/api/tool-catalog":
        try:
            payload = build_catalog_payload()
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            writer.write(_format_response(200, body))
        except Exception:  # noqa: BLE001
            logger.exception("tool-catalog payload build failed")
            writer.write(
                _format_response(500, b'{"error":"catalog build failed"}')
            )
    elif path == "/api/telemetry/summary":
        try:
            summary = await build_telemetry_summary()
            body = json.dumps(summary, separators=(",", ":")).encode("utf-8")
            writer.write(_format_response(200, body))
        except Exception:  # noqa: BLE001
            logger.exception("telemetry summary build failed")
            writer.write(
                _format_response(500, b'{"error":"telemetry summary failed"}')
            )
    elif path == "/api/health":
        writer.write(_format_response(200, b'{"ok":true}'))
    else:
        writer.write(_format_response(404, b'{"error":"not found"}'))
    await writer.drain()
    writer.close()


def _format_streaming_head(
    status: int,
    headers: dict[str, str],
) -> bytes:
    """Assemble the status line + headers for a STREAMED response (no body).

    Unlike ``_format_response`` (which knows the full body and sets a
    Content-Length), the proxy does not buffer the body — it relays chunks as
    they arrive. We forward the upstream's filtered headers (which include the
    upstream Content-Length / Content-Type for the tile), add permissive CORS
    so the browser can fetch tiles cross-origin, and force ``Connection: close``
    so the client knows the body ends at EOF even when the upstream omitted a
    Content-Length.
    """
    reason = {
        200: "OK",
        204: "No Content",
        206: "Partial Content",
        301: "Moved Permanently",
        302: "Found",
        304: "Not Modified",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        500: "Internal Server Error",
        502: "Bad Gateway",
        503: "Service Unavailable",
    }.get(status, "OK")
    out_headers: dict[str, str] = {}
    # Upstream's relayable headers first (Content-Type/Length/Cache etc.).
    out_headers.update(headers)
    # CORS — WMS tiles are images, not credentialed data; permissive origin is
    # the correct posture (matches the catalog endpoint above).
    out_headers["Access-Control-Allow-Origin"] = "*"
    out_headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    out_headers["Access-Control-Allow-Headers"] = "Content-Type"
    out_headers["Connection"] = "close"
    head = (
        _HTTP_VERSION
        + b" "
        + str(status).encode()
        + b" "
        + reason.encode()
        + _CRLF
    )
    for k, v in out_headers.items():
        head += f"{k}: {v}".encode() + _CRLF
    return head + _CRLF


async def _handle_qgis_proxy(
    query_string: str,
    writer: asyncio.StreamWriter,
) -> None:
    """Stream a QGIS Server WMS response to ``writer`` (job-0255).

    Bridges the proxy module's ``stream_qgis_response`` to the raw asyncio
    stream writer: writes the status line + filtered headers when the upstream
    responds, then relays each body chunk as it arrives. Owns draining +
    closing the writer in all paths (success, upstream-unreachable 502, error).
    """
    from .qgis_proxy import ProxyResult, stream_qgis_response

    head_written = False

    async def _write_head(result: "ProxyResult") -> None:
        nonlocal head_written
        writer.write(_format_streaming_head(result.status, result.headers))
        await writer.drain()
        head_written = True

    async def _write_chunk(chunk: bytes) -> None:
        writer.write(chunk)
        await writer.drain()

    try:
        await stream_qgis_response(query_string, _write_head, _write_chunk)
    except Exception:  # noqa: BLE001 — upstream unreachable / transport error
        logger.warning("qgis-proxy: upstream relay failed", exc_info=True)
        if not head_written:
            # No bytes on the wire yet — we can still send an honest 502.
            writer.write(_format_response(502, b'{"error":"qgis upstream unreachable"}'))
    finally:
        try:
            await writer.drain()
        except Exception:  # noqa: BLE001
            pass
        writer.close()


async def serve_catalog_http(
    host: str = "127.0.0.1",
    port: int | None = None,
) -> asyncio.AbstractServer:
    """Start the catalog HTTP listener and return the server handle.

    Designed to be mounted alongside the WebSocket server in
    ``server.run_server`` — same asyncio loop, single process, no threads.

    Reads ``GRACE2_AGENT_HTTP_PORT`` if ``port`` is not passed (default
    ``DEFAULT_HTTP_PORT``).
    """
    if port is None:
        try:
            port = int(os.environ.get("GRACE2_AGENT_HTTP_PORT", DEFAULT_HTTP_PORT))
        except ValueError:
            port = DEFAULT_HTTP_PORT
    server = await asyncio.start_server(_handle_http, host, port)
    logger.info(
        "tool-catalog HTTP server listening host=%s port=%d", host, port
    )
    return server
