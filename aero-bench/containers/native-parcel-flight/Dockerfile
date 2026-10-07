FROM localhost:5000/aero-bench/flight@sha256:661dc7420eb130147ff422349e2bf097bd951c89f37a2f02e6f7aec088018abc

COPY --chown=65532:65532 containers/px4-gazebo/service.py /opt/aero-bench/service.py
ARG AERO_BENCH_REVISION
LABEL io.aero-bench.component="px4.gazebo" \
      io.aero-bench.implementation-kind="production" \
      org.opencontainers.image.revision="${AERO_BENCH_REVISION}" \
      org.opencontainers.image.source="https://github.com/ZhiweiWei-NAMI/AeroAgentSim" \
      org.opencontainers.image.version="0.4.0-px4-gazebo.1"
USER 65532:65532
