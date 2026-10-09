# Traffic accident demo migration design

Status: **design only**, 2026-10-08, platform base `ff4f740`. No scenario, code, dependency, asset or native simulator is installed or changed by D1. Implement the shared [RUNTIME.md](RUNTIME.md) contract; keep the kernel domain neutral. This becomes a selectable example in the existing City Studio/Runs console, with a complete operating and inspection frontend.

## 1. Source inventory and migration boundary

Read-only source is `/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim/aero-bench/demos/traffic_accident` (called `DEMO` below). Inspected `README.md`, `runtime.py`, `server.py`, `assignment.py`, `config.json`, `web/{app,scene}.js`, `web/assets/{scene.json,NOTICE.txt}`, `record_views.mjs`, root `report.md`, and `results/run-20261008-030056/{decisions.jsonl,report.md,result.json,state.json,views-manifest.json}`. Other agents may edit this directory; migration must freeze content hashes before extracting. Ignore `.venv`, wheelhouse, archives and install logs as implementation inputs.

The old demo runs a 15 Hz wall-paced motion thread and a concurrent LangGraph thread over one mutable `Runtime` guarded by an `RLock`. Model waiting lets motion continue. The browser polls `/api/state` every 150 ms, renders role cameras, uploads a PNG and records five views. This is not the new shared DES runtime: migrate model latency into explicit decision/ingress semantics and all world writes into field owners.

| Existing element | Verified content/behaviour | New-platform realization |
| --- | --- | --- |
| Roles | `vehicle.reporter`, `edge.coordinator`, `uav.alpha`, `uav.bravo`; reporter/edge/Alpha/Bravo public status and decision summaries | Preserve IDs. Typed actor/task entities and decision sessions; chain states/receipts generate status labels. Edge is nonspatial unless a genuine location is authored. |
| Incident actors | `vehicle.incident.a/b` approach the same lane in opposite directions, stop around lane distance 102 m at offsets ±2.3 m | Road plugin owns poses/speed. Scenario event commands staged stops; incident entity records authored accident source and participant relations. Do not call this a measured physical collision. |
| Background | 60 `vehicle.001`…`vehicle.060` routes; 6 `uav.bg.01`…`.06`, plus two candidate UAVs | Automatic road-follow/patrol instances from types/tasks/relations; no model calls for background movement. Default 63 road vehicles + 8 UAVs = 71 spatial moving entities, and one edge overview = 72 recorded views. |
| Road motion | Piecewise-linear arc-length route lookup/yaw; reporter 6 m/s, bypass 5.5 m/s; background per-route speed/offset and loop wrapping | New road-kinematic plugin using preserved polylines/connectors and measured progress, with explicit initialization, loop policy and seed. Current ENU point-mass plugin alone does not implement lane-following or traffic avoidance. |
| Occupancy | 4.5×1.8 m oriented vehicle rectangles; separating-axis test, base 0.55 m clearance; proposed next pose blocked if occupied; stable blocking/clear logging after 0.2/0.3 s | Road plugin computes complete occupancy/safe-gap assessments from actual poses, publishes evidence and vetoes unsafe motion. Q6 predicates turn committed conflict assessments into typed events; chains request stop/wait/retry. Stable-duration filters use explicit timers/history. |
| Reporter departure | Connected route `vehicle.005`; `adjacent_lane_bypass`, smoothstep borrow-and-return trajectory around accident; adjacent lane is opposite direction, width 3.2 m; 0.8 m clearance in sampled borrow corridor; runtime rechecks gap even after model authorization | Preserve route geometry and candidate IDs as authored assets. Route chain commands lane owner, waits for actual clear-gap result and execution receipts, then continues original network. Borrowing is an authored simulation policy, not inferred road/legal authorization. |
| Initialization | Background placement advances offsets in 7 m increments, at most 200 attempts to avoid overlap | Use a deterministic packing procedure with retained chosen offsets. Exhaustion reports the actual conflicting route/entities; no unverified last placement is accepted. Configured count beyond 60 requires explicit generated demand, not truncation. |
| UAV motion | Alpha follows five waypoints at 7 m/s at 145 m altitude, cycling; Bravo circles radius 210 m at configured speed/145 m; backgrounds radius `120+38*i`, altitude `145+5*i`, evenly spaced phase; winner moves directly to 70 m at 8 m/s, snaps within 1.5 m | Default motion model/profile declares these trajectories. Physical plugin owns position/velocity/attitude/path length. Task chains issue typed follow-route/orbit/goto/hold commands. Arrival requires actual pose and stopped/dwell evidence, not a snapped logical flag. |
| Battery | Initial Alpha 91%, Bravo 87%, backgrounds `76+2.5*i`; `round(max(5, initial - distance*0.006),1)` | Optional labeled `legacy_distance_pct/v1` calibration reproduces presentation using actual path length; the 5% floor is deprecated for scientific feasibility. Default uses explicit capacity J and shared consumption model; native profile uses measured fraction/voltage. Do not derive calibrated joules from legacy percentage without an authored capacity/model. |
| Accident trigger/detection | Scene `time_s: 8.0`; reporter progress starts 10 m and stops at 84 m; detection requires active incident and progress ≥83.95 m | Authored timer/injection triggers incident chain. Predicate over committed active/progress state produces detection event. It is state/distance detection, not a visual detector. |
| Medical task | Alpha `medical-delivery-01`, `interruptible:false`, first target offset `[-360,+270]` in old XZ at 145 m; label says remaining flight >180 s | Preserve non-interruption rule and continuing waypoints. There is no package custody, delivery/acceptance or computed >180 s guarantee in old code. Name this a medical-route demonstration; complete logistics requires a separate explicit pack/acceptance extension. |
| LangGraph | `vehicle_report → edge_broadcast → {uav_alpha,uav_bravo} → edge_award`; GraphState `report`, `broadcast`, additive `bids`, `winner_id` | Optional decision graph plugin with immutable role observations, stable node IDs, recorded joins and typed outputs. Runtime chains own report/broadcast/assignment/task state. Keep a compatibility four-model-call profile and recommended rule-assisted profile. |
| Reporter prompt/output | Chinese instruction requests report, selected observed `adjacent_lane_bypass`, route reason and `departure: reroute_when_clear`; includes time, ego, incident, distance, route candidate | Versioned prompt file and typed `ReportProposal`. Validate decision enum/route/departure against current candidate; use public reason and route reason. No prompt text can authorize unsafe physical departure. |
| Bid prompts/output | Per-candidate time/pose/battery/task/interruptibility/distance/ETA/broadcast; output `accept`, public `reason/action`, `alt_target_m:70` | Typed `BidProposal`; decision records preserve input cut. Rule enforces task interruption, known region/feasibility and configured altitude. Model failure is `decision_failed`, not a refusal bid with false eligibility. |
| Model wire call | Compatible `/chat/completions`, temperature 0.1, configured max tokens, 150 s request timeout; old parser finds the first JSON object and old logs omit full prompt/usage/strict tool validation | Pin compatibility settings, strict proposal schemas and total provider deadlines. Record exact prompts/actual usage/reply/validation. Do not reuse permissive substring parsing as a typed tool contract or convert failed calls into valid refusal bids. |
| Broadcast/award | Broadcast is deterministic. Legacy eligibility is only `model_ok && accept && interruptible`. Sorted ETA is shown to edge model; code verifies winner membership, not minimum ETA, region or battery feasibility | Broadcast, authoritative eligibility and exactly-one-award become chains/rules. Recommended minimum-feasible-ETA policy with stable ID tie-break is an explicit stricter revision; compatibility mode allows a model to choose an eligible winner. Region/energy enforcement are new authored rules, not claims about old code. |
| Capture/photo/upload | After 3 s hover, request `capture-NNN`; browser captures winner canvas at its current frame and posts bytes. Server checks run ID but does not validate request/actor/frame/PNG before marking completed | Capture service/renderer plugin with exact cut, request/actor/generation/dwell correlation, actual PNG validation/storage receipt and edge acceptance event. Browser response enters its own stream; missing/invalid upload cannot complete the task. |
| Role cameras | Edge overview offset `(30,220,70)` in renderer axes; reporter eye height 1.6 m/forward 1.1 m; candidate underside camera −0.75 m/forward 0.8 m, looks at incident when awarded; follow cameras for all others; FOV 55°, winner capture 35° | Data-driven camera presets for one existing Three.js viewport/multiview component. Edge overview is a camera role anchored to incident, not an invented edge-node position. Camera metadata and source cut accompany images/videos. |
| Frontend controls | Load scene/start, phase/actor/background selector/logs/winner/photo/results; no shared graph view or proper run pause/replay cut | Existing Studio/Runs/AgentConsole extended with profile configuration, validation, effective run control/injection, graph+3D, shared selection/timeline, typed decision/receipt/cause panels and artifact links. |
| Server API | `/api/{start,state,result,capture,video,recordings-done}` plus static files | Use `/v1/studio`, `/v1/runs`, feed paging/SSE, K4 ingress and run-scoped artifact APIs. No independent demo `Runtime` or mutable `/api/state`. Artifact endpoints below are proposed additions. |
| Recording | Five live 15 fps VP8 WebM views, plus MP4 conversion; `record_views.mjs` enumerates every actual identity, batches six, 640×360/15 fps, default 2× replay, 750 kbps, 1 s tail hold | Optional generic run-view recorder consumes frozen WAL/feed. Preserve live vs replay recording provenance and all CLI controls, actual source interval, speed, failed/pending/completed views. Rendering/transcoding failure does not silently reduce expected view count. |
| Documentation/packaging | `assignment.py` builds Word/PDF/talk/video index/submission manifest; `package.py`/`run.sh` distribute an old Python 3.10 environment | Scenario bundle and platform install/CLI replace vendored environment/server. Optional report exporter derives verified metrics/artifacts from WAL; course documents remain optional export templates. No fictional member names or packaged wheels are required to run the example. |

