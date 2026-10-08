# E1 native engine adapters

`aeroagentsim.adapters` hosts PX4/Gazebo, SUMO and ns-3 through the independent
AeroKernel engine protocol. Each factory accepts the platform's `EngineBuild`;
entity types, field IDs, writer/lifecycle bindings and clock mappings come from
the scenario. The kernel package and other platform modules were not changed.
Runtime adapter code uses the standard library and AeroKernel; the scenario
runner also uses the platform's existing YAML loader.

## Run and registration

The delivered runner registers real `EntryPoint` objects in a local
`EngineCatalog.entries` collection. It performs no global catalog mutation.
Factories are `aeroagentsim.adapters.{px4_gazebo,sumo,ns3}:build`.

```python
from pathlib import Path
from aeroagentsim.adapters.runner import AdapterRun, observed_flight
from aeroagentsim.scenario import load_scenario

scenario = load_scenario(Path("scenarios/adapters/px4-flight.yaml"))
with AdapterRun(scenario, containers=True) as run:
    run.start()
    phases = observed_flight(run)  # arm → takeoff → goto → land, by terminal receipts
```

To connect existing services, use `containers=False` and configure `host`/`port`.
For managed services, `containers=True` uses the scenario's `image`.
`DockerContainer` can also be used directly with `environment`, read-only
`mounts`, and `startup_timeout_s`. It allows only the three E1 images, starts
containers with `--label aeroagentsim.job=e1 --cpus 8`, publishes a random
loopback port, checks readiness and verifies the owned ID/label before removal.
Healthchecks take precedence; these images otherwise use a passive `/proc/net/tcp`
listener check through `docker exec`. A TCP connection probe was removed after
real tests exposed a race with the services' single-owner connection lifecycle.

The checked-in scenarios use the existing `aeroagentsim.scenario/v1` schema.
`authored-registry.json` is an explicitly authored empty base snapshot; each YAML
adds its selected example types, typed fields and message schemas. These `e1:`
types are demonstration declarations, not claims of AeroGraph compilation or
source review. Regenerate them with `scenarios/adapters/generate.py`.

```bash
export E1_PY=/mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src:../aerokernel:/tmp/aas-e1/deps
"$E1_PY" -m aeroagentsim.adapters.runner scenarios/adapters/px4-flight.yaml \
  --containers --flight --journal /tmp/aas-e1/flight-example.jsonl
"$E1_PY" -m aeroagentsim.adapters.runner scenarios/adapters/sumo-grid.yaml \
  --containers --journal /tmp/aas-e1/traffic-example.jsonl
```

The supplied kernel venv lacks PyYAML. Gate runs used PyYAML and its typing stubs
installed exclusively in `/tmp/aas-e1/deps`; no shared environment was changed.
A normal platform installation already declares PyYAML. Journal paths must be
new files. The runner verifies offline replay after closing the backends.

## Timing, transport and publication

The shared `lockstep_base.py` uses each backend's native protocol
(`aeroagentsim.px4/v1`, `aeroagentsim.sumo/v1`, `aeroagentsim.ns3/v1`). Requests are
strict LF-terminated UTF-8 JSON objects with monotonically increasing IDs.
Duplicate keys, nonfinite numbers, malformed UTF-8, incomplete/oversized frames,
wrong IDs/versions, unsolicited trailing bytes, EOF and backend errors taint and
close the connection. Integer tokens retain arbitrary precision within the
kernel resource budget. Binary64 numeric behavior follows the kernel codec.
Each operation has a positive finite wall deadline, including fragmented reads;
stateful requests are never retried.

`hello` must advertise lockstep timing, exact stop, certified hold, the required
actions, a positive native quantum and a frame budget. PX4 also checks the model
and sensor catalog. The configured communication step must align to the native
quantum; reset confirms actual timing/origin. Every native advance must confirm
exactly the granted boundary. Early returns and overshoots fault the run.
Intermediate kernel boundaries certify holds and produce no invented samples.
Commands latch to the communication grid and are sent after physical integration
at that boundary, so they affect subsequent integration.

Outputs are assembled and validated before publication. Adapter faults become
stable `KernelError` faults; the kernel rejects the whole output wave. Dynamic
SUMO snapshots need additional lifecycle waves: validate the complete native
snapshot, commit new identities in a reaction, then commit fields/events/removals
in a later reaction at the same physical time. Local receipt causes are remapped
when splitting these waves. Initially discovered TLS identities use this same
path. Predeclared identities are created during bootstrap. Missing departure,
arrival/removal evidence, duplicate identities and stale samples fault instead
of being silently dropped. Native effects cannot be rolled back after a fault;
the committed journal prefix remains authoritative.

