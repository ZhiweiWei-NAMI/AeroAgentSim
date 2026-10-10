FROM docker.1panel.live/library/python@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/opt/aero-bench

COPY containers/harness/requirements.lock /opt/aero-bench/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes \
      -r /opt/aero-bench/requirements.lock

COPY aero_bench /opt/aero-bench/aero_bench
COPY containers/urban-recovery-participant/entrypoint.py /opt/aero-bench/entrypoint.py

ARG AERO_BENCH_REVISION
ARG AERO_BENCH_AGENT_ID
RUN case "${AERO_BENCH_AGENT_ID}" in uav.policy.01|uav.policy.02|groundstation.rule) ;; *) exit 1 ;; esac
LABEL io.aero-bench.component="${AERO_BENCH_AGENT_ID}" \
      io.aero-bench.implementation-kind="production" \
      org.opencontainers.image.revision="${AERO_BENCH_REVISION}" \
      org.opencontainers.image.source="https://github.com/ZhiweiWei-NAMI/AERO_BENCH" \
      org.opencontainers.image.version="1.0.0-urban-recovery"

USER 65532:65532
WORKDIR /tmp
ENTRYPOINT ["python", "/opt/aero-bench/entrypoint.py"]
