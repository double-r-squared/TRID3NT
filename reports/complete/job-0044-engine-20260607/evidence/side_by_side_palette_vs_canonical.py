"""Side-by-side palette-decoded (WMS) vs canonical (WCS) raster bytes for Fort Myers.

Re-fetches the SAME bbox via the two MRLC sub-protocols to document the
OQ-42-NLCD-WMS-PALETTE-ENCODING → job-0044 fix transition.

Run:

    .venv-agent/bin/python reports/inflight/job-0044-engine-20260607/evidence/side_by_side_palette_vs_canonical.py
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import requests
import rasterio
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("side_by_side")

EVIDENCE = Path(__file__).parent
BBOX = (-81.92, 26.55, -81.80, 26.68)


def _fetch_wms_bytes() -> bytes:
    url = "https://www.mrlc.gov/geoserver/mrlc_display/wms"
    params = {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetMap",
        "layers": "NLCD_2021_Land_Cover_L48",
        "srs": "EPSG:4326",
        "bbox": f"{BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]}",
        "width": "512",
        "height": "512",
        "format": "image/geotiff",
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.content


def _fetch_wcs_bytes() -> bytes:
    url = "https://www.mrlc.gov/geoserver/mrlc_display/wcs"
    params = {
        "service": "WCS",
        "version": "1.0.0",
        "request": "GetCoverage",
        "Coverage": "mrlc_display:NLCD_2021_Land_Cover_L48",
        "CRS": "EPSG:4326",
        "BBOX": f"{BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]}",
        "WIDTH": "512",
        "HEIGHT": "512",
        "FORMAT": "GeoTIFF",
    }
    r = requests.get(url, params=params, timeout=120)
    r.raise_for_status()
    return r.content


def _summarize(path: Path) -> dict:
    with rasterio.open(path) as src:
        arr = src.read(1)
        uniq = sorted(set(int(v) for v in np.unique(arr).tolist()))
        cmap = src.colormap(1) if src.count else {}
        return {
            "path": str(path),
            "shape": list(src.shape),
            "dtype": str(src.dtypes[0]),
            "nodata": src.nodata,
            "crs": str(src.crs),
            "colorinterp": str(src.colorinterp[0]),
            "has_colormap": bool(cmap),
            "unique_band1_values": uniq,
            "colormap_entries_for_observed": {
                str(k): list(cmap[k]) for k in uniq if k in cmap
            }
            if cmap
            else {},
        }


def main() -> int:
    wms_path = EVIDENCE / "fort_myers_wms.tif"
    wcs_path = EVIDENCE / "fort_myers_wcs.tif"
    log.info("fetching WMS GetMap GeoTIFF for Fort Myers...")
    wms_path.write_bytes(_fetch_wms_bytes())
    log.info("WMS GeoTIFF: %d bytes", wms_path.stat().st_size)
    log.info("fetching WCS 1.0.0 GetCoverage GeoTIFF for Fort Myers...")
    wcs_path.write_bytes(_fetch_wcs_bytes())
    log.info("WCS GeoTIFF: %d bytes", wcs_path.stat().st_size)

    wms_summary = _summarize(wms_path)
    wcs_summary = _summarize(wcs_path)

    canonical_nlcd = {11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 51, 52, 71, 72, 73, 74, 81, 82, 90, 95}
    wms_non_nodata = set(wms_summary["unique_band1_values"]) - {0, 255, -9999}
    wcs_non_nodata = set(wcs_summary["unique_band1_values"]) - {0, 255, -9999}

    side_by_side = {
        "bbox": list(BBOX),
        "wms_get_map": {
            **wms_summary,
            "non_nodata_values": sorted(wms_non_nodata),
            "all_canonical_nlcd": wms_non_nodata.issubset(canonical_nlcd),
            "verdict": "palette-encoded indices (OQ-42-NLCD-WMS-PALETTE-ENCODING)"
            if not wms_non_nodata.issubset(canonical_nlcd)
            else "canonical NLCD integers (unexpected)",
        },
        "wcs_1_0_0_get_coverage": {
            **wcs_summary,
            "non_nodata_values": sorted(wcs_non_nodata),
            "all_canonical_nlcd": wcs_non_nodata.issubset(canonical_nlcd),
            "verdict": "canonical NLCD integers (job-0044 chosen path)"
            if wcs_non_nodata.issubset(canonical_nlcd)
            else "still palette-encoded (unexpected — refute hotfix)",
        },
        "wms_to_wcs_index_to_class_mapping": {
            "1": 11,
            "3": 21,
            "4": 22,
            "5": 23,
            "6": 24,
            "7": 31,
            "9": 41,
            "10": 42,
            "11": 43,
            "13": 52,
            "14": 71,
            "18": 81,
            "19": 82,
            "20": 90,
            "21": 95,
        },
        "wms_to_wcs_index_to_class_mapping_source": (
            "WMS palette ColorTable RGB → canonical NLCD legend RGB lookup "
            "(MRLC NLCD 2021 legend per "
            "https://www.mrlc.gov/data/legends/national-land-cover-database-class-legend-and-description). "
            "This is the Path A mapping that would have been required if the "
            "hotfix decoded the palette client-side instead of using WCS."
        ),
    }
    out = EVIDENCE / "side_by_side_palette_vs_canonical.json"
    out.write_text(json.dumps(side_by_side, indent=2), encoding="utf-8")
    log.info("wrote %s", out)
    log.info("WMS non-nodata values: %s", sorted(wms_non_nodata))
    log.info("WCS non-nodata values: %s", sorted(wcs_non_nodata))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