Facts use explicit source `Stamp`s and half-open validity with an authored hold
until a newer observation or entity removal. Events preserve native occurrence
in `Message.at` and source stamps, while the coordinator assigns availability
at the publication boundary/microstep. Payload `available_ns` records the
physical publication boundary; ns-3 also preserves `available_sim_ns`.

## Backend configuration and commands

All engines require `host`, `port`, `step_ns`, `clock_id`, `mapping_id`,
`event_topic` and a native `reset` configuration. Optional `timeouts_s` overrides
operation deadlines. The pinned kernel root seed is passed unchanged to reset;
a conflicting reset seed faults. ns-3 requires its native nonzero uint32 seed.

| Backend | Additional configuration | Published observations |
| --- | --- | --- |
| PX4/Gazebo | `vehicles: {kernel_entity_id: native_vehicle_id}`, `fields: {semantic_name: field_id}`, `pose_clock_id`, `pose_mapping_id` | Position, velocity, attitude, battery, armed, mode, landed; optional freshness; native contacts |
| SUMO | `kinds.vehicle/person/tls`, each with `type_id`, `prefix`, `fields` | Dynamic vehicles/persons; TLS state/phase/program/next switch; departure, arrival, explicit removal, teleport and collision events |
| ns-3 | `consumer_lag_ns`, `mobility: [{entity, field, node}]`, explicitly configured reset nodes | Delivery/drop outcomes and native link statistics |

PX4 semantic field names are `position`, `velocity`, `attitude`, `battery`,
`armed`, `mode`, `landed`, `freshness`. Spatial descriptors must declare `enu`.
Position/velocity are three-vectors; attitude is Gazebo `[x,y,z,w]` quaternion.
Battery is `{remaining_fraction, voltage_v}`. Gazebo pose/attitude/contact stamps
retain the absolute native clock; a pinned offset subtracts confirmed warmup.
MAVSDK exposes cached telemetry with unmapped native acquisition time. Those
facts use a separate boundary-observation clock, not a fabricated native source
time; the optional freshness field retains `mavsdk_source_sim_ns: null`, receipt
ages and versions. The examples' descriptor metadata explicitly states this
observation policy. Field IDs can be any compatible declared registry IDs.

SUMO sample field mappings select native `position`, `angle`, `speed`, `lane`,
`edge`, `type`, `route`, and vehicle-specific `route_id`, `route_index`, `signals`.
TLS fields are `state`, `phase`, `program`, `next_switch_s` (native seconds).
Identity is `prefix + native_id` with a new kernel generation on reuse. Arrival
and explicit removal remain distinct events. SUMO network XY stays in its
native frame; no unconfigured XY-to-ENU or zero-altitude conversion is performed.

ns-3 mobility fields must declare ENU. Lag must be positive and at least one
radio communication step. At a grant ending at `t`, the adapter reads actual
kernel facts at `t - lag` and applies their coordinates at the beginning of the
native interval. Their fact versions become recorded causes. Initial intervals
retain the explicitly authored reset positions. A missing lagged fact faults;
it never becomes `[0,0,0]`. The coupled example feeds actual UAV pose and declares
a stationary ground radio node at `[20,0,0]`; SUMO traffic runs concurrently.

`schemas.py` supplies portable closed record schemas. Command IDs have prefix
`adapters.<backend>.`; action selection comes from the schema ID. Payloads are
flattened:

| Backend | Action | Required payload |
| --- | --- | --- |
| PX4/Gazebo | `arm`, `hold`, `land`, `disarm` | `entity` |
| PX4/Gazebo | `takeoff` | `entity`, `altitude_m` |
| PX4/Gazebo | `goto` | `entity`, `position_enu`, `yaw_deg` |
| SUMO | `set_speed` | `vehicle`, `speed` |
| SUMO | `reroute` | `vehicle` (optional `edges`) |
| SUMO | `change_target` | `vehicle`, `edge` |
| SUMO | `lane_restriction` | `lane`, `disallowed` (optional `at_sim_ns`) |
| SUMO | `tls_phase` | `tls`, `phase` (optional `duration_s`) |
| SUMO | `add_vehicle` | `vehicle`, `route_id` (optional `type`, `depart`) |
| SUMO | `remove_vehicle` | `vehicle` |
| ns-3 | `send` | `packet_id`, `src`, `dst`, `size`, `payload_ref` (optional `lifetime_ns`) |

PX4 `goto` maps to native `goto_location`. PX4/SUMO acceptance is queued work;
execution and terminal receipts derive from subsequent backend state/readback
updates with matching command identity. ns-3 acceptance follows an actual
native SEND; completion/failure follows a delivery/drop, retaining occurrence
and availability times. A transport acknowledgment never completes an action.
Cancellation is not advertised by these native command schemas.

