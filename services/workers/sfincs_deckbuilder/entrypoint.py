#!/usr/bin/env python3
"""SFINCS deck-builder worker entrypoint (GPL-isolated quadtree + SnapWave).

The coastal North Star gate. ``hydromt-sfincs`` can only READ/WRITE one quadtree;
only Deltares ``cht_sfincs`` (GPL-3.0) can BUILD a refined multi-level quadtree
connectivity table (mu1/mu2/nu1/nu2/md1/md2/nd1/nd2 + level flags) from scratch.
So deck authoring moves to this dedicated GPL-bearing worker; the agent only
submits a Batch job + reads the resulting manifest.

Contract (mirror of ``services/workers/sfincs/entrypoint.py`` — the SOLVE worker):

    Input (CLI or env):
        --run-id RUN_ID                  ($GRACE2_RUN_ID)
            Run identifier. completion.json lands under
            {scheme}://${GRACE2_RUNS_BUCKET}/${RUN_ID}/.
        --build-spec-uri s3://.../build_spec.json   ($GRACE2_BUILD_SPEC_URI)
            JSON deck-build spec. Schema (scout I/O contract):
                {
                  "run_id": "<ulid>",
                  "aoi": {"bbox": [minlon,minlat,maxlon,maxlat],
                          "target_epsg": 32616},
                  "topobathy": {"cog_uri": "s3://.../topobathy.tif",
                                "bathymetry_present": true},
                  "grid": {"x0":..,"y0":..,"nmax":..,"mmax":..,"dx":..,"dy":..,
                           "rotation": 0.0,
                           "refinement_polygons_uri": "s3://.../refine.fgb"},
                  "mask": {"zmin":-1000.0,"zmax":9000.0,
                           "open_boundary_polygon_uri":"s3://.../wl_bnd.fgb",
                           "open_boundary_zmin":..,"open_boundary_zmax":..},
                  "snapwave": {"mask_zmin":..,"mask_zmax":..,
                               "open_boundary_polygon_uri":"s3://.../wave_bnd.fgb",
                               "open_boundary_zmin":..,"open_boundary_zmax":..,
                               "use_herbers": 1,
                               "gamma":0.8,"dtheta":15.0,"hmin":0.1, ...},
                  "forcing": {"tref":"YYYYMMDD HHMMSS","tstart":"...","tstop":"...",
                              "waterlevel": {"timeseries_uri":..,"locations_uri":..},
                              "snapwave_boundary": {
                                  "points":[{"x":..,"y":..,"hs":..,"tp":..,
                                             "wd":..,"ds":..}]}},
                  "output": {
                    "deck_dir_uri":"s3://.../sfincs_setup/<ulid>/deck/",
                    "manifest_uri":"s3://.../sfincs_setup/<ulid>/manifest.json"}
                }

    Output:
        deck_dir_uri/<file>            sfincs.nc + sfincs.inp + snapwave.* + bzs/dis
        manifest_uri                   {"inputs":[{"gs_uri":..,"dest":..},...],
                                        "sfincs_args":[], "outputs":[...]} — the
                                        EXACT shape run_solver feeds to the SOLVE
                                        worker, byte-compatible so the solve half
                                        is unchanged.
        {scheme}://${RUNS_BUCKET}/${RUN_ID}/completion.json
                                        SAME schema the solve worker writes
                                        (status ok|error, exit_code, output_uris
                                        incl. manifest_uri, started/finished_at,
                                        error) so the agent's wait_for_completion
                                        polls it identically.

Two FIXED caveats vs the spike's proven (but flawed) deck:
    CAVEAT 1 — SnapWave forcing time column is tref-RELATIVE (0.0, 7200.0, ...),
               NOT the SnapWave-internal epoch seconds the spike emitted. Enforced
               two ways: (a) tref/tstart/tstop set as proper datetimes anchored so
               cht's ``(time - tref).total_seconds()`` already yields tref-relative
               values, and (b) a post-write normalizer that rewrites any
               bhs/btp/bwd/bds whose first time column is not 0-anchored.
    CAVEAT 2 — snapwave_use_herbers = 1 (infragravity wave run-up), NOT 0.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

LOG = logging.getLogger("grace2.worker.sfincs_deckbuilder")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

SCRATCH = Path(os.environ.get("GRACE2_DECK_SCRATCH", "/opt/grace2/work"))
GCP_PROJECT = os.environ.get("GCP_PROJECT", "grace-2-hazard-prod")
RUNS_BUCKET = os.environ.get("GRACE2_RUNS_BUCKET", "grace-2-hazard-prod-runs")

# Deck files cht writes, in the order the solve worker expects them downloaded.
# Globbed at runtime, this is only documentation of the canonical set.
DECK_GLOB = "**/*"

# SnapWave time-series ascii files whose first column must be tref-relative.
SNAPWAVE_TS_FILES = (
    "snapwave.bhs",
    "snapwave.btp",
    "snapwave.bwd",
    "snapwave.bds",
)

SFINCS_TIME_FMT = "%Y%m%d %H%M%S"


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Object-store abstraction — scheme-dispatched s3:// / gs:// (mirror of the
# solve worker's _download/_upload). Lazy SDK imports so a pure-S3 Batch image
# never pays for the GCP SDK.
# --------------------------------------------------------------------------- #


def _split_object_uri(uri: str) -> tuple[str, str, str]:
    """Split ``s3://bucket/key`` / ``gs://bucket/key`` → (scheme, bucket, key)."""
    for scheme in ("s3", "gs"):
        prefix = f"{scheme}://"
        if uri.startswith(prefix):
            bucket, _, key = uri[len(prefix):].partition("/")
            if not bucket or not key:
                raise ValueError(f"malformed {scheme}:// URI: {uri!r}")
            return scheme, bucket, key
    raise ValueError(
        f"unsupported object URI scheme: {uri!r} (expected s3:// or gs://)"
    )


