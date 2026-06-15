"""SFINCS model setup via HydroMT (job-0042, OQ-4 §4 — Invariant 7 mitigation).

This module implements ``build_sfincs_model(dem_uri, landcover_uri,
river_geometry_uri, forcing, bbox, options) → ModelSetup`` per the binding
contract in ``docs/decisions/oq-4-hydromt-depth.md`` §4 "Immediate (job-0042)".

The headline of this module — and the safety-critical invariant for sprint-07 —
is the **NLCD vintage validation gate**:

    HydroMT's roughness component maps landcover class integers to Manning's
    values via a user-supplied CSV mapping table. If NLCD changes class
    encoding (historically a real risk), HydroMT **silently fills unmatched
    classes with default Manning's values** (it logs a warning but does not
    raise). This is a silent-wrong-answer failure: the model runs but uses
    incorrect roughness in cells where the mapping failed. — OQ-4 §2

The gate flips this silent failure into a typed
``SFINCSSetupError("LULC_MAPPING_MISMATCH", details=…)`` that the workflow
surface lifts into a failed AssessmentEnvelope. See the matching
``manning_mapping.csv`` for the version-pinned NLCD → Manning's n table.

Decision OQ-4 §3 "Selected: Option A — Full HydroMT" (job-0038, approved):
- ``build_sfincs_model`` wraps Deltares' ``hydromt_sfincs.SfincsModel`` with a
  ``DataCatalog`` constructed from our atomic-tool ``LayerURI`` GCS paths.
- GCS READS into HydroMT use GDAL's ``/vsigs/`` virtual filesystem
  (job-0170): the YAML config rewrites every ``gs://`` URI to ``/vsigs/``
  before it reaches ``rioxarray.open_rasterio``, keeping the unstable
  ``gcsfs`` backend out of the read path. Deck UPLOADS (manifest +
  staging) still use ``fsspec[gcs]`` — those write paths are stable and
  not in the segfault-prone ``DatasetBase.stop`` finaliser chain.
- ``hydromt-sfincs >= 1.1.2, < 2.0`` is the pinned dependency; v2.0 RC has
  breaking ``SfincsModel`` API changes — defer until it exits RC.

Cross-cutting principles in force (per AGENTS.md + engine.md):

- **Invariant 1 (Determinism boundary): preserves.** No LLM in the path; the
  YAML build config is programmatically generated; given the same inputs the
  output SFINCS deck is byte-for-byte reproducible.
- **Invariant 2 (Deterministic workflows): preserves.** Pure-Python composition
  of typed atomic-tool outputs; no global state.
- **Invariant 7 (no silent wrong answers): EXTENDS — the headline.** The NLCD
  validation gate is the load-bearing mitigation OQ-4 §4 demanded. Silent
  fallback is replaced with a typed, structured ``SFINCSSetupError`` carrying
  ``error_code="LULC_MAPPING_MISMATCH"`` and the unmapped class set + vintage
  year so the agent surface can render a meaningful failure rather than
  dispatching a broken model to the solver.
- **Decision K (minimal parameter surface): preserves.** The signature exposes
  intent + irreducible inputs only (URIs of upstream atomic-tool outputs, bbox,
  options). Manning's mapping CSV vintage, grid resolution, CRS — all derived
  inside.

This module does NOT register an atomic tool. ``build_sfincs_model`` is a
workflow-internal helper called by ``model_flood_scenario`` after the fetcher
chain has run. The workflow itself is the LLM-exposed surface (via the thin
``run_model_flood_scenario`` wrapper).
"""

from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from grace2_contracts import new_ulid
from grace2_contracts.execution import LayerURI, ModelSetup

__all__ = [
    "SFINCSSetupError",
    "ForcingSpec",
    "BuildOptions",
    "build_sfincs_model",
    "load_manning_mapping",
    "validate_nlcd_vintage_against_mapping",
    "MANNING_MAPPING_PATH",
    "MANNING_MAPPING_VERSION",
]

logger = logging.getLogger("grace2_agent.workflows.sfincs_builder")


# --------------------------------------------------------------------------- #
# GDAL/rasterio env (job-0170): stabilize remote-raster reads + kill gcsfs
# --------------------------------------------------------------------------- #
#
# job-0170 headline: the agent process was crashing mid-run with
#
#   SystemError: <cyfunction DatasetBase.stop> returned a result with an
#   exception set
#
# traced to ``hydromt_sfincs.setup_manning_roughness`` →
# ``rioxarray.open_rasterio`` → ``gcsfs``. The gcsfs backend on the agent
# venv is unstable under HydroMT's concurrent open/close pattern and
# segfaults inside the cyfunction destructor — taking the agent process
# down with it (NFR-R-1 breach).
#
# The cure is to keep gcsfs out of the read path entirely: rasterio ships
# GDAL with a native ``/vsigs/`` virtual filesystem that talks GCS via
# libcurl + libgoogle_cloud_storage and is rock-solid under concurrency.
# Two things must hold for ``/vsigs/`` to work:
#
# 1. ``GDAL_NUM_THREADS=1`` — multi-threaded GDAL reads through ``/vsigs/``
#    have historically interacted badly with rasterio's dataset finaliser
#    (the same cyfunction destructor that segfaulted under gcsfs). Pinning
#    to 1 thread eliminates the race; remote bandwidth, not CPU, is the
#    bottleneck for our typical bbox-sized NLCD/DEM reads anyway.
#
# 2. ``CPL_GS_OAUTH2_REFRESH_TOKEN`` OR ADC — GDAL's GS driver needs auth.
#    Cloud Run service accounts and the dev box's ADC both surface as ADC;
#    GDAL picks ADC up automatically as long as
#    ``GOOGLE_APPLICATION_CREDENTIALS`` is set or
#    ``~/.config/gcloud/application_default_credentials.json`` exists. We
#    do NOT set ``CPL_GS_OAUTH2_REFRESH_TOKEN`` (that would shadow ADC and
#    hard-pin a stale token); we just trust ADC, which is what
#    ``fetch_dem``/``fetch_landcover`` already rely on for their py3dep
#    and STAC reads.
#
# These env keys are set at module-import time so any importer (the agent
# Cloud Run service, the test harness, the dev box) inherits the safe
# defaults without needing entrypoint plumbing. ``setdefault`` semantics
# mean a caller can override (e.g. ``GDAL_NUM_THREADS=4`` for a CPU-bound
# local raster job) by setting the env BEFORE importing the workflow.
os.environ.setdefault("GDAL_NUM_THREADS", "1")
# Modest VSI cache + timeout for transient GCS hiccups (FR-DT-2 cache is
# external; this is the per-read pace inside GDAL).
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "1")
os.environ.setdefault("CPL_VSIL_CURL_CHUNK_SIZE", "1048576")  # 1 MiB


# --------------------------------------------------------------------------- #
# Module constants — Manning's mapping CSV location + version
# --------------------------------------------------------------------------- #


#: Absolute path to the version-pinned Manning's mapping CSV.
#: Co-located with the workflows package so it travels in the agent service
#: container. The validation gate reads this file once per ``build_sfincs_model``
#: call (no module-level caching — keep the read explicit so a hot-swap is
#: trivial in tests).
MANNING_MAPPING_PATH: Path = Path(__file__).parent / "manning_mapping.csv"

