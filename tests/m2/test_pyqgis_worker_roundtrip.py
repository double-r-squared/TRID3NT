"""M2 acceptance — PyQGIS worker round-trip via Cloud Run Job.

Sprint-04 EC4 + EC5 verification: the PyQGIS worker Cloud Run Job
(job-0020 code + job-0021 container) reads a ``.qgs`` from the GCS bucket
via the gen2 writable mount at ``/mnt/qgs``, appends a typed polygon
layer, writes the project back, and publishes a typed completion envelope
to the ``grace-2-worker-events`` Pub/Sub topic.

Test flow (one shared test fixture, three asserting tests):

1. ``worker_run_result`` (session fixture):
   - copies the canonical sample ``.qgs`` to a unique
     ``acceptance-<random>.qgs`` object in ``gs://grace-2-hazard-prod-qgs/``
   - creates a temp Pub/Sub subscription on the worker-events topic so the
     completion envelope is captured at the moment of publish
   - invokes the Cloud Run Job via ``gcloud run jobs execute --wait`` with
     ``--args=--qgs-uri,/mnt/qgs/<test-input>,--layer-to-add,
     acceptance-test-layer-<random>``
   - pulls the published envelope, then deletes the temp subscription
     and the test object at teardown.

2. Three tests assert on the captured result:
   - ``test_worker_job_execute_succeeds`` — execution status is Succeeded.
   - ``test_worker_mutation_visible_in_gcs`` — downloaded mutated ``.qgs``
     contains 2 layers (the canonical ``basemap-osm-conus`` plus the
     appended ``acceptance-test-layer-*``).
   - ``test_worker_publishes_envelope`` — envelope has the expected shape
     (``qgs_uri``, ``layers_after``, ``status=ok``, plus the worker-side
     ``qgs_version`` + ``ts`` fields documented in
     ``services/workers/pyqgis/types.py``).

Each failure message names its layer per the testing.md "diagnose before
fix" discipline.
"""

from __future__ import annotations

import dataclasses
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import textwrap
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(
    cmd: list[str],
    *,
    timeout: float = 600.0,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with text-mode capture. Returns the CompletedProcess."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
        env=env,
    )


def _gcloud_storage_cp(
    gcloud: str, src: str, dst: str, project: str
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            gcloud,
            "storage",
            "cp",
            src,
            dst,
            f"--project={project}",
        ],
        timeout=120,
    )


def _gcloud_storage_rm(
    gcloud: str, target: str, project: str
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            gcloud,
            "storage",
            "rm",
            target,
            f"--project={project}",
        ],
        timeout=60,
    )


def _gcloud_storage_stat(
    gcloud: str, target: str, project: str
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            gcloud,
            "storage",
            "objects",
            "describe",
            target,
            f"--project={project}",
            "--format=json",
        ],
        timeout=60,
    )


def _create_temp_subscription(
    gcloud: str,
    *,
    project: str,
    topic: str,
    sub: str,
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            gcloud,
            "pubsub",
            "subscriptions",
            "create",
            sub,
            f"--topic={topic}",
            f"--project={project}",
            "--ack-deadline=60",
            "--message-retention-duration=10m",
        ],
        timeout=60,
    )


def _delete_subscription(
    gcloud: str, *, project: str, sub: str
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            gcloud,
            "pubsub",
            "subscriptions",
            "delete",
            sub,
            f"--project={project}",
        ],
        timeout=60,
    )


def _pull_subscription(
    gcloud: str,
    *,
    project: str,
    sub: str,
    limit: int = 10,
    timeout_seconds: float = 120.0,
    poll_interval: float = 5.0,
) -> list[dict[str, Any]]:
    """Pull messages from ``sub`` until at least one is received or timeout.

    Pub/Sub message delivery to a new subscription can take a few seconds;
    we poll with ``--auto-ack`` until the subscription returns a message
    or the deadline elapses. Returns the parsed JSON message list.
    """
    deadline = time.monotonic() + timeout_seconds
    last_stdout = ""
    while time.monotonic() < deadline:
        proc = _run(
            [
                gcloud,
                "pubsub",
                "subscriptions",
                "pull",
                sub,
                f"--project={project}",
                f"--limit={limit}",
                "--auto-ack",
                "--format=json",
            ],
            timeout=60,
        )
        last_stdout = proc.stdout
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                messages = json.loads(proc.stdout)
            except json.JSONDecodeError:
                messages = []
            if messages:
                return messages
        time.sleep(poll_interval)
    raise AssertionError(
        "layer=Pub/Sub (subscription pull): no message arrived within "
        f"{timeout_seconds:.0f}s on subscription. Last stdout tail: "
        f"{last_stdout[-400:]!r}"
    )


