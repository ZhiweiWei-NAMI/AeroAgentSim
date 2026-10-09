# Source build adapted from AeroBench containers/px4-gazebo/Dockerfile.
# See README.md for provenance.
FROM px4io/px4-sitl-gazebo AS mavsdk-builder
COPY apt-install.sh /usr/local/bin/aas-apt-install

ARG MAVSDK_VERSION=3.17.2
ARG MAVSDK_COMMIT=9e3ca17faa84aa868caea10a3bbdab7e53810ced
ARG MAVLINK_COMMIT=d6a7eeaf43319ce6da19a1973ca40180a4210643
ARG PYMAVLINK_COMMIT=5977f44504f01715a44bed44a4601e6c12cef92d
ARG LIBEVENTS_COMMIT=7c1720749dfe555ec2e71d5f9f753e6ac1244e1c
ARG PICOSHA2_COMMIT=1bf940d8a03bb752604fbb366d47b97b50b9e6ce
ARG TINYXML2_VERSION=11.0.0
ARG LIBMAVLIKE_COMMIT=90498b14262137ae10b633705810e81bdb85de9c
ARG JSONCPP_VERSION=1.9.6
ARG LIBLZMA_VERSION=5.4.5
ARG OPENSSL_VERSION=3.6.0
ARG CARES_VERSION=1.34.5
ARG ABSL_COMMIT=cd75cb4ae32c46c84cef9a9c78b42174f22ed0ac
ARG PROTOBUF_VERSION=29.1
ARG RE2_COMMIT=b84e3ff189980a33d4a0c6fa1201aa0b3b8bab4a
ARG GRPC_VERSION=1.68.2

RUN find /etc/apt/sources.list.d -type f -exec sed -i "s|http://|https://|g" {} + \
    && apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update \
    && sh /usr/local/bin/aas-apt-install builder \
      build-essential \
      ca-certificates \
      cmake \
      git \
      patch \
      python3 \
      python3-pip \
      wget

RUN mkdir -p /opt/sources /work \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/mavsdk.tar.gz \
      "https://codeload.github.com/mavlink/MAVSDK/tar.gz/${MAVSDK_COMMIT}" \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/mavlink.tar.gz \
      "https://codeload.github.com/mavlink/mavlink/tar.gz/${MAVLINK_COMMIT}" \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/pymavlink.tar.gz \
      "https://codeload.github.com/ArduPilot/pymavlink/tar.gz/${PYMAVLINK_COMMIT}" \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/libevents.tar.gz \
      "https://codeload.github.com/mavlink/libevents/tar.gz/${LIBEVENTS_COMMIT}" \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/picosha2.tar.gz \
      "https://codeload.github.com/julianoes/PicoSHA2/tar.gz/${PICOSHA2_COMMIT}" \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/tinyxml2.tar.gz \
      "https://codeload.github.com/leethomason/tinyxml2/tar.gz/${TINYXML2_VERSION}" \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/libmavlike.tar.gz \
      "https://codeload.github.com/julianoes/libmavlike/tar.gz/${LIBMAVLIKE_COMMIT}" \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/jsoncpp.tar.gz \
      "https://codeload.github.com/open-source-parsers/jsoncpp/tar.gz/${JSONCPP_VERSION}"

RUN wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/liblzma.tar.gz \
      "https://github.com/tukaani-project/xz/releases/download/v${LIBLZMA_VERSION}/xz-${LIBLZMA_VERSION}.tar.gz" \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/openssl.tar.gz \
      "https://github.com/openssl/openssl/releases/download/openssl-${OPENSSL_VERSION}/openssl-${OPENSSL_VERSION}.tar.gz"

