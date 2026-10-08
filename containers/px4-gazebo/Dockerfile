# Source build adapted from AeroBench containers/px4-gazebo/Dockerfile.
# See README.md and docs/platform/containers.md for provenance and all hashes.
FROM px4io/px4-sitl-gazebo@sha256:6f687ce0dad52b648c6f882ebaee93a03254001a8857b761312bafb689f7803e AS mavsdk-builder
COPY apt-install.sh /usr/local/bin/aas-apt-install

ARG MAVSDK_VERSION=3.17.2
ARG MAVSDK_COMMIT=9e3ca17faa84aa868caea10a3bbdab7e53810ced
ARG MAVSDK_SOURCE_SHA256=709de37d225f6374b3ebe65af8e34921759b7a8c2bbe611a581fd614126293c0
ARG MAVLINK_COMMIT=d6a7eeaf43319ce6da19a1973ca40180a4210643
ARG MAVLINK_SOURCE_SHA256=1e82b295bfd4fb9c3ce4d7f503e445c2050ea6d254dfd8addf56b7f2fa8b984d
ARG PYMAVLINK_COMMIT=5977f44504f01715a44bed44a4601e6c12cef92d
ARG PYMAVLINK_SOURCE_SHA256=e0949dd5858ecce28c52f5b30a7957f2236482417798867f5e961d7a2abd1fc8
ARG MAVSDK_PATCH_SHA256=972bef4a0d23a72a1fa46c3d9fa808e4f2f73467da32692b1c5b5d681e3bfed5
ARG MAVLINK_OFFLINE_PATCH_SHA256=777a0f83ab88ab9eff71a51daeddc7e88efcf58c23b1c73c5477c788ce82734a
ARG PYMAVLINK_BUILD_LOCK_SHA256=4c51de8177452454bc62e795ee802c521e442b1b2a8429dc01cde45675546795
ARG LIBEVENTS_COMMIT=7c1720749dfe555ec2e71d5f9f753e6ac1244e1c
ARG LIBEVENTS_SOURCE_SHA256=03a940cf47863e9c943cb1466bd26cc225ee3549c2eb8bed5978a9d0ea488191
ARG PICOSHA2_COMMIT=1bf940d8a03bb752604fbb366d47b97b50b9e6ce
ARG PICOSHA2_SOURCE_SHA256=b99ca6df2c596b68c4587a56007aa2305b715f379729a665ebeb18ec1b99f28f
ARG TINYXML2_VERSION=11.0.0
ARG TINYXML2_SOURCE_SHA256=5556deb5081fb246ee92afae73efd943c889cef0cafea92b0b82422d6a18f289
ARG LIBMAVLIKE_COMMIT=90498b14262137ae10b633705810e81bdb85de9c
ARG LIBMAVLIKE_SOURCE_SHA256=7ca84d49104b615b00f9cb4af12241438d03581c6b0c2478197fa9b3831cdbd7
ARG JSONCPP_VERSION=1.9.6
ARG JSONCPP_SOURCE_SHA256=f93b6dd7ce796b13d02c108bc9f79812245a82e577581c4c9aabe57075c90ea2
ARG LIBLZMA_VERSION=5.4.5
ARG LIBLZMA_SOURCE_SHA256=135c90b934aee8fbc0d467de87a05cb70d627da36abe518c357a873709e5b7d6
ARG OPENSSL_VERSION=3.6.0
ARG OPENSSL_SOURCE_SHA256=b6a5f44b7eb69e3fa35dbf15524405b44837a481d43d81daddde3ff21fcbb8e9
ARG CARES_VERSION=1.34.5
ARG CARES_SOURCE_SHA256=dcd919635f01b7c8c9c2f5fb38063cd86500f7c6d4d32ecf4deff5e3497fb157
ARG ABSL_COMMIT=cd75cb4ae32c46c84cef9a9c78b42174f22ed0ac
ARG ABSL_SOURCE_SHA256=f17bec838a89633561fdfadbdc89efaca36a6bac04999290adb7c13559dfd504
ARG PROTOBUF_VERSION=29.1
ARG PROTOBUF_SOURCE_SHA256=3d32940e975c4ad9b8ba69640e78f5527075bae33ca2890275bf26b853c0962c
ARG RE2_COMMIT=b84e3ff189980a33d4a0c6fa1201aa0b3b8bab4a
ARG RE2_SOURCE_SHA256=d59276d828823b73fd567d7f238d11a47fdab6e5542fbcf98e754e9319925141
ARG GRPC_VERSION=1.68.2
ARG GRPC_SOURCE_SHA256=afbc5d78d6ba6d509cc6e264de0d49dcd7304db435cbf2d630385bacf49e066c

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
    && echo "${MAVSDK_SOURCE_SHA256}  /opt/sources/mavsdk.tar.gz" \
      | sha256sum --check --strict \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/mavlink.tar.gz \
      "https://codeload.github.com/mavlink/mavlink/tar.gz/${MAVLINK_COMMIT}" \
    && echo "${MAVLINK_SOURCE_SHA256}  /opt/sources/mavlink.tar.gz" \
      | sha256sum --check --strict \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/pymavlink.tar.gz \
      "https://codeload.github.com/ArduPilot/pymavlink/tar.gz/${PYMAVLINK_COMMIT}" \
    && echo "${PYMAVLINK_SOURCE_SHA256}  /opt/sources/pymavlink.tar.gz" \
      | sha256sum --check --strict \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/libevents.tar.gz \
      "https://codeload.github.com/mavlink/libevents/tar.gz/${LIBEVENTS_COMMIT}" \
    && echo "${LIBEVENTS_SOURCE_SHA256}  /opt/sources/libevents.tar.gz" \
      | sha256sum --check --strict \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/picosha2.tar.gz \
      "https://codeload.github.com/julianoes/PicoSHA2/tar.gz/${PICOSHA2_COMMIT}" \
    && echo "${PICOSHA2_SOURCE_SHA256}  /opt/sources/picosha2.tar.gz" \
      | sha256sum --check --strict \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/tinyxml2.tar.gz \
      "https://codeload.github.com/leethomason/tinyxml2/tar.gz/${TINYXML2_VERSION}" \
    && echo "${TINYXML2_SOURCE_SHA256}  /opt/sources/tinyxml2.tar.gz" \
      | sha256sum --check --strict \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/libmavlike.tar.gz \
      "https://codeload.github.com/julianoes/libmavlike/tar.gz/${LIBMAVLIKE_COMMIT}" \
    && echo "${LIBMAVLIKE_SOURCE_SHA256}  /opt/sources/libmavlike.tar.gz" \
      | sha256sum --check --strict \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/jsoncpp.tar.gz \
      "https://codeload.github.com/open-source-parsers/jsoncpp/tar.gz/${JSONCPP_VERSION}" \
    && echo "${JSONCPP_SOURCE_SHA256}  /opt/sources/jsoncpp.tar.gz" \
      | sha256sum --check --strict