def _gcloud_run_jobs_execute(
    gcloud: str,
    *,
    project: str,
    region: str,
    job: str,
    args: list[str],
    timeout: float = 600.0,
) -> subprocess.CompletedProcess[str]:
    """Execute a Cloud Run Job synchronously (``--wait``) with task args."""
    args_csv = ",".join(args)
    return _run(
        [
            gcloud,
            "run",
            "jobs",
            "execute",
            job,
            f"--project={project}",
            f"--region={region}",
            f"--args={args_csv}",
            "--wait",
            "--format=json",
        ],
        timeout=timeout,
    )


def _gcloud_run_jobs_executions_list(
    gcloud: str,
    *,
    project: str,
    region: str,
    job: str,
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            gcloud,
            "run",
            "jobs",
            "executions",
            "list",
            f"--job={job}",
            f"--project={project}",
            f"--region={region}",
            "--limit=1",
            "--sort-by=~creationTimestamp",
            "--format=json",
        ],
        timeout=60,
    )


def _count_layers_in_qgs(qgs_path: Path) -> list[str]:
    """Return layer names embedded in a ``.qgs`` (XML) file.

    Avoids depending on PyQGIS in the M1 venv — parses the project XML
    directly. The ``.qgs`` shape is well-defined: layer names live under
    ``<projectlayers>/<maplayer>/<layername>`` (and as ``name`` attrs on
    legend nodes).
    """
    tree = ET.parse(str(qgs_path))
    root = tree.getroot()
    names: list[str] = []
    for ml in root.iter("maplayer"):
        ln = ml.find("layername")
        if ln is not None and ln.text:
            names.append(ln.text)
    return names


# ---------------------------------------------------------------------------
# Shared session fixture — runs the worker once, three tests assert on it
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class WorkerRunResult:
    """Captured artifacts of one live Cloud Run Job execution."""

    test_qgs_key: str
    """GCS object key (not full path) used for this run, e.g. ``acceptance-XXXX.qgs``."""
    test_qgs_uri_local: str
    """``/mnt/qgs/<key>`` form passed to the worker as ``--qgs-uri``."""
    test_qgs_gs_uri: str
    """``gs://<bucket>/<key>`` form for GCS reads."""
    layer_to_add: str
    """Layer name appended by this run, e.g. ``acceptance-test-layer-XXXX``."""
    execute_proc: subprocess.CompletedProcess[str]
    """Result of ``gcloud run jobs execute --wait``."""
    execution_status: dict[str, Any]
    """Parsed ``gcloud run jobs executions list`` payload."""
    envelope: dict[str, Any]
    """Decoded Pub/Sub completion envelope (worker -> grace-2-worker-events)."""
    mutated_qgs_path: Path
    """Local path to the downloaded mutated ``.qgs``."""