## 2. City, roads and frozen source assets

The inspected `scene.json` labels Shanghai Huangpu, bounds `[-540,540,-540,540]` in old XZ, has 414 building placements, 60 routes, and road layers: asphalt 434, sidewalk 1163, concrete 42, markings 1585, arrows 735, medians 10. Route metadata says native SUMO motor-edge connections/internal junction curves and closed loops. That is source-export provenance, **not proof this demo ran SUMO**: `runtime.py` uses polylines. Preserve corners/connector curves; never replace a route with endpoint chords.

Incident source lane is `--2682428834550472112#0_1`; opposite adjacent lane is `-2682428834550472112#0_1`. Incident old position is `(x=91.9499764938994,y=0,z=93.03744113461998)`, lane progress 102 m, trigger 8 s. Define a named `traffic-demo/local-enu/v1` frame with conversion `[east,north,up]=[x,-z,y]` from old renderer coordinates; retain original coordinates, conversion revision and heading convention. This conversion yields incident `[91.9499764938994,-93.03744113461998,0.0]`. The zero altitude is an **explicit flat-road model assumption**. A geodetic origin/vertical datum is not present in inspected scene metadata and must be recovered before claiming WGS84/native-world alignment. Verify GLB axes/scales instead of applying this transform twice.

Assets include `web/assets/models/{car,uav}.glb`, individual `buildings/building-*.glb` with embedded textures, road geometry in scene JSON and local Three.js dependencies. The short NOTICE says assets were reused from the workspace for a classroom demo; it does not establish complete redistribution rights or original textured city production. Pin hashes/byte counts, upstream source, licence/attribution, axes and required decoder revisions in an asset manifest. Reuse authorized files as independently packaged platform assets or select an explicitly labeled public-source replacement. Do not silently substitute a different city while claiming visual parity, or run AeroGraph/old-tree build scripts.

Explicit initial conditions must preserve source values where the compatibility profile claims to do so. Reporter starts at lane progress 10 m; incident cars start about 48 m from their final stops (bounded by lane geometry). Alpha starts at incident old-XZ offset `(65,30)`, height 145 m; its five waypoint offsets are `(65,30),(-360,270),(360,275),(-345,-280),(350,-270)`, all at 145 m. Bravo starts at `(210,0)`, height 145 m, then follows the 210 m circle; its initial task target `(200,65)` is superseded by the actual orbit target. Background UAV `i=0..5` starts at angle `i*2π/6`, radius `120+38*i`, height `145+5*i`. Convert every old-XZ offset `(dx,dz)` to ENU `(dx,-dz)` once. The default physically bounded profile can improve motion/energy handling, but those differences must be named rather than advertised as historical reproduction.

## 3. Proposed registry and state ownership

Native IDs directly verified in AeroGraph are `oo:UAV`, `oo:GroundVehicle` (abstract), `oo:ComputeNode`, `oo:Task`, `oo:WindField`, `oo:ModelObject` (abstract), `oo:ObservationRecord` (abstract), `he.aircraft.position_enu_m` and `oo:digital_twin.wind.horizontalVelocity`. The position field is a metre three-vector declared on UAV with a frame supplied by the record/config; wind is an array of east/north two-vectors in m/s. Preserve proposed/candidate review dispositions and source hashes; selecting a definition does not approve it. `agp:type:RoadSegment` exists as an unresolved/quarantined profile; use an explicit local concrete road type instead of asserting native road-model readiness.

All `aas:Traffic*` types, `traffic.*` fields/relations/messages below are **proposed scenario-local overlays** to create, not existing or approved AeroGraph vocabulary. Instantiate only selected active fields; do not populate every inherited descriptor with default values.

Declare common `traffic.actor.*` fields on selected `oo:Vehicle` ancestry, which covers the local road/UAV subtypes; declare road/UAV/task/coordinator/record fields on their named concrete overlay types. Field applicability and lifecycle domains are separate from the owner column. At bootstrap, route-input owners create routes, road/air owners create their actor generations, weather creates its field entity, behaviour creates tasks/coordinator/incident/report/bid/instances, assessment creates assessment records and capture creates capture/recording records. Future native actor discovery remains controlled by SUMO/PX4 adapter lifecycle declarations, with other writers notified after identity commits. Relation owners do not gain lifecycle authority over their endpoints.

| Entity identities/type | Active fields (ID and schema) | Writer/lifecycle and meaning |
| --- | --- | --- |
| `vehicle.reporter`, `vehicle.incident.a/b`, `vehicle.001`…`.060`: `aas:TrafficRoadVehicle <: oo:GroundVehicle` | `traffic.road.position_enu_m` vector3 m; `traffic.road.velocity_enu_mps` vector3; `traffic.road.attitude_xyzw` quaternion; `traffic.road.speed_mps` number; `traffic.road.route_id/lane_id` string; `traffic.road.progress_m` number; `traffic.road.blocked_by` typed-ref array; `traffic.road.safe_gap` boolean with geometry evidence | `road_motion` default plugin; native road owner/converter profile below. Behaviour cannot set these. Finite schema-valid initialization and full occupancy query are required. |
| `uav.alpha/bravo`, `uav.bg.01`…`.06`: `aas:TrafficUAV <: oo:UAV` | Native `he.aircraft.position_enu_m`; local `traffic.uav.velocity_enu_mps`, `traffic.uav.attitude_xyzw`, `traffic.uav.path_length_m`; `traffic.uav.energy_j` or `traffic.uav.battery_native` record `{remaining_fraction,voltage_v}` | `air_motion` owns physical slots. Activate J for kinematic profile; native battery record for PX4. Separate calibrated estimator may own J with measured input causes. Legacy percentage has a separate labeled field `traffic.uav.legacy_battery_pct`. |
| Same vehicle/UAV entities, behaviour role data | `traffic.actor.role` enum `reporter\|incident\|candidate\|background`; `traffic.actor.current_task` typed ref; `traffic.actor.phase` enum; `traffic.actor.route_choice` string; `traffic.actor.public_summary` string | `behaviour` owns logical fields. Role assignment is authored provenance, not inferred from ID prefixes. |
| `edge.coordinator`: `aas:TrafficCoordinator <: oo:ComputeNode` | `traffic.edge.phase` enum; `traffic.edge.radius_m` number 400; `traffic.edge.incident_ref` typed ref; `traffic.edge.winner` nullable UAV ref, explicitly unset until award | `behaviour`; no synthetic readiness/pose/compute telemetry. The graph displays it; 3D offers its incident overview camera. |
| `incident.01`: `aas:TrafficIncident <: oo:ModelObject` | `traffic.incident.active` boolean; `traffic.incident.phase` enum; `traffic.incident.source_kind` enum `authored_staged\|native_contact\|operator`; `traffic.incident.position_enu_m` vector3; `traffic.incident.occurred_at` exact Instant | `behaviour` owns declared event record fields. Position is an authored location or copied, evidenced contact location, not vehicle telemetry. |
| `medical-delivery-01`, `district-patrol-02`, `background-patrol-XX`, road route tasks and `incident-capture-01`: `aas:TrafficTask <: oo:Task` | `traffic.task.kind` enum; `traffic.task.phase` enum; `traffic.task.interruptible` boolean; `traffic.task.episode` integer; `traffic.task.target_enu_m` vector3; `traffic.task.route_ref` typed ref; `traffic.task.capture_altitude_m/dwell_s` number; `traffic.task.deadline_ns` integer | `behaviour` owns task intent/status. Medical interruptibility is authored false. Route plugin supplies physical responses; capture task completion needs storage and edge receipt. |
| Lane entities `lane.incident/lane.adjacent`, route entities `route.vehicle.XXX/route.alpha/route.bravo/route.bg.XX`: `aas:TrafficRoute <: oo:ModelObject` | `traffic.route.points_enu_m` vector3 array; `traffic.route.native_lane_id/native_edges` strings; `traffic.route.source_digest` string; `traffic.route.mode` enum; explicit speed/offset/phase/radius/altitude parameters | Pinned authored route input producer; road/air plugins consume. These are geometry/model inputs, not observed trajectories. |
| `wind.region`: `oo:WindField` | `oo:digital_twin.wind.horizontalVelocity: [[east,north],…]` m/s; only the selected sample is active | `weather` = `environment` default owner. Explicit calm `[[0.0,0.0]]` or declared gust values. Native/model replacement preserves schema/sample/frame provenance. |
| `report.01`, `bid.01.alpha/bravo`: local report/bid types `<: oo:ModelObject` | Typed incident/actor refs; `traffic.report.body`; `traffic.bid.recommendation` enum `accept\|refuse`; public reason; `traffic.bid.input_cut`; recorded `traffic.bid.decision_status` enum, not a coerced acceptance bool | `behaviour` records validated decision outputs. Failed node/call is a failure record, not a valid bid. |
| `assessment.01.alpha/bravo`: `aas:TrafficAssessment <: oo:ModelObject` | `traffic.assessment.distance_m/eta_s/budget_j` numbers, `traffic.assessment.in_region/feasible` booleans, model/pose/task evidence refs | `traffic_assessment` computation plugin. Rule eligibility consumes these computed facts and current task state. Known insufficiency is false; absent source leaves the assessment unresolved with diagnostics. |
| `capture.001`: `aas:TrafficCapture <: oo:ObservationRecord` | `traffic.capture.request_id`, actor/incident refs, `traffic.capture.source_cut`, frame acquisition Instant, camera/asset digest, byte count, PNG content hash, `traffic.capture.storage_status` | `capture` plugin creates the image record and owns capture metadata on actual render/storage results. Runtime availability is kernel publication; native observation stamps are not invented. |
| Recording artifact records | `traffic.recording.view_id`, source run/epoch/generation, frame range/cuts, camera preset, output fps/speed, codec/dimensions/hash/status | Artifact/recording service owns these records; failure/pending/completed status stays separate from simulation-task completion. |