def _output_scheme() -> str:
    """Runs-bucket output scheme — ``s3`` or ``gs`` (env GRACE2_OBJECT_STORE)."""
    b = (os.environ.get("GRACE2_OBJECT_STORE") or "gcs").strip().lower()
    return "s3" if b in {"s3", "aws"} else "gs"


def _runs_uri(run_id: str, rel: str) -> str:
    return f"{_output_scheme()}://{RUNS_BUCKET}/{run_id}/{rel}"


_GCS_CLIENT: Any = None
_S3_CLIENT: Any = None


def _gcs_client() -> Any:
    global _GCS_CLIENT
    if _GCS_CLIENT is None:
        from google.cloud import storage  # type: ignore

        _GCS_CLIENT = storage.Client(project=GCP_PROJECT)
    return _GCS_CLIENT


def _s3_client() -> Any:
    global _S3_CLIENT
    if _S3_CLIENT is None:
        import boto3  # type: ignore

        _S3_CLIENT = boto3.client(
            "s3", region_name=os.environ.get("AWS_REGION", "us-west-2")
        )
    return _S3_CLIENT


def _download(uri: str, dest: Path) -> None:
    scheme, bucket, key = _split_object_uri(uri)
    dest.parent.mkdir(parents=True, exist_ok=True)
    LOG.info("downloading %s -> %s", uri, dest)
    if scheme == "s3":
        resp = _s3_client().get_object(Bucket=bucket, Key=key)
        with dest.open("wb") as fh:
            shutil.copyfileobj(resp["Body"], fh)
        return
    _gcs_client().bucket(bucket).blob(key).download_to_filename(str(dest))


def _upload(src: Path, uri: str, content_type: str | None = None) -> str:
    scheme, bucket, key = _split_object_uri(uri)
    LOG.info("uploading %s -> %s", src, uri)
    if scheme == "s3":
        extra = {"ContentType": content_type} if content_type else {}
        with src.open("rb") as fh:
            _s3_client().put_object(Bucket=bucket, Key=key, Body=fh, **extra)
        return uri
    blob = _gcs_client().bucket(bucket).blob(key)
    if content_type:
        blob.upload_from_filename(str(src), content_type=content_type)
    else:
        blob.upload_from_filename(str(src))
    return uri


def _read_json(uri: str) -> dict:
    scheme, bucket, key = _split_object_uri(uri)
    LOG.info("reading json %s", uri)
    if scheme == "s3":
        resp = _s3_client().get_object(Bucket=bucket, Key=key)
        text = resp["Body"].read().decode("utf-8")
    else:
        text = _gcs_client().bucket(bucket).blob(key).download_as_text()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("build-spec must be a JSON object")
    return data


