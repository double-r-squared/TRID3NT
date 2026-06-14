"""Tool-call telemetry writer (B-tel / Wave 4.10 + Wave 4.11 M3).

Wave 4.10: emits one JSON-line per LLM-initiated or workflow-initiated tool
call to a local JSONL file.

Wave 4.11 M3: swaps the backend for MongoDB MCP when the Persistence singleton
is bound (``GRACE2_MONGO_MCP_STDIO=1``).  Falls back to the local-file path
when Persistence is unbound (dev / CI without Atlas).

Write path is fire-and-forget: ``emit_tool_call_event`` schedules an async
write task and returns immediately.  A write failure is logged at WARNING level
but never raised — telemetry must never break the tool-dispatch loop.

Configuration:
    ``GRACE2_TELEMETRY_PATH`` env var overrides the default output path for the
    local-file fallback.  Default: ``/tmp/grace2_tool_call_telemetry.jsonl``

Record shape (one JSON object per line, newline-terminated — local-file path):
    {
        "session_id":                  str,
        "ts":                          str  (ISO-8601 UTC, e.g. "2026-06-09T...Z"),
        "tool_name":                   str,
        "source":                      "llm" | "workflow" | "manual",
        "args_hash":                   str  (hex digest of SHA-256 of JSON-encoded args),
        "success":                     bool,
        "latency_ms":                  float,
        "error_code":                  str | null,
        "retry_attempt":               int   (0 for first call),
        "cached_content_token_count":  int | null,
    }

MongoDB record shape (tool_call_telemetry collection — MCP-backed path):
    Maps 1:1 to ``ToolCallTelemetryDocument`` from
    ``grace2_contracts.mongo_collections``.  Key differences from the local
    file path:
    - ``_id`` is a ULID (time-sortable; generated on write).
    - ``called_at_utc`` is a UTC datetime (the TTL index field; 90-day expiry).
    - ``result_ok`` replaces ``success`` (BSON-friendlier naming).
    - ``session_id`` / ``tool_name`` / ``source`` / ``args_hash`` /
      ``result_ok`` / ``latency_ms`` / ``error_code`` / ``retry_attempt`` /
      ``cached_content_token_count`` map directly from the call args.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .persistence import Persistence

logger = logging.getLogger("grace2_agent.telemetry")

_DEFAULT_TELEMETRY_PATH = "/tmp/grace2_tool_call_telemetry.jsonl"


def get_persistence() -> "Persistence | None":
    """Lazy wrapper around ``server.get_persistence``.

    Defined at module level so tests can patch
    ``grace2_agent.telemetry.get_persistence`` without reaching into the
    server module.  The deferred import avoids a circular dependency at import
    time (server.py already imports from telemetry at the top level).

    Returns ``None`` if the server module hasn't finished bootstrapping yet
    (early startup) or if the Persistence singleton is unbound (M1 path).
    """
    try:
        from .server import get_persistence as _server_get_persistence
        return _server_get_persistence()
    except Exception:  # noqa: BLE001
        return None


def _get_telemetry_path() -> str:
    """Return the JSONL output path from env, falling back to the default."""
    return os.environ.get("GRACE2_TELEMETRY_PATH", _DEFAULT_TELEMETRY_PATH)


def _hash_args(args: dict | None) -> str:
    """Return a hex-digest SHA-256 of the JSON-serialized args dict.

    Provides a stable fingerprint for dedup and tracing without storing the
    full (potentially large) args blob in the telemetry log.  Returns the
    digest of ``{}`` when ``args`` is ``None``.
    """
    payload = json.dumps(args or {}, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


async def _write_line(path: str, record: dict) -> None:
    """Append one JSON-line to ``path``.

    Uses ``aiofiles`` when available (best practice for async file I/O) and
    falls back to a blocking ``open()`` + ``asyncio.get_event_loop().
    run_in_executor`` otherwise.  The fallback ensures the module works even
    if ``aiofiles`` is not installed (it is NOT in the pyproject deps; the
    executor path is the safe default until it is added).

    Never raises — any I/O error is logged at WARNING.
    """
    line = json.dumps(record, default=str) + "\n"
    try:
        aiofiles = None
        try:
            import aiofiles as _aiofiles  # type: ignore[import-not-found]
            aiofiles = _aiofiles
        except ImportError:
            pass

        if aiofiles is not None:
            async with aiofiles.open(path, mode="a", encoding="utf-8") as fh:
                await fh.write(line)
        else:
            # Fallback: blocking write via executor so the event loop is not
            # starved on slow filesystems (e.g. NFS mounts in CI).
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, _blocking_append, path, line
            )
    except Exception:  # noqa: BLE001 — telemetry must never break the call loop
        logger.warning(
            "telemetry write failed path=%s tool=%s",
            path,
            record.get("tool_name", "?"),
            exc_info=True,
        )


def _blocking_append(path: str, line: str) -> None:
    """Blocking file append — called from an executor thread only."""
    with open(path, mode="a", encoding="utf-8") as fh:
        fh.write(line)


async def _write_to_mongo(
    persistence: "Persistence",
    session_id: str,
    ts: str,
    tool_name: str,
    source: Literal["llm", "workflow", "manual"],
    args_hash: str,
    success: bool,
    latency_ms: float,
    error_code: str | None,
    retry_attempt: int,
    cached_content_token_count: int | None,
) -> None:
    """Emit one tool-call telemetry record to MongoDB via the MCP Persistence.

    Constructs a ``ToolCallTelemetryDocument``, validates it against the schema,
    and calls ``insert-one`` via the Persistence singleton's underlying MCP
    client.  Telemetry insert is done directly on the MCP client (bypassing the
    typed Persistence methods, which own Case/User/Secret shapes) using the
    ``tool_call_telemetry`` collection name from the contracts constant.

    Never raises — any Persistence failure is logged at WARNING.
    """
    try:
        from grace2_contracts import new_ulid
        from grace2_contracts.mongo_collections import (
            TELEMETRY_COLLECTION,
            ToolCallTelemetryDocument,
        )
        from .persistence import DEFAULT_DATABASE

        # Parse ts string to datetime for called_at_utc.  Accepts ISO-8601 with
        # trailing Z (e.g. "2026-06-09T12:34:56.789Z") or offset-aware strings.
        called_at: datetime
        if isinstance(ts, str):
            normalized = ts.replace("Z", "+00:00")
            called_at = datetime.fromisoformat(normalized)
        else:
            called_at = ts  # type: ignore[assignment]
        if called_at.tzinfo is None:
            called_at = called_at.replace(tzinfo=timezone.utc)

        doc = ToolCallTelemetryDocument(
            _id=new_ulid(),
            session_id=session_id,
            tool_name=tool_name,
            called_at_utc=called_at,
            source=source,
            args_hash=args_hash,
            result_ok=success,
            latency_ms=latency_ms,
            error_code=error_code,
            retry_attempt=retry_attempt,
            cached_content_token_count=cached_content_token_count,
        )

        body = doc.model_dump(mode="json", by_alias=True)

        await persistence._mcp.call_tool(
            "insert-one",
            {
                "database": DEFAULT_DATABASE,
                "collection": TELEMETRY_COLLECTION,
                "document": body,
            },
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the call loop
        logger.warning(
            "telemetry mongo write failed tool=%s session=%s",
            tool_name,
            session_id,
            exc_info=True,
        )


async def emit_tool_call_event(
    session_id: str,
    ts: str,
    tool_name: str,
    source: Literal["llm", "workflow", "manual"],
    args_hash: str,
    success: bool,
    latency_ms: float,
    error_code: str | None = None,
    retry_attempt: int = 0,
    cached_content_token_count: int | None = None,
) -> None:
    """Emit one tool-call telemetry record (non-blocking).

    The write is scheduled as a fire-and-forget asyncio task.  The caller
    does NOT await completion — latency impact on the tool-dispatch loop is
    bounded by the time to enqueue the task (microseconds), not the actual
    I/O.

    Backend selection:
    - When the app-level ``Persistence`` singleton (from ``server.get_persistence``)
      is bound (i.e. ``GRACE2_MONGO_MCP_STDIO=1`` or dev-file mode), the record
      is written to the ``tool_call_telemetry`` MongoDB collection via MCP.
    - When ``Persistence`` is unbound (M1 in-memory / CI without Atlas), the
      record falls back to the local-file JSONL path (``GRACE2_TELEMETRY_PATH``
      or the default ``/tmp/grace2_tool_call_telemetry.jsonl``).

    Args:
        session_id: WebSocket session identifier (ULID string).
        ts: ISO-8601 UTC timestamp of the tool call start (e.g.
            ``"2026-06-09T12:34:56.789Z"``).  Callers should pass
            ``grace2_contracts.now_utc().isoformat()`` or equivalent.
        tool_name: Registered tool name (e.g. ``"fetch_dem"``).
        source: Where the call originated.
            - ``"llm"`` — Gemini-initiated ``function_call`` in the multi-turn
              loop (``_stream_gemini_reply``).
            - ``"workflow"`` — inside-composer dispatch (future; reserved for
              Wave 4.11+ workflow orchestration paths).
            - ``"manual"`` — ``/invoke`` directive from the debug harness or
              a test fixture.
        args_hash: Hex digest of SHA-256 over the JSON-serialized args dict.
            Use ``telemetry.compute_args_hash(args)`` to build this.
        success: ``True`` when the tool returned without raising; ``False``
            when ``dispatch_error`` was set in the call loop.
        latency_ms: Wall-clock elapsed time from dispatch to result, in
            milliseconds (float precision).
        error_code: A.6 / FR-AS-11 error code string when ``success=False``;
            ``None`` on success or when unavailable.
        retry_attempt: Zero-based retry counter.  ``0`` for the first (or
            only) attempt; ``1`` for the first retry, etc.
        cached_content_token_count: Gemini ``UsageMetadata.
            cached_content_token_count`` from the response that triggered
            this call.  ``None`` when the field is absent or the stream did
            not report usage metadata (e.g. mid-stream chunks).
    """
    # Resolve the Persistence singleton via the module-level lazy wrapper.
    # That wrapper defers the import of server.py to avoid a circular import
    # at module load time.  Tests can patch ``grace2_agent.telemetry.
    # get_persistence`` to inject a mock without touching the server module.
    # We defensively catch any exception from get_persistence() itself so that
    # failures during early startup (e.g. ImportError) always fall through
    # to the local-file path rather than propagating.
    try:
        persistence: "Persistence | None" = get_persistence()
    except Exception:  # noqa: BLE001
        persistence = None

    if persistence is not None:
        # MCP-backed path: fire-and-forget to Mongo.
        asyncio.ensure_future(
            _write_to_mongo(
                persistence=persistence,
                session_id=session_id,
                ts=ts,
                tool_name=tool_name,
                source=source,
                args_hash=args_hash,
                success=success,
                latency_ms=latency_ms,
                error_code=error_code,
                retry_attempt=retry_attempt,
                cached_content_token_count=cached_content_token_count,
            )
        )
        return

    # Local-file fallback (v0 path — preserved for backward compat).
    record: dict = {
        "session_id": session_id,
        "ts": ts,
        "tool_name": tool_name,
        "source": source,
        "args_hash": args_hash,
        "success": success,
        "latency_ms": latency_ms,
        "error_code": error_code,
        "retry_attempt": retry_attempt,
        "cached_content_token_count": cached_content_token_count,
    }
    path = _get_telemetry_path()
    # Fire-and-forget: the event loop schedules the write; we do not await it.
    asyncio.ensure_future(_write_line(path, record))


def compute_args_hash(args: dict | None) -> str:
    """Public helper — compute the SHA-256 hex digest for a tool's args dict.

    Callers in ``server.py`` should use this rather than re-implementing the
    digest logic.  Safe to call from sync contexts (no I/O).
    """
    return _hash_args(args)


__all__ = [
    "emit_tool_call_event",
    "compute_args_hash",
]
