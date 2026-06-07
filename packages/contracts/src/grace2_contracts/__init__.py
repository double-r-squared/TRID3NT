"""GRACE-2 shared contracts (SRS v0.3 Appendices A-D + FR-PHC-2 + solver shapes).

Single source of truth for every type that crosses a specialist boundary:
- ``ws``: WebSocket protocol — envelope + every message type (Appendix A).
- ``envelope``: AssessmentEnvelope + flood subtype (Appendix B).
- ``event``: EventMetadata + ClaimSet/NumericClaim + intensity union (Appendix C).
- ``collections``: the five MongoDB collection schemas + vector index configs
  + TTL config (Appendix D).
- ``catalog``: CatalogEntry — the public_hazard_catalog.yaml entry (FR-PHC-2).
- ``execution``: ModelSetup / ExecutionHandle / RunResult / LayerURI (FR-TA-2).
- ``tool_metadata``: tool-docstring metadata + ``tool_category`` conventions
  (FR-TA-3, FR-AS-3) — convention only; ``agent`` owns the registry code.

All models subclass ``GraceModel`` (``extra="forbid"``, UTC-``Z`` datetimes).
The canonical wire form is ``model_dump(mode="json")`` (add ``by_alias=True``
for the ``_id``-aliased collection documents; see ``collections.MONGO_DUMP_KWARGS``).
"""

from __future__ import annotations

from . import (
    catalog,
    collections,
    envelope,
    event,
    execution,
    tool_metadata,
    tool_registry,
    ws,
)
from .common import (
    BBox,
    GraceModel,
    Lat,
    Lon,
    TimeRange,
    ULIDStr,
    new_ulid,
    now_utc,
)

__version__ = "0.1.0"
SCHEMA_VERSION = "v1"

__all__ = [
    "__version__",
    "SCHEMA_VERSION",
    # modules
    "ws",
    "envelope",
    "event",
    "collections",
    "catalog",
    "execution",
    "tool_metadata",
    "tool_registry",
    # common primitives
    "GraceModel",
    "ULIDStr",
    "BBox",
    "Lon",
    "Lat",
    "TimeRange",
    "new_ulid",
    "now_utc",
]
