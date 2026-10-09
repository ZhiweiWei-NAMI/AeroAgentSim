# Public Python 3.12.11/bookworm base.
FROM python:3.12.11-bookworm
COPY apt-install.sh /usr/local/bin/aas-apt-install
RUN find /etc/apt/sources.list.d -type f -exec sed -i "s|http://|https://|g" {} + \
    && apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update \
    && sh /usr/local/bin/aas-apt-install runtime \
      libatomic1 libexpat1 libgl1 libx11-6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /opt/aeroagentsim/requirements.txt
RUN python -m pip install --no-cache-dir -r /opt/aeroagentsim/requirements.txt
LABEL aeroagentsim.job="release" aeroagentsim.backend="sumo"
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
