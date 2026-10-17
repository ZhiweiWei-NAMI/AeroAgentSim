# Rescue companion implementation checkpoint

Status: IN PROGRESS. No native acceptance case has passed yet.

## Scope and current instructions

Implement the supplied four-UAV companion research prototype on the existing
AeroAgentSim kernel, PX4/Gazebo, ns-3 and provider stack. The original execution
package is preserved in `execution-package/` as a requirements reference, not
as an implementation or evidence of execution.

The owner's 2026-10-17 clarification supersedes administrative checks in that
package: no TDD or general validation platform; no software version/hash-field
acceptance gates. Keep useful dependency versions for reproduction, and file
integrity checks where needed for actual radio delivery. Functional evidence
(camera inputs, estimated poses, actions, communication and physical delivery)
remains required. Preserve failures and do not lower functional thresholds.

## Recovery

- Repository: ZhiweiWei-NAMI/AeroAgentSim
- Base: origin/main at 0b406d01f27920da45facd8d2d6cdde1e7c3d49e
- Working branch: feat/rescue-companion-native
- PR base: main; do not auto-merge.
- Resume from this branch after environment loss; preserve newer remote changes.
- Main agent reviews each completed implementation checkpoint before commit/push.
- Raw datasets, weights, credentials and full run directories stay outside Git.

## Current host observation

2026-10-17: Debian 13.6, x86_64, approximately 9.7 GiB RAM and 28 GiB free disk.
No docker/podman executable, Docker socket, /dev/dri or NVIDIA device was found;
process capabilities are empty. Native Docker build/run and four-UAV RGB-D
execution are BLOCKED on this host. Do not attempt privileged nested Docker.
A direct Unix socket creation attempt also returned EPERM in the execution sandbox;
the supported elevated execution attempt failed before running. No host security
settings were changed. Source and model preparation proceed independently; they do not count as native
acceptance. No training server is being used.

## Work ownership

- Flight: companion image/Compose, private bridge, operation lifecycle,
  flight client, native cancellation, coordinator integration.
- Perception: native sensors/estimated telemetry, geometry, visual inference,
  local history and evidence processing.
- Runtime: radio delivery, physical payload, per-UAV/centre agent runtime,
  task profile and solver input integration.
- Preparation: runtime environment and trained model preparation.
- Evidence: case entrypoints, functional evidence summaries and recovery docs.
- Review: independent integration review; main agent owns final full-system review.

## Remaining

P0 host/baseline recording is underway. P1–P8 are not complete. The initial
acceptance matrix remains NOT_RUN; source code presence must not change it to PASS.

## Completed software execution (not native acceptance)

Official trained RT-DETRv2 R18 was exported to ONNX and compared on ten real
SeaDronesSee images: matching detections within 0.000245 pixels and 0.00000245
score difference. Local BGE retrieval, SQLite restart/history reanalysis and
a 5,015-byte target crop were executed on a real image. Full image compression
at a 30,000-byte budget returned unavailable and is preserved. No native flight,
radio delivery or physical drop has passed yet.
