# UI calibration baseline and additive delta

Snapshot: 2026-10-01 08:12 PDT. This report records the code and evidence that
the additive observation UI can reuse. It does not change the delivery plan,
formal Provider claims, published scenes, or active worker ownership.

## Ownership boundary

| Area | Owner at this snapshot | Files |
| --- | --- | --- |
| Central frontend integration | Codex project owner | `frontend/src/map.ts`, `frontend/src/app.ts`, and shared shell styling needed for integration |
| Optical camera math and sensor adapter | `optical_camera` worker, active | `frontend/src/observation-camera.ts`, its tests, `frontend/src/observation-camera-sensors.ts`, its tests, and `docs/ui-calibration-camera-20261001.en.md` |
| Operations monitor | `monitor_ui` worker, active | `frontend/src/operations-monitor.ts`, its CSS and tests, and `docs/ui-calibration-monitor-20261001.en.md` |
| Formal v8 execution and inspection | W1d, active | `aero_bench/executor/planning.py`, its assigned tests, formal inspection modules, the v8 run, verification, and read-only delivery evidence |
| Narrow regression gates | Separate takeover workers, active | `frontend/src/loading-progress.test.ts` and `tests/test_traffic_preview_jobs.py` |
| Minimap module | Reserve before implementation | A new isolated module and tests; it must not edit `map.ts` or `app.ts` without the project owner's integration handoff |

W3d has completed and has no live process. W2, W4, W5 and W6 have completed
their assigned lanes. Their accepted implementation remains in place. The old
B-017 ownership snapshot is useful history, but later worker reports supersede
its unfinished browser states. The current takeover entry is B-018 in
the project coordination record (session record, omitted from this export).

## Current frontend behavior

| Concern | Current state | Reusable interface or evidence | Delta |
| --- | --- | --- | --- |
| Shared selection | Working | `SelectionState` holds one `TraceTarget` and exposes `select`, `clear`, `isSelected` and `subscribe` (`frontend/src/state/selection.ts:5-25`). `TraceTarget` already covers entities, spatial objects and events (`frontend/src/state/target.ts:1-25`). | Keep this as the sole selection authority. Minimap, fleet, event and camera actions call it through integration callbacks. |
| Selection propagation | Working | Map picks update selection (`frontend/src/app.ts:383-390`); one subscription refreshes the tree, inspector, right rail, map and telemetry (`frontend/src/app.ts:457-470`); entity and event rows also select through the same store (`frontend/src/app.ts:1640-1642`, `frontend/src/app.ts:2990-3008`). | Add minimap and monitor subscribers. Do not add a second selected-vehicle store. |
| Observation intent | Partly connected | `CameraState` separates one-shot focus from persistent follow (`frontend/src/state/camera.ts:4-45`). It is already separate from execution controls. | Add an explicit observation mode and selected sensor while keeping dispatch and runtime commands outside this state. |
| Camera modes | Mislabelled partial implementation | `PublicTraceMap` exposes `free`, `chase` and `cockpit` (`frontend/src/map.ts:62`). Chase is an external offset. Cockpit places the main camera 0.3 m above the vehicle centre and points it along model +X (`frontend/src/map.ts:1019-1052`). | Rename the existing behaviors in the UI as free inspection and external follow. Replace cockpit behavior with a declared sensor mount. Do not call an orbit camera near the vehicle an onboard camera. |
| Vehicle pose | Working at the selected tick | Every dynamic scene object receives the exact `SceneState` position and quaternion; ENU becomes renderer `x=east`, `y=up`, `z=-north`, and the quaternion conversion is `(qx, qz, -qy, qw)` (`frontend/src/map.ts:3032-3038`, `frontend/src/map.ts:3138-3147`). | Reuse this conversion in one reviewed sensor adapter. Test translation, yaw, pitch and roll against known vectors. |
| Replay time | Working | `ReplayState` uses recorded ticks and simulation nanoseconds, presents at 10 Hz, and applies pause, step, seek and speed without inventing intermediate states (`frontend/src/state/replay.ts:10-67`, `frontend/src/state/replay.ts:89-143`). App builds the map scene and panels from the same current tick (`frontend/src/app.ts:1192-1217`, `frontend/src/app.ts:1264-1298`). | Drive camera pose, footprint, monitor data, event location and minimap from that same snapshot. Do not introduce another playback clock. |
| Main rendering | Working | The current tick updates entities, camera, trajectories and network before one scene render (`frontend/src/map.ts:3138-3154`). | A preview may use the existing scene and renderer with a bounded viewport or render target. Avoid a second full renderer. Measure its cost separately. |
| Flight display | Partly connected and honestly described in code | `TelemetryHud` states that it is derived from `StateSample`, not a Gazebo frame (`frontend/src/telemetry-hud.ts:1-7`). It exposes a swap callback, but App does not supply it (`frontend/src/telemetry-hud.ts:13-18`, `frontend/src/app.ts:414-425`). Its dropdown nevertheless labels cockpit as “Onboard Cam” (`frontend/src/telemetry-hud.ts:92-117`). UAV discovery relies on an ID substring (`frontend/src/telemetry-hud.ts:233-240`). | Correct the label until the sensor path is integrated, wire deliberate view exchange, and identify UAVs from the scenario entity kind. Keep the small canvases labelled as instruments unless they contain actual imagery. |
| Telemetry | Working for declared fields | `TelemetryPanel` accepts exact-tick pose, velocity, battery, health, mission, network, region and verifier data and renders absent fields as undeclared (`frontend/src/telemetry-panel.ts:47-71`). It exposes sensor-frame identifiers and digests (`frontend/src/telemetry-panel.ts:349-390`). | Build a compact selected-aircraft summary from the same source. Show source time and unknown values. Do not infer payload, destination, reserve, connectivity or freshness thresholds when the record does not declare them. |
| Events | Working as a read-only record, limited as an alarm model | `PublicRunEvent` carries time, causal IDs, entity/vehicle/provider IDs and public payload but has no uniform severity, location, acknowledgement or resolution fields (`frontend/src/generated/aero-bench-contracts.ts:2069-2092`). The interaction timeline keeps missing causal stages visible (`frontend/src/interaction-timeline.ts:13-30`, `frontend/src/interaction-timeline.ts:54-84`). | Locate an event only from an explicit entity pose, explicit public coordinates, or another declared relationship. Render unsupported alarm fields as unknown. Acknowledgement and resolution need a separate contract and command path. |
| Authored event preview | Correctly bounded | The authored timeline says that scheduled/current/past follows the preview playhead and does not mean a Provider accepted or executed the event (`frontend/src/city-event-timeline.ts:53-70`). | Preserve this wording and visual distinction from formal execution and verified replay. |
| Minimap | Missing | No production minimap or observation-viewport module exists. Existing small HUD canvases are instruments. | Add a lightweight SVG or Canvas2D map from public scenario/state geometry. It must remain useful while the 3D view is close to an asset or inside an onboard view. |

