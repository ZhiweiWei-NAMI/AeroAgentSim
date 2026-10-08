# PX4/Gazebo container backend

P2a extracts the real native flight stack into `containers/px4-gazebo/service`, underneath AeroAgentSim. It has no AeroBench workload, ResolvedScenario, session-token, inspection, urban, evidence-verifier or command-audit decoder dependency. The independent aerokernel remains general; a later host adapter binds these backend-specific fields/actions into the kernel's registry and lockstep timing contract.

The runnable image is **`aeroagentsim/px4-gazebo:dev-p2a-ipc2`**, ID `sha256:9a421a10219067220de9b337d3d9af018e43f5758bd77cdc18a4ecb0bad04ed0`. Its base is the user-specified local flight image, inspected before use: PX4 `v1.17.0-alpha1-1551-g381149fb01`, Gazebo Harmonic `8.11.0`, patched MAVSDK `3.17.2`. No native binaries were rebuilt. The image retains the non-root user and native dependencies. Its reported size is 2,370,450,139 bytes: the service is slim, but inheriting the 2.37 GB base cannot remove those inherited layers.

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

The final implementation uses a persistent request-only Python child, separate from subscriber callbacks. The Transport13 [blocking request binding](https://github.com/gazebosim/gz-transport/blob/gz-transport13/python/src/transport/_gz_transport_pybind11.cc#L217) retains the GIL; isolating requests avoids interference with Python subscription callbacks and removes per-call CLI startup/discovery. It sends once, then independently requires exact paused simulation time and pose confirmation. All final flight runs completed without control-call faults.

MAVSDK has no exact source simulation timestamps for the selected field streams; the service exposes that limitation explicitly. Contacts are received events and can lag a barrier; an empty list is not a proof of absence. Only x500 has been validated; other catalogued airframes require instrumented contact topics. Mounted worlds must supply ENU spherical coordinates and an explicit physics step. Bitwise determinism, larger-fleet capacity, camera payload transport and the host aerokernel adapter remain outside this verified slice.

## Independent verification note (orchestrator, 2026-10-08)

A separate one-vehicle rerun with the smoke client defaults (`--step-ms 20`) succeeded
(arm/takeoff/goto/land all reached observed terminal states; hello-to-reset 13.1 s) but
ran at about **0.2x** real time (221 s wall for ~44 s simulated flight) while the host was
shared with other jobs. The ~2x figures above apply to 200 ms barriers only. Per-barrier
overhead is therefore on the order of 100 ms wall; reducing it (e.g. in-process
gz-transport world control instead of per-call `gz service` subprocesses) is the main P2
performance item before fine-grained lockstep coupling.
