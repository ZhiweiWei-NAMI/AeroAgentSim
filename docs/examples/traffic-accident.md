# Traffic-accident experiment

This experiment combines road motion, UAV motion, weather, model-assisted decisions and a shared event-chain runtime. A stopped pair of vehicles creates an incident. A reporter chooses a bypass and reports it. The coordinator solicits bids and dispatches an eligible UAV to photograph the scene. The UAV's upload completes the task.

Run it after [installation](../getting-started/install.md):

```bash
aeroagentsim demo traffic-accident
```

The command imports an editable Studio workspace and opens the console. Validate and run it in Studio. For a file-based run without the console, use `aeroagentsim demo traffic-accident --headless`.

## What is included

The committed [scenario](../../scenarios/demos/traffic-accident/scenario.yaml) contains 63 road vehicles, eight UAVs and a nonspatial coordinator, together with task and incident entities. The [registry snapshot](../../scenarios/demos/traffic-accident/registry.snapshot.json) and local [overlay](../../scenarios/demos/traffic-accident/registry.overlay.yaml) describe their types, fields, events and relations. The AeroGraph repository is optional.

The default decision profile uses [authored scripted replies](../../scenarios/demos/traffic-accident/fixtures/decisions.json). These are explicit experimental inputs, not historical or live model responses. The [lite city](../../scenarios/demos/traffic-accident/inputs/lite-city/scene.json) uses OSM-derived footprints and roads with procedural building, car and UAV geometry; see its [attribution](../../scenarios/demos/traffic-accident/inputs/lite-city/ATTRIBUTION.txt). No separately licensed mesh pack is required.

## Follow the simulated timeline

These times describe the shipped kinematic/scripted configuration. They are simulated seconds, not wall-clock durations; live decisions or changed physics can change them.

| Simulated time | Stage | What to inspect |
| --- | --- | --- |
| 0 s | Automatic routines begin | Road and UAV poses, active tasks and chain instances |
| 8 s | Accident activation | Incident state and its participant relations; actual road-owner stop commands |
| After 8 s | Detection, report and solicitation | Committed stopping/distance predicates, reporter route choice and agent records |
| About 15.07 s | Award committed | Bravo's current eligibility, reservation and task suspension; Alpha keeps its medical task |
| Before 49 s | Travel, hold and dwell | Physical command receipts and the three-second sampled stationary/position condition |
| About 49.07 s | Capture stored | The real Chromium-rendered PNG and its request association |
| About 49.27 s | Upload accepted | Separate acceptance event and completed capture chain |
| 90 s | Run horizon | Completed run status and journal available for replay |

Default road/air publication is 1 Hz. The shared message lag is 66,666,667 ns, so message times need not fall on whole seconds. On the current development machine, upload has been observed at roughly 27 seconds of wall time; hardware, rendering and inference affect that duration. Use the CLI output and journal for the times of your own experiment.

The decisions propose actions; runtime rules authorize them against current committed state. Alpha's task is noninterruptible. Bravo's patrol can be interrupted, but assignment still requires current eligibility and an available capture reservation. A proposed award cannot substitute for an owner receipt, and a stored photograph is distinct from an accepted upload.

## Who owns what

| Plugin / engine | Responsibility |
| --- | --- |
| `traffic_road_motion` / `road_motion` | Road vehicle pose, speed and road-command execution |
| `kinematic` / `air_motion` | UAV pose, velocity, motion execution and energy state |
| `environment` / `weather` | Weather forcing |
| `traffic_route_inputs` / `route_inputs` | Authored road and UAV route inputs |
| `traffic_assessment` / `traffic_assessment` | Computed feasibility and eligibility inputs |
| `behaviour` / `behaviour` | Incident/task state, relations, reservations and event-chain execution |
| `traffic_decisions` / `decisions` | Explicit scripted proposals; the live profile replaces decision production |
| `traffic_camera_capture` / `capture` | Camera rendering and stored capture events |
| `traffic_capture_bridge` / `capture_bridge` | Upload checks and acceptance |

All engines share one runtime. Field bindings in the scenario assign one writer to each state field. A domain replacement changes the binding/configuration and starts a new run; it cannot write alongside the existing owner.

## Change the experiment

In Studio, use the seven steps to change one factor at a time, validate and create a new run. For file-based experiments, copy the complete scenario directory to keep relative snapshot, behaviour and input references together.

- **Accident timing:** the [behaviour package](../../scenarios/demos/traffic-accident/behaviours.yaml) declares the accident timer and the named `accident` injection point. Change the timer for a scheduled experiment, or use [external ingress](../guides/external-events.md) for operator-controlled timing.
- **Motion resolution:** change both motion engines' `step_ns` deliberately. Check sampled dwell conditions and message lag when changing publication cadence.
- **Weather:** configure the environment engine and preserve its field ownership. Follow [Weather](../guides/weather.md).
- **Task policy:** edit eligibility predicates, relation selectors and guards in the package. Keep the Alpha/Bravo distinction explicit if it is a research factor.
- **Decision model:** follow [Deploy an agent](../guides/agents.md) and use `--profile live-llm` with configured model and credentials. Live inference currently blocks simulated time while producing a decision; replay uses recorded decisions without calling the provider.
- **Native physics:** the demo's SUMO/PX4 profiles are configuration contracts, not ready-to-run substitutions. Start from the working [SUMO](../guides/sumo.md) or [PX4/Gazebo](../guides/px4-gazebo.md) adapter examples and supply the declared mappings and services.

## Inspect and compare

In **Inspect**, select Bravo to follow its task relations and receipts; open the capture artifact after storage. Graph and 3D views use the same entity identity and selected journal cut. Nonspatial tasks and coordinators remain in the graph even when they have no 3D representation.

Use **Runs** to pause simulation, resume or stop cleanly. An input wait is visible as `waiting_for_input`; advance the declared stream rather than interpreting the wait as completion. Playback controls only move the viewing timeline.

Replay a completed run with `aeroagentsim replay <run-directory>`. Keep configuration changes in separate runs and compare their committed outcomes. Default lean provenance retains replay and semantic events; select `--provenance full` for deeper engine diagnostics. See [Runs and replay](../concepts/runs.md) and [Visualize and inspect](../guides/visualization.md).
