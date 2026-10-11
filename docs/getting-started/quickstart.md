# Run a template and tour the console

After cloning and activating a Python environment, these three commands install the platform, build the console and open the parcel-delivery template:

```bash
python -m pip install -e ./aerokernel -e '.[server]'
(cd frontend && npm ci && npm run build)
aeroagentsim run-template parcel-delivery
```

See [Install](install.md) for Python and Node.js setup. The last command prints and opens a local URL on a free port with an editable Studio workspace. Choose **Validate & run → Validate → Run now** to start. Neither AeroGraph nor a separate asset pack is required.

List all nine templates, or run parcel delivery directly in the terminal with a different battery reserve:

```bash
aeroagentsim templates
aeroagentsim run-template parcel-delivery --set battery_reserve=35 --headless
```

Each template declares its own knobs; see [Scenario templates](../guides/templates.md). For detailed buildings and display models, install the optional Huangpu city pack 1.1, then reload the viewer:

```bash
aeroagentsim assets fetch
```

This downloads release asset `huangpu-city-v1.1.zip` from tag `assets-huangpu-city-v1.1` outside the repository. The bundled OSM city works without it; [Install](install.md#optional-detailed-huangpu-city) covers mirrors and offline installation.

## Tour the console

1. **Home** opens saved workspaces and recent runs. **Scenario templates** provides everyday low-altitude templates and the traffic-accident demo. **Run now** uses the defaults, **Customize** changes named controls, and **Open in Studio** imports an editable city workspace.
2. **Studio** guides you through **Scene → Entities → Domain plugins → Predicates → Event chains & rules → Agents → Validate & run**. Review the imported workspace, validate it and start a run.
3. **AeroGraph** browses types, fields and relations. Without an AeroGraph checkout, the catalog is limited to the scenario snapshot; the console identifies that scope.
4. **Runs** shows status and controls. Simulation pause stops time advancement; playback pause only stops the viewer. `waiting_for_input` means a declared stream has not yet permitted the next simulation time.
5. **Inspect** follows entities, relations, state and events in synchronized graph and 3D views. Select a drone to see its task, battery and journey; use **Why?** or its status explanation to open the cause → effect trace. Simple view adds Director camera, cinema mode and Chinese/English switching.

## Try the traffic-accident demo

The traffic-accident and city-day scenarios capture a real image using Chromium. Install the [camera dependencies](install.md#camera-dependencies) first. To launch the guided traffic demo:

```bash
aeroagentsim demo traffic-accident
```

The accident starts at simulated 8 seconds. A vehicle reports it, eligible UAVs bid, Bravo receives the assignment, captures a photograph and uploads it. Alpha continues its noninterruptible medical task. The run continues to its declared 90-second horizon. The [walkthrough](../examples/traffic-accident.md) explains the stages and how to change them.

The shipped profile uses explicit scripted replies. Live model inference requires configuration and is never selected automatically when scripted data is absent.

## Run the traffic demo without the console

```bash
aeroagentsim demo traffic-accident --headless
```

This still uses the real Chromium camera. It prints accident, award, capture and upload times plus the journal path. Default runs collect lean provenance; add `--provenance full` to diagnose engine proposals and transactions.

Replay the run directory printed by the command:

```bash
aeroagentsim replay runs/demo/<run-id>
```

Replay calls no physics engine, camera or model provider. To inspect runs later:

```bash
aeroagentsim serve --out runs/demo --scenario-root . --frontend frontend/dist
```

`--profile sumo` and `--profile px4` currently describe required native configuration and report what remains to be supplied. Use `kinematic` for the shipped runnable demo, or the separate [SUMO](../guides/sumo.md) and [PX4/Gazebo](../guides/px4-gazebo.md) adapter scenarios. See [CLI](../reference/cli.md) for ports and output options.

## Intervene in food delivery

On Home, choose **Restaurant meal delivery → Customize**, set **Run mode** to
**Interactive**, and open it in Studio. Configure kitchens, orders and drones,
then save, validate and start. In the live workbench, choose **Inject event →
Restaurant delay**, select a kitchen and enter the delay in seconds. Inspect
the admission receipt and the affected orders as preparation and delivery advance.
Simulation pause holds execution and source progress; replay playback and seeking
only change the shared viewing time. Refresh restores the real source watermark.

For reproducible unattended runs, keep **Scheduled** (the default):

```bash
aeroagentsim run-template food-delivery --headless
```

See the [food-delivery guide](../../scenarios/templates/food-delivery/README.md)
and [verified workbench delivery](../project/workbench-delivery.md).
