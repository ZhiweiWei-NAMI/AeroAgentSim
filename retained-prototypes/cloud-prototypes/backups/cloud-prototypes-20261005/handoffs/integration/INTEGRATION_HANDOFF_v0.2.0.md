# AeroBench-centered integration handoff v0.2.0

Date: 2026-10-05. Status: source-backed implementation specification, not a deployed integration.

## Decision

Keep AeroBench as the run, simulation-time, command, provider, evidence and original Three.js-renderer authority. Bring AeroAgentSim business authoring/workflows and the P08 typed graph into that host through explicit adapters. Do not start a second AeroAgentSim physics loop, replace the city with a graph canvas, or present a configured rule as executed.

The first delivery should be one real flight/inspection operation with a visible P08 evidence chain in the original city. Follow it with one actually evaluated P08 rule and an explicit agent/script response. This work can start with the available 265 predicate definitions; it need not wait for recovery of the original 1,686-item catalog. The original 1,686-item catalog is not available to this review. The supplied server status describes an existing AERO_WORLD P01 fixed-rule evaluator with 72 executable contracts across 12 families and 91 bound typed fields; reuse is the first choice. Its confirmed server directory is `/mnt/data2/weizhiwei/AERO_WORLD/Dataset/world_model/rule_factor_graph/`; `predicate_trajectory_models.py` is the model file, not an established evaluator entrypoint. The server coordinator must locate the existing fixed evaluator there. Its source and version were not independently inspected during this review.

## 1. Reviewed baseline

- BENCH public source snapshot: ZhiweiWei-NAMI/AeroAgentSim branch `codex/aerobench-source-config-20261005`, verified HEAD `2abd1108f36449eca22041472296d42b925668d0`, tree `b2eee7eb315c5d04cfcf3d0e074aefe838bdc51d`. Upstream provenance is `repair/inspection-v1-r5 @ ea295073cdd310a31fa2e29317a3571bc716bb87` plus the export's recorded working-tree provenance. The current server checkout was not inspected during this review; reconcile only affected files there before implementing.
- AeroAgentSim cloud workbench HEAD: `68b3d4a65799008e8d3ae0f175faf64d9ef60b44`. Its `src/aeroagentsim/integration` contains real reusable normalization/history/identity contracts, but currently no live connection, native evaluator, command dispatch or original-viewer mount.
- P08: `p08.typed-graph/v1`, contract `1.0.0`; inspected graph contains 20,201 nodes, 64,978 directed multiedges, 1,382 state specifications, 265 predicate definitions and 258 rule definitions. These are preserved definition/source/authored-record counts, not live executions or 1,686 recovered predicates.
- BENCH export excludes the original city GLBs, OSM packs, releases and sealed private run evidence. The original renderer source is available. Use the server's existing authorized asset registration and asset resolver; do not replace missing assets with a different city or republish them.

Source URLs and hashes are in `source_manifest.json`. Refer to those pinned public source URLs; repository-review caches are not included in this handoff.

## 2. Concrete findings that change implementation

1. `HarnessCoordinator._advance_provider_barrier_uncontrolled()` already does motion → SceneState assembly → network → business_environment → runtime hook → final barrier commit. Reuse this order. `runtime_stage`, not a provider's label, decides its stage.
2. `HarnessCoordinator.install_runtime_hook()` permits one hook, installed before prepare. `InspectionRuntimeHook` already handles validated camera observations and real network delivery. Replacing it with a graph hook would break the native business chain. Compose hooks or add a post-native-hook observer in the task package. A rule response must be queued for the next agent turn; a hook must not recursively call or synchronously wait for the next barrier.
3. `ValidatedObservation` contains identities, times and digests, not camera pixels or capture pose. A view-count projector cannot manufacture capture evidence from this callback alone. Correlate the permitted observation payload and its digest with the real `InspectionRgbObservation`/sensor frame; leave missing capture metadata unknown.
4. `RunSession` retains only 240 SceneStates, 1,000 events and 200 transitions. This is a display buffer, not sufficient history for temporal rule evaluation. Keep semantic history in the server-side run/epoch/binding session and use native sealed replay/history for rewind.
5. BENCH `StateSample` already contains exact entity/provider identity, ENU/NED pose/velocity, optional mode/armed/battery/health and sample digest. `SceneState` carries stage evidence. Use those contracts rather than reading mesh transforms as physics.
6. Frontend `ReplayState` and strict JSON parsing reject unsafe integer nanoseconds. P08 requires decimal-string nanoseconds. Convert server-side Python integers directly to decimal strings for the semantic stream. Never turn an already-rounded JavaScript Number into a supposed lossless string. The existing renderer can keep its supported native range initially; unsupported long-duration times must be explicit rather than silently coerced.
7. P08 `ui/app.js:createApp()` is a standalone document-ID-bound app with automatic startup. Reuse `model.js` and `GraphRenderer` for an embedded panel, or refactor `createApp` to accept a scoped root and host callbacks. Do not drop the entire HTML page into the city DOM with colliding IDs. `renderer.js` is a graph visualization, not a replacement city renderer.
8. Existing P08 cases are not directly executable BENCH packages. For example `city.w17.s03.command.definition` names `authored.sensor.view_count`; its predicate has `native_id: null`, and its rule says `engine_status: native_not_connected`. This string must not be sent as a Gateway tool ID.
9. `CityDraftCompiler._lower()` explicitly treats keyframes/weather/landscape as authoring or visual content, not provider state, and only lowers its declared event types. A business-to-BENCH compiler must add supported task lowering, not relabel an existing draft as a physical run.
10. AeroAgentSim `/api/runs` is a native simulator lifecycle API; even listing reconciles native manifests. It is not an external BENCH attachment API. Do not use it as a proxy for BENCH runs.

