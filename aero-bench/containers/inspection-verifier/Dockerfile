FROM docker.1panel.live/library/python@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/opt/aero-bench

COPY containers/harness/requirements.lock /opt/aero-bench/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes \
      -r /opt/aero-bench/requirements.lock

COPY aero_bench /opt/aero-bench/aero_bench
COPY containers/inspection-verifier/entrypoint.py /opt/aero-bench/entrypoint.py
COPY containers/inspection-verifier/selfcheck.py /opt/aero-bench/selfcheck.py

ARG AERO_BENCH_REVISION
LABEL io.aero-bench.component="inspection.verifier" \
      io.aero-bench.implementation-kind="production" \
      org.opencontainers.image.revision="${AERO_BENCH_REVISION}" \
      org.opencontainers.image.source="https://github.com/ZhiweiWei-NAMI/AERO_BENCH" \
      org.opencontainers.image.version="0.4.0-inspection-verifier.4"

USER 65532:65532
WORKDIR /tmp
ENTRYPOINT ["python", "/opt/aero-bench/entrypoint.py"]
CMD ["verify"]
