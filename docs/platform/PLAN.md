# AeroAgentSim platform consolidation plan

P0 design, 2026-10-08. AeroAgentSim becomes the general simulation platform; the independent `aerokernel` package supplies its execution semantics. AeroBench contributes engines, authoring, agent integration and visualization, then disappears as a runnable product. Neither kernel nor platform core assumes aircraft, spatial entities, logistics, or a particular engine sequence.

The normative authority is [aerokernel DESIGN.md](/mnt/data2/weizhiwei/aeroagentsim/aerokernel/docs/DESIGN.md:1), especially §§2–9 and §13. Its current implementation is concurrent work, so the API below targets that specification rather than claiming implementation readiness. [REPORT.md, Round 2](/tmp/aas-survey/REPORT.md:88) supplies architectural reasoning. MIGRATION.md and INVENTORY.md are discovery aids; their classifications do not override this boundary. In particular, world/config compilers, executors, gateways and predicate languages belong outside the kernel.

Source references use these read-only roots: **A** = `/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform`; **B** = `/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim/aero-bench`; **G** = `/mnt/data2/weizhiwei/AeroGraph`; **K** = `/mnt/data2/weizhiwei/aeroagentsim/aerokernel`. Ranges identify extraction candidates, not permission to copy entire modules. B and the audit are changing concurrently: implementation must pin the source content and reconcile moved functions. This plan reports static inspection, not measured engine performance.

## 1. Architecture, packages and replacement model

The dependency rule is `aerokernel ← aeroagentsim core ← engines/scenario packs/services/frontend contracts`. Arrows mean “is depended on by.” Frontend consumes versioned HTTP/stream contracts, not Python modules. Kernel imports no platform package; core imports no installed engine implementations or domain packs. Optional plugins enter through declared entry points and manifests. Services may compose plugins, but engines never depend on an HTTP server or viewer.

```text
aerokernel/                         independent repository/distribution
AeroAgentSim/
  pyproject.toml                   platform distribution; pinned kernel dependency
  src/aeroagentsim/
    core/                          Simulation, RunSession, plugin contracts/catalog
    scenario/                      YAML loading, binding resolution, artifact inputs
    integrations/aerograph/         registry compiler, dialect normalization
    engines/
      kinematic/  workflow/  predicates/  decision/
      px4_gazebo/  sumo/  ns3/      optional host adapters
    packs/
      logistics/  inspection/      domain models, editors, scenarios, metrics
      city/  examples/             spatial authoring and explicitly authored examples
    services/
      control/  storage/  replay/  authoring/  agent_gateway/  launcher/
    compat/v1/                     bounded migration adapter
  frontend/                        React + TypeScript + Vite
    src/{shell,registry,studio,runs,viewport,agents,contracts}/
  containers/{px4-gazebo,sumo,ns3,agent}/
  schemas/  scenarios/  tests/  tools/
```

Platform core wraps `Kernel.bind/start/submit/cancel/run_until/view/close`; it does not introduce another mutable world, scheduler, receipt ledger or public commit path. A run selects arbitrary types, active fields, relations and engines. The kernel resolves one writer per instance-field and relation source scope, plus separate lifecycle controllers. A nonspatial order, radio signal or record can exist without pose. Spatial indexes are derived consumers; an index cannot become a second physical writer.

Keep the default installation small: core, YAML loader and fast engines. Put FastAPI/uvicorn, native simulator libraries, model clients and optimization dependencies in explicit extras or separate plugin distributions. Raise the platform baseline to Python >=3.10; use Python 3.11 for development. The independent kernel remains pure stdlib at runtime. Frontend builds with the available Node 22 toolchain; native libraries stay in their containers.

| Existing abstraction | Replacement and migration source |
|---|---|
| Environment | Host `Simulation`/`RunSession` around kernel, scenario and services. Remove automatic aviation-manager installation and rounded float time (`A:src/aeroagentsim/core/environment.py:48–116`). |
| Agent | Registered entity plus optional decision partition; identity is independent of behavior. Replace mutable state/task queues (`A:src/aeroagentsim/core/agent.py:130–208`) with committed reads and typed commands. |
| Component | Capability binding selecting fields, relations, command schemas and producer partitions. A capability profile supplies semantic applicability, not executable behavior. |
| Task | Typed action with acceptance, feedback, result and cancellation; optionally an `oo:Activity`/task entity when persistent business state is needed. Physical completion belongs to the accepting engine. |
| Workflow | Reusable DES transition engine; each pack supplies state machines, typed trigger dependencies and transitions (`A:src/aeroagentsim/core/workflow.py:115–234,376–433`). |
| Trigger | Typed event subscription, predicate result dependency or partition-owned timer (`A:src/aeroagentsim/core/trigger.py:53–142`). No undeclared string callback bus or fabricated cron interpretation. |