def _put_json(payload: dict, uri: str) -> str:
    scheme, bucket, key = _split_object_uri(uri)
    body = json.dumps(payload, indent=2)
    if scheme == "s3":
        _s3_client().put_object(
            Bucket=bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
    else:
        _gcs_client().bucket(bucket).blob(key).upload_from_string(
            body, content_type="application/json"
        )
    LOG.info("wrote json -> %s", uri)
    return uri


# --------------------------------------------------------------------------- #
# Pure-Python build-spec validation + helpers (NO cht_sfincs import — unit
# tested without the GPL library).
# --------------------------------------------------------------------------- #


class BuildSpecError(ValueError):
    """Raised when the deck-build spec is malformed or missing required fields."""


def parse_sfincs_time(value: Any) -> _dt.datetime:
    """Parse a forcing time into a ``datetime`` (naive, tz-agnostic).

    Accepts the SFINCS ascii form ``"YYYYMMDD HHMMSS"`` (what sfincs.inp uses),
    ISO-8601 (``"2018-10-10T00:00:00Z"`` / ``"2018-10-10 00:00:00"``), or an
    already-parsed ``datetime``. Returning a real ``datetime`` is load-bearing:
    cht computes the SnapWave time column as ``(time - tref).total_seconds()``,
    so tref/tstart/tstop being PROPER datetimes (not strings, not epoch ints) is
    what makes the written time column tref-relative (CAVEAT 1).
    """
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo is None else value.replace(tzinfo=None)
    if not isinstance(value, str):
        raise BuildSpecError(f"unparseable time value: {value!r}")
    raw = value.strip()
    # SFINCS ascii form first (the canonical sfincs.inp representation).
    try:
        return _dt.datetime.strptime(raw, SFINCS_TIME_FMT)
    except ValueError:
        pass
    # ISO-8601 (tolerate trailing Z).
    iso = raw[:-1] if raw.endswith("Z") else raw
    try:
        return _dt.datetime.fromisoformat(iso).replace(tzinfo=None)
    except ValueError as exc:  # noqa: TRY003
        raise BuildSpecError(
            f"time {value!r} not in SFINCS ({SFINCS_TIME_FMT!r}) or ISO-8601 form"
        ) from exc


def _require(d: dict, key: str, ctx: str) -> Any:
    if key not in d or d[key] is None:
        raise BuildSpecError(f"build-spec missing required field {ctx}.{key}")
    return d[key]


#: Default grid CRS when the build-spec leaves ``aoi.target_epsg`` unset — the
#: fetch_topobathy default (UTM 16N / Mexico Beach zone, the coastal North Star).
DEFAULT_TARGET_EPSG = 32616


def validate_build_spec(spec: dict) -> dict:
    """Validate the deck-build spec shape; return a normalized copy.

    Pure structural validation — no I/O, no cht. Returns a dict with parsed
    datetimes under ``_parsed_times`` and resolved output URIs so ``build_deck``
    works against clean, typed values.

    Tolerant of the agent composer's actual shape (model_flood_scenario.py
    ``_compose_and_upload_deckbuild_spec``): ``aoi.target_epsg`` / ``mask.zmin`` /
    ``mask.zmax`` may be ``None`` (defaults applied), grid params are flat under
    ``grid`` (x0/y0/nmax/mmax/dx/dy required for cht's quadtree), and the surge /
    river forcing lives under ``forcing.surge_forcing`` (materialised
    timeseries+locations URIs).
    """
    if not isinstance(spec, dict):
        raise BuildSpecError("build-spec must be a JSON object")

    aoi = _require(spec, "aoi", "")
    grid = _require(spec, "grid", "")
    topobathy = _require(spec, "topobathy", "")
    output = _require(spec, "output", "")
    forcing = _require(spec, "forcing", "")

    # target_epsg is OPTIONAL in the agent spec (may be None) — default it.
    raw_epsg = aoi.get("target_epsg")
    target_epsg = int(raw_epsg) if raw_epsg is not None else DEFAULT_TARGET_EPSG

    # cht's quadtree REQUIRES the base-grid geometry. The agent spreads these
    # flat under ``grid`` from build_sfincs_model's computed params.
    for k in ("x0", "y0", "nmax", "mmax", "dx", "dy"):
        if grid.get(k) is None:
            raise BuildSpecError(
                f"build-spec grid.{k} is required for the quadtree base grid "
                "(the agent must populate grid params from build_sfincs_model)"
            )

    _require(topobathy, "cog_uri", "topobathy")
    _require(output, "deck_dir_uri", "output")
    _require(output, "manifest_uri", "output")

    deck_dir_uri = str(output["deck_dir_uri"])
    if not deck_dir_uri.endswith("/"):
        deck_dir_uri += "/"
    manifest_uri = str(output["manifest_uri"])

    tref = parse_sfincs_time(_require(forcing, "tref", "forcing"))
    tstart = parse_sfincs_time(_require(forcing, "tstart", "forcing"))
    tstop = parse_sfincs_time(_require(forcing, "tstop", "forcing"))
    if tstop <= tstart:
        raise BuildSpecError(
            f"forcing.tstop ({tstop}) must be after tstart ({tstart})"
        )

    normalized = dict(spec)
    normalized["aoi"] = {**aoi, "target_epsg": target_epsg}
    normalized["output"] = {
        **output,
        "deck_dir_uri": deck_dir_uri,
        "manifest_uri": manifest_uri,
    }
    normalized["_parsed_times"] = {
        "tref": tref,
        "tstart": tstart,
        "tstop": tstop,
    }
    return normalized


def resolve_forcing_blocks(spec: dict) -> dict:
    """Resolve waterlevel / discharge / snapwave forcing from EITHER shape.

    The agent composer nests materialised forcing under
    ``forcing.surge_forcing.{waterlevel,discharge}`` (each
    ``{"timeseries_uri","locations_uri",...}``). A direct caller (and the tests)
    may instead place ``waterlevel`` / ``discharge`` / ``snapwave_boundary`` at
    the top of ``forcing``. This returns a single normalised dict:
        {"waterlevel": {...}|None, "discharge": {...}|None,
         "snapwave_boundary": {...}|None}
    """
    forcing = spec.get("forcing") or {}
    surge = forcing.get("surge_forcing") or {}

    def _pick(name: str):
        block = forcing.get(name)
        if isinstance(block, dict) and block:
            return block
        block = surge.get(name)
        return block if isinstance(block, dict) and block else None

    return {
        "waterlevel": _pick("waterlevel"),
        "discharge": _pick("discharge"),
        "snapwave_boundary": _pick("snapwave_boundary"),
    }


def snapwave_inp_overrides(spec: dict) -> dict:
    """Resolve the snapwave_* sfincs.inp knobs from the spec.

    CAVEAT 2 — ``snapwave_use_herbers`` is FORCED to **1** (infragravity-wave
    run-up). The agent composer (and the spike's proven deck) emit ``0``, the
    known-bad setting; the worker is the authority on the fix, so it ignores the
    spec's ``use_herbers`` value and forces 1. A DELIBERATE opt-out exists for
    callers that truly want the Herbers path OFF: ``snapwave.force_no_herbers =
    true`` (only that explicit flag turns it back to 0).
    """
    sw = spec.get("snapwave") or {}
    # CAVEAT 2 fix — force infragravity run-up ON unless the deliberate escape
    # hatch is set. The bare ``use_herbers`` field the agent emits is IGNORED.
    use_herbers = 0 if bool(sw.get("force_no_herbers", False)) else 1
    knobs: dict[str, Any] = {
        "snapwave_gamma": float(sw.get("gamma", 0.8)),
        "snapwave_gammaig": float(sw.get("gammaig", 1.0)),
        "snapwave_gammax": float(sw.get("gammax", 1.0)),
        "snapwave_dtheta": float(sw.get("dtheta", 15.0)),
        "snapwave_hmin": float(sw.get("hmin", 0.1)),
        "snapwave_fw0": float(sw.get("fw0", 0.01)),
        "snapwave_crit": float(sw.get("crit", 0.01)),
        "snapwave_igwaves": int(sw.get("igwaves", 1)),
        "snapwave_nrsweeps": int(sw.get("nrsweeps", 1)),
        "snapwave_use_herbers": use_herbers,
    }
    return knobs


def normalize_snapwave_time_columns(
    deck_dir: Path,
    tref: _dt.datetime,
    files: tuple[str, ...] = SNAPWAVE_TS_FILES,
) -> list[str]:
    """Force the SnapWave time-series time column to be tref-RELATIVE (CAVEAT 1).

    cht writes ``dt = (time - tref).total_seconds()``. When tref/tstart/tstop are
    proper datetimes this is already 0-anchored (0.0, 7200.0, ...). But the
    spike's proven deck emitted SnapWave-internal *epoch* seconds (e.g.
    242524800.0) because a non-datetime time index slipped through. This guard
    re-reads each bhs/btp/bwd/bds, and if the FIRST time value is not ~0 (i.e.
    not tref-anchored) it re-bases the entire column by subtracting the first
    value, so column[0] == 0.0 and spacing is preserved.

    Pure-Python (whitespace-delimited ascii) — no cht / pandas dependency, so it
    is unit-testable without the GPL library. Returns the list of files rewritten.
    """
    rewritten: list[str] = []
    for fname in files:
        fpath = deck_dir / fname
        if not fpath.exists():
            continue
        lines = fpath.read_text().splitlines()
        rows: list[tuple[float, list[str]]] = []
        for ln in lines:
            parts = ln.split()
            if not parts:
                continue
            try:
                t = float(parts[0])
            except ValueError:
                # Not a numeric time row (header?) — leave the file untouched.
                rows = []
                break
            rows.append((t, parts[1:]))
        if not rows:
            continue
        first_t = rows[0][0]
        # Already tref-relative if the first timestamp is ~0 (allow tiny fp).
        if abs(first_t) <= 1.0:
            continue
        LOG.warning(
            "normalizing %s: first time column %.3f is not tref-relative; "
            "re-basing to 0.0 (CAVEAT 1)",
            fname,
            first_t,
        )
        new_lines = []
        for t, rest in rows:
            rel_t = t - first_t
            new_lines.append(
                "  ".join([f"{rel_t:.3f}", *[f"{x}" for x in rest]])
            )
        fpath.write_text("\n".join(new_lines) + "\n")
        rewritten.append(fname)
    return rewritten


def compose_manifest(deck_dir: Path, deck_dir_uri: str) -> dict:
    """Compose the run_solver-compatible manifest.json (the SOLVE input).

    IDENTICAL shape to sfincs_builder.py: one ``{"gs_uri","dest"}`` per deck file
    (legacy field name ``gs_uri``; the VALUE is scheme-resolved, s3:// on Batch),
    plus ``sfincs_args=[]`` and the standard outputs glob. The solve worker +
    run_solver are unchanged.
    """
    files = sorted(p for p in deck_dir.glob(DECK_GLOB) if p.is_file())
    inputs = []
    for f in files:
        rel = f.relative_to(deck_dir).as_posix()
        inputs.append({"gs_uri": deck_dir_uri + rel, "dest": rel})
    return {
        "inputs": inputs,
        "sfincs_args": [],
        "outputs": ["sfincs_map.nc", "*.nc", "*.tif"],
    }


# --------------------------------------------------------------------------- #
# The GPL section — cht_sfincs imported LAZILY here only. NEVER at module top
# level, NEVER in the agent. Adapts the proven spike (author_quadtree_cht.py).
# --------------------------------------------------------------------------- #


def _read_polygon_gdf(uri: str | None, scratch: Path, name: str):
    """Download + read an optional refinement / boundary polygon into a GDF."""
    if not uri:
        return None
    import geopandas as gpd  # type: ignore

    local = scratch / f"{name}{Path(_split_object_uri(uri)[2]).suffix or '.fgb'}"
    _download(uri, local)
    return gpd.read_file(local)


def _sample_topobathy(cog_local: Path, xc, yc, target_epsg: int):
    """Sample the topobathy COG at quadtree face centres -> z array (float32).

    Reprojects face centres (in target_epsg, the grid CRS) into the COG CRS if
    they differ (a no-op when the topobathy COG is already in the grid CRS, the
    North Star path), then point-samples (nearest). nodata / off-tile cells fall
    back to a high+dry land sentinel (+9999 m) so the active-cell zmax window
    masks them OUT rather than treating them as deep water (positive-up, NAVD88,
    matching fetch_topobathy's single-band float32 convention).
    """
    import numpy as np  # type: ignore
    import rasterio  # type: ignore
    from rasterio.warp import transform as warp_transform  # type: ignore

    with rasterio.open(cog_local) as ds:
        band_nodata = ds.nodata
        src_crs = ds.crs
        xs, ys = list(xc), list(yc)
        if src_crs is not None and src_crs.to_epsg() not in (target_epsg, None):
            xs, ys = warp_transform(
                f"EPSG:{target_epsg}", src_crs, xs, ys
            )
        samples = np.fromiter(
            (v[0] for v in ds.sample(zip(xs, ys))),
            dtype="float32",
            count=len(xs),
        )
    if band_nodata is not None:
        samples = np.where(samples == np.float32(band_nodata), np.nan, samples)
    # Replace NaN (off-tile / nodata) with a high+dry sentinel so the active
    # mask (zmax window) drops them instead of treating them as deep water.
    samples = np.where(np.isnan(samples), np.float32(9999.0), samples)
    return samples.astype("float32")


def build_deck(spec: dict, scratch: Path) -> Path:
    """Author the quadtree + SnapWave deck via cht_sfincs (GPL-isolated).

    Adapts services/workers/sfincs_quadtree_spike/author_quadtree_cht.py, swapping
    its synthetic constants for the build-spec inputs + real topobathy sampling.
    Returns the local deck directory (caller uploads it).
    """
    # --- GPL import, lazy + isolated to this function ---
    import numpy as np  # type: ignore
    import xarray as xr  # type: ignore
    import xugrid as xu  # type: ignore
    from cht_sfincs import SFINCS  # type: ignore  # GPL-3.0 — image-only

    deck_dir = scratch / "deck"
    if deck_dir.exists():
        shutil.rmtree(deck_dir)
    deck_dir.mkdir(parents=True, exist_ok=True)

    aoi = spec["aoi"]
    grid = spec["grid"]
    topobathy = spec["topobathy"]
    output = spec["output"]
    times = spec["_parsed_times"]
    target_epsg = int(aoi["target_epsg"])

    x0 = float(grid["x0"])
    y0 = float(grid["y0"])
    nmax = int(grid["nmax"])
    mmax = int(grid["mmax"])
    dx = float(grid["dx"])
    dy = float(grid["dy"])
    rotation = float(grid.get("rotation", 0.0))

    # ---- 1. refined quadtree (the gate) -------------------------------------
    LOG.info(
        "building quadtree: x0=%s y0=%s nmax=%d mmax=%d dx=%s dy=%s epsg=%d",
        x0, y0, nmax, mmax, dx, dy, target_epsg,
    )
    refinement_polygons = _read_polygon_gdf(
        grid.get("refinement_polygons_uri"), scratch, "refine"
    )
    sf = SFINCS(root=str(deck_dir), crs=target_epsg, mode="w")
    sf.grid.build(
        x0, y0, nmax, mmax, dx, dy, rotation,
        refinement_polygons=refinement_polygons,
    )
    nr_cells = int(sf.grid.data.sizes["mesh2d_nFaces"])
    nr_levels = int(sf.grid.data.attrs.get("nr_levels", 1))
    LOG.info("quadtree built: nr_cells=%d nr_levels=%d", nr_cells, nr_levels)

    # ---- 2. bathymetry from the topobathy COG -------------------------------
    cog_local = scratch / "topobathy.tif"
    _download(str(topobathy["cog_uri"]), cog_local)
    xc, yc = sf.grid.face_coordinates()
    zb = _sample_topobathy(cog_local, xc, yc, target_epsg)
    ugrid2d = sf.grid.data.grid
    sf.grid.data["z"] = xu.UgridDataArray(
        xr.DataArray(data=zb, dims=[ugrid2d.face_dimension]),
        ugrid2d,
    )
    LOG.info("bathymetry sampled: z range %.2f .. %.2f m", float(np.nanmin(zb)),
             float(np.nanmax(zb)))

    # ---- 3. SFINCS active + waterlevel-boundary mask ------------------------
    mask_spec = spec.get("mask") or {}
    # mask.zmin/zmax may be None in the agent spec — fall back to domain-wide
    # bounds (active everywhere below +2 m, the proven coastal window).
    def _mb(key: str, default: float) -> float:
        v = mask_spec.get(key)
        return float(v) if v is not None else float(default)

    mask_zmin = _mb("zmin", -1000.0)
    mask_zmax = _mb("zmax", 2.0)
    wl_bnd = _read_polygon_gdf(
        mask_spec.get("open_boundary_polygon_uri"), scratch, "wl_bnd"
    )
    sf.mask.build(
        zmin=mask_zmin,
        zmax=mask_zmax,
        open_boundary_polygon=wl_bnd,
        open_boundary_zmin=_mb("open_boundary_zmin", mask_zmin),
        open_boundary_zmax=_mb("open_boundary_zmax", mask_zmax),
    )
    mvals = sf.grid.data["mask"].values
    LOG.info(
        "sfincs mask: active=%d wlbnd=%d inactive=%d",
        int((mvals == 1).sum()), int((mvals == 2).sum()), int((mvals == 0).sum()),
    )

    # ---- 4. SnapWave mask ----------------------------------------------------
    sw_spec = spec.get("snapwave") or {}

    def _swb(key: str, default: float) -> float:
        v = sw_spec.get(key)
        return float(v) if v is not None else float(default)

    wave_bnd = _read_polygon_gdf(
        sw_spec.get("open_boundary_polygon_uri"), scratch, "wave_bnd"
    )
    sf.snapwave.mask.build(
        zmin=_swb("mask_zmin", mask_zmin),
        zmax=_swb("mask_zmax", mask_zmax),
        open_boundary_polygon=wave_bnd if wave_bnd is not None else wl_bnd,
        open_boundary_zmin=_swb("open_boundary_zmin", mask_zmin),
        open_boundary_zmax=_swb("open_boundary_zmax", mask_zmax),
    )
    swvals = sf.grid.data["snapwave_mask"].values
    LOG.info(
        "snapwave mask: active=%d wavebnd=%d inactive=%d",
        int((swvals == 1).sum()), int((swvals > 1).sum()),
        int((swvals == 0).sum()),
    )

    # ---- 5. time keywords (MUST precede SnapWave forcing — CAVEAT 1) --------
    # Set tref/tstart/tstop as proper datetimes BEFORE building the SnapWave
    # boundary timeseries: set_timeseries_uniform / add_point read tstart/tstop
    # off input.variables and cht writes (time - tref).total_seconds(), so these
    # being real datetimes is what makes the time column tref-relative.
    v = sf.input.variables
    v.qtrfile = "sfincs.nc"
    v.x0, v.y0, v.dx, v.dy = x0, y0, dx, dy
    v.nmax, v.mmax, v.rotation = nmax, mmax, rotation
    v.epsg = target_epsg
    v.tref = times["tref"]
    v.tstart = times["tstart"]
    v.tstop = times["tstop"]
    v.dtout = float(spec.get("output_dt", 600.0))
    v.dtmaxout = float(spec.get("output_dt", 600.0))

    # ---- 6. SnapWave boundary forcing (incident waves) ----------------------
    # Resolve forcing from EITHER the agent's nested forcing.surge_forcing.* or
    # a direct forcing.* shape.
    forcing_blocks = resolve_forcing_blocks(spec)
    sw_bc = forcing_blocks["snapwave_boundary"] or {}
    points = sw_bc.get("points") or []
    if points:
        # One boundary point per offshore location; uniform-in-time per the
        # spec values (the proven cht path). add_point(hs=..) seeds the
        # timeseries from tstart/tstop — anchored to the datetimes set in
        # step 5, so the written time column is tref-relative (CAVEAT 1).
        for pt in points:
            sf.snapwave.boundary_conditions.add_point(
                float(pt["x"]), float(pt["y"]),
                hs=float(pt.get("hs", 0.0)),
                tp=float(pt.get("tp", 0.0)),
                wd=float(pt.get("wd", 0.0)),
                ds=float(pt.get("ds", 0.0)),
            )
    else:
        LOG.warning("no SnapWave boundary points in spec — deck has no wave forcing")

    # ---- 7. SnapWave coupling keywords + CAVEAT 2 ---------------------------
    v.snapwave = True
    v.snapwave_bndfile = "snapwave.bnd"
    v.snapwave_bhsfile = "snapwave.bhs"
    v.snapwave_btpfile = "snapwave.btp"
    v.snapwave_bwdfile = "snapwave.bwd"
    v.snapwave_bdsfile = "snapwave.bds"
    for key, val in snapwave_inp_overrides(spec).items():
        setattr(v, key, val)
    LOG.info(
        "snapwave keywords set (use_herbers=%s — CAVEAT 2 fix)",
        getattr(v, "snapwave_use_herbers"),
    )

    # ---- 8. optional water-level (surge) boundary forcing -------------------
    _attach_waterlevel_forcing(sf, forcing_blocks["waterlevel"])

    # ---- 9. optional discharge (river) forcing ------------------------------
    _attach_discharge_forcing(sf, forcing_blocks["discharge"])

    # ---- 10. write the whole deck -------------------------------------------
    sf.write()
    LOG.info("cht wrote deck to %s", deck_dir)

    # ---- 11. CAVEAT 1 guard: tref-relative SnapWave time columns ------------
    rewritten = normalize_snapwave_time_columns(deck_dir, times["tref"])
    if rewritten:
        LOG.info("re-based SnapWave time columns to tref-relative: %s", rewritten)

    return deck_dir


def _attach_waterlevel_forcing(sf, waterlevel: dict | None) -> None:
    """Attach optional surge water-level boundary (bnd + bzs) if present.

    The forcing adapter materialises a waterlevel timeseries (bzs CSV) +
    locations (bnd FlatGeobuf) as object URIs
    (``{"timeseries_uri","locations_uri"}``); we stage them into the deck dir
    under the canonical SFINCS names so the solve worker picks them up. These are
    already in SFINCS format from the adapter — cht's regular boundary machinery
    is intentionally bypassed.
    """
    wl = waterlevel or {}
    deck_dir = Path(sf.path)
    ts_uri = wl.get("timeseries_uri")
    loc_uri = wl.get("locations_uri")
    if ts_uri and loc_uri:
        _download(loc_uri, deck_dir / "sfincs.bnd")
        _download(ts_uri, deck_dir / "sfincs.bzs")
        sf.input.variables.bndfile = "sfincs.bnd"
        sf.input.variables.bzsfile = "sfincs.bzs"
        LOG.info("attached water-level boundary forcing (bnd + bzs)")


def _attach_discharge_forcing(sf, discharge: dict | None) -> None:
    """Attach optional river discharge (src + dis) if present (staged ascii)."""
    dis = discharge or {}
    deck_dir = Path(sf.path)
    src_uri = dis.get("locations_uri")
    dis_uri = dis.get("timeseries_uri")
    if src_uri and dis_uri:
        _download(src_uri, deck_dir / "sfincs.src")
        _download(dis_uri, deck_dir / "sfincs.dis")
        sf.input.variables.srcfile = "sfincs.src"
        sf.input.variables.disfile = "sfincs.dis"
        LOG.info("attached discharge forcing (src + dis)")


# --------------------------------------------------------------------------- #
# Completion + main
# --------------------------------------------------------------------------- #


def _write_completion(
    run_id: str,
    status: str,
    exit_code: int,
    output_uris: list[str],
    started_at: str,
    error: str | None,
) -> str:
    payload = {
        "run_id": run_id,
        "status": status,
        "exit_code": exit_code,
        "output_uris": output_uris,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "error": error,
    }
    return _put_json(payload, _runs_uri(run_id, "completion.json"))


def _build_argv_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grace2-sfincs-deckbuilder",
        description="GRACE-2 SFINCS quadtree+SnapWave deck-builder worker.",
    )
    p.add_argument(
        "--run-id",
        default=os.environ.get("GRACE2_RUN_ID", "").strip(),
        help="Run identifier (also $GRACE2_RUN_ID).",
    )
    p.add_argument(
        "--build-spec-uri",
        default=os.environ.get("GRACE2_BUILD_SPEC_URI", "").strip(),
        help="s3:// / gs:// URI of the deck-build spec JSON "
        "(also $GRACE2_BUILD_SPEC_URI).",
    )
    return p


