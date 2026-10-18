# AeroRescue

A separate application distribution on the AeroAgentSim native platform. The platform owns sensor/estimate streams, command cancellation, radio physics and payload actuation. AeroRescue owns rescue skills, local execution, scenario assets and the Cordis harness integration.

## Local runtime prerequisites

The recorded local mock runtime used Python 3.12 and Node.js 24.19.0. Use the
committed Python requirements and npm lockfile; the application package alone
declares only its base dependencies, not the complete ONNX/BGE image stack.
From the **repository root**:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r AeroRescue/containers/companion/requirements.txt
python -m pip install ./aerokernel . ./AeroRescue
(cd AeroRescue/harness && npm ci --omit=dev --no-audit --no-fund)
cd AeroRescue
```

Keep this Python environment active when invoking the runner: it passes the
current interpreter to its workers. Node must be on PATH. This installs the
recorded runtime dependencies; it does not fetch model weights or imagery.
Restore or separately prepare the ONNX/BGE runtime models using the model
archive instructions in [guide.md](docs/guide.md), and prepare the local sprite
using [mock-visual-asset.md](docs/mock-visual-asset.md). The model directory passed
to `--model-cache` must contain `rtdetrv2_r18vd_120e_coco.onnx` and
`bge-small-zh-v1.5/`.

Run source preparation and case commands from the AeroRescue directory. Deployment is `deploy/compose.yaml`; its build context is the repository root. `containers/companion/build.sh` builds a complete model-bearing image from that root and requires MODEL_ROOT. Prebuilt runtime images do not require the model directory.

Built wheels include the canonical `skills/` and `scenarios/` directories and an explicit copy of the platform adapter template/registry. Runtime resource loading uses importlib.resources, never a guessed repository root. For editable source use set AAS_SKILLS_DIR and RESCUE_CONFIG to explicit source paths; ordinary wheel installs need neither.

The 21 directories under `skills/` each contain an independently loaded manifest and manual. `harness/` uses Cordis composition and the official DSH session/agent loop as the sole decision and tool-dispatch owner. Shared Python execution in `src/aerorescue/companion` runs with `--execution-only`: it exposes factual state, bounded execution and pure rule proposals, but does not dispatch those proposals autonomously.

The companion image defaults to the Node harness, which starts its local Python worker. Each UAV and the centre retain separate `/data/dsh` sessions; the coordinator service explicitly starts Python without an Agent. See [the harness guide](harness/README.md) for launch and provider configuration.

Local execution verified a real `read-evidence` result and official DSH session resume without repeating its notification or tool execution. That request was explicitly queued; autonomous native rescue-task selection, flight and radio execution remain unverified. Docker is unavailable on this host, so the integrated image has not been built.

See docs/guide.md and docs/STATUS.md for execution limitations. Packaging/import checks are not native acceptance.

## Explicit mock run

The mock backend uses the same kernel, adapter request/reply objects, task worker and radio transfer code. It substitutes kinematic flight, RGB-D scene rendering, parcel motion and packet delivery; native remains the default coordinator backend.

Prepare the local photo asset with `tools/prepare_mock_sprite.py` as described in `docs/mock-visual-asset.md`. After the installation above and local model/sprite preparation, run from this directory:

```sh
python tools/run_mock.py --scenario scenarios/rooftop-flood-mock.yaml \
  --run-id C01-42-rule-attempt1 --seed 42 --stop-on-outcome \
  --model-cache /path/to/runtime-models --person-sprite /path/to/person-sprite.png
```

Each attempt gets a new directory under `runs/mock/`, five separate Cordis/DSH owners and per-agent histories. The summary separates process shutdown, task outcome and return completion. This source checkpoint does not mark the four-agent rescue acceptance complete; current evidence and remaining work are recorded separately in `docs/mock-report.md` when available.


## Deliverables and validation scope

[DELIVERABLES.md](docs/DELIVERABLES.md) separates the AeroAgentSim platform,
AeroRescue application and private Google Drive data/evidence. The application
branch includes its platform dependency; both pull requests still target main.
Full native image construction and execution remain unverified. Local mock
execution uses the installed Python/Node processes directly, not those images.

The existing runs show scoped end-to-end outcomes and fault behavior. They do
not establish a general reliability rate or that an LLM is necessary. One paired
comparison of stage-wise allocation without replanning after resource withdrawal
against online replanning is complete: online replanning restored inspection,
but neither arm completed delivery. A traditional planner can also replan online.
See the actual costs, outcomes and limits in [the report](docs/mock-report.md).


## Isolated mock containers (build/run not yet verified)

`deploy/compose.mock.yaml` is a standalone deployment; do not combine it with
the native Compose file. It explicitly starts the coordinator with
`--backend mock` and gives each of five rule agents only its own data, file-IPC
endpoint and read-only configuration. The centre cannot mount other agents'
histories. Public capabilities, map, incident and inventory geometry are
configuration; peer spawn, energy, commitments and task bodies are omitted.
The coordinator alone receives private world inputs and platform visual assets.

The `agent` image target reuses the existing Python/Node/model image and removes
bundled example scenarios. The `mock-coordinator` target alone adds the built
AeroAgentSim frontend and official Playwright Chromium. Model weights are build
inputs; they are not shared writable histories. Each container has its own
filesystem and no network interface beyond loopback; no Docker socket or whole
workspace is mounted. This entry supports local rule mode, not a network LLM.

On a Docker/BuildKit/Compose-capable host, after restoring models and the actual
platform asset pack, run from `AeroRescue` with the installed Python environment:

```sh
export RUN_ID=isolated-platform-mock-001
export RUN_ROOT="$PWD/runs/containers/$RUN_ID"
export AAS_UID="$(id -u)" AAS_GID="$(id -g)"
export MODEL_ROOT=/absolute/path/to/model-runtime
export PLATFORM_ASSET_ROOT=/absolute/path/to/platform-assets
export AEROAGENTSIM_ASSETS_DIR="$PLATFORM_ASSET_ROOT"
export PLATFORM_SCENARIO=scenarios/huangpu-platform-camera.yaml
python tools/prepare_mock_compose.py   --scenario "$PLATFORM_SCENARIO" --run-id "$RUN_ID" --run-root "$RUN_ROOT"
docker compose -f deploy/compose.mock.yaml build
docker compose -f deploy/compose.mock.yaml up   --abort-on-container-exit --exit-code-from coordinator
```

Preparation refuses an existing run directory. It requires an authored scenario with
`mock_backend.visual_backend=platform_city` and derives public obstacle bounds from the restored official GLB assets, then
validates its source-city geometry before generating mounts. The supplied
`huangpu-platform-camera.yaml` uses that city geometry; its outcomes are not
covered by the earlier gray-world runs. The legacy
`rooftop-flood-mock.yaml` is rejected; merely renaming its visual backend does not
make its coordinates/collisions match the city. No photograph sprite is required
by this platform GLB entry. Renderer startup/capture failures
must remain errors, never silently return the earlier photo-billboard renderer.
The platform asset mount is read-only and coordinator-only. The image's browser
uses loopback HTTP and local pipes, so no external web service is required.
The independent world horizon defaults to 1040 seconds to leave recovery time;
the incident's existing 900-second business deadline is unchanged. Compose
stops the other services when the coordinator exits; inspect each agent's
recorded outcomes/return state and container logs separately rather than treating
a process exit as mission success.

This host has no Docker/Podman executable or engine socket. These image stages
and mount declarations have **not** been built or started here; source/config
inspection is not evidence of container isolation or platform-camera execution.
The earlier `tools/run_mock.py` results used same-user host processes and cannot
establish OS/container isolation.
