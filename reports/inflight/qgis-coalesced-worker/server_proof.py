import sys, time
sys.path.insert(0, "/home/nate/Documents/grace2-tools/reports/inflight/qgis-coalesced-worker")
t=time.time()
import processing_server as S
print(f"INIT: {S.INIT_SECONDS}s | {len(S._ALGS)} algorithms | INIT_COUNT={S.INIT_COUNT}")
DEM="s3://grace2-hazard-cache-226996537797/cache/static-30d/dem/b64b3d4406e1830852297b4bea88edb8.tif"
calls=[("gdal:slope",{"INPUT":DEM,"BAND":1,"OUTPUT":"out.tif"}),
 ("gdal:aspect",{"INPUT":DEM,"BAND":1,"OUTPUT":"out.tif"}),
 ("gdal:hillshade",{"INPUT":DEM,"BAND":1,"OUTPUT":"out.tif"}),
 ("gdal:contour",{"INPUT":DEM,"BAND":1,"INTERVAL":50.0,"FIELD_NAME":"ELEV","OUTPUT":"out.gpkg"})]
for alg,params in calls:
    r=S.run_algorithm(alg,params)
    o=list(r["outputs"].values())
    print(f"  {alg:15s} compute={r['compute_s']:.2f}s -> {o[0] if o else 'NO OUTPUT'}")
print(f"PROOF: {len(calls)} requests served by INIT_COUNT={S.INIT_COUNT} (single warm init), {time.time()-t:.1f}s wall")
