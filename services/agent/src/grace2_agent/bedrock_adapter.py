"""Bedrock Converse adapter (sprint-14-aws job-0286) — the agent's AWS brain.

GRACE was built on Vertex AI / Gemini via ``adapter.py``. The AWS migration
swaps the model provider to **Amazon Bedrock** (Claude Sonnet 4.6 by default)
WITHOUT touching the multi-turn loop, the 57-tool catalog, the envelope
emission, or the web client. The seam is deliberately narrow:

  * This module accepts the SAME inputs ``adapter.stream_events_with_contents``
    accepts — a ``list[genai_types.Content]`` history + a list of
    ``genai_types.FunctionDeclaration`` tool specs + a system prompt — and
    converts them to the Bedrock Converse shapes at the boundary.
  * It yields the SAME ``StreamEvent`` union (``TextDeltaEvent`` /
    ``FunctionCallEvent`` / ``UsageMetadataEvent``) the Gemini path yields, so
    ``server.py``'s dispatch loop, ``categories.validate_function_call``, the
    PipelineEmitter, and the cache-status telemetry all work unchanged.

Provider selection is ``MODEL_PROVIDER`` (``vertex`` default; ``bedrock`` to
engage this path). ``adapter.stream_events_with_contents`` branches here when
the flag is ``bedrock`` — see that function. The Gemini ``cached_content``
fast-path does not apply (Bedrock has its own ``cachePoint`` prompt-caching;
deferred to a follow-up) — ``cached_content_name`` is ignored here.

Keeping the genai types as the internal lingua franca means the migration is
reversible and the Gemini path stays bit-for-bit intact while Bedrock is
proven. A later job can drop the genai dependency entirely once Bedrock parity
is verified end-to-end.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from google.genai import types as genai_types

from .adapter import (
    FunctionCallEvent,
    StreamEvent,
    TextDeltaEvent,
    UsageMetadataEvent,
)

logger = logging.getLogger("grace2_agent.bedrock_adapter")

# Cross-region inference profile for Claude Sonnet 4.6 (confirmed accessible in
# the target account). Override via ``BEDROCK_MODEL_ID``. The ``us.`` prefix is
# the inference-profile id required for on-demand throughput on Claude 4.x.
BEDROCK_DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"

# Match the Gemini per-request config (adapter.py:GenerateContentConfig).
_DEFAULT_TEMPERATURE = 0.7
_DEFAULT_MAX_TOKENS = 8192


def model_provider() -> str:
    """Resolve the active model provider (``vertex`` default, ``bedrock`` opt-in).

    Read at call time so a Cloud Run / ECS env injection (or a local-run
    ``MODEL_PROVIDER=bedrock``) takes effect without re-import.
    """
    return (os.environ.get("MODEL_PROVIDER") or "vertex").strip().lower()


def bedrock_model_id() -> str:
    return os.environ.get("BEDROCK_MODEL_ID", BEDROCK_DEFAULT_MODEL)


def _prompt_cache_enabled() -> bool:
    """Bedrock prompt caching (``cachePoint``) ON by default; env off-switch.

    The sprint-14 Gemini->Bedrock swap DEFERRED prompt caching, so every turn
    re-sent the full static system prompt + 94-tool catalog UNCACHED — the #1
    Bedrock cost driver (the Gemini path had cachedContent ~90% discount). We
    restore it with ``cachePoint`` markers. Gated by ``BEDROCK_PROMPT_CACHE`` so
    ops can disable without a redeploy if a model ever rejects cachePoint blocks.
    """
    return (
        os.environ.get("BEDROCK_PROMPT_CACHE", "1").strip().lower()
        not in {"0", "false", "no", "off"}
    )


def _build_converse_kwargs(
    contents: Any,
    tool_declarations: Any,
    system_prompt: str | None,
    model: str | None,
) -> dict[str, Any]:
    """Build the boto3 ``converse_stream`` kwargs (pure — unit-testable).

    Inserts Bedrock ``cachePoint`` markers (when enabled) at the END of the
    system block AND the tool list. Caching is PREFIX-based: for Anthropic models
    the cacheable prefix order is tools -> system -> messages, so the tool-catalog
    cachePoint caches the large static 94-tool block independently, and the system
    cachePoint additionally caches the system prefix when it is stable. A miss is
    a normal uncached call (no correctness risk).
    """
    model_id = model or bedrock_model_id()
    _system_unused, messages = contents_to_bedrock_messages(contents)
    system_blocks: list[dict[str, Any]] = (
        [{"text": system_prompt}] if system_prompt else []
    )
    tools = tool_declarations_to_bedrock_tools(tool_declarations)
    cache = _prompt_cache_enabled()

    if system_blocks and cache:
        system_blocks = [*system_blocks, {"cachePoint": {"type": "default"}}]

    kwargs: dict[str, Any] = {
        "modelId": model_id,
        "messages": messages,
        "inferenceConfig": {
            "temperature": _DEFAULT_TEMPERATURE,
            "maxTokens": _DEFAULT_MAX_TOKENS,
        },
    }
    if system_blocks:
        kwargs["system"] = system_blocks
    if tools:
        tool_list = (
            [*tools, {"cachePoint": {"type": "default"}}] if cache else tools
        )
        kwargs["toolConfig"] = {"tools": tool_list, "toolChoice": {"auto": {}}}
    return kwargs


def _bedrock_client():
    """Build a ``bedrock-runtime`` client. boto3 resolves creds + region from
    the standard chain (env / ~/.aws / instance role). ``AWS_REGION`` wins."""
    import boto3  # local import: keeps boto3 optional for the Vertex path

    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-west-2"
    )
    return boto3.client("bedrock-runtime", region_name=region)


# --------------------------------------------------------------------------- #
# Tool-spec conversion: genai FunctionDeclaration -> Bedrock toolConfig
# --------------------------------------------------------------------------- #

# genai Schema ``type`` is an uppercase enum (STRING/OBJECT/...); JSON Schema
# (what Bedrock's inputSchema.json wants) is lowercase.
_TYPE_MAP = {
    "STRING": "string",
    "NUMBER": "number",
    "INTEGER": "integer",
    "BOOLEAN": "boolean",
    "ARRAY": "array",
    "OBJECT": "object",
    "TYPE_UNSPECIFIED": "string",
}


def _genai_schema_to_json_schema(node: Any) -> dict[str, Any]:
    """Recursively convert a genai-dumped Schema dict to JSON Schema."""
    if not isinstance(node, dict):
        return {"type": "string"}
    out: dict[str, Any] = {}
    raw_type = node.get("type")
    if raw_type is not None:
        t = raw_type.value if hasattr(raw_type, "value") else str(raw_type)
        out["type"] = _TYPE_MAP.get(t.upper(), t.lower())
    if node.get("description"):
        out["description"] = node["description"]
    if node.get("enum"):
        out["enum"] = list(node["enum"])
    if node.get("format"):
        out["format"] = node["format"]
    props = node.get("properties")
    if isinstance(props, dict):
        out["properties"] = {
            k: _genai_schema_to_json_schema(v) for k, v in props.items()
        }
    items = node.get("items")
    if items is not None:
        out["items"] = _genai_schema_to_json_schema(items)
    if node.get("required"):
        out["required"] = list(node["required"])
    # Bedrock requires object schemas to at least declare type=object.
    if out.get("type") == "object" and "properties" not in out:
        out["properties"] = {}
    return out


def tool_declarations_to_bedrock_tools(
    tool_declarations: list[genai_types.FunctionDeclaration] | None,
) -> list[dict[str, Any]]:
    """Convert genai FunctionDeclarations to Bedrock ``tools[]`` (toolSpec)."""
    tools: list[dict[str, Any]] = []
    for decl in tool_declarations or []:
        dumped = decl.model_dump(mode="json", exclude_none=True)
        params = dumped.get("parameters")
        if params:
            schema = _genai_schema_to_json_schema(params)
        else:
            schema = {"type": "object", "properties": {}}
        if schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}
        tools.append(
            {
                "toolSpec": {
                    "name": dumped["name"],
                    "description": (dumped.get("description") or dumped["name"])[
                        :1000
                    ],
                    "inputSchema": {"json": schema},
                }
            }
        )
    return tools


# --------------------------------------------------------------------------- #
# History conversion: genai Content[] -> Bedrock messages[] + system[]
# --------------------------------------------------------------------------- #


def _coalesce(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive same-role messages — Bedrock rejects two assistant (or
    two user) messages in a row, but the codebase emits one Content per part
    (text turn + function_call turn are both ``model``)."""
    merged: list[dict[str, Any]] = []
    for m in messages:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"].extend(m["content"])
        else:
            merged.append({"role": m["role"], "content": list(m["content"])})
    return merged