Relations are concrete descriptors with source scope, direction and both cardinality directions declared in `registry.overlay.yaml`:

| Proposed relation | Endpoints and temporal cardinality | Owner |
| --- | --- | --- |
| `traffic.task-assignee` | Task→actor, at most one target per task and at most one active task per actor; close old patrol before awarding capture | `behaviour` |
| `traffic.actor-route` | Actor→route, one active route per actor; many actors may share a route | `behaviour`, representing intent; plugin route progress remains physical state |
| `traffic.incident-participant` | Incident→road vehicle; exactly two in this package, many incidents historically | `behaviour` |
| `traffic.report-incident`, `traffic.bid-task`, `traffic.assessment-bid` | Record→declared subject, one subject per record, many records per subject | `behaviour` for report/bid; assessment plugin for assessment edges |
| `traffic.coordinator-candidate` | Coordinator→UAV, many-to-many, only Alpha/Bravo initially; backgrounds do not enter bids unless explicitly configured | `behaviour` |
| `traffic.capture-incident`, `traffic.capture-actor` | Capture→incident/actor, one each; many captures per subject | `capture` |

Use native `oo:relation:observation-subject` for capture→incident only after compiling its exact directional obligations and compatible endpoint types; otherwise keep the named local relation and record why. Relation assertions and typed subject refs use full EntityRefs including generation. Chain instances use the generic `aas:BehaviourInstance` overlay specified in RUNTIME; no accident type is added to kernel.

Predeclare inactive `incident.01` and its two participant edges in the default bootstrap. Dynamic injected incidents create identity first and bind participants in a following lifecycle/relation wave; activation waits for the exact two-participant guard. Do not impose an immediate minimum-cardinality obligation before those endpoints/edges can legally exist. Award similarly closes the old task assignment and asserts the new one atomically within behaviour's edge authority after physical suspension is confirmed.

## 4. Predicates, chains and conflict-driven workflow

Expressions below are readable definitions of pinned Q6 ASTs, not a new text evaluator. Emit equivalent typed nodes with role/field/path/parameter bindings in `predicates.yaml`. For example detection uses `{op:and,args:[{op:eq,args:[{field:traffic.incident.active,role:incident,path:[]},{literal:true}]},{op:gte,args:[{field:traffic.road.progress_m,role:reporter,path:[]},{parameter:detect_progress_m}]}]}` with `detect_progress_m:83.95`. Complete relation queries select role tuples; no arbitrary graph query or string ID guessing is hidden in the evaluator.

| Predicate ID | Bound expression/source | Profile and use |
| --- | --- | --- |
| `traffic.p.detected` | `incident.active == true AND reporter.progress_m >= 83.95` with initial known false | Reactive `entered` emits `traffic.incident.detected`; no visual inference. |
| `traffic.p.gap_clear` | `reporter.safe_gap == true`, produced by road occupancy calculation with actual pose evidence and borrowed corridor geometry | Reactive `while` permits route start only after current guard; initial clear is usable. Native road plugin also vetoes unsafe command admission. |
| `traffic.p.road_conflict` | `len(vehicle.blocked_by) > 0` over complete physical assessment | Reactive `entered/exited` emits blocked/cleared events, drives waiting/status and stable-duration timers. Collision prevention remains in the physical owner at each proposed step. |
| `traffic.p.medical_locked` | `task.kind == medical_delivery AND task.interruptible == false AND task.phase == executing` | Reactive `while` produces policy refusal/keeps medical chain running. Unknown task facts cannot unlock a candidate. |
| `traffic.p.bid_eligible` | Known successful bid recommends accept AND current task is interruptible AND assessment.in_region AND assessment.feasible AND no active capture assignment | Reactive level guard rechecked at award; false assessment excludes, missing assessment waits/diagnoses. |
| `traffic.p.arrived` | `distance(uav.position, task.target) <= arrival_radius_m AND norm(uav.velocity) <= stopped_speed_mps` | Sampled body over exact pose/velocity, initial default tolerance 1.5 m/0.5 m/s (new speed gate, authored). Physical command success is also required. |
| `traffic.p.dwell_ready` | Native `hold(traffic.p.arrived, 3.0 s, configured maximum sample gap)`; the consuming chain separately requires its succeeded goto/hold receipt | Q6 sampled temporal profile plus runtime receipt guard. A false/unresolved/gapped sample resets or blocks the window according to pinned native semantics. Claim only sampled stability, not unseen motion. |
| `traffic.p.capture_accepted` | Matching outstanding request, awarded actor generation, source cut with valid dwell, validated stored PNG and independent edge acceptance receipt | Typed events/fields joined by reactive guard. Never enabled by timer, HTTP 200 or artifact pathname alone. |

Assessment distance is 3D to the 70 m capture target; region membership is explicitly defined as horizontal distance from coordinator's **incident-region anchor**, ≤400 m. ETA in the default simple profile is route length/configured speed with stated acceleration/hold allowance; native feasibility uses a configured planning model, distinct from measured native flight. The assessment writer records that model and pose cut. Medical remaining time is computed from the actual configured remaining route only if the model supports it; the old >180 s label is not evidence.

Rules form these templates, automatically bound on entity creation and task/route/relation assertions:

