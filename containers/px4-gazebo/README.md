# PX4/Gazebo backend

AeroAgentSim's container-side lockstep flight simulator. It starts PX4 SITL and Gazebo Harmonic from a public base, and MAVSDK 3.17.2 built from pinned upstream commits with the inherited patch, without loading any external service or its workload machinery. Runtime Python dependencies are version-pinned; the host smoke client uses only the standard library.

```bash
containers/px4-gazebo/build.sh
docker run -d --name aas-px4 --label aeroagentsim.job=release \
  --cpus 16 --memory 16g -p 127.0.0.1:19000:9000 \
  aeroagentsim/px4-gazebo:standalone
python \
  containers/px4-gazebo/smoke.py --port 19000 --vehicles 1 --runs 1 --step-ms 20 \
  --output /tmp/px4-flight-sweep.json
docker rm -f aas-px4
```

Each container owns one world and accepts one connection at a time. `close` cleans up its world and leaves the TCP listener available for a new run. A disconnect also cleans up. Every reset starts new native processes and fresh PX4 parameter/data directories. There is one reset per connection, with no crash resume or automatic retries.

Every frame is a strict UTF-8 JSON object ending in LF. The 8 MiB limit includes LF. Requests carry `protocol`, `major`, `minor`, increasing integer `id`, `op` and object `payload`. Responses echo protocol/version/id and contain exactly one `result` or structured `error`. The framing protocol remains `aeroagentsim.px4/v1`, major 1, minor 0; **the hello result is now major 1, minor 1** and explicitly advertises operations/features. This additive hello revision does not reinterpret old framing or old command schemas. Duplicate keys, nonfinite values, wrong framing version, incomplete frames and invalid IDs fault the connection. Operation failures taint the connection: only `close` is then legal.

Example request sequence (one request in flight):

```json
{"protocol":"aeroagentsim.px4/v1","major":1,"minor":0,"id":1,"op":"hello","payload":{}}
{"protocol":"aeroagentsim.px4/v1","major":1,"minor":0,"id":2,"op":"reset","payload":{"seed":42,"world":"default","vehicles":[{"id":"v0","model":"x500","spawn":[0,0,0,0,0,0]}],"warmup":10000000000}}
{"protocol":"aeroagentsim.px4/v1","major":1,"minor":0,"id":3,"op":"advance","payload":{"to_sim_ns":20000000}}
{"protocol":"aeroagentsim.px4/v1","major":1,"minor":0,"id":4,"op":"command","payload":{"vehicle":"v0","action":"arm","params":{},"command_id":"arm_1"}}
```

`hello` declares installed models, telemetry/contact capabilities, default-world physics step and operation deadlines. `reset` accepts an image world name or absolute mounted SDF path, integer seed, explicit vehicle declarations and warmup nanoseconds. Spawn uses ENU metres and roll/pitch/yaw radians. The actual physics step comes from SDF and is returned by reset. Native warmup precedes logical time zero. World physics runs paused, with wall-time throttling disabled; the world must provide ENU spherical coordinates for geographic commands.

`advance` requires a nondecreasing integer target aligned to the physics step. Equal-time advance holds native time and does not dispatch pending commands. Integration uses paused `WorldControl.multi_step` through a persistent request-only Gazebo node in a separate Python process; Native `WorldStatistics` on the unthrottled `/stats` topic must confirm the exact target and paused state. Each reset uses a unique transport partition with exactly one world; the namespaced statistics topic has a 10 Hz wall-time throttle. Native poses come from `dynamic_pose/info`, with SceneBroadcaster configured to 1000 Hz in a private copy of the installed server configuration. Event notifications wake confirmation waits; no polling or per-barrier subscription occurs. No stateful operation is resent. Per-vehicle pose/attitude come from timestamped Gazebo data. PX4 velocity, battery fraction, armed/mode/landed/health come from MAVSDK streams. Freshness metadata distinguishes source-time Gazebo pose from MAVSDK receipt age; a missing MAVSDK source-time mapping is explicit and never replaced with a guessed time. Contacts preserve raw collision names and source simulation timestamps. They are events received since the preceding result; subscriber delivery can lag a physics boundary, and an empty list does not certify contact absence. The installed model list is inventory; the historical smoke evidence linked below covers x500, not acceptance of every model or the additions described here. Models require instrumented contact topics, which reset reports explicitly.

