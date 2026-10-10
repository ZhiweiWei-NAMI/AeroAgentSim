FROM 127.0.0.1:5000/participants/urban-inspection@sha256:2d195b179a6d62658e3cc7cdda6adec66947e230bf759aab4333e34c689ecb0a

COPY aero_bench /opt/participant/aero_bench
ARG PARTICIPANT_REVISION
ARG SDK_REVISION
LABEL org.opencontainers.image.revision="${PARTICIPANT_REVISION}" \
      org.opencontainers.image.version="2.0.1" \
      io.aero-bench.sdk-revision="${SDK_REVISION}" \
      io.aero-bench.participant-base="sha256:2d195b179a6d62658e3cc7cdda6adec66947e230bf759aab4333e34c689ecb0a"
