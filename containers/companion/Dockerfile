FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:$PATH HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv
WORKDIR /opt/aeroagentsim
COPY containers/companion/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY aerokernel /opt/aeroagentsim/aerokernel
COPY pyproject.toml README.md /opt/aeroagentsim/
COPY src /opt/aeroagentsim/src
COPY skills/rescue /opt/aeroagentsim/skills/rescue
# Only public configuration and the native kernel template are available onboard.
COPY scenarios/adapters/px4-flight.yaml scenarios/adapters/authored-registry.json /opt/aeroagentsim/scenarios/adapters/
COPY scenarios/rescue/rooftop-flood-companion.yaml /opt/aeroagentsim/scenarios/rescue/
RUN pip install --no-cache-dir --no-deps ./aerokernel . \
    && mkdir -p /data /models /run/aas/u1 /run/aas/u2 /run/aas/u3 /run/aas/u4 /run/aas/centre \
    && chown -R 65532:65532 /data /run/aas
USER 65532:65532
ENV DATA_DIR=/data COMPANION_SOCKET=/run/aas-uav/agent.sock \
    AAS_SKILLS_DIR=/opt/aeroagentsim/skills/rescue
ENTRYPOINT ["python", "-m", "aeroagentsim.companion.runtime"]
CMD ["--role", "uav"]
