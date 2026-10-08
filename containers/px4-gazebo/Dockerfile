FROM localhost:5000/aero-bench/flight:p02-native-parcel-c829dabe0c7b378203444f280e9c2e64d7d143350c5c2b5be4ee3cbc5f326296
COPY --chown=65532:65532 service /opt/aeroagentsim/service
WORKDIR /opt/aeroagentsim
ENV AAS_PORT=9000
EXPOSE 9000/tcp
HEALTHCHECK NONE
ENTRYPOINT ["python3", "-m", "service"]
CMD []
