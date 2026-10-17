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

The owner retains the private recovery directory and download links in the
project conversation. Repository documentation lists filenames only.

Recovery folders contain inputs, models/assets, container images and runs/evidence.
The container-images folder is currently empty. Retain all previous attempts.
Available artifacts include the original execution package, model preparation
update, scene/real-image archive parts, P0/P1 failed-build logs, and controlled
source-check/blocked-case evidence. Controlled checks are labeled separately
from native execution.

Model archive filenames:
- model-runtime-assets.tar.gz.part-00
- model-runtime-assets.tar.gz.part-01
- model-runtime-assets.tar.gz.part-02

Download all three model parts with those filenames. Restore the 204,724,315-byte
archive, then put only ONNX and BGE in the model build-input directory:

```sh
cat model-runtime-assets.tar.gz.part-* > model-runtime-assets.tar.gz
mkdir -p data/model-preparation data/model-runtime
tar -xzf model-runtime-assets.tar.gz -C data/model-preparation
cp data/model-preparation/rtdetrv2_r18vd_120e_coco.onnx data/model-runtime/
cp -R data/model-preparation/bge-small-zh-v1.5 data/model-runtime/
export MODEL_ROOT="$PWD/data/model-runtime"
```

The archive also contains the trained checkpoint and export source/instructions.
Keep evaluation images/annotations and credentials outside that directory.
The Dockerfile copies the required inference files into the image; no runtime
model volume is required. Container images remain to be built; restore future image archives
with `docker load --input <archive>`. Keep earlier run archives separately.

## Build and run

On a Docker-capable host, prepare models and scene assets as described above.
Set `ASSET_ROOT` to the restored `cache/scene` directory containing
`makehuman_person/`. The runner generates each attempt's world and keeps its
labels evaluator-only. Add `--gpu` when using the supplied NVIDIA override:

```sh
./tools/companion/check-host.sh --out runs/acceptance/P0/host-report.json
containers/px4-gazebo/build.sh
containers/ns3/build.sh
sh containers/companion/build.sh
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
results come from observed behavior.

Additional case entrypoints:
- C02: `--variant no_person|occluded_then_visible|still_uncertain` builds the
  corresponding physical world before the common decision loop.
- C03: `missing_fragment_nack_then_retry|final_ack_lost` captures and packages a
  real near-base image with original channel attenuation, then injects one native
  delivery timeout. Its receiver is the coordinator transport service; centre
  planning stays stopped. Full blocked-direct → relay recovery remains.
- C04: protected-task, absent payload, role/ID permutation and
  `low_energy_after_offer`. The latter gives U2 a declared 0.23 energy reserve,
  gives U4 inspect capability and swaps their public horizontal spawn positions.
  Local PX4 motion/source time debits the declared energy budget; PX4 SOC is kept.
  Coverage requires actual U2 selection, a drop below 0.2, delivered withdrawal,
  native cancellation, new radio notices and a received replacement assignment.
- C05: `--variant queued_not_dispatched|running_cancel_with_late_results` drives
  queued cancel or measured 3±0.25 m/s running cancel, then stale-revision input.
- C06: freezes 20 positive/5 absence queries from real local observations, then
  executes the same corpus before/after restart plus a separate old-frame
  reanalysis. Missing corpus material stays BLOCKED; cross-UAV copies remain.
- C07: `receiver_receipt_lost|confirmation_final_ack_lost` applies native delivery
  timeouts to the actual carrier receipt or its first ACK; the centre copy is separate.
- C08: actual later-frame detection/location can produce a separate clue and
  radio event; `second_incident_queued` or five-second `deadline_expired`.
- C09: fresh offline rule deployment, then read-only kernel journal reconstruction
  and existing projector output. Time/action states are compared with native logs.
  Replay a stopped run without Docker using `--replay-run <retained-run>`.
- C10 scores retained native inference with `--perception-samples <frozen.json>`,
  `--perception-annotations <evaluator.json>` and `--inference-run <run>`.
Missing drivers: C03 direct/relay blockage, route loss, no path and lost assignment;
C05 late model/low-level callback; C07 stuck joint and out-of-zone
release; C08 no-eligible resource setup; C10 native sample acquisition/reset.

## Review and retain results

```sh
python tools/companion/verify_run.py --run runs/acceptance/C01-42-rule-a2
python tools/companion/export_evidence.py --run-root runs/acceptance --archive
```

The read-only checker decodes RGB/float32 depth, recomputes goto/hold settling
and landing state from PX4 estimates, and checks file/chunk bytes against
native-radio delivery, receiver commits and final ACKs. Fault cases also require
a matched native drop, actual missing-fragment recovery or receipt timeout. It returns exit 1 for
contradictions and 2 for incomplete evidence. Whole-case review is still required.

Exports retain all attempts and baseline diagnostics, copy the result ledger,
and show coverage gaps across 31 required combinations. `--archive` adds
`runs.tar.gz`; source records stay unchanged. Missing evidence stays BLOCKED.
The current diagnostic export is `runs/exports/companion-final-checkpoint-20261017-a2/`;
`C09-42-rule-a2/replay/` records the missing native WAL in the blocked C00 source.
Actual case-world preparations are in `runs/acceptance/case-scene-preparations/`.

Remaining work: native four-UAV flight/cancellation and identity checks;
headless RGB-D and clock/origin calibration; damaged-link recovery; physical
release/contact receipt; full rule/model rescue and required scenario variants.
Keep failed attempts, update only observed results, and publish the PR to main
without merging. Raw data, weights and full run archives remain outside Git.
