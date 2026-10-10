FROM docker.1panel.live/library/python@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/opt/aero-bench

COPY containers/harness/requirements.lock /opt/aero-bench/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /opt/aero-bench/requirements.lock
COPY aero_bench /opt/aero-bench/aero_bench
COPY containers/codex-driver/vendor/codex /opt/codex/codex
COPY containers/codex-driver/vendor/codex-code-mode-host /opt/codex/codex-code-mode-host
RUN chmod 0555 /opt/codex/codex /opt/codex/codex-code-mode-host

ARG AERO_BENCH_REVISION
LABEL io.aero-bench.component="astra.driver" \
      io.aero-bench.implementation-kind="production" \
      org.opencontainers.image.revision="${AERO_BENCH_REVISION}" \
      org.opencontainers.image.source="https://github.com/ZhiweiWei-NAMI/AERO_BENCH" \
      org.opencontainers.image.version="0.4.0-astra-driver.1"

USER 65532:65532
WORKDIR /tmp
ENTRYPOINT ["python", "-m", "aero_bench.agent.driver_entrypoint"]
CMD ["driver", "run"]