1. `traffic.road_follow`: every road vehicle+route task starts follow command; states `initializing→following↔waiting→finished/failed`. Road conflict events update logical state, not pose. Incident vehicles use staged approach/stop tasks. Backgrounds loop; route wraps are explicit continuous connector motion or declared discontinuities.
2. `traffic.uav_routine`: UAV+medical/patrol task issues waypoint/orbit commands; states `ready→executing→suspending→suspended/failed`. Alpha stays executing when new incident candidates are broadcast. Suspending Bravo waits for real stop/cancel/short-leg completion according to profile before capture replaces its task edge; no competing goto.
3. `traffic.incident_report`: authored timer 8 s or injected `traffic.inject.accident` activates incident, commands participant stopping, then detection event starts reporter decision/rule. Validated report creates report relation, emits `traffic.report.sent`, and independently starts reporter `waiting_gap→bypassing→network_continuing` chain. Reporter continuation does not wait for UAV award/capture.
4. `traffic.solicit_award`: report triggers region broadcast; dynamically bind bid decisions to configured candidates, join responses/failures with a 20 s simulated online deadline (new configurable policy), compute assessments, enforce medical/region/energy constraints, and commit exactly one task-assignee edge. States `broadcast→collecting→selecting→assigned/no_candidate/failed`. Stable ordering uses ETA then full identity. A model recommendation is never itself a reservation.
5. `traffic.capture_execute`: awarded task issues goto then hold; states `dispatching→enroute→dwelling→capture_requested→storing→edge_acceptance→completed/failed`. Receipt rejection/failure branches retain physical state. On dwell exit/invalidity, reset the dwell window. Capture request timeout (default proposed 10 simulated s online) leaves task failed/pending per authored policy, never completed. Capture and upload are separate actual events.

Do not hardcode all instances. Bind road templates by `aas:TrafficRoadVehicle` ancestry plus task/route relations, routine UAV templates by `oo:UAV` ancestry/task kind, bid templates by coordinator-candidate relation, and capture template by an awarded capture task. Instance keys include relation/task episode and generation. Two events attempting one award conflict through one behaviour transaction/reservation, producing a recorded loser result.

Sampled pose/dwell→chain→motion feedback must declare a positive return lag; the A2 verification profile declares 66,666,667 ns on all incoming messages of the shared behaviour partition, alongside explicit 1 Hz physical publication (§12). Reactive task-only cascades can proceed at later microsteps of the same ns. Q6 remains sampled once per physical time; do not silently resample it in a chain loop.

For the bounded default demo, predeclare inactive `incident-capture-01` plus one arrival/dwell sampled context for each configured candidate; award activates the task/guards without rebinding a sampled role. Bind native-profile pose and velocity as separate role aliases to the same UAV generation with their actual Gazebo/boundary-observation clocks. Injected additional incidents can always spawn reactive report/award chains; further sampled capture episodes require predeclared task/context slots in a configured finite pool, until generic context lifecycle support is implemented. Do not claim unbounded sampled context spawning from current Q6/K4. Configure the actual lagged feedback partition/route for sampled events, including task-target inputs to the upstream cone.

## 5. Plugins, profiles and decision modes

| Profile/plugin | Physical/environment ownership and commands | Additional work/support limits |
| --- | --- | --- |
| `kinematic-default` | `traffic_road_kinematic` owns road physical fields; `traffic_air_kinematic` owns UAV pose/velocity/attitude/path and selected modeled energy, uses current kinematic/model interfaces where appropriate; `environment` owns wind | Both traffic wrappers are proposed domain plugins, not new generic kernel code. Road follow/occupancy and UAV orbit/waypoints require implementations. Commands proposed: `traffic.road.follow/stop/bypass`, `traffic.air.follow/orbit/goto/hold`; result schemas distinguish rejected/accepted/executing/terminal with actual measured progress. |
| `sumo-road` | Existing `sumo` adapter owns native XY/lane/speed/route fields. Explicit frame conversion plugin owns `traffic.road.position_enu_m/velocity_enu_mps/attitude_xyzw`, with actual native samples and pinned geometry/datum causes | Bind native command IDs `adapters.sumo.{add_vehicle,set_speed,reroute,change_target,lane_restriction}` through scenario capability map. Convert heading and speed explicitly; unavailable elevation is not 0 unless flat-road assumption is configured. Native accident-contact mode differs from staged-stop profile; identify the source kind. Opposite-lane bypass feasibility is unverified and may require an explicit legal route/controller profile. |
| `px4-air` | `px4_gazebo` owns UAV native ENU pose/velocity/quaternion, armed/mode/landed and battery record; explicit calibrated battery-energy estimator may own J | Commands `adapters.px4_gazebo.{arm,takeoff,goto,hold,land,disarm}`; wait for real receipts/observed gates. Current adapter has no image field and does not advertise generic cancellation. Use bounded short patrol legs and their completed receipts before capture; unsupported preemption fails profile validation. Startup launch/70 m world datum are explicit, not a teleport to legacy 145 m. |
| `weather-calm/gust` | Current `environment` writes explicit nested wind samples; shared wind consumption model reads the selected vector | Energy effect only in default analytic model; no current position drift. Native Gazebo weather application needs a real command bridge/readback and separate smoke gate. If absent, label native weather entity as observed environment only and do not claim physical wind coupling. |
| `traffic_assessment` | Owns distance/ETA/budget/region/feasibility; reads committed physical state, tasks, wind and planning calibration | Reuse current shared consumption/budget APIs; no duplicate independent formula. Native planning calibration is authored, not an actual measured flight-energy model. |
| `traffic_langgraph` | Decision plugin owns node/call records and typed proposals only | Optional LangGraph extra, pinned version to be chosen/verified (old report claims 1.2.14; not installed/verified here). Reuse provider deadline/grants/tools. No direct mutation of task/pose, no second clock. |
| `traffic_capture` | Renderer/plugin owns camera requests/results and artifact observations; content-addressed store records actual bytes/digest | Default Three.js simulation-camera provider can run in a controlled browser worker, so completion does not depend on an operator keeping the tab open. PX4 still uses labeled simulation-camera output until real Gazebo image transport exists. |

Switching profiles creates a new run with ownership recompiled; no hot writer transfer or automatic fallback to kinematics when SUMO/PX4 is unavailable. Native sample fields and normalized conversion outputs have different IDs/owners. Prefer original IDs for authored actors; native discovery uses a declared mapping/prefix and full generation, verified before any command.

The SUMO profile also needs an explicit assessment producer for safe-gap/blocked-by and route progress in the normalized road contract. Its native XY field is `traffic.sumo.position_xy_m`; frame conversion owns ENU pose/velocity/attitude, SUMO owns compatible lane/speed/route fields, and a geometry computation owner supplies corridor occupancy/progress with native sample causes. No second road-kinematic engine runs beside SUMO. PX4 owns direct ENU UAV pose and measured battery; a trajectory/assessment plugin computes path length or planning results into distinct fields without integrating a competing flight state.

Baseline default publication step is proposed `66666667 ns` (explicit 15 Hz approximation), with configured rational source stamps; recording remains 15 fps. Pin zero initial velocity or moving initial velocity **as authored initial conditions**, route/altitude parameters, positive speed/acceleration, seed and run limit. Native profiles choose their actual supported grids and source mappings instead of copying this step blindly. The baseline wind profile explicitly supplies `[[0.0,0.0]]`; any gust and consumption coefficient are authored/calibration inputs.

Decision modes are explicit in the scenario:

- `rules`: no model or LangGraph calls. Typed report/bid/award outputs come from configured deterministic policy and have `source: configured_rule`.
- `fixture`: no model calls; stable node/input-signature keys read typed recorded/stub responses and explicitly authored latency schedules from `decisions.fixture.json`. Historical public responses may seed fixtures, but their old observations/times cannot be represented as new live measurements. Missing/mismatched fixtures fail with a source path; do not select a default winner.
- `live_compat`: reporter, Alpha, Bravo and edge award are four actual model decisions; broadcast is a rule. Keep node names and GraphState concepts. New hard feasibility constraints still apply, with compatibility differences recorded.
- `live_rules`: recommended; reporter/report explanation and candidate recommendations remain LLM, while broadcast, hard policy, minimum-feasible-ETA award and execution/capture are rules. Alpha may receive a model explanation but cannot override the lock. Award explanation is optional and cannot delay an already committed physical command unless explicitly configured.

The online profile preserves motion while models wait through K4 decision ingress streams and recorded availability, not the old mutable graph thread. Offline fixture/rule execution uses authored deterministic availability. The current generic decision engine is synchronous; an initial `offline_blocking` live profile can reuse it but must warn in its configuration description that simulation time is fixed during inference. Do not claim it reproduces the old online latency. Graph reductions sort node/actor IDs before joining, preserve `report/broadcast/bids/winner_id` as typed decision data, and record prompts, observation cuts, actual responses, usage, public summaries and tool/owner receipts. All decisions are revalidated at application time.

## 6. Files and configuration to create later

These are planned paths, not files delivered by D1. Common scenario composition must be an implemented explicit compiler step; today's loader cannot be assumed to support arbitrary `include` or `behaviours` keys. Save the fully resolved `aeroagentsim.scenario/v1` plus new package format in each run.

