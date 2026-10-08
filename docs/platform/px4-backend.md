# PX4/Gazebo container backend

P2a extracts the real native flight stack into `containers/px4-gazebo/service`, underneath AeroAgentSim. It has no AeroBench workload, ResolvedScenario, session-token, inspection, urban, evidence-verifier or command-audit decoder dependency. The independent aerokernel remains general; a later host adapter binds these backend-specific fields/actions into the kernel's registry and lockstep timing contract.

The current runnable image is **`aeroagentsim/px4-gazebo:dev-p2b`**, ID `sha256:14dbec72f4acbdcff604f3ee3c3adc29eef80ba8689730a046fc269767040446`. P2a’s baseline remains `aeroagentsim/px4-gazebo:dev-p2a-ipc2`, ID `sha256:9a421a10219067220de9b337d3d9af018e43f5758bd77cdc18a4ecb0bad04ed0`; its measurements below are historical. Its base is the user-specified local flight image, inspected before use: PX4 `v1.17.0-alpha1-1551-g381149fb01`, Gazebo Harmonic `8.11.0`, patched MAVSDK `3.17.2`. No native binaries were rebuilt. The image retains the non-root user and native dependencies. Its reported size is 2,370,456,329 bytes: the service is slim, but inheriting the 2.37 GB base cannot remove those inherited layers.

## Extraction map

Ranges refer to the read-only [AeroBench service.py](/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim/aero-bench/containers/px4-gazebo/service.py:4701) inspected for this extraction. These are extracted responsibilities and adapted algorithms, not an unchanged copy of the monolith.

| Original lines / responsibility | New files |
|---|---|
| 1580–1760 launch/time/vehicle helpers; 6100–6191 PX4 and MAVSDK startup; 8055–8098 process ownership/environment | [processes.py](../../containers/px4-gazebo/service/processes.py), [world_control.py](../../containers/px4-gazebo/service/world_control.py) |
| 1892–2134 paused SDF, vehicle includes and world selection; scenario/inspection/airspace branches removed | [config.py](../../containers/px4-gazebo/service/config.py) |
| 4701–5053 reset/epoch/multi-step; 5575–5632 snapshot/cleanup; 5823–5902 native startup | [runtime.py](../../containers/px4-gazebo/service/runtime.py) |
| 5387–5501 telemetry projection; 6167–6421 connections, streams and decoding; 6592–6634 readiness | [telemetry.py](../../containers/px4-gazebo/service/telemetry.py) |
| 5513–5574 acceptance/application; 6635–6665 MAVSDK actions; benchmark tool IDs removed | [commands.py](../../containers/px4-gazebo/service/commands.py) |
| 5903–6099 contact subscriptions; 6741–6903 poses; 7035–7080 and 7185–7530 time/paused subscriptions; 7837–7969 transport/control | [transport.py](../../containers/px4-gazebo/service/transport.py), request-only child in `world_control.py` |
| New strict JSONL TCP profile and real host measurement client | [rpc.py](../../containers/px4-gazebo/service/rpc.py), [smoke.py](../../containers/px4-gazebo/smoke.py) |

Every service module is below 600 lines. The inherited MAVSDK patch requires `--command-audit-journal-path` as well as the heartbeat timeout; launch supplies a private temporary path. The slim service never reads, decodes, validates or exports that journal. Incoming heartbeat timeout and connection-idle timeout are both 3600 wall seconds, allowing paused agent decisions.

## Protocol and kernel boundary

See [container README](../../containers/px4-gazebo/README.md) for request shapes, action parameters, execution and cleanup. The native service uses `aeroagentsim.px4/v1` with major 1/minor 0, increasing connection-local request IDs, one world owner, one in-flight request, strict UTF-8 LF framing and an 8 MiB LF-inclusive budget. Duplicate keys, nonfinite numbers, invalid versions/IDs and incomplete frames fault the connection. Integers are preserved without binary64 conversion. Deadlines are hello 10 s, reset 180 s, advance/command 30 s, close 20 s; stateful requests are never resent.

Lifecycle is NEW→HELLO→RESET→READY→CLOSED; operation errors taint the connection and permit cleanup only. `close` and disconnect both clean up native processes, subscriptions and private runtime files; the listener remains available for a new connection. Positive advances integrate only an exact, aligned target; equal-time advances hold physics and leave pending commands queued. Integration is confirmed by real paused WorldStatistics and exact source-time Gazebo poses. Contacts retain native collision names and source timestamps.

