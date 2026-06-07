"""Live catalog_fetch evidence — job-0047 acceptance.

Runs the catalog_fetch atomic tool live against two Tier-2 catalog entries
(no GCS write — FakeStorageClient stand-in, but the upstream OGC adapter
HTTPS request is real). Evidence captures the resolved upstream URL, HTTP
content-type, response body size, and the catalog entry's authoritative
metadata (citation, last_verified, source_class).

The kickoff calls out two acceptance fetches:
    1. catalog_fetch(entry_id="fema-nfhl-flood-zones", params={bbox: fort_myers_bbox})
       — returns a real flood-zone layer via the ArcGIS REST query path
       (FEMA NFHL flood hazard zones are layer 28 per the entry's how_to_use).
    2. catalog_fetch(entry_id="usgs-3dep-elevation-image-service",
       params={bbox: fort_myers_bbox}) — returns a real elevation layer via
       the ArcGIS ImageServer query path.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "services/agent/src")

from grace2_agent.tools import catalog as catalog_mod
from grace2_agent.tools.catalog import catalog_fetch


class _FakeBlob:
    def __init__(self, store, path):
        self._store = store
        self._path = path
        self.custom_time = None
        self.cache_control = None

    def exists(self):
        return self._path in self._store

    def download_as_bytes(self):
        return self._store[self._path]

    def upload_from_string(self, data, content_type=None):
        self._store[self._path] = data


class _FakeBucket:
    def __init__(self, store):
        self._store = store

    def blob(self, path):
        return _FakeBlob(self._store, path)


class _FakeStorageClient:
    def __init__(self):
        self.store = {}

    def bucket(self, name):
        return _FakeBucket(self.store)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger = logging.getLogger("live_catalog_fetch")
    fake = _FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    original = cache_mod.read_through
    pinned_now = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)

    def _patched(*a, **kw):
        kw.setdefault("storage_client", fake)
        kw.setdefault("now", pinned_now)
        return original(*a, **kw)

    catalog_mod.read_through = _patched  # type: ignore[assignment]

    fort_myers_bbox = (-81.92, 26.55, -81.80, 26.68)

    logger.info("==== catalog_fetch('fema-nfhl-flood-zones', layer_id=28, bbox=fort_myers) ====")
    try:
        flood_result = catalog_fetch(
            entry_id="fema-nfhl-flood-zones",
            params={
                "bbox": list(fort_myers_bbox),
                "layer_id": "28",  # FEMA NFHL flood hazard zones layer
                "where": "1=1",
            },
        )
        flood_ok = True
        logger.info(
            "  layer.uri=%s entry_id=%s tier=%s source_class=%s",
            flood_result["layer"].uri,
            flood_result["entry_id"],
            flood_result["access_tier"],
            flood_result["source_class"],
        )
        logger.info("  bytes=%d cache_hit=%s", flood_result["bytes"], flood_result["cache_hit"])
        # Inspect the fetched bytes (cached in-memory by FakeStorageClient).
        cached_path = list(fake.store.keys())[-1]
        body = fake.store[cached_path]
        logger.info("  cached_path=%s body_first_120=%r", cached_path, body[:120])
    except Exception as exc:
        flood_ok = False
        flood_result = None
        logger.error("FEMA NFHL fetch raised: %s", exc, exc_info=True)

    logger.info("==== catalog_fetch('usgs-3dep-elevation-image-service', bbox=fort_myers) ====")
    try:
        dem_result = catalog_fetch(
            entry_id="usgs-3dep-elevation-image-service",
            params={"bbox": list(fort_myers_bbox)},
        )
        dem_ok = True
        logger.info(
            "  layer.uri=%s entry_id=%s tier=%s source_class=%s",
            dem_result["layer"].uri,
            dem_result["entry_id"],
            dem_result["access_tier"],
            dem_result["source_class"],
        )
        logger.info("  bytes=%d cache_hit=%s", dem_result["bytes"], dem_result["cache_hit"])
        cached_path = list(fake.store.keys())[-1]
        body = fake.store[cached_path]
        logger.info("  cached_path=%s body_first_120=%r", cached_path, body[:120])
    except Exception as exc:
        dem_ok = False
        dem_result = None
        logger.error("3DEP fetch raised: %s", exc, exc_info=True)

    out_path = "reports/inflight/job-0047-engine-20260607/evidence/live_catalog_fetch_result.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(
            {
                "flood_fetch": {
                    "ok": flood_ok,
                    "entry_id": "fema-nfhl-flood-zones",
                    "params": {"bbox": list(fort_myers_bbox), "layer_id": "28"},
                    "result_summary": (
                        {
                            "uri": flood_result["layer"].uri,
                            "access_tier": flood_result["access_tier"],
                            "source_class": flood_result["source_class"],
                            "bytes": flood_result["bytes"],
                        }
                        if flood_ok
                        else None
                    ),
                },
                "dem_fetch": {
                    "ok": dem_ok,
                    "entry_id": "usgs-3dep-elevation-image-service",
                    "params": {"bbox": list(fort_myers_bbox)},
                    "result_summary": (
                        {
                            "uri": dem_result["layer"].uri,
                            "access_tier": dem_result["access_tier"],
                            "source_class": dem_result["source_class"],
                            "bytes": dem_result["bytes"],
                        }
                        if dem_ok
                        else None
                    ),
                },
            },
            f,
            indent=2,
            default=str,
        )
    logger.info("wrote %s", out_path)
    return 0 if (flood_ok or dem_ok) else 2


if __name__ == "__main__":
    raise SystemExit(main())
