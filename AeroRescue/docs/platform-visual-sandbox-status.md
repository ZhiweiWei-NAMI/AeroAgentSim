# Platform visual and mock sandbox acceptance status

Updated 2026-10-18. This is the status of the user's four requested checks.
No item below has full acceptance. Earlier gray-background/photo-billboard mock
runs do not substitute for platform-asset rendering or container execution.
All approvals remain pending.

## 1. Use the correct assets for rendering — installed, pixels blocked

The official AeroAgentSim release `assets-huangpu-city-v1.1` archive was
reused from the previously downloaded, verified local copy: 129,165,236 bytes,
481 manifest files and 414 city buildings. It is installed as
`huangpu-city/1.1.0`; its original NOTICE is retained. No second large download
was required. The frontend's lockfile install and Vite production build completed.
Scene geometry is derived from the source GLB transforms, not the previous
hand-authored gray buildings.

Two actual mock flight/hold source cuts were recorded at 20.4 and 26.0 simulation
seconds, positions [-80,75,36] and [-80,82.5,36] metres. These are committed
state cuts, **not camera pictures**. Platform RGB PNG count and depth-frame count
remain zero. The renderer's loopback server started, but Chromium aborted during
its process-singleton Unix socket creation with EPERM. A normal escalated
invocation failed before the command at the environment wrapper; it did not
produce a frame.

Ground-supported spawn positions replaced the earlier roof starts, which were
inside expanded planner obstacles; all eight actual public-map climb/return
planner calls now succeed. The x500 catalog size/axis normalization is corrected
in source, but its rendered appearance remains unverified; the older source-cuts-02
record does not establish planner usability.

Required next evidence: actual platform city/person/package camera frames,
registered depth and transforms, followed by the existing detector/localizer/
two-view verifier on those pixels. Static source builds cannot pass this item.

## 2. Correct integration, interactions and harness — source connected, new visual flow unrun

Cordis/DSH remains the sole decision and tool-dispatch owner. Python is an
execution-only worker. The platform-camera adapter consumes the same committed
mock world state and supplies RGB/depth/calibration through the existing
observation path. Browser failures are errors; there is no fallback to the old
photo-billboard pictures.

The existing rule harness has earlier real tool-call evidence, but the new
platform-camera flow has not completed a camera capture or autonomous rescue
run. Source/typechecking and the observation CLI's help are not runtime proof.
The live-model provider and native flight/radio remain unverified.

## 3. Correct mock image sandbox — definitions prepared, images unbuilt

`deploy/compose.mock.yaml` explicitly selects `--backend mock` and file IPC.
The existing image definition has separate agent and mock-coordinator targets.
Only the coordinator target carries the frontend and Playwright Chromium.
Only the coordinator mounts the private world and read-only platform asset pack;
models are built into the images. The native default image target is retained.

`tools/prepare_mock_compose.py` derives and validates source-city geometry before
exporting separate configurations. The old photo-billboard scenario is rejected.
Actual preparation produced 414 source-building bounds and the separated files,
without starting a browser, simulator or container. Preparation was repeated
on the corrected ground-spawn scenario in a new directory; the earlier roof-spawn
preparation is superseded.

This host has no Docker/Podman executable or engine socket. No image was built,
no Compose service was started, and container isolation has not been observed.
In the host probes, ordinary socket creation returned EPERM while socketpair
worked. strace could not trace because ptrace operations returned EPERM. These
observations do not establish the exact seccomp rule or a general ban on every
socket operation; no security setting was changed to bypass the restriction.

## 4. Correct multi-agent interaction — mounts/configuration reviewed, container behavior pending

Each of four UAV agents and the centre gets its own data/history/session volume,
its own file-IPC endpoint and its own read-only configuration. No agent mounts
the whole run, another agent's history, platform assets or a Docker socket.
Rule containers use `network_mode: none` (loopback remains available).

A UAV configuration carries its own profile and peers' public capabilities.
The centre carries public capabilities and IDs/revisions of initial notices
published on its behalf, not other agents' private workflows, energy or inventory
state. Shared map/demand/inventory geometry is explicitly public. The coordinator
routes messages and owns world truth; the centre agent does not mount that state.

These declarations and generated files still require actual container checks:
attempted cross-agent file access must fail, requests must remain endpoint-bound,
the centre must act on received messages, and the full case must retain tool,
packet and return evidence. Earlier same-user host processes cannot prove these
OS/container boundaries.

## Re-run on a permitted environment

From repository root, use installed Python dependencies and the built frontend.
Set paths to the restored official assets and a compatible Chromium executable:

```sh
export AEROAGENTSIM_ASSETS_DIR=/absolute/path/to/platform-assets
export AEROAGENTSIM_CHROMIUM=/absolute/path/to/chromium
export AEROAGENTSIM_FRONTEND_DIST="$PWD/frontend/dist"
export AEROAGENTSIM_FRONTEND_NODE_MODULES="$PWD/frontend/node_modules"
export PYTHONPATH="$PWD/AeroRescue/src:$PWD/src:$PWD/aerokernel"
python AeroRescue/tools/capture_platform_camera.py \
  --scenario AeroRescue/scenarios/huangpu-platform-camera.yaml \
  --output /absolute/path/to/new-camera-run --run-id new-platform-camera-001
```

Only successful capture produces
`coordinator/platform-frames/u2/<sim_ns>/capture.json`, PNG and depth.
`--source-only` deliberately stops at committed source states and is not a
rendering success. Feed actual capture files to
`AeroRescue/tools/observe_platform_capture.py --capture ... --model ... --output ...`.
Do not replace missing captures with earlier mock PNGs.

For images and six isolated containers, follow the exact preparation/build/up
commands in [README](../README.md#isolated-mock-containers-buildrun-not-yet-verified).
Run on a Docker-capable host; keep the new run separate from historical evidence.

## Existing evidence locations

All paths below are relative to AeroRescue; data stays in the private collection.

- `runs/development/platform-assets-use-01/`: asset installation, frontend build,
  browser failure, environment-wrapper failure and tracing limitation.
- `runs/development/platform-camera-source-cuts-03/report.json`: actual flight
  actions and two committed cuts; explicitly source-only.
- `runs/development/platform-ground-routes-01/report.json`: eight actual planner
  climb/return calls on corrected ground starts, all successful.
- `runs/development/platform-depth-source-01/report.json`: frontend/typecheck
  results and zero actual platform images.
- `deploy/compose.mock.yaml`, `tools/prepare_mock_compose.py`: declarations and
  preparation entry, not execution evidence.

Successful re-execution must update these four statuses using actual artifacts;
none is pre-approved by this document.
