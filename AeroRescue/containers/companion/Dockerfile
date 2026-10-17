# syntax=docker/dockerfile:1
# Official Node image builds the lockfile-pinned Cordis/DSH dependency tree.
# Host node_modules, SDK probes and credentials are never image inputs.
FROM node:24-bookworm-slim AS cordis-deps
WORKDIR /opt/cordis-deps
COPY AeroRescue/harness/package.json AeroRescue/harness/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:$PATH HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    DETECTOR_MODEL_PATH=/models/rtdetrv2_r18vd_120e_coco.onnx \
    EMBEDDING_MODEL_PATH=/models/bge-small-zh-v1.5 \
    ORT_DISABLE_TELEMETRY=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv ca-certificates libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv
COPY --from=cordis-deps /usr/local/bin/node /usr/local/bin/node
RUN node --version
WORKDIR /opt/aeroagentsim
COPY AeroRescue/containers/companion/requirements.txt /tmp/requirements.txt
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
COPY AeroRescue/pyproject.toml AeroRescue/setup.py AeroRescue/MANIFEST.in AeroRescue/README.md /opt/aeroagentsim/AeroRescue/
COPY AeroRescue/src /opt/aeroagentsim/AeroRescue/src
COPY AeroRescue/skills /opt/aeroagentsim/AeroRescue/skills
COPY AeroRescue/scenarios /opt/aeroagentsim/AeroRescue/scenarios
COPY AeroRescue/harness/package.json AeroRescue/harness/package-lock.json \
    AeroRescue/harness/launch.mjs AeroRescue/harness/plugin.mjs \
    AeroRescue/harness/bridge.mjs AeroRescue/harness/skills.mjs \
    AeroRescue/harness/rescue.patch.yml AeroRescue/harness/model.patch.yml \
    /opt/aeroagentsim/AeroRescue/harness/
COPY --from=cordis-deps /opt/cordis-deps/node_modules /opt/aeroagentsim/AeroRescue/harness/node_modules
RUN pip install --no-cache-dir --no-deps ./aerokernel . ./AeroRescue \
    && mkdir -p /data /models /run/aas/u1 /run/aas/u2 /run/aas/u3 /run/aas/u4 /run/aas/centre \
    && chown -R 65532:65532 /data /run/aas
USER 65532:65532
ENV DATA_DIR=/data DSH_HOME=/data/dsh HOME=/data AERORESCUE_PYTHON=/opt/venv/bin/python \
    COMPANION_SOCKET=/run/aas-uav/agent.sock \
    AAS_SKILLS_DIR=/opt/aeroagentsim/AeroRescue/skills
# Load actual weights as the runtime user during build, without downloads. This
# checks packaging, not person-detection accuracy or native simulation.
RUN --network=none python -c "import os; from aerorescue.companion.vision import RtdetrDetector; from aerorescue.companion.history import LocalBGE; RtdetrDetector(os.environ['DETECTOR_MODEL_PATH']); print('embedded BGE shape:', LocalBGE(os.environ['EMBEDDING_MODEL_PATH'])(['屋顶救援']).shape)"
# launch.mjs owns DSH and starts the local Python worker with --execution-only.
# Compose passes per-identity run parameters; coordinator overrides this entrypoint.
ENTRYPOINT ["node", "/opt/aeroagentsim/AeroRescue/harness/launch.mjs"]
CMD ["--role", "uav"]
