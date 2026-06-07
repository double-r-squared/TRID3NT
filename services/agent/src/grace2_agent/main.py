"""Entry point for the ``grace2-agent`` console script.

Run the WebSocket server. Optionally run an MCP smoke pre-flight (gated by
``GRACE2_AGENT_SKIP_MCP_SMOKE=1`` to skip).

Startup-time tool-registry wiring (job-0032, M4 substrate):

Importing ``grace2_agent.tools`` populates the module-level ``TOOL_REGISTRY``
via the import-time ``@register_tool`` decorators in the package's
submodules (``passthroughs`` for M4 job-0032; ``fetchers`` etc. for
job-0033+). The ``--startup-only`` flag below verifies the registry is
populated without binding the WebSocket port; ``make run-agent`` continues
to start the server normally.

FR-CE-8 fail-fast: any tool whose ``AtomicToolMetadata`` is misconfigured
(e.g. ``cacheable=True`` with ``ttl_class="live-no-cache"``) raises a
``pydantic.ValidationError`` at import time and prevents the agent service
from starting.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

# ---------------------------------------------------------------------------
# FR-FR-3 (job-0048): agent-side max-turns cap — cheap insurance.
#
# ``MAX_TURNS_PER_SESSION`` is the maximum number of user-message / tool-call
# turns allowed before the agent refuses further dispatch and emits a
# ``session-state`` envelope with ``status="max_turns_reached"``.
#
# Override via the ``GRACE2_MAX_TURNS_PER_SESSION`` environment variable for
# ops flexibility (e.g. set to 0 to disable — sentinel value; or raise for
# long sessions during demos). TENTATIVE default 25 per OQ-FR-1.
# ---------------------------------------------------------------------------
MAX_TURNS_PER_SESSION: int = int(os.environ.get("GRACE2_MAX_TURNS_PER_SESSION", "25"))


def _import_tools_registry() -> int:
    """Import ``grace2_agent.tools`` to populate ``TOOL_REGISTRY``.

    Returns the number of registered tools. Surfaced at startup so an empty
    registry (typically a packaging mistake) is visible in the logs rather
    than silent.

    job-0033: eagerly imports ``data_fetch`` (the 4 fetcher atomic tools) so
    their ``@register_tool`` decorators fire alongside the eager
    ``passthroughs`` import in ``tools/__init__.py``. ``tools/__init__.py``
    is FROZEN per job-0033 file ownership, so the fetcher import is
    co-located here instead.

    job-0034: similarly imports ``qgis_discovery`` so the 2 QGIS-algorithm
    discovery atomic tools (``list_qgis_algorithms`` +
    ``describe_qgis_algorithm``) register at startup. Together with
    ``passthroughs.qgis_process`` they complete the FR-AS-9 Level 1a
    capability-discovery loop.

    job-0041: imports ``solver`` so the 2 solver-dispatch atomic tools
    (``run_solver`` + ``wait_for_completion``) register at startup. These
    are FR-DC-6 uncacheable (``cacheable=False``, ``ttl_class="live-no-cache"``,
    ``source_class="solver_dispatch"``) — they drive Cloud Workflows
    executions of the M5 SFINCS substrate landed by job-0040.

    job-0042: imports ``workflows.model_flood_scenario`` so the M5 capstone
    workflow's thin atomic-tool wrapper ``run_model_flood_scenario`` is
    registered alongside the atomic tools it composes. The workflow itself
    is deterministic Python (FR-TA-1, Decision G); the wrapper exists so the
    LLM sees a single invocable tool that triggers the whole chain.
    """
    from . import tools  # noqa: F401 — side-effect: registers atomic tools
    # job-0033: register the 4 data-fetch atomic tools (FROZEN __init__.py).
    from .tools import data_fetch  # noqa: F401
    # job-0034: register the 2 QGIS discovery atomic tools.
    from .tools import qgis_discovery  # noqa: F401
    # job-0041: register run_solver + wait_for_completion (M5 substrate).
    from .tools import solver  # noqa: F401
    # job-0042: register run_model_flood_scenario (M5 capstone workflow wrapper).
    from .workflows import model_flood_scenario  # noqa: F401
    # job-0047: register catalog_search + catalog_fetch (Mode 1 substrate).
    from .tools import catalog  # noqa: F401

    return len(tools.TOOL_REGISTRY)


def _bind_mcp_client(client: object) -> None:
    """Bind the running ``MCPClient`` into the ``mongo_query`` pass-through.

    job-0033 DI seam: completes the wire-up promised by job-0032's
    ``passthroughs.set_mcp_client`` hook. With a bound client, the
    ``mongo_query`` tool body delegates to ``MCPClient.call_tool`` instead
    of raising ``RuntimeError("MCP client is not bound...")``.

    The pre-flight smoke harness (``scripts/mcp_smoke.py``) still owns the
    full ``MCPClient.start()`` async lifecycle; this helper just registers
    the in-flight handle with the tool surface.
    """
    from .tools.passthroughs import set_mcp_client

    set_mcp_client(client)


def _default_qgis_process_submitter():
    """Return the default ``qgis_process`` submitter used by ``set_worker_submitter``.

    job-0034 DI seam: completes the wire-up promised by job-0032's
    ``passthroughs.set_worker_submitter`` hook. The submitter is a callable
    matching the signature ``(args: list[str], timeout_s: int) -> dict``
    where the returned dict carries at least ``stdout`` (str), ``returncode``
    (int), and ``duration_s`` (float). Both ``qgis_discovery`` discovery
    tools and the ``qgis_process`` pass-through call this seam.

    The default submitter runs ``qgis_process`` as a local subprocess —
    suitable for the dev environment and the M4 discovery loop. In
    production this seam will route to the deployed ``grace-2-pyqgis-worker``
    Cloud Run Job (image @sha256:fffd7e0f) once the Cloud Run Jobs v2
    ``command``-override surface is resolved. The deployed worker has the
    same ``qgis_process`` binary baked in; the catalog shape is stable
    across the QGIS 3.x line, so the substitution is materially equivalent.

    Override via ``GRACE2_QGIS_PROCESS_BIN`` env var; defaults to
    ``qgis_process`` discovered on PATH (the ``grace2`` conda env per
    PROJECT_STATE / job-0022 has this).

    Returns:
        A zero-argument-less callable bound to the chosen ``qgis_process``
        binary; the agent service calls ``set_worker_submitter(callable)``
        during startup.
    """
    import os
    import shutil
    import subprocess
    import time

    qgis_bin = os.environ.get("GRACE2_QGIS_PROCESS_BIN") or shutil.which(
        "qgis_process"
    )
    if qgis_bin is None:
        # Last-resort hint for the user's conda env on this Debian box (per
        # PROJECT_STATE env-facts). Production agent image will bake the
        # binary in (or route through the Cloud Run Job submitter).
        candidate = os.path.expanduser("~/miniforge3/envs/grace2/bin/qgis_process")
        if os.path.exists(candidate):
            qgis_bin = candidate
    if qgis_bin is None:
        raise RuntimeError(
            "qgis_process binary not found on PATH; "
            "set GRACE2_QGIS_PROCESS_BIN or install the grace2 conda env."
        )

    def _submit(args: list[str], timeout_s: int) -> dict[str, object]:
        # QT_QPA_PLATFORM=offscreen mirrors the worker container env (job-0021
        # Dockerfile) so QGIS' Qt machinery doesn't try to attach to a display.
        env = dict(os.environ)
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        cmd = [qgis_bin, *args]
        start = time.monotonic()
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            env=env,
        )
        duration_s = time.monotonic() - start
        return {
            "stdout": proc.stdout.decode("utf-8", errors="replace"),
            "stderr": proc.stderr.decode("utf-8", errors="replace"),
            "returncode": proc.returncode,
            "duration_s": duration_s,
            "qgis_bin": qgis_bin,
        }

    return _submit


def _bind_worker_submitter() -> None:
    """Bind the default ``qgis_process`` submitter into ``passthroughs``.

    Called from ``run`` at agent service startup. After this binds, the
    ``qgis_process`` pass-through body no longer raises ``RuntimeError`` and
    the two QGIS-discovery tools can invoke the substrate.

    Gated by env var ``GRACE2_SKIP_WORKER_SUBMITTER`` for test contexts that
    don't want the binary resolved (CI without QGIS installed). When the env
    var is set, the binding stays None and tools raise the documented
    "submitter not bound" RuntimeError on call.
    """
    import os

    if os.environ.get("GRACE2_SKIP_WORKER_SUBMITTER"):
        return
    try:
        submitter = _default_qgis_process_submitter()
    except RuntimeError as exc:
        # Production agent containers will bind a Cloud Run Job submitter
        # here instead of a local subprocess; the dev-env fallback failing is
        # informational, not fatal — we let the agent service start so the
        # other tools (data_fetch, mongo_query, passthroughs) keep working,
        # and any actual QGIS discovery call surfaces the RuntimeError.
        logging.getLogger("grace2_agent.main").warning(
            "worker submitter not bound (qgis_process unavailable): %s", exc
        )
        return
    from .tools.passthroughs import set_worker_submitter

    set_worker_submitter(submitter)


def run(argv: list[str] | None = None) -> int:
    """Console-script entry point. ``make run-agent`` calls this.

    Supports a ``--startup-only`` flag that imports the tool registry, logs
    the registered tools, and exits 0 without binding the WebSocket port.
    Used by job-0032 acceptance and by container healthchecks.
    """
    logging.basicConfig(
        level=os.environ.get("GRACE2_AGENT_LOG", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("grace2_agent.main")

    args = sys.argv[1:] if argv is None else argv
    startup_only = "--startup-only" in args

    # Populate TOOL_REGISTRY by importing the tools package. Any import-time
    # registration error (duplicate name, bad metadata) surfaces here.
    n_tools = _import_tools_registry()
    from . import tools

    tool_names = sorted(tools.TOOL_REGISTRY.keys())
    logger.info("tool registry loaded: %d tool(s): %s", n_tools, tool_names)

    # job-0034: bind the qgis_process submitter so the discovery tools and the
    # qgis_process pass-through can reach the substrate. Best-effort: failure
    # to resolve a local qgis_process is informational, not fatal.
    _bind_worker_submitter()

    if startup_only:
        logger.info("--startup-only: tool registry verified; exiting without serving")
        return 0

    from .server import run_server

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        print("grace2-agent: interrupted, shutting down.", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
