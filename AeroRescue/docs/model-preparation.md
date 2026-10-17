# Local models and real-image preparation

The companion runtime uses CPU ONNX Runtime and FastEmbed. PyTorch is used only
in a separate preparation environment, and no weights are downloaded when a
companion starts. `containers/companion/requirements.txt` records the runtime
environment actually installed on Python 3.12. The export requirements are a
separate file and are not installed in the companion Dockerfile.

## Prepare models

```bash
python -m venv .venv
.venv/bin/python -m pip install -r containers/companion/requirements.txt
.venv/bin/python -m pip install -e ./aerokernel -e '.[server,agents]'
export COMPANION_ASSET_ROOT="$PWD/data/companion-assets"
python -m venv "$COMPANION_ASSET_ROOT/export-venv"
"$COMPANION_ASSET_ROOT/export-venv/bin/python" -m pip install \
  -r containers/companion/requirements-export.txt
.venv/bin/python tools/prepare_models.py \
  --cache "$COMPANION_ASSET_ROOT" \
  --export-python "$COMPANION_ASSET_ROOT/export-venv/bin/python" \
  --lock "$COMPANION_ASSET_ROOT/model-lock.json"
```

The preparation script uses the official RT-DETR repository and checkpoint,
with an explicit `-r` argument to the official exporter. Disabling the separate
ImageNet backbone download does not disable loading the trained full checkpoint.
The actual loaded state is the checkpoint's EMA module. Runtime and source
revisions are recorded for reproduction in `deploy/model-lock.json`.

### Detector input contract

- RGB, PIL bilinear resize to 640×640, float32 divided by 255, NCHW
- No mean/std normalization and no letterbox
- `images`: float32 `[N,3,640,640]`
- `orig_target_sizes`: int64 `[N,2]`, ordered **width, height**
- Outputs: `labels`, `boxes`, `scores`; boxes are original-image xyxy pixels
- Deploy labels are contiguous COCO labels 0–79. Person is **0**, not COCO's
  original category ID 1. Official deploy returns before category remapping.

FastEmbed's local directory is `bge-small-zh-v1.5`, containing
`model_optimized.onnx` and its tokenizer/configuration files. Use
`specific_model_path`, `local_files_only=True`, and `CPUExecutionProvider`.
The prepared model actually embedded Chinese text into 512-dimensional vectors.
Preparation separates public model downloading from local inference. The latter
runs in a child process with native networking denied before importing ORT. The
two Chinese inputs actually returned 512-dimensional vectors under that guard.
This is a model check, not a container test.

The preparation script creates `model-runtime/` with only the ONNX detector and
BGE assets. Set `MODEL_ROOT=$COMPANION_ASSET_ROOT/model-runtime` for the read-only
`/models` mount. Never mount the whole preparation directory into a companion:
it also contains training/export files and evaluation-only annotations.
After restoring the model archive, recreate this mount without downloading:

```bash
.venv/bin/python tools/prepare_models.py \
  --cache "$COMPANION_ASSET_ROOT" --runtime-view-only
```

## Prepare real assets and evaluation images

```bash
.venv/bin/python tools/prepare_assets.py \
  --cache "$COMPANION_ASSET_ROOT" \
  --lock "$COMPANION_ASSET_ROOT/assets.lock.json" \
  --seadronessee-count 200
```

This downloads the MakeHuman core CC0 body mesh and converts it into a 1.75 m,
grounded Z-up OBJ/SDF visual model. Helper meshes are removed and solid clothing
materials are assigned. It is a real body surface mesh, but its Gazebo rendering
and detector recognition remain unverified; it is not a dynamic human actor.
The generated asset root can be added to `GZ_SIM_RESOURCE_PATH` before startup.

The script obtains a bounded official SeaDronesSee Object Detection v2 validation
subset through the author's public read-only share. It uses the official JPEG
version, preserves original PNG IDs, and freezes the selection before inference.
The actual 200-image subset contains 67 no-swimmer images and 156 negative or
confuser images. No training images were downloaded. The author README states
CC0; the COCO JSON's generic `Unknown` license field is retained unchanged.