RUN wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/liblzma.tar.gz \
      "https://github.com/tukaani-project/xz/releases/download/v${LIBLZMA_VERSION}/xz-${LIBLZMA_VERSION}.tar.gz" \
    && echo "${LIBLZMA_SOURCE_SHA256}  /opt/sources/liblzma.tar.gz" \
      | sha256sum --check --strict \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/openssl.tar.gz \
      "https://github.com/openssl/openssl/releases/download/openssl-${OPENSSL_VERSION}/openssl-${OPENSSL_VERSION}.tar.gz" \
    && echo "${OPENSSL_SOURCE_SHA256}  /opt/sources/openssl.tar.gz" \
      | sha256sum --check --strict

RUN wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/cares.tar.gz \
      "https://codeload.github.com/c-ares/c-ares/tar.gz/v${CARES_VERSION}" \
    && echo "${CARES_SOURCE_SHA256}  /opt/sources/cares.tar.gz" \
      | sha256sum --check --strict \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/absl.tar.gz \
      "https://codeload.github.com/abseil/abseil-cpp/tar.gz/${ABSL_COMMIT}" \
    && echo "${ABSL_SOURCE_SHA256}  /opt/sources/absl.tar.gz" \
      | sha256sum --check --strict \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/protobuf.tar.gz \
      "https://github.com/protocolbuffers/protobuf/releases/download/v${PROTOBUF_VERSION}/protobuf-${PROTOBUF_VERSION}.tar.gz" \
    && echo "${PROTOBUF_SOURCE_SHA256}  /opt/sources/protobuf.tar.gz" \
      | sha256sum --check --strict \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/re2.tar.gz \
      "https://codeload.github.com/google/re2/tar.gz/${RE2_COMMIT}" \
    && echo "${RE2_SOURCE_SHA256}  /opt/sources/re2.tar.gz" \
      | sha256sum --check --strict \
    && wget --no-verbose --tries=8 --timeout=60 \
      --output-document=/opt/sources/grpc.tar.gz \
      "https://codeload.github.com/grpc/grpc/tar.gz/v${GRPC_VERSION}" \
    && echo "${GRPC_SOURCE_SHA256}  /opt/sources/grpc.tar.gz" \
      | sha256sum --check --strict

COPY mavsdk-incoming-heartbeat-timeout.patch /opt/sources/mavsdk-incoming-heartbeat-timeout.patch
COPY mavlink-offline-python.patch /opt/sources/mavlink-offline-python.patch
COPY pymavlink-build-requirements.lock /opt/sources/pymavlink-build-requirements.lock

RUN echo "${MAVSDK_PATCH_SHA256}  /opt/sources/mavsdk-incoming-heartbeat-timeout.patch" \
      | sha256sum --check --strict \
    && echo "${MAVLINK_OFFLINE_PATCH_SHA256}  /opt/sources/mavlink-offline-python.patch" \
      | sha256sum --check --strict \
    && echo "${PYMAVLINK_BUILD_LOCK_SHA256}  /opt/sources/pymavlink-build-requirements.lock" \
      | sha256sum --check --strict \
    && mkdir /opt/sources/pymavlink-wheels \
    && python3 -m pip download \
      --require-hashes \
      --only-binary=:all: \
      --dest /opt/sources/pymavlink-wheels \
      --requirement /opt/sources/pymavlink-build-requirements.lock

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

