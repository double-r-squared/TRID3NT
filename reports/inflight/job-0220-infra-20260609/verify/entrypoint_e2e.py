"""Independent e2e of services.workers.modflow.entrypoint.main() vs a fake
in-memory GCS + the REAL mf6 binary + the runner's actual fixture deck.
Verifies: download->run->upload envelope, completion.json shape, converged=True
on the real deck, AND the convergence guard's divergent + absent-list branches."""
import json, os, sys, io
from pathlib import Path

REPO = "/home/nate/Documents/GRACE-2"
sys.path.insert(0, REPO)
MF6 = os.path.abspath("mf6_dl/prefix/bin/mf6")
FIX = Path(REPO) / "services/workers/modflow/fixtures"

os.environ["GRACE2_MF6_BIN"] = MF6
os.environ["GRACE2_MF6_SCRATCH"] = os.path.abspath("e2e_scratch")
os.environ["GRACE2_RUNS_BUCKET"] = "fake-runs"
os.environ["GCP_PROJECT"] = "fake-proj"

# ---- fake in-memory GCS ----------------------------------------------------
class FakeBlob:
    def __init__(self, store, bucket, name):
        self.store=store; self.bucket=bucket; self.name=name
    def _key(self): return f"{self.bucket}/{self.name}"
    def download_to_filename(self, fn):
        data=self.store[self._key()]
        Path(fn).parent.mkdir(parents=True, exist_ok=True)
        Path(fn).write_bytes(data)
    def download_as_text(self):
        return self.store[self._key()].decode()
    def upload_from_filename(self, fn):
        self.store[self._key()]=Path(fn).read_bytes()
    def upload_from_string(self, s, content_type=None):
        self.store[self._key()]=s.encode() if isinstance(s,str) else s
class FakeBucket:
    def __init__(self, store, name): self.store=store; self.name=name
    def blob(self, name): return FakeBlob(self.store, self.name, name)
class FakeClient:
    def __init__(self, *a, **k): self.store={}
    def bucket(self, name): return FakeBucket(self.store, name)

import services.workers.modflow.entrypoint as ep
fake = FakeClient()
ep.storage.Client = lambda *a, **k: fake  # monkeypatch

# Stage the runner's fixture deck into the fake cache bucket + a real manifest
CACHE="fake-cache"; RUN="vrf-run-1"
deck = ["mfsim.nam","smoke.tdis","smoke.ims","smoke.nam","smoke.dis","smoke.ic","smoke.npf","smoke.chd","smoke.oc"]
manifest = {
  "inputs":[{"gs_uri":f"gs://{CACHE}/modflow/{RUN}/{f}","dest":f} for f in deck],
  "mf6_args":[], "model_crs":"EPSG:26915",
  "outputs":["smoke.hds","smoke.cbc","*.lst","mfsim.lst"],
}
for f in deck:
    fake.store[f"{CACHE}/modflow/{RUN}/{f}"]=(FIX/f).read_bytes()
man_uri=f"gs://{CACHE}/modflow/{RUN}/manifest.json"
fake.store[f"{CACHE}/modflow/{RUN}/manifest.json"]=json.dumps(manifest).encode()

print("=== run entrypoint.main on real fixture deck ===")
rc = ep.main(["--run-id", RUN, "--manifest-uri", man_uri])
print("entrypoint exit code:", rc)
comp_key=f"fake-runs/{RUN}/completion.json"
comp=json.loads(fake.store[comp_key].decode())
print("completion.json:", json.dumps(comp, indent=2))
assert rc==0, f"expected exit 0, got {rc}"
assert comp["status"]=="ok"
assert comp["exit_code"]==0
assert comp["converged"] is True
assert comp["model_crs"]=="EPSG:26915"
hds_key=f"fake-runs/{RUN}/smoke.hds"
assert hds_key in fake.store, "smoke.hds not uploaded"
print("uploaded smoke.hds bytes:", len(fake.store[hds_key]))
assert any("smoke.hds" in u for u in comp["output_uris"])
assert any("mfsim.lst" in u for u in comp["output_uris"])
print("E2E (real-deck) PASS")

# ---- Convergence guard branches (unit-level, no mf6) -----------------------
print("\n=== convergence guard branches ===")
import tempfile
# (a) converged deck -> True
d=Path(tempfile.mkdtemp()); (d/"mfsim.lst").write_text("...\n Normal termination of simulation.\n")
print("converged:", ep._check_convergence(d))
assert ep._check_convergence(d)==(True, None)
# (b) divergent list -> solver_diverged
d2=Path(tempfile.mkdtemp()); (d2/"mfsim.lst").write_text("FAILED TO MEET SOLVER CONVERGENCE CRITERIA\n")
print("divergent:", ep._check_convergence(d2))
assert ep._check_convergence(d2)==(False, "solver_diverged")
# (c) absent list -> not converged
d3=Path(tempfile.mkdtemp())
print("absent:", ep._check_convergence(d3))
assert ep._check_convergence(d3)[0] is False
print("CONVERGENCE GUARD PASS")
print("\nALL E2E ASSERTIONS PASSED")