Command acceptance means queued, not autopilot acceptance or success. MAVSDK execution begins at the next positive advance. Completion requires successful native action acknowledgment plus subsequent observations of every field used by the completion predicate; receipt age must be <=30 wall seconds. Flight goals require a one-simulation-second dwell. Terminal timestamps identify the first observation boundary satisfying the predicate, not an invented exact PX4 transition time. PX4 absolute altitude is mapped to world up using the actual stationary reset telemetry/pose pair; reset reports this offset. `yaw_deg` follows MAVSDK compass heading, while spawn yaw follows ENU radians.

The later host engine must wrap this profile with DESIGN.md §6's `aerokernel.rpc` envelope, partition/invocation identity, native/logical frontiers and input cuts. It must validate the exact grant before publishing and fault on timeout, EOF or frontier mismatch without retry or partial publication. This backend is not the kernel RPC adapter itself.

## Real validation on 2026-10-08

All runs used the built image, `--label aeroagentsim.job=p2a --cpus 8 --memory 16g`, seed 42, image `default` world, x500, 10 s native warmup, **4 ms physics steps and 200 ms agent/RPC barriers**. Single-vehicle runs repeat reset on fresh connections/processes in one container. The scaling run uses a separate container with three vehicles spawned at ENU north 0/8/16 m. Every vehicle arms, takes off to 10 m, goes to `[50,north,10]`, lands and is observed ON_GROUND/disarmed. No flight success is inferred from command acceptance.

Cold startup begins immediately before invoking `docker run`, including Docker creation/start overhead. Single vehicle: **0.570 s to hello**, **13.693 s to reset complete**; reset RPC itself 13.123 s. Three vehicles: **0.596 s to hello**, **15.692 s to reset complete**; reset RPC 15.097 s. Hello readiness means the TCP protocol is available; reset readiness includes native launch, warmup and telemetry.

| Run | Flight sim / wall seconds | Flight real-time factor | Goto 3D error (m) | Final landing horizontal error (m) |
|---|---:|---:|---:|---:|
| 1 vehicle, repeat 1 | 44.2 / 22.252 | 1.986× | 0.873 | 0.335 |
| 1 vehicle, repeat 2 | 45.2 / 22.744 | 1.987× | 0.778 | 0.362 |
| 1 vehicle, repeat 3 | 45.2 / 22.738 | 1.988× | 0.740 | 0.301 |
| 3 vehicles, one world | 45.2 / 22.762 | 1.986× | v0 0.735; v1 0.710; v2 0.725 | v0 0.354; v1 0.303; v2 0.339 |

RTF is flight simulation duration divided by wall duration of all four flight phases, including RPC/action overhead, excluding reset/warmup. At these limits, three vehicles increased reset time by about 2 s while maintaining throughput; this is one scaling run, not evidence about larger fleets. Median advance latency was 100.5–100.6 ms for one vehicle and 100.7 ms for three. A separate on-ground 20 ms cadence probe completed 100 exact advances: 2.000 sim seconds in 10.071 wall seconds (0.199×), median advance 100.7 ms, pose age 0 ns. That probe is not a flight RTF measurement; small RPC barriers expose the time-confirmation overhead.

Gazebo pose source age was **0 ns at every returned flight boundary**, across all runs. MAVSDK fields have receipt ages, not confirmed simulation acquisition times:

| Receipt age | 1 vehicle (largest p95/max across repeats) | 3 vehicles (p95/max, pooled across vehicles) |
|---|---:|---:|
| PX4 velocity | 78.3 / 80.0 ms | 64.4 / 67.1 ms |
| PX4 global position | 78.1 / 79.9 ms | 62.7 / 65.6 ms |
| Armed/mode/health/landed | 491.3 / 493.0 ms | 491.1 / 519.0 ms |
| Battery | 993.9 / 996.5 ms | 993.3 / 997.7 ms |