Recommend a bounded compatibility adapter for one migration release. New code imports `Simulation`; `from aeroagentsim import Environment` resolves lazily to `compat.v1.Environment` only with the compatibility extra installed, with a migration notice. Without it, provide an actionable import error. Translate supported templates, `create_agent`, commands and `run(until=...)` into compiled bindings, explicitly quantizing seconds. Ship a conversion command and equivalent runnable scenarios. Arbitrary SimPy generators, mutable `agent.state`, custom subclasses and visualization-driven tasks need migration; do not retain a parallel legacy runtime and label its logs kernel replay. Remove the root alias in the next major release; users needing full historical execution can pin 1.x separately.

This matters because legacy task execution depends on `visual_update` (`A:src/aeroagentsim/core/task.py:128–152`), and movement writes position and battery itself (`A:src/aeroagentsim/task/mobility.py:100–175`). Viewer cadence must have no influence on the new simulation.

## 2. Engine plugins and their contracts

Every plugin declares partitions, produces/consumes selectors, lifecycle domains, messages, history requirements, lag/latching and timing capabilities. These resolve against scenario descriptors; there is no mandatory motion/network/business stage. v0.1 calls are serial. Native simulators can internally parallelize; independent runs can occupy separate workers.

All adapters implement the normative common-boundary protocol: physical advance to the granted time, reactive microsteps without reintegration, sampled evaluation after upstream settlement, then seal. Logical holds do not fabricate native samples. A command arriving at 3 ms to a nonsplittable 20 ms integrator is applied after its 20 ms integration. Irreversible peers require exact stops or certified holds; unexpected early return faults the recorded run. Each output preserves acquisition/source clock, actual availability and validity, plus causal delivery references. RPC timeout or lost connection publishes no partial batch and triggers no automatic stateful retry.

### Kinematic default

Use one fleet partition initially, with configurable fixed steps and explicit spatial field bindings. It consumes destination/control commands, model parameters and selected environmental forces; produces bound position/velocity, completion feedback and modeled contacts. A separate energy owner consumes movement/sensor/compute usage and writes the selected energy fields once. Models need declared units, frames, validity and calibrated parameters; their outputs are labeled simulation results.

Extract straight-line integration and arrival ideas from `A:src/aeroagentsim/task/mobility.py:100–164`, not its state mutation or missing-speed default. Rewrite as a deterministic engine supporting `move_to`, `hold`, cancel and measured arrival; generic core never names those commands. Remove independent battery deductions, visual timers and “progress implies success.” Spatial entities may be vehicles, equipment or other moving types. Begin with stdlib arrays/loops; optimize behind the same adapter after fleet profiling.

### Workflow/DES

Partitions cover activity execution, resource allocation and lifecycle controllers; packs choose their domains. Consume typed receipts, relation changes, predicate results and timers. Produce activity state, reservations/edges, child commands and business events. DES advertises the next internal event or infinity, reacts at committed microsteps and never advances physical engines itself.

Port transition-definition ideas from `A:src/aeroagentsim/core/workflow.py:115–234,376–433` and timer semantics from `core/trigger.py:381–443`. Replace unreachable `status_changed` transitions, floating time and callbacks with registered schemas and explicit cancellation/cleanup. Generic resource allocation remains reusable; charger, payload, contract and mission policies live in packs. No default logistics workflow in core.

### PX4/Gazebo lockstep

Use one physics-world partition per Gazebo world, with separate declared autopilot and sensor partitions where their publication/latching requirements differ. Gazebo supplies ground-truth pose/contact/sensor state; PX4/MAVSDK supplies autopilot estimates, mode, armed state and available health/energy telemetry. They bind distinct fields. The same entity can also receive business fields from DES.

