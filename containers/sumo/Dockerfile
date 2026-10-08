# Public Python 3.12.11/bookworm base recovered from the dev image history.
FROM python@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49
COPY apt-install.sh /usr/local/bin/aas-apt-install
RUN find /etc/apt/sources.list.d -type f -exec sed -i "s|http://|https://|g" {} + \
    && apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update \
    && sh /usr/local/bin/aas-apt-install runtime \
      libatomic1 libexpat1 libgl1 libx11-6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /opt/aeroagentsim/requirements.lock
RUN python -m pip download --no-cache-dir --require-hashes --only-binary=:all: \
      --dest /tmp/aas-wheels -r /opt/aeroagentsim/requirements.lock \
    && sha256sum /tmp/aas-wheels/* > /opt/aeroagentsim/build-inputs/python-wheels.sha256 \
    && python -m pip install --no-cache-dir --no-index --find-links=/tmp/aas-wheels --require-hashes \
      -r /opt/aeroagentsim/requirements.lock \
    && rm -rf /tmp/aas-wheels
LABEL aeroagentsim.job="p9" aeroagentsim.backend="sumo" \
      org.opencontainers.image.base.name="python@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49"
RUN mkdir -p /opt/aeroagentsim && netgenerate --grid --grid.number 3 \
    --grid.length 200 --default.lanenumber 2 --default.speed 13.89 \
    --tls.guess --tls.guess.threshold 0 --no-turnarounds \
    --walkingareas --sidewalks.guess -o /opt/aeroagentsim/grid.net.xml
COPY service /opt/aeroagentsim/service
RUN chmod -R a+rX /opt/aeroagentsim
USER 65532:65532
WORKDIR /opt/aeroagentsim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AAS_PORT=9000
EXPOSE 9000
HEALTHCHECK NONE
ENTRYPOINT ["python", "-m", "service"]
CMD []
