FROM docker.1panel.live/library/python@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY containers/logistics-business/requirements.lock /opt/aero-bench/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes \
      -r /opt/aero-bench/requirements.lock

# The provider workload wraps the canonical Logistics Orders service, the
# facility-catalogue state, staged business-environment receipts and the
# append-only history from the repository package. The package is copied as
# source (no build step) so the image stays reproducible from the pinned
# revision and the service can never drift from the accepted contracts.
COPY aero_bench /opt/aero-bench/aero_bench
COPY containers/logistics-business/selfcheck.py /opt/aero-bench/selfcheck.py
COPY containers/selfcheck_scenario.py /opt/aero-bench/containers/selfcheck_scenario.py
COPY containers/logistics-business/service.py /opt/aero-bench/service.py
COPY containers/logistics-business/entrypoint.py /opt/aero-bench/entrypoint.py
COPY aero_bench/world/workload_scenario.py /opt/aero-bench/workload_scenario.py
COPY containers/readiness_probe.py /opt/aero-bench/readiness_probe.py

ENV PYTHONPATH=/opt/aero-bench

# The RPC port is materializer-owned and passed per run; the image deliberately
# declares no EXPOSE. Health is the shared one-shot readiness probe: it drives
# a real token-free probe round trip and compares the reported identity against
# the workload contract and environment (AERO_BENCH_ROLE is executor-issued).
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
  CMD ["python", "/opt/aero-bench/readiness_probe.py", "--timeout-seconds", "2"]

ARG AERO_BENCH_REVISION
LABEL io.aero-bench.component="logistics.business" \
      io.aero-bench.implementation-kind="production" \
      org.opencontainers.image.revision="${AERO_BENCH_REVISION}" \
      org.opencontainers.image.source="https://github.com/ZhiweiWei-NAMI/AeroAgentSim" \
      org.opencontainers.image.version="0.4.0-logistics-business.2"

USER 65532:65532
WORKDIR /tmp
ENTRYPOINT ["python", "/opt/aero-bench/entrypoint.py"]
CMD ["provider", "serve"]
