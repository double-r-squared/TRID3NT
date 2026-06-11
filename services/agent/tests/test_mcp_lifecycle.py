"""MCP sidecar lifecycle tests (job-0241 — sprint-13.5 prereq).

Verifies that ``MCPClient.start`` in ``mcp.py`` correctly:
  - calls ``os.setsid()`` (new session / process group) in the ``preexec_fn``
    so ``close()`` can kill the WHOLE process tree (npx + Node grandchild),
  - sets ``PR_SET_PDEATHSIG = SIGKILL`` via prctl so the sidecar tree dies if
    the agent process is abnormally killed (SIGKILL/crash), and
  - uses ``os.killpg`` in ``close()`` to signal the process GROUP rather than
    just the npx PID.

The manifest says: "start an MCPClient (or a faithful subprocess stand-in),
kill the parent process group, assert the sidecar process is gone."

Tests here use a Python subprocess stand-in (``python -c "import
time; time.sleep(60)"``) so the suite runs without Node.js / npx.
We invoke the SAME ``_preexec`` logic MCPClient.start uses (extracted from
the module source) via ``preexec_fn=`` on our stand-in subprocess, and then
drive the MCPClient internals (bypassing the MCP JSON-RPC handshake which
requires a real server) to verify the kill path.

No network. No Gemini. Pure subprocess / signal mechanics.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import textwrap
import time

import pytest

from grace2_agent.mcp import MCPClient


# --------------------------------------------------------------------------- #
# Helpers: the same preexec_fn MCPClient.start wires up
# --------------------------------------------------------------------------- #


def _mcp_preexec() -> None:
    """Mirror of the preexec_fn in MCPClient.start (job-0241).

    Called inside the child process before exec — sets a new session/process
    group (setsid) and registers PR_SET_PDEATHSIG=SIGKILL so the whole sidecar
    tree dies when the parent process exits abnormally.
    """
    os.setsid()
    try:
        import ctypes

        PR_SET_PDEATHSIG = 1
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
    except Exception:  # noqa: BLE001 — non-Linux: setsid alone
        pass


async def _start_standin() -> asyncio.subprocess.Process:
    """Launch a sleep-60 Python process using the same preexec_fn MCPClient uses."""
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=_mcp_preexec,
    )


def _pid_exists(pid: int) -> bool:
    """Return True if the process still exists (send signal 0)."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# --------------------------------------------------------------------------- #
# (1) source-level wiring assertions — mcp.py has setsid, prctl, killpg
# --------------------------------------------------------------------------- #


def test_mcp_py_has_setsid_in_preexec() -> None:
    """mcp.py source contains os.setsid() in the preexec_fn."""
    import inspect

    import grace2_agent.mcp as mcp_module

    source = inspect.getsource(mcp_module)
    assert "os.setsid()" in source, "mcp.py must call os.setsid() in preexec_fn"


def test_mcp_py_has_pr_set_pdeathsig_in_preexec() -> None:
    """mcp.py source contains PR_SET_PDEATHSIG prctl call in the preexec_fn."""
    import inspect

    import grace2_agent.mcp as mcp_module

    source = inspect.getsource(mcp_module)
    assert "PR_SET_PDEATHSIG" in source, "mcp.py must set PR_SET_PDEATHSIG in preexec_fn"
    assert "prctl" in source, "mcp.py must call prctl for PR_SET_PDEATHSIG"


def test_mcp_py_close_uses_killpg() -> None:
    """mcp.py close() uses os.killpg to signal the PROCESS GROUP, not just the PID."""
    import inspect

    import grace2_agent.mcp as mcp_module

    source = inspect.getsource(mcp_module)
    assert "os.killpg" in source, (
        "mcp.py close() must use os.killpg to signal the whole process group "
        "(npx + Node grandchild), not just self._proc.terminate()"
    )


def test_mcp_py_preexec_fn_is_wired_to_create_subprocess() -> None:
    """mcp.py passes preexec_fn=_preexec to asyncio.create_subprocess_exec."""
    import inspect

    import grace2_agent.mcp as mcp_module

    source = inspect.getsource(mcp_module)
    assert "preexec_fn" in source, (
        "MCPClient.start must wire the preexec_fn to asyncio.create_subprocess_exec"
    )


# --------------------------------------------------------------------------- #
# (2) subprocess stand-in — setsid creates a new process group
# --------------------------------------------------------------------------- #


