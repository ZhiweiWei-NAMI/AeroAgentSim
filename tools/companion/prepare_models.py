#!/usr/bin/env python3
"""Prepare official local models, or compare an export against loaded weights.

Preparation is deliberately separate from companion startup. PyTorch belongs in
an export-only virtual environment; runtime uses ONNX Runtime CPU and FastEmbed.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time

RTDETR_REVISION = "df2a96c8c3a098567a1149bf0248da6efe6d495d"
RTDETR_SOURCE = "https://github.com/lyuwenyu/RT-DETR.git"
CHECKPOINT_NAME = "rtdetrv2_r18vd_120e_coco_rerun_48.1.pth"
CHECKPOINT_URL = (
    "https://github.com/lyuwenyu/storage/releases/download/v0.2/" + CHECKPOINT_NAME
)
BGE_REVISION = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
BGE_REPOSITORY = "Qdrant/bge-small-zh-v1.5"
CONFIG = "configs/rtdetrv2/rtdetrv2_r18vd_120e_coco.yml"
CONTRACT = {
    "images": {"dtype": "float32", "shape": [1, 3, 640, 640]},
    "orig_target_sizes": {"dtype": "int64", "order": ["width", "height"]},
    "preprocessing": "PIL RGB, bilinear resize to 640x640, /255, NCHW; no letterbox or mean/std",
    "outputs": ["labels", "boxes", "scores"],
    "boxes": "original-image xyxy pixels",
    "labels": "contiguous COCO 0..79; deploy returns before COCO category remap",
    "person_label": 0,
    "person_coco_category_id": 1,
}


def disable_process_network() -> None:
    """Constrain this offline evaluation process, including native libraries.

    Linux seccomp is a per-process restriction, not a host firewall change.
    It is installed before importing ORT; native telemetry cannot open a socket.
    Fail closed if the local kernel does not support the restriction.
    """
    library = ctypes.util.find_library("seccomp")
    if not library:
        raise RuntimeError("Offline evaluation requires libseccomp; network isolation unavailable")
    seccomp = ctypes.CDLL(library, use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError("Could not create local network restriction")
    try:
        for name in (b"socket", b"connect", b"sendto", b"sendmsg", b"sendmmsg"):
            number = seccomp.seccomp_syscall_resolve_name(name)
            if number < 0 or seccomp.seccomp_rule_add(context, 0x00050001, number, 0) < 0:
                raise RuntimeError(f"Could not restrict network syscall {name!r}")
        if seccomp.seccomp_load(context) < 0:
            raise RuntimeError("Kernel refused local network restriction; inference not started")
    finally:
        seccomp.seccomp_release(context)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def file_record(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest}


def download(url: str, destination: Path) -> None:
    """Use official URL and the environment's normal proxy; never embed tokens."""
    if destination.is_file() and destination.stat().st_size:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    subprocess.run(
        ["curl", "--fail", "--location", "--retry", "2", "--max-time", "600",
         "--output", str(temporary), url], check=True,
    )
    temporary.replace(destination)


def prepare_runtime_view(cache: Path) -> Path:
    """Copy/link only inference assets into the read-only shared model mount."""
    root = cache / "model-runtime"
    root.mkdir(exist_ok=True)
    sources = [cache / "rtdetrv2_r18vd_120e_coco.onnx"]
    bge = cache / "bge-small-zh-v1.5"
    if bge.is_dir():
        sources += [p for p in bge.iterdir() if p.is_file()]
    for source in sources:
        if not source.is_file():
            continue
        destination = root / source.relative_to(cache)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and os.path.samefile(source, destination):
            continue
        if destination.exists():
            destination.unlink()
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)
    return root


