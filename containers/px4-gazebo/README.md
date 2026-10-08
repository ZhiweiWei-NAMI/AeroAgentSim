# PX4/Gazebo backend

AeroAgentSim's container-side lockstep flight simulator. It starts PX4 SITL and Gazebo Harmonic from a public digest-pinned base, and MAVSDK 3.17.2 built from verified source with the inherited patch, without loading the AeroBench service or its workload/evidence machinery. Runtime Python dependencies are installed from the vendored hash lock; the host smoke client uses only the standard library.

```bash
containers/px4-gazebo/build.sh
docker run -d --name aas-p9-px4 --label aeroagentsim.job=p9 \
  --cpus 16 --memory 16g -p 127.0.0.1:19000:9000 \
  aeroagentsim/px4-gazebo:standalone
/mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python \
  containers/px4-gazebo/smoke.py --port 19000 --vehicles 1 --runs 1 --step-ms 20 \
  --output /tmp/aas-p9-px4/p2b-flight-sweep.json
docker rm -f aas-p9-px4
```

Each container owns one world and accepts one connection at a time. `close` cleans up its world and leaves the TCP listener available for a new run. A disconnect also cleans up. Every reset starts new native processes and fresh PX4 parameter/data directories. There is one reset per connection, with no crash resume or automatic retries.

Every frame is a strict UTF-8 JSON object ending in LF. The 8 MiB limit includes LF. Requests carry `protocol`, `major`, `minor`, increasing integer `id`, `op` and object `payload`. Responses echo protocol/version/id and contain exactly one `result` or structured `error`. The protocol is `aeroagentsim.px4/v1`, major 1, minor 0. Duplicate keys, nonfinite values, wrong version, incomplete frames and invalid IDs fault the connection. Operation failures taint the connection: only `close` is then legal.

Example request sequence (one request in flight):

```json
{"protocol":"aeroagentsim.px4/v1","major":1,"minor":0,"id":1,"op":"hello","payload":{}}
{"protocol":"aeroagentsim.px4/v1","major":1,"minor":0,"id":2,"op":"reset","payload":{"seed":42,"world":"default","vehicles":[{"id":"v0","model":"x500","spawn":[0,0,0,0,0,0]}],"warmup":10000000000}}
{"protocol":"aeroagentsim.px4/v1","major":1,"minor":0,"id":3,"op":"advance","payload":{"to_sim_ns":20000000}}
{"protocol":"aeroagentsim.px4/v1","major":1,"minor":0,"id":4,"op":"command","payload":{"vehicle":"v0","action":"arm","params":{},"command_id":"arm_1"}}
```

`hello` declares installed models, telemetry/contact capabilities, default-world physics step and operation deadlines. `reset` accepts an image world name or absolute mounted SDF path, integer seed, explicit vehicle declarations and warmup nanoseconds. Spawn uses ENU metres and roll/pitch/yaw radians. The actual physics step comes from SDF and is returned by reset. Native warmup precedes logical time zero. World physics runs paused, with wall-time throttling disabled; the world must provide ENU spherical coordinates for geographic commands.

`advance` requires a nondecreasing integer target aligned to the physics step. Equal-time advance holds native time and does not dispatch pending commands. Integration uses paused `WorldControl.multi_step` through a persistent request-only Gazebo node in a separate Python process; Native `WorldStatistics` on the unthrottled `/stats` topic must confirm the exact target and paused state. Each reset uses a unique transport partition with exactly one world; the namespaced statistics topic has a 10 Hz wall-time throttle. Native poses come from `dynamic_pose/info`, with SceneBroadcaster configured to 1000 Hz in a private copy of the installed server configuration. Event notifications wake confirmation waits; no polling or per-barrier subscription occurs. No stateful operation is resent. Per-vehicle pose/attitude come from timestamped Gazebo data. PX4 velocity, battery fraction, armed/mode/landed/health come from MAVSDK streams. Freshness metadata distinguishes source-time Gazebo pose from MAVSDK receipt age; a missing MAVSDK source-time mapping is explicit and never replaced with a guessed time. Contacts preserve raw collision names and source simulation timestamps. They are events received since the preceding result; subscriber delivery can lag a physics boundary, and an empty list does not certify contact absence. The installed model list is inventory; only x500 is validated here. Models require instrumented contact topics, which reset reports explicitly.

`command` accepts one active command per vehicle and a run-unique command ID. Acceptance queues execution at the next positive advance; it is not an autopilot acknowledgment or success result. Later advances emit `running`, `failed` or `succeeded` updates with the observation boundary time. A successful MAVSDK call alone cannot complete a command: PX4 status and position/velocity completion conditions must also hold. All completion-predicate fields require observations after the action acknowledgment and receipt ages at most 30 wall seconds. State observations are asynchronous and their first observed boundary is an upper bound on the native state transition time.