```text
scenarios/demos/traffic-accident/
  scenario.yaml                   resolved default recipe, seed/frames/owners/engines/run
  registry.overlay.yaml           local types/fields/relations/messages/subject paths
  registry.snapshot.json          selected real AeroGraph closure + provenance
  predicates.yaml                 Q6 pinned definition closure/roles/profiles/evidence
  behaviours.yaml                 RUNTIME v1 templates/bindings/conflict/injection rules
  entities.yaml                   explicit primary/background identities and initial tasks
  routes.json                     native-exported loops, lane geometry and UAV parameters
  decisions.fixture.json          declared responses and latency/input-signature fixtures
  cameras.yaml                    role presets, exact-cut capture/record settings
  profiles/{rules,fixture,live-compat,live-rules,sumo-road,px4-air,weather-gust}.yaml
  prompts/{vehicle-report,uav-bid,edge-award}.txt
  assets.manifest.json             licensed inputs/digests/frame/attribution pointers
src/aeroagentsim/packs/traffic_accident/
  {plugin,road,air,assessment,commands,reporting}.py
src/aeroagentsim/agents/langgraph_plugin.py
src/aeroagentsim/observations/{capture,artifacts}.py
tools/demos/{import_traffic_accident,record_run_views,export_traffic_report}.*
```

Large GLBs/images/videos are content-addressed external assets, never duplicated into source Git. Asset recipes/manifests and small independent input fixtures belong to the platform. The importer reads frozen old data; it does not require the old runtime, environment or an old-tree build on subsequent runs.

Every `config.json` option has a destination:

| Old key/default | New destination and explicit meaning |
| --- | --- |
| `host:127.0.0.1`, `port:5420` | Platform serve deployment options, outside scenario physics; one console host/API. |
| `model_endpoint:http://127.0.0.1:8788/v1` | `traffic_langgraph.provider.base_url`, with configured credential environment name; actual endpoint readiness, no auto-fallback. |
| `model:glm-5.3-flash` | Pinned decision model name in live profile/run records. |
| `model_max_tokens:131072` | Per-call completion ceiling plus invocation total budget/max calls/public prompt bytes. Retain 131072 compatibility setting explicitly; recommended budgets are separate authored choices. |
| `tick_hz:15` | Physical publication clock profile and recording fps as separate settings. Exact 1/15 s is not integral ns: specify rational source stamps and pinned nearest-ties-even canonical mapping, or choose an explicit fixed step (e.g. 66666667 ns) and record the approximation. Do not reuse floating accumulation/wall timer as simulation time. |
| `background_cars:60`, `background_uavs:6` | Typed demand/initialization counts; validate route coverage and identity ranges, create routine chains automatically. Zero is valid only when explicitly authored. |
| `uav_speed_mps:8.0` | Air-model speed/capture ETA planning calibration; Alpha's separate 7 m/s routine remains explicitly configured. |
| `capture_dwell_s:3.0` | Task dwell predicate duration with sample-gap policy and receipt/pose evidence. |
| `region_radius_m:400.0` | Coordinator incident-region horizontal eligibility predicate. Old code broadcast the number but did not enforce it. |

Additional declared parameters are seed, exact steps/clocks, run limit, source latency/late-input policies, launch assumptions, acceleration/energy capacity/reserve, arrival/stopped tolerances, sample gaps, decision/capture deadlines, staged/native accident source, borrowed-lane policy, asset transform and recording presets. These values are scenario assumptions, not hidden default telemetry.

Create distinct named `operator`, `decisions` and `capture` streams using Q9 `ingress_streams` and K4 policies. Operator injection `traffic.inject.accident` targets the behaviour gateway with typed incident/participant refs, authored location and source-kind payload. Decisions submit typed validated proposals to behaviour; capture submits typed artifact-storage results. `capture` may explicitly select late displacement to preserve a delayed frame's acquisition stamp, while operator control can select rejection. Each source has a configured finite wait/deadline and explicit progress/closed-prefix assertions, not closure inferred from observed timestamps. Fixture mode replaces live sources with the recorded offline schedule; it does not leave a live watermark silently unresolved.

## 7. End-to-end console operation and dual views

1. In `/studio`, select Traffic accident example. Choose motion and decision profiles, camera source, authored weather and seed. Edit counts, routes, incident time/location, task interruption, radius, speed/dwell/deadlines. The configuration graph shows types/roles/task-route/candidate relations and trigger→chain links; the 3D preview shows authored placements/roads only, labeled preview.
2. Validate. Display source dispositions, field-owner table, frame mappings, available plugin capabilities, predicate/chain errors and chosen timing/latency effects. Resolve missing producer/route/asset/unsupported command with a concrete config change. Native profile readiness is checked separately and never reported from preview success. Export/import the same files and confirm semantic digest.
3. Run now freezes exact inputs and opens `/runs/<id>?mode=live`. Both panes share run/epoch/generation IDs and one selected cut. Initial graph shows medical/patrol/road chain instances; 3D shows corresponding actual initial poses. Selecting edge/task/wind opens exact nonspatial state without placing it at origin.
4. At the authored accident event, graph highlights cause→incident→participant stop commands/receipts→detection predicate transition→report chain. 3D shows actual participant stopping and reporter approach. Inject another event through its named stream at a selected future admissible time or while paused; show occurrence, admission, publication and stream closure separately.
5. During report and bidding, graph shows reporter reroute and candidate chains concurrently, model calls/joins, lock/eligibility predicates and evidence cuts. 3D shows reporter waiting/bypassing, Alpha continuing medical route and Bravo patrol. Actor cameras and public decision/receipt panels select the same identities. Unknown feasibility shows its missing input path, without a displayed zero ETA.
6. At award, graph shows one task-assignee edge, closed patrol assignment, measured command receipts and capture chain state. 3D follows the winner's actual flight to 70 m; rejection/unsafe/failed preemption remains visible. Pause/resume operates at a safe settled boundary, with pending/effective state shown; timeline playback controls only display.
7. During dwell/capture/upload, graph exposes sampled arrived/hold truth intervals, request, actor/camera source cut, byte-storage receipt and edge acceptance. 3D displays exact capture cut/camera, and artifact pane displays the PNG only after actual stored bytes. Timeouts/errors leave the task incomplete/failed with causes.
8. Inspect completion and exports, or seek any prior journal index/microstep. Both views and AgentConsole return to recorded chain/predicate/pose/task/decision values at that cut. Replay makes no model/native/camera calls and cannot recapture the incident. Optional recorder creates new **derived replay videos** from the immutable run, labeled source interval and speed; it does not alter the run.

Proposed artifact APIs are `POST /v1/runs/{id}/capture-artifacts` (outstanding request ID, actor EntityRef, exact source cut, camera manifest revision, acquisition stamp and bytes), `GET /v1/runs/{id}/artifacts/{digest}`, and a recorder/export job API. Store and validate bytes first, then submit a typed storage result through `capture` ingress with idempotency keyed to request/content. The runtime validates source-cut dwell and current request/actor before edge acceptance. Network retries reuse the key; changed content conflicts. Acquisition and later availability remain distinct; no caller-supplied frame time becomes kernel publication time. The console is one client of this provider contract, not the authority establishing photo success.

## 8. Results and legacy artifact migration

The historical `run-20261008-030056` files report: accident 8.067 s, detection 12.333 s, report/reroute/broadcast 21.667 s, award 32.733 s, arrival 60.467 s, capture request 63.467 s, frame 63.533 s, upload 65.133 s, winner Bravo. `decisions.jsonl` has 10 mixed public decision/action entries and **four actual `model_response` entries** (reporter/Alpha/Bravo/edge). Do not count every action log as a model request. The view manifest records 979 source frames, interval 0–65.133 s, 2× playback, 640×360/15 fps, 72 views. These are inspected historical artifacts, not results reproduced by D1 or targets for a new latency/model policy.