## 3. Adapter boundaries and file ownership

The following module names are proposed additions, not claims that these adapters already exist. Keep them in the server AeroBench integration scope and avoid publisher/export files.

### A. Business authoring → immutable BENCH configuration

Proposed `aero_bench/integration/aeroagent_compile.py`:

`compile_business_plan(plan_revision, graph_package_digest, explicit_bindings, native_registration) -> CompiledBusinessPlan`

Input: AeroAgentSim workflow/task/agent/policy/resource definitions, P08 source references, selected BENCH registration and exact entity/agent/tool mappings. Output: native task/policy assets, a binding manifest, and inputs to the existing resolver/registered compiler. Freeze this before run identity is calculated. Return unsupported workflow nodes as concrete unresolved mappings; do not pretend all 35 activities lower automatically.

Reuse `CityDraftCompiler`, `ResolvedRunSpec`, the provider registry and existing native inspection task package. Reuse AeroAgentSim business definitions and state-machine structure. Do not instantiate its `Environment`/`SimulationManager` to move the same aircraft in parallel with PX4/Gazebo.

### B. Closed BENCH evidence → P08 immutable facts

Proposed `aero_bench/integration/p08_projection.py`:

`project_closed_tick(scene_state, provider_events, stage_barriers, binding_manifest) -> RuntimeGraphDelta`

Reuse native `StateSample`, `SceneState` and digest functions plus the existing AeroAgentSim `normalize_bench_scene` projection where applicable. Map by exact `entity_id`, not array position or label. Position is `sample.pose.position.enu.{east_m,north_m,up_m}`; velocity is `sample.linear_velocity_enu.{east_mps,north_mps,up_mps}`; renderer coordinates stay `[E,U,-N]`. Actual pose, requested target and accepted command target remain separate.

Each fact has source record/digest, run identity, attachment/attempt epoch, exact subject mapping, clock domain, occurrence/availability/validity times and projection version. BENCH does not supply P08 generation/epoch members automatically: an explicit versioned lifecycle mapping must define them, or exact-identity evaluation stays unresolved. Do not invent them from ID strings. Add new runtime records; never overwrite P08 authored fixture facts/results in place.

### C. P08 rule evaluation

Proposed `aero_bench/integration/p08_evaluation.py`:

`evaluate(binding, evidence_cutoff, history) -> PredicateEvaluation + PredicateResult`

Use P08's exact rule AST, ordered roles, lexical parameters and separate applicability AST. Preserve `True/False/Unknown`; unknown diagnostics are metadata. Every output references the selected fact IDs, rule digest, binding revision and evaluator implementation/version. No current P08 file implements this runtime evaluator.

First compare the existing P01 fixed-rule contracts and typed adapter against P08 definitions. Produce a machine-readable mapping keyed by immutable source occurrence/rule digest and P01 contract ID/version; compare exact AST operators, ordered arguments, quantity/unit/frame, entity/role arity, parameter binding, missingness, clock, time window and applicability semantics. A shared label is not equivalence. Classify each mapping as exact, compatible through an explicit adapter, or unmapped. P08 265 and P01 72 are different denominators; do not subtract or claim overlap before this comparison.

