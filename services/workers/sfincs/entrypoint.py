"""SFINCS Cloud Run Job entrypoint — thin shim around the upstream binary.

Contract (sprint-07 / M5 / FR-CE-1/2/3):

    Input  (env or CLI):
        --run-id RUN_ID
            Run identifier. Outputs land under
            gs://${GRACE2_RUNS_BUCKET}/${RUN_ID}/.
        --manifest-uri gs://bucket/path/setup.json
            JSON setup manifest. Schema:
                {
                  "inputs": [
                    {"gs_uri": "gs://.../dem.tif", "dest": "dem.tif"},
                    {"gs_uri": "gs://.../sfincs.inp", "dest": "sfincs.inp"},
                    ...
                  ],
                  "sfincs_args": ["..."],           # optional argv to sfincs
                  "outputs": [                       # glob patterns to upload
                    "sfincs_map.nc",
                    "*.nc",
                    "*.tif"
                  ]
                }
            All `inputs` are downloaded into the scratch dir before SFINCS
            runs; all `outputs` (glob expansion) are uploaded to the runs
            bucket after SFINCS exits.

    Output:
        gs://${GRACE2_RUNS_BUCKET}/${RUN_ID}/<every output file>
        gs://${GRACE2_RUNS_BUCKET}/${RUN_ID}/completion.json
            Terminal manifest. Schema:
                {
                  "run_id": "<run_id>",
                  "status": "ok" | "error",
                  "exit_code": <int>,
                  "sfincs_stdout_uri": "gs://.../sfincs.stdout",
                  "sfincs_stderr_uri": "gs://.../sfincs.stderr",
                  "output_uris": ["gs://.../<path>", ...],
                  "started_at": "<ISO8601 Z>",
                  "finished_at": "<ISO8601 Z>",
                  "error": "<message>" | null
                }
            The agent's `wait_for_completion` (job-0041) polls this object;
            its presence with status="ok" or status="error" is the terminal
            signal. Truthful: NOT in this image's scope to assert the SFINCS
            run is physically valid — only that the binary executed.

Design notes:
    - The SFINCS binary at /usr/local/bin/sfincs takes its inputs from CWD:
      the classic SFINCS deck expects sfincs.inp + grid + forcings in the
      run directory. We chdir into the scratch dir before exec.
    - We do NOT mount GCS; we download via google-cloud-storage SDK. The
      runs bucket mount adds gcsfuse complexity for no benefit on this
      worker's M5 footprint (outputs are bounded; explicit upload is
      auditable).
    - The smoke-run pattern (kickoff verification): a tiny synthetic
      manifest with no `inputs` and an `sfincs_args` that asks SFINCS to
      `--help` or fails gracefully demonstrates the wiring even with no
      valid model deck.
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

from google.cloud import storage  # type: ignore

LOG = logging.getLogger("grace2.worker.sfincs")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

SFINCS_BIN = os.environ.get("GRACE2_SFINCS_BIN", "/usr/local/bin/sfincs")
SCRATCH = Path(os.environ.get("GRACE2_SFINCS_SCRATCH", "/opt/grace2/work"))
GCP_PROJECT = os.environ.get("GCP_PROJECT", "grace-2-hazard-prod")
RUNS_BUCKET = os.environ.get("GRACE2_RUNS_BUCKET", "grace-2-hazard-prod-runs")


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"not a gs:// URI: {uri!r}")
    path = uri[len("gs://") :]
    bucket, _, blob = path.partition("/")
    if not bucket or not blob:
        raise ValueError(f"malformed gs:// URI: {uri!r}")
    return bucket, blob


def _download(client: storage.Client, gs_uri: str, dest: Path) -> None:
    bucket_name, blob_name = _parse_gs_uri(gs_uri)
    dest.parent.mkdir(parents=True, exist_ok=True)
    LOG.info("downloading %s -> %s", gs_uri, dest)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(str(dest))


def _upload(client: storage.Client, src: Path, gs_uri: str) -> str:
    bucket_name, blob_name = _parse_gs_uri(gs_uri)
    LOG.info("uploading %s -> %s", src, gs_uri)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(src))
    return gs_uri


def _read_manifest(client: storage.Client, manifest_uri: str) -> dict:
    bucket_name, blob_name = _parse_gs_uri(manifest_uri)
    LOG.info("reading manifest %s", manifest_uri)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    text = blob.download_as_text()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    return data


def _prepare_scratch() -> Path:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return SCRATCH


def _run_sfincs(args: list[str], cwd: Path) -> tuple[int, Path, Path]:
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
    LOG.info("sfincs exit=%d stdout_bytes=%d stderr_bytes=%d",
             proc.returncode, stdout_path.stat().st_size, stderr_path.stat().st_size)
    return proc.returncode, stdout_path, stderr_path


def _expand_outputs(patterns: list[str], cwd: Path) -> list[Path]:
    seen: set[Path] = set()
    for pat in patterns:
        for hit in glob.glob(str(cwd / pat)):
            p = Path(hit)
            if p.is_file():
                seen.add(p.resolve())
    return sorted(seen)


def _build_argv_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grace2-sfincs-entrypoint",
        description="GRACE-2 SFINCS Cloud Run Job entrypoint (FR-CE-1/2/3).",
    )
    p.add_argument(
        "--run-id",
        default=os.environ.get("GRACE2_RUN_ID", "").strip(),
        help="Run identifier (also $GRACE2_RUN_ID).",
    )
    p.add_argument(
        "--manifest-uri",
        default=os.environ.get("GRACE2_MANIFEST_URI", "").strip(),
        help="gs:// URI of the setup manifest (also $GRACE2_MANIFEST_URI).",
    )
    return p


def _write_completion(
    client: storage.Client,
    run_id: str,
    status: str,
    exit_code: int,
    output_uris: list[str],
    stdout_uri: str | None,
    stderr_uri: str | None,
    started_at: str,
    error: str | None,
) -> str:
    payload = {
        "run_id": run_id,
        "status": status,
        "exit_code": exit_code,
        "sfincs_stdout_uri": stdout_uri,
        "sfincs_stderr_uri": stderr_uri,
        "output_uris": output_uris,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "error": error,
    }
    completion_uri = f"gs://{RUNS_BUCKET}/{run_id}/completion.json"
    bucket_name, blob_name = _parse_gs_uri(completion_uri)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(
        json.dumps(payload, indent=2),
        content_type="application/json",
    )
    LOG.info("wrote completion -> %s", completion_uri)
    return completion_uri


def main(argv: list[str] | None = None) -> int:
    parser = _build_argv_parser()
    args = parser.parse_args(argv)

    run_id = args.run_id
    manifest_uri = args.manifest_uri
    if not run_id:
        LOG.error("run_id is required (pass --run-id or set $GRACE2_RUN_ID)")
        return 2
    if not manifest_uri:
        LOG.error("manifest_uri is required (pass --manifest-uri or set $GRACE2_MANIFEST_URI)")
        return 2

    LOG.info("grace-2-sfincs-solver starting — project=%s run_id=%s manifest=%s",
             GCP_PROJECT, run_id, manifest_uri)
    started_at = _utc_now()
    client = storage.Client(project=GCP_PROJECT)

    # Best-effort completion writing: even on hard error we attempt to write
    # completion.json so wait_for_completion (job-0041) sees a terminal state
    # instead of polling forever.
    output_uris: list[str] = []
    stdout_uri: str | None = None
    stderr_uri: str | None = None
    error_msg: str | None = None
    exit_code = 1
    status = "error"

    try:
        manifest = _read_manifest(client, manifest_uri)
        inputs = manifest.get("inputs", []) or []
        sfincs_args = manifest.get("sfincs_args", []) or []
        outputs = manifest.get("outputs", []) or []

        scratch = _prepare_scratch()

        for item in inputs:
            gs_uri = item["gs_uri"]
            dest = scratch / item["dest"]
            _download(client, gs_uri, dest)

        rc, stdout_path, stderr_path = _run_sfincs(list(sfincs_args), scratch)

        # Always upload stdout/stderr so the smoke run produces evidence.
        stdout_uri = _upload(
            client, stdout_path, f"gs://{RUNS_BUCKET}/{run_id}/sfincs.stdout"
        )
        stderr_uri = _upload(
            client, stderr_path, f"gs://{RUNS_BUCKET}/{run_id}/sfincs.stderr"
        )

        for path in _expand_outputs(list(outputs), scratch):
            rel = path.relative_to(scratch)
            uri = _upload(client, path, f"gs://{RUNS_BUCKET}/{run_id}/{rel}")
            output_uris.append(uri)

        exit_code = rc
        status = "ok" if rc == 0 else "error"
        if rc != 0:
            error_msg = f"sfincs exited with non-zero code {rc}"

    except Exception as exc:  # pragma: no cover — defensive, logged + emitted
        LOG.exception("solver entrypoint failed")
        error_msg = f"{type(exc).__name__}: {exc}"
        exit_code = 1
        status = "error"

    _write_completion(
        client=client,
        run_id=run_id,
        status=status,
        exit_code=exit_code,
        output_uris=output_uris,
        stdout_uri=stdout_uri,
        stderr_uri=stderr_uri,
        started_at=started_at,
        error=error_msg,
    )
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
