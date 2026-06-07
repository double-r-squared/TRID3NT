"""Live catalog_search evidence — job-0047 acceptance.

Runs the catalog_search atomic tool against the in-process YAML catalog
twice:
    1. topic="flood zones", location=fort_myers_bbox → expect FEMA NFHL.
    2. topic="DEM" → expect USGS 3DEP.

The evidence file captures the resolved entry IDs + top relevance scores;
the rendered JSON serializes the top-ranked entry's authoritative fields
(id, name, urls, access_tier, source_class, how_to_use snippet) so an
auditor can verify the labeling §F.1.2 Mode 1 calls out is actually present.

Patches read_through to use a FakeStorageClient so the run does not require
GCS write access — catalog_search is a pure in-memory operation modulo the
cache-write side effect.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

# Make sure the in-repo grace2_agent is preferred.
sys.path.insert(0, "services/agent/src")

from grace2_agent.tools import catalog as catalog_mod
from grace2_agent.tools.catalog import catalog_search


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
    logger = logging.getLogger("live_catalog_search")
    fake = _FakeStorageClient()
    from grace2_agent.tools import cache as cache_mod

    original = cache_mod.read_through
    pinned_now = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)

    def _patched(*a, **kw):
        kw.setdefault("storage_client", fake)
        kw.setdefault("now", pinned_now)
        return original(*a, **kw)

    catalog_mod.read_through = _patched  # type: ignore[assignment]

    logger.info("==== catalog_search(topic='flood zones', location=fort_myers_bbox) ====")
    fort_myers_bbox = (-81.92, 26.55, -81.80, 26.68)
    results_flood = catalog_search(topic="flood zones", location=fort_myers_bbox)
    logger.info("n_matches=%d", len(results_flood))
    top_flood = results_flood[:3]
    for i, r in enumerate(top_flood):
        logger.info(
            "  rank=%d id=%s score=%.2f source_class=%s access_tier=%s",
            i + 1,
            r["id"],
            r["relevance_score"],
            r["source_class"],
            r["access_tier"],
        )

    logger.info("==== catalog_search(topic='DEM') ====")
    results_dem = catalog_search(topic="DEM")
    logger.info("n_matches=%d", len(results_dem))
    top_dem = results_dem[:3]
    for i, r in enumerate(top_dem):
        logger.info(
            "  rank=%d id=%s score=%.2f source_class=%s access_tier=%s",
            i + 1,
            r["id"],
            r["relevance_score"],
            r["source_class"],
            r["access_tier"],
        )

    # Verify the kickoff's acceptance entries are present.
    flood_ids = [r["id"] for r in results_flood]
    dem_ids = [r["id"] for r in results_dem]
    assert "fema-nfhl-flood-zones" in flood_ids, f"FEMA NFHL missing from {flood_ids}"
    assert any("3dep" in d for d in dem_ids), f"3DEP missing from {dem_ids}"

    out_path = "reports/inflight/job-0047-engine-20260607/evidence/live_catalog_search_result.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(
            {
                "search_topic_1": "flood zones",
                "search_location": list(fort_myers_bbox),
                "n_matches_flood": len(results_flood),
                "top_flood_ids": flood_ids[:5],
                "top_flood_entry": top_flood[0] if top_flood else None,
                "search_topic_2": "DEM",
                "n_matches_dem": len(results_dem),
                "top_dem_ids": dem_ids[:5],
                "top_dem_entry": top_dem[0] if top_dem else None,
            },
            f,
            indent=2,
            default=str,
        )
    logger.info("PASS — wrote %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
