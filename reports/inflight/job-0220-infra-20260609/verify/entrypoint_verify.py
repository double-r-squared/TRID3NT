"""Independent re-verification of services.workers.modflow.entrypoint.

Fake in-memory GCS (no real network). Exercises:
  1. Full main() happy path against the committed fixture deck + real mf6.
  2. Convergence guard _check_convergence branches.
  3. Exit-code resolution: divergent list overrides exit-0 -> exit 2.
  4. manifest with subdir dest paths -> _download mkdir -p reconstruction.
"""
import json, os, sys, tempfile
from pathlib import Path

REPO = "/home/nate/Documents/GRACE-2"
MF6 = "/tmp/mf6_smoke/mf6"
FIX = Path(REPO) / "services/workers/modflow/fixtures"

os.environ["GRACE2_MF6_BIN"] = MF6
SCRATCH = tempfile.mkdtemp(prefix="mf6_ep_verify_")
os.environ["GRACE2_MF6_SCRATCH"] = SCRATCH
os.environ["GRACE2_RUNS_BUCKET"] = "fake-runs"
os.environ["GCP_PROJECT"] = "fake-project"

sys.path.insert(0, REPO)

STORE = {}
UPLOADED = {}
CACHE = "fake-cache"
RUN = "verify-run-001"

inputs = [
    ("mfsim.nam", "mfsim.nam"),
    ("smoke.tdis", "smoke.tdis"),
    ("smoke.ims", "smoke.ims"),
    ("smoke.nam", "smoke.nam"),
    ("smoke.dis", "smoke.dis"),
    ("smoke.ic", "smoke.ic"),
    ("smoke.npf", "smoke.npf"),
    ("smoke.chd", "smoke.chd"),
    ("smoke.oc", "smoke.oc"),
]
manifest = {
    "inputs": [
        {"gs_uri": f"gs://{CACHE}/modflow/{RUN}/{fn}", "dest": dest}
        for fn, dest in inputs
    ],
    "mf6_args": [],
    "model_crs": "EPSG:26915",
    "outputs": ["smoke.hds", "smoke.cbc", "*.lst", "mfsim.lst"],
}
manifest_uri = f"gs://{CACHE}/modflow/{RUN}/manifest.json"
STORE[manifest_uri] = json.dumps(manifest).encode()
for fn, _ in inputs:
    STORE[f"gs://{CACHE}/modflow/{RUN}/{fn}"] = (FIX / fn).read_bytes()


class FakeBlob:
    def __init__(self, bucket, name):
        self._uri = f"gs://{bucket}/{name}"

    def download_to_filename(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(STORE[self._uri])

    def download_as_text(self):
        return STORE[self._uri].decode()

    def upload_from_filename(self, path):
        data = Path(path).read_bytes()
        STORE[self._uri] = data
        UPLOADED[self._uri] = len(data)

    def upload_from_string(self, s, content_type=None):
        data = s.encode() if isinstance(s, str) else s
        STORE[self._uri] = data
        UPLOADED[self._uri] = data


class FakeBucket:
    def __init__(self, name):
        self._name = name

    def blob(self, name):
        return FakeBlob(self._name, name)


class FakeClient:
    def __init__(self, *a, **k):
        pass

    def bucket(self, name):
        return FakeBucket(name)


import services.workers.modflow.entrypoint as ep

ep.storage.Client = FakeClient  # type: ignore

failures = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        failures.append(label)


# ---- 1. Happy-path main() -------------------------------------------------
rc = ep.main(["--run-id", RUN, "--manifest-uri", manifest_uri])
check("main() returned exit 0", rc == 0)
comp = json.loads(STORE[f"gs://fake-runs/{RUN}/completion.json"].decode())
print("completion.json:", json.dumps(comp, indent=2))
check("completion status ok", comp["status"] == "ok")
check("completion exit_code 0", comp["exit_code"] == 0)
check("completion converged True", comp["converged"] is True)
check("completion model_crs echoed", comp["model_crs"] == "EPSG:26915")
check("smoke.hds in output_uris", any("smoke.hds" in u for u in comp["output_uris"]))
check("mfsim.lst in output_uris", any("mfsim.lst" in u for u in comp["output_uris"]))
check("smoke.hds uploaded 852 bytes", UPLOADED.get(f"gs://fake-runs/{RUN}/smoke.hds") == 852)

# ---- 2. Convergence guard branches ---------------------------------------
conv, note = ep._check_convergence(Path(SCRATCH))
check("guard: converged deck -> True", conv is True and note is None)

d2 = Path(tempfile.mkdtemp())
(d2 / "mfsim.lst").write_text("preamble\nFAILED TO MEET SOLVER CONVERGENCE CRITERIA\nmore\n")
conv2, note2 = ep._check_convergence(d2)
check("guard: divergent list -> not converged", conv2 is False)
check("guard: divergent note solver_diverged", note2 == "solver_diverged")

d3 = Path(tempfile.mkdtemp())
conv3, note3 = ep._check_convergence(d3)
check("guard: absent list -> not converged", conv3 is False and note3 is not None)

d4 = Path(tempfile.mkdtemp())
(d4 / "mfsim.lst").write_text("partial garbage with no markers\n")
conv4, note4 = ep._check_convergence(d4)
check("guard: no-marker list -> not converged", conv4 is False and note4 is not None)

# ---- 3. Exit-code resolution: divergent list overrides exit-0 -------------
orig_check = ep._check_convergence


def fake_diverge(cwd):
    return False, "solver_diverged"


ep._check_convergence = fake_diverge
rc2 = ep.main(["--run-id", "verify-diverge-002", "--manifest-uri", manifest_uri])
ep._check_convergence = orig_check
comp2 = json.loads(STORE["gs://fake-runs/verify-diverge-002/completion.json"].decode())
print("diverge completion:", json.dumps({k: comp2[k] for k in ("status", "exit_code", "converged", "error")}, indent=2))
check("diverge-override: main() returns exit 2", rc2 == 2)
check("diverge-override: status error", comp2["status"] == "error")
check("diverge-override: exit_code 2", comp2["exit_code"] == 2)
check("diverge-override: converged False", comp2["converged"] is False)

# ---- 4. subdir dest reconstruction ---------------------------------------
sub_dest = Path(SCRATCH) / "gwf/nested/deep.dis"
ep._download(FakeClient(), f"gs://{CACHE}/modflow/{RUN}/smoke.dis", sub_dest)
check("subdir _download reconstructs nested parent", sub_dest.exists())
check("subdir _download content matches source", sub_dest.read_bytes() == (FIX / "smoke.dis").read_bytes())

print()
if failures:
    print("VERIFY FAILED:", failures)
    sys.exit(1)
print("ALL INDEPENDENT ENTRYPOINT ASSERTIONS PASSED")
