"""Adversarial escape probes: threads, subprocess, fork bombs, memory, signal masking."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath("services/agent/src"))
os.environ["GRACE2_SANDBOX_LOCAL"] = "1"
os.environ["MPLBACKEND"] = "Agg"

from grace2_agent.sandbox_runner import run_sandbox_local  # noqa: E402


def run(name, code, cap, expect_status=None, max_elapsed=None):
    t0 = time.time()
    env = run_sandbox_local(code, {}, timeout_seconds=cap)
    el = time.time() - t0
    status = env["status"]
    print(f"[{name}] cap={cap}s elapsed={el:.2f}s status={status} error={env.get('error')}")
    if env.get("stdout"):
        print(f"    stdout: {env['stdout'][:200]!r}")
    if env.get("result", {}).get("value") not in (None, {}):
        print(f"    result.value: {str(env['result'].get('value'))[:200]!r}")
    return env, el


# PROBE 1: busy-loop INSIDE a background thread. SIGALRM is delivered to the main
# thread; if the main thread is blocked in thread.join() the alarm should still
# fire there. But if main thread finishes and a daemon thread keeps spinning, what
# happens to the cap? Test a non-daemon thread that busy-loops forever while main
# joins it.
code_thread_join = (
    "import threading\n"
    "def spin():\n"
    "    x = 0\n"
    "    while True:\n"
    "        x += 1\n"
    "t = threading.Thread(target=spin)\n"
    "t.start()\n"
    "t.join()\n"  # main blocks here; SIGALRM should interrupt the join
    "result = 'ESCAPED_VIA_THREAD'\n"
)
env, el = run("thread+join", code_thread_join, 3)

# PROBE 2: busy-loop in a DAEMON thread, main thread sleeps. Daemon thread keeps
# the CPU busy. The alarm fires in main (which is in time.sleep). Does the run
# terminate near the cap?
code_daemon = (
    "import threading, time\n"
    "def spin():\n"
    "    x = 0\n"
    "    while True:\n"
    "        x += 1\n"
    "t = threading.Thread(target=spin, daemon=True)\n"
    "t.start()\n"
    "time.sleep(1000)\n"
    "result = 'ESCAPED_VIA_DAEMON'\n"
)
env, el = run("daemon+sleep", code_daemon, 3)

# PROBE 3: os.fork() a child that busy-loops. Does the child outlive the cap +
# keep the subprocess pipe open (preventing communicate() from returning)?
code_fork = (
    "import os, time\n"
    "pid = os.fork()\n"
    "if pid == 0:\n"
    "    # child: busy loop forever\n"
    "    x = 0\n"
    "    while True:\n"
    "        x += 1\n"
    "else:\n"
    "    result = f'parent_forked_child_{pid}'\n"
)
env, el = run("os.fork child busyloop", code_fork, 3)

# PROBE 4: subprocess.Popen a detached child that holds stdout open. The classic
# way to defeat communicate(): a grandchild inherits the stdout pipe fd, so the
# read side never sees EOF even after the direct child is killed.
code_subproc = (
    "import subprocess, sys, time\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(1000)'])\n"
    "result = f'spawned_pid_{p.pid}'\n"
)
env, el = run("subprocess.Popen detached sleeper", code_subproc, 3)

# PROBE 5: subprocess child that INHERITS stdout and holds it -> communicate() may
# hang past outer timeout because the pipe stays open. This is the real escape
# vector against communicate(timeout=). The child sleeps 30s holding fd 1.
code_subproc_holds_stdout = (
    "import subprocess, sys\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time,sys; sys.stdout.write(\"held\\\\n\"); sys.stdout.flush(); time.sleep(30)'])\n"
    "result = 'spawned_stdout_holder'\n"
)
env, el = run("subprocess holds stdout 30s", code_subproc_holds_stdout, 2)

# PROBE 6: memory blow-up. The output cap bounds the REPORTED stdout, but does the
# harness bound actual RSS? Allocate a big list -> should NOT be bounded by the
# harness (no rlimit), confirming the claim that memory containment is the Cloud
# Run 2GiB cap, NOT the harness. We use a modest 200MB so we don't OOM the box.
code_mem = (
    "buf = bytearray(200 * 1024 * 1024)\n"  # 200 MB
    "result = len(buf)\n"
)
env, el = run("memory alloc 200MB", code_mem, 10)

# PROBE 7: result that is a huge string -> does convert_result bound it?
code_bigresult = (
    "result = 'A' * (50 * 1024 * 1024)\n"  # 50 MB string as result
)
env, el = run("50MB string result", code_bigresult, 10)
rv = env["result"].get("value")
print(f"    -> result kind={env['result'].get('kind')} len={len(rv) if isinstance(rv, str) else 'n/a'}")

# PROBE 8: numpy array result over the row cap -> value omitted?
code_bigarray = (
    "import numpy as np\n"
    "result = np.zeros(1_000_000)\n"
)
env, el = run("1M-element numpy array result", code_bigarray, 10)
print(f"    -> result kind={env['result'].get('kind')} truncated={env['result'].get('truncated')} value_is_none={env['result'].get('value') is None}")

print("PROBES COMPLETE")