Only the independent evaluator reads annotations. Companion detector inputs are
RGB files only. Do not mount annotation or evaluator directories into UAVs.
The official validation split is retained. This work did not train or tune on
it; a separate video-disjoint training/development split has not been established.

## Actual inference and export comparison

Ten distinct real images were compared using strictly loaded PyTorch weights and
the exported ONNX, retaining all top-300 outputs from both engines. At score
≥0.3, same-class matched detections had a maximum box difference of 0.000244 px
and score difference of 0.000002444. All ten had equal class counts and passed
the declared 0.1 px / 0.0001 comparison tolerances. A low-confidence rank tie in
one frame changed raw ordering; the raw arrays retain it. ORT CPU took roughly
0.34–0.41 seconds per image in this preparation environment.

```bash
"$COMPANION_ASSET_ROOT/export-venv/bin/python" tools/prepare_models.py \
  --cache "$COMPANION_ASSET_ROOT" --compare-images /path/to/ten-real-images \
  --output runs/acceptance/model-parity-a1
.venv/bin/python tools/prepare_models.py \
  --cache "$COMPANION_ASSET_ROOT" \
  --score-dataset "$COMPANION_ASSET_ROOT/seadronessee-odv2/subset-200-annotations.json" \
  --output runs/acceptance/sds200-inference-a1
```

Scoring uses the same runtime `RtdetrDetector` at its fixed confidence 0.5 and
independent IoU≥0.5 matching. COCO person maps to swimmer. Other dataset classes
are excluded from positive ground truth, but person predictions on boats or
buoys still count as false positives. Only explicit category-0 ignore regions
can suppress a prediction. All input IDs, raw detections, failures and per-image
TP/FP/FN are retained. Outputs refuse to overwrite an earlier run.

ORT telemetry is explicitly disabled before sessions. Standalone export,
equivalence and scoring additionally install a process-local Linux seccomp
restriction before importing ORT, denying native socket/network system calls.
This requires the host's `libseccomp.so.2` and fails closed when unavailable.
It does not change the host network or prevent other processes from working.
The first 200-image attempt was interrupted after 25 image outputs when default
ORT telemetry was flagged by execution review. Those files are preserved; a
fresh attempt runs with telemetry disabled and native networking denied. The
initial Python-only offline check did not cover native C++ networking.

The completed offline `sds200-inference-a2` run scored all 200 images in 104.12 s:
68 TP, 79 FP and 562 FN, giving **precision 46.26% and recall 10.79%**. These
numbers include every fixed sample and every missed swimmer. The unchanged COCO
baseline therefore has a serious domain gap on this maritime subset. No model,
confidence threshold, or sample selection was tuned after seeing these results.
The summary is in `external-image-metrics.json`; full raw outputs and the error
gallery are in the evidence artifact. The ten-image export comparison was also
repeated successfully as `model-parity-a2` under the native network restriction.

Export equivalence is not detection accuracy. These real-file results do not
establish native camera provenance, geographic localization, Docker cold start,
or the four-UAV rescue loop. Those remain separately recorded in the acceptance
matrix. Large weights, source images and raw outputs belong in the recovery
artifact bundle, not Git.

## Official sources

- [RT-DETRv2 source and model table](https://github.com/lyuwenyu/RT-DETR/tree/df2a96c8c3a098567a1149bf0248da6efe6d495d/rtdetrv2_pytorch)
- [FastEmbed's BGE conversion](https://huggingface.co/Qdrant/bge-small-zh-v1.5/tree/46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59)
- [MakeHuman asset license](https://github.com/makehumancommunity/makehuman/blob/a8bc2d54ff0ac92e78ff71431b1023eda42bf482/LICENSE.ASSETS.md)
- [SeaDronesSee data and license](https://github.com/Ben93kie/SeaDronesSee)