#: Version string embedded in the CSV's header block. Surfaced in
#: ``ModelSetup.parameters`` for provenance.
MANNING_MAPPING_VERSION: str = "1.0.0"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class SFINCSSetupError(RuntimeError):
    """Raised by ``build_sfincs_model`` on any setup-time failure.

    The ``error_code`` is the A.6 open-set code surfaced to the WS error frame
    and threaded into the final ``AssessmentEnvelope`` when the workflow
    returns a failed envelope. ``details`` is a free-form dict with the gate
    specifics — for ``LULC_MAPPING_MISMATCH`` it carries::

        {
          "nlcd_vintage_year": int,
          "mapping_version": str,
          "unmapped_classes": list[int],
          "mapping_csv_path": str,
        }

    Open-set codes used by this module (per OQ-4 §5 OQ-4c):
    - ``LULC_MAPPING_MISMATCH`` — the **headline**; gate fired before HydroMT
      ran the roughness component.
    - ``DEM_COVERAGE_GAP`` — DEM bytes were not readable or had no spatial
      overlap with the bbox (defensive; HydroMT also catches this).
    - ``FORCING_OUT_OF_RANGE`` — forcing tuple was empty or carried no
      precipitation depth.
    - ``HYDROMT_UNAVAILABLE`` — ``import hydromt_sfincs`` failed in the
      runtime (container missing the dep — surfaces as schema-pushback to
      infra job-0040).
    - ``HYDROMT_BUILD_FAILED`` — HydroMT itself raised during the build (any
      uncaught underlying error is re-raised wrapped in this code).
    """

    def __init__(
        self,
        error_code: str,
        *,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or error_code)
        self.error_code = error_code
        self.details: dict[str, Any] = dict(details or {})


# --------------------------------------------------------------------------- #
# Forcing + options surface (engine-internal)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ForcingSpec:
    """Compact specification of the design-storm forcing for SFINCS.

    For the v0.1 substrate ``model_flood_scenario`` constructs this from the
    ``lookup_precip_return_period`` atomic-tool output — a single
    precipitation depth + duration + ARI metadata. Future workflows (storm
    surge, fluvial) populate it from ``fetch_hurricane_track`` /
    ``fetch_streamflow`` instead; the shape is intentionally open enough to
    grow.

    Fields used by the v0.1 pluvial SFINCS deck:

    - ``forcing_type`` — drives the SFINCS forcing component(s) HydroMT
      configures (``"pluvial_synthetic"`` → uniform rainfall hyetograph from
      an Atlas 14 design storm; ``"pluvial_observed"`` → uniform rainfall
      hyetograph from an OBSERVED precip raster (job-0225 v2, area-mean
      netamt fallback); ``"storm_surge"`` → wind/pressure/water-level series;
      future).
    - ``precip_inches`` — total depth from Atlas 14 (design-storm path).
    - ``duration_hours`` — design-storm / accumulation duration (Atlas 14 row
      for ``pluvial_synthetic``; the precip-raster accumulation window for
      ``pluvial_observed``).
    - ``return_period_years`` — ARI (Atlas 14 column; ``None`` for observed
      forcing — observed precip has no ARI).
    - ``precip_magnitude_mm_per_hr`` — pre-computed uniform-rain rate in mm/hr
      (job-0225 v2 ``pluvial_observed`` netamt path). When set, the YAML
      emitter uses it VERBATIM as the SFINCS ``setup_precip_forcing``
      ``magnitude`` (mm/hr) — bypassing the Atlas 14
      ``precip_inches / duration_hours`` arithmetic. This is the seam where
      the area-mean of a real precip raster (MRMS QPE, ERA5, gridMET …)
      enters the deck. ``None`` for the design-storm path (where magnitude is
      derived from ``precip_inches``). See ``model_flood_scenario``'s
      ``forcing_raster_uri`` branch + OQ-6 (area-mean netamt v0.1; spw
      upgrade path documented there).
    - ``provenance`` — free-form dict echoed into ``ForcingSummary.parameters``
      so the AssessmentEnvelope carries the Atlas 14 volume / project_area /
      vintage strings (design storm) or the precip-raster URI + area-mean
      depth (observed) for narration.
    """

    forcing_type: str
    precip_inches: float | None = None
    duration_hours: float | None = None
    return_period_years: int | None = None
    precip_magnitude_mm_per_hr: float | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BuildOptions:
    """Knobs ``build_sfincs_model`` exposes for engine-internal tuning.

    The workflow caller populates these from defaults — never user-input — per
    Decision K. Surfaces:

    - ``grid_resolution_m`` — SFINCS grid spacing. Defaults to 30 m to match
      NLCD native + NFR-P-4 (≤200 km² at 30 m).
    - ``simulation_hours`` — total simulation length (storm duration + spin-up).
    - ``crs`` — projected CRS the model grid is built in. SFINCS runs in a
      projected metric CRS; we use EPSG:3857 (Web Mercator) as a generic
      default for the v0.1 smoke. A production-grade default would route to
      the appropriate UTM zone per bbox center — captured as
      OQ-42-MODEL-CRS-AUTO-UTM (TENTATIVE: EPSG:3857 for v0.1 smoke).
    - ``output_setup_uri`` — explicit override for the staged deck's gs:// URI.
      When ``None`` we derive one inside the cache bucket.
    """

    grid_resolution_m: float = 30.0
    simulation_hours: float = 24.0
    crs: str = "EPSG:3857"
    output_setup_uri: str | None = None


# --------------------------------------------------------------------------- #
# Manning's mapping loader + NLCD vintage validation gate
# --------------------------------------------------------------------------- #