def prepare_detector(cache: Path, export_python: str | None) -> dict[str, object]:
    source = cache / "RT-DETR"
    if not source.exists():
        subprocess.run(["git", "clone", RTDETR_SOURCE, str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "checkout", RTDETR_REVISION], check=True)
    checkpoint = cache / CHECKPOINT_NAME
    download(CHECKPOINT_URL, checkpoint)
    result: dict[str, object] = {
        "source": RTDETR_SOURCE, "revision": RTDETR_REVISION,
        "license": "Apache-2.0 (official RT-DETR repository)",
        "checkpoint_url": CHECKPOINT_URL, "checkpoint": file_record(checkpoint),
        "configuration": CONFIG, "contract": CONTRACT, "export_status": "NOT_RUN",
    }
    if export_python:
        output = cache / "rtdetrv2_r18vd_120e_coco.onnx"
        command = [export_python, str(Path(__file__).resolve()), "--cache", str(cache),
                   "--offline-export", str(source / "rtdetrv2_pytorch")]
        # The full trained checkpoint supplies the backbone. Disabling its
        # separate ImageNet download does not disable checkpoint loading.
        env = dict(os.environ, OMP_NUM_THREADS="2", MKL_NUM_THREADS="2")
        with (cache / "export.log").open("w") as log:
            subprocess.run(command, cwd=source / "rtdetrv2_pytorch", env=env,
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        result.update(export_status="EXPORTED", export_command=command,
                      onnx=file_record(output), export_log=str(cache / "export.log"))
    return result


def prepare_bge(cache: Path) -> dict[str, object]:
    # Set the public download cache before importing HF; avoid implicit token use.
    os.environ.setdefault("HF_HOME", str(cache / "huggingface"))
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from huggingface_hub import snapshot_download

    destination = cache / "bge-small-zh-v1.5"
    snapshot_download(BGE_REPOSITORY, revision=BGE_REVISION, token=False,
                      local_dir=destination,
                      allow_patterns=["*.json", "*.txt", "README.md", "model_optimized.onnx"])
    # Downloading needs public network access; inference runs in a separate
    # child that denies native networking before even importing ONNX Runtime.
    check = cache / "bge-local-check.json"
    subprocess.run([sys.executable, str(Path(__file__).resolve()), "--cache", str(cache),
                    "--offline-bge", str(destination), "--output", str(check)], check=True)
    return {
        "model": "BAAI/bge-small-zh-v1.5", "converted_repository": BGE_REPOSITORY,
        "revision": BGE_REVISION, "license": "MIT", "directory": str(destination),
        "files": [file_record(p) for p in sorted(destination.iterdir()) if p.is_file()],
        "local_inference": json.loads(check.read_text()),
        "fastembed_version": importlib.metadata.version("fastembed"),
    }


def check_bge(destination: Path) -> dict[str, object]:
    import onnxruntime as ort
    ort.disable_telemetry_events()
    from fastembed import TextEmbedding

    model = TextEmbedding(model_name="BAAI/bge-small-zh-v1.5",
                          specific_model_path=str(destination),
                          local_files_only=True, threads=2,
                          providers=["CPUExecutionProvider"])
    vectors = list(model.embed(["屋顶上发现两名人员", "队友刚收到的救援物资照片"]))
    return {"inputs": len(vectors), "dimension": len(vectors[0]),
            "provider": "CPUExecutionProvider",
            "privacy": {"ort_telemetry": "disabled", "native_network": "seccomp-denied"}}


def compare_export(cache: Path, images: Path, output: Path) -> dict[str, object]:
    """Run both real engines on >=10 distinct real source images, not random data."""
    import numpy as np
    import onnxruntime as ort
    ort.disable_telemetry_events()
    from PIL import Image, ImageDraw
    from scipy.optimize import linear_sum_assignment
    import torch
    import torch.nn as nn

    paths = sorted(p for p in images.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"})
    if len(paths) < 10:
        raise ValueError("Supply at least ten real source images; synthetic inputs are not parity evidence")
    if len({file_record(p)["sha256"] for p in paths}) != len(paths):
        raise ValueError("Duplicate image bytes do not count as distinct images")
    if output.exists():
        raise FileExistsError(f"Preserve previous evidence: choose a new output directory: {output}")
    output.mkdir(parents=True)
    sys.path.insert(0, str(cache / "RT-DETR" / "rtdetrv2_pytorch"))
    from src.core import YAMLConfig

    torch.set_num_threads(2)
    checkpoint = cache / CHECKPOINT_NAME
    cfg = YAMLConfig(str(cache / "RT-DETR" / "rtdetrv2_pytorch" / CONFIG))
    cfg.yaml_cfg["PResNet"]["pretrained"] = False
    weights = torch.load(checkpoint, map_location="cpu", weights_only=True)
    source_key = "ema.module" if "ema" in weights else "model"
    state = weights["ema"]["module"] if "ema" in weights else weights["model"]
    incompatible = cfg.model.load_state_dict(state, strict=True)

    class Deploy(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.model = cfg.model.deploy()
            self.postprocessor = cfg.postprocessor.deploy()

        def forward(self, image, size):
            return self.postprocessor(self.model(image), size)

    model = Deploy().eval()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    session = ort.InferenceSession(str(cache / "rtdetrv2_r18vd_120e_coco.onnx"),
                                   sess_options=options, providers=["CPUExecutionProvider"])
    rows = []
    for path in paths:
        rgb = Image.open(path).convert("RGB")
        array = np.asarray(rgb.resize((640, 640), Image.Resampling.BILINEAR), dtype=np.float32) / 255
        tensor = np.ascontiguousarray(array.transpose(2, 0, 1)[None])
        size = np.asarray([rgb.size], dtype=np.int64)
        start = time.perf_counter()
        with torch.inference_mode():
            native = [a.numpy() for a in model(torch.from_numpy(tensor), torch.from_numpy(size))]
        torch_seconds = time.perf_counter() - start
        start = time.perf_counter()
        exported = session.run(None, {"images": tensor, "orig_target_sizes": size})
        ort_seconds = time.perf_counter() - start
        np.savez_compressed(output / (path.stem + ".npz"),
                            torch_labels=native[0], torch_boxes=native[1], torch_scores=native[2],
                            onnx_labels=exported[0], onnx_boxes=exported[1], onnx_scores=exported[2])
        # Float ties can reorder the top-300. Match per class rather than falsely
        # interpreting a rank swap as a changed detection. Retain all raw arrays.
        delta_boxes, delta_scores, matched, same_counts = [], [], 0, True
        threshold = 0.3
        for label in range(80):
            left = np.flatnonzero((native[0][0] == label) & (native[2][0] >= threshold))
            right = np.flatnonzero((exported[0][0] == label) & (exported[2][0] >= threshold))
            if len(left) != len(right):
                same_counts = False
            if not len(left) or not len(right):
                continue
            costs = np.max(np.abs(native[1][0, left, None] - exported[1][0, right][None]), axis=2)
            a, b = linear_sum_assignment(costs)
            delta_boxes.extend(costs[a, b].tolist())
            delta_scores.extend(np.abs(native[2][0, left[a]] - exported[2][0, right[b]]).tolist())
            matched += len(a)
        row = {"image": file_record(path), "width_height": list(rgb.size),
               "torch_seconds": torch_seconds, "onnx_seconds": ort_seconds,
               "raw_labels_same_rank_fraction": float(np.mean(native[0] == exported[0])),
               "matched_detections_at_0_3": matched, "class_counts_equal": same_counts,
               "max_box_delta_px": max(delta_boxes, default=0.0),
               "max_score_delta": max(delta_scores, default=0.0)}
        row["equivalent"] = same_counts and row["max_box_delta_px"] <= 0.1 and row["max_score_delta"] <= 1e-4
        draw = ImageDraw.Draw(rgb)
        for label, box, score in zip(*(a[0] for a in exported)):
            if label == 0 and score >= threshold:
                draw.rectangle(box.tolist(), outline="red", width=3)
                draw.text((float(box[0]), float(box[1])), f"person {score:.3f}", fill="red")
        rgb.save(output / (path.stem + "-persons.jpg"))
        rows.append(row)
        print(json.dumps(row), flush=True)
    result = {"scope": "export equivalence on real images, not detector accuracy or simulation acceptance",
              "checkpoint": file_record(checkpoint), "loaded_state": source_key,
              "strict_load": {"missing": incompatible.missing_keys, "unexpected": incompatible.unexpected_keys},
              "contract": CONTRACT, "providers": session.get_providers(),
              "criterion": {"score_threshold": 0.3, "box_delta_px": 0.1, "score_delta": 1e-4},
              "image_count": len(rows), "all_equivalent": all(r["equivalent"] for r in rows), "images": rows}
    write_json(output / "equivalence.json", result)
    return result


def match_detections(detections, targets_xyxy, ignored_regions=(), iou_threshold=0.5):
    """Pure score-ordered one-to-one matching shared by independent evaluators.

    Inputs are already class/confidence filtered. Ignore regions suppress only
    otherwise unmatched predictions with at least 50% of their area inside an
    explicit ignore region. No inference, downloads or model imports occur.
    """

    def intersection(a, b):
        return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))

    def area(a):
        return max(0, a[2] - a[0]) * max(0, a[3] - a[1])

    def iou(a, b):
        shared = intersection(a, b)
        return shared / max(1e-9, area(a) + area(b) - shared)

    unmatched = set(range(len(targets_xyxy)))
    matched, false_positive, ignored = [], [], []
    for detection in sorted(detections, key=lambda x: -x["score"]):
        bbox = detection["bbox_xyxy"]
        candidates = [(iou(bbox, targets_xyxy[i]), i) for i in unmatched]
        overlap, target = max(candidates, default=(0, -1))
        if target >= 0 and overlap >= iou_threshold:
            unmatched.remove(target)
            matched.append({"target_index": target, "detection_id": detection["detection_id"], "iou": overlap})
        elif any(intersection(bbox, region) / max(1e-9, area(bbox)) >= 0.5 for region in ignored_regions):
            ignored.append(detection["detection_id"])
        else:
            false_positive.append(detection["detection_id"])
    return {"matched": matched, "false_positive": false_positive, "ignored": ignored,
            "unmatched_indices": sorted(unmatched)}


def score_dataset(cache: Path, annotations: Path, output: Path) -> dict[str, object]:
    """Score frozen real images independently of the runtime detector."""
    from collections import defaultdict
    from PIL import Image, ImageDraw
    import onnxruntime as ort
    ort.disable_telemetry_events()
    from aeroagentsim.companion.vision import RtdetrDetector

    if output.exists():
        raise FileExistsError(f"Choose a fresh output directory: {output}")
    output.mkdir(parents=True)
    data = json.loads(annotations.read_text())
    labels = defaultdict(list)
    for item in data["annotations"]:
        labels[item["image_id"]].append(item)
    detector = RtdetrDetector(cache / "rtdetrv2_r18vd_120e_coco.onnx", score_threshold=0.5)

    def box_xyxy(box):
        x, y, width, height = box
        return [x, y, x + width, y + height]

    rows = []
    started = time.perf_counter()
    for index, im in enumerate(data["images"]):
        path = annotations.parent / "images" / Path(im["file_name"]).with_suffix(".jpg").name
        source = Image.open(path).convert("RGB")
        if source.size != (im["width"], im["height"]):
            raise ValueError(f"Image dimensions do not match official labels: {path}")
        inference = detector.detect(path, f"sds-v2-val:{im['id']}", output / "detections")
        targets = [a for a in labels[im["id"]] if a["category_id"] == 1]
        ignored_regions = [box_xyxy(a["bbox"]) for a in labels[im["id"]] if a["category_id"] == 0]
        match = match_detections(inference["detections"], [box_xyxy(a["bbox"]) for a in targets], ignored_regions)
        unmatched = match["unmatched_indices"]
        matched = [{"annotation_id": targets[item["target_index"]]["id"],
                    "detection_id": item["detection_id"], "iou": item["iou"]}
                   for item in match["matched"]]
        false_positive, ignored = match["false_positive"], match["ignored"]
        row = {"image_id": im["id"], "source": im.get("source"), "image": file_record(path),
               "ground_truth_swimmers": len(targets), "tp": len(matched), "fp": len(false_positive),
               "fn": len(unmatched), "ignored_predictions": ignored, "matched": matched,
               "false_positive_detection_ids": false_positive,
               "missed_annotation_ids": [targets[i]["id"] for i in sorted(unmatched)],
               "inference_wall_ns": inference["inference_wall_ns"],
               "raw_result": inference["raw_result_path"]}
        if false_positive or unmatched:
            draw = ImageDraw.Draw(source)
            for annotation in targets:
                draw.rectangle(box_xyxy(annotation["bbox"]), outline="lime", width=3)
            for detection in inference["detections"]:
                draw.rectangle(detection["bbox_xyxy"], outline="red", width=3)
            gallery = output / "error-gallery"
            gallery.mkdir(exist_ok=True)
            source.thumbnail((1280, 1280))
            source.save(gallery / f"{im['id']}.jpg", quality=85)
        rows.append(row)
        if (index + 1) % 10 == 0:
            print(f"Scored {index + 1}/{len(data['images'])} real images", flush=True)
    tp, fp, fn = (sum(row[key] for row in rows) for key in ("tp", "fp", "fn"))
    summary = {"dataset": "SeaDronesSee Object Detection v2", "split": "official validation subset",
               "scope": "external real-image person-to-swimmer baseline; not rooftop or localization accuracy",
               "confidence": 0.5, "matching_iou": 0.5,
               "mapping": "COCO contiguous person label 0 to SeaDronesSee swimmer category 1",
               "ignored_categories": "Non-swimmer categories excluded from positive GT; only explicit category-0 regions ignore predictions. Predictions on boats/buoys still count as FP.",
               "images": len(rows), "negative_images": sum(row["ground_truth_swimmers"] == 0 for row in rows),
               "tp": tp, "fp": fp, "fn": fn, "precision": tp / (tp + fp) if tp + fp else None,
               "recall": tp / (tp + fn) if tp + fn else None,
               "elapsed_wall_s": time.perf_counter() - started,
               "providers": detector.providers, "model_sha256": detector.model_sha256,
               "privacy": {"ort_telemetry": "disabled", "native_network": "seccomp-denied"},
               "selection": file_record(annotations), "per_image": rows}
    write_json(output / "detection-metrics.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "per_image"}), flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("data/companion-models"))
    parser.add_argument("--lock", type=Path, default=Path("deploy/rescue/model-lock.json"))
    parser.add_argument("--export-python", help="Python in export-only environment with CPU torch/torchvision")
    parser.add_argument("--detector", action="store_true")
    parser.add_argument("--bge", action="store_true")
    parser.add_argument("--runtime-view-only", action="store_true",
                        help="Restore the inference-only model mount from already local files, without downloads")
    parser.add_argument("--compare-images", type=Path)
    parser.add_argument("--score-dataset", type=Path, help="Frozen COCO-format subset annotations; image files in adjacent images/")
    parser.add_argument("--offline-export", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--offline-bge", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, default=Path("runs/acceptance/model-parity"))
    args = parser.parse_args()
    cache = args.cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    if args.runtime_view_only:
        required = [cache / "rtdetrv2_r18vd_120e_coco.onnx",
                    cache / "bge-small-zh-v1.5/model_optimized.onnx"]
        if any(not path.is_file() for path in required):
            raise FileNotFoundError("Restore the detector and BGE assets before building their runtime mount")
        print(prepare_runtime_view(cache))
        return 0
    if args.offline_bge:
        disable_process_network()
        write_json(args.output, check_bge(args.offline_bge))
        return 0
    if args.offline_export:
        disable_process_network()
        import onnxruntime as ort
        ort.disable_telemetry_events()
        script = args.offline_export / "tools/export_onnx.py"
        sys.argv = [str(script), "-c", str(args.offline_export / CONFIG),
                    "-r", str(cache / CHECKPOINT_NAME),
                    "-o", str(cache / "rtdetrv2_r18vd_120e_coco.onnx"),
                    "-u", "PResNet.pretrained=False", "--check"]
        runpy.run_path(str(script), run_name="__main__")
        return 0
    if args.compare_images:
        disable_process_network()
        result = compare_export(cache, args.compare_images, args.output)
        return 0 if result["all_equivalent"] else 1
    if args.score_dataset:
        disable_process_network()
        score_dataset(cache, args.score_dataset, args.output)
        return 0
    result = json.loads(args.lock.read_text()) if args.lock.exists() else {}
    result.update(schema="aeroagentsim.companion-models/v1", runtime_downloads=False)
    try:
        if args.detector or not args.bge:
            result["detector"] = prepare_detector(cache, args.export_python)
        if args.bge or not args.detector:
            result["retrieval"] = prepare_bge(cache)
    except Exception as exc:
        result["preparation_error"] = f"{type(exc).__name__}: {exc}"
        write_json(args.lock, result)
        raise
    result.pop("preparation_error", None)
    result["runtime_mount"] = str(prepare_runtime_view(cache))
    write_json(args.lock, result)
    print(f"Prepared local model records: {args.lock}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
