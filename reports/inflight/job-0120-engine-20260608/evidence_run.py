"""Live Fort Myers acceptance run for job-0120.

Drives ``run_pelicun_damage_assessment`` against the job-0086 Y-flip-fixed
flood COG + ``fetch_administrative_boundaries(level='place', bbox=fort_myers)``.

Writes:
- evidence/fort_myers_damage.fgb  — the output FlatGeobuf
- evidence/summary.txt            — human-readable per-asset damage summary

Run from repo root:
    GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/application_default_credentials.json \
    GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \
    .venv-agent/bin/python reports/inflight/job-0120-engine-20260608/evidence_run.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "agent" / "src"))

import geopandas as gpd  # noqa: E402

from grace2_agent.tools.fetch_administrative_boundaries import (  # noqa: E402
    fetch_administrative_boundaries,
)
from grace2_agent.tools.run_pelicun_damage_assessment import (  # noqa: E402
    _download_uri_to_local,
    run_pelicun_damage_assessment,
)

EVIDENCE_DIR = Path(__file__).parent / "evidence"
EVIDENCE_DIR.mkdir(exist_ok=True)


def main() -> None:
    fort_myers_bbox = (-82.0, 26.55, -81.7, 26.75)
    hazard_uri = (
        "gs://grace-2-hazard-prod-runs/01KTJX71NKGDMXB9TN0DV75JWK/"
        "flood_depth_peak_0086.tif"
    )

    print("=" * 60)
    print("job-0120 Fort Myers Pelicun damage assessment")
    print("=" * 60)
    print(f"hazard_raster_uri : {hazard_uri}")
    print(f"bbox              : {fort_myers_bbox}")

    print("\n[1/3] Fetching Fort Myers admin boundaries (level='place')...")
    assets_layer = fetch_administrative_boundaries(
        level="place", bbox=fort_myers_bbox
    )
    print(f"assets_uri        : {assets_layer.uri}")

    print("\n[2/3] Running Pelicun damage assessment (500 realizations)...")
    result = run_pelicun_damage_assessment(
        hazard_raster_uri=hazard_uri,
        assets_uri=assets_layer.uri,
        fragility_set="hazus_flood_v6",
        realization_count=500,
    )
    print(f"output uri        : {result.uri}")
    print(f"layer_id          : {result.layer_id}")
    print(f"style_preset      : {result.style_preset}")
    print(f"units             : {result.units}")

    print("\n[3/3] Reading output FGB + writing local evidence...")
    local_path = _download_uri_to_local(result.uri, ".fgb")
    gdf = gpd.read_file(local_path, engine="pyogrio")

    out_fgb = EVIDENCE_DIR / "fort_myers_damage.fgb"
    gdf.to_file(out_fgb, driver="FlatGeobuf", engine="pyogrio")

    # Summary stats.
    summary_path = EVIDENCE_DIR / "summary.txt"
    with summary_path.open("w") as fh:
        fh.write("job-0120 Fort Myers Pelicun damage assessment\n")
        fh.write("=" * 60 + "\n")
        fh.write(f"hazard_raster_uri : {hazard_uri}\n")
        fh.write(f"assets_uri        : {assets_layer.uri}\n")
        fh.write(f"fragility_set     : hazus_flood_v6\n")
        fh.write(f"realization_count : 500\n")
        fh.write(f"output_uri        : {result.uri}\n")
        fh.write("\n")
        fh.write(f"n_assets          : {len(gdf)}\n")
        fh.write(f"columns           : {list(gdf.columns)}\n")
        fh.write("\nPer-asset results:\n")
        cols_show = [
            c
            for c in [
                "NAME",
                "GEOID",
                "component_type_used",
                "hazard_depth_sampled",
                "ds_mean",
                "ds_p05",
                "ds_p95",
                "loss_ratio_mean",
                "loss_ratio_p95",
                "repair_cost_mean",
                "repair_cost_p95",
                "replacement_value",
            ]
            if c in gdf.columns
        ]
        fh.write(gdf[cols_show].to_string(index=False) + "\n")
        fh.write("\nAggregate statistics:\n")
        fh.write(f"  total replacement_value: ${gdf['replacement_value'].sum():,.0f}\n")
        fh.write(f"  total repair_cost_mean : ${gdf['repair_cost_mean'].sum():,.0f}\n")
        fh.write(f"  total repair_cost_p95  : ${gdf['repair_cost_p95'].sum():,.0f}\n")
        fh.write(f"  max ds_mean            : {gdf['ds_mean'].max():.3f}\n")
        fh.write(f"  mean ds_mean           : {gdf['ds_mean'].mean():.3f}\n")
        fh.write(f"  max sampled depth (m)  : {gdf['hazard_depth_sampled'].max():.3f}\n")
        fh.write(f"  mean sampled depth (m) : {gdf['hazard_depth_sampled'].mean():.3f}\n")

    print(f"\nWrote {out_fgb}")
    print(f"Wrote {summary_path}")
    print(f"\nn_assets             = {len(gdf)}")
    print(f"max sampled depth (m) = {gdf['hazard_depth_sampled'].max():.3f}")
    print(f"max ds_mean           = {gdf['ds_mean'].max():.3f}")
    print(f"total repair_cost_mean= ${gdf['repair_cost_mean'].sum():,.0f}")


if __name__ == "__main__":
    main()
