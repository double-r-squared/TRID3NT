#!/usr/bin/env python3
"""COMBINED coastal quadtree worker — BUILD + SOLVE in ONE Batch job.

This worker fuses what used to be two separate one-shot Batch workers into a
single image + a single job-definition:

  1. the GPL deck-builder (``cht_sfincs`` authors a refined multi-level quadtree
     + SnapWave deck from a build-spec JSON), and
  2. the MIT solve shim (``/usr/local/bin/sfincs`` runs the deck in-place and
     writes ``sfincs_map.nc``).

Before the combine, the agent reached these over an S3 + Batch-submit seam with
TWO job submissions, TWO completion polls, and one S3 round-trip of the deck
(deckbuilder uploads the deck + manifest.json; the solve worker re-downloads
them). The combined worker eliminates the round-trip: after ``build_deck()``
populates a LOCAL deck dir, the same process invokes the SFINCS binary directly
on that dir (no download), uploads ``sfincs_map.nc`` + stdout/stderr, and writes
ONE ``completion.json``. The agent collapses to ONE submit + ONE poll against
ONE new job-def (``GRACE2_AWS_BATCH_JOB_DEF_SFINCS_QUADTREE``).

What the combined worker adds on top of the deck-build half:

  * AUTO-REFINEMENT — derives the cht refinement polygons (a GeoDataFrame with a
    descending ``refinement_level`` column) from the inputs rather than relying
    on a pre-baked URI: the topobathy 0 m NAVD88 contour buffered (finest), the
    nearshore ~-2..0 m band, a slope threshold, OSM river centerlines buffered,
    and OSM building footprints buffered. The agent may still hand a
    ``grid.refinement_polygons_uri`` (the explicit/legacy path) — it is unioned
    in. See :func:`derive_refinement_polygons`.
  * BUDGET CAP — estimates the resulting quadtree cell count and reduces the max
    refinement level (and, last resort, the refinement extent) until it fits the
    spec's ``grid.max_cells`` budget, logging exactly what it coarsened. This
    generalizes the regular-grid autoscale spirit to the quadtree.
    See :func:`apply_cell_budget`.
  * BUILDING OBSTACLES — burns OSM footprints into the deck so water routes
    AROUND buildings: ``thin_dams`` along footprint exterior edges (blocked
    uv-faces, the default), OR raised ``z`` at footprint cells, OR an exclude
    mask (dropped cells). See :func:`burn_building_obstacles`.

Two FIXED caveats vs the spike's proven (but flawed) deck are preserved:
    CAVEAT 1 — SnapWave forcing time column is tref-RELATIVE (0.0, 7200.0, ...),
               NOT the SnapWave-internal epoch seconds the spike emitted.
               Enforced two ways: (a) tref/tstart/tstop set as proper datetimes
               anchored so cht's ``(time - tref).total_seconds()`` already yields
               tref-relative values, and (b) a post-write normalizer that
               rewrites any bhs/btp/bwd/bds whose first time column is not
               0-anchored.
    CAVEAT 2 — snapwave_use_herbers = 1 (infragravity wave run-up), NOT 0.

GPL note: ``cht_sfincs`` is GPL-3.0 and stays IMAGE-ONLY (imported lazily inside
``build_deck`` / the refinement + obstacle helpers, NEVER by agent code). The
combined image bases on the ``deltares/sfincs-cpu`` solve image (for
``/usr/local/bin/sfincs``) AND carries the cht venv; the agent reaches this
worker arms-length over the object-store + Batch-submit seam exactly as before.

Contract:

    Input (CLI or env):
        --run-id RUN_ID                  ($GRACE2_RUN_ID)
            Run identifier. completion.json + outputs land under
            {scheme}://${GRACE2_RUNS_BUCKET}/${RUN_ID}/.
        --build-spec-uri s3://.../build_spec.json   ($GRACE2_BUILD_SPEC_URI)
            JSON build spec (schema_version "v2"). See the module docstring of
            ``validate_build_spec`` / the build_spec_contract scout note for the
            full shape: aoi, topobathy COG, grid + refinement + max_cells,
            mask, buildings, rivers, snapwave, forcing, output.

    Output (all under {scheme}://${RUNS_BUCKET}/${RUN_ID}/):
        sfincs_map.nc                    the load-bearing flood output
        sfincs.stdout / sfincs.stderr    binary run logs
        manifest.json                    audit (the deck->solve manifest)
        deck/<file>                      optional deck audit upload
        completion.json                  UNION of the deck + solve schemas — the
                                         SAME object the agent's
                                         ``wait_for_completion`` polls identically.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

LOG = logging.getLogger("grace2.worker.sfincs_quadtree")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

SCRATCH = Path(os.environ.get("GRACE2_DECK_SCRATCH", "/opt/grace2/work"))
GCP_PROJECT = os.environ.get("GCP_PROJECT", "grace-2-hazard-prod")
RUNS_BUCKET = os.environ.get("GRACE2_RUNS_BUCKET", "grace-2-hazard-prod-runs")

# The SFINCS binary the combined image carries (from deltares/sfincs-cpu). The
# combined worker invokes it IN-PROCESS on the local deck dir after build.
SFINCS_BIN = os.environ.get("GRACE2_SFINCS_BIN", "/usr/local/bin/sfincs")

# Deck files cht writes, in the order the solve half expects them. Globbed at
# runtime; this constant only documents the canonical set.
DECK_GLOB = "**/*"

# SnapWave time-series ascii files whose first column must be tref-relative.
SNAPWAVE_TS_FILES = (
    "snapwave.bhs",
    "snapwave.btp",
    "snapwave.bwd",
    "snapwave.bds",
)

SFINCS_TIME_FMT = "%Y%m%d %H%M%S"

# SFINCS outputs to upload after the solve (glob patterns, expanded under the
# deck dir). sfincs_map.nc is the load-bearing flood output; *.nc / *.tif sweep
# any extra outputs (his, point series, derived rasters).
SOLVE_OUTPUT_PATTERNS = ("sfincs_map.nc", "*.nc", "*.tif")

#: Default grid CRS when the build-spec leaves ``aoi.target_epsg`` unset — the
#: fetch_topobathy default (UTM 16N / Mexico Beach zone, the coastal North Star).
DEFAULT_TARGET_EPSG = 32616

#: Default cell budget when the spec omits ``grid.max_cells``. A quadtree this
#: size builds + solves comfortably inside a c7i-class Batch box; the budget cap
#: coarsens refinement levels until the estimate fits.
DEFAULT_MAX_CELLS = 2_000_000


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
    """Raised when the build spec is malformed or missing required fields."""


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


def validate_build_spec(spec: dict) -> dict:
    """Validate the build spec shape; return a normalized copy.

    Pure structural validation — no I/O, no cht. Returns a dict with parsed
    datetimes under ``_parsed_times`` and resolved output URIs so ``build_deck``
    works against clean, typed values.

    Tolerant of the agent composer's actual shape (model_flood_scenario.py
    ``_compose_and_upload_deckbuild_spec``): ``aoi.target_epsg`` / ``mask.zmin`` /
    ``mask.zmax`` may be ``None`` (defaults applied), grid params are flat under
    ``grid`` (x0/y0/nmax/mmax/dx/dy required for cht's quadtree), and the surge /
    river forcing lives under ``forcing.surge_forcing`` (materialised
    timeseries+locations URIs).

    The combined worker additionally honours (all OPTIONAL, validated leniently):
        grid.refinement_levels   int  — max auto-refinement levels (default 2)
        grid.max_cells           int  — quadtree cell budget (default 2,000,000)
        buildings.footprints_uri str  — OSM building polygons FGB
        buildings.mode           str  — thin_dams | raise_subgrid | exclude
        rivers.lines_uri         str  — OSM waterway lines FGB
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

    # Cell budget + refinement levels — lenient parse with safe defaults.
    raw_max = grid.get("max_cells")
    max_cells = int(raw_max) if raw_max is not None else DEFAULT_MAX_CELLS
    if max_cells <= 0:
        raise BuildSpecError(f"grid.max_cells must be positive, got {max_cells}")
    raw_levels = grid.get("refinement_levels")
    refinement_levels = int(raw_levels) if raw_levels is not None else 2
    if refinement_levels < 0:
        raise BuildSpecError(
            f"grid.refinement_levels must be >= 0, got {refinement_levels}"
        )

    # Buildings block — validate ``mode`` if present (default thin_dams).
    buildings = spec.get("buildings") or {}
    mode = str(buildings.get("mode", "thin_dams")).strip().lower()
    if buildings.get("footprints_uri") and mode not in {
        "thin_dams",
        "raise_subgrid",
        "exclude",
    }:
        raise BuildSpecError(
            f"buildings.mode {mode!r} invalid "
            "(expected thin_dams | raise_subgrid | exclude)"
        )

    normalized = dict(spec)
    normalized["aoi"] = {**aoi, "target_epsg": target_epsg}
    normalized["grid"] = {
        **grid,
        "max_cells": max_cells,
        "refinement_levels": refinement_levels,
    }
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
    """Compose the run_solver-compatible manifest.json (AUDIT artefact).

    In the combined worker the solve runs IN-PROCESS on the local deck dir, so
    this manifest is no longer fed to a second job — it is written for audit /
    debug parity with the old two-stage flow (and so a deck can still be replayed
    by the standalone solve worker if ever needed). IDENTICAL shape to
    sfincs_builder.py: one ``{"gs_uri","dest"}`` per deck file (legacy field name
    ``gs_uri``; the VALUE is scheme-resolved, s3:// on Batch), plus
    ``sfincs_args=[]`` and the standard outputs glob.
    """
    files = sorted(p for p in deck_dir.glob(DECK_GLOB) if p.is_file())
    inputs = []
    for f in files:
        rel = f.relative_to(deck_dir).as_posix()
        inputs.append({"gs_uri": deck_dir_uri + rel, "dest": rel})
    return {
        "inputs": inputs,
        "sfincs_args": [],
        "outputs": list(SOLVE_OUTPUT_PATTERNS),
    }


# --------------------------------------------------------------------------- #
# Pure-Python cell-budget estimation (NO cht — unit-tested). The cht quadtree
# halves dx/dy once per refinement level, so a polygon refined to level L has
# 4**L sub-cells per base cell it covers. We estimate the cell count from the
# base grid + the per-level refinement coverage (fraction of the base grid each
# level's polygons cover) and coarsen levels until the estimate fits the budget.
# --------------------------------------------------------------------------- #


def estimate_quadtree_cells(
    nmax: int,
    mmax: int,
    level_coverage: dict[int, float],
) -> int:
    """Estimate the refined quadtree cell count.

    ``level_coverage`` maps a refinement level (1..N) -> the FRACTION of the base
    grid that gets refined to AT LEAST that level (cumulative coverage; a cell at
    level 3 is also covered by levels 1 and 2). The base grid has ``nmax*mmax``
    level-0 cells; each base cell covered to level L is replaced by 4**L finest
    sub-cells along that nesting (cht refines x2 in each axis per level). We use
    the incremental coverage per level to avoid double counting:

        cells ≈ base*(1 - cov[1])
              + Σ_{L>=1}  base * (cov[L] - cov[L+1]) * 4**L

    where cov[L] is monotonically non-increasing in L (a deeper level covers a
    subset of a shallower one) and cov[L]=0 for L beyond the max level. This is
    an UPPER-ish estimate (treats covered base cells as fully nested), which is
    the safe side for a budget cap. Pure arithmetic — no cht, unit-testable.
    """
    base = int(nmax) * int(mmax)
    if base <= 0:
        return 0
    max_level = max(level_coverage) if level_coverage else 0
    # Normalise to cumulative, monotonically non-increasing coverage in [0,1].
    cov: dict[int, float] = {}
    for lvl in range(1, max_level + 1):
        c = float(level_coverage.get(lvl, 0.0))
        cov[lvl] = max(0.0, min(1.0, c))
    # Enforce monotonic non-increasing (deeper level ⊆ shallower).
    for lvl in range(2, max_level + 1):
        cov[lvl] = min(cov[lvl], cov[lvl - 1])

    cov1 = cov.get(1, 0.0)
    total = base * (1.0 - cov1)
    for lvl in range(1, max_level + 1):
        cov_here = cov.get(lvl, 0.0)
        cov_next = cov.get(lvl + 1, 0.0)
        incremental = max(0.0, cov_here - cov_next)
        total += base * incremental * (4 ** lvl)
    return int(round(total))


def apply_cell_budget(
    nmax: int,
    mmax: int,
    level_coverage: dict[int, float],
    max_cells: int,
) -> tuple[int, list[str]]:
    """Reduce the max refinement level until the cell estimate fits the budget.

    Returns ``(allowed_max_level, notes)`` where ``allowed_max_level`` is the
    deepest refinement level kept (any polygon requesting a deeper level is
    clamped to it) and ``notes`` records what was coarsened (surfaced in the
    completion provenance + logs). Generalizes the regular-grid autoscale spirit:
    rather than shrinking dx, we drop the finest quadtree levels first (they cost
    4**L each), preserving coarse coverage of the whole AOI.

    Pure arithmetic — unit-testable without cht.
    """
    notes: list[str] = []
    max_level = max(level_coverage) if level_coverage else 0
    allowed = max_level
    while allowed > 0:
        capped = {
            lvl: cov for lvl, cov in level_coverage.items() if lvl <= allowed
        }
        est = estimate_quadtree_cells(nmax, mmax, capped)
        if est <= max_cells:
            if allowed < max_level:
                notes.append(
                    f"budget cap: reduced max refinement level "
                    f"{max_level} -> {allowed} "
                    f"(estimate {est:,} <= budget {max_cells:,})"
                )
            return allowed, notes
        notes.append(
            f"budget cap: level {allowed} estimate "
            f"{est:,} > budget {max_cells:,} — dropping to level {allowed - 1}"
        )
        allowed -= 1
    # Even the unrefined base grid is over budget — keep level 0 and warn loudly.
    base = int(nmax) * int(mmax)
    if base > max_cells:
        notes.append(
            f"budget cap: even the base grid ({base:,} cells) exceeds the "
            f"budget ({max_cells:,}) — refinement fully disabled; the agent "
            "should coarsen grid.dx/dy or shrink the AOI"
        )
    return 0, notes


# --------------------------------------------------------------------------- #
# The GPL section — cht_sfincs imported LAZILY here only. NEVER at module top
# level, NEVER in the agent. Adapts the proven spike (author_quadtree_cht.py).
# --------------------------------------------------------------------------- #


def _read_gdf(uri: str | None, scratch: Path, name: str):
    """Download + read an optional polygon/line vector into a GeoDataFrame."""
    if not uri:
        return None
    import geopandas as gpd  # type: ignore

    local = scratch / f"{name}{Path(_split_object_uri(uri)[2]).suffix or '.fgb'}"
    _download(uri, local)
    return gpd.read_file(local)


# Backwards-compatible alias retained for any external caller / test that
# imported the deck-builder-only name.
_read_polygon_gdf = _read_gdf


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


# --------------------------------------------------------------------------- #
# AUTO-REFINEMENT — derive cht refinement polygons from the inputs.
# --------------------------------------------------------------------------- #


def _vectorize_mask_to_polygons(mask, transform, crs):
    """Vectorize a boolean raster mask into a dissolved (multi)polygon GDF row.

    Uses ``rasterio.features.shapes`` (no skimage dependency) to extract the
    polygons where ``mask`` is True, in the raster's CRS, and dissolves them into
    one geometry. Returns a shapely geometry (possibly a MultiPolygon) or None
    when the mask is empty.
    """
    import numpy as np  # type: ignore
    from rasterio import features  # type: ignore
    from shapely.geometry import shape  # type: ignore
    from shapely.ops import unary_union  # type: ignore

    mask = np.asarray(mask, dtype=bool)
    if not mask.any():
        return None
    geoms = [
        shape(geom)
        for geom, val in features.shapes(
            mask.astype("uint8"), mask=mask, transform=transform
        )
        if val == 1
    ]
    if not geoms:
        return None
    merged = unary_union(geoms)
    return merged if not merged.is_empty else None


def derive_refinement_polygons(
    spec: dict,
    scratch: Path,
    cog_local: Path,
    target_epsg: int,
):
    """Auto-derive the cht refinement-polygon GeoDataFrame from the inputs.

    Assembles, in DESCENDING ``refinement_level`` order, polygons from:
      * the topobathy 0 m NAVD88 contour buffered (finest level)   — coastline
      * the nearshore ~-2..0 m band                                — surf/run-up
      * a slope threshold band                                     — steep terrain
      * OSM river centerlines buffered                             — riverine flow
      * OSM building footprints buffered                           — urban detail
    plus any explicit ``grid.refinement_polygons_uri`` (legacy/manual path),
    unioned in at the finest level.

    Returns a GeoDataFrame with a ``refinement_level`` int column (the shape cht's
    ``grid.build`` consumes) and a dict ``level_coverage`` mapping each level ->
    the fraction of the AOI bbox it covers (for the budget estimate). Returns
    ``(None, {})`` when nothing could be derived (cht then builds the base grid).

    The deepest derived level is ``grid.refinement_levels`` (default 2); shallower
    features get shallower levels so the quadtree steps down gracefully from the
    coastline outward.
    """
    import geopandas as gpd  # type: ignore
    import numpy as np  # type: ignore
    import rasterio  # type: ignore
    from rasterio.warp import reproject, Resampling, calculate_default_transform  # type: ignore  # noqa: E501
    from shapely.geometry import box  # type: ignore

    grid = spec["grid"]
    max_level = int(grid.get("refinement_levels", 2) or 0)
    if max_level <= 0:
        LOG.info("auto-refinement disabled (grid.refinement_levels=0)")
        return None, {}

    sw = spec.get("snapwave") or {}
    mask_spec = spec.get("mask") or {}
    # Nearshore band: bathymetry between [near_lo, near_hi] (positive-up NAVD88).
    near_lo = float(sw.get("nearshore_zmin", -2.0))
    near_hi = float(sw.get("nearshore_zmax", 0.0))
    # Slope threshold (m of z change per cell) above which terrain is refined.
    slope_thresh = float(grid.get("slope_threshold", 0.05))
    # Buffer widths (m, projected) for line/point-derived features.
    river_buffer = float((spec.get("rivers") or {}).get("buffer_m", 150.0))
    building_buffer = float((spec.get("buildings") or {}).get("buffer_m", 20.0))

    # --- read the topobathy into the grid CRS (reproject if needed) ----------
    with rasterio.open(cog_local) as ds:
        src_crs = ds.crs
        dst_crs = rasterio.crs.CRS.from_epsg(target_epsg)
        if src_crs is not None and src_crs.to_epsg() == target_epsg:
            z = ds.read(1).astype("float32")
            transform = ds.transform
            nodata = ds.nodata
            pix = abs(transform.a)
        else:
            # Reproject the band into the grid CRS so contours/slope are metric.
            dt, dw, dh = calculate_default_transform(
                src_crs, dst_crs, ds.width, ds.height, *ds.bounds
            )
            z = np.full((dh, dw), np.nan, dtype="float32")
            reproject(
                source=rasterio.band(ds, 1),
                destination=z,
                src_transform=ds.transform,
                src_crs=src_crs,
                dst_transform=dt,
                dst_crs=dst_crs,
                resampling=Resampling.bilinear,
                dst_nodata=float("nan"),
            )
            transform = dt
            nodata = ds.nodata
            pix = abs(dt.a)

    if nodata is not None:
        z = np.where(z == np.float32(nodata), np.nan, z)
    valid = np.isfinite(z)
    if not valid.any():
        LOG.warning("topobathy has no valid pixels — auto-refinement skipped")
        return None, {}

    # --- band masks ----------------------------------------------------------
    # 0 m contour: cells straddling z==0 (a sign change between neighbours). A
    # cheap robust proxy: |z| within half a typical cell's expected relief. We
    # use the nearshore band's upper edge to capture the shoreline robustly.
    coast_band = valid & (z >= -0.5) & (z <= 0.5)
    nearshore_band = valid & (z >= near_lo) & (z <= near_hi)

    # slope (gradient magnitude in m per metre) via finite differences.
    gz = np.zeros_like(z)
    zf = np.where(valid, z, np.nan)
    gy, gx = np.gradient(np.nan_to_num(zf, nan=0.0))
    gz = np.sqrt(gx * gx + gy * gy) / max(pix, 1e-6)
    slope_band = valid & (gz >= slope_thresh)

    # --- vectorize each band -------------------------------------------------
    rows: list[dict] = []

    def _add(geom, level: int, source: str):
        if geom is None or geom.is_empty:
            return
        rows.append(
            {"refinement_level": int(level), "geometry": geom, "_source": source}
        )

    # finest level: coastline (0 m contour) + buildings.
    _add(
        _vectorize_mask_to_polygons(coast_band, transform, dst_crs),
        max_level,
        "coast_0m",
    )
    # one level shallower (clamped >=1): nearshore band + slope.
    mid_level = max(1, max_level - 1)
    _add(
        _vectorize_mask_to_polygons(nearshore_band, transform, dst_crs),
        mid_level,
        "nearshore_band",
    )
    _add(
        _vectorize_mask_to_polygons(slope_band, transform, dst_crs),
        mid_level,
        "slope_band",
    )

    # --- OSM rivers (lines) buffered -> mid level ----------------------------
    rivers = spec.get("rivers") or {}
    river_gdf = _read_gdf(rivers.get("lines_uri"), scratch, "rivers")
    if river_gdf is not None and len(river_gdf):
        try:
            river_gdf = river_gdf.to_crs(epsg=target_epsg)
            from shapely.ops import unary_union  # type: ignore

            buffered = unary_union(
                list(river_gdf.geometry.buffer(river_buffer).values)
            )
            _add(buffered, mid_level, "osm_rivers")
        except Exception as exc:  # noqa: BLE001 — refinement is best-effort
            LOG.warning("river refinement skipped: %s", exc)

    # --- OSM buildings (polygons) buffered -> finest level -------------------
    buildings = spec.get("buildings") or {}
    bld_gdf = _read_gdf(buildings.get("footprints_uri"), scratch, "buildings_refine")
    if bld_gdf is not None and len(bld_gdf):
        try:
            bld_gdf = bld_gdf.to_crs(epsg=target_epsg)
            from shapely.ops import unary_union  # type: ignore

            buffered = unary_union(
                list(bld_gdf.geometry.buffer(building_buffer).values)
            )
            _add(buffered, max_level, "osm_buildings")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("building refinement skipped: %s", exc)

    # --- explicit/legacy refinement polygons unioned in (finest) -------------
    explicit = _read_gdf(
        grid.get("refinement_polygons_uri"), scratch, "refine_explicit"
    )
    if explicit is not None and len(explicit):
        try:
            explicit = explicit.to_crs(epsg=target_epsg)
            for _, row in explicit.iterrows():
                lvl = row.get("refinement_level", max_level)
                _add(row.geometry, int(lvl) if lvl is not None else max_level,
                     "explicit_uri")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("explicit refinement polygons skipped: %s", exc)

    if not rows:
        LOG.info("auto-refinement derived no polygons — building base grid")
        return None, {}

    gdf = gpd.GeoDataFrame(
        [{"refinement_level": r["refinement_level"], "geometry": r["geometry"]}
         for r in rows],
        crs=f"EPSG:{target_epsg}",
    )

    # --- level coverage (fraction of the AOI bbox) for the budget estimate ---
    minx, miny, maxx, maxy = (
        float(grid["x0"]),
        float(grid["y0"]),
        float(grid["x0"]) + int(grid["mmax"]) * float(grid["dx"]),
        float(grid["y0"]) + int(grid["nmax"]) * float(grid["dy"]),
    )
    aoi_box = box(minx, miny, maxx, maxy)
    aoi_area = aoi_box.area or 1.0
    from shapely.ops import unary_union  # type: ignore

    level_coverage: dict[int, float] = {}
    levels_present = sorted({r["refinement_level"] for r in rows})
    # Cumulative coverage: a cell refined to level L is also covered by all
    # shallower levels (cht nests x2 each level), so accumulate from deepest up.
    for lvl in range(min(levels_present), max(levels_present) + 1):
        geoms = [r["geometry"] for r in rows if r["refinement_level"] >= lvl]
        if not geoms:
            level_coverage[lvl] = 0.0
            continue
        merged = unary_union(geoms).intersection(aoi_box)
        level_coverage[lvl] = float(merged.area / aoi_area) if not merged.is_empty else 0.0

    LOG.info(
        "auto-refinement: %d polygon group(s) across levels %s; coverage=%s",
        len(rows),
        levels_present,
        {k: round(v, 4) for k, v in level_coverage.items()},
    )
    return gdf, level_coverage


def _clamp_refinement_levels(gdf, allowed_max_level: int):
    """Clamp the GDF's ``refinement_level`` column to ``allowed_max_level``.

    The budget cap may decide the deepest level the quadtree can afford; any
    polygon requesting a deeper level is clamped down (its geometry stays, only
    the level cap shrinks). Polygons whose level falls to 0 are dropped (no
    refinement). Returns the (possibly empty) clamped GDF or None.
    """
    if gdf is None or allowed_max_level <= 0:
        return None
    out = gdf.copy()
    out["refinement_level"] = out["refinement_level"].clip(upper=allowed_max_level)
    out = out[out["refinement_level"] >= 1]
    return out if len(out) else None


# --------------------------------------------------------------------------- #
# BUILDING OBSTACLES — burn OSM footprints so water routes AROUND buildings.
# --------------------------------------------------------------------------- #


def burn_building_obstacles(sf, spec: dict, scratch: Path, zb, target_epsg: int):
    """Burn OSM building footprints into the deck as flow obstacles.

    Three modes (``buildings.mode``):
      * ``thin_dams`` (default) — add a thin dam (blocked uv-face) along every
        footprint exterior ring, so flow cannot cross building walls without
        raising terrain. cht ``thin_dams.add_xy`` per ring, then snap + write via
        ``sf.write()``.
      * ``raise_subgrid`` — raise the sampled ``zb`` at face centres inside any
        footprint by ``buildings.raise_height_m`` (default 5 m), so buildings
        become high+dry blocks the flow goes around. Mutates + returns ``zb``;
        the caller re-assigns it to the grid BEFORE the mask is built.
      * ``exclude`` — passed through as an exclude polygon to the mask build
        (handled in ``build_deck``); this function is a no-op for that mode.

    Returns the (possibly modified) ``zb`` array. cht_sfincs / geopandas imported
    lazily. Best-effort: a footprint read failure logs + continues (the deck is
    still valid, just without obstacles).
    """
    import numpy as np  # type: ignore

    buildings = spec.get("buildings") or {}
    footprints_uri = buildings.get("footprints_uri")
    mode = str(buildings.get("mode", "thin_dams")).strip().lower()
    if not footprints_uri or mode == "exclude":
        return zb

    bld_gdf = _read_gdf(footprints_uri, scratch, "buildings")
    if bld_gdf is None or not len(bld_gdf):
        LOG.warning("buildings.footprints_uri empty — no obstacles burned")
        return zb
    try:
        bld_gdf = bld_gdf.to_crs(epsg=target_epsg)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("building reprojection failed (%s) — obstacles skipped", exc)
        return zb

    if mode == "thin_dams":
        added = 0
        for geom in bld_gdf.geometry:
            for ring in _exterior_rings(geom):
                xs = [float(c[0]) for c in ring]
                ys = [float(c[1]) for c in ring]
                if len(xs) >= 2:
                    sf.thin_dams.add_xy(xs, ys)
                    added += 1
        if added:
            try:
                sf.thin_dams.snap_to_grid()
            except Exception as exc:  # noqa: BLE001 — snap is best-effort
                LOG.warning("thin_dams snap_to_grid failed: %s", exc)
        LOG.info("burned %d building wall(s) as thin dams", added)
        return zb

    if mode == "raise_subgrid":
        raise_h = float(buildings.get("raise_height_m", 5.0))
        xc, yc = sf.grid.face_coordinates()
        inside = _faces_inside_polygons(xc, yc, bld_gdf, target_epsg)
        if inside is not None and inside.any():
            zb = np.asarray(zb, dtype="float32").copy()
            # Raise to building height ABOVE the local terrain (so multi-storey
            # footprints sit on a hill rather than at a flat absolute height).
            zb[inside] = np.maximum(zb[inside], 0.0) + np.float32(raise_h)
            LOG.info(
                "raised z by +%.1f m at %d building face(s) (raise_subgrid)",
                raise_h,
                int(inside.sum()),
            )
        else:
            LOG.warning("no quadtree faces inside building footprints")
        return zb

    return zb


def _exterior_rings(geom):
    """Yield exterior-ring coordinate lists for a (Multi)Polygon geometry."""
    gtype = getattr(geom, "geom_type", "")
    if gtype == "Polygon":
        yield list(geom.exterior.coords)
    elif gtype == "MultiPolygon":
        for part in geom.geoms:
            yield list(part.exterior.coords)
    # other geometry types (lines/points) have no walls to burn — skip.


def _faces_inside_polygons(xc, yc, gdf, target_epsg):
    """Boolean array: which face centres (xc,yc) fall inside any GDF polygon.

    Uses a unary-union + a vectorized shapely ``contains`` via STRtree when
    available, falling back to a per-point test. Returns a numpy bool array or
    None on failure.
    """
    try:
        import numpy as np  # type: ignore
        from shapely.geometry import Point  # type: ignore
        from shapely.ops import unary_union  # type: ignore
        from shapely.prepared import prep  # type: ignore

        merged = unary_union(list(gdf.geometry.values))
        pgeom = prep(merged)
        xs = list(xc)
        ys = list(yc)
        inside = np.fromiter(
            (pgeom.contains(Point(x, y)) for x, y in zip(xs, ys)),
            dtype=bool,
            count=len(xs),
        )
        return inside
    except Exception as exc:  # noqa: BLE001
        LOG.warning("face-in-polygon test failed: %s", exc)
        return None


def build_deck(spec: dict, scratch: Path) -> tuple[Path, dict]:
    """Author the quadtree + SnapWave deck via cht_sfincs (GPL-isolated).

    Adapts services/workers/sfincs_quadtree_spike/author_quadtree_cht.py, swapping
    its synthetic constants for the build-spec inputs + real topobathy sampling,
    and ADDING the combined worker's auto-refinement, budget cap, and
    building-obstacle steps. Returns ``(deck_dir, provenance)`` where provenance
    carries nr_cells / nr_levels / coverage / budget notes for the completion.
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
    times = spec["_parsed_times"]
    target_epsg = int(aoi["target_epsg"])

    x0 = float(grid["x0"])
    y0 = float(grid["y0"])
    nmax = int(grid["nmax"])
    mmax = int(grid["mmax"])
    dx = float(grid["dx"])
    dy = float(grid["dy"])
    rotation = float(grid.get("rotation", 0.0))
    max_cells = int(grid.get("max_cells", DEFAULT_MAX_CELLS))

    provenance: dict[str, Any] = {"budget_notes": []}

    # ---- 0. download the topobathy COG (needed for refinement + sampling) ---
    cog_local = scratch / "topobathy.tif"
    _download(str(topobathy["cog_uri"]), cog_local)

    # ---- 1. AUTO-REFINEMENT + BUDGET CAP ------------------------------------
    refinement_polygons, level_coverage = derive_refinement_polygons(
        spec, scratch, cog_local, target_epsg
    )
    if refinement_polygons is not None and level_coverage:
        allowed_level, notes = apply_cell_budget(
            nmax, mmax, level_coverage, max_cells
        )
        provenance["budget_notes"].extend(notes)
        for n in notes:
            LOG.info("%s", n)
        refinement_polygons = _clamp_refinement_levels(
            refinement_polygons, allowed_level
        )

    # ---- 2. refined quadtree (the gate) -------------------------------------
    LOG.info(
        "building quadtree: x0=%s y0=%s nmax=%d mmax=%d dx=%s dy=%s epsg=%d "
        "refined=%s",
        x0, y0, nmax, mmax, dx, dy, target_epsg,
        refinement_polygons is not None,
    )
    sf = SFINCS(root=str(deck_dir), crs=target_epsg, mode="w")
    sf.grid.build(
        x0, y0, nmax, mmax, dx, dy, rotation,
        refinement_polygons=refinement_polygons,
    )
    nr_cells = int(sf.grid.data.sizes["mesh2d_nFaces"])
    nr_levels = int(sf.grid.data.attrs.get("nr_levels", 1))
    provenance["nr_cells"] = nr_cells
    provenance["nr_levels"] = nr_levels
    LOG.info("quadtree built: nr_cells=%d nr_levels=%d", nr_cells, nr_levels)
    if nr_cells > max_cells:
        # The estimate under-counted; the real grid still over-ran. Record it as
        # a hard provenance note (the solve still runs, but the agent + reviewer
        # should see the budget was breached).
        msg = (
            f"WARNING: built quadtree nr_cells={nr_cells:,} exceeds budget "
            f"{max_cells:,} (estimate under-counted the refinement)"
        )
        provenance["budget_notes"].append(msg)
        LOG.warning("%s", msg)

    # ---- 3. bathymetry from the topobathy COG -------------------------------
    xc, yc = sf.grid.face_coordinates()
    zb = _sample_topobathy(cog_local, xc, yc, target_epsg)

    # ---- 3b. BUILDING OBSTACLES (raise_subgrid path mutates zb BEFORE mask) -
    # thin_dams are added here too (they don't touch zb), so the deck carries
    # the walls; raise_subgrid raises zb so the mask drops/raises those cells.
    zb = burn_building_obstacles(sf, spec, scratch, zb, target_epsg)

    ugrid2d = sf.grid.data.grid
    sf.grid.data["z"] = xu.UgridDataArray(
        xr.DataArray(data=zb, dims=[ugrid2d.face_dimension]),
        ugrid2d,
    )
    LOG.info("bathymetry sampled: z range %.2f .. %.2f m", float(np.nanmin(zb)),
             float(np.nanmax(zb)))

    # ---- 4. SFINCS active + waterlevel-boundary mask ------------------------
    mask_spec = spec.get("mask") or {}

    def _mb(key: str, default: float) -> float:
        v = mask_spec.get(key)
        return float(v) if v is not None else float(default)

    mask_zmin = _mb("zmin", -1000.0)
    mask_zmax = _mb("zmax", 2.0)
    wl_bnd = _read_gdf(
        mask_spec.get("open_boundary_polygon_uri"), scratch, "wl_bnd"
    )
    # exclude buildings from the domain entirely if mode=exclude (mask=0).
    buildings = spec.get("buildings") or {}
    exclude_poly = None
    if str(buildings.get("mode", "")).strip().lower() == "exclude" and \
            buildings.get("footprints_uri"):
        exclude_poly = _read_gdf(
            buildings.get("footprints_uri"), scratch, "buildings_exclude"
        )
        if exclude_poly is not None:
            try:
                exclude_poly = exclude_poly.to_crs(epsg=target_epsg)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("exclude-polygon reprojection failed: %s", exc)
                exclude_poly = None
    # Allow an explicit exclude polygon URI on the mask block too.
    if exclude_poly is None and mask_spec.get("exclude_polygon_uri"):
        exclude_poly = _read_gdf(
            mask_spec.get("exclude_polygon_uri"), scratch, "mask_exclude"
        )

    mask_kwargs: dict[str, Any] = dict(
        zmin=mask_zmin,
        zmax=mask_zmax,
        open_boundary_polygon=wl_bnd,
        open_boundary_zmin=_mb("open_boundary_zmin", mask_zmin),
        open_boundary_zmax=_mb("open_boundary_zmax", mask_zmax),
    )
    if exclude_poly is not None:
        mask_kwargs["exclude_polygon"] = exclude_poly
    sf.mask.build(**mask_kwargs)
    mvals = sf.grid.data["mask"].values
    LOG.info(
        "sfincs mask: active=%d wlbnd=%d inactive=%d",
        int((mvals == 1).sum()), int((mvals == 2).sum()), int((mvals == 0).sum()),
    )

    # ---- 5. SnapWave mask ----------------------------------------------------
    sw_spec = spec.get("snapwave") or {}

    def _swb(key: str, default: float) -> float:
        v = sw_spec.get(key)
        return float(v) if v is not None else float(default)

    wave_bnd = _read_gdf(
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

    # ---- 6. time keywords (MUST precede SnapWave forcing — CAVEAT 1) --------
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
    out_dt = float((spec.get("output") or {}).get("output_dt",
                                                  spec.get("output_dt", 600.0)))
    v.dtout = out_dt
    v.dtmaxout = out_dt

    # ---- 7. SnapWave boundary forcing (incident waves) ----------------------
    forcing_blocks = resolve_forcing_blocks(spec)
    sw_bc = forcing_blocks["snapwave_boundary"] or {}
    points = sw_bc.get("points") or []
    if points:
        # One boundary point per offshore location; uniform-in-time per the
        # spec values (the proven cht path). add_point(hs=..) seeds the
        # timeseries from tstart/tstop — anchored to the datetimes set in
        # step 6, so the written time column is tref-relative (CAVEAT 1).
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

    # ---- 8. SnapWave coupling keywords + CAVEAT 2 ---------------------------
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

    # ---- 9. optional water-level (surge) boundary forcing -------------------
    _attach_waterlevel_forcing(sf, forcing_blocks["waterlevel"])

    # ---- 10. optional discharge (river) forcing -----------------------------
    _attach_discharge_forcing(sf, forcing_blocks["discharge"])

    # ---- 11. write the whole deck -------------------------------------------
    sf.write()
    LOG.info("cht wrote deck to %s", deck_dir)

    # ---- 12. CAVEAT 1 guard: tref-relative SnapWave time columns ------------
    rewritten = normalize_snapwave_time_columns(deck_dir, times["tref"])
    if rewritten:
        LOG.info("re-based SnapWave time columns to tref-relative: %s", rewritten)

    return deck_dir, provenance


def _attach_waterlevel_forcing(sf, waterlevel: dict | None) -> None:
    """Attach optional surge water-level boundary (bnd + bzs) if present.

    The forcing adapter materialises a waterlevel timeseries (bzs CSV) +
    locations (bnd FlatGeobuf) as object URIs
    (``{"timeseries_uri","locations_uri"}``); we stage them into the deck dir
    under the canonical SFINCS names so the solve picks them up. These are
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
# SOLVE — invoke /usr/local/bin/sfincs IN-PROCESS on the local deck dir. Reuses
# the MIT solve worker's invocation pattern (services/workers/sfincs/entrypoint).
# No download step: the deck is already local from build_deck.
# --------------------------------------------------------------------------- #


def _run_sfincs(args: list[str], cwd: Path) -> tuple[int, Path, Path]:
    """Run the SFINCS binary in ``cwd``; return (returncode, stdout, stderr).

    SFINCS reads its entire deck (sfincs.inp + sfincs.nc + snapwave.* + bnd/bzs/
    src/dis + thin-dam/subgrid files) from CWD and takes NO argv in practice
    (``args`` is ``[]`` for a quadtree deck). Mirrors
    services/workers/sfincs/entrypoint.py::_run_sfincs byte-for-byte.
    """
    stdout_path = cwd / "sfincs.stdout"
    stderr_path = cwd / "sfincs.stderr"
    cmd = [SFINCS_BIN, *args]
    LOG.info("exec: %s (cwd=%s)", " ".join(cmd), cwd)
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=out,
            stderr=err,
            check=False,
        )
    LOG.info(
        "sfincs exit=%d stdout_bytes=%d stderr_bytes=%d",
        proc.returncode,
        stdout_path.stat().st_size,
        stderr_path.stat().st_size,
    )
    return proc.returncode, stdout_path, stderr_path