RUN mkdir -p /opt/aeroagentsim/build-inputs \
    && sha256sum /opt/sources/pymavlink-wheels/* \
      > /opt/aeroagentsim/build-inputs/pymavlink-wheels.sha256 \
    && sha256sum /opt/sources/*.tar.gz /opt/sources/*.patch /opt/sources/*.lock \
      > /opt/aeroagentsim/build-inputs/mavsdk-sources.sha256

FROM px4io/px4-sitl-gazebo@sha256:6f687ce0dad52b648c6f882ebaee93a03254001a8857b761312bafb689f7803e AS runtime
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

COPY requirements.lock /opt/aeroagentsim/build-inputs/requirements.lock
COPY --from=mavsdk-builder /opt/mavsdk-install/bin/mavsdk_server /opt/mavsdk/mavsdk_server
COPY --from=mavsdk-builder /opt/aeroagentsim/build-inputs/ /opt/aeroagentsim/build-inputs/mavsdk-builder/
COPY patch_camera_model.py /opt/aeroagentsim/build-inputs/patch_camera_model.py
RUN python3 /opt/aeroagentsim/build-inputs/patch_camera_model.py \
      /opt/px4-gazebo/share/gz/models/mono_cam/model.sdf \
    && echo "2d8b948585fc615cb0f34ed9d67885c15a9bac16df2d1fc59ebb03325c0c26cf  /opt/px4-gazebo/share/gz/models/mono_cam/model.sdf" \
      | sha256sum --check --strict \
    && rm /opt/aeroagentsim/build-inputs/patch_camera_model.py

COPY inject_contact_sensors.py /opt/aeroagentsim/build-inputs/inject_contact_sensors.py
RUN python3 /opt/aeroagentsim/build-inputs/inject_contact_sensors.py \
      /opt/px4-gazebo/share/gz/models/x500_base/model.sdf \
    && echo "7456e4b43a1894a727bdf544250205780dc2e74460d9cfa424c65edb3fb4e898  /opt/px4-gazebo/share/gz/models/x500_base/model.sdf" \
      | sha256sum --check --strict \
    && rm /opt/aeroagentsim/build-inputs/inject_contact_sensors.py

RUN find /etc/apt/sources.list.d -type f -exec sed -i "s|http://|https://|g" {} + \
    && apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update \
    && sh /usr/local/bin/aas-apt-install runtime \
      ca-certificates \
      python3-venv \
    && python3 -m venv /opt/aero-python \
    && /opt/aero-python/bin/pip download --no-cache-dir --require-hashes --only-binary=:all: \
      --dest /tmp/aas-wheels -r /opt/aeroagentsim/build-inputs/requirements.lock \
    && sha256sum /tmp/aas-wheels/* > /opt/aeroagentsim/build-inputs/python-wheels.sha256 \
    && /opt/aero-python/bin/pip install --no-cache-dir --no-index --find-links=/tmp/aas-wheels \
      --require-hashes -r /opt/aeroagentsim/build-inputs/requirements.lock \
    && rm -rf /tmp/aas-wheels \
    && sha256sum /opt/mavsdk/mavsdk_server > /opt/aeroagentsim/build-inputs/native-binaries.sha256 \
    && chmod 0755 /opt/mavsdk/mavsdk_server \
    && ln -sf /opt/mavsdk/mavsdk_server /opt/mavsdk/mavsdk-server \
    && /opt/mavsdk/mavsdk_server --help \
      | grep --fixed-strings --quiet -- "--incoming-heartbeat-timeout-s" \
    && /opt/mavsdk/mavsdk_server --help \
      | grep --fixed-strings --quiet -- "--command-audit-journal-path" \
    && python3 -c "from hashlib import sha256; from pathlib import Path; px4=Path('/opt/px4-gazebo/bin/px4').read_bytes(); gz=Path('/usr/bin/gz').read_bytes(); assert sha256(px4).hexdigest()=='b6d41b7e7d8be65017a1dc4cfe12502382e8595d2408c4c27d28c64a7cff66f6'; assert sha256(gz).hexdigest()=='6e9cccdef9f9f266f45b76559c0974b1970e4348e82feb7a51964b4854cdd22a'; assert b'v1.17.0-alpha1-1551-g381149fb01' in px4; assert b'381149fb012762f5e38c4a7fdc1b905b28038970' in px4" \
    && apt-get purge --yes python3-venv \
    && apt-get autoremove --yes \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=65532:65532 service /opt/aeroagentsim/service
WORKDIR /opt/aeroagentsim
ENV AAS_PORT=9000
USER 65532:65532
LABEL aeroagentsim.job="p9" aeroagentsim.backend="px4-gazebo" \
      org.opencontainers.image.base.name="px4io/px4-sitl-gazebo@sha256:6f687ce0dad52b648c6f882ebaee93a03254001a8857b761312bafb689f7803e"
EXPOSE 9000/tcp
HEALTHCHECK NONE
ENTRYPOINT ["python3", "-m", "service"]
CMD []