## Verification and measured results

Run from the platform workspace with the environment above:

```bash
"$E1_PY" -m pytest tests/adapters -p no:cacheprovider --basetemp=/tmp/aas-e1/unit-gate
"$E1_PY" -m pytest tests/adapters -m docker -s -p no:cacheprovider \
  --basetemp=/tmp/aas-e1/docker-gate
"$E1_PY" -m ruff check --no-cache src/aeroagentsim/adapters tests/adapters scenarios/adapters/generate.py
"$E1_PY" -m ruff format --no-cache --check src/aeroagentsim/adapters tests/adapters scenarios/adapters/generate.py
MYPYPATH=../aerokernel "$E1_PY" -m mypy --strict --cache-dir=/tmp/aas-e1/mypy src/aeroagentsim/adapters
```

Docker tests are skipped by default; `-m docker` enables them. Tests use real
local TCP fake peers for framing/deadlines, mapped fields, command latching,
terminal receipts, lifecycle reuse and short-lived entities, consumer lag,
occurrence/availability separation and invalid final records without partial
publication. Five parallel WorkBuddy GLM sessions supplied independent protocol
and kernel reviews, schema tables, container fixtures and transport tests. Their
actual scratch reports/outputs were checked and integrated; all reported gates
below were run by the coordinating agent using the required kernel interpreter.

Final gates: **132 unit tests passed, 3 Docker tests skipped by default**;
**3 real Docker integration tests passed**; ruff check and format check passed
(17 files); **mypy --strict passed on all 8 adapter modules**. All tests used
the mandated Python 3.11.12 interpreter.

Real measurements on 2026-10-08, seed 42, 8 CPUs per container:

| Run | Simulated s | Startup s | Integration wall s | RTF | Replay |
| --- | ---: | ---: | ---: | ---: | --- |
| px4-flight | 28.2 | 13.303 | 5.688 | 4.957 | passed |
| sumo-grid | 60.0 | 2.239 | 25.643 | 2.340 | passed |
| coupled | 30.0 | 16.882 | 18.897 | 1.588 | passed |

RTF is simulated seconds divided by wall time after reset/startup, including
kernel coordination and journal append/flush. It excludes container startup,
warmup, cleanup and offline replay. These are single measured runs, not a
numerical repeatability claim. Replay runs after backend removal and compares
all entity lifecycles, fact histories and messages against the live prefix.
Full measurement values are in `tests/adapters/measurements.json`.

The flight reached observed arm, 5 m takeoff, ENU `[8,0,5]` goto and ground/disarmed
landing. SUMO ran 60 s with 50 vehicle identities and 3 authored pedestrians;
its reroute at 5 s succeeded by native route readback. The coupled 30 s run
armed/took off the UAV, ran 50 SUMO vehicles, consumed UAV position with 200 ms
lag, delivered 29/29 packets with 0 drops and recorded 150 link samples. Delivery
occurrences preceded availability, and link distance changed with observed UAV
motion. Every journal passed offline replay.

Image IDs used (images were neither rebuilt nor retagged):

- `aeroagentsim/px4-gazebo:dev-p2b`: `sha256:14dbec72f4acbdcff604f3ee3c3adc29eef80ba8689730a046fc269767040446`
- `aeroagentsim/sumo:dev-p3a-5`: `sha256:170cfbb03b0096043aaf7ff034c1ed4a05a2f26c73bc19c0809f019b59494c62`
- `aeroagentsim/ns3:dev-p4b`: `sha256:50bc201107afb8875d3879fd94a0c41c97f94c079c3738a7d9f87bab945ae57c`

The initial real attempts exposed the readiness/ownership race and undeclared
TLS bootstrap creation; both were repaired. One coupled attempt loaded adapter
modules while a result-validator edit was in progress; the final Docker gate
ran from a fresh interpreter after source changes finished. Only passing final
measurements are presented above. All owned E1 containers were removed.

## Platform requests

The current platform `Simulation` creates its catalog internally. The delivered
`AdapterRun` works through the public catalog API immediately. To enable these
plugins through ordinary platform CLI/RunSession construction, the orchestrator
should add the following distribution metadata outside E1's write scope:

```toml
[project.entry-points."aeroagentsim.engines"]
px4_gazebo = "aeroagentsim.adapters.px4_gazebo:build"
sumo = "aeroagentsim.adapters.sumo:build"
ns3 = "aeroagentsim.adapters.ns3:build"
```

Alternatively, platform construction can accept an explicitly supplied catalog
and call `adapters.register(catalog)`. No changes to platform/scenario/engines/
services were made in E1. Domain-specific cancellation and additional telemetry
need native protocol support before those capabilities can be advertised.