RUN wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/cares.tar.gz \
      "https://codeload.github.com/c-ares/c-ares/tar.gz/v${CARES_VERSION}" \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/absl.tar.gz \
      "https://codeload.github.com/abseil/abseil-cpp/tar.gz/${ABSL_COMMIT}" \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/protobuf.tar.gz \
      "https://github.com/protocolbuffers/protobuf/releases/download/v${PROTOBUF_VERSION}/protobuf-${PROTOBUF_VERSION}.tar.gz" \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/re2.tar.gz \
      "https://codeload.github.com/google/re2/tar.gz/${RE2_COMMIT}" \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/grpc.tar.gz \
      "https://codeload.github.com/grpc/grpc/tar.gz/v${GRPC_VERSION}"

COPY mavsdk-incoming-heartbeat-timeout.patch /opt/sources/mavsdk-incoming-heartbeat-timeout.patch
COPY mavlink-offline-python.patch /opt/sources/mavlink-offline-python.patch
COPY pymavlink-build-requirements.txt /opt/sources/pymavlink-build-requirements.txt

RUN mkdir /opt/sources/pymavlink-wheels \
    && python3 -m pip download \
      --only-binary=:all: \
      --dest /opt/sources/pymavlink-wheels \
      --requirement /opt/sources/pymavlink-build-requirements.txt

RUN --network=none mkdir /work/MAVSDK /opt/sources/mavlink \
    && tar --extract --gzip --file=/opt/sources/mavsdk.tar.gz \
      --strip-components=1 --directory=/work/MAVSDK \
    && tar --extract --gzip --file=/opt/sources/mavlink.tar.gz \
      --strip-components=1 --directory=/opt/sources/mavlink \
    && rm -rf /opt/sources/mavlink/pymavlink \
    && mkdir /opt/sources/mavlink/pymavlink \
    && tar --extract --gzip --file=/opt/sources/pymavlink.tar.gz \
      --strip-components=1 --directory=/opt/sources/mavlink/pymavlink \
    && patch --directory=/opt/sources/mavlink --strip=1 \
      --input=/opt/sources/mavlink-offline-python.patch \
    && patch --directory=/work/MAVSDK --strip=1 \
      --input=/opt/sources/mavsdk-incoming-heartbeat-timeout.patch \
    && grep --fixed-strings --quiet -- "configure_command_audit_journal_path" \
      /work/MAVSDK/src/mavsdk/core/mavlink_command_sender.h \
    && grep --fixed-strings --quiet -- "--command-audit-journal-path" \
      /work/MAVSDK/src/mavsdk_server/src/mavsdk_server_bin.cpp \
    && grep --fixed-strings --quiet -- "O_EXCL" \
      /work/MAVSDK/src/mavsdk/core/mavlink_command_sender.cpp \
    && grep --fixed-strings --quiet -- "std::locale::classic()" \
      /work/MAVSDK/src/mavsdk/core/mavlink_command_sender.cpp \
    && grep --fixed-strings --quiet -- "command_ack_ingress" \
      /work/MAVSDK/src/mavsdk/core/mavlink_command_sender.cpp \
    && grep --fixed-strings --quiet -- "unmatched_no_outstanding_command" \
      /work/MAVSDK/src/mavsdk/core/mavlink_command_sender.cpp \
    && grep --fixed-strings --quiet -- \
      "audit_command_transport_accept(message, successful_emissions)" \
      /work/MAVSDK/src/mavsdk/core/mavsdk_impl.cpp \
    && cmake -S /work/MAVSDK -B /work/build \
      -DCMAKE_BUILD_TYPE=Release \
      -DMAVSDK_SOURCE_VERSION="v${MAVSDK_VERSION}" \
      -DCMAKE_INSTALL_PREFIX=/opt/mavsdk-install \
      -DBUILD_MAVSDK_SERVER=ON \
      -DBUILD_SHARED_LIBS=OFF \
      -DBUILD_TESTING=OFF \
      -DBUILD_WITHOUT_CURL=ON \
      -DENABLE_CPPTRACE=OFF \
      -DMAVLINK_HASH="${MAVLINK_COMMIT}" \
      -DMAVLINK_SOURCE_DIR=/opt/sources/mavlink \
    && cmake --build /work/build --parallel 16 --target install \
    && strip --strip-unneeded /opt/mavsdk-install/bin/mavsdk_server \
    && /opt/mavsdk-install/bin/mavsdk_server --help \
      | grep --fixed-strings --quiet -- "--incoming-heartbeat-timeout-s" \
    && /opt/mavsdk-install/bin/mavsdk_server --help \
      | grep --fixed-strings --quiet -- "--command-audit-journal-path"


