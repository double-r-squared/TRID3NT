# TRID3NT dev image for ELMFIRE (FIRE-1) - adapted from the upstream first-party
# Dockerfile (ubuntu:22.04, make_gnu.sh) with container hygiene applied:
#   - TRUE multi-stage: compilers (gfortran, libopenmpi-dev, make) live only in
#     the builder stage; final stage gets binaries + runtime libs only.
#     (Upstream's "purge build-essential" ran in a later layer of the same
#     stage, which does not reclaim layer space, and gfortran was never purged.)
#   - No Cloudfire/gRPC pip deps (google-api-python-client, grpcio) - per the
#     ELMFIRE design doc we do not take the worldgen.cloudfire.io dependency.
#   - Dropped from final: nano, sudo, wget, csvkit, locales, pip.
#   - .dockerignore excludes .git (24M) and docs (19M).
# Pinned source: release tag 2025.0526 (checked out in the build context).

FROM ubuntu:22.04 AS builder
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -y && \
    apt-get install --no-install-recommends -y \
        gfortran \
        libopenmpi-dev \
        make \
        openmpi-bin && \
    rm -rf /var/lib/apt/lists/*

COPY build /elmfire/elmfire/build
WORKDIR /elmfire/elmfire/build/linux
# nice: a GPU benchmark shares this machine; the build is a short serial
# Fortran compile (no -j in make_gnu.sh) but keep it polite anyway.
RUN nice -n 10 ./make_gnu.sh

FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -y && \
    apt-get install --no-install-recommends -y \
        bc \
        gdal-bin \
        python3-gdal \
        jq \
        libgfortran5 \
        libgomp1 \
        openmpi-bin \
        pigz \
        python3-minimal && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /scratch/elmfire
COPY . /elmfire/elmfire
COPY --from=builder /elmfire/elmfire/build/linux/bin /elmfire/elmfire/build/linux/bin

WORKDIR /elmfire

ENV ELMFIRE_VER=2025.0526
ENV ELMFIRE_BASE_DIR=/elmfire/elmfire
ENV ELMFIRE_SCRATCH_BASE=/scratch/elmfire
ENV ELMFIRE_INSTALL_DIR=$ELMFIRE_BASE_DIR/build/linux/bin
ENV PATH=$PATH:$ELMFIRE_INSTALL_DIR

CMD ["bash"]