Reuse the P01 evaluator directly for exact matches. If the P01 engine is coupled to model/training code, extract its existing fixed operator implementation into a shared dependency used by both P01 and the BENCH bridge, preserving its tests and implementation version. Do not write a divergent threshold-only engine. For a P08-only rule, extend that same operator library with a narrowly defined supported operator only after the comparison identifies an actual gap. Unknown/unsupported mappings remain explicitly unevaluated. P01 forecasts must remain predicted facts with their horizon and provenance, separate from measured BENCH facts and fixed-rule truth. Native Atlas parity and 1,686-item recovery are not implied. Do not wait for the catalog to implement the evidence/renderer adapters, and never ship constant booleans, fixture expectations or command receipts as predicate truth.

### D. Event/script/policy → native command dispatch

Proposed `aero_bench/integration/aeroagent_participant.py`:

`on_observation(authorized_projection, predicate_results) -> Decision/ActionIntent`

This is a BENCH participant using the existing `AgentContext` and `GatewayClient`, preserving the AeroAgentSim task/workflow/policy organization. Entity, agent and policy are separate records; the agent does not gain access to all SceneState/verifier truth simply because the graph panel can display it.

An explicit event policy converts eligible predicate transitions into `event_occurrence` records. A separately bound script/workflow consumes an occurrence and proposes an action. Route an authorized native tool through `GatewayClient.command`; complete turns through `GatewayClient.complete_turn`. Persist proposal → command_attempt → each `received/accepted/applied/completed/failed` receipt → effect evaluation. Reuse the native grants and PX4 physical-completion evidence. Never let a graph click or true predicate directly dispatch a flight command.

P08 has no generic `script` or `action` node kind today. Model scripts with the existing `behavior_definition`/`command_definition` composition and their execution records, or add a versioned explicit extension. Do not flatten behavior, command definition, attempt and receipt into an ambiguous “action” node.

### E. One shared view → original GLB renderer and graph

Proposed `frontend/src/integration-view-store.ts` and `p08-runtime-panel.ts`:

Atomic key: attachment, run_id, run epoch, scenario/manifest revision, BENCH tick, SceneState digest, semantic evaluation revision and binding epoch. Carry the three source stream cursors separately. BENCH `RunSession`/`ReplayState` remains the source of cursor movement; the graph receives the same current snapshot.

Keep `app.ts:currentMapScene()/renderMap()` → `PublicTraceMap.render(scene, view)`, `native-city-presentation.ts`/existing OSM loaders, original GLTF assets, camera, raycast and layer behavior. Mount the graph as a sibling panel. Graph entity selection maps to existing `SelectionState.select({kind:'entity',id:benchId})`; city selection maps back via the versioned binding manifest. Pure definitions without a configured mapping show their definition and do not move to an arbitrary aircraft.

Reject late results for a different complete key. Rewind reads saved facts/evaluations or recomputes from native history in an isolated session. It must not emit occurrences twice or send any command.

### F. Evidence/result → business UI

Proposed `aero_bench/integration/aeroagent_run_view.py`: expose a BENCH-backed read-only business run view or route the existing business UI service directly to it. The native BENCH control API remains lifecycle authority. Keep native sealed artifact IDs and receipts linked; no duplicate mutable “current run” in AeroAgentSim.

## 4. First implementable slice

### Slice 1A: native flight and graph evidence in the original city

Use one existing server-registered inspection run and one permitted aircraft/agent/work order. Keep its original GLB city and PX4/Gazebo provider. Run a native supported flight operation through the existing participant/Gateway. At each closed tick, project pose and the real command receipt lifecycle into new P08 runtime records. The original aircraft, telemetry panel, business task row and graph detail must all show the same run/tick/entity and linked evidence.

This slice proves the actual integration seams without claiming an unavailable evaluator. A receipt's completed phase may appear as a receipt. It does not become an arbitrary graph predicate or task-success result.

### Slice 1B: one complete state → rule → predicate → script/event chain

Concrete source case: P08 `city.w17.s03`, “Acquire three independently positioned views.” Selected source nodes and exact pointers are supplied in `p08_case_binding_evidence.json`; it is a scoped source excerpt, not a complete or executable graph.

