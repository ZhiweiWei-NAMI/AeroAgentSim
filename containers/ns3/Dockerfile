# Public Ubuntu 24.04 base and SHA-verified ns-3 source, recovered from history.
FROM ubuntu:24.04@sha256:1e0a86e57d247923571b75e0aaf48a1449cf8c543d51fb3e07a4a7d7bfa79316 AS build
COPY apt-install.sh /usr/local/bin/aas-apt-install
LABEL aeroagentsim.job="p9"
# Bootstrap the public CA store over Ubuntu's signed APT metadata; all later
# downloads use HTTPS with verification enabled.
RUN apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update \
    && sh /usr/local/bin/aas-apt-install ca-bootstrap ca-certificates
RUN find /etc/apt/sources.list.d -type f -exec sed -i "s|http://|https://|g" {} + \
    && apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update \
    && sh /usr/local/bin/aas-apt-install builder \
      bzip2 ca-certificates cmake g++ make ninja-build python3 wget \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt
RUN wget --no-verbose --output-document ns-3.48.tar.bz2 \
      https://www.nsnam.org/releases/ns-3.48.tar.bz2 \
    && echo "5700ceecef2c9bc862502914b2237fe798d4c7ae07652247050e300e49407c4d  ns-3.48.tar.bz2" \
      | sha256sum --check --strict \
    && tar --extract --bzip2 --file ns-3.48.tar.bz2 \
    && rm ns-3.48.tar.bz2
COPY native/ /opt/ns-3.48/scratch/
COPY bundle.py /opt/aeroagentsim-bundle.py
WORKDIR /opt/ns-3.48
# ns3 refuses privileged execution; configure directly with CMake as its wrapper does.
RUN cmake -S . -B cmake-cache -G Ninja \
      -DCMAKE_BUILD_TYPE=release -DNS3_NATIVE_OPTIMIZATIONS=ON \
      -DNS3_ASSERT=OFF -DNS3_LOG=OFF \
      -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF \
      -DNS3_ENABLED_MODULES="core;network;internet;mobility;propagation;traffic-control;wifi" \
    && cmake --build cmake-cache --target scratch_aero-ns3-provider -j16 \
    && python3 /opt/aeroagentsim-bundle.py \
    && sha256sum scratch/aero-ns3-provider.cc scratch/scene.h /opt/aeroagentsim-bundle.py \
      > /opt/aeroagentsim/build-inputs/provider-sources.sha256 \
    && cp -a /opt/aeroagentsim/build-inputs /out/opt/aeroagentsim/

FROM scratch
COPY --from=build /out/ /
COPY service/ /opt/aeroagentsim/service/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    LD_LIBRARY_PATH=/opt/aeroagentsim/lib AAS_PORT=9000 LANG=C.UTF-8
WORKDIR /opt/aeroagentsim
USER 65532:65532
LABEL aeroagentsim.backend="ns3" aeroagentsim.protocol="aeroagentsim.ns3/v1" \
    aeroagentsim.job="p9" org.opencontainers.image.base.name="ubuntu:24.04"
EXPOSE 9000
ENTRYPOINT ["/usr/bin/python3", "-m", "service.rpc"]
