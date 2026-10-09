# Run the traffic-accident demo

After cloning and activating a Python environment, these three commands install the platform, build the console and start the demo:

```bash
python -m pip install -e ./aerokernel -e '.[server]'
(cd frontend && npm ci && npm run build)
aeroagentsim demo traffic-accident
```

See [Install](install.md) for Python setup and Chromium system dependencies. The demo prints and opens a local URL on a free port and imports an editable workspace. Neither AeroGraph nor a separate asset pack is required.

## Tour the console

1. **Home** provides demos and recent work.
2. **Studio** guides you through **Scene → Entities → Domain plugins → Predicates → Event chains & rules → Agents → Validate & run**. Review the imported traffic workspace, validate it and start a run.
3. **AeroGraph** browses types, fields and relations. Without an AeroGraph checkout, the catalog is limited to the scenario snapshot; the console identifies that scope.
4. **Runs** shows status and controls. Simulation pause stops time advancement; playback pause only stops the viewer. `waiting_for_input` means a declared stream has not yet permitted the next simulation time.
5. **Inspect** follows entities, relations, state and events in synchronized graph and 3D views. Select the responding UAV and inspect its task, command receipts and capture artifact.

The accident starts at simulated 8 seconds. A vehicle reports it, eligible UAVs bid, Bravo receives the assignment, captures a photograph and uploads it. Alpha continues its noninterruptible medical task. The run continues to its declared 90-second horizon. The [walkthrough](../examples/traffic-accident.md) explains the stages and how to change them.

The shipped profile uses explicit scripted replies. Live model inference requires configuration and is never selected automatically when scripted data is absent.

## Run without the console

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