Same-seed trajectory comparison uses common 200 ms boundaries and real ENU positions: repeat 2 versus 1 RMS **0.109 m**, maximum **0.222 m**; repeat 3 versus 1 RMS **0.107 m**, maximum **0.277 m**, each over 221 shared vehicle samples. Positions were not bitwise identical. This is a closed-loop repeat with the same client policy; asynchronously observed completion can change subsequent command times. It does not isolate physics determinism under an identical fixed command schedule.

An additional real sequence verified arm→disarm→arm→takeoff→hold→goto→hold→land→disarm, including hold when already holding and disarm when already disarmed. All nine commands produced observed successful terminal updates. [measurements.json](../../containers/px4-gazebo/measurements.json) retains results, field-age summaries, action updates and final telemetry. Full flight traces and native logs are retained under `/tmp/aas-p2a/` on this workspace host. Fifteen contract tests passed, including strict framing/lossless times, rejected-action handling, stale observations, measured vertical datum and client cleanup; focused Ruff checks passed.

## Failures resolved and remaining limits

The initial native Python request on the subscriber node returned false after a 10 s timeout. The original per-call `gz service` path completed a flight to 50 m (0.868 m error) but later returned `Service call timed out` during landing; the failed trace is preserved as `/tmp/aas-p2a/single-cli-failure.json`. These failures were not counted as successful final runs. Two early reset integration errors—Contacts timestamp location and MAVSDK channel ownership—were fixed against the installed API. MAVSDK 3.17.2 reports battery remaining as 0–100 percent, which is explicitly converted to a fraction.

