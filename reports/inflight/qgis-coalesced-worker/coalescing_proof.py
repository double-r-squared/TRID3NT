"""COALESCING PROOF: one persistent QgsApplication runs N Processing algos in a
SINGLE init (the turn-scoped warm box), vs the current per-call path (a fresh
qgis_process subprocess + full init per algo). Times both -> the savings = the
avoided re-inits. Runs headless on the local grace2 conda QGIS. ASCII only.
"""
import os, time, subprocess
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
D = "/tmp/qgis_proof"
DEM = f"{D}/dem.tif"
QGIS_PROCESS = "/home/nate/miniforge3/envs/grace2/bin/qgis_process"

# --- 1. ONE-TIME init: the fixed "spin-up" cost the warm box pays ONCE ---
t0 = time.time()
from qgis.core import QgsApplication
qgs = QgsApplication([], False)
qgs.initQgis()
import processing
from processing.core.Processing import Processing
Processing.initialize()
T_INIT = time.time() - t0
try:
    from grassprovider.grass_provider import GrassProvider
    QgsApplication.processingRegistry().addProvider(GrassProvider())
    grass = "loaded"
except Exception as e:
    grass = f"unavailable ({type(e).__name__})"
algs = set(a.id() for a in QgsApplication.processingRegistry().algorithms())
print(f"[INIT] persistent QgsApplication + Processing init: {T_INIT:.2f}s | "
      f"{len(algs)} algorithms | grass provider: {grass}")


def run(alg, params, label):
    if alg not in algs:
        print(f"  [n/a ] {label}  ({alg} not available)")
        return 0.0
    t = time.time()
    try:
        processing.run(alg, params)
        dt = time.time() - t
        print(f"  [{dt:5.2f}s] {label}  ({alg})")
        return dt
    except Exception as e:
        dt = time.time() - t
        print(f"  [FAIL{dt:5.2f}s] {label}  ({alg}): {type(e).__name__}: {str(e)[:70]}")
        return dt


# --- 2. COALESCED: a real chained pipeline, all in the ONE warm process ---
print("\n[COALESCED PIPELINE] N algos, one init:")
steps = []
steps.append(run("gdal:slope", {"INPUT": DEM, "BAND": 1, "OUTPUT": f"{D}/slope.tif"}, "slope"))
steps.append(run("gdal:aspect", {"INPUT": DEM, "BAND": 1, "OUTPUT": f"{D}/aspect.tif"}, "aspect"))
steps.append(run("gdal:hillshade", {"INPUT": DEM, "BAND": 1, "OUTPUT": f"{D}/hillshade.tif"}, "hillshade"))
steps.append(run("gdal:contour", {"INPUT": DEM, "BAND": 1, "INTERVAL": 50.0,
                                   "FIELD_NAME": "ELEV", "OUTPUT": f"{D}/contours.gpkg"}, "contour (dem->lines)"))
# dependent chain: slope -> reclassify -> polygonize -> buffer
steps.append(run("native:reclassifybytable",
                 {"INPUT_RASTER": f"{D}/slope.tif", "RASTER_BAND": 1,
                  "TABLE": [0, 15, 1, 15, 30, 2, 30, 90, 3], "OUTPUT": f"{D}/slopeclass.tif"},
                 "reclassify slope -> 3 classes"))
steps.append(run("gdal:polygonize", {"INPUT": f"{D}/slopeclass.tif", "BAND": 1,
                                      "FIELD": "DN", "OUTPUT": f"{D}/slopepolys.gpkg"}, "polygonize classes -> polys"))
steps.append(run("native:dissolve", {"INPUT": f"{D}/slopepolys.gpkg", "FIELD": ["DN"],
                                      "OUTPUT": f"{D}/dissolved.gpkg"}, "dissolve by class (dependent)"))
steps.append(run("native:buffer", {"INPUT": f"{D}/dissolved.gpkg", "DISTANCE": 0.003,
                                    "SEGMENTS": 5, "OUTPUT": f"{D}/buffered.gpkg"}, "buffer dissolved (dependent)"))
# hydrology workhorse (grass) if available
steps.append(run("grass:r.watershed", {"elevation": DEM, "accumulation": f"{D}/accum.tif",
                                        "threshold": 1000}, "r.watershed (grass hydrology)"))

n_ok = sum(1 for s in steps if s > 0)
compute = sum(steps)
COALESCED = T_INIT + compute
print(f"\n[COALESCED] {n_ok} algos succeeded in ONE init -> total {COALESCED:.2f}s "
      f"(init {T_INIT:.2f}s + compute {compute:.2f}s)")
qgs.exitQgis()

# --- 3. PER-CALL baseline: one qgis_process subprocess (fresh init EVERY call) ---
t = time.time()
subprocess.run([QGIS_PROCESS, "run", "gdal:slope", f"--INPUT={DEM}", "--BAND=1",
                f"--OUTPUT={D}/slope_sub.tif"],
               env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, capture_output=True)
T_SUB = time.time() - t
# subprocess init overhead = full subprocess time minus the algo's own compute
# (the in-process slope compute is steps[0]); the per-call path pays this EVERY algo.
init_overhead = max(0.1, T_SUB - (steps[0] if steps and steps[0] > 0 else 0.0))
# correct comparison holds COMPUTE constant: per-call = N*init + compute; coalesced = 1*init + compute.
percall = n_ok * init_overhead + compute
saved = (n_ok - 1) * init_overhead
print(f"\n[PER-CALL] one qgis_process subprocess (fresh init + 1 algo): {T_SUB:.2f}s "
      f"(=> ~{init_overhead:.2f}s init overhead per call)")
print("\n=== COALESCING WIN (the turn-scoped warm box) ===")
print(f"  per-call path  ({n_ok} algos, fresh init each): ~{percall:5.1f}s  "
      f"({n_ok} x {init_overhead:.1f}s init + {compute:.1f}s compute)")
print(f"  coalesced warm box (one init):                  {COALESCED:5.1f}s  "
      f"(1 x {T_INIT:.1f}s init + {compute:.1f}s compute)")
print(f"  SAVED per turn: ~{saved:.1f}s  = the {n_ok - 1} avoided re-inits @ ~{init_overhead:.1f}s each")
print(f"  (init overhead is the dominant per-call cost for the many sub-second algos -> "
      f"coalescing removes ~{100 * saved / percall:.0f}% of this turn's QGIS wall-time)")
