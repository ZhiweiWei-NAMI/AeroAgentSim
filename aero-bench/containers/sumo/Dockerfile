FROM docker.1panel.live/library/python@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
      libatomic1 \
      libexpat1 \
      libgl1 \
      libx11-6 \
      libxext6 \
      libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY containers/sumo/requirements.lock /opt/aero-bench/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes \
      -r /opt/aero-bench/requirements.lock

COPY containers/sumo/selfcheck.py /opt/aero-bench/selfcheck.py
COPY containers/sumo/service.py /opt/aero-bench/service.py
COPY containers/sumo/entrypoint.py /opt/aero-bench/entrypoint.py
COPY aero_bench/world/frame_math.py /opt/aero-bench/aero_frame_math.py
COPY aero_bench/world/workload_scenario.py /opt/aero-bench/workload_scenario.py
COPY containers/readiness_probe.py /opt/aero-bench/readiness_probe.py

ARG AERO_BENCH_REVISION
LABEL io.aero-bench.component="sumo.traci" \
      io.aero-bench.implementation-kind="production" \
      io.aero-bench.external.sumo.commit="7717f2379d9e314a0c81c5cec748444de06a2a91" \
      io.aero-bench.external.sumo.version="1.27.1" \
      io.aero-bench.external.sumo.wheel-sha256="97abddcb7395c25538f22a272ef67ca77c8a625d3eec6010eed5b62dfac011f3" \
      org.opencontainers.image.revision="${AERO_BENCH_REVISION}" \
      org.opencontainers.image.source="https://github.com/ZhiweiWei-NAMI/AERO_BENCH" \
      org.opencontainers.image.version="0.4.0-sumo.2"

USER 65532:65532
WORKDIR /tmp
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 CMD ["python", "/opt/aero-bench/readiness_probe.py", "--timeout-seconds", "2"]
ENTRYPOINT ["python", "/opt/aero-bench/entrypoint.py"]
CMD ["provider", "serve"]
