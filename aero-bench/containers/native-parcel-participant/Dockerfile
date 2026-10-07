FROM localhost:5000/aero-bench/agent@sha256:d2c7898772d318e121144a18d4d9be1f0f9b04a932d2fc909340bf9224f813c7
COPY aero_bench /opt/aero-bench/aero_bench
ARG AERO_BENCH_REVISION
LABEL io.aero-bench.component="participant.agent" \
      io.aero-bench.implementation-kind="production" \
      org.opencontainers.image.revision="${AERO_BENCH_REVISION}" \
      org.opencontainers.image.source="https://github.com/ZhiweiWei-NAMI/AeroAgentSim" \
      org.opencontainers.image.version="0.4.0-native-parcel-participant.1"
USER 65532:65532
ENTRYPOINT ["python", "-m", "aero_bench.agent.native_parcel"]
CMD ["run"]