- State specification: `n:ec0befc075c8973f90f7fec2`, original `city.w17.s03.field.view_count`.
- Rule: `n:802fea9aec43233df2925a48`, original `city.w17.s03.rule`; exact AST `gte(state(view_count), param(city.w17.s03.parameter))`.
- Predicate: `n:582e5e72a4c03b8b073aff31`, original `city.w17.s03.predicate.requirement`.
- Parameter: `n:f12a2ef5e85e2f7193682626`, source value 3 views.
- Capture artifact entity: `n:0300bf119645bada34355930`; aircraft entity: `n:ec447c07e7916619755705c3`; mission agent: `n:5d8df68898f5ae37c65ed1aa`.
- Behavior: `n:af843e7962d348850f0b9466`; event definition: `n:581bc7643f3809b9c25989c2`; feedback policy: `n:cc56d96e6f555a7d8a694b55`.

Create a new configured binding for the actual server target/work order/capture artifact; keep the authored B17-P2 case unchanged. Configure three distinct viewpoints explicitly. Each counted view must reference a real validated native RGB observation, the matching target/camera and observed capture pose. Three duplicate frames at the same viewpoint do not satisfy independent positions. A versioned capture aggregator emits the count and its membership evidence; missing/incomplete evidence yields unknown, not zero.

At a declared evaluation boundary, evaluate the source `gte` AST with threshold 3 through the selected real evaluator. Store the selected fact and result. The workflow can then advance to its next declared capture/report step, using the existing native command/observation/business path. False/unknown at an ordinary partial-capture tick need not trigger a premature failure: configure the assessment boundary explicitly. The source interruption event is “first failed/unknown criterion in this task attempt, once per episode,” with fresh evidence plus reassessment clearing it. Preserve that policy if it is used. A successful-completion event is a new explicit definition, not a renamed interruption event.

Use existing BENCH `InspectionRuntimeHook` for observation readiness and network delivery. Its current contract permits one required observation per work order; if three observations are grouped into a new capture-set artifact, extend the task registration and aggregation explicitly rather than relaxing the native identity checks or counting callback invocations. This makes Slice 1B a small task-package addition, not a frontend-only change.

This predicate only establishes the authored view-count criterion. Clearance, permission, quality, defect diagnosis and real-world flight authority remain separate questions. Do not turn this limited result into full workflow acceptance.

## 5. Implementation order and stopping evidence

0. Compare P08’s actual 265 predicate/258 rule definitions with P01’s reported 72 executable contracts/91 fields; pin the existing evaluator and select the first compatible rule. This is a targeted mapping task, not a broad new validation gate.
1. Add a versioned binding manifest and sidecar runtime graph projection to one existing task package. Compose the existing native hook. No new provider, physics engine or deployment framework is needed.
2. Mount a bounded P08 neighborhood in the original viewer and connect exact selection/tick identity. Add business task/receipt detail from the same snapshot.
3. Implement one supported binding to the reused P01 evaluator and one participant response. Keep other P08 rules browsable and explicitly unevaluated. Do not add a repository-wide validation campaign before this works.

Focused evidence is sufficient: one native provider run; source/destination command and receipt IDs; two genuinely different SceneState ticks that move the existing mesh; a graph fact linked to those exact native samples; one calculated predicate with its facts/AST/parameters; one causally linked workflow response; and replay of that same sealed run without dispatch. Retain false/unknown and missing-data examples for the affected binding only. Do not call these tests executed until the server task actually runs them.

## 6. Expansion to all scenario types

The inspection slice is the first increment, not the final scope. Preserve all P08 cases and all four domain catalogs (agriculture, delivery, city, network), with source coverage of 35 workflows/226 steps and the separately counted 20 machine examples. Add supported execution routes to a versioned coverage matrix: source case/step → state projector → fixed-rule binding → participant/script → native provider/tools → evidence output. Expand by reusable primitive (flight, sensing, network delivery, business transfer, resource reservation, then specialized domain operations). A scene type without a corresponding native provider/tool remains visible as authored but is not called executed. This matrix replaces disconnected badge statuses with concrete adapter and evidence links; it must not merge distinct actors, policies or resources simply to fill the matrix.

## 7. Explicitly unfinished

This review has not executed PX4/Gazebo, started or mutated the server, mounted the renderer, recovered the 1,686 catalog, run a native Atlas evaluator or certified all 35 business activities. It identified actual reuse points and a bounded executable path. Source existence, authored graph coverage and mock tests are not substitutes for the native evidence above.
