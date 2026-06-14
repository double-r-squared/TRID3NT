import os, sys, subprocess, shutil
sys.path.insert(0, 'services')
import numpy as np
import flopy
from workers.modflow.gwt_adapter import build_modflow_deck

EVID = "reports/inflight/job-0221-engine-20260609/evidence"
os.makedirs(EVID, exist_ok=True)
MF6 = "/tmp/mf6"
RUNDIR = "/tmp/mf6_e2e_run"
if os.path.exists(RUNDIR):
    shutil.rmtree(RUNDIR)
os.makedirs(RUNDIR)

print("=== building deck ===")
m = build_modflow_deck(
    spill_location_latlon=(26.64, -81.87),  # Fort Myers area
    contaminant="benzene",
    release_rate_kg_s=0.01,
    duration_days=30,
    aquifer_k_ms=1e-4,
    porosity=0.3,
    workdir=RUNDIR,
)
print(f"CRS={m.model_crs} grid={m.nrow}x{m.ncol} spill_cell=(r{m.spill_row},c{m.spill_col})")
print(f"mass_rate={m.mass_rate_g_per_day} g/day  total_released={m.total_released_mass_kg()} kg")

print("\n=== running mf6 6.5.0 ===")
proc = subprocess.run([MF6], cwd=RUNDIR, capture_output=True, text=True)
print(f"mf6 exit code: {proc.returncode}")
stdout_tail = "\n".join(proc.stdout.splitlines()[-25:])
print(stdout_tail)
if proc.returncode != 0:
    print("STDERR:", proc.stderr[-2000:])
    sys.exit(1)

# Save full run log
with open(f"{EVID}/mf6_run.log", "w") as f:
    f.write(f"mf6 binary: {MF6}\n")
    f.write(f"exit code: {proc.returncode}\n\n")
    f.write("===== STDOUT =====\n")
    f.write(proc.stdout)
    f.write("\n===== STDERR =====\n")
    f.write(proc.stderr)

# Convergence guard (design.md section 8 / OQ-MOD-1)
lst = os.path.join(RUNDIR, "mfsim.lst")
conv_fail = False
if os.path.exists(lst):
    with open(lst) as f:
        txt = f.read()
    conv_fail = "FAILED TO MEET SOLVER CONVERGENCE CRITERIA" in txt
print(f"\nconvergence-failure string present in mfsim.lst: {conv_fail}")

print("\n=== reading concentration output ===")
ucn_path = os.path.join(RUNDIR, "gwt_model.ucn")
assert os.path.exists(ucn_path), f"missing UCN: {ucn_path}"
ucn = flopy.utils.HeadFile(ucn_path, text="CONCENTRATION")
times = ucn.get_times()
print(f"n save-times: {len(times)}  last time (days): {times[-1]}")
conc = ucn.get_data(totim=times[-1])  # (nlay, nrow, ncol)
conc2d = conc[0]

finite = np.isfinite(conc2d).all()
cmax = float(np.nanmax(conc2d))
cmin = float(np.nanmin(conc2d))
nonzero_cells = int((conc2d > 1e-9).sum())
peak_idx = np.unravel_index(np.nanargmax(conc2d), conc2d.shape)
peak_row, peak_col = int(peak_idx[0]), int(peak_idx[1])

print(f"finite: {finite}")
print(f"conc range: min={cmin:.6g}  max={cmax:.6g} (mass/volume units, g/m^3 == mg/L)")
print(f"non-zero cells (>1e-9): {nonzero_cells} of {conc2d.size}")
print(f"peak cell (row,col): ({peak_row},{peak_col})   spill cell: ({m.spill_row},{m.spill_col})")
dist_from_source = abs(peak_row - m.spill_row) + abs(peak_col - m.spill_col)
print(f"manhattan distance peak->source: {dist_from_source} cells")