@pytest.fixture(scope="module")
def worker_run_result(
    gcloud_bin: str,
    adc_available: bool,
    qgs_bucket: str,
    gcp_project: str,
    gcp_region: str,
    pubsub_topic: str,
    worker_job_name: str,
    repo_root_m2: Path,
    artifacts_dir: Path,
) -> WorkerRunResult:
    """Execute one live worker round-trip end-to-end against the deployed
    Cloud Run Job and capture the published envelope + mutated ``.qgs``.

    The fixture is module-scoped so the three assertions below share the
    same execution — the Cloud Run Job is expensive (~30-60s warm) and
    Pub/Sub temp subscriptions are rate-limited.
    """
    if os.environ.get("GRACE2_SKIP_LIVE_WORKER"):
        pytest.skip(
            "GRACE2_SKIP_LIVE_WORKER=1 — skipping the live Cloud Run Job. "
            "Layer: dev opt-out."
        )
    if not adc_available:
        pytest.skip(
            "layer=dev-env: Application Default Credentials unavailable; "
            "skipping live worker round-trip. Run "
            "'gcloud auth application-default login' to enable."
        )

    canonical_qgs = (
        repo_root_m2
        / "services"
        / "workers"
        / "pyqgis"
        / "sample_project"
        / "grace2-sample.qgs"
    )
    assert canonical_qgs.is_file(), (
        f"layer=engine (job-0019): canonical sample .qgs missing at "
        f"{canonical_qgs!r}; cannot run worker acceptance."
    )

    # Random suffix avoids clobbering the canonical object and avoids name
    # collisions when the suite runs in parallel.
    rand = secrets.token_hex(4)
    test_key = f"acceptance-{rand}.qgs"
    test_layer = f"acceptance-test-layer-{rand}"
    test_gs_uri = f"gs://{qgs_bucket}/{test_key}"
    test_mnt_uri = f"/mnt/qgs/{test_key}"
    sub_name = f"acceptance-{rand}-sub"

    # Upload the canonical .qgs to the test-scoped key first.
    cp = _gcloud_storage_cp(
        gcloud_bin, str(canonical_qgs), test_gs_uri, gcp_project
    )
    assert cp.returncode == 0, (
        f"layer=GCS upload (gcloud storage cp): exit={cp.returncode}; "
        f"stderr tail: {cp.stderr[-400:]!r}"
    )

    # Create the temp Pub/Sub subscription BEFORE the execute call so
    # the envelope is captured at the moment the worker publishes it.
    sub_create = _create_temp_subscription(
        gcloud_bin, project=gcp_project, topic=pubsub_topic, sub=sub_name
    )
    if sub_create.returncode != 0:
        # Roll back the staged GCS object.
        _gcloud_storage_rm(gcloud_bin, test_gs_uri, gcp_project)
        pytest.fail(
            f"layer=Pub/Sub (subscription create): exit="
            f"{sub_create.returncode}; stderr tail: "
            f"{sub_create.stderr[-400:]!r}"
        )

    execute_proc: subprocess.CompletedProcess[str] | None = None
    envelope: dict[str, Any] | None = None
    mutated_local_path = artifacts_dir / f"mutated-{rand}.qgs"
    try:
        # Execute the Cloud Run Job synchronously.
        execute_proc = _gcloud_run_jobs_execute(
            gcloud_bin,
            project=gcp_project,
            region=gcp_region,
            job=worker_job_name,
            args=[
                "--qgs-uri",
                test_mnt_uri,
                "--layer-to-add",
                test_layer,
            ],
            timeout=600.0,
        )

        # Pull the envelope from the temp subscription (best-effort —
        # publish happens at the END of the worker run so by the time
        # execute returns, the message should be in the subscription's
        # backlog).
        try:
            messages = _pull_subscription(
                gcloud_bin,
                project=gcp_project,
                sub=sub_name,
                limit=5,
                timeout_seconds=60,
                poll_interval=3.0,
            )
        except AssertionError as exc:
            # Surface the assertion message but keep the original error as
            # the diagnostic for the worker-publishes-envelope test —
            # the execute_succeeds test will already have flagged a
            # broken Job.
            messages = []
            envelope = {"_pull_error": str(exc)}

        if messages and envelope is None:
            # The most recent message matching our test_qgs_uri wins (the
            # topic may carry envelopes from concurrent runs).
            for msg in messages:
                data = msg.get("message", {}).get("data", "")
                if not data:
                    continue
                try:
                    payload = json.loads(_b64decode(data))
                except Exception:
                    continue
                if payload.get("qgs_uri") == test_mnt_uri:
                    envelope = payload
                    break
            if envelope is None and messages:
                # Fall back to the first decodable message — if execute
                # succeeded the envelope likely matches; the per-uri
                # assertion below will catch a mismatch with a layered
                # error message.
                for msg in messages:
                    data = msg.get("message", {}).get("data", "")
                    if not data:
                        continue
                    try:
                        envelope = json.loads(_b64decode(data))
                        break
                    except Exception:
                        continue

        # Download the mutated .qgs back from GCS for layer-count assertion.
        dl = _run(
            [
                gcloud_bin,
                "storage",
                "cp",
                test_gs_uri,
                str(mutated_local_path),
                f"--project={gcp_project}",
            ],
            timeout=120,
        )
        assert dl.returncode == 0, (
            f"layer=GCS download (gcloud storage cp): exit={dl.returncode}; "
            f"stderr tail: {dl.stderr[-400:]!r}"
        )

        # Pull the most recent execution status for the worker-job test.
        ex_list = _gcloud_run_jobs_executions_list(
            gcloud_bin,
            project=gcp_project,
            region=gcp_region,
            job=worker_job_name,
        )
        execution_status: dict[str, Any] = {}
        if ex_list.returncode == 0 and ex_list.stdout.strip():
            try:
                ex = json.loads(ex_list.stdout)
                if isinstance(ex, list) and ex:
                    execution_status = ex[0]
                elif isinstance(ex, dict):
                    execution_status = ex
            except json.JSONDecodeError:
                execution_status = {}

        # Persist artifacts for the audit.
        (artifacts_dir / f"execute-stdout-{rand}.json").write_text(
            execute_proc.stdout if execute_proc.stdout else ""
        )
        if execute_proc.stderr:
            (artifacts_dir / f"execute-stderr-{rand}.log").write_text(
                execute_proc.stderr
            )
        if envelope is not None:
            (artifacts_dir / f"worker-notify-{rand}.json").write_text(
                json.dumps(envelope, indent=2)
            )

        return WorkerRunResult(
            test_qgs_key=test_key,
            test_qgs_uri_local=test_mnt_uri,
            test_qgs_gs_uri=test_gs_uri,
            layer_to_add=test_layer,
            execute_proc=execute_proc,
            execution_status=execution_status,
            envelope=envelope or {},
            mutated_qgs_path=mutated_local_path,
        )
    finally:
        # Clean up cloud-side state: subscription + test object.
        try:
            _delete_subscription(gcloud_bin, project=gcp_project, sub=sub_name)
        except Exception:
            pass
        try:
            _gcloud_storage_rm(gcloud_bin, test_gs_uri, gcp_project)
        except Exception:
            pass


