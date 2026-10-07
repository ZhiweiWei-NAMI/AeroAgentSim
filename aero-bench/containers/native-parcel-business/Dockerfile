FROM localhost:5000/aero-bench/business@sha256:f6e27404f8a20c34ad60889dc6a8980f327ad808765c548c64b8c75a26adf76d

COPY --chown=65532:65532 aero_bench /opt/aero-bench/aero_bench

ARG AERO_BENCH_REVISION
LABEL org.opencontainers.image.source="https://github.com/ZhiweiWei-NAMI/AeroAgentSim" \
      io.aero-bench.component="logistics.native-parcel" \
      org.opencontainers.image.revision="${AERO_BENCH_REVISION}" \
      org.opencontainers.image.version="0.4.0-native-parcel-business.1"
