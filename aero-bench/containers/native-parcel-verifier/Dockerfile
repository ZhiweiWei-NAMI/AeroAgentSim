# The corrected datum-aware verifier is the immutable base; parcel goals have
# a distinct entrypoint and require their own newly built OCI digest.
FROM localhost:5000/aero-bench/verifier@sha256:e8c2d1dc09369aa4d800e0f2ee975e97641922f01ac94d950964f884a7e74eec

COPY --chown=65532:65532 aero_bench /opt/aero-bench/aero_bench
COPY --chown=65532:65532 containers/inspection-verifier/entrypoint.py /opt/aero-bench/inspection_entrypoint.py
COPY --chown=65532:65532 containers/native-parcel-verifier/entrypoint.py /opt/aero-bench/native_parcel_entrypoint.py

ARG AERO_BENCH_REVISION
LABEL org.opencontainers.image.source="https://github.com/ZhiweiWei-NAMI/AeroAgentSim" \
      io.aero-bench.component="logistics.parcel.verifier" \
      org.opencontainers.image.revision="${AERO_BENCH_REVISION}" \
      org.opencontainers.image.version="0.1.0-native-parcel"

USER 65532:65532
ENTRYPOINT ["python", "/opt/aero-bench/native_parcel_entrypoint.py"]
CMD ["verify"]
