# SUMO container backend

P3a supplies a general native traffic backend under AeroAgentSim. It extracts
process/TraCI ownership, exact lockstep integration, live vehicles/persons,
traffic lights and scheduled restrictions from the read-only AeroBench
SUMO monolith. It has no workload/ResolvedScenario, session token, benchmark,
receipt, evidence writer, verifier or artifact-plumbing dependency. It does not
depend on AeroGraph or aerokernel at runtime.

The deliverable is [containers/sumo](../../containers/sumo/README.md).
The final image is **aeroagentsim/sumo:dev-p3a-5**,
ID sha256:170cfbb03b0096043aaf7ff034c1ed4a05a2f26c73bc19c0809f019b59494c62.
Its size is 854,170,017 bytes; the base is 853,959,335 bytes. The slim service and
generated grid add 210,682 bytes, while inherited image layers remain present. It inherits the specified
local traffic image with SUMO 1.27.1 and its existing TraCI wheels; no native
binary or wheel was rebuilt. The service remains non-root. Host checks use the
requested aerokernel Python 3.11 interpreter; the inherited container interpreter
is Python 3.12.11. The service's own imports are stdlib apart from native TraCI
loaded only for a running world.

## Responsibilities and input semantics

| AeroBench source responsibility | Slim service |
|---|---|
| _start_runtime around 2138; native process/TraCI lifetime | service/runtime.py |
| simulationStep / time barriers around 2313–2390 | Runtime.advance, confirmed native time after every internal step |
| _live_entity_state around 1632 | Vehicle/person native subscriptions and current-stage person routes |
| traffic-light projection | Native phase/state/program/next-switch observations |
| scheduled lane restrictions/reroutes around 1973 | service/commands.py, queued boundary application and observed terminal effects |
| New transport/input contract | service/wire.py, service/rpc.py, service/scenario.py |

All service modules remain below 600 lines. The container build uses netgenerate
to author a 3×3 grid with 200 m links, two driving lanes per direction,
sidewalks/walking areas and five traffic lights. Inline demand is selected
explicitly: 200 initial vehicles, three persons, 0.1 s scheduled-departure spacing,
seeded perimeter routes. It is authored test input, not a replacement for missing
user data. Reset also accepts existing network/route paths or a SUMO configuration
inside a read-only data mount.

The service owns seed, step length, begin/end and TraCI port. It forces
ignore-route-errors=false, including when a legacy configuration asks SUMO to
skip invalid routes. A failed native input/clock/read faults the connection;
there is no zero/null substitution, synthetic success or stateful retry.
Startup retries only the initial local TraCI connection before any native
simulation request. Cold-start measurement retries only the stateless hello
while Docker's port proxy becomes ready.

## RPC and future kernel adapter

The profile is **aeroagentsim.sumo/v1**, major 1/minor 0, strict UTF-8 LF JSON
over TCP, one world owner and one in-flight request. Frames have an LF-inclusive
8 MiB limit, 128-level nesting budget and 4096-digit integer budget. IDs are exact
positive integers, strictly increasing per connection. Duplicate keys,
nonfinite/exponent-overflow numbers, invalid UTF-8/surrogates, CRLF, incomplete
frames and unknown fields fail. Responses echo identity with exactly one result
or structured error. The [README](../../containers/sumo/README.md) has schemas.

Lifecycle is NEW→HELLO→RESET→READY→CLOSED; a fault enters TAINTED and permits
cleanup only. Reset runs once. Deadlines are hello 10 s, reset 180 s, advance/
command 30 s and close 20 s; idle timeout is 3600 s. Native sockets are bounded
to 25 s and integration checks a 29 s overall deadline. Timeout/disconnect
requests cooperative cancellation; the single native worker serializes cleanup
behind the interrupted operation. Process termination/kill and private-directory
cleanup belong only to the owning connection. Cleanup errors are reported. Native termination returns NativeFailure with the
actual tail of the captured SUMO log (at most 16 KiB), preserving its cause before
private runtime files are removed.

A positive advance accepts only a monotone, aligned integer target. Every
internal SUMO step must reach exactly the expected native frontier. Events and
command observations are gathered at each native tick, even with a 1 s RPC grant;
entity/TLS samples represent the final boundary. Equal-time advances integrate
nothing and leave queued commands unapplied.