| Old artifact | New equivalent |
| --- | --- |
| `trajectory.jsonl` snapshots | Kernel `journal.jsonl` plus indexed viewer feed; optional legacy snapshot export from exact cuts. Old trajectory import is a labeled archival visualization and cannot reconstruct missing authoritative receipts/causes. |
| `events.jsonl`, `decisions.jsonl` | Typed events/evaluation/decision records with cuts, prompts/settings/actual replies/usage, causes and command correlations in WAL; optional readable exports. |
| `state.json`, `result.json` | Final projected state and metrics/result export from WAL, including actual task status and error prefix; retain history, do not replace it with one mutable state. |
| `incident.png`, `image_url` | Content-addressed image artifact + camera/acquisition/source-cut metadata + actual storage and edge receipts. Historical 45,537-byte PNG is historical simulation imagery, not a new capture fixture by default. |
| Five root WebM/MP4 videos | Live recording artifacts with wall/display/source timing and camera provenance; transcodes reference source digest. |
| `videos/*.mp4`, `views-manifest.json` | Offline recorder outputs/manifest with enumerated actual identities, completed/failed/pending views, source interval/frames, speed/fps/dimensions/codecs/digests. Defaults produce 72 views for the declared configuration, not a universal constant. |
| `demo-overview.png`, `screen-{patrol,reporting,bidding,enroute,capturing,awaiting_capture,completed}.png` and related screenshots | Optional exact-cut stage captures, stored independently from vehicle camera evidence. |
| `report.md` and course submissions | Optional report/docx/pdf/talk/video index exporters based on journal/manifest; claims link to actual receipts/artifacts. Preserve unavailable member names explicitly. |
| Old archives/run scripts/dependency cache | Explicit platform scenario/assets bundle and environment recipe; no runtime dependency on old checkout/wheels. |

No global simulation success is inferred from video export success, and a video encoding error does not rewrite a completed capture task. Reports include profile, source kind, decision latency policy, model calls/errors, non-interruption, unique award, reporter continuation, pose/dwell/capture/upload evidence, artifacts and source assumptions.

## 9. Acceptance tests for implementation

These are **future acceptance requirements**, not D1 test results. Use the prescribed Python 3.11 interpreter with this worktree's `PYTHONPATH=src` and `MYPYPATH=../aerokernel`; test/build/run artifacts go under the implementation job's `/tmp/aas-q/<job>/`. Run relevant non-Docker suites plus lint/strict types for implemented files; native Docker gates stay separate and explicitly reported.

| Gate | Test and observable assertion |
| --- | --- |
| Deterministic no-LLM full run | Select rules and fixture profiles explicitly; run twice with same pinned inputs/schedule/seed and semantic run/epoch/serializer IDs in separate output directories, compare WAL bytes in offline deterministic mode with a pinned labeled PNG fixture and its declared availability. Model/provider constructors are forbidden. All routine instances are automatic, no duplicate ancestry/edge matches; incident detected once from known baseline; reporter passes incident and continues original network; Alpha keeps original task/moves; Bravo alone receives award; receipts→arrival/stopped→3 s sampled dwell→validated PNG/storage→edge acceptance→completion. Then run rules with the actual headless renderer for the separate actual-image gate below. Do not claim byte-identical live renderer latency/outputs without measuring it; a metadata-only fixture cannot pass actual-image gate. |
| Policy counterexamples | Alpha closer than Bravo but noninterruptible; accepting model output cannot assign Alpha. Out-of-region, insufficient energy, absent weather/pose/task, blocked borrowed lane, stale bid and conflicting simultaneous awards produce the actual wait/refusal/rejection/failure path. Missing model result/fixture/route is not a false bid or fabricated success. Changing counts to 0/less/more verifies explicit demand and view enumeration. |
| Timing/ownership | `set` of physical fields fails at compile; two writers fail binding; commands cannot complete on HTTP/admission acknowledgement. Inject at arbitrary open future boundary/paused state with original stamps; K4 stream-specific idempotency/reject/delay/progress tests retain cause/displacement. Slow decision/camera stream cannot close another stream; shared seal waits where dependencies require it. Same-ns cascades remain finite; sampled feedback without explicit lag fails validation. |
| Capture/recording | Real headless Three.js renders nonempty decodable PNG with correct request/actor/source cut/camera hash; stale generation, wrong actor/cut, duplicate conflicting bytes, corrupt/empty data and upload timeout cannot complete. Dwell resets after failed/unknown pose coverage. Live five-view and offline all-view outputs decode, report actual frame counts/speeds, and retain per-view errors. At default counts expected identities are 71 physical + edge overview = 72. |
| Live LLM | `live_compat` with endpoint/model/budgets pinned makes four actual calls for one complete successful decision episode; `live_rules` uses its declared count. Capture actual prompts/replies/usage/deadlines and tools. Concurrent Alpha/Bravo outputs join deterministically by node identity. Schema-invalid/timeout/transport/model-policy errors remain visible and cannot choose a default winner. Do not require historical wall/simulation timings or identical live replies. |
| Zero-call replay | Remove/disable model/native/camera services; replay every semantic prefix and final cut. Assert provider, LangGraph, plugin factories, sensors and renderer are never invoked. Compare exact entity generations, all active fields/retractions, relation intervals, predicate truth/status/evidence, chain revisions/children, command/receipt histories, decision records, artifact digests and final status with the recorded run. Fault prefixes replay as incomplete. |
| Console integration | Real API browser test: import→edit predicate/task/profile→validate→export/reimport semantic digest→run→pause/effective resume/inject→select spatial and nonspatial graph nodes→seek microstep cut while tail grows→inspect both views and AgentConsole→open actual photo→replay. Assert no synthetic origin/zero values, exact inspector values, shared selection/cursor, recorded truth/state, transport error retention, zero page errors. Compare same authorized city/assets/camera traces before claiming visual parity; functional software rendering is not a GPU benchmark. |
| SUMO profile smoke | Real existing SUMO service/network with pinned transform/demand: vehicle identities/native fields move, staged stop/reroute yields actual readback/receipts, normalized pose matches native geometry at known cuts, one field writer, capture graph still completes where route capabilities permit. Unavailable opposite-lane route is an explicit unsupported profile result. Do not substitute kinematic poses. |
| PX4 profile smoke | Real PX4/Gazebo service: launch/arm/takeoff, completed patrol leg, award, goto/hold at authored datum, actual pose/speed dwell and battery/source clocks; land/cleanup receipts. Camera remains labeled Three.js unless real image bridge is tested. Retain failed native trajectories; no widened tolerance/default battery/preemption success. Replay exact native result with services off; no byte-identical native rerun claim required. |
| Weather/independence | Explicit calm/gust profiles produce recorded wind facts and shared modeled budget/consumption changes; missing weather blocks rather than becoming calm. Native physical gust influence requires separate actual readback test. Run/import/replay/render with old demo checkout unavailable and independent authorized assets; no old absolute/default path remains. |

## 10. Six implementation jobs and integration order

Use separate git worktrees. Each writer owns the paths listed; no two jobs edit the same file until integration transfers ownership. They are not alone in the repository and must preserve/adapt other jobs' changes. Share registry/message/profile/IR examples early, pin Q6/Q7/Q9 integrated revisions, and submit concrete diffs/tests instead of declaring work merely scheduled.

| Job | Owned files/responsibility | Dependencies and reviewable finish |
| --- | --- | --- |
| A — generic behaviour runtime | `src/aeroagentsim/behaviours/`, `engines/behaviour.py`, compatibility `engines/{workflow,threshold}.py`, `tests/behaviours/`; sole owner of additive `scenario/loader.py` and `platform/plugins.py` hooks | Start after pinning Q6/Q9 APIs. Compile format/ownership/dependencies, auto binding/lifecycle, reactive wrapper and sampled reuse, actions/receipts/timers/limits/journal/replay. Incorporate Q9 loader/catalog changes first; no domain code here. Finish minimal nonspatial chain and converted workflow regressions. |
| B — scenario/domain and asset migration | `scenarios/demos/traffic-accident/`, `src/aeroagentsim/packs/traffic_accident/`, `tools/demos/import_traffic_accident.*`, `tests/demos/traffic_accident/test_{scenario,road,air,policy}.py`; one registration descriptor proposal to integrator | Starts in parallel with A on agreed schema. Freeze inventory/licences/transforms, local registry/provenance, physical road/UAV/assessment plugins, native replacement profiles, templates/predicates/prompts and fixtures. Finish deterministic domain trace and native-profile config/capability validation; A's hook owner integrates registration. |
| C — LangGraph/online decisions | `src/aeroagentsim/agents/langgraph_plugin.py`, any owned helper `agents/langgraph_*.py`, `tests/agents/test_langgraph*.py`, optional LangGraph dependency declaration proposed to integrator | Reuse existing provider/tools; pin Q9 ingress and A's typed proposals. Stable node/join IDs, deadlines/grants/usage/observations, source streams and explicit modes, stale-result checks. B owns scenario prompts/fixtures; C supplies their contract. No pose/task writer. |
| D — captures/artifacts/exports | `src/aeroagentsim/observations/`, `services/artifacts.py` and its artifact route hook (sole ownership of `services/app.py` integration), `tools/demos/{record_run_views,export_traffic_report}.*`, `tests/observations/` | Starts on shared capture messages/camera metadata. Use Q9/worker submission path rather than editing worker in parallel. Actual renderer bridge/storage/typed receipts, source-cut correlation, recordings/manifests/report exporter. Coordinate service hook with A/C/Q9; finish real PNG and corrupt/stale/timeout cases plus recorder decode gate. |
| E — console/dual views | `frontend/src/{studio,pages/RunsPage.tsx,pages/AgentConsole.tsx,feeds,contracts/viewer-feed.ts}`, new `frontend/src/behaviours/` and `observations/` components/tests; sole owner of `authoring/{api,workspace,templates}.py` behaviour editing hooks | Incorporate Q7 graph/state store first. A/B provide schema and D camera API. One draft model/forms/graph/raw round trip; profile/ownership controls; same selected identity/cut in graph/3D/decisions; exact inspector/artifact status. No new npm dependency assumed; if required declare it. |
| F — projector/integration acceptance | `src/aeroagentsim/services/projector.py`, `tests/demos/traffic_accident/test_{replay,integration,native}.py`, `tests/demos/traffic_accident/*.mjs`, integration gate records/docs after D1 ownership ends; sole owner of final `pyproject.toml` registration/optional-extra edits | Begin feed fixture/projection with A/E contract, then run composed no-LLM/live/replay/browser/native gates as B/C/D arrive. Only F edits shared packaging; other jobs submit exact metadata requirements. Merge dependency order Q6/Q7/Q9→A contracts→B/C/D→E/F final integration; record actual counts/errors/skips and independent assets. |