def load_manning_mapping(
    csv_path: Path | str | None = None,
) -> dict[int, float]:
    """Load the version-pinned NLCD class → Manning's n mapping.

    Reads ``manning_mapping.csv`` (default: the module-local file) and returns
    a dict keyed by NLCD class integer. Comments (``#``) and empty lines are
    ignored; the CSV header row is consumed; data rows must have exactly two
    numeric columns at indices 0 (nlcd_class) and 1 (manning_n). Optional
    columns (e.g. ``description``) are tolerated.

    Args:
        csv_path: optional explicit override (tests use this to inject a fixture
            CSV with only a subset of classes); ``None`` reads
            ``MANNING_MAPPING_PATH``.

    Returns:
        ``{nlcd_class_int: manning_n_float}`` — every row in the CSV becomes
        an entry; duplicates are last-wins with a logged warning.

    Raises:
        SFINCSSetupError("MANNING_MAPPING_LOAD_FAILED", …): the CSV is missing,
            empty, or unparseable.
    """
    path = Path(csv_path) if csv_path is not None else MANNING_MAPPING_PATH
    if not path.exists():
        raise SFINCSSetupError(
            "MANNING_MAPPING_LOAD_FAILED",
            message=f"Manning's mapping CSV not found at {path}",
            details={"path": str(path)},
        )

    mapping: dict[int, float] = {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            # Skip leading comments + blank lines.
            data_lines = [
                line
                for line in fh.readlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
        if not data_lines:
            raise SFINCSSetupError(
                "MANNING_MAPPING_LOAD_FAILED",
                message=f"Manning's mapping CSV at {path} is empty after stripping comments",
                details={"path": str(path)},
            )
        reader = csv.reader(data_lines)
        header = next(reader, None)
        if header is None or not header:
            raise SFINCSSetupError(
                "MANNING_MAPPING_LOAD_FAILED",
                message=f"Manning's mapping CSV at {path} has no header row",
                details={"path": str(path)},
            )
        for row_idx, row in enumerate(reader, start=2):
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) < 2:
                continue
            try:
                cls = int(row[0].strip())
                n_val = float(row[1].strip())
            except (ValueError, IndexError):
                logger.warning(
                    "manning_mapping row %d not parseable: %r (skipped)",
                    row_idx,
                    row,
                )
                continue
            if cls in mapping:
                logger.warning(
                    "manning_mapping duplicate nlcd_class=%d at row %d "
                    "(was %.4f, now %.4f) — last-wins",
                    cls,
                    row_idx,
                    mapping[cls],
                    n_val,
                )
            mapping[cls] = n_val
    except SFINCSSetupError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SFINCSSetupError(
            "MANNING_MAPPING_LOAD_FAILED",
            message=f"Manning's mapping CSV at {path} could not be parsed: {exc}",
            details={"path": str(path)},
        ) from exc

    if not mapping:
        raise SFINCSSetupError(
            "MANNING_MAPPING_LOAD_FAILED",
            message=f"Manning's mapping CSV at {path} parsed to an empty mapping",
            details={"path": str(path)},
        )
    return mapping


def validate_nlcd_vintage_against_mapping(
    fetched_classes: set[int],
    nlcd_vintage_year: int,
    mapping: dict[int, float],
    mapping_version: str = MANNING_MAPPING_VERSION,
    mapping_csv_path: str | None = None,
) -> None:
    """The **OQ-4 §4 Invariant-7 mitigation gate**.

    Verifies that every NLCD class integer observed in the fetched landcover
    raster is present in the Manning's mapping. If any class is missing, raises
    ``SFINCSSetupError("LULC_MAPPING_MISMATCH")`` carrying the specifics so the
    workflow surface can render a failed AssessmentEnvelope rather than
    dispatching a model with HydroMT's silently-filled Manning's defaults to
    the solver.

    Args:
        fetched_classes: the set of integer class codes present in the fetched
            NLCD landcover raster. The workflow layer reads the raster
            (lazily) and extracts the unique class set; this gate is the
            verification step.
        nlcd_vintage_year: the NLCD vintage year (e.g. 2021) returned in the
            ``fetch_landcover`` sidecar.
        mapping: the loaded NLCD → Manning's mapping (from
            ``load_manning_mapping``).
        mapping_version: the CSV's pinned version string (echoed into the
            error details for provenance).
        mapping_csv_path: optional path string for diagnostics.

    Raises:
        SFINCSSetupError("LULC_MAPPING_MISMATCH"): one or more classes in
            ``fetched_classes`` is not covered by ``mapping``. The error
            carries the unmapped class list + vintage year + mapping version
            so the failure surface is fully actionable.
    """
    if not fetched_classes:
        # Defensive — an empty set shouldn't happen but isn't a mismatch per se.
        logger.warning(
            "NLCD validation gate received empty fetched_classes for vintage=%d",
            nlcd_vintage_year,
        )
        return
    # NLCD class 0 = nodata; ignore for the gate (HydroMT's mask component
    # handles nodata cells separately).
    candidate = {cls for cls in fetched_classes if cls != 0}
    unmapped = sorted(candidate - set(mapping.keys()))
    if unmapped:
        details: dict[str, Any] = {
            "nlcd_vintage_year": nlcd_vintage_year,
            "mapping_version": mapping_version,
            "unmapped_classes": unmapped,
            "fetched_classes": sorted(candidate),
            "mapped_classes": sorted(mapping.keys()),
        }
        if mapping_csv_path is not None:
            details["mapping_csv_path"] = mapping_csv_path
        raise SFINCSSetupError(
            "LULC_MAPPING_MISMATCH",
            message=(
                f"NLCD vintage {nlcd_vintage_year} contains classes "
                f"{unmapped} not covered by Manning's mapping "
                f"v{mapping_version}; HydroMT roughness would fill silently "
                "with defaults (Invariant 7 violation). Update "
                "manning_mapping.csv before SFINCS setup proceeds."
            ),
            details=details,
        )


# --------------------------------------------------------------------------- #
# GCS path helper — gs:// → /vsigs/ (job-0170)
# --------------------------------------------------------------------------- #


def _stage_gcs_local(uri: str) -> str:
    """Materialize a ``gs://`` object to a local cache path for HydroMT.

    job-0248 (OQ-0248-FLOOD-BUILD-VSIGS): HydroMT's data adapter stats every
    catalog path with *fsspec's local filesystem* BEFORE GDAL ever sees it
    (``data_adapter.py: self.fs.exists(fn)``), so a ``/vsigs/`` GDAL-ism
    fails the existence check with "No such file found" even though the GCS
    object exists and GDAL could open it. The job-0170 ``/vsigs/`` rewrite is
    still correct for DIRECT rasterio reads (see the landcover extraction
    above) — but paths handed to the HydroMT CATALOG must be real local
    files. Staging via google-cloud-storage (ADC — the proven auth path on
    both Cloud Run and the dev box) also keeps gcsfs out of the read path,
    preserving job-0170's segfault avoidance.

    Local paths pass through unchanged. Downloads are content-keyed under
    a process-stable cache dir and reused across builds in the same host.

    job-0291 (sprint-14-aws): ``s3://`` URIs stage via **boto3** through the
    solver module's shared S3 client seam (``tools.solver.set_s3_client`` —
    boto3 NOT s3fs, the job-0289 instance-role lesson). Same content-keyed
    cache layout as the gs:// path.
    """
    if not (uri.startswith("gs://") or uri.startswith("s3://")):
        if uri.startswith("file://"):
            return uri[len("file://"):]
        return uri

    import hashlib
    from pathlib import Path as _Path

    cache_dir = _Path(tempfile.gettempdir()) / "grace2-hydromt-stage"
    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = _Path(uri).suffix or ".bin"
    local = cache_dir / (hashlib.sha256(uri.encode()).hexdigest()[:24] + suffix)
    if local.exists() and local.stat().st_size > 0:
        return str(local)

    tmp = local.with_suffix(local.suffix + ".part")
    if uri.startswith("s3://"):
        from ..tools.solver import _get_s3_client

        bucket_name, _, obj_key = uri[len("s3://"):].partition("/")
        resp = _get_s3_client().get_object(Bucket=bucket_name, Key=obj_key)
        import shutil as _shutil

        with tmp.open("wb") as fh:
            _shutil.copyfileobj(resp["Body"], fh)
    else:
        from google.cloud import storage  # ADC — same client the cache shim uses

        bucket_name, _, blob_name = uri[len("gs://"):].partition("/")
        client = storage.Client(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT", "grace-2-hazard-prod")
        )
        client.bucket(bucket_name).blob(blob_name).download_to_filename(str(tmp))
    os.replace(tmp, local)
    logger.info("staged %s -> %s (%d bytes)", uri, local, local.stat().st_size)
    return str(local)


def _to_vsigs(uri: str) -> str:
    """Convert a ``gs://bucket/key`` URI to a GDAL ``/vsigs/`` path.

    The ``/vsigs/`` virtual filesystem is rasterio's native, libcurl-backed
    GCS reader — distinct from (and unaffected by) the fragile ``gcsfs``
    backend that ``rioxarray.open_rasterio`` would otherwise pick up when
    handed a ``gs://`` URI.

    Local paths (``file://`` or absolute) pass through unchanged; already-
    converted ``/vsigs/`` paths are idempotent. Anything else is treated
    as a local path (the caller's resolver layer is the gate).

    job-0291 (sprint-14-aws): ``s3://`` URIs map to GDAL's native ``/vsis3/``
    virtual filesystem string. WARNING (job-0293c live observation): GDAL's
    ``/vsis3/`` credential chain does NOT resolve the EC2 instance role in this
    environment — it falls back to anonymous and reports "does not exist" /
    AccessDenied on an existing private object. boto3 DOES resolve the instance
    role. Therefore any caller that intends to ``rasterio.open`` an ``s3://``
    object MUST NOT route it through this function; it must stage the bytes via
    ``cache.read_object_bytes_s3`` and open them from a ``rasterio.io.MemoryFile``
    (see ``model_flood_scenario.compute_precip_area_mean_mm_per_hr`` and
    ``_extract_unique_nlcd_classes`` below, plus the clip / landcover tools).
    The ``s3://`` → ``/vsis3/`` mapping is retained only for non-rasterio string
    consumers; it is NOT a working read path on this instance.

    Args:
        uri: ``gs://...`` GCS URI, ``s3://...`` S3 URI, ``/vsigs/...`` /
            ``/vsis3/...`` GDAL virtual path, or local filesystem path
            (with or without ``file://`` prefix).

    Returns:
        The GDAL-readable string GDAL drivers (rasterio, HydroMT's
        rioxarray, the gdal CLI) can open without invoking ``gcsfs``/``s3fs``.
    """
    if uri.startswith("/vsigs/") or uri.startswith("/vsis3/"):
        return uri
    if uri.startswith("gs://"):
        return "/vsigs/" + uri[len("gs://"):]
    if uri.startswith("s3://"):
        return "/vsis3/" + uri[len("s3://"):]
    if uri.startswith("file://"):
        return uri[len("file://"):]
    return uri


def _rasterio_open_with_retry(read_path: str, *, max_attempts: int = 3):
    """Open a raster via rasterio with retry-and-backoff for transient GS hiccups.

    ``/vsigs/`` reads can fail with transient HTTP errors when the GCS
    endpoint rate-limits or returns a 5xx. Retry up to ``max_attempts``
    times with exponential backoff (1s, 2s, 4s); on final failure re-raise
    the underlying exception unwrapped so the caller's typed-error
    translation layer sees the real cause.

    The retry loop only catches ``rasterio.errors.RasterioIOError`` /
    generic ``RuntimeError`` / ``OSError`` — programming errors
    (TypeError, ValueError on the path string) escape immediately.

    NFR-R-1: external-API resilience — segfault root cause (gcsfs) is
    avoided structurally by the ``/vsigs/`` swap; this wrapper handles
    the remaining transient layer.
    """
    import time

    import rasterio  # local — caller already vouched for the import

    try:
        from rasterio.errors import RasterioIOError  # type: ignore[import-not-found]
        retryable_excs: tuple[type[BaseException], ...] = (
            RasterioIOError,
            RuntimeError,
            OSError,
        )
    except Exception:  # noqa: BLE001
        retryable_excs = (RuntimeError, OSError)

    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return rasterio.open(read_path)
        except retryable_excs as exc:  # type: ignore[misc]
            last_exc = exc
            if attempt == max_attempts:
                break
            backoff_s = 2 ** (attempt - 1)
            logger.warning(
                "rasterio.open(%s) transient failure on attempt %d/%d (%s); "
                "retrying in %.1fs",
                read_path,
                attempt,
                max_attempts,
                exc,
                backoff_s,
            )
            time.sleep(backoff_s)
    assert last_exc is not None  # logic guarantee — the loop sets last_exc on each fail
    raise last_exc


# --------------------------------------------------------------------------- #
# Landcover-class extraction
# --------------------------------------------------------------------------- #


def _extract_unique_nlcd_classes(landcover_uri: str) -> set[int]:
    """Read the landcover raster at ``landcover_uri`` and return its unique class set.

    Uses rasterio for the read (transitively available via the agent venv;
    same dep as ``data_fetch._fetch_3dep_dem_bytes``). The raster may live on
    GCS (``gs://...``) or local disk; rasterio's ``/vsigs/`` virtual filesystem
    handles GCS transparently when ADC is configured (the deployed agent
    runtime has ADC; the dev box has it per PROJECT_STATE env-facts).

    Returns:
        Set of integer class codes present in the raster, after filtering
        out the typical nodata sentinels (-9999, 255).

    Raises:
        SFINCSSetupError("LANDCOVER_READ_FAILED"): the read or class
            extraction failed.
    """
    try:
        import rasterio  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise SFINCSSetupError(
            "LANDCOVER_READ_FAILED",
            message=f"rasterio/numpy not available for landcover class extraction: {exc}",
            details={"landcover_uri": landcover_uri},
        ) from exc

    # Scheme dispatch for the NLCD validation-gate read:
    #   s3://  — boto3 stage-then-open (sprint-14-aws / job-0293c). GDAL's
    #            /vsis3/ credential chain does NOT resolve the EC2 instance role
    #            in this env (boto3 does) — observed live: "does not exist" on an
    #            existing object. Stage the bytes via the shared boto3 reader and
    #            open in-memory (mirrors extract_landcover_class._open_source).
    #            This gate runs on EVERY model_flood_scenario call, so it must be
    #            AWS-correct for Case 1 / Case 3 / plain flood to reach the solver.
    #   gs:// / /vsigs/ / file:// / local — keep the GDAL /vsigs/ path (job-0170
    #            — kills the gcsfs path that segfaulted inside the cyfunction
    #            destructor under HydroMT) with the transient-HTTP retry wrapper.
    read_path = landcover_uri  # echoed into the error envelope below
    try:
        if landcover_uri.startswith("s3://"):
            from rasterio.io import MemoryFile  # type: ignore[import-not-found]

            from ..tools.cache import read_object_bytes_s3

            src = MemoryFile(read_object_bytes_s3(landcover_uri)).open()
            try:
                arr = src.read(1)
                nodata = src.nodata
            finally:
                src.close()
        else:
            # ``_rasterio_open_with_retry`` wraps transient ``/vsigs/`` HTTP
            # failures in exponential backoff; programming errors escape.
            read_path = _to_vsigs(landcover_uri)
            src = _rasterio_open_with_retry(read_path)
            try:
                arr = src.read(1)
                nodata = src.nodata
            finally:
                src.close()
    except Exception as exc:  # noqa: BLE001
        raise SFINCSSetupError(
            "LANDCOVER_READ_FAILED",
            message=f"rasterio.open({landcover_uri}) failed: {exc}",
            details={"landcover_uri": landcover_uri, "read_path": read_path},
        ) from exc

    try:
        unique_vals = np.unique(arr)
        classes: set[int] = set()
        for v in unique_vals.tolist():
            try:
                iv = int(v)
            except (TypeError, ValueError):
                continue
            if nodata is not None and iv == int(nodata):
                continue
            if iv in (-9999, 255):  # common GeoTIFF nodata sentinels
                continue
            classes.add(iv)
    except Exception as exc:  # noqa: BLE001
        raise SFINCSSetupError(
            "LANDCOVER_READ_FAILED",
            message=f"class extraction failed for {landcover_uri}: {exc}",
            details={"landcover_uri": landcover_uri},
        ) from exc

    return classes


# --------------------------------------------------------------------------- #
# build_sfincs_model — workflow-internal entry point
# --------------------------------------------------------------------------- #


def _write_hydromt_reclass_table_csv(
    mapping: dict[int, float],
    out_path: Path,
) -> Path:
    """Write a reclass-table CSV in the **hydromt-sfincs 1.2.x** expected format.

    OQ-52 hotfix (job-0053). ``_parse_datasets_rgh`` reads the reclass table
    via ``data_catalog.get_dataframe(reclass_table, index_col=0)`` then
    indexes ``df_map[["N"]]`` — i.e. the first column must be the LULC class
    integer (used as the index), and there must be a column literally named
    ``N`` carrying the Manning's roughness value. Our authored
    ``manning_mapping.csv`` uses ``nlcd_class,manning_n,description`` columns
    (load-bearing for ``load_manning_mapping`` + the OQ-4 §4 validation gate);
    here we rewrite the in-memory mapping into the v1.2.x-shaped CSV that
    HydroMT will actually consume during ``setup_manning_roughness``.

    Args:
        mapping: ``{nlcd_class_int: manning_n_float}`` as loaded by
            ``load_manning_mapping`` (the substrate-version-pinned set).
        out_path: destination path inside the per-build temp dir.

    Returns:
        ``out_path`` for convenience.
    """
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        # First column is the LULC class integer (index_col=0); the ``N``
        # column is what HydroMT's reclassify call picks up.
        writer.writerow(["nlcd_class", "N"])
        for cls in sorted(mapping.keys()):
            writer.writerow([cls, mapping[cls]])
    return out_path


def _default_setup_uri(bbox: tuple[float, float, float, float]) -> str:
    """Compose a default object-store URI for the SFINCS setup manifest JSON.

    Returns the URI of the manifest FILE (not the directory), per the worker
    contract in ``services/workers/sfincs/entrypoint.py``:

        ``manifest_uri`` must be a single JSON file URI (``gs://.../setup.json``)
        — the worker calls ``blob.download_as_text()`` on it, then
        ``json.loads(text)``. Passing a trailing-slash directory URI hits a
        ``404 No such object`` because GCS has no object at that path.

    The directory prefix ``{scheme}://{bucket}/cache/static-30d/sfincs_setup/{id}/``
    is implicit — deck files land there; the manifest URI is that prefix +
    ``manifest.json``.

    job-0291 (sprint-14-aws): the scheme follows ``cache.storage_scheme()``
    (``GRACE2_STORAGE_BACKEND=s3`` → ``s3://``, default ``gs://`` unchanged)
    so the manifest's input URIs match the scheme the local-docker staging
    resolves them by.

    Lives under the cache bucket's ``static-30d/sfincs_setup/`` source-class
    prefix (a new dispatch class for staged decks). Per-deck uniqueness is
    enforced via a ULID; HydroMT-determinism would let us cache by content
    hash but the v0.1 smoke skips that.
    """
    from ..tools.cache import storage_scheme

    cache_bucket = os.environ.get("GRACE2_CACHE_BUCKET", "grace-2-hazard-prod-cache")
    setup_id = new_ulid()
    return (
        f"{storage_scheme()}://{cache_bucket}/cache/static-30d/sfincs_setup/"
        f"{setup_id}/manifest.json"
    )


def _generate_hydromt_yaml_config(
    *,
    bbox: tuple[float, float, float, float],
    options: BuildOptions,
    dem_local_path: str,
    landcover_local_path: str,
    river_local_path: str | None,
    forcing: ForcingSpec,
    mapping_csv_path: str,
) -> str:
    """Compose a HydroMT-SFINCS YAML build config string.

    Per OQ-4 §3 + §4: the YAML drives ``hydromt build sfincs`` (or the
    equivalent Python API call). Generated programmatically from the typed
    inputs — never user-input.

    The component list is the v0.1 pluvial-flood capstone shape, with every
    step matched to a hydromt-sfincs 1.2.2 live ``inspect.signature`` cite
    (job-0054 comprehensive migration audit):

      * setup_config — config-file passthrough (``SfincsModel.setup_config``
        takes ``**cfdict`` per inheritance from ``hydromt.Model``). Time
        values (``tref``, ``tstart``, ``tstop``) MUST be in SFINCS format
        ``YYYYMMDD HHMMSS`` (e.g. ``"20260101 000000"``), NOT ISO 8601 —
        ``sfincs_input.py`` parses them via
        ``datetime.strptime(val, "%Y%m%d %H%M%S")``, and
        ``utils.parse_datetime`` uses the same format. ISO 8601 strings
        raise ``ValueError: time data '...' does not match format '%Y%m%d
        %H%M%S'`` inside ``setup_precip_forcing → get_model_time()``.
        (Discovered and fixed in job-0055.)
      * setup_grid_from_region — defines the SFINCS grid. Live sig:
        ``(region: dict, res: float = 100, crs: Union[str, int] = "utm", ...)``.
        We pass ``region: {bbox: [...]}`` + ``res``; ``crs`` left at the
        ``"utm"`` default so HydroMT picks the appropriate UTM zone for the
        bbox (Decision K: minimal parameter surface, derive inside).
      * setup_dep — DEM/topobathy ingest. Live sig:
        ``(datasets_dep: List[dict], buffer_cells: int = 0, interp_method:
        str = "linear")``. We pass ``datasets_dep: [{elevtn: <path>}]``.
      * setup_mask_active — active-cell mask. Live sig accepts ``zmin`` +
        ``zmax`` as keyword args; we pass both.
      * setup_manning_roughness — Manning's grid via NLCD + the reclass CSV.
        Live sig: ``(datasets_rgh: List[dict] = [], manning_land=0.04,
        manning_sea=0.02, rgh_lev_land=0)`` — NO top-level ``map_fn``
        (OQ-52). The reclass table lives INSIDE each ``datasets_rgh`` entry
        under key ``reclass_table`` (per ``_parse_datasets_rgh``: each dict
        supports ``manning`` (gridded n) OR ``lulc`` + ``reclass_table``);
        the CSV must be ``index_col=0`` + column literally ``N`` —
        ``_write_hydromt_reclass_table_csv`` materializes that view.
      * setup_river_inflow — NOT EMITTED for v0.1 pluvial deck. The v0.1
        M5 demo is pluvial-only (Atlas 14 design storm, no river forcing
        required). Additionally, hydromt-sfincs 1.2.2's ``set_forcing_1d``
        (sfincs.py:1858) calls ``pd.RangeIndex.is_integer()`` which was
        removed in pandas ≥ 2.0 (we run 3.0.3); this upstream library bug
        is exercised by the river-inflow discharge-point path. Dropping this
        block bypasses ``set_forcing_1d`` entirely and lets the chain reach
        solver dispatch (job-0055, OQ-54 routing recommendation b).
        The ``river_local_path`` parameter is RETAINED in the function
        signature (so call sites still pass the cached FGB without change);
        re-enabling river inflow for v0.2+ (real ATCF storm surge) requires
        adding this block back AND either pinning pandas < 2.0 or applying
        the upstream hydromt-sfincs fix (``pd.api.types.is_integer_dtype``).
      * setup_precip_forcing — uniform precip forcing. Live sig:
        ``(timeseries=None, magnitude=None)`` — accepts EITHER a tabulated
        timeseries CSV OR a single ``magnitude`` float in ``mm/hr``
        (constant rate over the simulation window, then projected onto a
        10-minute time grid). OQ-54 fix: we previously emitted ``precip``
        + ``duration_hr`` (neither is a 1.2.x parameter); we now emit
        ``magnitude: <mm_per_hr>`` derived from Atlas 14 depth ÷ duration.
        The Atlas 14 depth + duration are still echoed via the inline YAML
        comment so the provenance trail survives.

    Returns the YAML as a string. Test code parses it back; the production
    runtime writes it to a temp file and points HydroMT at it.
    """
    crs = options.crs
    grid_res = options.grid_resolution_m
    components: list[str] = []
    components.append("setup_config:")
    components.append(f"  crs: {crs}")
    # Time values MUST be in SFINCS format "YYYYMMDD HHMMSS" — sfincs_input.py
    # parses them with strptime(val, "%Y%m%d %H%M%S"). ISO 8601 format raises
    # ValueError inside setup_precip_forcing -> get_model_time() (job-0055).
    sim_days = max(1, int(options.simulation_hours / 24))
    tstop_day = 1 + sim_days
    components.append(f'  tref: "20260101 000000"')
    components.append(f'  tstart: "20260101 000000"')
    components.append(f'  tstop: "202601{tstop_day:02d} 000000"')
    components.append("setup_grid_from_region:")
    components.append(
        f"  region: {{ bbox: [{bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]}] }}"
    )
    components.append(f"  res: {grid_res}")
    # job-0248 (supersedes the job-0170 /vsigs/ rewrite FOR THE CATALOG PATH
    # ONLY): HydroMT's data adapter stats catalog paths with fsspec's LOCAL
    # filesystem before GDAL ever opens them, so a /vsigs/ GDAL-ism raises
    # "No such file found" even when the GCS object exists (proven live,
    # round-5 Stage 3). gs:// inputs are therefore STAGED to a local cache
    # via google-cloud-storage (ADC) and HydroMT receives a real local path
    # — which also keeps gcsfs out of the read path (job-0170's segfault
    # avoidance holds). Direct rasterio reads elsewhere still use /vsigs/.
    dem_read_path = _stage_gcs_local(dem_local_path)
    landcover_read_path = _stage_gcs_local(landcover_local_path)
    components.append("setup_dep:")
    components.append(f"  datasets_dep: [{{ elevtn: '{dem_read_path}' }}]")
    components.append("setup_mask_active:")
    components.append("  zmin: -10.0")
    components.append("  zmax: 10.0")
    components.append("setup_manning_roughness:")
    components.append(
        f"  datasets_rgh: [{{ lulc: '{landcover_read_path}', "
        f"reclass_table: '{mapping_csv_path}' }}]"
    )
    # v0.1 SCOPE DECISION (job-0055, OQ-54 routing recommendation b):
    # ``setup_river_inflow`` is intentionally NOT emitted for the v0.1 pluvial
    # deck. Two reasons:
    #   1. Scope: the v0.1 M5 demo is pluvial-only (Atlas 14 design storm);
    #      river inflow is M5+ / sprint-9+ scope (real ATCF + storm surge).
    #   2. Upstream bug: hydromt-sfincs 1.2.2's ``set_forcing_1d``
    #      (sfincs.py:1858) calls ``pd.RangeIndex.is_integer()`` which was
    #      removed in pandas ≥ 2.0 (we run pandas 3.0.3). This bug is
    #      exercised by the river-inflow discharge-point path — dropping this
    #      block bypasses ``set_forcing_1d`` entirely and lets the chain
    #      proceed to solver dispatch without triggering the upstream defect.
    # ``river_local_path`` is kept in the function signature so call sites
    # continue to pass the cached NHDPlus HR FlatGeobuf unchanged; the FGB
    # is fetched and cached for future use but not wired into this deck.
    # To re-enable for v0.2+ (real ATCF + river inflow): add the block back
    # AND pin pandas < 2.0 OR apply the upstream patch
    # (``pd.api.types.is_integer_dtype(idx)`` instead of ``idx.is_integer()``).
    # --- Precip forcing emission (uniform netamt magnitude) ---
    #
    # Two upstream paths converge on the same SFINCS ``setup_precip_forcing``
    # ``magnitude`` (mm/hr) — a single uniform precipitation hyetograph the
    # source projects onto a 10-minute time grid (``get_model_time()``):
    #
    #   1. ``pluvial_synthetic`` (Atlas 14 design storm, M5 v0.1): the
    #      magnitude is DERIVED here from ``precip_inches`` over
    #      ``duration_hours`` (depth → rate arithmetic).
    #   2. ``pluvial_observed`` (job-0225 v2, real precip raster): the
    #      magnitude is PRE-COMPUTED by ``model_flood_scenario``'s
    #      ``forcing_raster_uri`` branch (area-mean of the precip raster over
    #      the model domain, in mm, divided by the accumulation window) and
    #      carried on ``forcing.precip_magnitude_mm_per_hr``. We emit it
    #      verbatim — this is the netamt fallback locked by OQ-6 (see below).
    #
    # OQ-6 (manifest, TENTATIVE → LOCKED here): SFINCS accepts precipitation
    # as ``netamt`` (uniform mm/hr — what ``setup_precip_forcing``'s
    # ``magnitude`` produces) OR ``spw`` (spatially-variable precip via
    # NetCDF). v0.1 maps a precip raster to a SINGLE area-mean ``magnitude``
    # (netamt). This collapses spatial structure but demonstrates the
    # real-data forcing path end-to-end. SPW UPGRADE PATH: when the SFINCS
    # container is confirmed to support spw spatially-varying precip, replace
    # this single-magnitude emission for ``pluvial_observed`` with a
    # ``setup_precip_forcing_from_grid`` (hydromt-sfincs ≥ 1.1) step that
    # ingests the precip raster as a time-resolved 2D grid → SFINCS
    # ``precip_2d.nc`` (spw). That keeps the raster's spatial gradient (e.g.
    # an MRMS QPE band crossing the domain) instead of flattening to a mean.
    # The container-support finding for spw is recorded in this job's
    # report.md (job-0225).
    if (
        forcing.forcing_type == "pluvial_observed"
        and forcing.precip_magnitude_mm_per_hr is not None
    ):
        # job-0225 v2 — area-mean netamt path. The magnitude was computed
        # upstream from a real precip raster (MRMS QPE / ERA5 / gridMET); we
        # do NOT re-derive it from depth here. ``precip_inches`` may be None
        # on this path (observed forcing has no Atlas 14 depth).
        magnitude_mm_per_hr = forcing.precip_magnitude_mm_per_hr
        accum_hr = forcing.duration_hours or 24.0
        mean_mm = magnitude_mm_per_hr * accum_hr
        components.append("setup_precip_forcing:")
        components.append(
            f"  magnitude: {magnitude_mm_per_hr}  # mm/hr "
            f"(observed precip raster: area-mean {mean_mm:.4f} mm over "
            f"{accum_hr} hr → {magnitude_mm_per_hr:.4f} mm/hr; netamt fallback, "
            "OQ-6 — spw spatial path is the documented upgrade)"
        )
    elif forcing.forcing_type == "pluvial_synthetic" and forcing.precip_inches is not None:
        # OQ-54 fix (job-0054): the live 1.2.x signature is
        # ``setup_precip_forcing(timeseries=None, magnitude=None)``; ``precip``
        # / ``duration_hr`` (what we previously emitted) are NOT accepted
        # kwargs and would raise ``TypeError: got an unexpected keyword
        # argument``. We convert Atlas 14 (depth in inches over duration
        # hours) to a constant rate in mm/hr and pass ``magnitude``:
        #
        #     magnitude = precip_inches * 25.4 / duration_hours    [mm/hr]
        #
        # SFINCS receives this as a uniform precipitation hyetograph (the
        # source builds a 10-minute time grid from ``get_model_time()`` and
        # fills with ``magnitude``).
        duration_hr = forcing.duration_hours or 24.0
        magnitude_mm_per_hr = (forcing.precip_inches * 25.4) / duration_hr
        components.append("setup_precip_forcing:")
        components.append(
            f"  magnitude: {magnitude_mm_per_hr}  # mm/hr "
            f"(Atlas 14: {forcing.precip_inches} in over "
            f"{duration_hr} hr → {magnitude_mm_per_hr:.4f} mm/hr)"
        )
    return "\n".join(components)


def build_sfincs_model(
    dem_uri: str,
    landcover_uri: str,
    river_geometry_uri: str | None,
    forcing: ForcingSpec,
    bbox: tuple[float, float, float, float],
    options: BuildOptions | None = None,
    nlcd_vintage_year: int | None = None,
    manning_mapping_csv: Path | str | None = None,
) -> ModelSetup:
    """Build an SFINCS model deck via HydroMT, returning a typed ``ModelSetup``.

    This is the **workflow-internal** model-setup entry point. It is NOT
    registered as an atomic tool — workflows compose it directly inside
    ``model_flood_scenario``.

    Pre-HydroMT invariant gate (the headline):

        Before ``hydromt_sfincs.SfincsModel.setup_manning_roughness`` runs,
        ``validate_nlcd_vintage_against_mapping`` checks that every NLCD class
        integer present in the fetched landcover raster is covered by the
        version-pinned ``manning_mapping.csv``. If not, raises
        ``SFINCSSetupError("LULC_MAPPING_MISMATCH")`` with the unmapped class
        set and vintage year — Invariant 7 (no silent wrong answers).

    Args:
        dem_uri: ``gs://...`` (or local path) URI of the DEM COG produced by
            ``fetch_dem``.
        landcover_uri: ``gs://...`` (or local path) URI of the NLCD GeoTIFF
            produced by ``fetch_landcover``.
        river_geometry_uri: ``gs://...`` (or local path) URI of the NHDPlus HR
            FlatGeobuf produced by ``fetch_river_geometry``; may be ``None``
            for fluvial-irrelevant workflows.
        forcing: ``ForcingSpec`` carrying the SFINCS forcing
            (``precip_inches`` + ``duration_hours`` + ``return_period_years``
            for the v0.1 pluvial path).
        bbox: ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326 — the
            domain the SFINCS grid is built over.
        options: ``BuildOptions`` knobs (grid resolution, simulation hours,
            CRS, output URI override). ``None`` → ``BuildOptions()`` defaults.
        nlcd_vintage_year: the NLCD vintage year from the
            ``fetch_landcover`` sidecar. **Load-bearing** — the validation
            gate is keyed against this. ``None`` is accepted only when
            ``landcover_uri`` is a local fixture in tests; production callers
            always thread the sidecar through.
        manning_mapping_csv: optional override for the Manning's CSV path
            (tests use this to inject a subset CSV that triggers the gate).

    Returns:
        ``ModelSetup{setup_id, solver="sfincs", setup_uri, grid_resolution_m,
        bbox, parameters: {...}, created_at}`` — the staged deck URI is what
        the workflow hands to ``run_solver``.

    Raises:
        SFINCSSetupError("LULC_MAPPING_MISMATCH"): the gate fired.
        SFINCSSetupError("LANDCOVER_READ_FAILED"): the landcover raster could
            not be read.
        SFINCSSetupError("MANNING_MAPPING_LOAD_FAILED"): the mapping CSV could
            not be loaded.
        SFINCSSetupError("HYDROMT_UNAVAILABLE"): ``hydromt_sfincs`` not
            importable in the runtime.
        SFINCSSetupError("HYDROMT_BUILD_FAILED"): HydroMT raised during build.
        SFINCSSetupError("FORCING_OUT_OF_RANGE"): forcing has no precip depth.
    """
    opts = options or BuildOptions()

    # --- Forcing sanity ---
    if (
        forcing.forcing_type == "pluvial_synthetic"
        and (forcing.precip_inches is None or forcing.precip_inches <= 0)
    ):
        raise SFINCSSetupError(
            "FORCING_OUT_OF_RANGE",
            message=(
                f"pluvial forcing requires positive precip_inches; "
                f"got {forcing.precip_inches!r}"
            ),
            details={"forcing": forcing.__dict__},
        )
    # job-0225 v2: the observed-precip-raster (netamt area-mean) path carries
    # a pre-computed ``precip_magnitude_mm_per_hr`` instead of an Atlas 14
    # ``precip_inches`` depth. Require it to be positive — a zero / missing
    # magnitude would silently emit no precip forcing (Invariant 7: a flood
    # deck with no rainfall is a silent-wrong-answer).
    if forcing.forcing_type == "pluvial_observed" and (
        forcing.precip_magnitude_mm_per_hr is None
        or forcing.precip_magnitude_mm_per_hr <= 0
    ):
        raise SFINCSSetupError(
            "FORCING_OUT_OF_RANGE",
            message=(
                "pluvial_observed forcing requires positive "
                f"precip_magnitude_mm_per_hr; got "
                f"{forcing.precip_magnitude_mm_per_hr!r}"
            ),
            details={"forcing": forcing.__dict__},
        )

    # --- Step 1: Manning's mapping load ---
    mapping = load_manning_mapping(manning_mapping_csv)
    mapping_path = str(
        Path(manning_mapping_csv) if manning_mapping_csv else MANNING_MAPPING_PATH
    )
    logger.info(
        "manning_mapping loaded version=%s classes=%d path=%s",
        MANNING_MAPPING_VERSION,
        len(mapping),
        mapping_path,
    )

    # --- Step 2: extract fetched NLCD class set + validate (the headline gate) ---
    fetched_classes = _extract_unique_nlcd_classes(landcover_uri)
    logger.info(
        "landcover classes observed: %s (vintage_year=%s)",
        sorted(fetched_classes),
        nlcd_vintage_year,
    )
    if nlcd_vintage_year is not None:
        validate_nlcd_vintage_against_mapping(
            fetched_classes=fetched_classes,
            nlcd_vintage_year=nlcd_vintage_year,
            mapping=mapping,
            mapping_version=MANNING_MAPPING_VERSION,
            mapping_csv_path=mapping_path,
        )
    else:
        logger.warning(
            "build_sfincs_model called with nlcd_vintage_year=None — "
            "the OQ-4 §4 validation gate cannot run. Production callers MUST "
            "thread the fetch_landcover sidecar through."
        )

    # --- Step 3: invoke HydroMT-SFINCS ---
    # Lazy import so test environments without HydroMT installed can still
    # import this module and exercise the validation gate.
    try:
        from hydromt_sfincs import SfincsModel  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise SFINCSSetupError(
            "HYDROMT_UNAVAILABLE",
            message=(
                f"hydromt_sfincs not importable: {exc}; agent runtime must "
                "include the dep (job-0040 SFINCS container bundles it per "
                "OQ-4 §4)."
            ),
            details={"import_error": str(exc)},
        ) from exc

    # Resolve manifest URI + directory base URI.
    # The worker contract (services/workers/sfincs/entrypoint.py:9-23) requires
    # ``manifest_uri`` to be a single JSON FILE that the worker reads via
    # ``blob.download_as_text()``.  A trailing-slash directory URI hits 404.
    #
    # ``_default_setup_uri`` already returns a manifest file URI; if the caller
    # passes an ``output_setup_uri`` override we normalise it: if it ends with
    # ``/manifest.json`` we use it as-is; if it ends with ``/`` (directory) we
    # append ``manifest.json``; otherwise we treat it as the manifest URI.
    _raw_setup_uri = opts.output_setup_uri or _default_setup_uri(bbox)
    if _raw_setup_uri.endswith("/manifest.json"):
        manifest_uri = _raw_setup_uri
    elif _raw_setup_uri.endswith("/"):
        manifest_uri = _raw_setup_uri + "manifest.json"
    else:
        # Assume it's already a manifest file URI (no trailing slash).
        manifest_uri = _raw_setup_uri
    # The directory base is the manifest URI without the ``manifest.json`` suffix.
    deck_base_uri = manifest_uri[: -len("manifest.json")]  # ends with "/"

    # YAML config + HydroMT invocation. We build inside a temp dir, then
    # upload the staged deck + a manifest.json via fsspec[gcs]. The hand-off
    # to run_solver is the gs:// URI of the manifest JSON file (NOT the
    # directory), matching the worker contract.
    with tempfile.TemporaryDirectory(prefix="sfincs-build-") as tmpdir:
        tmp = Path(tmpdir)
        # OQ-52 hotfix (job-0053): the authored ``manning_mapping.csv`` uses
        # our column names (``nlcd_class,manning_n,description``) that are
        # load-bearing for ``load_manning_mapping`` + the OQ-4 §4 validation
        # gate. hydromt-sfincs 1.2.x expects ``index_col=0`` + a column
        # literally named ``N``. Write a v1.2.x-shaped reclass table into the
        # build's temp dir and point the YAML config's ``reclass_table`` key
        # at it — the original substrate file is unchanged.
        reclass_csv_path = _write_hydromt_reclass_table_csv(
            mapping, tmp / "manning_reclass.csv"
        )
        yaml_path = tmp / "sfincs_build.yml"
        yaml_text = _generate_hydromt_yaml_config(
            bbox=bbox,
            options=opts,
            # job-0170: ``_generate_hydromt_yaml_config`` rewrites
            # ``gs://`` → ``/vsigs/`` internally so HydroMT's
            # ``rioxarray.open_rasterio`` reads through GDAL's native
            # libcurl backend, not the segfault-prone ``gcsfs``.
            dem_local_path=dem_uri,
            landcover_local_path=landcover_uri,
            river_local_path=river_geometry_uri,
            forcing=forcing,
            mapping_csv_path=str(reclass_csv_path),
        )
        yaml_path.write_text(yaml_text, encoding="utf-8")
        try:
            # OQ-49 hotfix (job-0052): hydromt-sfincs 1.2.x expects ``opt`` as a
            # parsed ``Dict[str, Dict[str, Any]]`` (step-name → step-kwargs), NOT
            # a raw YAML text string. Passing the unparsed string raises
            # ``'str' object has no attribute 'keys'`` deep inside HydroMT's
            # ``_parse_steps``. Parse with ``yaml.safe_load`` here so the dict
            # the v1.x API documents is what reaches ``SfincsModel.build``.
            # Malformed YAML at this seam surfaces as ``HYDROMT_BUILD_FAILED``
            # via the broad except below (FR-FR-2 substrate-integrity routing).
            opt_dict = yaml.safe_load(yaml_text)
            model = SfincsModel(root=str(tmp / "deck"), mode="w")
            # ``SfincsModel.build`` is the v1.x entrypoint; v2 RC changes to
            # component-based, hence the OQ-4 §4 pin to < 2.0.
            model.build(opt=opt_dict)
            model.write()
        except SFINCSSetupError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SFINCSSetupError(
                "HYDROMT_BUILD_FAILED",
                message=f"HydroMT SfincsModel build failed: {exc}",
                details={
                    "bbox": list(bbox),
                    "dem_uri": dem_uri,
                    "landcover_uri": landcover_uri,
                    "river_geometry_uri": river_geometry_uri,
                    "underlying": str(exc),
                },
            ) from exc

        # --- Compose the worker-contract manifest.json ---
        # Worker reads this file first (services/workers/sfincs/entrypoint.py
        # line 121-130, ``_read_manifest``); it must exist at ``manifest_uri``
        # as a JSON object matching:
        #   {
        #     "inputs": [{"gs_uri": "gs://...", "dest": "<relative-path>"}],
        #     "sfincs_args": [],
        #     "outputs": ["sfincs_map.nc", "*.nc", "*.tif"]
        #   }
        #
        # fsspec.upload(deck_dir, deck_base_uri, recursive=True) uploads
        # the "deck" directory itself as a child of deck_base_uri — i.e.
        # files land at ``{deck_base_uri}deck/{relative_path}``, not at
        # ``{deck_base_uri}{relative_path}``.  The manifest gs_uri values must
        # reflect this actual upload layout (deck/ subdirectory).
        #
        # ``dest`` is the relative path from the deck directory root; the
        # worker does ``dest = scratch / item["dest"]`` and creates any needed
        # parent dirs, so ``gis/dep.tif`` works fine.
        deck_dir = tmp / "deck"
        # The on-GCS prefix for deck files: deck_base_uri + "deck/" because
        # fsspec recursive upload preserves the source directory name.
        deck_gcs_prefix = deck_base_uri + "deck/"
        deck_files = sorted(
            f for f in deck_dir.glob("**/*") if f.is_file()
        )
        manifest_inputs = []
        for deck_file in deck_files:
            # Relative path from the deck dir root (e.g. "sfincs.inp" or
            # "gis/dep.tif").  Use POSIX separators — GCS key is a POSIX path.
            rel = deck_file.relative_to(deck_dir).as_posix()
            gs_uri = deck_gcs_prefix + rel
            dest = rel  # worker does scratch / dest; preserves any subdir
            manifest_inputs.append({"gs_uri": gs_uri, "dest": dest})
        manifest_dict = {
            "inputs": manifest_inputs,
            "sfincs_args": [],
            "outputs": ["sfincs_map.nc", "*.nc", "*.tif"],
        }
        manifest_local = tmp / "manifest.json"
        manifest_local.write_text(
            json.dumps(manifest_dict, indent=2), encoding="utf-8"
        )
        logger.info(
            "composed manifest.json with %d input(s); deck_prefix=%s target=%s",
            len(manifest_inputs),
            deck_gcs_prefix,
            manifest_uri,
        )

        # Upload staged deck + manifest to the object store. Best-effort:
        # fall back to a local URI if the store is unavailable (smoke run
        # still produces a typed ModelSetup the workflow can hand to
        # run_solver, which will surface its own dispatch error if the URI
        # isn't reachable).
        #
        # job-0291 (sprint-14-aws): scheme-aware. ``s3://`` manifests upload
        # via **boto3** (the job-0289 lesson — s3fs falls back to anonymous
        # on the EC2 instance role; boto3 resolves IMDS credentials). The
        # gs:// branch is byte-identical to the pre-job-0291 fsspec[gcs]
        # path. Both branches preserve the ``deck/`` sub-prefix layout the
        # manifest's input URIs cite.
        final_setup_uri = manifest_uri
        try:
            if manifest_uri.startswith("s3://"):
                from ..tools.solver import _get_s3_client

                s3 = _get_s3_client()
                s3_bucket, _, manifest_key = (
                    manifest_uri[len("s3://"):].partition("/")
                )
                # Deck files land under <deck_base_key>/deck/<rel> — same
                # layout the fsspec recursive upload produces on GCS.
                deck_base_key = manifest_key[: -len("manifest.json")]
                for deck_file in deck_files:
                    rel = deck_file.relative_to(deck_dir).as_posix()
                    with deck_file.open("rb") as fh:
                        s3.put_object(
                            Bucket=s3_bucket,
                            Key=f"{deck_base_key}deck/{rel}",
                            Body=fh,
                        )
                logger.info(
                    "uploaded SFINCS deck to %s (under deck/ prefix, boto3)",
                    deck_base_uri,
                )
                with manifest_local.open("rb") as fh:
                    s3.put_object(
                        Bucket=s3_bucket,
                        Key=manifest_key,
                        Body=fh,
                        ContentType="application/json",
                    )
                logger.info("uploaded manifest.json to %s (boto3)", manifest_uri)
            else:
                import fsspec  # type: ignore[import-not-found]

                fs = fsspec.filesystem("gcs")
                # fsspec.upload(src_dir, dest_prefix, recursive=True) uploads
                # src_dir as a child of dest_prefix, so deck files land at
                # deck_base_uri/deck/<relative_path>.
                fs.upload(str(deck_dir), deck_base_uri, recursive=True)
                logger.info("uploaded SFINCS deck to %s (under deck/ prefix)", deck_base_uri)
                # Upload manifest.json alongside the deck directory.
                fs.upload(str(manifest_local), manifest_uri)
                logger.info("uploaded manifest.json to %s", manifest_uri)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "deck/manifest upload failed (%s); using local manifest URI",
                exc,
            )
            final_setup_uri = f"file://{manifest_local}"

    setup_uri = final_setup_uri
    return ModelSetup(
        setup_id=new_ulid(),
        solver="sfincs",
        setup_uri=setup_uri,
        grid_resolution_m=opts.grid_resolution_m,
        bbox=bbox,
        parameters={
            "crs": opts.crs,
            "simulation_hours": opts.simulation_hours,
            "manning_mapping_version": MANNING_MAPPING_VERSION,
            "manning_mapping_path": mapping_path,
            "nlcd_vintage_year": nlcd_vintage_year,
            "fetched_classes": sorted(fetched_classes),
            "forcing_type": forcing.forcing_type,
            "forcing_provenance": dict(forcing.provenance),
            "river_geometry_uri": river_geometry_uri,
        },
        created_at=datetime.now(timezone.utc),
    )
