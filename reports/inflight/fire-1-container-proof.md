# FIRE-1 Proof: ELMFIRE Container + Canonical Known-Good Reproduction

- Date: 2026-07-07
- Design doc: reports/design/elmfire-engine-2026-07-07.md (section 4, "GeoClaw lesson applied")
- Verdict: PASS - tutorial 01 and verification case 01 both reproduce in-container with valid outputs.

## Source

- Repo: https://github.com/lautenberger/elmfire cloned (shallow) to
  /home/nate/Documents/trid3nt-local/vendor/elmfire
- Checked out at the release tag the design doc pins: **2025.0526**
  (commit 23a4cbd27fc84b4b194fed48a8d933f7e7c7fdeb). Newer tags exist upstream
  (2025.0609, 2025.0717); main HEAD is at internal version 2025.1002.

## Image: trid3nt/elmfire:dev

- Built from `Dockerfile.trid3nt` (in the vendor clone), adapted from the
  repo's first-party Dockerfile.
- Size: **522 MB unpacked on disk / 137 MB content (compressed)**.
- Solver binary confirms `ELMFIRE 2025.0526` at runtime.

### Layer breakdown (docker history)

| Size | Layer |
|---|---|
| 87.7 MB | ubuntu:22.04 base |
| 286 MB | runtime apt deps (bc, gdal-bin, python3-gdal, jq, libgfortran5, libgomp1, openmpi-bin, pigz, python3-minimal) - gdal-bin + python3-gdal dominate |
| 12.3 kB | mkdir /scratch/elmfire |
| 7.22 MB | COPY repo (tutorials, verification, build sources; .git/docs/ucb_team_doc dockerignored) |
| 4 MB | COPY compiled binaries from builder stage (elmfire, elmfire_post + debug variants) |
| 0 B | ENV/CMD metadata |

### Dockerfile adaptations vs upstream (documented per task)

1. **True multi-stage.** Upstream is nominally two-stage but the second stage
   is `FROM intermediate`, so `apt-get purge build-essential` runs as a LATER
   LAYER of the same image - layers are additive, nothing is reclaimed, and
   gfortran/libopenmpi-dev were never purged at all. Adapted: builder stage
   (gfortran, libopenmpi-dev, make, openmpi-bin) compiles via the repo's own
   `build/linux/make_gnu.sh`; final stage is ubuntu:22.04 + runtime libs only
   (libgfortran5, libgomp1, OpenMPI runtime) + `COPY --from=builder` of
   build/linux/bin. No compiler or -dev package in any final-image layer.
2. **Dropped the Cloudfire/gRPC deps** (google-api-python-client, grpcio,
   grpcio-tools pip installs and the CLOUDFIRE_SERVER env) - the design doc
   forbids the worldgen.cloudfire.io dependency; tutorials 01/02 and
   verification need none of it.
3. **Dropped from final image:** nano, sudo, wget, csvkit, locales, pip,
   `apt-get upgrade`.
4. **.dockerignore** extended (upstream had docs + .github): added .git
   (24 MB), ucb_team_doc, Dockerfiles, docker-compose.yml.
5. **Build politeness:** make_gnu.sh is a short serial compile (no -j);
   wrapped in `nice -n 10` and the whole build was niced because a GPU
   benchmark shares the machine. Build took ~48 s.

### Portability caveat for FIRE-4 (flagged, not fixed here)

`Makefile_elmfire` compiles with `-march=native`. Fine for this local dev
image; the ECR/Batch image in FIRE-4 must override to a generic
microarchitecture (e.g. -march=x86-64-v3) or pin the Batch instance family,
or it can SIGILL on older hosts.

## Tutorial 01 (constant wind) - PASS

Command (exact reproduction):

```
export DOCKER_HOST=unix:///run/user/1000/docker.sock   # rootless docker (see note)
docker run --rm --cpus=4 -v "$PWD/out01:/out" trid3nt/elmfire:dev bash -c \
  "cp -r /elmfire/elmfire/tutorials /scratch/ && \
   cd /scratch/tutorials/01-constant-wind && ./01-run.sh && cp -r outputs/* /out/"
```

- Runtime: **4.3 s** for the full 01-run.sh (input generation + solve +
  postprocess) inside the container; ~5.5 s wall including container start.
- Solver log: `ELMFIRE 2025.0526` ... `Fire area: 3851.9 acres.` ...
  `End of simulation reached successfully.`