## Sensor and camera contract

The current public contract is enough for a rigid simulated camera, with one
narrow adapter:

- `ResolvedSensor` declares parent entity, kind, absolute initial pose, horizontal
  and vertical field of view, and pixel resolution
  (`frontend/src/generated/aero-bench-contracts.ts:2493-2503`).
- The source `SensorSpec.pose` is a unit-quaternion parent-relative pose in metres
  (`aero_bench/world/contracts.py:571-594`,
  `aero_bench/world/contracts.py:776-790`). Resolution composes parent initial
  transform and mount before publishing `initial_pose`
  (`aero_bench/world/resolved.py:2365-2385`). A frontend adapter can therefore
  recover the fixed mount as `inverse(parentInitial) * sensorInitial`, then apply
  `currentParent * mount` at the replay tick.
- The inspection contract declares the optical convention as camera forward +X,
  image right +Y and image up +Z
  (`aero_bench/tasks/inspection/formal_v2_contracts.py:299-317`). The sealed
  context publishes the mount and the same convention
  (`aero_bench/tasks/inspection/formal_v2_sealed.py:341-352`). The adapter must
  convert that convention to Three.js camera axes explicitly.
- Units are metres, degrees, pixels and simulation nanoseconds. Public quaternion
  objects are `qw`, `qx`, `qy`, `qz` (`frontend/src/generated/aero-bench-contracts.ts:1988-2007`).
- Near and far clipping planes are absent from `ResolvedSensor`. Any values used
  by the viewer are display configuration and must not be described as calibrated
  sensor data.

The accepted R5 v3 replay provides a useful integration fixture: Run
`1b0d34243be9920fb827b77b5809b763f50cfb2338d755d1debf9c21d3190471`
contains 524 scene states, one 640×480 camera with 80°×60° field of view and four
public sensor-frame references. This is measured from
`validation/codex-takeover-20261001/I3-v3/execution/1b0d34243be9920fb827b77b5809b763f50cfb2338d755d1debf9c21d3190471/public/public-trace.json`.