def _b64decode(s: str) -> bytes:
    import base64

    return base64.b64decode(s + "==")  # tolerate missing padding


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.live_worker
def test_worker_job_execute_succeeds(worker_run_result: WorkerRunResult) -> None:
    """EC4 — ``gcloud run jobs execute`` returns 0 and the most recent
    execution status is Succeeded.
    """
    proc = worker_run_result.execute_proc
    assert proc is not None and proc.returncode == 0, (
        f"layer=Cloud Run Job (execute): non-zero exit "
        f"({proc.returncode if proc else 'no-proc'}); "
        f"stderr tail: {proc.stderr[-600:] if proc and proc.stderr else ''!r}"
    )

    # Best-effort: confirm the latest execution's status condition.
    status = worker_run_result.execution_status
    conditions = []
    if isinstance(status, dict):
        conditions = (
            status.get("status", {}).get("conditions", []) if status else []
        )
    succeeded = False
    for cond in conditions:
        if (
            cond.get("type") in ("Completed", "Ready")
            and cond.get("status") == "True"
        ):
            succeeded = True
            break

    # If the execute call returned 0 but we can't read the status (e.g.
    # gcloud format drift), accept the executor's verdict — this is a
    # diagnostic aid, not a primary signal.
    if status and not succeeded:
        raise AssertionError(
            f"layer=Cloud Run Job (execution status): execution did not "
            f"report Completed=True. Conditions: {conditions!r}. "
            f"Full status: {json.dumps(status)[:600]!r}"
        )