Extract `RealPx4Stack` into a slim native container: `B:containers/px4-gazebo/service.py:4701–4857` for lifecycle/epoch, `4879–5024` for paused stepping, `5387–5500` and `6192–6248` for telemetry, `6635–6666` for action translation. Keep actual WorldStatistics time and pause confirmation. Rework completion checks from `8748–8930,8931–9218`; replace staged benchmark orchestration at `9271–9610,10694–10928` with kernel grants and receipts. Preserve native transport-ACK reconciliation only when observed simulator time establishes the actual result; never repeat a kernel advance blindly.

The host adapter speaks `aerokernel.rpc` JSONL to the container shim over a loopback connection or subprocess transport, negotiates capabilities and pins logical/native frontiers and input cuts. Retain alignment tests, fresh source stamps and real takeoff/move/hold/land/cancel outcomes. Drop provider tokens, evidence writers, sealing, fixed scene coverage and parcel/inspection policy from the native backend. Camera rendering remains an optional genuine sensor path. The existing local flight image supplies PX4 v1.17-alpha/Gazebo Harmonic 8.11 for the first experiment; pin its digest. README still labels the formal provider path blocked (`B:containers/README.md:1–7`); source existence does not demonstrate readiness.

### SUMO

One TraCI partition per network owns selected road actor kinematics and traffic-light fields; a lifecycle controller creates/removes bound actors after native evidence. It consumes routes, speed/control inputs and restrictions via pack-defined commands; emits departures, arrivals, collisions and command outcomes. Use native-grid lockstep with exact TraCI time, certified intermediate holds and next-boundary input latching.

Port `B:containers/sumo/service.py:1497–1571,1632–1722,1771–1938,2138–2227,2313–2392`. Extract task restrictions at `1973–2038` into pack handlers. Replace evidence/provider envelopes. Requested arrival/TLS capabilities must use actual TraCI APIs: the empty-set/list paths at `1725–1742` can otherwise masquerade as known empty state. Terminal removal ends measurement validity; retained last pose remains historical, not a fresh zero-speed sample. Share compiled roads/frames with the viewer. Cross-simulator contacts require an explicit obstacle-mirroring/coupling adapter, with documented delay and collision authority.

### ns-3

Use a network DES partition per coupled network, consuming interval mobility, interfaces, radio/channel parameters and send commands. Produce packet delivery/drop, mailbox/link measurements and actual native event timestamps; send acceptance means queued, not delivered. Sub-boundary occurrences become available at the agreed return boundary unless the backend exposes exact communication points. Feedback into control needs declared lag or supported discrete reaction.

Port client semantics from `B:aero_bench/providers/ns3/config.py:10–68`, `protocol.py:9–16`, `provider.py:477–731,1016–1110,1150–1243,1245–1308`. Current QoS is best-effort/priority-0 (`provider.py:121–135`). No `containers/ns3` backend source exists in this checkout: recover reproducible source or implement a small ns-3 bridge before claiming integration. Docker/client stubs cannot pass this milestone. Drop stage dependencies, participant grants and evidence-envelope coupling; preserve real delivery traces and supported QoS limits.

### Business/logistics and inspection

Domain partitions own orders, offers, assignments, facility reservations, transfer records and acceptance assembly. Consume action results, physical presence, observation records and independent acceptance inputs; produce business state, temporal edges and domain events. Order arrival is DES; service/policy decisions cannot write motion.

Port `B:aero_bench/tasks/logistics/orders.py:461–501`, `order_arrivals.py:708–1116`, and `containers/logistics-business/service.py:1436–1565,1854–1995`. Extract parcel transitions around `tasks/logistics/native_parcel_runtime.py:822`; retain inspection geometry/sensor/report contracts from `tasks/inspection/formal_v2_contracts.py:293–375,602–646,758–882` and analysis from `formal_v2_verifier.py:429–625`. Move them into packs, with benchmark verification optional. Replace fixed PX4 tool bindings and independent clocks. Pending physical-observation capabilities (`service.py:170–177`) need real producers. Inspection collecting bytes must not pretend to produce a rendered camera image.

### LLM decision engine

