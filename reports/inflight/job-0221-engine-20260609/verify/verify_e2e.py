"""Independent adversarial re-run of the job-0221 mf6 GWF+GWT deck E2E.

Does NOT reuse the runner's evidence script. Builds the deck fresh from the
committed adapter, runs the independently-downloaded mf6 6.5.0 binary, and
inspects the concentration array numerically with the verifier's own physics
assertions (not the runner's).
"""
import os
import shutil
import subprocess
import sys

import numpy as np

# Import the committed adapter (the thing under audit), not a copy.
sys.path.insert(0, "/home/nate/Documents/GRACE-2/services/workers/modflow")
import flopy  # noqa: E402
from gwt_adapter import build_modflow_deck, AQUIFER_THICKNESS_M  # noqa: E402

VERIFY = "/home/nate/Documents/GRACE-2/reports/inflight/job-0221-engine-20260609/verify"
MF6 = os.path.join(VERIFY, "mf6")
RUNDIR = os.path.join(VERIFY, "e2e_run")
if os.path.exists(RUNDIR):
    shutil.rmtree(RUNDIR)
os.makedirs(RUNDIR)

print("=== build deck (Fort Myers benzene spill) ===")
m = build_modflow_deck(
    spill_location_latlon=(26.64, -81.87),
    contaminant="benzene",
    release_rate_kg_s=0.01,
    duration_days=30,
    aquifer_k_ms=1e-4,
    porosity=0.3,
    workdir=RUNDIR,
)
print(f"CRS={m.model_crs} grid={m.nrow}x{m.ncol} spill=(r{m.spill_row},c{m.spill_col})")
print(f"mass_rate={m.mass_rate_g_per_day} g/day total_released={m.total_released_mass_kg()} kg")
print(f"files written: {len(m.files)}")

print("\n=== run mf6 ===")
proc = subprocess.run([MF6], cwd=RUNDIR, capture_output=True, text=True)
print(f"exit code: {proc.returncode}")
print("\n".join(proc.stdout.splitlines()[-8:]))
assert proc.returncode == 0, f"mf6 nonzero exit {proc.returncode}: {proc.stderr[-1000:]}"
assert "Normal termination of simulation" in proc.stdout, "no normal-termination banner"

# Convergence guard (design OQ-MOD-1)
lst = os.path.join(RUNDIR, "mfsim.lst")
txt = open(lst).read()
conv_fail = "FAILED TO MEET SOLVER CONVERGENCE CRITERIA" in txt
print(f"convergence-failure string present: {conv_fail}")
assert not conv_fail, "solver diverged"

print("\n=== read concentration ===")
ucn = flopy.utils.HeadFile(os.path.join(RUNDIR, "gwt_model.ucn"), text="CONCENTRATION")
times = ucn.get_times()
conc2d = ucn.get_data(totim=times[-1])[0]
finite = bool(np.isfinite(conc2d).all())
cmax = float(np.nanmax(conc2d))
cmin = float(np.nanmin(conc2d))
nonzero = int((conc2d > 1e-9).sum())
pr, pc = (int(x) for x in np.unravel_index(np.nanargmax(conc2d), conc2d.shape))
dist = abs(pr - m.spill_row) + abs(pc - m.spill_col)
print(f"last time={times[-1]} d  finite={finite}  min={cmin:.4g} max={cmax:.4g} mg/L")
print(f"nonzero cells={nonzero}/{conc2d.size}  peak=({pr},{pc}) spill=({m.spill_row},{m.spill_col}) dist={dist}")

# Independent mass-balance check (verifier's own arithmetic, using the
# adapter's own thickness constant — NOT the hardcoded 30.0 in the runner's
# script — to catch a mismatch if one existed).
cell_porevol = m.delr * m.delc * AQUIFER_THICKNESS_M * m.porosity
mass_kg = float(np.nansum(conc2d) * cell_porevol) / 1000.0
released_kg = m.total_released_mass_kg()
frac = mass_kg / released_kg
print(f"\nmass in domain={mass_kg:.5g} kg  released={released_kg:.5g} kg  frac={frac:.4f}")

print("\n=== assertions (verifier's own thresholds) ===")
assert finite, "non-finite conc"
print("PASS finite")
assert cmax > 0, "zero plume"
print(f"PASS non-zero (max={cmax:.4g} mg/L)")
assert nonzero >= 3, "plume too small"
print(f"PASS spread ({nonzero} cells)")
assert dist <= 3, f"peak not at source (dist={dist})"
print(f"PASS peaked near source (dist={dist})")
assert 0.0 < frac <= 1.5, f"mass implausible {frac}"
print(f"PASS mass plausible ({frac:.2%} retained)")

# Cross-check: SRC mass rate vs 0.01 kg/s -> g/day exactly
assert abs(m.mass_rate_g_per_day - 0.01 * 1000.0 * 86400.0) < 1e-6
print("PASS src rate == 864000 g/day")

# Cross-check: CRS is UTM 17N for Fort Myers
assert m.model_crs == "EPSG:32617", m.model_crs
print("PASS CRS EPSG:32617 (UTM 17N)")

print("\nALL VERIFIER ASSERTIONS PASSED")
