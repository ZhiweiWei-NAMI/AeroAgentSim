# AeroRescue

A separate application distribution on the AeroAgentSim native platform. The platform owns sensor/estimate streams, command cancellation, radio physics and payload actuation. AeroRescue owns rescue skills, local execution, scenario assets and the Cordis harness integration.

Install platform and application from repository root:

    python -m pip install ./aerokernel . ./AeroRescue

Run source preparation and case commands from the AeroRescue directory. Deployment is `deploy/compose.yaml`; its build context is the repository root. `containers/companion/build.sh` builds a complete model-bearing image from that root and requires MODEL_ROOT. Prebuilt runtime images do not require the model directory.

Built wheels include the canonical `skills/` and `scenarios/` directories and an explicit copy of the platform adapter template/registry. Runtime resource loading uses importlib.resources, never a guessed repository root. For editable source use set AAS_SKILLS_DIR and RESCUE_CONFIG to explicit source paths; ordinary wheel installs need neither.

The 21 directories under `skills/` each contain an independently loaded manifest and manual. `harness/` uses Cordis composition and the official DSH session/agent loop as the sole decision and tool-dispatch owner. Shared Python execution in `src/aerorescue/companion` runs with `--execution-only`: it exposes factual state, bounded execution and pure rule proposals, but does not dispatch those proposals autonomously.

The companion image defaults to the Node harness, which starts its local Python worker. Each UAV and the centre retain separate `/data/dsh` sessions; the coordinator service explicitly starts Python without an Agent. See [the harness guide](harness/README.md) for launch and provider configuration.

Local execution verified a real `read-evidence` result and official DSH session resume without repeating its notification or tool execution. That request was explicitly queued; autonomous native rescue-task selection, flight and radio execution remain unverified. Docker is unavailable on this host, so the integrated image has not been built.

See docs/guide.md and docs/STATUS.md for execution limitations. Packaging/import checks are not native acceptance.

## Explicit mock run

The mock backend uses the same kernel, adapter request/reply objects, task worker and radio transfer code. It substitutes kinematic flight, RGB-D scene rendering, parcel motion and packet delivery; native remains the default coordinator backend.

Prepare the local photo asset with `tools/prepare_mock_sprite.py` as described in `docs/mock-visual-asset.md`. With the installed Python dependencies, Harness dependencies (`cd harness && npm ci`) and existing runtime models, run from this directory:

```sh
python tools/run_mock.py --scenario scenarios/rooftop-flood-mock.yaml \
  --run-id C01-42-rule-attempt1 --seed 42 --stop-on-outcome \
  --model-cache /path/to/runtime-models --person-sprite /path/to/person-sprite.png
```

Each attempt gets a new directory under `runs/mock/`, five separate Cordis/DSH owners and per-agent histories. The summary separates process shutdown, task outcome and return completion. This source checkpoint does not mark the four-agent rescue acceptance complete; current evidence and remaining work are recorded separately in `docs/mock-report.md` when available.