Partition by decision session/subject group. Consume authorized committed projections and events; produce typed engine commands, decision records and declared policy fields. Model/vendor code lives behind the agent gateway. Offline mode waits explicitly for each scheduled decision while simulation time stays fixed; online mode uses recorded ingress/watermarks and explicit latency policy. Record prompts, input cuts, model settings and actual responses for replay; replay never calls a model.

Port `B:aero_bench/agent/bridge.py:83–160,242–330,363–459,655–722` and `gateway/dispatcher.py:86–260,634–765`. Retain strict tool schemas, budgets and observation scoping; replace provider side effects with kernel command reservations/action queries. Isolate `agent/codex_driver.py:579–629,809–849` and `model_proxy.py:655–822` as optional drivers. Drop all-agents-must-finish-turn barriers, benchmark credentials/seals and successful-decision synthesis after model failure.

### AeroGraph evaluator

Partition by native dialect and sampled context/dependency cone. Consume exact bound field/relation histories, role identities, scoped parameters and clocks; produce typed evaluation records and eligible events. Keep native AST authority: `G:semantic-directory/src/predicate_format.py:106–263,482–560`, `original_runtime.js:442–533`, `expanded_runtime.js:143–200,412–449,472–529`. First use a pinned Node sidecar with a lossless RPC shim, or a differential-tested Python port; do not import predicate languages into kernel.

Sample once after the full upstream cone settles. `entered` requires known false→known true at different physical sample times with matching identity/source/clock. First true, missing inputs and a newer unresolved frame do not create an event. Record diagnostics, fix the producer path and rerun; displaying UNKNOWN alone is not integration. Positive-lag return paths avoid sampled feedback cycles.

## 3. Ontology compilation and scenario format

AeroGraph contains 970 conceptual types across seven browsing directories. The current [audit](/mnt/data2/weizhiwei/aeroagentsim/aerokernel/docs/audit/aerograph-audit.md:21) finds no concrete runtime writer bindings; descriptive/static readiness is not simulation readiness. Compile selected closures, not all fields on every entity. Full-catalog browsing may remain available separately.

The compiler reads source JSON without building or modifying G. Preserve raw descriptors, actual inheritance, abstractness, review disposition, ASTs and content hashes. Normalize source-backed role dialects, local schema references, tagged unions and explicitly directed relation cardinalities into the kernel subset. Keep configuration separate from observations; map `Own/Oi` and writer descriptions through scoped scenario bindings. Record every normalization/temporal/frame interpretation. Never infer missing units, frames, producer identities, cardinality directions or approval. Unselected imperfect definitions do not block the selected scenario.

Relevant source: `G:entity-directory/README.md:28–43`, `semantic-directory/data/state.schema.json:5–103`, `relation.schema.json:5–64`, `src/expanded_semantics.py:406–438,459–521,771–806`. Seven directory membership and suggested parents do not become inheritance; capability applications remain explicit. Pin compiler version, source hashes, normalization digest, selected registry and binding manifest in the run header.

Platform algorithms receive descriptor handles and semantic input slots, such as a motion adapter’s configured position field. Scenario bindings map those slots to IDs. Generated Python/TS constants are conveniences from the pinned registry, never a hand-maintained ontology enum. Missing descriptors fail binding with a path to the required producer/conversion. The inspector enumerates descriptor schemas. Plugin command names and visuals are likewise declared metadata.

Proposed YAML surface, an authored example rather than telemetry or an already implemented parser:

