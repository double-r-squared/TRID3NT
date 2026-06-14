"""Probe orphan survival after outer kill, + characterize the big-result envelope bug."""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.abspath("services/agent/src"))
os.environ["GRACE2_SANDBOX_LOCAL"] = "1"
os.environ["MPLBACKEND"] = "Agg"

from grace2_agent.sandbox_runner import run_sandbox_local  # noqa: E402

# ---------------------------------------------------------------------------
# A. Does a Popen'd grandchild survive after the executor child is hard-killed?
#    The executor child is killed via proc.kill() (SIGKILL to the direct child
#    only, NOT the process group). A grandchild it spawned is orphaned, reparented
#    to init, and keeps running. We mark it by having it write a sentinel file.
# ---------------------------------------------------------------------------
sentinel = "/tmp/grace2_orphan_sentinel.txt"
try:
    os.unlink(sentinel)
except OSError:
    pass

code_orphan = (
    "import subprocess, sys\n"
    "# grandchild writes a sentinel AFTER 8s — well past the 2s cap + outer kill.\n"
    "p = subprocess.Popen([sys.executable, '-c', "
    "'import time; time.sleep(8); open(\"%s\",\"w\").write(\"ORPHAN_SURVIVED\")'])\n"
    "result = f'spawned_{p.pid}'\n"
) % sentinel

t0 = time.time()
env = run_sandbox_local(code_orphan, {}, timeout_seconds=2)
print(f"[orphan] elapsed={time.time()-t0:.2f}s status={env['status']}")
# Wait long enough for the orphan (if alive) to write its sentinel.
time.sleep(10)
if os.path.exists(sentinel):
    print("    !!! ORPHAN GRANDCHILD SURVIVED the outer kill — wrote sentinel.")
    print(f"    content={open(sentinel).read()!r}")
    os.unlink(sentinel)
else:
    print("    orphan did NOT survive (no sentinel) — process-group kill or it died.")

# ---------------------------------------------------------------------------
# B. Big JSON-native result -> envelope truncation -> result lost as error.
#    Show the threshold: a result string just under vs over MAX_ENVELOPE_BYTES (8MB).
# ---------------------------------------------------------------------------
for mb in (4, 9, 50):
    code = f"result = 'A' * ({mb} * 1024 * 1024)\n"
    env = run_sandbox_local(code, {}, timeout_seconds=10)
    rv = env["result"].get("value")
    print(f"[bigresult {mb}MB] status={env['status']} result.kind={env['result'].get('kind')} "
          f"value_len={len(rv) if isinstance(rv, str) else 'n/a'} error={(env.get('error') or '')[:80]!r}")

# ---------------------------------------------------------------------------
# C. Big JSON list result (list of dicts — the realistic 'records'-style payload a
#    DataFrame-to-json or a big aggregation could produce) -> same truncation path.
# ---------------------------------------------------------------------------
code_list = "result = [{'i': i, 'pad': 'x'*100} for i in range(100000)]\n"
env = run_sandbox_local(code_list, {}, timeout_seconds=10)
print(f"[biglist 100k dicts] status={env['status']} result.kind={env['result'].get('kind')} "
      f"error={(env.get('error') or '')[:80]!r}")

print("DONE")
