"""Independent live-verify: build a fresh 10x10 GWF model via flopy, run mf6,
inspect head output numerically. Does NOT reuse the runner's fixtures."""
import os, sys, numpy as np, flopy

MF6 = os.path.abspath("mf6_dl/prefix/bin/mf6")
WS = os.path.abspath("verify_model")
os.makedirs(WS, exist_ok=True)

print("flopy", flopy.__version__, "numpy", np.__version__)
print("mf6 binary:", MF6)

sim = flopy.mf6.MFSimulation(sim_name="vrf", sim_ws=WS, exe_name=MF6)
tdis = flopy.mf6.ModflowTdis(sim, nper=1, perioddata=[(1.0, 1, 1.0)], time_units="days")
ims = flopy.mf6.ModflowIms(sim, complexity="simple",
                           outer_dvclose=1e-6, inner_dvclose=1e-6)
gwf = flopy.mf6.ModflowGwf(sim, modelname="vrf", save_flows=True)
dis = flopy.mf6.ModflowGwfdis(gwf, nlay=1, nrow=10, ncol=10,
                              delr=100.0, delc=100.0, top=10.0, botm=0.0,
                              length_units="meters")
ic = flopy.mf6.ModflowGwfic(gwf, strt=5.0)
npf = flopy.mf6.ModflowGwfnpf(gwf, icelltype=0, k=1e-4)
# Left edge head=8, right edge head=2 -> linear gradient steady state
chd_spd = []
for r in range(10):
    chd_spd.append([(0, r, 0), 8.0])
    chd_spd.append([(0, r, 9), 2.0])
chd = flopy.mf6.ModflowGwfchd(gwf, stress_period_data=chd_spd)
oc = flopy.mf6.ModflowGwfoc(gwf, head_filerecord="vrf.hds",
                            budget_filerecord="vrf.cbc",
                            saverecord=[("HEAD", "ALL"), ("BUDGET", "ALL")])
sim.write_simulation()
print("=== running mf6 ===")
success, buff = sim.run_simulation(silent=False)
print("run_simulation success:", success)
if not success:
    print("BUILD/RUN FAILED"); sys.exit(1)

# Inspect head output numerically
hds = gwf.output.head().get_data()
print("head shape:", hds.shape)
print("head min:", float(np.min(hds)), "max:", float(np.max(hds)))
print("all_finite:", bool(np.all(np.isfinite(hds))))
# Physics sanity: left col ~8, right col ~2, monotonic decreasing across cols
mid = hds[0, 5, :]
print("middle-row head profile (col 0..9):", [round(float(x), 4) for x in mid])
left_ok = abs(float(hds[0,5,0]) - 8.0) < 1e-3
right_ok = abs(float(hds[0,5,9]) - 2.0) < 1e-3
mono = all(mid[i] >= mid[i+1] - 1e-9 for i in range(9))
print("left col == 8.0:", left_ok)
print("right col == 2.0:", right_ok)
print("monotonic decreasing L->R:", mono)
# Check normal termination string in list file
lst = os.path.join(WS, "mfsim.lst")
txt = open(lst).read()
print("Normal termination present:", "Normal termination of simulation" in txt)
print("Convergence-failure marker present:", "FAILED TO MEET SOLVER CONVERGENCE CRITERIA" in txt)
hds_size = os.path.getsize(os.path.join(WS, "vrf.hds"))
print("vrf.hds size_bytes:", hds_size)
assert hds.shape == (1, 10, 10)
assert bool(np.all(np.isfinite(hds)))
assert left_ok and right_ok and mono
assert "Normal termination of simulation" in txt
assert hds_size > 0
print("INDEPENDENT SMOKE TEST PASS")