```yaml
format: aeroagentsim.scenario/v1
id: p1-kinematic-order
seed: 42
ontology:
  source: {path: /mnt/data2/weizhiwei/AeroGraph, revision: selected-content-digest}
  types: ["oo:UAV", "oo:Order", "oo:ObservationRecord"]
  research_selection: {scope: this_scenario_only, preserve_source_disposition: true}
  overlays:
    - {id: "aas:KinematicObservation", parent: "oo:ObservationRecord", abstract: false}
  fields:
    position: "he.aircraft.position_enu_m"
    acquisition: "oo:shared.observationRecord.acquisitionTime"
    availability: "oo:shared.observationRecord.availableTime"
  contracts: [exp.contract.governance.observation_delivery_latency, aas.p1.reached_x]
frames: {world: {convention: ENU, unit: m, transform_revision: p1-local-1}}
clocks: {simulation: {mapping: canonical_ns}}
engines:
  motion: {plugin: kinematic, step_ns: 10000000, frame: world}
  operations: {plugin: workflow, definition: packs/examples/p1-order}
  observations:
    plugin: workflow
    definition: packs/examples/observation-pipeline
    release_delay_ns: 20000000
    create_on: sample.release
    id_rule: obs/<capture-source-version>
  evaluation: {plugin: aerograph-expanded, parameters: {maxLatencyS: 0.05, thresholdXM: 10.0}}
bindings:
  lifecycle:
    - {type: "oo:UAV", ids: [uav-1], controller: operations/entities}
    - {type: "oo:Order", ids: [order-1], controller: operations/orders}
    - {type: "aas:KinematicObservation", id_prefix: obs/, controller: observations/create}
  fields:
    - {subject: uav-1, field: position, writer: motion/fleet}
    - {type: "aas:KinematicObservation", field: acquisition, writer: observations/acquire}
    - {type: "aas:KinematicObservation", field: availability, writer: observations/publish}
  manifests: [packs/examples/p1-order.bindings.yaml]
  control_return_lag_ns: 10000000
initial:
  entities:
    - {id: uav-1, type: "oo:UAV"}
    - {id: order-1, type: "oo:Order"}
  owner_inputs:
    motion/fleet: {uav-1: {position: [0.0, 0.0, 0.0]}}
    operations/orders: {order-1: {aas.p1.order_state: submitted}}
  commands: [{schema: aas.example.order.submit, target: operations/orders, order: order-1}]
storage: {durability: flush, artifacts: local-content-store}
```

Included manifests expand aliases, active fields, subscriptions, typed payloads, lifecycle selectors, relation authority/obligations and validity. Initialization is partition-owned reset input, not permission for the loader to write another owner’s fields. The concrete observation overlay preserves the abstract source type (`G:entity-directory/data/concepts.json:21184–21210`) and is explicitly a simulation extension. Selected inherited metadata also needs producers.

The P1 companion manifest must explicitly select and bind this active set: UAV position → `motion/fleet`; `aas.p1.order_state` → `operations/orders`; observation acquisitionTime → `observations/acquire`; availableTime → `observations/publish`; observedSubjectRef and `aas.p1.position_sample` → `observations/acquire`; `oo:relation:observation-subject` → `observations/links`. Other inherited fields/relations remain inactive. Define the local state enum (submitted/executing/awaiting_acceptance/accepted/failed/canceled) and finite three-vector sample (metres, world frame) as explicit scenario capabilities. The pipeline captures a computed motion sample at C, retains its payload and source stamp, and schedules release at C+20 ms. At release its controller creates the deterministic record; owners then publish headers/result and the subject edge in reactive waves. The complete observation is unavailable before release; an availability timestamp alone cannot implement delay.

For a positive transition, the manifest authors `aas.p1.reached_x` as an expanded native AST: UAV role, `gte(position[0], thresholdXM)`, metre units and `entered`. Move from x=0 to x=10. The evaluator records a real false baseline and one later true frame, producing exactly one typed event with cuts/causes. This is a declared local research contract, not an existing AeroGraph approval. The 10 ms control return lag breaks any sampled cone→workflow→motion feedback; observation-latency evaluation separately proves actual delayed inputs and known results.

P1 does not activate the entire UAV propulsor contract without propulsor observations. Native Order acceptance requires resolving the fulfilment descriptor’s missing temporal contract and supplying actual receipt/evidence inputs (`G:semantic-directory/data/expanded/foundation.json:4142–4314,12363–12365`). One assembly partition owns that whole record, consuming independently produced delivery and acceptance messages; two producers cannot write different members of one field. Arrival alone cannot supply accepted quantity. Observation latency uses separate acquisition/availability fields (`expanded/governance.json:568–581`), including native clocks.

## 4. Services, storage and local operation

Run control uses REST for create/list/status, command/cancel, advance, pause/resume/stop and artifact queries; SSE is the default live stream, with WS for interactive subscriptions. Return command IDs/reservation status immediately; acceptance and terminal results come from action history. Reset creates a new epoch/journal. Pause takes effect at a settled safe boundary, freezes advancement and leaves native worlds paused. The host records its advance schedule; viewer refreshes only read it.