A's executor and F's feed projection can proceed against small typed fixtures while B/C/D build independent domain/decision/camera parts. E extends Q7 instead of replacing its work. Integration preserves one kernel, one behaviour execution path, one set of state owners and one console. Native smoke prerequisites do not prevent completing the deterministic kinematic example; unsupported native profiles remain explicit until the missing real capability is implemented.

## 11. Unverified assumptions and D1 validation

The original city geodetic anchor/vertical datum, complete asset redistribution rights/native scene recipe, exact SUMO demand/network path matching all exported loops, opposite-lane native bypass and capture-capable Gazebo sensor bridge were not verified. They need real source/configuration, not default geometry or substituted success. The old report's LangGraph version and >180 s medical label were not established by executable dependency/delivery evidence. Historical video durations/content were read from manifests/reports, not decoded or re-rendered in D1.

Q6 operator semantics/exports, Q7 graph/store surface and Q9 loader/ingress API may change; their final revisions require pinning. Native energy/arrival/weather coupling differs from analytic defaults. Automatic dynamic sampled contexts, the new package/import composition, online decision runner, camera artifact contract and all proposed `traffic.*` descriptors are work to implement. The concrete new thresholds, deadlines, hard region/energy gates, speed/dwell evidence and minimum-ETA policy are explicit research-model changes; they do not reproduce historical timings by definition.

D1 validation is source/contract review and Markdown/diff checks only. No runtime, ruff, mypy, pytest, frontend, LLM or Docker success is claimed. Two concurrent requested GLM writer invocations and both retries were launched with `workbuddy/glm-5.3-flash`, 131072 output budget, no effort, separate scratch ownership and project context; all exited 1 with WorkBuddy proxy transport failure before a model response or draft. Logs/configurations remain under `/tmp/aas-q/d1/`; no completed GLM session/result was available for integration. Only `docs/platform/RUNTIME.md` and this file are repository deliverables.

### Job D implementation and validation (2026-10-08)

Job D adds the generic capture metadata owner, persistent Playwright bridge, run-local hashed PNG storage, additive artifact/upload routes, exact committed-prefix scene route, replay MP4 recorder and journal-derived Markdown exporter. [observations.md](observations.md) specifies the typed contracts, ownership/config, E's canvas API and F's exact entry-point/package-data requirements. No frontend, worker, projector, shared packaging, domain pack or old demo source was changed. Generated runs, media and logs remain under `/tmp/aas-q/d/`.

Scope differences and reasons:

- The initial capture engine is an explicitly blocking render profile. It renders the requested committed cut while simulation time waits; online motion during camera latency requires an independently scheduled camera source. Upload admission uses the existing Q9 ingress and never implicitly closes a stream watermark. Generic upload verification emits a separate typed event; B's behaviour owner must validate incident/dwell/current-request evidence before edge/task acceptance. A renderer timeout has typed result `timeout` inside a failed kernel receipt because the kernel has no terminal timeout status.
- The real-image gate uses a built small Three.js viewer with recorded kernel poses and hashed HTML/Three assets. It exercises exact cuts, camera changes, large integer times, actual WebGL pixels and storage; E/F must attach the same API to the final console. City visual parity, historical 72-view parity, five-role live recording and native SUMO/PX4 gates are not claimed by this isolated job.
- Recorder/report entry points are offline CLIs. A background export-job HTTP API is deferred to composed service integration; the additive service hook here covers artifact storage/upload and live committed-prefix reads. Missing ffmpeg/encoder/render tools remain explicit failures. Installed ffmpeg lacks libx264, so media tests explicitly select `mpeg4`; there is no codec fallback. Optional DOCX/PDF needs installed pandoc/LaTeX, unavailable here and not executed.
- F owns `pyproject.toml`: capture registration and `.mjs` package data are provided as exact metadata in observations.md, rather than editing F's file. No new npm dependency is needed.

Validation used the worktree sources and prescribed Python 3.11 interpreter. Final observation gates pass **28 tests**, including real Chromium PNG, stale/forged requests, invalid typed requests, corrupt bytes/metadata, REST integrity/admission, zero-render replay, large source ns, partial/gapped indexes, actual browser-to-MP4 encoding and decoded frame counts. Affected non-Docker platform/adapter/agent/pack/authoring suites pass **400 tests**, with **4 Docker tests deselected**. The initial broad run lacked `AEROAGENTSIM_AEROGRAPH_ROOT`; it failed fixture setup. The corrected run reads AeroGraph inputs only and passes. Ruff and strict mypy pass for all changed Python files (17 source files); frontend checks were not run because frontend was untouched. No Docker gate was run.

Exact successful commands (task-specific variables below only shorten the fixed paths):

```bash
task_d_python=/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python
export PYTHONPATH=src
export MYPYPATH=../aerokernel
export AEROAGENTSIM_AEROGRAPH_ROOT=/mnt/data2/weizhiwei/AeroGraph
export AEROAGENTSIM_FFMPEG=/usr/share/anaconda3/bin/ffmpeg
export AEROAGENTSIM_CHROMIUM=/home/weizhiwei/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell
TMPDIR=/tmp/aas-q/d/tmp "$task_d_python" -m pytest -q -p no:cacheprovider -m 'not docker' tests/observations --basetemp=/tmp/aas-q/d/pytest-observations-final3
"$task_d_python" -m pytest -q -p no:cacheprovider -m 'not docker' tests/platform tests/adapters tests/agents tests/packs tests/authoring --basetemp=/tmp/aas-q/d/pytest-platform-env
"$task_d_python" -m ruff check src/aeroagentsim/observations src/aeroagentsim/services/artifacts.py src/aeroagentsim/services/app.py tools/demos tests/observations
MYPYPATH=src:../aerokernel:. "$task_d_python" -m mypy --strict --explicit-package-bases src/aeroagentsim/observations src/aeroagentsim/services/artifacts.py src/aeroagentsim/services/app.py tools/demos tests/observations
```

Mypy's additional source roots disambiguate the tools/tests namespace packages; kernel imports still resolve to the prescribed sibling checkout. Logs are `observations-final3.log` and `platform-tests-env.log` under `/tmp/aas-q/d/`.

Two GLM writer sessions ran concurrently with separate report/recorder ownership and project context, using `workbuddy/glm-5.3-flash`, 131072 output budget and no effort. They produced reasoning/tool activity but no usable files before being stopped; their drafts are not claimed as completed. Two subsequent concurrent, read-only GLM audits completed with exit 0. Their actual outputs were reviewed: index continuity/closed-prefix and explicit decision-schema concerns are covered, while incorrect claims about the speed formula and already-checked artifact hashes were rejected. Session/config/output records remain under `/tmp/aas-q/d/dsh-home/`, `glm-report/`, `glm-recorder/`, `glm-check-record/` and `glm-check-report/`.

## 12. Recorded A2 runtime timeline

The shipped scenario now composes the behaviour executor, road/air physical owners,
four predeclared Q6 arrival/dwell contexts, explicitly scripted decision proposals,
and real browser capture/storage/upload verification. Alpha's actual medical-task
lock rejects interruption; the edge selects Bravo using committed eligibility and
ETA, waits for its stop receipt, then changes the assignment. Capture completion
requires real goto/hold receipts, sampled dwell, stored PNG bytes and independent
upload verification. Reports/bids use finite authored record slots rather than
inventing runtime identity expressions.