The same contract does not publish time-varying gimbal orientation. A gimbal
mode must remain unavailable unless a source supplies that state. It also does
not link `PublicSensorFrameReference` directly to a `sensor_id`; it supplies an
artifact, frame, observation, provider and time
(`frontend/src/generated/aero-bench-contracts.ts:2528-2537`). Recorded imagery
needs an explicit sensor/frame association before the UI may present it as the
selected camera. Simulated rendering can proceed independently and must be
labelled “simulated RGB”. Live video is unsupported by the current public trace.

## Additive implementation sequence

### 1. Shared observation and navigation

Reuse `SelectionState`, `CameraState`, the current map scene, vehicle objects and
`ReplayState`. Integrate the in-flight camera math and sensor adapter, then add a
pure minimap module. The minimap model should accept scene bounds, facilities,
regions, routes, current entity samples, selected target, event locations,
observation viewport and sensor footprint. Its outputs are SVG or Canvas2D
geometry and callbacks, with no command gateway dependency.

Required interactions:

- A vehicle marker selects the entity and exposes only declared camera options.
- An event selects the event and frames an explicit location or affected entity.
- Empty-map click requests free observation focus at an ENU ground point.
- Viewport drag pans the free observation camera. A completed drag suppresses its
  click, using a tested movement threshold and pointer capture.
- An onboard footprint is inspect-only. Dragging it cannot move the vehicle or
  gimbal. Map navigation from onboard mode visibly enters free inspection and
  keeps a return-to-aircraft action.
- North, scale, fit-to-area and a compact symbol legend remain visible. Heading
  and camera direction use separate glyphs. Dense markers use deterministic
  decluttering rather than hiding selected or exception objects.

Acceptance evidence: camera transform unit tests, sensor-adapter fixtures,
minimap transform and gesture tests, one browser flow across map/list/event/view
selection, and a command-spy assertion showing zero flight or dispatch calls.

### 2. Operational context

Mount the in-flight operations monitor around the same selected target and tick.
Reuse `TelemetryPanel` derivations and explicit public event relationships. Show
mission phase, pose, altitude reference, heading, speed, battery, health and
network state only where the current sample supplies them. Keep activity,
connectivity and health as separate fields. Every state row includes source and
recorded time; stale classification is used only when a declared cadence or
provider status supports it.

Authored facilities, orders and agents may appear in Studio preview with the
existing preview label. Formal/replay views show logistics relationships only
when the public run carries them. Arrival, accepted command, handover and delivery
remain distinct states. Event cards show affected object, tick/time, location and
evidence when present, and “unknown” for absent severity, condition or action.

Acceptance evidence: missing-value, stale/disconnected, event-to-object and
scene-change tests; desktop keyboard traversal; symbols and labels accompanying
status colours.

### 3. Replay inspection and presentation

Use the existing replay cursor for scene, sensor pose, footprint, telemetry and
events. Exercise pause, single-step, speed changes, first/last, reset and seek.
The view swap changes placement only; selection, selected sensor, mission context
and replay tick remain unchanged. Wallboard mode reuses the same snapshot and
suppresses authoring or dispatch controls through presentation state.

Acceptance evidence: an end-to-end browser sequence that finds a global
exception, selects its aircraft, opens simulated onboard view, locates the event
on the minimap, reads its declared logistics context, swaps views, and returns to
overview without losing selection or tick. Capture common desktop sizes and one
short recording when the browser harness is stable.

## Performance boundary

The accepted W5 measurements establish a comparison method, not a desktop UI
budget. On the declared headless GPU-3 run, overview improved from 13.47 to
14.68 FPS and street view from 18.53 to 19.12 FPS; native readback dominated,
hardware composition was not established, and short-warmup repeatability failed
(`validation/platform-plan-20261001/W5-GPU/perf/report.md`).

Measure the main render, secondary camera and minimap separately after integration.
Use the same renderer for the secondary camera, update the minimap at a bounded
rate, record fleet size and hardware, and report frame cost and update latency as
measurements. Until then, any frame budget is a target.

## Contract work outside this UI increment

Keep these as separate proposals because the current public contracts do not
support them uniformly:

- time-varying gimbal pose and stabilization mode;
- direct sensor-to-frame association and recorded/live stream state;
- uniform event severity, spatial location, acknowledgement and resolution;
- formal order custody, facility capacity/queue/charging and ground-agent state;
- declared telemetry cadence or freshness thresholds;
- Weather physics, Airspace enforcement, physical Logistics and formal binding
  of the offline T2 preview.

The UI must expose unavailable data and existing capability blockers. It must not
replace a failing formal source with authored or mock data. Ground roads retain
the current ground-level constraint.
