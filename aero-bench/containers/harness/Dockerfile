FROM docker.1panel.live/library/python@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/opt/aero-bench

COPY containers/harness/requirements.lock /opt/aero-bench/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes \
      -r /opt/aero-bench/requirements.lock

COPY --chown=65532:65532 aero_bench /opt/aero-bench/aero_bench
COPY --chown=65532:65532 containers/harness/entrypoint.py /opt/aero-bench/entrypoint.py
COPY --chown=65532:65532 containers/harness/selfcheck.py /opt/aero-bench/selfcheck.py
COPY --chown=65532:65532 aero_bench/world/workload_scenario.py /opt/aero-bench/workload_scenario.py
COPY --chown=65532:65532 containers/readiness_probe.py /opt/aero-bench/readiness_probe.py

ARG AERO_BENCH_REVISION
LABEL io.aero-bench.component="aero-bench.harness" \
      io.aero-bench.implementation-kind="production" \
      org.opencontainers.image.revision="${AERO_BENCH_REVISION}" \
      org.opencontainers.image.source="https://github.com/ZhiweiWei-NAMI/AeroAgentSim" \
      org.opencontainers.image.version="0.4.0-harness.1"

USER 65532:65532
WORKDIR /tmp
HEALTHCHECK --interval=10s --timeout=3s --start-period=420s --retries=3 CMD ["python", "/opt/aero-bench/readiness_probe.py", "--timeout-seconds", "2"]
ENTRYPOINT ["python", "/opt/aero-bench/entrypoint.py"]
CMD ["serve"]
