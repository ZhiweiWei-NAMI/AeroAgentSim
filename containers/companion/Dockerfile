# syntax=docker/dockerfile:1
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:$PATH HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    DETECTOR_MODEL_PATH=/models/rtdetrv2_r18vd_120e_coco.onnx \
    EMBEDDING_MODEL_PATH=/models/bge-small-zh-v1.5
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv
WORKDIR /opt/aeroagentsim
COPY containers/companion/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
# A named local build context supplies prepared inference assets, never the
# training/evaluation cache. The resulting image needs no model bind mounts.
COPY --from=companion_models --chmod=0444 /rtdetrv2_r18vd_120e_coco.onnx /models/rtdetrv2_r18vd_120e_coco.onnx
COPY --from=companion_models --chmod=0444 \
    /bge-small-zh-v1.5/model_optimized.onnx \
    /bge-small-zh-v1.5/config.json \
    /bge-small-zh-v1.5/ort_config.json \
    /bge-small-zh-v1.5/tokenizer.json \
    /bge-small-zh-v1.5/tokenizer_config.json \
    /bge-small-zh-v1.5/special_tokens_map.json \
    /bge-small-zh-v1.5/vocab.txt \
    /bge-small-zh-v1.5/README.md /models/bge-small-zh-v1.5/
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
# Load actual weights as the runtime user during build, without downloads. This
# checks packaging, not person-detection accuracy or native simulation.
RUN --network=none python -c "import os; from aeroagentsim.companion.vision import RtdetrDetector; from aeroagentsim.companion.history import LocalBGE; RtdetrDetector(os.environ['DETECTOR_MODEL_PATH']); print('embedded BGE shape:', LocalBGE(os.environ['EMBEDDING_MODEL_PATH'])(['屋顶救援']).shape)"
ENTRYPOINT ["python", "-m", "aeroagentsim.companion.runtime"]
CMD ["--role", "uav"]
