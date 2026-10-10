FROM docker.1panel.live/library/python@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49 AS python-dependencies

COPY containers/harness/requirements.lock /requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes \
      --target /opt/aero-python -r /requirements.lock

FROM docker.1panel.live/library/python@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49 AS runtime-package

COPY containers/world-scene/stage_runtime.py /build/stage_runtime.py
COPY aero_bench /repo/aero_bench
RUN python /build/stage_runtime.py --source /repo/aero_bench --output /staged/aero-bench

FROM docker.1panel.live/library/python@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/aero-python

COPY --from=python-dependencies /opt/aero-python /opt/aero-python
COPY --from=runtime-package /staged/aero-bench/aero_bench /opt/aero-python/aero_bench
COPY containers/world-scene/selfcheck.py /opt/aero-bench/selfcheck.py
COPY containers/selfcheck_scenario.py /opt/aero-bench/containers/selfcheck_scenario.py
COPY containers/world-scene/service.py /opt/aero-bench/service.py
COPY containers/world-scene/entrypoint.py /opt/aero-bench/entrypoint.py
COPY aero_bench/world/workload_scenario.py /opt/aero-bench/workload_scenario.py
COPY containers/readiness_probe.py /opt/aero-bench/readiness_probe.py

ARG AERO_BENCH_REVISION
LABEL io.aero-bench.component="world.scene.rpc" \
      io.aero-bench.implementation-kind="production" \
      org.opencontainers.image.revision="${AERO_BENCH_REVISION}" \
      org.opencontainers.image.source="https://github.com/ZhiweiWei-NAMI/AERO_BENCH" \
      org.opencontainers.image.version="0.3.0-world-scene.2"

USER 65532:65532
WORKDIR /tmp
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 CMD ["python", "/opt/aero-bench/readiness_probe.py", "--timeout-seconds", "2"]
ENTRYPOINT ["python", "/opt/aero-bench/entrypoint.py"]
CMD ["provider", "serve"]