The later aerokernel adapter must wrap this backend in DESIGN.md §6's
aerokernel.rpc envelope and add partition/invocation identity, expected native/
logical frontiers and transaction/read/native-input cuts. It must validate the
whole result before publication, never retry a stateful request, and fault on
EOF, timeout or frontier mismatch. This service does not implement kernel
transactions or claim external exactly-once recovery.

## Observations and command completion

Entities are active native objects only. Samples retain SUMO network xy metres,
native compass angle, speed, lane/edge, type and boundary sim_ns. Vehicles add
native route ID, edge route/index and signal bitmask. Persons add their current
stage edges; vehicle-only fields are omitted. TraCI supplies lon/lat only for a
geo-referenced network. No inferred ENU frame or last-known ghost entity is returned.

Departure, arrival, teleport start/end, native collision records and explicit
commanded removal are distinct lists with source boundary time. All internal
tick events survive a larger grant. The native pending-vehicle insertion queue
is returned separately, without fabricated active samples. Collision-action
removal is represented by native collision observations, not mislabeled as a
commanded removal.

Command acceptance only reserves an ID and queues work. Commands apply at the
next positive advance boundary, or the exact scheduled restriction boundary.
An executing update marks native application; terminal success requires a
later tick's readback. Application errors and disappeared targets fail the
command; transport/native frontier faults taint the whole connection.

| Action | Later native completion predicate |
|---|---|
| set_speed | Observed speed within 0.05 m/s of requested speed; safety constraints can prevent completion |
| reroute | Observed native route equals the route resulting from setRoute or rerouteTraveltime; before/after routes are preserved |
| change_target | Observed route ends at requested destination |
| lane_restriction | Observed disallowed set equals prior native bans union requested classes |
| tls_phase | Observed phase equals requested phase; optional duration sets its native remaining duration |
| add_vehicle | New vehicle is observed active after native insertion |
| remove_vehicle | Explicit native remove applied, followed by observed absence; separate removal event retains application time |

The effect deadline is 30 simulated seconds after due/application eligibility,
or scheduled departure for add_vehicle. State that cannot achieve a requested
speed/phase under native constraints is never declared successful merely from
ACK. An automatic reroute may legitimately retain the same optimal route;
readback is reported and no route change is invented. The smoke uses an explicit
connected detour and verifies it differs from the original route.

## Real grid validation — 2026-10-08

All runs use label aeroagentsim.job=p3a, --cpus 8, --memory 8g, seed 7,
200 initial vehicles plus three persons, and 100 ms native steps. Every run
integrates 300 s. At 1 s RPC cadence, each grant still performs ten native
100 ms steps. There are no sleeps to mimic real time.

Both cadence measurements below use the final **dev-p3a-5** image, including
strict fatal route-error handling and native error diagnostics. Images were
built under new tags; existing images were not modified.

| RPC cadence / repeat | Sim / measured wall s | RTF | RPC median / p95 / max ms | Reset RPC s |
|---|---:|---:|---:|---:|
| 100 ms / 1 | 300 / 112.796 | 2.660× | 33.669 / 39.082 / 50.624 | 0.549 |
| 100 ms / 2 | 300 / 113.531 | 2.642× | 34.007 / 39.376 / 59.717 | 0.545 |
| 1 s / 1, final image | 300 / 65.213 | 4.600× | 219.185 / 246.261 / 263.299 | 0.571 |
| 1 s / 2, final image | 300 / 64.673 | 4.639× | 219.554 / 244.419 / 273.907 | 0.551 |

Cold startup on the final image was **1.505 s to hello** and **2.058 s to reset
complete**, measured from immediately before docker run. Reset RPC took 0.553 s;
this includes native launch/TraCI readiness at exact zero, not vehicle insertion.
Hello RPC took 0.515 s.

RTF includes all advance and command RPCs, client JSON parsing, canonical full
trajectory serialization and disk writes; it excludes connection/hello/reset/
close and final file comparison. Advance latency is send-to-complete-response,
including network/serialization, without trajectory disk write. These are
local TCP measurements at the stated resource limits, not universal capacity
claims.

Both 100 ms runs observed peak 197 active vehicles and three persons; 201 total
departures comprise 197 initial vehicles, one added vehicle and three persons.
Two persons arrived, the added vehicle was explicitly removed, and no native
teleports or collisions occurred. Three initial vehicles remained in SUMO's
insertion queue at 300 s: v185, v189, v197. The client verifies departed initial
IDs union actual pending IDs equals all 200 authored IDs, with disjoint sets.
It does not equate scheduled departure with actual insertion.

