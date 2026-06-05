"""Round-trip + negative tests for CatalogEntry (FR-PHC-2)."""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from grace2_contracts.catalog import CatalogEntry


def _entry() -> CatalogEntry:
    return CatalogEntry(
        id="usfs-wildfire-hazard-potential",
        title="USFS Wildfire Hazard Potential",
        agency="USFS",
        topic=["wildfire", "fire_risk"],
        coverage="CONUS",
        format="raster_cog",
        access="https://example.com/usfs/whp.tif",
        style_preset="wildfire_whp_grad",
        license="Public Domain",
        description="Wildfire Hazard Potential raster from USFS.",
        last_verified=date(2026, 1, 15),
    )


def test_catalog_entry_roundtrip_idempotent() -> None:
    entry = _entry()
    a = entry.model_dump(mode="json")
    text_a = json.dumps(a, sort_keys=True)
    b = CatalogEntry.model_validate(json.loads(text_a)).model_dump(mode="json")
    text_b = json.dumps(b, sort_keys=True)
    assert text_a == text_b


def test_topic_must_have_at_least_one_entry() -> None:
    with pytest.raises(ValidationError):
        CatalogEntry(
            id="x",
            title="x",
            agency="x",
            topic=[],
            coverage="x",
            format="raster_cog",
            access="https://example.com",
            style_preset="x",
            license="x",
            description="x",
            last_verified=date(2026, 1, 15),
        )


def test_unknown_format_rejected() -> None:
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(
            {
                **_entry().model_dump(mode="json"),
                "format": "shapefile",  # not in CatalogFormat
            }
        )


def test_last_verified_iso_date_string_roundtrip() -> None:
    entry = _entry()
    dumped = entry.model_dump(mode="json")
    assert dumped["last_verified"] == "2026-01-15"