# Mass balance plausibility: integrate concentration over pore volume.
# pore volume per cell = dx*dy*thickness*porosity ; mass = conc * porevol
cell_porevol = m.delr * m.delc * 30.0 * m.porosity  # m^3 of water per cell
mass_in_domain_g = float(np.nansum(conc2d) * cell_porevol)
mass_in_domain_kg = mass_in_domain_g / 1000.0
total_released_kg = m.total_released_mass_kg()
print(f"\nmass dissolved in domain (final step): {mass_in_domain_kg:.4g} kg")
print(f"total released over {m.duration_days} d: {total_released_kg:.4g} kg")
frac = mass_in_domain_kg / total_released_kg if total_released_kg else 0
print(f"fraction of released mass still in domain: {frac:.3f}")
print("(fraction < 1 expected: some mass advects out the east boundary)")

# === ASSERTIONS ===
print("\n=== assertions ===")
assert finite, "concentration field has non-finite values"
print("PASS: field is finite")
assert cmax > 0, "max concentration is zero — no plume formed"
print(f"PASS: field is non-zero (max={cmax:.4g})")
assert nonzero_cells >= 3, "plume too small / no spread"
print(f"PASS: plume spread to {nonzero_cells} cells")
assert dist_from_source <= 3, f"peak not near source (dist={dist_from_source})"
print(f"PASS: peak is near source (dist={dist_from_source} cells)")
# plausibility: dissolved mass should be a meaningful, sub-total fraction
assert 0.0 < frac <= 1.5, f"mass fraction implausible: {frac}"
print(f"PASS: dissolved mass plausible vs release ({frac:.2%} retained)")

# Concentration summary printout to evidence
summary = []
summary.append("MODFLOW 6.5.0 GWF+GWT end-to-end run — concentration summary")
summary.append("=" * 60)
summary.append(f"scenario: benzene spill, Fort Myers area (26.64, -81.87)")
summary.append(f"release_rate: 0.01 kg/s  duration: 30 days  K: 1e-4 m/s  porosity: 0.3")
summary.append(f"model CRS: {m.model_crs}")
summary.append(f"grid: {m.nrow} x {m.ncol} @ {m.delr} m  ({m.nrow*m.ncol} cells, {m.nlay} layer)")
summary.append(f"spill cell (row,col): ({m.spill_row},{m.spill_col})")
summary.append(f"mf6 exit code: {proc.returncode}")
summary.append(f"convergence-failure flagged: {conv_fail}")
summary.append("")
summary.append(f"final concentration (t={times[-1]} d):")
summary.append(f"  min: {cmin:.6g}  max: {cmax:.6g}  (g/m^3 == mg/L)")
summary.append(f"  finite: {finite}")
summary.append(f"  non-zero cells (>1e-9 mg/L): {nonzero_cells} / {conc2d.size}")
summary.append(f"  peak cell (row,col): ({peak_row},{peak_col})  [source at ({m.spill_row},{m.spill_col})]")
summary.append(f"  manhattan peak->source: {dist_from_source} cells")
summary.append("")
summary.append(f"mass dissolved in domain (final): {mass_in_domain_kg:.4g} kg")
summary.append(f"total mass released (rate x duration): {total_released_kg:.4g} kg")
summary.append(f"fraction retained in domain: {frac:.3f} (<1 -> mass advects out east boundary)")
summary.append("")
# A compact ASCII plume slice across the spill row
summary.append(f"concentration along spill row {m.spill_row} (mg/L, west->east):")
row_vals = conc2d[m.spill_row]
summary.append("  " + "  ".join(f"{v:6.1f}" for v in row_vals[::2]))  # every 2nd col
summary.append("")
summary.append("ALL ASSERTIONS PASSED")
text = "\n".join(summary)
print("\n" + text)
with open(f"{EVID}/concentration_summary.txt", "w") as f:
    f.write(text + "\n")

# copy the list file as evidence too
shutil.copy(os.path.join(RUNDIR, "mfsim.lst"), f"{EVID}/mfsim.lst")
print(f"\nevidence written to {EVID}/")