Every run exercises reroute, set_speed, scheduled lane_restriction, tls_phase,
add_vehicle, change_target and remove_vehicle. Held advances preserve state and
do not apply queued commands. Terminal observations are reported in the machine
measurement files; command ACK is not used as an effect test.


Observed terminal times in the first 100 ms run (source boundary seconds):

| Action | Sim s | Result |
|---|---:|---|
| reroute | 0.2 | succeeded |
| set_speed | 1.3 | succeeded |
| lane_restriction | 2.2 | succeeded |
| tls_phase | 0.2 | succeeded |
| add_vehicle | 0.2 | succeeded |
| change_target | 0.3 | succeeded |
| remove_vehicle | 0.3 | succeeded |

The two 100 ms full trajectory files each contain 3000 complete advance results
and are byte-identical. SHA-256:
c91b5fc21bafd777ab483e60281bb3080952728874fb4e903d6b66882937bc67.
Comparison covers every entity field, lifecycle/collision list, pending IDs,
traffic light and command update; wall diagnostics are excluded. The final-image 1 s runs each contain 300 advance results and are also
byte-identical, SHA-256:
d1d95916dd20b90b3a56f9921557b6783959de8b34e4146dcc823b98c0cc088e.
Different RPC cadences use different command application schedules and are not compared with
each other for determinism.

Machine summaries live in [validation](../../containers/sumo/validation/).
Full trajectories remain under /tmp/aas-p3a/results; they are not embedded as
hundreds of megabytes of repository test data.

## Contract verification and repository scenario

66 tests pass without Docker or TraCI, covering frame/UTF-8/duplicate/nonfinite/
version/integer/resource limits; owner/lifecycle/taint/timeout/no-retry behavior;
native frontier mismatch, exact internal steps and hold; queued acceptance,
ID reuse, integer/Decimal deadlines and malformed command parameters. Ruff
check/format pass. GLM workers supplied 16 literal framing fixtures and a
narrow arithmetic/type review; all output was reviewed and verified before use.

No sumo_berlin or sumo_wujiaochang configuration exists in this workspace,
including ignored files. A separate bundled OSM configuration exists at
frontend/public/data/traffic/sumocfg/osm_generated.sumocfg; its geographic bounds
are Beijing (116.385550–116.428678 E, 39.886865–39.918355 N), not Berlin or
Wujiaochang. Its native read-only validation is recorded below.

The bundled Beijing scenario was tested with the final image, seed 7, 100 ms
native steps, 1 s RPC grants, and a read-only mount of the existing four files.
It published valid results through **230 s**, observing **32 distinct vehicles,
32 departures and 14 arrivals**, with real geo-referenced lon/lat samples.
The next grant failed when SUMO loaded vehicle32: no connection exists between
700759986#0 and 614213874#0; SUMO reported “Vehicle 'vehicle32' has no valid route.”
The RPC returned NativeFailure/TAINTED with this original diagnosis and published
no partial result for that grant. No successful 300 s run or same-seed repeat is
claimed for this invalid source dataset. No source route/network was changed
and no invalid vehicle was silently skipped. Direct native SUMO execution with
the same fatal route-error policy independently reproduced the same error.
The [scenario record](../../containers/sumo/validation/repository-scenario.json)
contains the actual prefix counts and first geo sample.

All created containers were labeled, limited and removed. All new image tags
remain local. No existing image was removed/retagged/pushed/pruned, no other
container was stopped, and no git commit/branch/reset was run.

## Standalone build (P9)

`containers/sumo/Dockerfile` now uses public, digest-pinned bases and verified
source/dependency inputs. Build with `containers/sumo/build.sh`; the output tag
is `aeroagentsim/sumo:standalone`. No AeroBench image or checkout is required.
The existing backend service/native model is unchanged.

The final image is `sha256:7e3bdf8db7a7f1df726ef39c7df82e2cf887c414bb1d576bb5d02e10083f9530`, **860,715,317 bytes**.
Its existing real smoke client passed on this image; the three standalone Docker
adapter/replay tests also passed. Input hashes, command timings, RTF/repeatability,
build-network failure history and artifact links are recorded in
[containers.md](containers.md). Verification artifacts are in
`containers/sumo/standalone/`; the original dev-image measurements above remain
historical and are not substituted for standalone results.
