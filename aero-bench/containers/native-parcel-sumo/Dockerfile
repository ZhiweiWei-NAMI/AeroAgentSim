FROM localhost:5000/aero-bench/traffic@sha256:0823507eb65d928ea073a2eaaa252e63c4c0ffeacc9f9efb99c106f53b919f8f

COPY --chown=65532:65532 containers/sumo/service.py /opt/aero-bench/service.py
COPY --chown=65532:65532 containers/sumo/selfcheck.py /opt/aero-bench/selfcheck.py
COPY --chown=65532:65532 containers/sumo/entrypoint.py /opt/aero-bench/entrypoint.py
COPY --chown=65532:65532 aero_bench/world/frame_math.py /opt/aero-bench/aero_frame_math.py
COPY --chown=65532:65532 aero_bench/world/workload_scenario.py /opt/aero-bench/workload_scenario.py
COPY --chown=65532:65532 containers/readiness_probe.py /opt/aero-bench/readiness_probe.py

ARG AERO_BENCH_REVISION
LABEL io.aero-bench.component="sumo.traci" \
      org.opencontainers.image.revision="${AERO_BENCH_REVISION}" \
      org.opencontainers.image.source="https://github.com/ZhiweiWei-NAMI/AeroAgentSim" \
      org.opencontainers.image.version="0.4.0-sumo.2"
USER 65532:65532