def _expand_outputs(patterns: list[str], cwd: Path) -> list[Path]:
    """Glob each pattern under ``cwd`` -> sorted unique existing files."""
    seen: set[Path] = set()
    for pat in patterns:
        for hit in glob.glob(str(cwd / pat)):
            p = Path(hit)
            if p.is_file():
                seen.add(p.resolve())
    return sorted(seen)


# --------------------------------------------------------------------------- #
# Completion + main
# --------------------------------------------------------------------------- #


def _write_completion(
    run_id: str,
    status: str,
    exit_code: int,
    output_uris: list[str],
    stdout_uri: str | None,
    stderr_uri: str | None,
    deck_provenance: dict | None,
    started_at: str,
    error: str | None,
) -> str:
    """Write the combined completion.json — a UNION of the deck + solve schemas.

    The agent's ``wait_for_completion`` polls this object identically to the
    standalone solve worker's completion: the keys it reads (status, exit_code,
    output_uris, sfincs_stdout_uri/sfincs_stderr_uri, started/finished_at, error)
    are all present; ``deck`` is the extra build-provenance block.
    """
    payload = {
        "run_id": run_id,
        "status": status,
        "exit_code": exit_code,
        "sfincs_stdout_uri": stdout_uri,
        "sfincs_stderr_uri": stderr_uri,
        "output_uris": output_uris,
        "deck": deck_provenance,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "error": error,
    }
    return _put_json(payload, _runs_uri(run_id, "completion.json"))


