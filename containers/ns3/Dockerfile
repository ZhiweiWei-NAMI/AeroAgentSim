# This immutable production stage already contains Ubuntu 24.04, the original
# SHA-verified ns-3.48 tree and compiler. Build only our new provider, then copy
# runtime files into a fresh rootfs. Original images/tags are never modified.
FROM 127.0.0.1:5000/aero-bench/ns3@sha256:95cce3de770019767b7a62352002a4c9cf36c4dcde934353958fe559503461f8 AS build
USER 0:0
LABEL aeroagentsim.job="p4"
COPY native/ /opt/ns-3.48/scratch/
COPY bundle.py /opt/aeroagentsim-bundle.py
WORKDIR /opt/ns-3.48
RUN cmake --build cmake-cache --target scratch_aero-ns3-provider -j8 \
    && python3 /opt/aeroagentsim-bundle.py

FROM scratch
COPY --from=build /out/ /
COPY service/ /opt/aeroagentsim/service/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    LD_LIBRARY_PATH=/opt/aeroagentsim/lib AAS_PORT=9000 LANG=C.UTF-8
WORKDIR /opt/aeroagentsim
USER 65532:65532
LABEL aeroagentsim.backend="ns3" aeroagentsim.protocol="aeroagentsim.ns3/v1" \
    aeroagentsim.job="p4" org.opencontainers.image.base.name="ubuntu:24.04"
EXPOSE 9000
ENTRYPOINT ["/usr/bin/python3", "-m", "service.rpc"]
