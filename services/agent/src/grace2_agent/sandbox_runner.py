"""Host-side dispatch shim for the Python sandbox Cloud Run Job (job-0232).

``submit_sandbox_job(python_code, layer_refs)`` is the agent-side entry point the
``code_exec_request`` tool (job-0233) calls. It has two modes:

* **Cloud mode (default).** Stages the ``{python_code, layer_refs}`` payload to a
  GCS object, submits a ``grace-2-python-sandbox`` Cloud Run Job execution with
  ``GRACE2_SANDBOX_PAYLOAD_URI`` pointing at it, and returns a
  :class:`SandboxExecutionHandle` (the sandbox analogue of the solver's
  ``ExecutionHandle`` — a pending-result handle whose execution id is the seam the
  agent polls / cancels on). The Cloud Run Job in ``infra/python-sandbox.tf`` runs
  ``infra/python-sandbox/executor.py`` inside the egress-denied container; the
  executor prints its result envelope (``GRACE2_SANDBOX_ENVELOPE_V1`` marker) to
  stdout -> Cloud Logging, and ``read_sandbox_result`` (job-0265) reads it back
  from Cloud Logging under the AGENT'S identity — keeping the sandbox runtime SA
  ``objectViewer``-only (no GCS/Logging write on a hostile-code-reachable SA;
  Invariant 5). This is option (b) of OQ-SANDBOX-3.

* **Local-subprocess fallback (``GRACE2_SANDBOX_LOCAL=1``).** Runs the SAME
  ``executor.py`` harness in a child ``python`` subprocess on this machine — no
  docker daemon, no gcloud needed. Used for dev + the job-0232 / job-0238 tests.
  The local fallback enforces the SAME 60s wallclock cap (via the executor's
  in-process SIGALRM watchdog) AND an outer subprocess hard-kill at cap+grace, and
  the same output bounds (the executor truncates; the runner caps the JSON it
  parses). ``run_sandbox_local`` returns the parsed result envelope directly
  (synchronous) — there is no Cloud Workflows indirection in local mode.

Why the executor is invoked as a subprocess (not imported) even in local mode
-----------------------------------------------------------------------------
1. The 60s wallclock cap + the in-process net guard MONKEYPATCH process-global
   state (``socket.socket.connect``, proxy env). Running it inline in the agent
   process would poison the agent's own socket stack. A child process is disposed
   after each run — clean isolation.
2. The outer ``subprocess`` hard-kill (``communicate(timeout=...)`` + ``kill()``)
   is a belt-and-suspenders wallclock bound that survives even if user code
   installs its own SIGALRM handler or blocks signals in a C extension. An inline
   call has no such outer bound.
3. ``executor.py`` lives in ``infra/python-sandbox/`` (the container build
   context), NOT on the agent's import path — running it by file path keeps that
   single source of truth for the harness logic without copying it into the agent
   package.

The executor module is located by walking up from this file to the repo root and
joining ``infra/python-sandbox/executor.py``; an env override
(``GRACE2_SANDBOX_EXECUTOR``) lets tests / the container point elsewhere.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger("grace2.agent.sandbox_runner")


class SandboxCloudModeUnavailable(RuntimeError):
    """Cloud-mode result readback could not run (job-0265 — the readback CLIENT
    is unavailable, distinct from "the envelope was not found").

    job-0265 wired the sprint-13.5 cloud-result transport: the executor writes
    its result envelope to **stdout** (which lands in Cloud Logging), and
    ``read_sandbox_result`` reads it back from Cloud Logging filtered on the
    execution name + the ``GRACE2_SANDBOX_ENVELOPE_V1`` marker — option (b) of
    the OQ-SANDBOX-3 decision. This keeps the runtime SA ``objectViewer``-only
    (no GCS write into a hostile-code-reachable SA — Invariant 5 preserved): the
    AGENT'S identity (not the sandbox runtime's) does the privileged Cloud
    Logging read.

    This typed error is raised ONLY when the readback transport itself cannot
    run — ``google-cloud-logging`` is not importable, or ADC / the logging client
    cannot be constructed. (A successfully-queried-but-empty result raises
    :class:`SandboxResultNotFound` instead, so the agent can distinguish "I
    couldn't look" from "I looked and the run produced nothing readable yet".)

    The always-available path remains **local-subprocess** mode
    (``GRACE2_SANDBOX_LOCAL=1``), which reads the child's stdout directly and
    returns a complete result envelope synchronously.

    ``error_code`` / ``retryable`` follow the FR-AS-11 typed-exception convention
    so ``summarize_tool_result`` surfaces a structured function_response to
    Gemini (the agent should fall back to local mode or narrate honestly, NOT
    retry the identical cloud dispatch).
    """

    error_code: str = "SANDBOX_CLOUD_MODE_UNAVAILABLE"
    retryable: bool = False


class SandboxResultNotFound(RuntimeError):
    """The Cloud Logging readback ran but no result envelope was found in time
    (job-0265).

    Raised when ``read_sandbox_result`` queried Cloud Logging for the execution's
    marker line and, after polling up to
    :data:`SANDBOX_LOG_READ_TIMEOUT_SECONDS`, found no parseable
    ``GRACE2_SANDBOX_ENVELOPE_V1`` entry — e.g. log ingestion lag exceeded the
    window, the execution never reached the emit (it was killed before flushing),
    or the execution name was wrong. The agent should narrate the missing result
    honestly (NOT fabricate one) and may retry the READBACK once more or fall
    back to local mode.

    ``retryable=True`` because the failure is transient (ingestion lag): a later
    readback of the SAME execution may succeed. The agent must NOT re-DISPATCH
    the code on this error (that would double-run the user's confirmed snippet);
    it may re-READ the same handle.
    """

    error_code: str = "SANDBOX_RESULT_NOT_FOUND"
    retryable: bool = True

# Wallclock cap (seconds) — matches infra/python-sandbox.tf's 60s Job timeout and
# the executor's GRACE2_SANDBOX_TIMEOUT. The runner's OUTER subprocess timeout is
# this + a grace window so the executor's own SIGALRM fires first (cleaner error:
# status="timeout" with captured partial output) and the outer kill is the
# backstop only.
WALLCLOCK_CAP_SECONDS = int(os.environ.get("GRACE2_SANDBOX_TIMEOUT", "60"))
# Grace window added to the outer subprocess timeout so the in-process alarm wins
# the race in the normal case (and the executor can flush its JSON envelope).
SUBPROCESS_GRACE_SECONDS = int(os.environ.get("GRACE2_SANDBOX_SUBPROC_GRACE", "10"))
# Max bytes of the child's stdout we will read/parse. The executor truncates its
# own stdout/stderr fields; this is the outer bound on the JSON envelope line.
MAX_ENVELOPE_BYTES = int(os.environ.get("GRACE2_SANDBOX_MAX_ENVELOPE_BYTES", str(8 * 1024 * 1024)))

# Cloud Run Job name (must match infra/python-sandbox.tf).
SANDBOX_JOB_NAME = os.environ.get("GRACE2_SANDBOX_JOB_NAME", "grace-2-python-sandbox")

# Envelope marker prefix the executor stamps on its result line — MUST stay in
# lockstep with ``infra/python-sandbox/executor.py``'s ``ENVELOPE_MARKER``. The
# executor lives in the container build context (not on the agent import path),
# so the constant is duplicated here rather than imported. The local parser uses
# it to tolerate the prefix; the cloud readback uses it as the Cloud Logging
# textPayload filter token. A drift would silently break cloud readback, so a
# unit test asserts the two literals match.
SANDBOX_ENVELOPE_MARKER = "GRACE2_SANDBOX_ENVELOPE_V1"

# --- Cloud Logging readback (job-0265) ------------------------------------- #
# How long to poll Cloud Logging for the result line after the Cloud Run Job
# execution finishes. The executor runs under a 60s wallclock cap; logs are
# usually queryable within a few seconds of the write, but ingestion lag can be
# tens of seconds, so we poll up to this bound before declaring the envelope
# un-readable. Kept generous because a missing envelope means an honest error,
# not a fabricated result.
SANDBOX_LOG_READ_TIMEOUT_SECONDS = int(
    os.environ.get("GRACE2_SANDBOX_LOG_READ_TIMEOUT", "120")
)
# Delay between Cloud Logging poll attempts while the envelope has not yet landed.
SANDBOX_LOG_POLL_INTERVAL_SECONDS = float(
    os.environ.get("GRACE2_SANDBOX_LOG_POLL_INTERVAL", "3")
)


def _is_local_mode() -> bool:
    return os.environ.get("GRACE2_SANDBOX_LOCAL", "").strip() in ("1", "true", "TRUE", "yes")


def _repo_root() -> Path:
    """Walk up from this file to the repo root (the dir containing ``infra/``)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "infra" / "python-sandbox" / "executor.py").exists():
            return parent
    # Fallback: four levels up from services/agent/src/grace2_agent/sandbox_runner.py
    return here.parents[4]


def _executor_path() -> Path:
    override = os.environ.get("GRACE2_SANDBOX_EXECUTOR", "").strip()
    if override:
        return Path(override)
    return _repo_root() / "infra" / "python-sandbox" / "executor.py"


# --------------------------------------------------------------------------- #
# Result + handle shapes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SandboxExecutionHandle:
    """Pending-result handle for a cloud sandbox dispatch (the sandbox analogue of
    the solver ``ExecutionHandle``). The agent polls/cancels on
    ``execution_name`` — the Cloud Run Job execution resource name.

    Fields:
      - ``handle_id`` — ULID for this dispatch (agent-side key).
      - ``execution_name`` — Cloud Run Job execution resource name
        (``projects/.../jobs/grace-2-python-sandbox/executions/...``) — the
        cancellation/poll seam.
      - ``payload_uri`` — gs:// staging file the Job reads (python_code + refs).
      - ``result_uri`` — gs:// object reserved for a future GCS transport.
        VESTIGIAL in the job-0265 transport: the executor writes its envelope to
        stdout -> Cloud Logging (NOT this object), and ``read_sandbox_result``
        reads it back from Cloud Logging keyed on ``execution_name``. The field
        is retained for back-compat + a possible future GCS-sink transport.
      - ``submitted_at`` — UTC submit time (also the Cloud Logging readback's
        ``timestamp>=`` floor — see ``_build_log_filter``).
      - ``mode`` — "cloud" (always, for this handle type).
    """

    handle_id: str
    execution_name: str
    payload_uri: str
    result_uri: str
    submitted_at: datetime
    mode: str = "cloud"


# --------------------------------------------------------------------------- #
# Local-subprocess fallback — reuses executor.py
# --------------------------------------------------------------------------- #


def run_sandbox_local(
    python_code: str,
    layer_refs: dict[str, str] | None = None,
    *,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Run ``python_code`` through ``executor.py`` in a child subprocess.

    Returns the parsed result envelope (the same shape the container emits):
        {"stdout", "stderr", "result", "status", "error",
         "stdout_truncated", "stderr_truncated", "wallclock_cap_seconds", ...}

    Enforces:
      - the executor's in-process 60s SIGALRM cap (via GRACE2_SANDBOX_TIMEOUT), AND
      - an OUTER subprocess hard-kill at cap + grace (belt-and-suspenders), AND
      - the executor's output truncation + this runner's MAX_ENVELOPE_BYTES bound.

    On the outer hard-kill (the in-process alarm was defeated) we synthesize a
    ``status="timeout"`` envelope so the caller always gets a well-formed result.
    """
    cap = timeout_seconds if timeout_seconds is not None else WALLCLOCK_CAP_SECONDS
    executor = _executor_path()
    if not executor.exists():
        raise FileNotFoundError(f"sandbox executor not found at {executor}")

    payload = {"python_code": python_code, "layer_refs": layer_refs or {}}

    # Write the payload to a temp file the child reads via --payload-file.
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", prefix="grace2_sandbox_", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(payload, fh)
        payload_path = fh.name

    # Child env: pin the executor's own cap to `cap` so the in-process alarm
    # matches the outer timeout's intent. Keep the rest of the env (PATH, venv,
    # PYTHONPATH) so the child resolves the same interpreter deps as the agent.
    child_env = dict(os.environ)
    child_env["GRACE2_SANDBOX_TIMEOUT"] = str(cap)
    child_env.setdefault("MPLBACKEND", "Agg")
    # Run the executor as a script by path — its __main__ guard calls main().
    cmd = [sys.executable, str(executor), "--payload-file", payload_path]

    LOG.info("sandbox local run: %s (cap=%ds)", " ".join(cmd), cap)
    proc = subprocess.Popen(  # noqa: S603 — fixed cmd, no shell
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
        text=True,
    )
    outer_timeout = cap + SUBPROCESS_GRACE_SECONDS
    try:
        out, err = proc.communicate(timeout=outer_timeout)
    except subprocess.TimeoutExpired:
        # In-process alarm was defeated; hard-kill the child and synthesize a
        # timeout envelope. This is the wallclock backstop the kickoff requires.
        proc.kill()
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        LOG.warning("sandbox local run exceeded outer timeout %ds; child killed", outer_timeout)
        return {
            "stdout": (out or "")[:MAX_ENVELOPE_BYTES],
            "stderr": (err or "")[:MAX_ENVELOPE_BYTES],
            "result": {"kind": "none", "value": None},
            "status": "timeout",
            "error": f"sandbox exceeded {cap}s wallclock cap (outer subprocess kill at {outer_timeout}s)",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "wallclock_cap_seconds": cap,
        }
    finally:
        try:
            os.unlink(payload_path)
        except OSError:
            pass

    # The executor prints exactly one JSON envelope line on stdout. We do NOT
    # blind-slice ``out`` to MAX_ENVELOPE_BYTES (job-0233 FINDING 2): a raw byte
    # slice through a JSON document corrupts it (cuts mid-token / mid-escape) and
    # yields an un-parseable envelope. Instead we PARSE the full stdout, then
    # bound the string fields INSIDE the parsed envelope with honest markers
    # (``_parse_envelope`` -> ``_bound_envelope``). If the raw stdout itself is
    # absurdly large (a misbehaving harness that printed the unbounded result to
    # the real stdout) we reject with a typed too-large error rather than parse
    # gigabytes — also honest, never silently corrupt.
    out = out or ""
    if len(out) > MAX_ENVELOPE_BYTES:
        return {
            "stdout": "",
            "stderr": (err or "")[-2000:],
            "result": {"kind": "none", "value": None},
            "status": "error",
            "error": (
                f"sandbox stdout ({len(out)} bytes) exceeded MAX_ENVELOPE_BYTES "
                f"({MAX_ENVELOPE_BYTES}); refusing to parse a potentially-corrupt "
                "envelope (the executor's own MAX_RESULT_BYTES / MAX_OUTPUT_CHARS "
                "caps should keep a well-behaved envelope well under this bound)"
            ),
            "stdout_truncated": True,
            "stderr_truncated": False,
            "wallclock_cap_seconds": WALLCLOCK_CAP_SECONDS,
            "envelope_truncated": True,
        }
    envelope = _parse_envelope(out, err or "", proc.returncode)
    return envelope


#: Per-field char bound applied INSIDE a parsed envelope (FINDING 2). The
#: executor already caps stdout/stderr at MAX_OUTPUT_CHARS and the result
#: descriptor at MAX_RESULT_BYTES; this is the host-side defense-in-depth bound
#: that guarantees the envelope this runner returns can never carry an
#: unboundedly-large string field even if an env override loosened the executor's
#: own caps. Truncation is honest: a ``*_truncated`` flag is set when it fires.
MAX_ENVELOPE_FIELD_CHARS = int(
    os.environ.get("GRACE2_SANDBOX_MAX_ENVELOPE_FIELD_CHARS", str(256 * 1024))
)


def _bound_str_field(value: Any, *, cap: int | None = None) -> tuple[Any, bool]:
    """Bound a string field to ``cap`` chars; return ``(bounded, was_truncated)``.

    Non-string values pass through unchanged. The marker is appended so the
    truncation is visible in the field itself, and the boolean lets the caller
    flip the matching ``*_truncated`` flag (honest, never silent). ``cap``
    defaults to the module-level :data:`MAX_ENVELOPE_FIELD_CHARS` read at CALL
    time (so an env / monkeypatch override of the constant takes effect)."""
    if cap is None:
        cap = MAX_ENVELOPE_FIELD_CHARS
    if not isinstance(value, str) or len(value) <= cap:
        return value, False
    marker = f"...[truncated {len(value) - cap} chars]"
    return value[:cap] + marker, True


def _bound_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Bound the string fields of a parsed envelope (FINDING 2 — parse-then-bound).

    Truncates INSIDE the already-parsed dict (so JSON validity is preserved by
    construction) rather than slicing the raw JSON string. Sets the matching
    ``stdout_truncated`` / ``stderr_truncated`` flags when a bound fires."""
    out_bounded, out_trunc = _bound_str_field(envelope.get("stdout"))
    err_bounded, err_trunc = _bound_str_field(envelope.get("stderr"))
    if out_trunc:
        envelope["stdout"] = out_bounded
        envelope["stdout_truncated"] = True
    if err_trunc:
        envelope["stderr"] = err_bounded
        envelope["stderr_truncated"] = True
    # ``error`` is a short message; bound it too for completeness.
    err_msg_bounded, _ = _bound_str_field(envelope.get("error"))
    if envelope.get("error") is not None:
        envelope["error"] = err_msg_bounded
    return envelope


def _parse_envelope(stdout: str, stderr: str, returncode: int | None) -> dict[str, Any]:
    """Parse the executor's JSON envelope from stdout; synthesize on parse failure.

    FINDING 2: we parse the FULL (unsliced) stdout line, then bound the string
    fields inside the parsed dict via :func:`_bound_envelope` so the returned
    envelope is always valid JSON with honestly-marked truncation — never a
    corrupt slice of a JSON document.

    job-0265: the executor now prefixes the envelope line with
    ``ENVELOPE_MARKER`` (``GRACE2_SANDBOX_ENVELOPE_V1 {...}``) so the cloud
    Cloud-Logging readback can pin it. We tolerate that prefix here by extracting
    the JSON from the first ``{`` on the line — a marker-prefixed line and a bare
    ``{...}`` line both parse, so the SAME emit path serves both transports."""
    candidate: str | None = None
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        # Tolerate the ``GRACE2_SANDBOX_ENVELOPE_V1 {...}`` marker prefix: take
        # the JSON from the first ``{`` to the last ``}`` on the line.
        if SANDBOX_ENVELOPE_MARKER in line:
            brace = line.find("{")
            if brace != -1 and line.endswith("}"):
                candidate = line[brace:]
                break
        if line.startswith("{") and line.endswith("}"):
            candidate = line
            break
    if candidate is not None:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and "status" in parsed:
                return _bound_envelope(parsed)
        except (TypeError, ValueError):
            pass
    # No well-formed envelope — the child crashed before emitting one.
    return {
        "stdout": _bound_str_field(stdout)[0],
        "stderr": _bound_str_field(stderr)[0],
        "result": {"kind": "none", "value": None},
        "status": "error",
        "error": (
            f"sandbox child produced no parseable result envelope "
            f"(returncode={returncode}); stderr tail: {stderr[-500:]!r}"
        ),
        "stdout_truncated": len(stdout) > MAX_ENVELOPE_FIELD_CHARS,
        "stderr_truncated": len(stderr) > MAX_ENVELOPE_FIELD_CHARS,
        "wallclock_cap_seconds": WALLCLOCK_CAP_SECONDS,
    }


# --------------------------------------------------------------------------- #
# Cloud dispatch
# --------------------------------------------------------------------------- #


def submit_sandbox_job(
    python_code: str,
    layer_refs: dict[str, str] | None = None,
    *,
    timeout_seconds: int | None = None,
) -> SandboxExecutionHandle | dict[str, Any]:
    """Dispatch a sandbox run.

    * In local mode (``GRACE2_SANDBOX_LOCAL=1``) this runs synchronously via
      :func:`run_sandbox_local` and returns the parsed RESULT ENVELOPE dict
      directly (no handle — the run is already complete).
    * In cloud mode it stages the payload + submits the Cloud Run Job execution
      and returns a :class:`SandboxExecutionHandle` (pending result).

    The caller (job-0233) branches on the return type: a dict is a finished local
    result; a ``SandboxExecutionHandle`` is a pending cloud dispatch to poll.
    """
    if _is_local_mode():
        return run_sandbox_local(python_code, layer_refs, timeout_seconds=timeout_seconds)
    return _submit_cloud(python_code, layer_refs)


def _submit_cloud(
    python_code: str,
    layer_refs: dict[str, str] | None,
) -> SandboxExecutionHandle:
    """Stage the payload to GCS + submit the Cloud Run Job execution.

    Lazy-imports google-cloud clients so the agent boots in CI/test without ADC
    (mirror of solver.py discipline). The result-readback (job-0233) reads the
    envelope the container writes to ``result_uri``.
    """
    from grace2_contracts import new_ulid  # local import: keeps module import light

    handle_id = new_ulid()
    run_key = new_ulid()
    submitted_at = datetime.now(timezone.utc)

    cache_bucket = os.environ.get("GRACE2_CACHE_BUCKET", "grace-2-hazard-prod-cache")
    project = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GCP_REGION", "us-central1")

    payload = {"python_code": python_code, "layer_refs": layer_refs or {}}
    payload_blob = f"sandbox/{run_key}/payload.json"
    result_blob = f"sandbox/{run_key}/result.json"
    payload_uri = f"gs://{cache_bucket}/{payload_blob}"
    result_uri = f"gs://{cache_bucket}/{result_blob}"

    # Stage the payload object.
    storage_client = _get_storage_client(project)
    bucket = storage_client.bucket(cache_bucket)
    bucket.blob(payload_blob).upload_from_string(
        json.dumps(payload), content_type="application/json"
    )

    # Submit the Cloud Run Job execution with the payload URI + result URI as
    # per-execution env overrides.
    run_client = _get_run_jobs_client()
    job_name = f"projects/{project}/locations/{location}/jobs/{SANDBOX_JOB_NAME}"
    execution_name = _run_job_with_overrides(
        run_client,
        job_name,
        env_overrides={
            "GRACE2_SANDBOX_PAYLOAD_URI": payload_uri,
            "GRACE2_SANDBOX_RESULT_URI": result_uri,
        },
    )

    handle = SandboxExecutionHandle(
        handle_id=handle_id,
        execution_name=execution_name,
        payload_uri=payload_uri,
        result_uri=result_uri,
        submitted_at=submitted_at,
    )
    LOG.info(
        "submit_sandbox_job (cloud) handle_id=%s execution=%s payload=%s",
        handle.handle_id,
        handle.execution_name,
        handle.payload_uri,
    )
    return handle


def read_sandbox_result(
    handle: SandboxExecutionHandle,
    *,
    timeout_seconds: int | None = None,
    poll_interval_seconds: float | None = None,
    logging_client: Any | None = None,
) -> dict[str, Any]:
    """Read the cloud dispatch's result envelope back from Cloud Logging (job-0265).

    Transport — option (b) of OQ-SANDBOX-3
    --------------------------------------
    The egress-denied executor prints its result envelope (prefixed with
    ``GRACE2_SANDBOX_ENVELOPE_V1``) to stdout, which Cloud Run ships to Cloud
    Logging. The sandbox runtime SA stays ``objectViewer``-only (Invariant 5: it
    never gains a GCS/Logging WRITE that hostile code could abuse). Instead THIS
    process — the agent, running under its own identity with ``logging.viewer`` —
    reads the marker line back via the Cloud Logging API, parses the JSON
    envelope, and returns it in the SAME shape ``run_sandbox_local`` produces.

    Polling
    -------
    Cloud Logging ingestion lags the stdout write by seconds (occasionally tens
    of seconds). We poll the API every ``poll_interval_seconds`` until the marker
    entry appears or ``timeout_seconds`` elapses. On success we return the parsed,
    field-bounded envelope. On timeout-with-no-entry we raise
    :class:`SandboxResultNotFound` (transient — the agent may re-read). If the
    logging client itself can't be built (no ``google-cloud-logging`` / no ADC)
    we raise :class:`SandboxCloudModeUnavailable` (the agent should fall back to
    local mode or narrate honestly).

    Args:
        handle: the cloud :class:`SandboxExecutionHandle` returned by
            ``submit_sandbox_job`` in cloud mode (carries the execution name).
        timeout_seconds / poll_interval_seconds: readback poll bounds (default to
            the module constants; overridable for tests).
        logging_client: a pre-built ``google.cloud.logging.Client`` (tests inject
            a mock; production builds one lazily via ADC).
    """
    timeout = (
        timeout_seconds if timeout_seconds is not None else SANDBOX_LOG_READ_TIMEOUT_SECONDS
    )
    interval = (
        poll_interval_seconds
        if poll_interval_seconds is not None
        else SANDBOX_LOG_POLL_INTERVAL_SECONDS
    )

    if logging_client is None:
        try:
            logging_client = _get_logging_client(
                _project_from_execution_name(handle.execution_name)
            )
        except SandboxCloudModeUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — any client-construction failure
            raise SandboxCloudModeUnavailable(
                "cloud-mode sandbox result readback could not construct a Cloud "
                f"Logging client (handle={handle.handle_id}, "
                f"execution={handle.execution_name}): {type(exc).__name__}: {exc}. "
                "Use local mode (GRACE2_SANDBOX_LOCAL=1) for a synchronous result."
            ) from exc

    log_filter = _build_log_filter(handle)
    LOG.info(
        "read_sandbox_result (cloud) handle_id=%s execution=%s filter=%r timeout=%ds",
        handle.handle_id,
        handle.execution_name,
        log_filter,
        timeout,
    )

    deadline = time.monotonic() + timeout
    attempts = 0
    while True:
        attempts += 1
        try:
            envelope = _read_envelope_from_logging(logging_client, log_filter)
        except SandboxCloudModeUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — a query error is transient
            LOG.warning(
                "read_sandbox_result query attempt %d failed (handle=%s): %s",
                attempts,
                handle.handle_id,
                exc,
            )
            envelope = None
        if envelope is not None:
            LOG.info(
                "read_sandbox_result found envelope after %d attempt(s) (handle=%s)",
                attempts,
                handle.handle_id,
            )
            return _bound_envelope(envelope)
        if time.monotonic() >= deadline:
            break
        time.sleep(min(interval, max(deadline - time.monotonic(), 0)))

    raise SandboxResultNotFound(
        "no sandbox result envelope found in Cloud Logging after "
        f"{timeout}s / {attempts} attempt(s) (handle={handle.handle_id}, "
        f"execution={handle.execution_name}, marker={SANDBOX_ENVELOPE_MARKER!r}). "
        "Log ingestion may still be lagging, or the execution did not reach the "
        "envelope emit. Re-read the handle later, or use local mode "
        "(GRACE2_SANDBOX_LOCAL=1)."
    )


def _project_from_execution_name(execution_name: str) -> str | None:
    """Extract the project id from a Cloud Run Job execution resource name.

    Execution names look like
    ``projects/<project>/locations/<loc>/jobs/<job>/executions/<exec>``. The
    Cloud Logging client must target the same project the Job ran in. Falls back
    to the GCP_PROJECT / GOOGLE_CLOUD_PROJECT env if the name isn't parseable."""
    parts = (execution_name or "").split("/")
    if len(parts) >= 2 and parts[0] == "projects" and parts[1]:
        return parts[1]
    return os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")


def _execution_short_name(execution_name: str) -> str:
    """The trailing ``<exec>`` id of a full execution resource name.

    Cloud Run Job log entries carry the execution in the
    ``run.googleapis.com/execution_name`` label as the SHORT id (not the full
    resource path), so the log filter matches on this."""
    return (execution_name or "").rstrip("/").split("/")[-1]


def _build_log_filter(handle: SandboxExecutionHandle) -> str:
    """Build the Cloud Logging advanced filter that pins the result line.

    Three conjuncts, narrowest first:
      1. the Cloud Run Job stdout log stream,
      2. the execution-name label (so we only read THIS dispatch's logs), and
      3. the envelope marker substring (so we skip the user-code stdout lines and
         match only the result envelope line).

    A ``timestamp >=`` floor (submit time minus a small skew) bounds the scan to
    this run's window so an old execution with a recycled short-name can't match."""
    short = _execution_short_name(handle.execution_name)
    # Floor a minute before submit to absorb clock skew between this process and
    # the Cloud Run control plane; format as RFC3339 UTC.
    floor = (handle.submitted_at - timedelta(minutes=1)).astimezone(timezone.utc)
    floor_rfc3339 = floor.strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        'resource.type="cloud_run_job" '
        'AND logName:"run.googleapis.com%2Fstdout" '
        f'AND labels."run.googleapis.com/execution_name"="{short}" '
        f'AND textPayload:"{SANDBOX_ENVELOPE_MARKER}" '
        f'AND timestamp>="{floor_rfc3339}"'
    )


def _read_envelope_from_logging(logging_client: Any, log_filter: str) -> dict[str, Any] | None:
    """Run one Cloud Logging query and parse the FIRST marker entry into an envelope.

    Returns the parsed envelope dict, or ``None`` if no matching entry exists yet
    (the caller polls). We order by ``timestamp desc`` and take the most recent
    matching entry — a retried Job execution emits its own marker line, and the
    latest is the authoritative result for this execution id. The text payload is
    ``GRACE2_SANDBOX_ENVELOPE_V1 {json}``; :func:`_extract_envelope_from_text`
    strips the prefix and parses the JSON (rejecting a non-dict / status-less
    parse so a coincidental ``{...}`` substring can't masquerade as a result)."""
    entries = logging_client.list_entries(
        filter_=log_filter,
        order_by="timestamp desc",
        page_size=10,
    )
    for entry in entries:
        text = _entry_text_payload(entry)
        if not text or SANDBOX_ENVELOPE_MARKER not in text:
            continue
        envelope = _extract_envelope_from_text(text)
        if envelope is not None:
            return envelope
    return None


def _entry_text_payload(entry: Any) -> str | None:
    """Pull the text payload string out of a Cloud Logging entry.

    ``google-cloud-logging`` returns ``TextEntry`` objects whose ``payload`` is
    the string; we also tolerate a ``.text_payload`` attribute and a plain dict
    shape (defensive for mocks / struct entries)."""
    payload = getattr(entry, "payload", None)
    if isinstance(payload, str):
        return payload
    text = getattr(entry, "text_payload", None)
    if isinstance(text, str):
        return text
    if isinstance(entry, dict):
        for key in ("textPayload", "text_payload", "payload"):
            val = entry.get(key)
            if isinstance(val, str):
                return val
    return None


def _extract_envelope_from_text(text: str) -> dict[str, Any] | None:
    """Strip the marker prefix from a log line and parse the JSON envelope.

    Format: ``GRACE2_SANDBOX_ENVELOPE_V1 {json}`` (possibly with surrounding
    whitespace / a trailing newline). We take the substring from the first ``{``
    after the marker to the last ``}`` and JSON-parse it. Returns the dict only
    if it parses to a dict carrying ``status`` (the result-envelope shape); else
    ``None`` so a malformed or partial line is skipped, never returned as a fake
    result (honesty — Invariant 1)."""
    line = text.strip()
    marker_at = line.find(SANDBOX_ENVELOPE_MARKER)
    if marker_at == -1:
        return None
    brace = line.find("{", marker_at)
    last = line.rfind("}")
    if brace == -1 or last == -1 or last < brace:
        return None
    candidate = line[brace : last + 1]
    try:
        parsed = json.loads(candidate)
    except (TypeError, ValueError):
        return None
    if isinstance(parsed, dict) and "status" in parsed:
        return parsed
    return None


def _get_logging_client(project: str | None) -> Any:
    """Build a ``google.cloud.logging.Client`` lazily (mirror of the storage/run
    client discipline — import inside the function so the agent boots without
    ``google-cloud-logging`` / ADC in CI).

    Raises :class:`SandboxCloudModeUnavailable` if the package isn't importable so
    the caller surfaces an honest typed error rather than an ImportError."""
    try:
        from google.cloud import logging as gcloud_logging  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover — declared dep, present in venv
        raise SandboxCloudModeUnavailable(
            "google-cloud-logging is not installed; cloud-mode sandbox result "
            "readback is unavailable. Use local mode (GRACE2_SANDBOX_LOCAL=1)."
        ) from exc
    return gcloud_logging.Client(project=project)


def _get_storage_client(project: str | None) -> Any:
    from google.cloud import storage  # type: ignore[import-not-found]  # noqa: PLC0415

    return storage.Client(project=project)


def _get_run_jobs_client() -> Any:
    from google.cloud import run_v2  # type: ignore[import-not-found]  # noqa: PLC0415

    return run_v2.JobsClient()


def _run_job_with_overrides(
    client: Any,
    job_name: str,
    env_overrides: dict[str, str],
) -> str:
    """Submit ``jobs.run`` with container env overrides; return the execution name.

    Uses the run_v2 RunJobRequest.Overrides shape. Returns the long-running
    operation's metadata execution name (the seam the agent polls/cancels).
    """
    from google.cloud import run_v2  # type: ignore[import-not-found]  # noqa: PLC0415

    overrides = run_v2.RunJobRequest.Overrides(
        container_overrides=[
            run_v2.RunJobRequest.Overrides.ContainerOverride(
                env=[run_v2.EnvVar(name=k, value=v) for k, v in env_overrides.items()]
            )
        ]
    )
    operation = client.run_job(
        request=run_v2.RunJobRequest(name=job_name, overrides=overrides)
    )
    # The operation's metadata carries the Execution resource being created.
    meta = getattr(operation, "metadata", None)
    name = getattr(meta, "name", None)
    if not name:
        # Fall back to the operation name if metadata isn't populated yet.
        name = getattr(operation, "operation", None)
        name = getattr(name, "name", None) or str(operation)
    return name
