# Guide: SUMO road traffic

The `sumo` engine runs real SUMO/TraCI in exact lockstep with the kernel. The
adapter is
[../../src/aeroagentsim/adapters/sumo.py](../../src/aeroagentsim/adapters/sumo.py);
the backend service and its wire contract are documented in
[../../containers/sumo/README.md](../../containers/sumo/README.md).

## What it does

- **Timing**: lockstep `Timing` with a positive `step_ns`; every internal SUMO
  step must reach exactly the expected native frontier. A positive advance
  integrates; an equal-time advance holds native time and leaves queued
  commands unapplied.
- **Observations**: vehicle/person samples (native `position`, `angle`,
  `speed`, `lane`, `edge`, `type`, `route`, `route_id`, `route_index`,
  `signals`) and traffic-light state (`state`, `phase`, `program`,
  `next_switch_s`), plus departure, arrival, explicit removal, teleport and
  collision events. Samples carry native occurrence times; availability is the
  kernel publication boundary.
- **Commands** (`adapters.sumo.<action>`): `set_speed`, `reroute`,
  `change_target`, `lane_restriction`, `tls_phase`, `add_vehicle`,
  `remove_vehicle`.
- **Dynamic identities**: vehicles and persons discovered at runtime are
  created in one reaction, then their fields/events/removals commit in a later
  reaction at the same physical time. Missing departure or arrival/removal
  evidence, duplicate identities and stale samples fault the run.

## Container setup

```sh
containers/sumo/build.sh
docker run -d --name aas-sumo --cpus 16 \
  --memory 8g -p 127.0.0.1:19002:9000 aeroagentsim/sumo:standalone
```

Reset accepts either a generated test grid or real data. For your own network,
mount data read-only and point reset at a configuration inside the container:

```json
{"scenario": {"kind": "config", "path": "/data/example.sumocfg"}}
```

Configuration-relative references must resolve inside the container. The
service forces `ignore-route-errors=false`: a failed native input or clock
read faults the connection rather than being skipped.

## Scenario wiring

Complete runnable example:
[../../scenarios/adapters/sumo-grid.yaml](../../scenarios/adapters/sumo-grid.yaml).
Its engine section (abridged):

```yaml
engines:
  traffic:
    plugin: sumo
    config:
      image: aeroagentsim/sumo:standalone   # omit to connect manually
      host: 127.0.0.1
      port: 19002
      step_ns: 100000000
      clock_id: sumo.simulation
      mapping_id: sumo.elapsed/v1
      event_topic: native-events
      kinds:
        vehicle:
          type_id: e1:RoadVehicle
          prefix: vehicle/                  # kernel entity IDs are prefix+native id
          fields:
            position: e1.sumo.vehicle.position   # native 'position' -> field
            speed: e1.sumo.vehicle.speed
            route: e1.sumo.vehicle.route
        person:
          type_id: e1:Person
          prefix: person/
          fields:
            position: e1.sumo.person.position
            speed: e1.sumo.person.speed
        tls:
          type_id: e1:TrafficLight
          prefix: tls/
          fields:
            state: e1.sumo.tls.state
            phase: e1.sumo.tls.phase
            program: e1.sumo.tls.program
      reset:
        scenario:
          kind: grid          # generated test grid; use kind: config for data
          vehicles: 50
          persons: 3
          depart_interval_s: 0.1
clock_mappings:
- {clock_id: canonical, mapping_id: canonical}
- {clock_id: sumo.simulation, mapping_id: sumo.elapsed/v1}
run:
  seed: 42
  until_ns: 60000000000
  advance_ns: 1000000000
```

Each `kinds.<kind>` entry needs `type_id`, `prefix` and a `fields` mapping from
native sample names to declared registry field IDs. Mapped fields must be
written by this partition (`bindings.rules` with `writer: traffic`).

Run against the manually started service:

```bash
python -m aeroagentsim.adapters.runner scenarios/adapters/sumo-grid.yaml --journal /tmp/sumo-example.jsonl
```

Alternatively, skip `docker run` and add `--containers` to let the runner manage the image.

## Frame mapping

Native SUMO positions are **network XY metres**. Declare that frame honestly:

```yaml
- id: e1.sumo.vehicle.position
  type: e1:RoadVehicle
  schema:
    type: record
    members: {xy: {type: vector, items: {type: number}, length: 3}}
    required: [xy]
    extra: false
  metadata:
    role: observation
    frame: sumo-network-xy
```

Do not present XY as ENU. Converting to a local ENU frame is a separate,
explicitly authored step with its own offset/rotation writer; the demo
scenario's
[../../scenarios/demos/traffic-accident/profiles/sumo.yaml](../../scenarios/demos/traffic-accident/profiles/sumo.yaml)
names this as a distinct `road_conversion` owner over the `traffic.road.*` ENU
fields. That profile is a configuration contract, not a runnable replacement:
the current SUMO adapter publishes native XY samples and does not implement
the ENU road converter.

## Commands

Sumo command schemas follow the pattern `adapters.sumo.<action>` with typed
payloads (for example `adapters.sumo.set_speed` takes a vehicle selector and a
speed). Grant them to a behaviour or agent engine through
`grants.commands` / behaviour `capabilities` entries like:

```yaml
capabilities:
  traffic.road.stop:
    schema: adapters.sumo.set_speed
    target: traffic
```

A command result is a real observed terminal update, not the transport
acknowledgement; check receipts in your chain or agent before acting on the
effect.

## Known limits

- Native `position`/`angle` are XY; no built-in ENU conversion is shipped.
- The standalone grid is authored test input, not a substitute for real
  network data.
- SUMO step length must equal the adapter `step_ns`; a mismatch faults reset.

See also: [PX4/Gazebo guide](px4-gazebo.md), the ns-3 backend
[README](../../containers/ns3/README.md) for radio coupling in the same
lockstep pattern, and [containers](containers.md).
