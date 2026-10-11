# Run a template and tour the console

After cloning and activating a Python environment, these three commands install the platform, build the console and open the parcel-delivery template:

```bash
python -m pip install -e ./aerokernel -e '.[server]'
(cd frontend && npm ci && npm run build)
aeroagentsim run-template parcel-delivery
```

See [Install](install.md) for Python and Node.js setup. The last command prints and opens a local URL on a free port with the project open in the workbench. Choose **Validate → Save version → Initialize → Start**. Neither AeroGraph nor a separate asset pack is required.

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

The console is one workbench: a full-screen 3D scene with hovering panels.

1. **Gallery** shows the nine ready-made worlds. A thumbnail opens its guide (what to observe, controls, KPIs); **Create a project** makes an editable draft.
2. **Configure** (C) edits the draft next to its 3D preview: template controls, the seven sections (space, objects, resources, tasks, algorithms, components, run) and the YAML file. **Validate** jumps to each issue; **Save version** freezes it; **Initialize** loads the world at t=0 without moving it.
3. **Start**, **Pause**, **Resume**, **Stop** and the simulation speed are in the top bar. The dock's playback only browses the record; viewing the past shows **Back to live**. `waiting_for_input` means a declared stream has not yet permitted the next simulation time.
4. Select a drone in the 3D scene, the object list or the **Graph** (G): the inspector shows its state, story and the commands you can send. **Why?** opens the cause → effect trace of an event.
5. **Records** (R) replays runs, compares 2–4 of them and derives a new draft (**Modify & retry**). The contract (AeroGraph) opens as an overlay from the palette (⌘K).

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

In the gallery, open **Restaurant meal delivery** and create a project. In
**Controls**, set **Run mode** to **Interactive**; configure kitchens, orders and
drones, then validate, save a version, initialize and start. Select a kitchen
and, in the inspector's **Operate** tab, send **Restaurant delay** with the
delay in seconds. Inspect
the admission receipt and the affected orders as preparation and delivery advance.
Simulation pause holds execution and source progress; replay playback and seeking
only change the shared viewing time. Refresh restores the real source watermark.

For reproducible unattended runs, keep **Scheduled** (the default):

```bash
aeroagentsim run-template food-delivery --headless
```

See the [food-delivery guide](../../scenarios/templates/food-delivery/README.md)
and [verified workbench delivery](../project/workbench-delivery.md).
