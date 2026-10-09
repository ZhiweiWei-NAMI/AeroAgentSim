# Guide: PX4/Gazebo flight simulation

The `px4_gazebo` engine couples real PX4 SITL and Gazebo into the shared DES
kernel over an exact lockstep protocol. The adapter code is
[../../src/aeroagentsim/adapters/px4_gazebo.py](../../src/aeroagentsim/adapters/px4_gazebo.py);
the native service lives in
[../../containers/px4-gazebo/](../../containers/px4-gazebo/) (see its
[README](../../containers/px4-gazebo/README.md) for the wire protocol, action
parameters and completion predicates).

## What it does

- **Timing**: `Timing("lockstep", step_ns, certified_hold=True)`. The adapter
  advances Gazebo in fine `control_step_ns` increments (defaulting to
  `step_ns`) and publishes once at the granted kernel boundary; every advance
  must land exactly on the configured boundary. Native commands latch to the
  next boundary — they affect subsequent integration, never the current step.
- **Telemetry**: Gazebo pose/attitude keep source simulation timestamps;
  PX4 velocity, battery fraction, armed/mode/landed state come from MAVSDK
  streams and carry receipt ages, not confirmed source simulation times. That
  limitation is preserved in fact metadata, never papered over.
- **Actions**: `arm`, `takeoff`, `goto_location`, `hold`, `land`, `disarm`,
  exposed as commands `adapters.px4_gazebo.<action>`. Command acceptance only
  means queued; success requires observed PX4/Gazebo completion conditions
  (for example `takeoff` needs armed, IN_AIR, relative height within 1 m, and
  speed < 1.5 m/s held for one simulation second).

## Container setup

Build and start the standalone backend image (standalone service;
see [containers guide](containers.md)):

```sh
containers/px4-gazebo/build.sh
docker run -d --name aas-px4 --cpus 16 \
  --memory 16g -p 127.0.0.1:19001:9000 aeroagentsim/px4-gazebo:standalone
```

Reset includes native launch and warmup; each container owns one world and one connection. `close` and disconnect both clean
up the native world and leave the listener available for the next connection.
You can also use a manually started service by omitting `image` and setting
`host`/`port` yourself.

## Scenario wiring

A complete runnable example is
[../../scenarios/adapters/px4-flight.yaml](../../scenarios/adapters/px4-flight.yaml).
Its engine section (abridged):

```yaml
engines:
  flight:
    plugin: px4_gazebo
    config:
      image: aeroagentsim/px4-gazebo:standalone   # omit to connect manually
      host: 127.0.0.1
      port: 19001
      step_ns: 200000000          # communication/publication step
      clock_id: px4.observation   # MAVSDK boundary-observation clock
      pose_clock_id: gazebo.simulation
      pose_mapping_id: gazebo.warmup/v1
      mapping_id: px4.elapsed/v1
      event_topic: native-events
      vehicles:
        aircraft: u1              # kernel entity ID -> native vehicle ID
      fields:                     # semantic name -> declared registry field
        position: e1.px4.position
        velocity: e1.px4.velocity
        attitude: e1.px4.attitude
        battery: e1.px4.battery
        armed: e1.px4.armed
        mode: e1.px4.mode
        landed: e1.px4.landed
      reset:
        world: default            # or an absolute mounted SDF path
        warmup: 10000000000
        vehicles:
        - id: u1
          model: x500
          spawn: [0, 0, 0.2, 0, 0, 0]   # ENU metres + roll/pitch/yaw radians
clock_mappings:
- {clock_id: canonical, mapping_id: canonical}
- {clock_id: px4.observation, mapping_id: px4.elapsed/v1}
- {clock_id: gazebo.simulation, mapping_id: gazebo.warmup/v1, offset_ns: -10000000000}
```

Field IDs must exist in the scenario registry and be written by this partition
(`bindings.rules` with `writer: flight`). Position/velocity/attitude
descriptors must declare `metadata.frame: enu`. Semantic field names are
`position`, `velocity`, `attitude`, `battery` (`{remaining_fraction,
voltage_v}`), `armed`, `mode`, `landed` and optional `freshness`. The pinned
kernel root seed is passed to reset unchanged; a conflicting native reset seed
faults the run.

Run it with the adapter scenario runner:

```sh
python -m aeroagentsim.adapters.runner scenarios/adapters/px4-flight.yaml \
  --journal /tmp/px4-example.jsonl
```

This connects to the manually started service. Alternatively, skip `docker run` and add `--containers`; the runner then starts and owns the configured image. The runner verifies offline replay
after closing the backends.

## Commands from behaviours or agents

Declare the command schemas in your registry and grant them to a behaviour or
agent partition. Example command message:

```yaml
messages:
- id: adapters.px4_gazebo.goto_location
  kind: command
  schema:
    type: record
    members:
      position_enu: {type: vector, items: {type: number}, length: 3}
      yaw_deg: {type: number}
    required: [position_enu]
    extra: false
```

Frame mapping notes to keep straight:

- `position_enu` is local east/north/up metres, converted through the SDF
  WGS84 tangent origin. `yaw_deg` uses MAVSDK compass heading (0° north,
  clockwise), while spawn yaw uses ENU radians (0° east, counterclockwise) —
  do not mix them.
- `takeoff` altitude is relative to the PX4 home position. Vertical datum
  conversion is measured at reset against stationary pose; reset returns that
  offset. Declare an explicit local/geodetic origin when authoring fields.

## Known limits

- Only the `x500` model is validated; other catalogued airframes need
  instrumented contact topics (reset reports which are available).
- Contact events are received since the preceding result and can lag a
  boundary; an empty contact list does not certify absence.
- MAVSDK exposes no source simulation timestamps for its streams; the adapter
  uses a boundary-observation clock instead of a fabricated native time.
- PX4/Gazebo is not claimed bitwise deterministic.
- The demo scenario's
  [../../scenarios/demos/traffic-accident/profiles/px4.yaml](../../scenarios/demos/traffic-accident/profiles/px4.yaml)
  is a configuration contract only — it names the inputs a working PX4 profile
  must supply (service URL, vehicle mapping, origin/vertical datum, clock
  mapping, energy model) and is not a runnable replacement for the kinematic
  demo profile.

See also: [SUMO guide](sumo.md), [containers](containers.md),
[architecture concepts](../concepts/architecture.md).
