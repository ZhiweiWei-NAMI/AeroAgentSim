#!/bin/sh
# Build one complete, offline-capable companion image for all UAV identities.
set -eu
: "${MODEL_ROOT:?set MODEL_ROOT to the prepared model-runtime directory}"
models=$(CDPATH= cd -- "$MODEL_ROOT" && pwd)
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
for file in rtdetrv2_r18vd_120e_coco.onnx \
    bge-small-zh-v1.5/model_optimized.onnx bge-small-zh-v1.5/config.json \
    bge-small-zh-v1.5/ort_config.json bge-small-zh-v1.5/tokenizer.json \
    bge-small-zh-v1.5/tokenizer_config.json bge-small-zh-v1.5/special_tokens_map.json \
    bge-small-zh-v1.5/vocab.txt bge-small-zh-v1.5/README.md; do
    if [ ! -s "$models/$file" ]; then
        echo "Missing prepared inference asset: $models/$file" >&2
        exit 2
    fi
done
if ! command -v docker >/dev/null 2>&1; then
    echo "BLOCKED: Docker is unavailable; model assets were found, but no image was built." >&2
    exit 2
fi
exec docker buildx build --load --progress=plain \
    --build-context "companion_models=$models" \
    -f "$root/containers/companion/Dockerfile" \
    -t "${1:-${COMPANION_IMAGE:-aeroagentsim/companion:research}}" "$root"
