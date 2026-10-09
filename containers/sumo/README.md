# SUMO backend

Build SUMO 1.27.1/TraCI from the public digest-pinned Python base and vendored wheel hash lock:

~~~sh
containers/sumo/build.sh
docker run -d --name aas-p9-sumo --label aeroagentsim.job=p9 \
  --cpus 16 --memory 8g -p 127.0.0.1:19003:9000 aeroagentsim/sumo:standalone
python \
  containers/sumo/smoke.py --seconds 60 --repeats 2 --output /tmp/aas-p9/sumo-smoke
docker stop aas-p9-sumo
docker rm aas-p9-sumo
~~~

The generated 3×3 grid has two driving lanes per direction, sidewalks, walking
areas and five traffic lights. The explicitly selected inline scenario authors
200 vehicles and three pedestrians; it is test input, never substituted for
missing network data. Vehicle demand uses two laps around the perimeter with
seeded direction/start selection; one vehicle follows a known path for command
verification. smoke.py defaults to seed 7, 300 simulated seconds, 100 ms native
steps and RPC cadence, and two repetitions. Use --step-ms 1000 to measure 1 s
RPC barriers with the same 100 ms native integration. Full results go to JSON
reports and complete JSONL trajectories. The test asserts seven observed command
results, person/vehicle departures, explicit removal and bitwise trajectory equality.

For external scenarios, mount data read-only and select --config /data/example.sumocfg.
Files must exist inside the container, with configuration-relative references
preserved. No benchmark/session/evidence/token or AeroGraph dependency is loaded.

## TCP contract

Port 9000 (AAS_PORT) accepts one owning connection. A second connection is
closed while a world is owned. Every frame is one UTF-8 JSON object plus LF,
at most 8 MiB including LF. Send one request at a time:

~~~json
{"protocol":"aeroagentsim.sumo/v1","major":1,"minor":0,"id":1,"op":"hello","payload":{}}
~~~

Responses echo protocol, major, minor and ID, with exactly one result or
error:{code,message,state}. IDs strictly increase per connection. Reject
duplicates, nonfinite numbers, invalid UTF-8/surrogates, missing/extra fields and
invalid integer types. Integers retain exact precision. Nesting limit is 128;
decimal integer tokens are limited to 4096 digits.

Lifecycle is NEW → HELLO → RESET → READY → CLOSED. Reset runs once. A
protocol/runtime/timeout error taints the connection; only close is then legal.
Malformed envelopes without a trustworthy identity cause EOF. There is no
automatic retry. Deadlines in wall seconds: hello 10, reset 180, advance 30,
command 30, close 20; socket writes 10, owner idle 3600. Disconnect and shutdown
clean up only this connection's native process/private files. Failed cleanup is
reported/logged. A later connection creates a new world.

Hello reports executable version, supported actions/entity kinds, lockstep,
exact-stop/hold capabilities, 1 ms clock quantum and default 100 ms step length.
The active step is selected by reset, not inferred from hello.

Reset examples:

~~~json
{"seed":7,"step_length_ns":100000000,"scenario":{"kind":"grid","vehicles":200,"persons":3,"depart_interval_s":0.1}}
{"seed":7,"step_length_ns":100000000,"network":"/data/city.net.xml","routes":["/data/city.rou.xml"]}
{"seed":7,"step_length_ns":100000000,"config_file":"/data/city.sumocfg","options":{"time_to_teleport_s":300,"collision_action":"warn"}}
~~~

Choose exactly one scenario/network/config_file. Seeds are nonnegative signed
31-bit integers. Step length is 1 ms–1 s, in integral milliseconds. Options are
limited to the two shown keys; native begin/end/seed/step/port remain owned by
the service. Route errors are always fatal, overriding an external configuration
that asks SUMO to silently ignore them. Startup confirms TraCI version, exact zero frontier and native step.

Advance payload is {"to_sim_ns":100000000}. Targets must be monotone and
aligned to the native step. Every internal step confirms TraCI time exactly,
collects per-step events and observes command progress; the final boundary
returns all active entity samples and traffic lights. Equal-time advance holds
the world and leaves commands queued. Invalid/native-mismatched grants publish
no successful response. Large grants remain subject to the advance timeout.

Entities contain id, kind, native type, sim_ns, position.xy in SUMO
network metres, angle in SUMO degrees, speed in m/s, lane, edge, and a
native edge route. Vehicles additionally have route_id, route_index,
and native signal bitmask signals. Person routes are the current walking
stage; persons have no vehicle route ID/signal fields. Position.lon_lat is
included only for a geo-referenced network, via TraCI convertGeo. No implicit
ENU transform or absent entity state is invented.

Departed and arrived contain {id,kind,sim_ns}. Teleported_start and
teleported_end preserve native vehicle teleport lists. Removed records
explicitly commanded removal at its application frontier. Collisions returns
native collision records with participants, types, speeds, lane, lane position
and boundary time. These lists aggregate every native step within the grant;
they are separate from final active samples. SUMO's collision-action removal
must be interpreted from collision observations; it is not a commanded removal.
Pending_vehicle_ids is TraCI’s actual insertion queue, without invented active states.
Traffic lights expose ID, state, phase, program and next-switch time.

## Commands

Send command with {command_id,action,params}. IDs are unique nonempty strings
for the connection/epoch, including rejected submissions. Acceptance queues an
operation; it means neither native execution nor success. Positive advances
apply commands at native boundaries and return command_updates with
command_id, action, status, sim_ns, and native observation.

| Action | Params |
|---|---|
| set_speed | vehicle, nonnegative speed in m/s |
| reroute | vehicle, optional explicit edges |
| change_target | vehicle, destination edge |
| lane_restriction | lane, disallowed vehicle-class list, optional aligned at_sim_ns |
| tls_phase | tls, phase, optional positive duration_s |
| add_vehicle | new vehicle, existing route_id, optional type, numeric depart seconds |
| remove_vehicle | active vehicle |

Lane restrictions add to the native disallowed class set and retain existing bans.

See [platform report](../../docs/platform/sumo-backend.md) for exact observed
completion predicates and real validation numbers. The runtime contains no
command receipt/evidence/audit verifier. Contract tests run without Docker:

~~~sh
PYTHONDONTWRITEBYTECODE=1 python \
  -m pytest -p no:cacheprovider --basetemp=/tmp/aas-p3a/pytest containers/sumo/tests
~~~

This backend profile is designed for a future aerokernel lockstep adapter.
The adapter must wrap it in the kernel RPC envelope and provide partition,
invocation, cuts and frontiers; the backend itself does not claim kernel commits.

## Standalone build provenance

The Dockerfile has no AeroBench base image or repository dependency. Public bases,
verified source archives, vendored dependency locks/patches, build timings, image
sizes and real validation results are listed in
[the container build record](../../docs/platform/containers.md). APT-selected
artifact URLs/SHA-256 values and installed package versions are retained under
`/opt/aeroagentsim/build-inputs`; Python wheel selection is recorded alongside
its enforced hash lock. Build-only caches, wheels and compilers are excluded
from the runtime where a separate build stage is used.

`requirements.lock` is copied byte-for-byte from AeroBench `containers/sumo/`.