The P2a implementation uses a persistent request-only Python child, separate from subscriber callbacks. The Transport13 [blocking request binding](https://github.com/gazebosim/gz-transport/blob/gz-transport13/python/src/transport/_gz_transport_pybind11.cc#L217) retains the GIL; isolating requests avoids interference with Python subscription callbacks and removes per-call CLI startup/discovery. It sends once, then independently requires exact paused simulation time and pose confirmation. All final flight runs completed without control-call faults.

MAVSDK has no exact source simulation timestamps for the selected field streams; the service exposes that limitation explicitly. Contacts are received events and can lag a barrier; an empty list is not a proof of absence. Only x500 has been validated; other catalogued airframes require instrumented contact topics. Mounted worlds must supply ENU spherical coordinates and an explicit physics step. Bitwise determinism, larger-fleet capacity, camera payload transport and the host aerokernel adapter remain outside this verified slice.

## Independent verification note (orchestrator, 2026-10-08)

A separate one-vehicle rerun with the smoke client defaults (`--step-ms 20`) succeeded
(arm/takeoff/goto/land all reached observed terminal states; hello-to-reset 13.1 s) but
ran at about **0.2x** real time (221 s wall for ~44 s simulated flight) while the host was
shared with other jobs. The ~2x figures above apply to 200 ms barriers only. Per-barrier
overhead is therefore on the order of 100 ms wall; P2b below measures and removes it. The baseline already used a persistent requester;
per-call `gz service` startup was not the cause in that image.

## P2b barrier performance, 2026-10-08

The final image achieves **4.24–4.52× real time for one x500 at 20 ms barriers**, and **2.66–2.81× for three x500s at 20 ms**. Every final flight completed arm→takeoff to 10 m→goto `[50,north,10]`→land, with observed terminal results and final ON_GROUND/disarmed state. Physics remains 4 ms, with exact native WorldStatistics and pose confirmation at every returned boundary.

All measurements use the same local flight base, seed 42, default world, 10 s native warmup, `--cpus 8 --memory 16g`, and `AAS_PROFILE=1`. Each final cadence and fleet size has two fresh-process repeats. The host is shared with other jobs; these are measured runs, not isolated-host capacity guarantees. RTF includes all flight phase RPCs, command submission and host observation processing, excluding reset/warmup and output-file writing.

| Vehicles | Barrier | Repeat 1 sim / wall seconds; RTF | Repeat 2 sim / wall seconds; RTF | Median advance latency, repeats 1 / 2 |
|---|---:|---|---|---:|
| 1 | 4 ms | 44.004 / 34.045; **1.293×** | 44.004 / 34.477; **1.276×** | 2.999 / 3.014 ms |
| 1 | 20 ms | 45.020 / 10.609; **4.243×** | 44.020 / 9.741; **4.519×** | 4.386 / 4.160 ms |
| 1 | 100 ms | 44.100 / 5.142; **8.577×** | 45.100 / 4.839; **9.320×** | 10.382 / 9.644 ms |
| 1 | 200 ms | 45.200 / 4.686; **9.646×** | 45.200 / 4.098; **11.030×** | 18.760 / 16.301 ms |
| 3 | 20 ms | 44.020 / 16.533; **2.663×** | 44.020 / 15.656; **2.812×** | 7.075 / 6.452 ms |

Across these ten final flights, Gazebo pose source age was **0 ns for every returned vehicle sample**. Largest observed terminal goto error was **0.886 m**, and largest final horizontal landing error **0.395 m**. MAVSDK fields remain real cached stream observations with exposed wall receipt ages and an unmapped source simulation timestamp; faster barriers do not turn them into synchronous physics samples. The original acknowledgment-plus-fresh-observation completion predicates and dwell times are unchanged.

### Matched before/after probes

These are stationary on-ground probes, separate from the flight table: two fresh resets, then 60 consecutive advances at each cadence in the same order. Both versions require exact source-time poses. Ground contacts make this workload different from flight.

| Barrier | P2a RTF, repeats 1 / 2 | P2b RTF, repeats 1 / 2 | P2a median RPC, repeats 1 / 2 | P2b median RPC, repeats 1 / 2 |
|---|---:|---:|---:|---:|
| 4 ms | 0.0397 / 0.0397× | 1.1926 / 1.1773× | 100.735 / 100.587 ms | 3.252 / 3.251 ms |
| 20 ms | 0.1986 / 0.1985× | 3.6205 / 3.5283× | 100.662 / 100.585 ms | 5.208 / 5.447 ms |
| 100 ms | 0.9939 / 0.9932× | 5.8812 / 5.8570× | 100.660 / 100.534 ms | 16.795 / 17.140 ms |
| 200 ms | 1.9853 / 1.9885× | 6.7952 / 6.9088× | 100.691 / 100.573 ms | 29.598 / 28.680 ms |

### End-to-end 20 ms barrier profile

Durations below are measured wall milliseconds. Runtime phases use `perf_counter_ns`; encoding/drain/total RPC come from server logs, and RTT comes from the host client. Before uses a separate two-repeat 20 ms probe; after uses the two matched ground probes above. Ranges are the two per-run medians, except the before encode/drain/RPC total, which are pooled medians over 120 barriers. Runtime total and RPC total contain the component phases; component medians should not be summed.

| Span | Before | After |
|---|---:|---:|
| Strict RPC decode/validation | 0.078–0.080 | 0.053–0.060 |
| Command dispatch | 10.150–10.151 | 0.0015–0.0017 |
| SDK simulation heartbeat release/check | absent | 0.0015–0.0017 |
| Persistent WorldControl request + acknowledgment | 0.667–0.676 | 0.441–0.483 |
| Integration + exact paused WorldStatistics confirmation | 88.711–88.789 | 3.969–4.162 |
| Exact pose timestamp confirmation | 0.015–0.016 | 0.164–0.210 |
| Process check, pose/cache projection | 0.037–0.038 | 0.024–0.026 |
| Observed command evaluation | 0.004–0.005 | 0.0027–0.0030 |
| Contact drain/time mapping | 0.009–0.011 | 0.0063–0.0067 |
| Response JSON encoding | 0.134 | 0.112–0.117 |
| Response socket drain | 0.101 | 0.069–0.080 |
| Runtime total | 99.676–99.786 | 4.589–4.868 |
| Server RPC total | 100.180 | 4.894–5.115 |
| Client RTT | 100.598–100.672 | 5.208–5.447 |

The dominant P2a cost was waiting for the **10 Hz namespaced WorldStatistics publisher**, not parsing, JSON size or subprocess creation. The persistent request-only child was already sub-millisecond and remains isolated from subscription callbacks because the installed blocking Transport13 binding retains the GIL. Gazebo publishes the same native WorldStatistics unthrottled on `/stats` within the backend’s unique, single-world partition. [Gazebo 8.11 SimulationRunner source](https://github.com/gazebosim/gz-sim/blob/gz-sim8_8.11.0/src/SimulationRunner.cc) shows both publisher configurations.

After changing the statistics source, the fixed 60 Hz `pose/info` publisher became visible as a roughly 16–17 ms wait. P2b subscribes once to the native `dynamic_pose/info` stream and sets SceneBroadcaster’s configurable `dynamic_pose_hertz` to 1000 in a private copy of the installed system configuration and any explicit world SceneBroadcaster plugin. It preserves the system list, physics step and geometry. The dynamic stream carries the same controlled model pose and native timestamp while omitting static visuals. [SceneBroadcaster 8.11 source](https://github.com/gazebosim/gz-sim/blob/gz-sim8_8.11.0/src/systems/scene_broadcaster/SceneBroadcaster.cc) defines the configurable rate and source timestamp.

Thread-safe event notifications replace 1 ms polling sleeps. The clear→locked-check→event-wait order prevents lost wakeups; source errors wake and fault the waiter. The 10 ms command-release delay runs only when a command is newly queued, rather than on every advance. There is no deliberate per-barrier MAVSDK settle timer: the service projects the real stream cache and exposes its ages. WorldControl acknowledgment alone still cannot release a barrier; exact paused statistics and exact pose timestamps are independently required.

### Heartbeat timing and retained failure

An intermediate image with the transport optimizations but ordinary SDK wall-clock GCS heartbeat cadence failed one 200 ms goto: PX4 entered RETURN_TO_LAUNCH at 24.2 sim seconds, returned to the origin, and the command correctly failed its 180 sim-second completion deadline. The failed run is retained in `/tmp/aas-p2a/p2b-flight-sweep.json` and excluded from the final image’s table. Its timing is consistent with a GCS heartbeat gap at accelerated simulation speed; native logs were not retained for that particular failure, so the exact failsafe trigger is an inference. Two subsequent diagnostic repeats without the heartbeat fix succeeded; the failure was intermittent.

The final service uses MAVSDK’s native `MavlinkDirect.send_message` to release an actual GCS HEARTBEAT once per native simulation second before integration chunks, including warmup. MAVSDK supplies its configured sender identity and native framing; the backend adds no audit decoder or CRC implementation. PX4 failsafe parameters are unchanged. A send failure propagates before integration, and heartbeat transmission is never treated as PX4 state, a vehicle acknowledgment or command success. The inherited incoming-heartbeat timeout remains 3600 wall seconds for paused agent decisions. Both final 200 ms flights completed, including the 11.03× repeat.

[measurements-p2b.json](../../containers/px4-gazebo/measurements-p2b.json) retains per-run metrics, phase profiles, source-age summaries, observed command updates, terminal telemetry and artifact paths. Full traces, RPC logs and final native logs are under `/tmp/aas-p2a/p2b-*`. The smoke client supports `--step-ms 4,20,100,200 --runs 2` and `--vehicles 3 --step-ms 20 --runs 2`. **39 host tests pass**, including strict framing, false-success protection, barrier acknowledgment/unpaused/stale-pose rejection, overshoot and subscription faults without resend, event wakeups, pose-rate configuration preservation, heartbeat send failure, sweep grouping and partial/cleanup failure recording. Focused Ruff checks (`E4,E7,E9,F,I`) and formatting pass; all service modules remain below 600 lines.

Orchestrator re-verification of `dev-p2b` (2026-10-08): one vehicle, `--step-ms 20`,
arm/takeoff/goto/land all observed terminal success; 45.02 s simulated in 10.6 s wall
(about 4.25x), hello-to-reset 12.6 s.

## Standalone build (P9)

`containers/px4-gazebo/Dockerfile` now uses public, digest-pinned bases and verified
source/dependency inputs. Build with `containers/px4-gazebo/build.sh`; the output tag
is `aeroagentsim/px4-gazebo:standalone`. No AeroBench image or checkout is required.
The existing backend service/native model is unchanged.

The final image is `sha256:2c973f0fb2bb0bc18f3f23f2b4a5d8ed2a5932eb0335e7cb7865fd6a8dd31444`, **2,335,561,749 bytes**.
Its existing real smoke client passed on this image; the three standalone Docker
adapter/replay tests also passed. Input hashes, command timings, RTF/repeatability,
build-network failure history and artifact links are recorded in
[containers.md](containers.md). Verification artifacts are in
`containers/px4-gazebo/standalone/`; the original dev-image measurements above remain
historical and are not substituted for standalone results.
