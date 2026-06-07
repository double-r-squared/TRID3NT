"""Live verification harness for job-0044 — NLCD WCS canonical-bytes hotfix.

Live-fetches NLCD landcover at Fort Myers against the production cache bucket
via the patched ``fetch_landcover``, downloads the cached COG back, and reports
the unique NLCD class integers found in the raster band. The hotfix lands when
those integers are in the canonical NLCD class set (11, 21, ..., 95) rather
than the palette indices (1, 3, ..., 21) job-0042's gate caught.

Run:

    GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \
      .venv-agent/bin/python reports/inflight/job-0044-engine-20260607/evidence/live_landcover_canonical.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("live_landcover_canonical")

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", os.environ.get("GRACE2_GCP_PROJECT", "grace-2-hazard-prod"))


def main() -> int:
    from grace2_agent.tools.data_fetch import fetch_landcover

    bbox = (-81.92, 26.55, -81.80, 26.68)
    log.info("==== fetch_landcover live (WCS 1.0.0 path) bbox=%s ====", bbox)

    out = fetch_landcover(bbox, dataset="nlcd_2021")
    log.info("returned source=%s vintage=%s", out["source"], out["nlcd_vintage_year"])
    layer = out["layer"]
    log.info("cached COG uri=%s", layer.uri)

    # Download the cached COG and inspect its band values.
    from google.cloud import storage  # type: ignore[import-not-found]
    import rasterio  # type: ignore[import-not-found]
    import numpy as np  # type: ignore[import-not-found]

    no_prefix = layer.uri[len("gs://"):]
    bucket_name, _, blob_key = no_prefix.partition("/")
    client = storage.Client(project=PROJECT)
    blob = client.bucket(bucket_name).blob(blob_key)
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as fh:
        local_tif = fh.name
    blob.download_to_filename(local_tif)
    log.info("downloaded cached COG to %s (%d bytes)", local_tif, Path(local_tif).stat().st_size)

    with rasterio.open(local_tif) as src:
        arr = src.read(1)
        nodata = src.nodata
        uniq = sorted(set(int(v) for v in np.unique(arr).tolist()))
        log.info("CRS=%s shape=%s dtype=%s nodata=%s", src.crs, src.shape, src.dtypes, nodata)
        log.info("unique band1 values: %s", uniq)

    # Canonical NLCD class integers per manning_mapping.csv v1.0.0.
    canonical_nlcd = {11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 51, 52, 71, 72, 73, 74, 81, 82, 90, 95}
    nodata_sentinels = {0, 255, -9999}
    non_nodata = set(uniq) - nodata_sentinels
    canonical_match = non_nodata.issubset(canonical_nlcd)
    palette_indices_observed = non_nodata - canonical_nlcd

    log.info("non-nodata band values: %s", sorted(non_nodata))
    log.info("canonical_nlcd match (subset): %s", canonical_match)
    if palette_indices_observed:
        log.error(
            "FAIL: band carries non-canonical values (palette indices?): %s",
            sorted(palette_indices_observed),
        )
        return 1
    log.info("PASS: all band values are canonical NLCD integers + nodata sentinels")
    Path(__file__).parent.joinpath("live_landcover_canonical_result.json").write_text(
        json.dumps(
            {
                "bbox": list(bbox),
                "source": out["source"],
                "nlcd_vintage_year": out["nlcd_vintage_year"],
                "uri": layer.uri,
                "unique_band_values": uniq,
                "non_nodata_values": sorted(non_nodata),
                "canonical_nlcd_match": canonical_match,
                "palette_indices_observed": sorted(palette_indices_observed),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