`command` accepts one active command per vehicle and a run-unique command ID. Acceptance queues execution at the next positive advance; it is not an autopilot acknowledgment or success result. Later advances emit `running`, `failed` or `succeeded` updates with the observation boundary time. A successful MAVSDK call alone cannot complete a command: PX4 status and position/velocity completion conditions must also hold. All completion-predicate fields require observations after the action acknowledgment and receipt ages at most 30 wall seconds. State observations are asynchronous and their first observed boundary is an upper bound on the native state transition time.

| Action | Params | Observed success |
|---|---|---|
| arm | `{}` | fresh PX4 armed=true |
| takeoff | `{"altitude_m":10}` | PX4 IN_AIR, armed, relative height within 1 m, speed <1.5 m/s for 1 sim second |
| goto_location | `{"position_enu":[50,0,10],"yaw_deg":0}` | PX4 IN_AIR/armed, Gazebo distance <=2 m, speed <1.5 m/s for 1 sim second |
| hold | `{}` | PX4 HOLD, speed <1 m/s for 1 sim second |
| land | `{}` | PX4 ON_GROUND and disarmed for 1 sim second |
| disarm | `{}` | fresh PX4 armed=false |

ENU destinations convert through the SDF's WGS84 local tangent origin. Vertical conversion uses the reported PX4 absolute-altitude datum measured against stationary Gazebo pose at reset; reset returns this offset. `yaw_deg` uses MAVSDK's compass heading (0° north, clockwise), whereas spawn yaw uses ENU radians (0 east, counterclockwise). Takeoff height is relative to the PX4 home position. Commands have a 30-second wall deadline for the MAVSDK call and a 180-second simulation deadline for observed completion. A timeout or PX4 action rejection produces failure, not synthetic success. Ambiguous native termination additionally quarantines the vehicle until reset.

Before integration, the backend sends GCS heartbeats through MAVSDK once per native simulation second, including warmup. This keeps the real SDK link alive when simulation outpaces the SDK’s normal wall-clock heartbeat. It changes no PX4 failsafe parameters and provides no vehicle telemetry or command-success evidence.

The MAVSDK heartbeat timeout is 3600 wall seconds, matching the advertised connection idle deadline and allowing pauses for agent decisions. The inherited patch requires a private audit-journal output argument; this service does not decode, validate, export or consume that journal. `AAS_PORT` changes the TCP port. `AAS_DIAGNOSTIC_DIR` optionally copies native logs and generated SDF after cleanup into a writable mounted directory. Default files are private and removed.

The host adapter (`src/aeroagentsim/adapters/px4_gazebo.py`) owns kernel command correlation, field/event validation and journaled publication; this service is not a kernel implementation. See the [historical measured validation and extraction map](../../docs/platform/px4-backend.md), which does not establish acceptance of these new primitives.

Use `--step-ms 20` for one cadence, or `--step-ms 4,20,100,200` for a sweep. Each cadence runs `--runs` fresh connections/resets; comparisons remain within a cadence. `--vehicles 3 --step-ms 20 --runs 2` measures fleet scaling. A failure exits nonzero and retains partial observations, including cleanup errors.

`AAS_PROFILE=1` enables optional `advance.result.profile_wall_ns`, a map of measured phase durations in integer wall nanoseconds. The client summarizes actual phases and their counts. Service logs additionally report response encoding, socket drain and total server RPC duration; these cannot be included in the response they measure. Profiling does not change simulation time or telemetry values.

Run the host contract checks with:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=containers/px4-gazebo \
  python -m pytest \
  -q -p no:cacheprovider \
  containers/px4-gazebo/tests
