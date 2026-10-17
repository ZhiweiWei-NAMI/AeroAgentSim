# Native rescue companion

Implementation is in progress. No native C00–C10 case has passed: this host has
no Docker daemon/executable or graphics device. The current work includes source
integration, real model preparation, case entrypoints and retained diagnostics.

## Progress and recovery

- Branch: `feat/rescue-companion-native`; PR base: `main`.
- [Implementation checkpoint](../rescue-companion/STATUS.md),
  [result ledger](../rescue-companion/acceptance.csv),
  [original requirements](../rescue-companion/execution-package/01_requirements.md).
- P0 host report: `runs/acceptance/P0/host-report.json`.
- Both original PX4/ns-3 builds exited 127 (`docker not found`):
  `runs/acceptance/P1-baseline/{px4-build.log,ns3-build.log}`.
- C00 and C01 rule attempt `a1` stopped at Docker preflight; their manifests are
  retained under `runs/acceptance/`.
- Trained RT-DETR was exported and compared on ten real images; all ten matched
  within the recorded tolerance. [Model preparation](../rescue-companion/model-preparation.md)
  contains inputs, commands and parity measurements. The 200-frame SeaDronesSee
  run measured precision 46.26% and recall 10.79% (TP 68, FP 79, FN 562), so model
  accuracy needs improvement; results are in `runs/acceptance/sds200-inference-a2/`.

Resume on this branch, restore the data below, and use a new attempt number.
The coordinating developer reviews each completed checkpoint before publishing.

## Private data recovery

[Recovery folder](https://drive.google.com/drive/folders/1Txz-gkOQ3Kkwa6hgB8ynxuv_V4_cJ2oQ):
[input data](https://drive.google.com/drive/folders/18aCCLqLsyfuBxmhDb2tDFTY7N2qmEHi6),
[models/assets](https://drive.google.com/drive/folders/1cIGWo-G5ql3mEyMOuWwhbyPRt7yzjVu2),
[container images](https://drive.google.com/drive/folders/10J5I0GVx0pc2evoo8VTDBfso-0xiPW3Y),
[runs/evidence](https://drive.google.com/drive/folders/1anNqPLDxfWtlWmMHJwIIXPGbotoeRV1C).

Uploaded files:
- [Original execution package](https://drive.google.com/file/d/1rZ_8rpVI6mdl_PJO5Oq3ULzy1R8pKt0o/view)
- [P0/P1 host and failed-build logs](https://drive.google.com/file/d/1CT9zV8i9exLIi_Orvs3cmL4RiTxwh4Rb/view)
- [model-runtime-assets.tar.gz.part-00](https://drive.google.com/file/d/1BExlgyJURpUDvSVxfwvYk-PXymPkirh0/view)
- [model-runtime-assets.tar.gz.part-01](https://drive.google.com/file/d/10sDVLQMCqoyYin8W_Aj_DkEHG4Rxz01l/view)
- [model-runtime-assets.tar.gz.part-02](https://drive.google.com/file/d/1u4TahKszDDXvLkJRf1NPfRWa8pL6PVKs/view)

Download all three model parts with those filenames. Restore the 204,724,315-byte
archive, then put only ONNX and BGE in the directory mounted to companions:

```sh
cat model-runtime-assets.tar.gz.part-* > model-runtime-assets.tar.gz
mkdir -p data/model-preparation data/model-runtime
tar -xzf model-runtime-assets.tar.gz -C data/model-preparation
cp data/model-preparation/rtdetrv2_r18vd_120e_coco.onnx data/model-runtime/
cp -R data/model-preparation/bge-small-zh-v1.5 data/model-runtime/
export MODEL_ROOT="$PWD/data/model-runtime"
```

The archive also contains the trained checkpoint and export source/instructions.
Keep evaluation images/annotations and credentials outside the mounted model
directory. Container images remain to be built; restore future image archives
with `docker load --input <archive>`. Keep earlier run archives separately.

## Build and run

On a Docker-capable host, prepare models and scene assets as described above.
Set `ASSET_ROOT` to the directory containing `makehuman_person/` and `WORLD_ROOT`
to the directory containing the prepared world SDF; these mount only in simulator:

```sh
./tools/companion/check-host.sh --out runs/acceptance/P0/host-report.json
containers/px4-gazebo/build.sh
containers/ns3/build.sh
export RUN_ID=build-only RUN_ROOT="$PWD/runs/build-only"
docker compose -f deploy/rescue/compose.yaml build
python tools/companion/run_case.py --list
python tools/companion/run_case.py --case C00 --seed 42 --agent rule --attempt a2
python tools/companion/run_case.py --case C01 --seed 42 --agent rule --attempt a2
python tools/companion/run_case.py --case C01 --seed 42 --agent model --attempt a1
```

C00 runs the existing PX4 single-vehicle smoke at 20 ms, existing ns-3 smoke,
then a private FlightClient sequence: radio notice → task acceptance → arm →
takeoff → five-metre goto → hold → land. It uses explicit diagnostic tasks and
runs without normal agent action workers. Four-way camera/isolation checks remain.

C01 starts the eight-service Compose deployment. The entrypoint creates fresh
private data/socket directories, sets host UID/GID, uses local images, records
output and stops only its unique project. The default limits are 900 simulated
seconds and 7,200 wall seconds. `FINISHED` is process execution status; case
results come from observed behavior. C02–C10 still need dedicated drivers.

## Review and retain results

```sh
python tools/companion/verify_run.py --run runs/acceptance/C01-42-rule-a2
python tools/companion/export_evidence.py --run-root runs/acceptance --archive
```

The read-only checker decodes RGB/float32 depth, recomputes goto/hold settling
and landing state from PX4 estimates, and checks file/chunk bytes against
native-radio delivery, receiver commits and final ACKs. It returns exit 1 for
contradictions and 2 for incomplete evidence. Whole-case review is still required.

Exports retain all attempts and baseline diagnostics, copy the result ledger,
and show coverage gaps across 31 required combinations. `--archive` adds
`runs.tar.gz`; source records stay unchanged. Missing evidence stays BLOCKED.
The current diagnostic export is `runs/exports/companion-c00-c01-blocked/`.

Remaining work: native four-UAV flight/cancellation and identity checks;
headless RGB-D and clock/origin calibration; damaged-link recovery; physical
release/contact receipt; full rule/model rescue and required scenario variants.
Keep failed attempts, update only observed results, and publish the PR to main
without merging. Raw data, weights and full run archives remain outside Git.
