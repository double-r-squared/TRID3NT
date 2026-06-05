"""Shared primitives for every GRACE-2 contract.

This module holds the cross-cutting building blocks every other contract module
depends on: the canonical pydantic base configuration, the ULID id helpers, the
``BBox`` type and its EPSG:4326 ordering validator, and the shared ``TimeRange``.

Conventions enforced here (SRS Appendix A.1, B.7, D.7):
- Ids are ULIDs: 26-char, Crockford base32, time-sortable, URL-safe.
- ``bbox`` is always ``[minLon, minLat, maxLon, maxLat]`` in EPSG:4326.
- Datetimes serialize to ISO-8601 with a ``Z`` suffix (UTC) on the wire.
- ``model_dump(mode="json")`` is the canonical wire/storage form.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
)
from ulid import ULID

__all__ = [
    "GraceModel",
    "ULIDStr",
    "BBox",
    "Lon",
    "Lat",
    "new_ulid",
    "now_utc",
    "TimeRange",
]


# --------------------------------------------------------------------------- #
# ULID
# --------------------------------------------------------------------------- #


def new_ulid() -> str:
    """Generate a fresh ULID string (26 chars, Crockford base32, time-sortable)."""
    return str(ULID())


def _validate_ulid(value: str) -> str:
    """Reject anything that is not a syntactically valid ULID string."""
    # ULID.from_str raises ValueError on malformed input; that surfaces as a
    # pydantic validation error, which is exactly what we want.
    ULID.from_str(value)
    return value


#: A string id that must be a valid ULID. Stored/serialized as a plain string.
ULIDStr = Annotated[str, AfterValidator(_validate_ulid)]


# --------------------------------------------------------------------------- #
# Datetime
# --------------------------------------------------------------------------- #


def now_utc() -> datetime:
    """Timezone-aware current UTC time (the default for ``*_at`` fields)."""
    return datetime.now(timezone.utc)


def _serialize_dt_z(value: datetime) -> str:
    """Serialize a datetime to ISO-8601 with a ``Z`` suffix (UTC).

    Naive datetimes are treated as UTC. Aware datetimes are converted to UTC.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    # isoformat() on a UTC-aware datetime yields "+00:00"; normalize to "Z".
    return value.isoformat().replace("+00:00", "Z")


#: A datetime that always serializes to an ISO-8601 ``Z`` string on the wire.
UTCDatetime = Annotated[datetime, PlainSerializer(_serialize_dt_z, return_type=str)]


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #

Lon = Annotated[float, Field(ge=-180.0, le=180.0)]
Lat = Annotated[float, Field(ge=-90.0, le=90.0)]


def _validate_bbox(value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Enforce EPSG:4326 ordering: [minLon, minLat, maxLon, maxLat]."""
    min_lon, min_lat, max_lon, max_lat = value
    if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0):
        raise ValueError(f"bbox longitudes out of range [-180, 180]: {value!r}")
    if not (-90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0):
        raise ValueError(f"bbox latitudes out of range [-90, 90]: {value!r}")
    if min_lon > max_lon:
        raise ValueError(f"bbox minLon {min_lon} > maxLon {max_lon}: {value!r}")
    if min_lat > max_lat:
        raise ValueError(f"bbox minLat {min_lat} > maxLat {max_lat}: {value!r}")
    return value


#: Bounding box, always [minLon, minLat, maxLon, maxLat] in EPSG:4326.
BBox = Annotated[tuple[float, float, float, float], AfterValidator(_validate_bbox)]


# --------------------------------------------------------------------------- #
# Base model
# --------------------------------------------------------------------------- #


class GraceModel(BaseModel):
    """Canonical base for every GRACE-2 contract model.

    - ``extra="forbid"``: unknown fields are a defect, not silently dropped.
      Forward-compatible growth happens through open ``Literal`` enums and
      additive fields, never through accepting arbitrary keys.
    - ``validate_assignment``: mutating a field re-validates it.
    - Wire form is ``model_dump(mode="json")``; datetimes carry the ``Z``
      serializer via the ``UTCDatetime`` alias used on every datetime field.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        ser_json_timedelta="iso8601",
    )


# --------------------------------------------------------------------------- #
# Shared types
# --------------------------------------------------------------------------- #


class TimeRange(GraceModel):
    """A UTC start/end interval. Shared by AssessmentEnvelope and EventMetadata."""

    start: UTCDatetime
    end: UTCDatetime