| Action | Params | Observed success |
|---|---|---|
| arm | `{}` | fresh PX4 armed=true |
| takeoff | `{"altitude_m":10}` | PX4 IN_AIR, armed, relative height within 1 m, speed <1.5 m/s for 1 sim second |
| goto_location | `{"position_enu":[50,0,10],"yaw_deg":0}` | PX4 IN_AIR/armed, Gazebo distance <=2 m, speed <1.5 m/s for 1 sim second |
| hold | `{}` | PX4 HOLD, speed <1 m/s for 1 sim second |
| land | `{}` | PX4 ON_GROUND and disarmed for 1 sim second |
| disarm | `{}` | fresh PX4 armed=false |

ENU destinations convert through the SDF's WGS84 local tangent origin. Vertical conversion uses the reported PX4 absolute-altitude datum measured against stationary Gazebo pose at reset; reset returns this offset. `yaw_deg` uses MAVSDK's compass heading (0° north, clockwise), whereas spawn yaw uses ENU radians (0 east, counterclockwise). Takeoff height is relative to the PX4 home position. Commands have a 30-second wall deadline for the MAVSDK call and a 180-second simulation deadline for observed completion. A timeout or PX4 action rejection produces failure, not synthetic success.

Before integration, the backend sends GCS heartbeats through MAVSDK once per native simulation second, including warmup. This keeps the real SDK link alive when simulation outpaces the SDK’s normal wall-clock heartbeat. It changes no PX4 failsafe parameters and provides no vehicle telemetry or command-success evidence.

The MAVSDK heartbeat timeout is 3600 wall seconds, matching the advertised connection idle deadline and allowing pauses for agent decisions. The inherited patch requires a private audit-journal output argument; this service does not decode, validate, export or consume that journal. `AAS_PORT` changes the TCP port. `AAS_DIAGNOSTIC_DIR` optionally copies native logs and generated SDF after cleanup into a writable mounted directory. Default files are private and removed.

The later aerokernel adapter must wrap this backend with `aerokernel.rpc` invocation identity, partition, frontier and input-cut validation. This native service is deliberately not a kernel implementation. See [measured validation and extraction map](../../docs/platform/px4-backend.md).

Use `--step-ms 20` for one cadence, or `--step-ms 4,20,100,200` for a sweep. Each cadence runs `--runs` fresh connections/resets; comparisons remain within a cadence. `--vehicles 3 --step-ms 20 --runs 2` measures fleet scaling. A failure exits nonzero and retains partial observations, including cleanup errors.

`AAS_PROFILE=1` enables optional `advance.result.profile_wall_ns`, a map of measured phase durations in integer wall nanoseconds. The client summarizes actual phases and their counts. Service logs additionally report response encoding, socket drain and total server RPC duration; these cannot be included in the response they measure. Profiling does not change simulation time or telemetry values. [P2b measurements](../../docs/platform/px4-backend.md#p2b-barrier-performance-2026-10-08) include matched before/after probes and complete flight repeats.

Run the host contract checks with:

```bash
TMPDIR=/tmp/aas-p9-px4 PYTHONPATH=containers/px4-gazebo PYTHONDONTWRITEBYTECODE=1 \
  /mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python -m pytest \
  -q -p no:cacheprovider --basetemp /tmp/aas-p9-px4/p2b-pytest \
  containers/px4-gazebo/tests
```

## Standalone build provenance

The Dockerfile has no AeroBench base image or repository dependency. Public bases,
verified source archives, vendored dependency locks/patches, build timings, image
sizes and real validation results are listed in
[the container build record](../../docs/platform/containers.md). APT-selected
artifact URLs/SHA-256 values and installed package versions are retained under
`/opt/aeroagentsim/build-inputs`; Python wheel selection is recorded alongside
its enforced hash lock. Build-only caches, wheels and compilers are excluded
from the runtime where a separate build stage is used.

`mavsdk-incoming-heartbeat-timeout.patch`, `mavlink-offline-python.patch`,
`pymavlink-build-requirements.lock`, `requirements.lock`,
`inject_contact_sensors.py` and `patch_camera_model.py` were copied byte-for-byte
from AeroBench's `containers/px4-gazebo/`. Its Dockerfile supplies the adapted
source-build recipe. The patch includes the audit-journal argument still required
by the slim launcher and local, verified third-party archives for offline CMake
compilation. Camera/contact post-patch hashes are enforced. The slim service
never loads or references AeroBench's airspace transition plugin, so that plugin
is excluded. The compiled MAVSDK output hash is measured per build; source and
patch hashes are the reproducibility constraints because compilers/system
libraries can change its binary bytes.