def _prepare_scratch() -> Path:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return SCRATCH


def main(argv: list[str] | None = None) -> int:
    args = _build_argv_parser().parse_args(argv)
    run_id = args.run_id
    build_spec_uri = args.build_spec_uri
    if not run_id:
        LOG.error("run_id is required (--run-id or $GRACE2_RUN_ID)")
        return 2
    if not build_spec_uri:
        LOG.error(
            "build_spec_uri is required (--build-spec-uri or $GRACE2_BUILD_SPEC_URI)"
        )
        return 2

    LOG.info(
        "grace-2-sfincs-deckbuilder starting — run_id=%s spec=%s object_store=%s",
        run_id, build_spec_uri, _output_scheme(),
    )
    started_at = _utc_now()
    output_uris: list[str] = []
    error_msg: str | None = None
    exit_code = 1
    status = "error"

    try:
        raw_spec = _read_json(build_spec_uri)
        spec = validate_build_spec(raw_spec)
        scratch = _prepare_scratch()

        deck_dir = build_deck(spec, scratch)

        # Upload the deck dir.
        deck_dir_uri = spec["output"]["deck_dir_uri"]
        deck_files = sorted(p for p in deck_dir.glob(DECK_GLOB) if p.is_file())
        for f in deck_files:
            rel = f.relative_to(deck_dir).as_posix()
            output_uris.append(_upload(f, deck_dir_uri + rel))

        # Compose + upload the solve manifest (the run_solver input).
        manifest = compose_manifest(deck_dir, deck_dir_uri)
        manifest_uri = spec["output"]["manifest_uri"]
        _put_json(manifest, manifest_uri)
        # The manifest URI is the load-bearing output the agent reads to hand to
        # run_solver — list it FIRST so wait_for_completion finds it easily.
        output_uris.insert(0, manifest_uri)

        exit_code = 0
        status = "ok"
        LOG.info(
            "deck build OK: %d deck file(s), manifest=%s",
            len(deck_files), manifest_uri,
        )
    except Exception as exc:  # noqa: BLE001 — defensive, logged + emitted
        LOG.exception("deck-builder entrypoint failed")
        error_msg = f"{type(exc).__name__}: {exc}"
        exit_code = 1
        status = "error"

    _write_completion(
        run_id=run_id,
        status=status,
        exit_code=exit_code,
        output_uris=output_uris,
        started_at=started_at,
        error=error_msg,
    )
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
