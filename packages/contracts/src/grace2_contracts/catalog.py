"""CatalogEntry — the public_hazard_catalog.yaml entry schema (FR-PHC-2).

One entry per curated public hazard layer. The catalog (engine-curated YAML at
the repo root) backs the discovery workflow (``show_hazard_layer``) and the
discovery tools (``hazard_catalog_search``, ``fetch_public_hazard_layer``).
``schema`` owns the entry shape; ``engine`` curates the content.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from .common import GraceModel

__all__ = ["CatalogFormat", "CatalogEntry"]


# Output/access format vocabulary. Open enum, but the fetched payload set for
# Tier-B serving stays COG / FlatGeobuf / GeoParquet (FR-CE-4, FR-QS-3);
# wms/wmts are remote-reference access formats for discovery sources.
CatalogFormat = Literal[
    "wms",
    "wmts",
    "raster_cog",
    "vector_fgb",
    "vector_geoparquet",
    "wfs",
]


class CatalogEntry(GraceModel):
    """A single curated public hazard catalog entry (FR-PHC-2)."""

    schema_version: Literal["v1"] = "v1"

    id: str  # stable identifier, e.g., "usfs-wildfire-hazard-potential"
    title: str  # human-readable name
    agency: str  # source organization, e.g., "USFS", "FEMA", "USGS"
    topic: list[str] = Field(min_length=1)  # e.g., ["wildfire", "fire_risk"]
    coverage: str  # geographic scope, e.g., "CONUS", "California", "Global"
    format: CatalogFormat  # data format
    access: str  # URL or service endpoint
    style_preset: str  # default QML style preset to apply
    license: str  # license text or URL
    description: str  # brief description of what the layer represents
    last_verified: date  # date the entry was last verified working
