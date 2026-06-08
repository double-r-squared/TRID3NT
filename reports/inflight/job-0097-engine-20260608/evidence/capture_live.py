"""Live-invocation capture for job-0097 fetch_roads_osm.

Hits the real Overpass endpoint with a Fort Myers bbox and dumps a verbatim
summary of the resulting FlatGeobuf — feature count, highway tag distribution,
bounding box of returned geometry, and the named routes that appeared.

Usage:
    python evidence/capture_live.py > evidence/osm_roads_live.txt

(Run from the job's evidence directory or pass GRACE2_TEST_LIVE_OSM=1.)
"""

from __future__ import annotations

import os
import sys
import tempfile

import geopandas as gpd  # type: ignore[import-not-found]

from grace2_agent.tools.fetch_roads_osm import _fetch_osm_roads_bytes


BBOX = (-82.0, 26.5, -81.8, 26.7)
ROAD_CLASSES = ("primary", "motorway")


def main() -> int:
    print(f"job-0097 fetch_roads_osm — live Overpass capture", flush=True)
    print(f"endpoint: https://overpass-api.de/api/interpreter", flush=True)
    print(f"bbox    : {BBOX}", flush=True)
    print(f"classes : {ROAD_CLASSES}", flush=True)
    print("-" * 64, flush=True)

    fgb_bytes = _fetch_osm_roads_bytes(BBOX, ROAD_CLASSES)
    print(f"fetched FlatGeobuf size: {len(fgb_bytes)} bytes", flush=True)

    with tempfile.NamedTemporaryFile(suffix=".fgb", delete=False) as f:
        path = f.name
        f.write(fgb_bytes)

    try:
        gdf = gpd.read_file(path, engine="pyogrio")
        print(f"feature count       : {len(gdf)}", flush=True)
        print(f"geometry total bbox : {tuple(round(v, 4) for v in gdf.total_bounds)}", flush=True)
        print(f"CRS                 : {gdf.crs}", flush=True)
        print(f"geometry types      : {set(gdf.geometry.geom_type.unique().tolist())}", flush=True)
        print(f"highway tag values  : {sorted(gdf['highway'].dropna().unique().tolist())}", flush=True)
        print(f"columns             : {list(gdf.columns)}", flush=True)

        named = gdf["name"].dropna().tolist()
        unique_named = sorted({str(n) for n in named})
        print(f"named features      : {len(named)} of {len(gdf)} have a 'name' tag", flush=True)
        print(f"distinct named roads:", flush=True)
        for nm in unique_named[:40]:
            print(f"   - {nm}", flush=True)
        if len(unique_named) > 40:
            print(f"   ... and {len(unique_named) - 40} more", flush=True)

        # Geographic-correctness signal (codified job-0086 lesson):
        # verify Fort Myers area markers — I-75 / Tamiami / US-41.
        names_lower = " ".join(unique_named).lower()
        markers_present = [
            m for m in ("i 75", "i-75", "interstate 75", "tamiami", "us 41", "us-41")
            if m in names_lower
        ]
        print(f"geographic markers  : {markers_present}", flush=True)

        # Per-feature sample.
        print("first 3 features:", flush=True)
        for i, row in gdf.head(3).iterrows():
            geom = row.geometry
            print(
                f"   [{i}] osm_id={row['osm_id']} highway={row['highway']!r} "
                f"name={row['name']!r} bounds={tuple(round(v,4) for v in geom.bounds)} "
                f"coords={len(geom.coords)}",
                flush=True,
            )

        # Final acceptance gate (the live verification per audit.md).
        assert len(gdf) >= 1, "ACCEPTANCE FAILED: expected at least 1 feature"
        assert set(gdf["highway"].dropna().unique()).issubset({"primary", "motorway"}), (
            "ACCEPTANCE FAILED: highway tags outside requested filter"
        )
        assert markers_present, (
            "ACCEPTANCE FAILED: no I-75 / US-41 / Tamiami marker in named routes"
        )
        print("-" * 64, flush=True)
        print("ACCEPTANCE PASSED", flush=True)
        return 0
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