def _build_argv_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grace2-sfincs-quadtree",
        description=(
            "GRACE-2 combined SFINCS quadtree+SnapWave BUILD+SOLVE worker "
            "(AWS Batch, one job)."
        ),
    )
    p.add_argument(
        "--run-id",
        default=os.environ.get("GRACE2_RUN_ID", "").strip(),
        help="Run identifier (also $GRACE2_RUN_ID).",
    )
    p.add_argument(
        "--build-spec-uri",
        default=os.environ.get("GRACE2_BUILD_SPEC_URI", "").strip(),
        help="s3:// / gs:// URI of the build spec JSON "
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
        "grace-2-sfincs-quadtree (BUILD+SOLVE) starting — run_id=%s spec=%s "
        "object_store=%s sfincs_bin=%s",
        run_id, build_spec_uri, _output_scheme(), SFINCS_BIN,
    )
    started_at = _utc_now()
    output_uris: list[str] = []
    stdout_uri: str | None = None
    stderr_uri: str | None = None
    deck_provenance: dict | None = None
    error_msg: str | None = None
    exit_code = 1
    status = "error"

    try:
        raw_spec = _read_json(build_spec_uri)
        spec = validate_build_spec(raw_spec)
        scratch = _prepare_scratch()

        # ---- BUILD (GPL: cht_sfincs) ----------------------------------------
        deck_dir, deck_provenance = build_deck(spec, scratch)

        # Compose + upload the audit manifest (NOT fed to a second job anymore).
        deck_dir_uri = spec["output"]["deck_dir_uri"]
        manifest = compose_manifest(deck_dir, deck_dir_uri)
        manifest_uri = spec["output"]["manifest_uri"]
        _put_json(manifest, manifest_uri)
        deck_provenance["manifest_uri"] = manifest_uri

        # ---- SOLVE (MIT: /usr/local/bin/sfincs on the LOCAL deck) -----------
        # No download — the deck is already populated in deck_dir.
        rc, stdout_path, stderr_path = _run_sfincs([], deck_dir)

        # Always upload stdout/stderr so even a failed solve produces evidence.
        stdout_uri = _upload(stdout_path, _runs_uri(run_id, "sfincs.stdout"))
        stderr_uri = _upload(stderr_path, _runs_uri(run_id, "sfincs.stderr"))

        # Upload the SFINCS outputs (sfincs_map.nc is the load-bearing one).
        for path in _expand_outputs(list(SOLVE_OUTPUT_PATTERNS), deck_dir):
            rel = path.relative_to(deck_dir).as_posix()
            # Skip stdout/stderr re-upload (handled above); keep map + tifs.
            if rel in {"sfincs.stdout", "sfincs.stderr"}:
                continue
            output_uris.append(_upload(path, _runs_uri(run_id, rel)))

        # Optional deck audit upload (off by default — the deck is large). The
        # manifest URI is the load-bearing audit pointer; list it for parity.
        if str(spec.get("output", {}).get("upload_deck", "")).strip().lower() in {
            "1", "true", "yes",
        }:
            for f in sorted(p for p in deck_dir.glob(DECK_GLOB) if p.is_file()):
                rel = f.relative_to(deck_dir).as_posix()
                if rel in {"sfincs.stdout", "sfincs.stderr"} or \
                        rel in {Path(u).name for u in output_uris}:
                    continue
                output_uris.append(
                    _upload(f, _runs_uri(run_id, f"deck/{rel}"))
                )
        output_uris.append(manifest_uri)

        # status is OK only if BOTH the build succeeded (we got here) AND the
        # solve exited 0.
        exit_code = rc
        if rc == 0:
            status = "ok"
        else:
            status = "error"
            error_msg = f"sfincs exited with non-zero code {rc}"
        LOG.info(
            "combined run finished: build OK (nr_cells=%s nr_levels=%s), "
            "solve exit=%d, %d output(s)",
            (deck_provenance or {}).get("nr_cells"),
            (deck_provenance or {}).get("nr_levels"),
            rc,
            len(output_uris),
        )
    except Exception as exc:  # noqa: BLE001 — defensive, logged + emitted
        LOG.exception("combined quadtree worker failed")
        error_msg = f"{type(exc).__name__}: {exc}"
        exit_code = 1
        status = "error"

    _write_completion(
        run_id=run_id,
        status=status,
        exit_code=exit_code,
        output_uris=output_uris,
        stdout_uri=stdout_uri,
        stderr_uri=stderr_uri,
        deck_provenance=deck_provenance,
        started_at=started_at,
        error=error_msg,
    )
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