@pytest.mark.live_worker
def test_worker_mutation_visible_in_gcs(
    worker_run_result: WorkerRunResult,
) -> None:
    """EC4 — the mutated ``.qgs`` downloaded from GCS contains 2 layers:
    the canonical ``basemap-osm-conus`` plus the appended
    ``acceptance-test-layer-<random>``.
    """
    path = worker_run_result.mutated_qgs_path
    assert path.is_file() and path.stat().st_size > 0, (
        f"layer=GCS / Cloud Run Job: mutated .qgs missing or empty at "
        f"{path!r}. The worker's GCS upload likely failed."
    )

    layer_names = _count_layers_in_qgs(path)
    assert len(layer_names) == 2, (
        f"layer=PyQGIS worker (job-0020): expected 2 layers after the "
        f"mutation (canonical 'basemap-osm-conus' + appended "
        f"{worker_run_result.layer_to_add!r}), got {len(layer_names)}: "
        f"{layer_names!r}. Likely cause: the worker did not append a "
        f"layer, or QgsProject.write() corrupted the project XML."
    )
    assert "basemap-osm-conus" in layer_names, (
        f"layer=.qgs content: canonical 'basemap-osm-conus' layer is "
        f"missing from the mutated project. Layer names: {layer_names!r}. "
        f"The worker likely dropped the existing layer instead of "
        f"appending."
    )
    assert worker_run_result.layer_to_add in layer_names, (
        f"layer=PyQGIS worker: appended layer "
        f"{worker_run_result.layer_to_add!r} is missing from the mutated "
        f"project. Layer names: {layer_names!r}. The "
        f"_append_memory_polygon_layer codepath did not run or did not "
        f"persist."
    )


@pytest.mark.live_worker
def test_worker_publishes_envelope(
    worker_run_result: WorkerRunResult,
) -> None:
    """EC4 — the Pub/Sub envelope published to ``grace-2-worker-events``
    has the expected shape: ``qgs_uri`` matches the run, ``status='ok'``,
    ``layers_after`` contains both layers, and the worker-side metadata
    fields (``qgs_version``, ``ts``) are populated.

    Per the OQ-20G documented behaviour, ``notify_message_id`` in the
    published payload is always ``null`` (chicken-and-egg) — we assert
    its presence as a key but not a non-null value. The outer Pub/Sub
    ``message.messageId`` is the in-flight correlation handle.
    """
    env = worker_run_result.envelope
    if not env or "_pull_error" in env:
        raise AssertionError(
            f"layer=Pub/Sub (envelope capture): no envelope was pulled "
            f"from the temp subscription. Likely cause: the worker "
            f"failed before its publish step, or the subscription was "
            f"created after the publish. _pull_error="
            f"{env.get('_pull_error') if env else 'missing'}"
        )

    expected_uri = worker_run_result.test_qgs_uri_local
    assert env.get("qgs_uri") == expected_uri, (
        f"layer=PyQGIS worker (envelope payload): qgs_uri mismatch. "
        f"Expected {expected_uri!r}, got {env.get('qgs_uri')!r}. "
        f"Full envelope: {env!r}"
    )
    assert env.get("status") == "ok", (
        f"layer=PyQGIS worker (worker status): expected status='ok', got "
        f"{env.get('status')!r}. envelope.error={env.get('error')!r}. "
        f"Full envelope: {env!r}"
    )
    layers_after = env.get("layers_after") or []
    assert worker_run_result.layer_to_add in layers_after, (
        f"layer=PyQGIS worker (envelope layers_after): the appended "
        f"layer {worker_run_result.layer_to_add!r} should be in "
        f"layers_after, got {layers_after!r}."
    )
    assert "basemap-osm-conus" in layers_after, (
        f"layer=PyQGIS worker (envelope layers_after): the canonical "
        f"'basemap-osm-conus' layer is missing from layers_after: "
        f"{layers_after!r}. The worker did not preserve the existing "
        f"layer."
    )
    # qgs_version + ts are populated by the worker per types.py
    assert isinstance(env.get("qgs_version"), str) and env["qgs_version"], (
        f"layer=PyQGIS worker (envelope metadata): qgs_version missing "
        f"or empty. Got {env.get('qgs_version')!r}."
    )
    assert isinstance(env.get("ts"), str) and env["ts"].endswith("Z"), (
        f"layer=PyQGIS worker (envelope metadata): ts missing, empty, or "
        f"not Z-suffixed UTC ISO-8601. Got {env.get('ts')!r}."
    )
    # OQ-20G documented behaviour — assert the key is present (shape
    # discipline), not a non-null value.
    assert "notify_message_id" in env, (
        f"layer=PyQGIS worker (envelope schema): notify_message_id key "
        f"is missing from the published envelope. Per types.py the field "
        f"is always present (and always null in the published payload)."
    )
