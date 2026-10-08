FROM localhost:5000/aero-bench/traffic:p02-native-parcel-afe62d73b36c126a83e67abd9d4abdba170bb83067f4fa77359d7f298aecba93
USER root
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