Carry API concepts from `A:src/aeroagentsim/visualization/routes/runs.py:49–126,152–261` and stream/control mechanics from `B:aero_bench/control/server.py:208–380,633–735`. Launch one worker process per run; ASGI handles control while the worker owns its kernel. Extract Docker readiness/cleanup around `B:aero_bench/executor/docker.py:773`, plus local composition/readiness from `tools/run_stack.py:114–166,215–270`, removing credential provisioning and mandatory sealing/verifier execution. Compile immutable inputs once; reuse them for start and rebuild only after an input changes.

Storage is `runs/<run>/manifest.json`, normalized inputs, kernel `journal.jsonl`, diagnostics and content-addressed artifact references. SQLite/query shards for trajectories, metrics, relations, events and search are rebuildable indexes tagged with the source cut. Replace the competing stores at `A:src/aeroagentsim/visualization/run_repository.py:68–111,171–220`. SSE cursors use journal indices/item references; reconnect resumes a complete committed prefix, with explicit resnapshot when presentation retention expires.

Replay reconstructs state from the journal without engines, containers, evaluators or model calls. Seeking uses derived offsets or read caches, not crash-resume checkpoints. Incomplete execution remains incomplete; corrupt complete records are rejected. Adapt `B:aero_bench/trace/projector.py:213–370,556–677,869–921` and replay contract concepts, dropping seals and universal SceneState pose requirements. Preserve strict transport validation and lossless integers through REST/TS codecs; browser counters/time use BigInt rather than rounded binary64 JSON parsing.

Authoring ports `B:aero_bench/authoring/api.py:115–313`, `compiler.py:65–154` and `world/scene_compiler.py:2164–2551` as platform/City modules. One compiled geometry/frame source feeds Gazebo collisions, SUMO roads and visual assets. Keep imports, preview jobs, source attribution and compile-to-scenario handoff; preview never becomes run telemetry. The agent gateway exposes only scenario-declared observations/commands and records decision inputs. Local development binds loopback with explicit host/origin bounds and scoped filesystem roots; no account system, credential provisioning or seal ceremony. Add credentials only for an actual remote or separately trusted agent boundary; external model keys stay in that service.

## 5. Frontend and City Studio

Build a React/Vite shell retaining Overview, Catalog, Scenario Studio, Run Console and Runs/Replay navigation, i18n and graph editing (`A:frontend/src/App.js:54–63,152–160`). Convert catalog from four legacy class kinds to registry types/fields/relations/contracts and capability bindings. Reuse relation-graph interaction and useful review forms; replace runtime-generated Python proxy definitions with declarative scenario edits.

Extract Three.js renderer, cameras, picking/layers and asset loading from `B:frontend/src/map.ts:452–490,624–642`; separate React state, render clock, scene resources and overlays. Replace `app.ts:285–338,1454–1497` composition with scoped stores and components. Keep ACES tone mapping, shadows, city batching/LOD and lighting modules. Add optional antialiasing/post-processing behind measured quality settings; current map does not provide an EffectComposer pipeline to port. Sensor camera readback at `map.ts:1270–1309` remains an optional sensor view, not the default viewport workload.

Rewrite direct pose assignment (`map.ts:3595–3612`) as display interpolation between exact committed samples, with quaternion slerp and a small live buffer. Do not interpolate across generation changes, teleports, invalidity or missing samples. Inspector/metrics always show exact values and timestamps. Playback position is separate from the authoritative cut; rendering never advances the simulation. Remove the fixed replay cadence assumption (`frontend/src/state/replay.ts:1–9`).

Visual descriptors choose model URI/digest, dimensions, axes, animations and projection fields. Replace the closed `uav/ugv/pedestrian` table (`B:frontend/src/entity-visuals.ts:15–44`) and kind-color switches. Any registered spatial type can select a visual; nonspatial entities appear in graph/list views without invented coordinates. An explicitly selected generic glyph is presentation metadata. A generic inspector renders any typed field/record/ref, validity, acquisition/availability, writer, relations and related events. Timeline filters kernel journal items, actions and causes, including equal-time microsteps; it is not a synthetic tick log.

