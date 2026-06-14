"""Adversarial re-run of the 4 kickoff scenarios + backstop. Independent driver."""
import json
import os
import sys
import time

# Ensure we import the agent package + run in local subprocess mode.
sys.path.insert(0, os.path.abspath("services/agent/src"))
os.environ["GRACE2_SANDBOX_LOCAL"] = "1"
os.environ["MPLBACKEND"] = "Agg"

from grace2_agent.sandbox_runner import run_sandbox_local, submit_sandbox_job  # noqa: E402


def dump(name, env, elapsed=None):
    print(f"===== {name} =====")
    if elapsed is not None:
        print(f"elapsed={elapsed:.2f}s")
    # Strip giant png for readability
    e = json.loads(json.dumps(env))
    r = e.get("result", {})
    if isinstance(r, dict) and r.get("png_base64"):
        r["png_base64"] = f"<{len(r['png_base64'])} b64 chars>"
    if isinstance(r, dict) and r.get("chart_emission"):
        ce = r["chart_emission"]
        if "vega_lite_spec" in ce:
            vals = ce["vega_lite_spec"].get("data", {}).get("values")
            if vals:
                ce["vega_lite_spec"]["data"]["values"] = "<image data omitted>"
    print(json.dumps(e, indent=2)[:3000])
    print()


# (a) benign numpy
code_a = (
    "import numpy as np\n"
    "arr = np.array([10.0, 20.0, 30.0, 40.0])\n"
    "print(f'sum={arr.sum()}')\n"
    "result = float(arr.mean())\n"
)
env_a = run_sandbox_local(code_a, {})
dump("A benign numpy", env_a)
assert env_a["status"] == "ok", env_a
assert env_a["result"]["kind"] == "json" and env_a["result"]["value"] == 25.0, env_a

# (b) matplotlib figure
code_b = (
    "import matplotlib\nmatplotlib.use('Agg')\n"
    "import matplotlib.pyplot as plt\nimport numpy as np\n"
    "fig, ax = plt.subplots()\n"
    "ax.hist(np.random.RandomState(7).normal(size=300), bins=15)\n"
    "ax.set_title('Adversarial chart')\nresult = fig\n"
)
env_b = run_sandbox_local(code_b, {})
dump("B matplotlib figure", env_b)
assert env_b["status"] == "ok", env_b
assert env_b["result"]["kind"] == "chart", env_b
assert env_b["result"]["png_base64"], "no PNG inlined"

# Construct the REAL pydantic model from the emitted chart_emission, independently.
from grace2_contracts import new_ulid  # noqa: E402
from grace2_contracts.chart_contracts import (  # noqa: E402
    ChartEmissionPayload,
    is_structurally_valid_vega_lite_spec,
)
ce = env_b["result"]["chart_emission"]
assert is_structurally_valid_vega_lite_spec(ce["vega_lite_spec"]), "spec not structurally valid"
payload = ChartEmissionPayload(
    chart_id=new_ulid(),
    vega_lite_spec=ce["vega_lite_spec"],
    title=ce["title"],
    caption=ce["caption"],
)
print(f"ChartEmissionPayload constructed: envelope_type={payload.envelope_type}, title={payload.title}\n")

# (c) malicious network — raw socket AND urllib
code_c = (
    "import socket, urllib.request\n"
    "res = {}\n"
    "try:\n"
    "    socket.create_connection(('example.com', 80), timeout=8)\n"
    "    res['socket'] = 'REACHED_INTERNET'\n"
    "except Exception as e:\n"
    "    res['socket'] = f'BLOCKED:{type(e).__name__}'\n"
    "try:\n"
    "    urllib.request.urlopen('http://example.com', timeout=8)\n"
    "    res['urllib'] = 'REACHED_INTERNET'\n"
    "except Exception as e:\n"
    "    res['urllib'] = f'BLOCKED:{type(e).__name__}'\n"
    "result = res\n"
)
t0 = time.time()
env_c = run_sandbox_local(code_c, {})
dump("C malicious network", env_c, time.time() - t0)
assert env_c["status"] == "ok", env_c
assert "REACHED_INTERNET" not in json.dumps(env_c), "INTERNET WAS REACHED"
assert env_c["result"]["value"]["socket"].startswith("BLOCKED:"), env_c
assert env_c["result"]["value"]["urllib"].startswith("BLOCKED:"), env_c

# (d) infinite loop -> killed at cap (3s)
code_d = "x = 0\nwhile True:\n    x += 1\nresult = x\n"
t0 = time.time()
env_d = run_sandbox_local(code_d, {}, timeout_seconds=3)
el_d = time.time() - t0
dump("D infinite loop (cap=3)", env_d, el_d)
assert env_d["status"] == "timeout", env_d
assert el_d < 12, f"cap not enforced promptly: {el_d:.1f}s"

# (d2) SIGALRM defeated -> outer kill backstop (cap=2 -> outer 12)
code_d2 = (
    "import signal\n"
    "signal.signal(signal.SIGALRM, signal.SIG_IGN)\n"
    "x = 0\nwhile True:\n    x += 1\nresult = x\n"
)
t0 = time.time()
env_d2 = run_sandbox_local(code_d2, {}, timeout_seconds=2)
el_d2 = time.time() - t0
dump("D2 SIGALRM defeated -> outer kill (cap=2)", env_d2, el_d2)
assert env_d2["status"] == "timeout", env_d2
assert el_d2 < 20, f"outer kill too slow: {el_d2:.1f}s"
assert el_d2 > 11, f"outer kill fired suspiciously early (alarm not actually defeated?): {el_d2:.1f}s"

print("ALL ADVERSARIAL SCENARIOS PASSED")