def contents_to_bedrock_messages(
    contents: list[genai_types.Content],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert genai ``contents`` to ``(system_blocks, messages)``.

    genai roles ``user``/``model`` map to Bedrock ``user``/``assistant``.
    A function_call Part -> ``toolUse``; a function_response Part ->
    ``toolResult``. toolUse/toolResult ids must match across the pair; when
    the source call_id is None (legacy Gemini history) we synthesize a stable
    id and pair by arrival order.
    """
    system_blocks: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    pending_ids: deque[str] = deque()
    counter = 0

    def _next_id() -> str:
        nonlocal counter
        counter += 1
        return f"tooluse_{counter}"

    for content in contents:
        role = getattr(content, "role", "user") or "user"
        bedrock_role = "assistant" if role == "model" else "user"
        blocks: list[dict[str, Any]] = []
        for part in getattr(content, "parts", None) or []:
            fc = getattr(part, "function_call", None)
            fr = getattr(part, "function_response", None)
            text = getattr(part, "text", None)
            if fc is not None and getattr(fc, "name", None):
                tid = getattr(fc, "id", None) or _next_id()
                pending_ids.append(tid)
                blocks.append(
                    {
                        "toolUse": {
                            "toolUseId": tid,
                            "name": fc.name,
                            "input": dict(getattr(fc, "args", None) or {}),
                        }
                    }
                )
            elif fr is not None and getattr(fr, "name", None):
                tid = getattr(fr, "id", None) or (
                    pending_ids.popleft() if pending_ids else _next_id()
                )
                resp = getattr(fr, "response", None)
                if not isinstance(resp, dict):
                    resp = {"result": resp}
                blocks.append(
                    {
                        "toolResult": {
                            "toolUseId": tid,
                            "content": [{"json": resp}],
                        }
                    }
                )
            elif text:
                if bedrock_role == "user" or role == "user":
                    blocks.append({"text": text})
                else:
                    blocks.append({"text": text})
        if blocks:
            messages.append({"role": bedrock_role, "content": blocks})

    return system_blocks, _coalesce(messages)


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #


async def stream_bedrock(
    contents: list[genai_types.Content],
    tool_declarations: list[genai_types.FunctionDeclaration] | None = None,
    system_prompt: str | None = None,
    model: str | None = None,
) -> AsyncIterator[StreamEvent]:
    """Stream one Bedrock Converse turn, yielding the GRACE ``StreamEvent`` union.

    Mirrors ``adapter.stream_events_with_contents``: one call == one model
    round. The dispatch loop in ``server.py`` appends function_call +
    function_response Contents and re-calls until no tool calls remain.

    boto3's ``converse_stream`` is synchronous and returns an EventStream; we
    run it in an executor thread feeding an ``asyncio.Queue`` — exactly the
    producer/consumer pattern the Gemini path uses — so cancellation and
    back-pressure behave identically.
    """
    loop = asyncio.get_running_loop()
    # Bedrock prompt-caching restored here (job — bill fix): caches the static
    # system prompt + 94-tool catalog across turns via cachePoint markers.
    kwargs = _build_converse_kwargs(contents, tool_declarations, system_prompt, model)

    queue: asyncio.Queue[StreamEvent | None | BaseException] = asyncio.Queue()

    def _producer() -> None:
        try:
            client = _bedrock_client()
            resp = client.converse_stream(**kwargs)
            # Per-contentBlock accumulation of streamed toolUse input JSON.
            tool_blocks: dict[int, dict[str, Any]] = {}
            for event in resp["stream"]:
                if "contentBlockStart" in event:
                    start = event["contentBlockStart"]
                    idx = start.get("contentBlockIndex", 0)
                    tu = start.get("start", {}).get("toolUse")
                    if tu:
                        tool_blocks[idx] = {
                            "name": tu.get("name"),
                            "toolUseId": tu.get("toolUseId"),
                            "buf": "",
                        }
                elif "contentBlockDelta" in event:
                    d = event["contentBlockDelta"]
                    idx = d.get("contentBlockIndex", 0)
                    delta = d.get("delta", {})
                    if "text" in delta and delta["text"]:
                        loop.call_soon_threadsafe(
                            queue.put_nowait, TextDeltaEvent(delta=delta["text"])
                        )
                    elif "toolUse" in delta and idx in tool_blocks:
                        tool_blocks[idx]["buf"] += delta["toolUse"].get("input", "")
                elif "contentBlockStop" in event:
                    idx = event["contentBlockStop"].get("contentBlockIndex", 0)
                    tb = tool_blocks.pop(idx, None)
                    if tb is not None:
                        try:
                            args = json.loads(tb["buf"]) if tb["buf"] else {}
                        except json.JSONDecodeError:
                            args = {}
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            FunctionCallEvent(
                                name=tb["name"],
                                call_id=tb["toolUseId"],
                                args=args if isinstance(args, dict) else {},
                            ),
                        )
                elif "metadata" in event:
                    usage = event["metadata"].get("usage", {}) or {}
                    cached = usage.get("cacheReadInputTokens")
                    ev = UsageMetadataEvent(
                        cached_content_token_count=cached,
                        total_token_count=usage.get("totalTokens"),
                        prompt_token_count=usage.get("inputTokens"),
                        candidates_token_count=usage.get("outputTokens"),
                        cache_hit=bool(cached and cached > 0),
                    )
                    loop.call_soon_threadsafe(queue.put_nowait, ev)
            loop.call_soon_threadsafe(queue.put_nowait, None)
        except BaseException as exc:  # noqa: BLE001 — surface to caller
            loop.call_soon_threadsafe(queue.put_nowait, exc)

    producer_task = loop.run_in_executor(None, _producer)
    try:
        while True:
            item = await queue.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    except asyncio.CancelledError:
        producer_task.cancel()
        raise
