FROM docker.1panel.live/library/ubuntu@sha256:1e0a86e57d247923571b75e0aaf48a1449cf8c543d51fb3e07a4a7d7bfa79316

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
      bzip2 \
      ca-certificates \
      cmake \
      g++ \
      make \
      ninja-build \
      python3 \
      wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN wget --no-verbose --output-document ns-3.48.tar.bz2 \
      https://www.nsnam.org/releases/ns-3.48.tar.bz2 \
    && echo "5700ceecef2c9bc862502914b2237fe798d4c7ae07652247050e300e49407c4d  ns-3.48.tar.bz2" \
      | sha256sum --check --strict \
    && tar --extract --bzip2 --file ns-3.48.tar.bz2 \
    && rm ns-3.48.tar.bz2

COPY containers/ns3/aero-ns3-selfcheck.cc /opt/ns-3.48/scratch/aero-ns3-selfcheck.cc
COPY containers/ns3/aero-ns3-provider.cc /opt/ns-3.48/scratch/aero-ns3-provider.cc

WORKDIR /opt/ns-3.48
RUN ./ns3 configure \
      --build-profile=optimized \
      --disable-examples \
      --disable-tests \
      --enable-modules=core,network,internet,mobility,propagation,traffic-control,wifi \
    && ./ns3 build aero-ns3-selfcheck aero-ns3-provider

ARG AERO_BENCH_REVISION
LABEL io.aero-bench.component="ns3.rpc" \
      io.aero-bench.implementation-kind="production" \
      io.aero-bench.network-model="wifi-adhoc-scene-mobility/v1" \
      io.aero-bench.propagation-model="log-distance-with-scene-volumes/v1" \
      io.aero-bench.external.ns3.archive-sha256="5700ceecef2c9bc862502914b2237fe798d4c7ae07652247050e300e49407c4d" \
      io.aero-bench.external.ns3.commit="d2add90b452d600cfb4859baed8e9ea633519447" \
      io.aero-bench.external.ns3.version="3.48" \
      org.opencontainers.image.revision="${AERO_BENCH_REVISION}" \
      org.opencontainers.image.source="https://github.com/ZhiweiWei-NAMI/AERO_BENCH" \
      org.opencontainers.image.version="0.4.0-ns3.1"

USER 65532:65532
COPY containers/ns3/server.py /opt/aero-bench/ns3-server.py
COPY aero_bench/world/workload_scenario.py /opt/aero-bench/workload_scenario.py
COPY containers/readiness_probe.py /opt/aero-bench/readiness_probe.py

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 CMD ["python3", "/opt/aero-bench/readiness_probe.py", "--timeout-seconds", "2"]

ENTRYPOINT ["python3", "/opt/aero-bench/ns3-server.py"]