City Studio becomes an optional spatial authoring workspace in the same shell. Keep region/OSM import, terrain/buildings/roads, lighting, selection, previews and compile handoff from `B:frontend/src/city-studio.ts:75–89,346–364,570–674,1614–1623`. Migrate `city-authoring-api.ts:121–140`, `osm2world/` and city rendering modules together with backend artifacts. Replace fixed fleet/facility/airspace/logistics unions in `city-workspace-config.ts:42–100` with pack-registered editors. Logistics operations and inspection panels remain useful pack pages; P02 labels and parcel visuals are optional pack content.

Delete runtime mock catalogs/configs and connection-error data substitution (`A:frontend/src/services/workbenchApi.js:29–144,1437–1586`). Connection failure shows stale recorded data with its cut, or an explicit unavailable state. Demonstrations require selecting an authored example scenario. Neither missing type→static asset nor missing time→now is acceptable.

## 6. Milestones and verification gates

Estimates are integration person-days, excluding concurrent kernel implementation. They indicate scope, not delivery promises. Each milestone ships a runnable example, documented command and retained regression fixture. One failure must not force unrelated plugins to wait. Use the prescribed `K:.venv/bin/python` for Python checks; kernel conformance comes from its own I1–I14 suite, not a second platform implementation.

| Milestone | Independently useful result | Acceptance gate | Size / parallel ownership |
|---|---|---|---|
| P1 | Local CLI/API plus viewer replay of kinematic+DES order activity, UAV and concrete ObservationRecord subtype. | Motion success comes from calculated arrival; separate DES consumer acknowledges output. Observe actual C→C+20 ms release and complete field/edge bindings. Known false→true emits exactly one typed reached-x event; missing/first-true cases emit none. Replay without engines reproduces that event, cuts, fields, edges and actions. Same pinned schedule/seed reproduces deterministic bytes. A 1,000-entity workload publishes elapsed time/memory/RTF; changing viewer refresh changes no journal. | 10–15 days; compiler/scenario, fast engines, control/storage and viewer/contracts in separate file scopes after shared contracts are pinned. |
| P2 | Single PX4/Gazebo vehicle using the existing local flight image through kernel RPC. | Actual readiness, native stop/pause/time, takeoff/move/hold/land/cancel and contact results; deliberate stale telemetry/EOF faults without partial publication. Repeat five runs; report startup, source freshness, completion error and RTF = simulated interval / wall interval, excluding startup and also reporting end-to-end cost. | 10–15 days; container backend and host adapter can proceed alongside P1 UI. Fleet scaling follows measurement. |
| P3 | Real SUMO scenario, standalone and coupled to P1/P2. | TraCI-confirmed times, depart/arrive/remove distinctions, TLS, route commands and cancel; 3→20 ms latch test. Viewer roads match engine roads. A coupled example declares whether cross-engine contacts are modeled and verifies the chosen mirror authority. | 6–10 days; SUMO adapter and scene/frame compiler separate ownership. |
| P4 | Reproducible ns-3 backend and network scenario. | Build from retained source; real packet send/delivery/drop and mobility consumption; explicit best-effort limits; recorded event occurrence versus availability. No backend means the plugin remains unavailable while other milestones ship. | 10–20 days after backend recovery; independent backend/client team. |
| P5 | Logistics and inspection packs with sensing/analysis/acceptance records and metrics. | Order pickup/transfer/acceptance and failure/cancel paths use real modeled inputs; receipt/evidence references and units match. An inspection example consumes an actual selected sensor output; bytes/progress cannot substitute for imagery. Test nonspatial records and resource contention. | 12–20 days; logistics and inspection writers own separate packs. Can start after P1 without P4. |
| P6 | Vendor-independent agent gateway and live/replayed decision console. | Real model session issues a declared command, sees exact action results and preserves observation scope. Timeout/malformed tool/cancel do not synthesize success. Offline waiting preserves simulation time; replay performs zero model calls. | 6–10 days; gateway/decision adapter and frontend console independent of native engines. |
| P7 | City Studio and descriptor-driven workbench replace separate authoring apps. | Import an attributed region, edit roads/buildings, preview, compile, run and replay using the same assets/frames. Edit a non-UAV type and inspect fields/relations/events. Compile source and preview errors remain visible. Target 60 Hz interaction on a documented reference scene; publish frame-time/VRAM measurements. | 15–25 days; backend/compiler, editor modules and renderer separate scopes. Start after P1 contracts. |
| P8 | Cutover release with AeroBench retirement and legacy migration. | Capability checklist covers flight, traffic, network, packs, gateway, authoring, control, replay and viewer; useful evaluation add-ons survive. Converted supported examples run; packaging/import checks find no AeroBench/airfogsim runtime dependency. Asset references resolve from a clean installation. | 4–7 days; migration/packaging and docs after integrated gates. |