- Note: tutorial 01's elmfire.data.in dumps DUMP_FLIN, DUMP_SPREAD_RATE,
  DUMP_TIME_OF_ARRIVAL (no flame-length flag in this tutorial - matches the
  design doc's expected outputs list; flame length is a separate DUMP flag
  available for our composer).

### Output rasters (gdalinfo)

All three: 400x400, 30 m pixels, EPSG:32610 (UTM 10N), NoData -9999,
DEFLATE GeoTIFF, non-empty stats:

| Raster | Min | Max | Mean |
|---|---|---|---|
| time_of_arrival_0000001_0019804.tif (s) | 30.0 | 19804.2 | 13193.8 |
| flin_0000001_0019804.tif (fireline intensity, kW/m) | 24.7 | 1218.3 | 916.1 |
| vs_0000001_0019804.tif (spread rate, ft/min) | 1.6 | 81.1 | 61.0 |

Plus hourly_isochrones.shp: 9 line features, extent
(-1090, -4495) to (1060, 3150).

### Downwind-ellipse acceptance

Ignition at (0, 3000), 15 mph wind, WD = 0 deg (from north). Burned extent:
~7.5 km downwind (south, to y = -4495), ~150 m upwind, ~1.1 km half-width -
a strongly downwind-elongated ellipse, as the tutorial doc specifies.

## Verification case 01 (elliptical fire shape vs exact solution) - PASS

Command:

```
docker run --rm --cpus=4 -v "$PWD/verif01:/out" trid3nt/elmfire:dev bash -c \
  "cp -r /elmfire/elmfire/tutorials /elmfire/elmfire/verification /scratch/ && \
   cd /scratch/verification/01-elliptical-shape && ./01-build-ellipse.sh && \
   cp -r outputs exact_ellipses /out/"
```

- 2400x2400 at 5 m, 25170 s sim; runtime **67 s** in-container (4 cpus).
- The case builds exact Behave-derived ellipses (L/W 2.236, head rate
  843.99 m/hr) and runs ELMFIRE on the same scenario. Repo docs eyeball the
  two in QGIS; here diffed numerically (burned area from the ToA raster vs
  shoelace area of the exact ellipse WKT; head-fire y-extent vs exact):

| hr | sim km2 | exact km2 | area err % | sim head y | exact head y | head err m |
|---|---|---|---|---|---|---|
| 1 | 0.285 | 0.279 | +2.35 | -2152.5 | -2156.0 | 3.5 |
| 2 | 1.134 | 1.115 | +1.67 | -1302.5 | -1312.0 | 9.5 |
| 3 | 2.546 | 2.510 | +1.44 | -452.5 | -468.0 | 15.5 |
| 4 | 4.522 | 4.462 | +1.34 | 397.5 | 376.0 | 21.5 |
| 5 | 7.058 | 6.972 | +1.24 | 1247.5 | 1219.9 | 27.6 |
| 6 | 10.157 | 10.039 | +1.17 | 2092.5 | 2063.9 | 28.6 |

Burned area within 1.2-2.4% of exact (small positive bias from finite cell
size, shrinking with time as expected); head-fire position within 29 m over
~5.1 km of spread (< 0.6%). Simulated head-fire advance is ~850 m/hr vs the
exact 843.99 m/hr.

## Environment notes

- Docker on this machine: the rootful daemon socket is root:docker and nate
  has no docker-group membership or passwordless sudo, so a **rootless
  docker** user daemon was set up (`dockerd-rootless-setuptool.sh install`;
  all prereqs were already present). The image and containers live under the
  rootless daemon: `DOCKER_HOST=unix:///run/user/1000/docker.sock`. The
  setuptool also switched the default docker CLI context to `rootless`
  (`docker context use default` reverts, but the default context was
  permission-denied for nate anyway). No existing process was touched; the
  GPU benchmark ran throughout; builds and runs were niced / --cpus=4 capped.
- Artifacts (logs, output rasters, diff script) in the session scratchpad
  under out01/, verif01/, tut01-stdout.log, verif01-stdout.log,
  elmfire-build.log, verif_diff2.py.
- Nothing pushed, no AWS touched, no git commits made (vendor clone has the
  untracked Dockerfile.trid3nt + a 5-line .dockerignore extension).

## FIRE-1 acceptance vs design doc

- [x] First-party-derived container builds from pinned release 2025.0526
- [x] Tutorial 01 reproduces in-container: outputs exist, valid GeoTIFFs,
      ellipse is downwind
- [x] Verification case 01 matches the exact solution (numeric diff above)
- [ ] Verification case 02 (crown fire) - not run in this pass; noted as the
      remaining regression-gate item for the FIRE-1 job proper