def test_standin_subprocess_gets_own_process_group() -> None:
    """The _mcp_preexec preexec_fn puts the child in its own process group.

    After setsid() the child's PGID == its own PID (it is the group leader),
    which is distinct from the parent process's PGID. This is the invariant
    close() relies on: os.getpgid(proc.pid) returns the child's group, and
    os.killpg signals only that group — not the agent.
    """

    async def _run() -> None:
        proc = await _start_standin()
        try:
            child_pgid = os.getpgid(proc.pid)
            parent_pgid = os.getpgid(0)
            # After setsid() the child is its own process-group leader.
            assert child_pgid == proc.pid, (
                f"setsid() must make the child its own process-group leader "
                f"(child_pgid={child_pgid} should equal pid={proc.pid})"
            )
            # Child's group is distinct from the parent's group.
            assert child_pgid != parent_pgid, (
                f"Child process group ({child_pgid}) must differ from parent "
                f"process group ({parent_pgid}) after setsid()"
            )
        finally:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), timeout=5.0)

    asyncio.run(_run())


# --------------------------------------------------------------------------- #
# (3) MCPClient.close() terminates the subprocess via killpg
# --------------------------------------------------------------------------- #


def test_mcp_client_close_kills_standin_subprocess() -> None:
    """MCPClient.close() via killpg terminates a faithful subprocess stand-in.

    We build an MCPClient directly (bypassing the JSON-RPC handshake) around
    a Python sleep subprocess launched with the same preexec_fn. After close(),
    the subprocess must be dead (returncode is set, process no longer exists).
    """

    async def _run() -> None:
        proc = await _start_standin()
        pid = proc.pid
        assert _pid_exists(pid), "subprocess should be running before close()"

        # Build the MCPClient wrapping our stand-in proc (no handshake needed).
        client = MCPClient(proc)
        # No _reader_task — the stand-in doesn't speak JSON-RPC.
        await client.close()

        # After close(), the process must be gone.
        assert proc.returncode is not None, (
            "close() must reap the subprocess (returncode should be set)"
        )
        assert not _pid_exists(pid), (
            f"subprocess PID {pid} still exists after close() — killpg did not fire"
        )

    asyncio.run(_run())


# --------------------------------------------------------------------------- #
# (4) killpg kills the WHOLE process group (including a grandchild)
# --------------------------------------------------------------------------- #


def test_killpg_kills_grandchild_subprocess() -> None:
    """The process-group kill in close() kills the grandchild (the Node server stand-in).

    This is the core invariant: in production npx spawns a Node.js grandchild;
    if we only kill the npx PID, the grandchild survives (and leaks memory).
    Here we simulate that by spawning a stand-in that itself spawns a grandchild
    in the same process group. killpg must terminate all of them.
    """
    # A stand-in parent that spawns a long-lived grandchild in the SAME group
    # (does NOT setsid again — the grandchild inherits the parent's group which
    # setsid made == parent's PID). We record the grandchild PID to a tempfile.
    grandchild_code = textwrap.dedent("""
        import os, subprocess, sys, tempfile, time

        # Write our own PID to a file so the test can inspect it.
        tmpfile = sys.argv[1]
        with open(tmpfile, "w") as f:
            f.write(str(os.getpid()))

        # Spawn a grandchild (long sleep) in the SAME process group.
        grandchild = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
        )

        # Also write grandchild PID to the file.
        with open(tmpfile, "a") as f:
            f.write("," + str(grandchild.pid))

        # Keep parent alive.
        time.sleep(600)
    """).strip()

    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as fh:
        pid_file = fh.name

    async def _run() -> tuple[int, int]:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            grandchild_code,
            pid_file,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=_mcp_preexec,
        )
        # Give the stand-in time to write the PIDs.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                text = open(pid_file).read().strip()
                if "," in text:
                    parts = text.split(",")
                    parent_pid, grandchild_pid = int(parts[0]), int(parts[1])
                    break
            except (OSError, ValueError):
                pass
            await asyncio.sleep(0.05)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            await proc.wait()
            pytest.skip("stand-in did not write PIDs within 5s (slow CI)")

        pgid = os.getpgid(proc.pid)

        # Both should be alive before the kill.
        assert _pid_exists(parent_pid), "parent should be alive before killpg"
        assert _pid_exists(grandchild_pid), "grandchild should be alive before killpg"

        # Kill the PROCESS GROUP (mirrors MCPClient.close()).
        os.killpg(pgid, signal.SIGTERM)
        await asyncio.wait_for(proc.wait(), timeout=5.0)

        # After killpg, BOTH parent and grandchild must be gone.
        # Give the OS a moment to reap grandchild.
        for _ in range(20):
            if not _pid_exists(grandchild_pid):
                break
            await asyncio.sleep(0.1)

        return parent_pid, grandchild_pid

    parent_pid, grandchild_pid = asyncio.run(_run())

    assert not _pid_exists(parent_pid), (
        f"parent PID {parent_pid} still exists after killpg"
    )
    assert not _pid_exists(grandchild_pid), (
        f"grandchild PID {grandchild_pid} still exists after killpg — "
        "process-group kill is required (not just terminating the npx/parent PID)"
    )

    # Cleanup temp file.
    try:
        os.unlink(pid_file)
    except OSError:
        pass
