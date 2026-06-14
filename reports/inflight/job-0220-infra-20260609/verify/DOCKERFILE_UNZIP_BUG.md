# CORRECTNESS REFUTATION — Dockerfile unzip path is broken (build-breaking)

## File / lines
services/workers/modflow/Dockerfile:88-90

## The bug
    ARG MF6_VERSION=6.5.0                                   # line 49
    ...
    && unzip -o /tmp/mf6.zip "mf6.${MF6_VERSION}_linux/bin/mf6" \
                             "mf6.${MF6_VERSION}_linux/bin/libmf6.so" -d /tmp/mf6_extracted \
    && install -m 755 "/tmp/mf6_extracted/mf6.${MF6_VERSION}_linux/bin/mf6"     /usr/local/bin/mf6 \
    && install -m 755 "/tmp/mf6_extracted/mf6.${MF6_VERSION}_linux/bin/libmf6.so" /usr/local/lib/libmf6.so

With MF6_VERSION=6.5.0, "mf6.${MF6_VERSION}_linux" expands to "mf6.6.5.0_linux".
The USGS 6.5.0 release zip's actual top-level directory is "mf6.5.0_linux"
(NOT "mf6.6.5.0_linux"). The zip filename mf6.5.0_linux.zip already drops the
leading "6." that the Dockerfile re-prepends.

## Proof (re-run on this box)
SHA-256-verified zip = 0fac00211c42b7a74c7266abbe50776a6215ea8409c8ce887e5decd4a9335940

zip contents (python zipfile):
  'mf6.6.5.0_linux/bin/mf6'   -> in zip? False   (Dockerfile-expanded target)
  'mf6.5.0_linux/bin/mf6'     -> in zip? True    (actual)

Running the EXACT Dockerfile unzip command:
  $ unzip -o mf6.5.0_linux.zip "mf6.6.5.0_linux/bin/mf6" "mf6.6.5.0_linux/bin/libmf6.so" -d /tmp/...
  caution: filename not matched:  mf6.6.5.0_linux/bin/mf6
  caution: filename not matched:  mf6.6.5.0_linux/bin/libmf6.so
  unzip exit code: 11
  (nothing extracted)

Consequence inside `docker build` / Cloud Build:
  - `unzip` exits 11 -> RUN step fails immediately (set -e via && chain).
  - Even if unzip's exit were swallowed, the next `install -m 755
    /tmp/mf6_extracted/mf6.6.5.0_linux/bin/mf6 ...` references a non-existent
    path -> install fails -> build aborts.
  => `make modflow-build` will FAIL at this layer. No image is ever produced.

The correct glob `mf6.5.0_linux/bin/mf6` extracts cleanly (exit 0, both files).

## Why the runner's evidence missed it
The [REQUIRED PASS] mf6 HOST smoke test downloaded the zip and used flopy's
run helper against a SEPARATELY pre-extracted binary at /tmp/mf6_smoke/mf6.
It never ran the Dockerfile's unzip/install lines. The in-Dockerfile build-time
smoke (`mf6 --version`, line 92 + 130) is UNREACHABLE because the build aborts
earlier at line 88. So the host smoke test does NOT prove "the image contents
are sound" as report.md § Deviations #1 / locals comment (modflow.tf:74-75) claims.

## Fix (one line, two occurrences collapse to a constant)
Replace the three `mf6.${MF6_VERSION}_linux` occurrences (lines 88, 89, 90)
with the literal zip dir `mf6.5.0_linux`, OR introduce a separate
`ARG MF6_ZIP_DIR=mf6.5.0_linux` and use $MF6_ZIP_DIR in the unzip + install
paths. Recommended: ARG MF6_ZIP_DIR=mf6.5.0_linux (keeps the version pin
visible and the dir name explicit, since USGS's naming is inconsistent —
zip is mf6.5.0 but binary --version reports 6.5.0).