This verification profile declares **1 Hz** physics/publication, a 2 s maximum
sample gap and 3 s stable dwell. It also declares a **66,666,667 ns return lag on
the shared behaviour recipient**, affecting every incoming event/receipt. Field
reactions remain reactive. These are timing changes from the historical 15 Hz
source; the domain regression harness separately retains the original 15 Hz grid.
Detection reads actual stopped speeds and reporter/participant proximity (20 m),
and the incident/capture target uses the participant's committed pose at activation. Thus the
early injected accident does not rely on a frozen route-progress threshold.

The HTTP case creates a live worker with operator watermark zero, posts typed
`aas.runtime.inject_event` to `POST /v1/runs/{id}/ingress` at 3 s with injection
point `accident`, then explicitly closes the operator prefix through 90 s.
Two recipient hops make incident activation available at 3.133333334 s. The CLI
case closes its offline prefix at startup and uses the authored 8 s timer. Both
activate the same finite incident/task slot; arbitrary new incidents require
additional authored sampled contexts. The test does not infer source closure
from event timestamps.

The generated table below includes only committed `Emit` proposals, excluding
enqueue/dispatch copies. Regenerate it with:

```bash
PYTHONPATH=src /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python \
  scenarios/demos/traffic-accident/timeline.py TIMER_RUN_DIRECTORY HTTP_RUN_DIRECTORY
```

<!-- Generated by scenarios/demos/traffic-accident/timeline.py; times are sim seconds. -->
| Event / chain transition | Authored timer (s) | HTTP injection (s) |
| --- | ---: | ---: |
| Accident activated; stop commands issued | 8.000000000 | 3.133333334 |
| Stopped/proximity predicate → detection | 13.000000000 | 11.000000000 |
| Reporter proposal → report sent | 13.266666667 | 11.266666667 |
| Edge broadcasts capture task | 13.333333334 | 11.333333334 |
| Alpha refuses its non-interruptible task | 13.433333334 | 11.433333334 |
| Bravo offers its interruptible patrol | 13.633333334 | 11.633333334 |
| Eligible minimum ETA → award proposed | 13.700000001 | 11.700000001 |
| Actual stop receipt → award committed | 15.066666667 | 13.066666667 |
| Goto receipt → hold command | 46.066666667 | 46.066666667 |
| Hold receipt → dwelling | 48.066666667 | 48.066666667 |
| Sampled 3 s dwell → capture requested | 49.066666667 | 49.066666667 |
| Real PNG persisted | 49.066666667 | 49.066666667 |
| Stored bytes verified; edge accepts | 49.266666667 | 49.266666667 |
| Receipt-backed capture chain completes | 49.333333334 | 49.333333334 |

Timer run `scenario-140075023735`: journal SHA-256 `c5d53631aea2bce4ff53c61f612ed6c86dbd7f89a98abf9ab83cfb0ae0653240`; PNG SHA-256 `5d04e0a9ffd8abb490c5500b0476e5113459373f8848fe73d3aca5aa8943bf98`; camera cut `{'index': 2143, 'instant': [49066666667, 2]}`.
Pinned package `7fc6a0db99006b04c1f50194ffb086e1e43a56a02504270e1e4c1a9fd3b97c79`, IR `01517cab66231bb571b6ae7ad812283108e2f4097998b9511e2aa6f6892ba2be`.

HTTP run `run-1c63cfc2458e4c0abf59971b0e833cb8`: journal SHA-256 `b5882139885ec56591121cd4c6f7f7f94b9e753314c563e11645dba6fe42c37b`; PNG SHA-256 `3dc27e56a00f70389252f7503f342ca60d071c5d86726af07232c9edead92f9f`; camera cut `{'index': 2160, 'instant': [49066666667, 2]}`.
Pinned package `7fc6a0db99006b04c1f50194ffb086e1e43a56a02504270e1e4c1a9fd3b97c79`, IR `01517cab66231bb571b6ae7ad812283108e2f4097998b9511e2aa6f6892ba2be`.


Capture uses actual Chromium WebGL with a pinned small Three.js viewer, primitive
vehicle geometry and the recorded pose snapshot at the requested cut. It produces
simulation-camera PNGs, not licensed city imagery or native camera pixels. The
installed browser executable is explicit in the scenario. Native SUMO/PX4, live
LLM, frontend operation and historical 72-view/video parity remain separate gates.

The target stays at its measured activation location while physical stopping waits
for the owner's publication boundary. In the early-injection journal the primary
vehicle stops 6 m beyond that target and remains in Bravo's actual 70 m camera
footprint. The operator event stops the two predeclared participants wherever
they are; in this early case the second participant is about 59 m from the camera
target. This is a staged accident response, not a computed collision or evidence
that both vehicles share one location. The reporter completes its authored bypass at
24.066666667 s in the timer case. In the early case it waits for the authored
safe-corridor predicate rather than attempting a disconnected bypass. Reporting
and capture do not require that separate departure chain to finish.

A2 validation commands (from `wt-a`; generated runs/logs remain under
`/tmp/aas-q/a/a2/`):

Final gates: **624 passed, 6 deselected in 4412.04 s (1:13:32)**, with the prescribed
complete suite run once. Ruff and strict mypy are clean on all 18 changed Python
files. Both complete 90 s scenarios pass the end-to-end trace and physical-pose /
PNG-integrity checks. CLI replay forbids plugin construction, predicate evaluation,
decision callbacks and rendering, then compares the separate header and every
subsequent expanded journal record; the reconstructed prefix is complete. Two
retained timer runs and two retained operator runs also have identical WAL and PNG
digests for their respective schedules. This measured equality does not claim
equivalence to the historical 15 Hz run.

The initial CLI test timed out on wall time after the business chain had completed;
its host timeout was extended without changing simulation time. A later replay
comparison failed because it included the header in `Kernel.records`; the kernel
exposes that header separately. The corrected comparison has its own representative
zero-call contract test and passes in the complete scene. Earlier targeted failures
also exposed oversized repeated causal lists, missing native adapter wiring and an
unavailable default browser revision; the implementation now shares recorded road
evidence, declares the finite Q6 pool/lag, and selects an actual installed browser.
No successful photo or receipt was synthesized on those failures.

The six deselections follow `not docker and not llm`. Docker, live LLM, actual
SUMO/PX4 replacement runs and frontend checks were not run; frontend was untouched.
The observation gate did exercise actual Chromium PNG and ffmpeg export. No commit
was made. Full-gate, ruff and mypy logs are `full-gate.txt`, `ruff-final.txt` and
`mypy-final.txt` under `/tmp/aas-q/a/a2/`.

```bash
task_a2_python=/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python
export PYTHONPATH=src MYPYPATH=src:../aerokernel:.
task_a2_files=(
  scenarios/demos/traffic-accident/timeline.py
  src/aeroagentsim/behaviours/{compiler,records,sampled}.py
  src/aeroagentsim/engines/behaviour.py
  src/aeroagentsim/packs/traffic_accident/{camera,capture,decisions,road_motion}.py
  src/aeroagentsim/platform/{plugins,simulation}.py
  src/aeroagentsim/scenario/loader.py src/aeroagentsim/services/app.py
  tests/demos/conftest.py
  tests/demos/traffic_accident/{test_end_to_end,test_policy,test_runtime_contract,test_scenario}.py
)
"$task_a2_python" -m ruff check "${task_a2_files[@]}"
"$task_a2_python" -m mypy --strict --explicit-package-bases "${task_a2_files[@]}"
export AEROAGENTSIM_AEROGRAPH_ROOT=/mnt/data2/weizhiwei/AeroGraph
export AEROAGENTSIM_CHROMIUM=/home/weizhiwei/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome
export AEROAGENTSIM_FFMPEG=/usr/share/anaconda3/bin/ffmpeg
TMPDIR=/tmp/aas-q/a/a2/tmp "$task_a2_python" -m pytest -q -p no:cacheprovider \
  tests/platform tests/adapters tests/agents tests/packs tests/authoring \
  tests/integrations tests/behaviours tests/demos tests/observations \
  -m 'not docker and not llm' --basetemp=/tmp/aas-q/a/a2/full-gate
```

Two actual GLM writer sessions ran concurrently with separate decision/capture
ownership, project context, `workbuddy/glm-5.3-flash`, 131072 output budget and no
effort. Their sessions/tool activity were inspected. Neither produced a usable
verified implementation before being stopped; their drafts were replaced and are
not claimed as completed contributions.
