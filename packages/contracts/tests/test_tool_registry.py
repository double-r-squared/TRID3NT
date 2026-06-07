"""Tests for ``AtomicToolMetadata`` (FR-DC-2, FR-CE-8, FR-AS-3).

job-0030-schema-20260606 (sprint-06 / M4 pre-flight). Verifies:
- All four TTL classes are accepted on a cacheable tool with a source_class.
- The ``live-no-cache`` class round-trips on an uncacheable tool (FR-DC-6).
- The cross-field ``model_validator`` rejects the two inconsistent combos.
- ``source_class`` is required when ``cacheable=True``.
- JSON serialize → deserialize → re-serialize is idempotent.
- ``extra="forbid"`` is inherited via ``GraceModel``.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from grace2_contracts.tool_registry import (
    TTL_CLASSES,
    AtomicToolMetadata,
)


# --- TTL class coverage --- #


@pytest.mark.parametrize(
    ("ttl_class", "cacheable", "source_class"),
    [
        ("static-30d", True, "dem"),
        ("semi-static-7d", True, "buildings"),
        ("dynamic-1h", True, "nwis_iv"),
        ("live-no-cache", False, None),
    ],
)
def test_atomic_tool_metadata_accepts_all_four_ttl_classes(
    ttl_class: str, cacheable: bool, source_class: str | None
) -> None:
    """FR-DC-2: each of the four TTL classes is a legal registration."""
    meta = AtomicToolMetadata(
        name=f"fetch_{ttl_class.replace('-', '_')}",
        ttl_class=ttl_class,  # type: ignore[arg-type]
        cacheable=cacheable,
        source_class=source_class,
    )
    assert meta.ttl_class == ttl_class
    assert meta.cacheable is cacheable
    assert meta.source_class == source_class


def test_ttl_classes_tuple_matches_literal_members() -> None:
    """The tuple form (used by the agent registry's known-class assertions)
    matches the four-member ``Literal``."""
    assert TTL_CLASSES == (
        "static-30d",
        "semi-static-7d",
        "dynamic-1h",
        "live-no-cache",
    )


# --- Cross-field validator (FR-DC-6 consistency rule) --- #


def test_atomic_tool_metadata_rejects_cacheable_with_live_no_cache() -> None:
    """cacheable=True + ttl_class='live-no-cache' is inconsistent (FR-DC-6)."""
    with pytest.raises(ValidationError) as exc_info:
        AtomicToolMetadata(
            name="fetch_x",
            ttl_class="live-no-cache",
            cacheable=True,
            source_class="x",
        )
    assert "live-no-cache" in str(exc_info.value)


def test_atomic_tool_metadata_rejects_uncacheable_with_static_class() -> None:
    """cacheable=False + ttl_class='static-30d' is inconsistent (FR-DC-6)."""
    with pytest.raises(ValidationError) as exc_info:
        AtomicToolMetadata(
            name="request_spatial_input",
            ttl_class="static-30d",
            cacheable=False,
        )
    assert "live-no-cache" in str(exc_info.value)


def test_atomic_tool_metadata_rejects_cacheable_without_source_class() -> None:
    """cacheable=True requires a non-empty ``source_class`` (FR-DC-1 bucket path)."""
    with pytest.raises(ValidationError) as exc_info:
        AtomicToolMetadata(
            name="fetch_dem",
            ttl_class="static-30d",
            cacheable=True,
            # source_class omitted
        )
    assert "source_class" in str(exc_info.value)

    # Empty string also rejected
    with pytest.raises(ValidationError):
        AtomicToolMetadata(
            name="fetch_dem",
            ttl_class="static-30d",
            cacheable=True,
            source_class="",
        )


def test_atomic_tool_metadata_uncacheable_omits_source_class() -> None:
    """FR-DC-6 uncacheable tool: source_class MAY be None / omitted."""
    meta = AtomicToolMetadata(
        name="request_spatial_input",
        ttl_class="live-no-cache",
        cacheable=False,
    )
    assert meta.source_class is None


# --- Defaults --- #


def test_atomic_tool_metadata_defaults_cacheable_true() -> None:
    """cacheable defaults to True because the cacheable case is the common case."""
    meta = AtomicToolMetadata(
        name="fetch_dem",
        ttl_class="static-30d",
        source_class="dem",
    )
    assert meta.cacheable is True


# --- Round-trip --- #


def test_atomic_tool_metadata_json_roundtrip_idempotent() -> None:
    """Round-trip through real JSON serialize/deserialize is idempotent."""
    meta = AtomicToolMetadata(
        name="fetch_buildings",
        ttl_class="static-30d",
        source_class="buildings",
        cacheable=True,
    )
    dumped_a = meta.model_dump(mode="json")
    text_a = json.dumps(dumped_a, sort_keys=True)
    meta_b = AtomicToolMetadata.model_validate(json.loads(text_a))
    dumped_b = meta_b.model_dump(mode="json")
    text_b = json.dumps(dumped_b, sort_keys=True)
    assert text_a == text_b


# --- extra=forbid inheritance --- #


def test_atomic_tool_metadata_forbids_extra_fields() -> None:
    """GraceModel sets ``extra='forbid'``; unknown fields are rejected."""
    with pytest.raises(ValidationError):
        AtomicToolMetadata.model_validate(
            {
                "name": "fetch_dem",
                "ttl_class": "static-30d",
                "source_class": "dem",
                "cacheable": True,
                "cost_usd": 0.01,  # invariant 9: no cost theater
            }
        )


def test_atomic_tool_metadata_rejects_unknown_ttl_class() -> None:
    """ttl_class is a closed 4-member Literal (FR-DC-2 binding registry)."""
    with pytest.raises(ValidationError):
        AtomicToolMetadata.model_validate(
            {
                "name": "fetch_dem",
                "ttl_class": "static-90d",  # not one of the four
                "source_class": "dem",
                "cacheable": True,
            }
        )