FROM px4io/px4-sitl-gazebo AS runtime
COPY apt-install.sh /usr/local/bin/aas-apt-install

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/tmp \
    TMPDIR=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/aero-python/bin:/opt/px4-gazebo/bin:/opt/mavsdk:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    PX4_DATA_DIR=/opt/px4-gazebo/etc \
    PX4_GZ_MODELS=/opt/px4-gazebo/share/gz/models \
    PX4_GZ_WORLDS=/opt/px4-gazebo/share/gz/worlds

COPY requirements.txt /opt/aeroagentsim/build-inputs/requirements.txt
COPY --from=mavsdk-builder /opt/mavsdk-install/bin/mavsdk_server /opt/mavsdk/mavsdk_server
COPY --from=mavsdk-builder /opt/aeroagentsim/build-inputs/ /opt/aeroagentsim/build-inputs/mavsdk-builder/
COPY patch_camera_model.py /opt/aeroagentsim/build-inputs/patch_camera_model.py
RUN python3 /opt/aeroagentsim/build-inputs/patch_camera_model.py \
      /opt/px4-gazebo/share/gz/models/mono_cam/model.sdf \
    && rm /opt/aeroagentsim/build-inputs/patch_camera_model.py

COPY inject_contact_sensors.py /opt/aeroagentsim/build-inputs/inject_contact_sensors.py
RUN python3 /opt/aeroagentsim/build-inputs/inject_contact_sensors.py \
      /opt/px4-gazebo/share/gz/models/x500_base/model.sdf \
    && rm /opt/aeroagentsim/build-inputs/inject_contact_sensors.py

RUN find /etc/apt/sources.list.d -type f -exec sed -i "s|http://|https://|g" {} + \
    && apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update \
    && sh /usr/local/bin/aas-apt-install runtime \
      ca-certificates \
      python3-venv \
    && python3 -m venv /opt/aero-python \
    && /opt/aero-python/bin/pip install --no-cache-dir -r /opt/aeroagentsim/build-inputs/requirements.txt \
    && rm -rf /tmp/aas-wheels \
    && chmod 0755 /opt/mavsdk/mavsdk_server \
    && ln -sf /opt/mavsdk/mavsdk_server /opt/mavsdk/mavsdk-server \
    && /opt/mavsdk/mavsdk_server --help \
      | grep --fixed-strings --quiet -- "--incoming-heartbeat-timeout-s" \
    && /opt/mavsdk/mavsdk_server --help \
      | grep --fixed-strings --quiet -- "--command-audit-journal-path" \
    && python3 -c "from pathlib import Path; px4=Path('/opt/px4-gazebo/bin/px4').read_bytes(); assert b'v1.17.0-alpha1-1551-g381149fb01' in px4; assert b'381149fb012762f5e38c4a7fdc1b905b28038970' in px4" \
    && apt-get purge --yes python3-venv \
    && apt-get autoremove --yes \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=65532:65532 service /opt/aeroagentsim/service
WORKDIR /opt/aeroagentsim
ENV AAS_PORT=9000
USER 65532:65532
LABEL aeroagentsim.job="p9" aeroagentsim.backend="px4-gazebo" \
      org.opencontainers.image.base.name="px4io/px4-sitl-gazebo"
EXPOSE 9000/tcp
HEALTHCHECK NONE
ENTRYPOINT ["python3", "-m", "service"]
CMD []