P2 correctness requires measured RTF, not an invented throughput claim; RTF >=1 is an initial local optimization target, not proof of lockstep or repeatability. The 96 CPUs and three RTX 3090s enable parallel experiments/render workloads, but v0.1 kernel calls remain serial. Measure per-engine startup/CPU/RAM/VRAM, communication cost and journal growth before choosing fleet limits.

Contract gates include unauthorized writes, whole-wave rejection, native cleanup before removal, command cancel/completion races, known versus missing evaluator input, prefix replay and lossless RPC integers. Test faults at actual adapter boundaries; do not merely mirror implementation branches. Browser gates cover seek/reconnect, inspector accuracy and display-only interpolation. Build characterization fixtures for shared geometry and authoring, whose existing coverage is thin. Run targeted checks once; broaden only after changes or unresolved failures.

Parallel writers receive explicit file ownership and shared schemas/fixtures. Compiler owns integrations/scenario resolution; adapters own their engine/container; packs own domain state machines/editors; services own control/storage; frontend owns stores/views. They must not revert other work. Coordinator, schema and package-root changes have one integration owner. Concurrency of implementation work does not imply concurrent kernel calls.

## 7. Removal, assets and risks

Track every valuable AeroBench capability against a destination and acceptance fixture. Preserve native flight/traffic/network, domain packs, gateway, authoring, run control, replay, Three.js, sensor paths and useful metrics/evaluation modules. Retire fixed stage barriers, benchmark participant turns, seal/proof chains, mandatory verifier execution, per-benchmark wrapper images and generated credential catalogs. Convert historical replay through a read-only importer with provenance; do not rewrite it as a native kernel journal or discard research results merely because sealing disappears.

After P8 gates, remove AeroBench CLI/package/imports, containers whose functionality is superseded and its standalone frontend; AeroAgentSim is the only runnable platform. Archive missing backend source requirements before deletion. Remove legacy `airfogsim` package (`A:src/airfogsim/__init__.py`) and old SimPy physical writers after supported conversions and the compatibility window. Carry valuable weather, spectrum, energy, file/compute, resource/economic and sensing models into optional engines/packs rather than losing them through blanket deletion. Rebuild metrics from actual journal inputs, replacing empty promised metrics directories and warn-and-skip environment injection.

Large GLBs, city meshes, imagery, native binaries, datasets and run outputs live outside git in a local content-addressed store or configured object store. Git holds small manifests, checksums, licenses/source attribution, scenario definitions and bounded fixtures. Keep existing large validation/demo results in an archive; exclude them from code migration. Existing `localhost:5000/aero-bench/flight:*` and `traffic:*` images are bootstrap inputs; record digests now and later retag reproducible platform builds. A missing asset remains a visible resolution error unless the scenario explicitly selects another presentation.

The principal risks are conflicting writers, unit/frame/clock errors, stale MAVSDK samples, undeclared cross-engine coupling, abstract/proposed ontology misuse, missing ns-3 source, seal-entangled ports and unbounded history volume. Control them through selected bindings, pinned transformations, source-time tests, explicit approximation policies, reproducible backend recovery and measured storage budgets. Dynamic writer transfer, arbitrary early return, parallel kernel invocation, rollback, crash resume and exactly-once external effects stay outside v0.1. A stopped faulty run retains its valid prefix; reconnecting or changing UNKNOWN labels does not repair its producer path. Repair that path, start a new run and demonstrate the intended computation.

P0 preparation changed only this plan. Three independent read-only source reviews returned engine, service/frontend and ontology findings and were checked during integration. Requested DSH/WorkBuddy GLM execution was unavailable: the launcher attempted a prohibited profile rewrite, and the configured local proxy could not connect. No successful GLM session, build, simulator run or performance result is claimed.