```

## Native primitives: implemented contracts, not native acceptance

These changes salvage reviewed native hunks from annotated tag
`archive/pr-17-companion-platform` (commit `4ea31d5a5c7451e7b6e835a8f17d3bb4033e4e8f`),
with stricter configuration, capability admission and recorded contracts. The
private PX4 native caches and ns-3 cache-only delta are deliberately excluded.
No Dockerfile or build-script changes are part of this extraction.

| Primitive | Implemented source behavior | Still requires native acceptance |
|---|---|---|
| RGB-D geometry/capture | Closed camera configuration; generated mount/optics; matching owner, frame, dimensions and source stamps; bounded PNG/depth chunk reads | Actual camera topics/frame IDs, headless rendering, image/depth registration and optical-Z values on a pinned image/graphics stack |
| Source-clock estimates | PX4 timestamped position/attitude and explicit AMSL origin, checked paused TIMESYNC anchors; unmapped, stale, reordered or reset data is unavailable, never replaced with Gazebo truth | PX4/MAVSDK stream availability, boot-clock mapping assumptions and stale/reset behavior on real native processes |
| Physical payload | Real SDF body, detachable joint and scoped receiver contacts; release ACK is admission only; delivery requires continuous source-stamped contact after confirmed observed detachment | Joint attachment/release, collision scope, callback ordering, physics contact duration and load effects |
| Cancellation | Undispatched queue cancellation, or wait for the original SDK response, issue HOLD, then observe fresh HOLD/low velocity for one simulation second; ambiguous termination/HOLD and deadlines quarantine until reset | Actual SDK retry termination, action/cancel races, observed hold and reset cleanup |

**No tests, images, builds, native processes or servers were run for this
change.** The new host fixtures exercise pure logic only. Even a future host
PASS is not native acceptance. Gazebo depth is **rendered geometric optical-Z**,
not physical ranging: no material response, weather, multipath or ranging-noise
model is established. A valid receipt is not proof of a whole mission's success.

### Admission and recorded owner contracts

Hello minor 1 advertises `operations` including `cancel`, `payload`, `capture`,
and `features` including `observed_cancel_hold`, `payload_contact_receipts`,
`rgbd_camera`, `source_clock_estimates`. An older minor-0 image remains usable
for legacy scenarios only. The adapter rejects missing or malformed required
capabilities **before reset or execution**, and checks again before each new RPC.
Framing minor 0 is intentionally unchanged; unsupported hello revisions reject.
The PX4 host engine reports implementation version `e1/2`; other native adapters
and legacy message meanings are unchanged.

Engine configuration `native_operations: [cancel, payload, capture]` explicitly
enables optional operations (omit unused entries). `reset.payload` implies
`payload`, `reset.rgbd_camera` implies `capture`, and `reset.altitude_datum` or
an `estimate` telemetry field mapping requires source-clock estimates. Enabling capture/payload without
the matching reset configuration cannot manufacture an available subsystem.
New command/event descriptors must be included in the authored registry snapshot
via `command_descriptors("px4_gazebo")` / `event_descriptors("px4_gazebo")`.

Existing flight command IDs keep their non-cancelable v1 meaning. Opt-in
cancellation uses new `adapters.px4_gazebo.<action>_cancel_v1` command IDs
(`arm`, `takeoff`, `goto`, `hold`, `land`, `disarm`), declared only when cancel
is enabled. Kernel cancel admission is recorded immediately, but a canceled
receipt waits for confirmed non-dispatch or the termination/HOLD observations.
SDK/transport timeouts and unknown errors do not prove stopped retries. A
quarantined vehicle accepts no replacement flight command until a new reset.

| New kernel command suffix (`adapters.px4_gazebo.`) | Payload beyond `entity` | Recorded evidence |
|---|---|---|
| `payload_release_v1` | `package_id` | Release ACK, observed `payload_state_v1`, then `receiver_receipt_v1`; success only on the validated contact receipt |
| `payload_status_v1` | `package_id` | Validated observed payload state and typed command result |
| `capture_poll_v1` | `after_sim_ns` (integer >= -1) | `capture_frame_v1` with source identity/time, calibration, localization availability and blob hashes/lengths |
| `capture_read_v1` | `frame_id`, `kind` (`rgb`/`depth`), `offset`, `max_bytes` (1..262144) | `capture_chunk_v1` with correlated offset/length, base64 bytes and EOF |

State/results go through declared, schema-validated kernel events and receipts,
not `.client`, mutable `latest_native`/`native_events` lists, or diagnostic files.
Capture pending/unavailable is recorded but does not become a successful frame.
Capture localization requires causal PX4 source estimates within 40 ms of the
image; unknown localization remains explicitly unavailable beside valid imagery.
Internal service frame retention is bounded and only serves the declared chunk
RPC. Payload receiver evidence is scoped to the configured package/collision;
no contact before confirmed detachment can count, gaps reset the duration,
and duplicate/reordered source stamps cannot extend it. Failed publication
attempts remain correlated in later observed state, but cannot arm delivery or
become successful release commands. Unstamped joint transitions retain null source time and an observed release interval rather
than a guessed acquisition timestamp. Recorded receipts can be replayed without
native execution; replay does not independently re-measure physical delivery.

### Strict optional reset configuration

Example nested values (alongside the required seed/world/vehicles/warmup):

```json
{
  "altitude_datum": "amsl",
  "rgbd_camera": {
    "sensor_id": "rgbd", "topic_prefix": "/aas/sensors",
    "pose_flu": [0.1, 0, -0.1, 0, 0.3, 0],
    "width": 640, "height": 480, "update_rate_hz": 30,
    "horizontal_fov_rad": 1.2, "near_m": 0.1, "far_m": 100
  },
  "payload": {
    "carrier": "v0", "package_id": "parcel", "receiver_id": "receiver",
    "detach_topic": "/aas/parcel/detach", "state_topic": "/aas/parcel/state",
    "contact_topic": "/aas/receiver/contact",
    "attachment_offset_enu_m": [0, 0, -0.3],
    "package_size_m": [0.1, 0.2, 0.2], "package_mass_kg": 0.5,
    "receiver_pose": [10, 0, 0, 0, 0, 0], "receiver_size": [2, 2, 0.1],
    "contact_duration_ns": 1000000000
  }
}
```

Unknown nested keys, missing required keys, booleans in numeric slots, nonfinite
values, invalid IDs/topics and dimension inconsistencies reject. Camera width
and height are integers 1..2048 with at most 1,048,576 pixels; RGB/depth share
the same dimensions. Rate is (0,240] Hz, horizontal FOV [0.01, pi-0.01] radians,
and 0 < near < far <= 10000 metres. Mount translations are bounded to +/-100 m
and rotations to +/-2pi. Payload box dimensions are [0.001,100] m and mass
[0.001,1000] kg; contact duration must align to the physics quantum and lie
between one quantum and 60 simulation seconds. Payload IDs/topics are distinct
and carrier must be declared. Optional link/collision IDs and package orientation
are also validated. Optional sections are disabled by omission, not malformed
or null values. These are admission limits, not validated physical operating
ranges.

Direct service RPCs require explicit actions: payload `release`/`status` and
capture `poll`/`read`; missing/unknown actions never default to status/poll.
Release additionally requires a run-unique `command_id`. A failed/ambiguous
publication is not retried. `AAS_COMPLETION_SOURCE=px4_estimate` opts into
estimate-based flight completion and requires `altitude_datum: amsl`; the
legacy completion profile remains the default. Even the estimate profile's
takeoff height predicate uses the explicitly receipt-aged PX4 relative altitude,
not a claimed source-clock mapping for that MAVSDK value.

The host-only additions are `tests/test_primitives.py` in this container and
`tests/adapters/test_native_admission.py` at repository root. Native acceptance
must separately pin the image, PX4/Gazebo/MAVSDK, models/world, graphics stack
and host adapter/kernel revisions and retain real completion/failure evidence.

## Build provenance

The Dockerfile has no external base image or repository dependency. Upstream
source commits and package versions are pinned by version/commit. Build-only
caches, wheels and compilers are excluded from the runtime where a separate
build stage is used.

`mavsdk-incoming-heartbeat-timeout.patch`, `mavlink-offline-python.patch`,
`pymavlink-build-requirements.txt`, `requirements.txt`,
`inject_contact_sensors.py` and `patch_camera_model.py` were adapted from an
earlier internal `containers/px4-gazebo/` definition. The patch includes the
audit-journal argument still required by the slim launcher and local
third-party archives for offline CMake compilation. The slim service never
loads or references the earlier internal airspace transition plugin, so that
plugin is excluded.
