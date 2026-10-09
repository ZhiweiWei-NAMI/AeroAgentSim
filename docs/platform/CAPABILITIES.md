# Platform capability checklist

P8 cutover snapshot, 2026-10-08. **AeroAgentSim is the final platform; AeroBench
remains a read-only migration source until pending capabilities and build/asset
inputs are resolved. This checklist does not authorize its deletion.**

Coverage sources: `/tmp/aas-survey/jobs/glm-bench/MIGRATION.md` §§1–4 and
cross-cutting §§5–8; `/tmp/aas-survey/jobs/glm-aas/INVENTORY.md` §§1–6; read-only
AeroBench tree at `/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim/aero-bench`.
The survey's original classification is advisory: scene compilation, gateways
and deployment belong in the platform, not the general-purpose kernel.
Two concurrent DSH GLM audits used `workbuddy/glm-5.3-flash`, configured output
budget 131072, no effort setting. Their actual reports were reviewed; stale
counts, newly appearing P7b files, unjustified drops and registry/scene compiler
conflations were corrected before integration. Their static reads are not tests.

**Statuses:** `ported` = retained/extracted capability with implementation and
verification evidence; `replaced-by` = a different implemented mechanism, with
scope/limits named; `intentionally dropped` = excluded from the new platform for
the stated reason (historical source is retained); `pending Pn` = unresolved work
assigned to the milestone in [PLAN.md](PLAN.md), including its follow-up gates.
A partial replacement never implies every historical behavior was migrated.

Path prefixes below are repository-relative: **S** = `src/aeroagentsim`,
**F** = `frontend/src`, **T** = `tests`, **C** = `containers`. All named
implementation/test/artifact paths were checked for existence. A legacy class
is still in its original module under S; its presence is not a port to kernel
semantics. P5-F and P7b are concurrent: their unverified additions stay pending.

## Verification evidence

| Code | Evidence and practical limit |
| --- | --- |
| U | Fresh P8 non-Docker/non-model run: 328 passed, 5 deselected across T/platform, T/integrations/aerograph, T/adapters, T/agents, T/packs and public compatibility tests. No container/LLM run was performed in P8. |
| P1 | Fresh clean install: 22 s slice in 8.015 s, RTF 2.745, RSS 114,084 KiB; engine-free replay cut 1645, complete. Historical scale/cadence evidence: T/platform/measurements.json and T/platform/p1-f-gates.json. |
| E1 | Recorded real container integration/replay in T/adapters/measurements.json. Per-backend host contract tests freshly passed in U. Numbers can differ from earlier prose in adapters.md; JSON is the named measurement source used here. |
| P2 | Recorded C/px4-gazebo/measurements.json and C/px4-gazebo/measurements-p2b.json; backend tests in C/px4-gazebo/tests. Real flights establish selected actions, not every P2 cancellation/contact/scaling gate. |
| P3 | Recorded C/sumo/validation/report-100ms.json and C/sumo/validation/report-1000ms.json; backend tests in C/sumo/tests. Native time, vehicle/person/TLS and route outcomes are recorded. |
| P4 | Recorded C/ns3/verification/metrics.json, manifest.json, startup.json and probe-results.json; C/ns3/tests/test_contract.py. Recovered source and two same-seed native transcripts exist; P8 did not rebuild. |
| P5 | Fresh pack/model/scenario tests in U. Recorded T/packs/logistics-kpis.json and T/packs/inspection-kpis.json; these authored model outputs are not real sensor imagery. |
| P6 | Fresh decision/provider tests in U. Recorded T/agents/llm-metrics.json: 10 orders complete, 7 model calls, 17 valid tools, 0 invalid, one wall timeout and HTTP 429; engine-free replay makes 0 model calls. Earlier agents.md prose has different run values; do not combine them into one measurement. |
| F1 | Recorded browser/frontend gates in T/platform/p1-f-gates.json and screenshots; source tests in F/feeds, F/viewport and F/scene. P8 checked files, not browser/GPU performance. |
| L | Fresh legacy-extra run and migration snippets; exact totals/failures are below. Historical v1 behavior is not kernel behavior. |

## AeroBench: packages (survey §1)

| Source capability | Status / new implementation | Test / verified result and remaining scope |
| --- | --- | --- |
| Config CLI, BundleReader, strict YAML/JSON, suite resolution | replaced-by S/scenario/loader.py and S/integrations/aerograph/compiler.py | T/platform/test_platform.py; T/integrations/aerograph/test_compiler.py, U/P1. Scenario/snapshot digests and duplicate-key rejection; no automatic old-suite bundle converter (pending P8). |
| Docker workload executor/planning/readiness | replaced-by S/adapters/container.py and S/adapters/runner.py | T/adapters/test_container.py and T/adapters/test_docker.py; U/E1. Small allowlisted image runner with ownership-checked cleanup, not arbitrary benchmark workload deployment. |
| Kubernetes executor and generalized distributed workload plans | pending P8 deployment follow-up | No equivalent implemented; local run workers/Docker remain supported. |
| Harness/bootstrap/barrier/control/lifecycle/scene history/internal commands | replaced-by independent aerokernel plus S/platform/simulation.py and S/services/worker.py | T/platform/test_platform.py and T/platform/test_kernel_compat.py; U/P1. Explicit partition timing/field authority replaces fixed motion→network→business barriers. |
| Evidence/ledger seal bindings and sealed-motion proof chains | intentionally dropped | Benchmark attestation is not simulation execution. Kernel WAL integrity and action receipts remain; no credential/seal service is required. |
| Provider registry, RPC, stages, contracts | replaced-by S/platform/plugins.py and S/adapters/lockstep_base.py; pyproject engine entry points | T/adapters/test_transport.py, test_engines.py, test_schemas.py; U/E1. Backend-specific native RPC is translated into kernel partitions, not a claim each service is an aerokernel.rpc endpoint. |
| PX4/Gazebo provider | ported S/adapters/px4_gazebo.py and C/px4-gazebo/service | T/adapters/test_engines.py, C/px4-gazebo/tests; U/E1/P2. Native source stamps, exact stops and observed flight outcomes. Cancellation and complete contact gates pending P2. |
| SUMO provider | ported S/adapters/sumo.py and C/sumo/service | T/adapters/test_engines.py, C/sumo/tests; U/E1/P3. Lifecycle/route/TLS evidence; native cancel and cross-engine contact coupling pending P3. |
| ns-3 provider | ported S/adapters/ns3.py and C/ns3/native/aero-ns3-provider.cc | T/adapters/test_engines.py, C/ns3/tests/test_contract.py; U/E1/P4. Real deliveries/drops; shared-medium single-radio best-effort profile. Broader topology/QoS pending P4. |
| Logistics-business provider; parcel/facility/fleet/arrival/energy/dwell task packages | replaced-by S/packs/logistics.py, arrivals.py, geometry.py and common.py | T/packs/test_logistics.py, test_models.py, test_scenarios.py; U/P5. Custody, capacity, physical dwell and independent acceptance; full benchmark package conversion pending P5. |
| Inspection-business provider; formal-v2 geometry/sensor/report contracts and verifier | replaced-by S/packs/inspection.py and metrics.py | T/packs/test_inspection.py and test_scenarios.py; U/P5. Typed geometric observations, coverage/analysis/report provenance. Genuine image capture/model analysis and benchmark-specific formal-v2 conversion pending P5. |
| world_scene reference provider | replaced-by authored scenarios, registry bindings and S/engines/kinematic.py | T/platform/test_motion.py; U/P1. No equivalent whole-city physical scene compiler is implied. |
| Urban-recovery demo task package | pending P5 optional scenario migration | No migrated urban-recovery scenario. Existing authored P1/pack examples are different models. |
| LLM participant bridge, model proxy, session/runtime, vendor CLI driver | replaced-by S/agents/decision.py, provider.py, observation.py and tools.py | T/agents/test_decision.py, test_provider.py, test_llm.py; U/P6. Typed observations/tools with bounded failures; generic HTTP endpoint replaces vendor-specific CLI. Interactive multi-driver sessions pending P6. |
| Gateway tool/query dispatch and principal permissions | replaced-by S/agents/tools.py and observation.py | T/agents/test_decision.py; U/P6. Scenario grants bound authorized projections/commands; distributed gateway/auth service pending P6/P8. |
| Authoring API/workspace/draft compiler/city registrations/source import/traffic preview/publication | pending P7 | S/authoring/api.py, catalog.py, templates.py and workspace.py are concurrent P7b files. Presence alone is not a verified City Studio port; full import/edit/compile/run/preview gates remain pending. Publication seals intentionally dropped. |
| World/scene compiler, frames/alignment, road widths/building merge, workload compilation | pending P7 | F/scene/source-geometry.ts and city-layer.ts render selected source geometry; S/integrations/aerograph/compiler.py compiles ontology, not physical worlds. Shared authoritative Gazebo/SUMO/viewer geometry compilation remains pending. |
| Trace/projector/public replay vocabulary and manifests | replaced-by S/services/projector.py, storage.py and F/contracts/viewer-feed.ts | T/platform/test_projection_review.py and test_scenario_replay_review.py; U/P1/F1. Versioned temporal feed/WAL replaces sealed replay format; no byte-compatible old PublicReplayManifest conversion. |
| Verifier goal/metric reports and output validation | replaced-by S/packs/metrics.py for supported packs | T/packs/test_models.py and test_scenarios.py; U/P5. Metrics derive from committed replay. General plug-in evaluation/legacy verifier report translation pending P5/P8; seal binding validation intentionally dropped. |
| Artifact SealManifest / credentials / observation grants tied to participants | intentionally dropped | Remove mandatory benchmark attestation/identity ceremony; ordinary artifact digests and authored agent scopes remain. |
| Run-control catalog/client/server/CLI, sealed replay manager | replaced-by S/services/app.py, cli.py and worker.py | T/platform/test_service.py, test_service_review.py; U/P1. /v1 run/create/list/header/commits/SSE/pause/resume/stop; no legacy control-token bootstrap. |
| Runner resolve→execute→seal→verify orchestration | replaced-by S/services/cli.py and S/adapters/runner.py for run/replay/metrics | U/P1/E1/P5. Seal ceremony intentionally dropped; reusable evaluation suite orchestration pending P8 follow-up. |

## AeroBench: container capabilities (survey §2)

| Capability | Status / path | Evidence / pending gate |
| --- | --- | --- |
| Harness runtime image | replaced-by S/services/worker.py in-process kernel worker | T/platform/test_service_review.py, U/P1; a mandatory harness image is unnecessary. |
| PX4/Gazebo native flight image/service/selfcheck | ported C/px4-gazebo/Dockerfile, service, smoke.py | P2/E1. Survey's blocked old formal provider path is superseded by recorded new service flights. Clean upstream base rebuild still pending P8. |
| SUMO native traffic image; restriction-verifier sidecar | ported C/sumo/Dockerfile, service, smoke.py | P3/E1. Restriction commands use actual readback. Mandatory sealing sidecar intentionally dropped; independent native base rebuild pending P8. |
| ns-3 image previously missing from AeroBench tree | ported C/ns3/Dockerfile, native, recovered and service | P4/E1. Source recovery/provenance and reproducible native run artifacts exist; broader radio profiles pending P4. |
| Logistics-business/arrivals-verifier/clock-agent images | replaced-by S/packs/logistics.py and arrivals.py; kernel timers | T/packs/test_logistics.py, U/P5. Arrival/workflow clocks do not require an extra service. Generic evaluation plugins pending P8. |
| Inspection-verifier image | replaced-by S/packs/metrics.py for current inspection KPIs | T/packs/test_inspection.py, U/P5; full optional formal-v2 verifier migration pending P5. |
| native-parcel business/flight/participant/SUMO/verifier wrapper images | intentionally dropped as benchmark composition | Compose explicit scenarios and engines instead. Current backend Dockerfiles still consume local AeroBench base images; their independent reproducible builds are pending P8 and block full retirement. |

## AeroBench: frontend (survey §3)

| Capability | Status / path | Test / result / remaining scope |
| --- | --- | --- |
| Live control/app/run-store/reconnect/auth/terminal/i18n console | replaced-by F/pages/RunsPage.tsx and F/feeds/http.ts | F/feeds/http.test.ts and review.test.ts; F1/U backend gates. Benchmark token files intentionally dropped; full auth/terminal UI pending P8. |
| Three.js 3-D map/live/replay; camera/selection/layers/telemetry | replaced-by F/viewport/viewport.ts, samples.ts, EntityInspector.tsx and F/feeds/temporal-store.ts | F/viewport/feed-boundaries.test.ts, F/feeds/temporal-store.test.ts; F1. Display interpolation and exact inspector values; full flight HUD/sensor camera UI pending P7. |
| City Studio terrain/roads/buildings/facades/weather/light/vegetation/water/region/compile panels | pending P7 | Concurrent F/studio files and S/authoring/api.py are documented in studio.md; they are not the complete legacy City Studio. Import/edit/preview/compile and non-UAV descriptor gates need P7b results. |
| City rendering/shadows/reflections/lighting/terrain/building batching | replaced-by F/scene/look-dev.ts, presentation.ts, source-geometry.ts and city-layer.ts | F/scene/source-geometry.test.ts; recorded frontend/test-results/p7a-measurements.json. Software-renderer results are below 60 Hz; hardware GPU/VRAM gate pending P7. |
| Native parcel / P02 branded scene, overlays, run-start identity | intentionally dropped as fixed benchmark presentation | Generic scene/entity bindings replace domain hardcoding; domain-specific reusable overlays remain optional P5/P7 work. |
| Operations monitor / orders workspace / business telemetry | pending P5/P7 | Current inspectors, agent console and pack metrics provide partial visibility; dedicated operations UI absent. |
| Browser OSM2World source/projection/mesh/pack runtime | replaced-by F/viewport/mesh-pack.ts and assets.ts for verified pack consumption | F/viewport/assets.test.ts; F1. tools/sync_assets.py remains a source-asset tool with AeroBench path input. Browser OSM authoring and independent attributed asset pipeline pending P7/P8. |
| Generated TS contracts, validators, strict JSON, asset resolver | replaced-by F/contracts/viewer-feed.ts and explicit parser checks for new feed | F/feeds/review.test.ts and F/viewport/assets.test.ts; F1. Full Python↔TS schema generation (46 old schemas) pending P8; handwritten contracts are not equivalent codegen. |
| Agent console / interaction timeline | replaced-by F/pages/AgentConsole.tsx | T/agents/agent-console.test.tsx and console-test.txt; P6/F1. Read-only typed decision/receipt history; interactive gateway terminal pending P6. |

## AeroBench: tools, schemas, docs, data (survey §§4–8)

| Capability | Status / implementation | Verification / milestone |
| --- | --- | --- |
| run_stack launcher/single-port proxy | replaced-by S/services/cli.py serve and app.py static hosting | T/platform/test_service.py; U/P1. No separate 5416 proxy stack is needed. |
| generate_contracts.py / generated JSON+TS schemas | pending P8 | Current F/contracts/viewer-feed.ts is manually maintained; single-source codegen remains a gap. |
| General agent/inspection image builder | replaced-by per-backend C/px4-gazebo/Dockerfile, C/sumo/Dockerfile and C/ns3/Dockerfile | P2/P3/P4 recorded builds; unified reproducible non-Bench base-image builder pending P8. |
| Native-parcel digest-pinning image builder | intentionally dropped | Fixed benchmark assembly is replaced by scenario configuration. |
| finalize_verified_run; sealed capture/status/stream evidence; publication capture tools | intentionally dropped | Benchmark proof capture is not platform execution; genuine replay/browser screenshots remain available. |
| Pose calibration / native parcel scene preparation/registration tools | pending P5/P7 optional tools | Their scenario-specific workflows have no general conversion yet; rendering frame support is not a calibrated sensor/scene pipeline. |
| p02 evidence manifests/calibration docs | intentionally dropped as release docs | New README/INSTALL/PLATFORM guides describe kernel contracts; historical research records remain in read-only source. |
| Existing validation payloads (5.5 GB), demos (382 MB), credentials | intentionally dropped from code distribution | Do not silently copy datasets or credential files. Users supply explicit attributed assets; binary archive migration is outside P8. |
| Migration dependency order and regression suites | replaced-by PLAN milestones and new test suites | U covers scheduler/loader/provider/pack/agent replacements. Prior sealing-specific tests intentionally dropped; P7 authoring/physical-world characterization still pending. |
| Mirror/local registry image pins and clean asset availability | pending P8 retirement gate | No Python runtime imports aero_bench, but backend builds still use local AeroBench image bases and sync_assets defaults to its asset tree. A clean Python install does not prove independent native/asset builds. |

## Legacy AeroAgentSim: public core and entities (inventory §§1–2)

| Legacy capability | Status / kernel equivalent | Test / result and limit |
| --- | --- | --- |
| Environment / AirFogSimEnv / AeroAgentSimEnv / airfogsim aliases | replaced-by public Simulation/RunSession in S/platform/simulation.py; deprecated legacy aliases retained | T/test_core/test_compatibility.py, U/L. Legacy only with dependency set; actionable ImportError otherwise. |
| Environment.create_agent/register_agent/create_workflow, implicit 11 managers | replaced-by S/scenario/loader.py entity/engine declarations and explicit bindings | T/platform/test_platform.py, U/P1. No full imperative create_* compatibility converter (pending P8). |
| store_data/get_data / env.data | intentionally dropped from kernel API | Replace meaningful state with typed facts/records and inputs; unrestricted mutable side storage has no authority/replay semantics. |
| add_data_provider/get_data_provider | replaced-by explicitly configured engines/adapters | S/platform/plugins.py; T/adapters/test_engines.py, U/E1. Specific weather/signal sources remain pending below. |
| visual_interval / visual_update physical integration | intentionally dropped | Kernel grants own state evolution; T/platform/test_platform.py cadence regression, U/P1. |
| AgentMeta / StateTemplate registration/validators | replaced-by S/integrations/aerograph/compiler.py and scenario registry extensions | T/integrations/aerograph/test_compiler.py, U. Schema/units/review/provenance; no Python subclass proxy generation. |
| Agent identity, get_state/update_state, attachment/live task queue/execute_task/events | replaced-by entity facts, authorized engines and typed commands/receipts | S/engines/kinematic.py, S/agents/decision.py; T/platform/test_motion.py, T/agents/test_decision.py, U. Mutable state writes/custom SimPy live generators require rewrite. |
| Component metrics/monitored states/can_execute/namespaced callbacks | replaced-by capability and writer bindings plus partition selectors | S/platform/plugins.py; T/platform/test_ownership_review.py, U. Metrics require an implemented producer. |
| Task.execute/progress/estimate/preempt | replaced-by typed command lifecycle and declared cancel support | S/engines/kinematic.py; T/platform/test_motion.py, U. Automatic generic preemption/remaining-time estimation pending P5; native cancellation not supported. |
| Workflow / WorkflowStatusMachine / add_transition / timeout | replaced-by S/engines/workflow.py and pack state machines | T/platform/test_workflow_review.py, U. Typed receipt/event/timer transitions. UML/Mermaid export pending P7. |
| Trigger / EventTrigger / StateTrigger / TimeTrigger / CompositeTrigger + max-count | replaced-by workflow timer/event transitions and S/engines/threshold.py | T/platform/test_native_threshold.py, test_workflow_review.py; U. General composition/count semantics pending P5; cron fallback intentionally dropped. |
| EventRegistry subscriptions/wildcards/SimPy event objects | replaced-by registered typed kernel messages and topics | S/engines/workflow.py; T/platform/test_platform.py, U. Arbitrary callback/wildcard bridge pending P8. |
| Resource / ResourceManager pooled allocation | replaced-by S/packs/logistics.py facilities/pad/locker reservations for supported pack | T/packs/test_logistics.py, U/P5. Generic resource scheduler pending P5. |
| LLMClient / LLMScheduler | replaced-by S/agents/decision.py and provider.py | T/agents/test_provider.py, U/P6; real outcomes/failure evidence P6. |
| Location / Speed Pint value wrappers | replaced-by registry schemas/metadata with explicit units/frames | S/integrations/aerograph/normalize.py; T/integrations/aerograph/test_compiler.py, U. No general dimensional algebra in kernel. |
| TerminalAgent | pending P5 compute/file/communication entity pack | General entity types are supported; specific virtual-file terminal behavior has not migrated. Legacy S/agent/terminal.py retained. |
| DroneAgent | replaced-by authored oo:UAV entity plus kinematic/native writer bindings | scenarios/p1-slice.yaml; T/platform/test_motion.py, U/P1/E1. Old battery heuristics are not preserved as calibrated physics. |
| DeliveryAgent / DeliveryDroneAgent | replaced-by carrier entities and S/packs/logistics.py | T/packs/test_logistics.py, U/P5. Carrier types are configurable, not kernel subclasses. |
| DeliveryStation / InspectionStation workflow spawner | replaced-by authored facilities, arrival schedule and pack mission definitions | S/packs/arrivals.py, logistics.py, inspection.py; T/packs/test_models.py, U/P5. Generic station spawner migration pending P5. |
| SensingAgent nearby-object bookkeeping | pending P5 sensor pack | Geometric inspection observations exist; object detection/tracking model has not migrated. S/agent/sensing_agent.py remains legacy-only. |

## Legacy components, tasks and workflows

| Old capability (all classes/factories retained in legacy modules) | Status / new implementation | Verification / pending behavior |
| --- | --- | --- |
| MoveToComponent / MoveToTask | replaced-by S/engines/kinematic.py and native flight commands | T/platform/test_motion.py, U/P1/E1. Calculated/native arrival replaces visual-update progress. |
| ChargingComponent / ChargingTask / RequestChargingStationTask / ChargingWorkflow / create_charging_workflow | pending P5 charging pack | Energy owner and facility reservations exist; charging allocation/rate/lifecycle has no equivalent. S/component/charging.py, S/task/charging.py and S/workflow/charging.py retained. |
| CommunicationComponent / FileTransferTask | pending P5 data-transfer pack | S/adapters/ns3.py delivers modeled packet outcomes (U/P4/E1); it is not virtual-file/content transfer. S/component/communication.py and S/task/transfer.py retained. |
| CPUComponent / ComputationComponent / FileComputeTask | pending P5 compute pack | S/component/computation.py and S/task/compute.py retained; no kernel compute/workload engine yet. |
| EMSensingComponent | pending P5 EM sensor model | Spectrum scenario shows typed observations/constraints, not a migrated signal detection/loss producer. S/component/em_sensor.py retained. |
| ImageSensingComponent / FileCollectTask | pending P5 genuine sensor/data capture | S/packs/inspection.py computes geometry; no image renderer is implied. S/component/img_sensor.py and S/task/collect.py retained. |
| ObjectSensorComponent | pending P5 detection/tracking | No migrated object-detected/lost stochastic sensor; S/component/object_sensor.py retained. |
| LogisticsComponent / PickupTask / HandoverTask / LogisticsWorkflow / create_logistics_workflow | replaced-by S/packs/logistics.py custody and physical handoff | T/packs/test_logistics.py, U/P5. State/relations and dwell are modeled explicitly. |
| InspectionWorkflow / create_inspection_workflow | replaced-by S/packs/inspection.py | T/packs/test_inspection.py, U/P5. Geometric capture/analysis/report chain; old collect/compute/image pipeline pending P5. |
| ImageProcessingWorkflow / create_image_processing_workflow | pending P5 actual processing engine | S/workflow/image_processing.py retained; the new inspection analysis does not replace arbitrary file/image computation. |
| OrderExecutionWorkflow / create_order_execution_workflow | replaced-by S/packs/logistics.py plus independent acceptance workflow | T/packs/test_scenarios.py, U/P5. Generic old template conversion pending P8. |
| ContractWorkflow / create_contract_workflow | pending P5 optional economic pack | S/workflow/contract.py retained; acceptance alone does not implement payment/rewards/penalties. |

## Legacy managers, data providers and statistics

| Capability | Status / implementation | Test / scope |
| --- | --- | --- |
| AgentManager registry/compatibility lookup | replaced-by registry/manifest identities and S/platform/plugins.py | T/platform/test_ownership_review.py, U. General semantic compatibility recommendation pending P7. |
| ComponentManager class registry/compat | replaced-by engine catalog + capability bindings | S/platform/plugins.py; U. Legacy class attachment conversion pending P8. |
| TaskManager catalog/recommendation/factory | replaced-by message descriptors/tool catalog for execution | S/agents/tools.py; T/agents/test_decision.py, U. Task recommendation policy pending P5/P7. |
| WorkflowManager registration/start-trigger/lifecycle fan-out | replaced-by S/engines/workflow.py and typed schemas | T/platform/test_workflow_review.py, U. No generic legacy factory bridge. |
| TriggerManager CRUD/activation | replaced-by authored workflow timers and predicate bindings | S/engines/threshold.py, U. Interactive predicate CRUD pending P7. |
| ContractManager balances/rewards/penalties | pending P5 economic engine | S/manager/contract.py retained. No silently defaulted balances in new core. |
| PayloadManager weight/volume/possession | replaced-by parcels, authored capacity and custody relations in S/packs/logistics.py | T/packs/test_logistics.py, U/P5. General payload physics/packing pending P5. |
| FileManager distribution/locations | pending P5 file/content engine | S/manager/file.py retained; opaque ns-3 payload_ref is not content transport. |
| AirspaceManager / OctreeNode spatial queries/proximity events | pending P5/P7 generic derived spatial index | S/manager/airspace.py retained. Pack route-box restrictions are narrower; they do not replace proximity/contact queries. |
| FrequencyManager block allocation/SINR/path loss | replaced-by native ns-3 propagation/delivery for supported radio profile | C/ns3/native/aero-ns3-provider.cc; P4/E1/U adapter tests. Spectrum block allocation and general radio-resource policies pending P4/P5. |
| LandingManager landing/charging/data/base sites | replaced-by S/packs/logistics.py pad/locker reservations for current logistics | T/packs/test_logistics.py, U/P5. Generic landing/charging/data-site policies pending P5. |
| DataProvider / DataIntegration ABC | replaced-by declared input engines with field authority/time provenance | S/platform/plugins.py and S/adapters/lockstep_base.py; U/E1. Legacy callback ABI requires migration. |
| WeatherDataProvider / WeatherIntegration / OpenWeatherMap adapter | pending P5 environment inputs | S/dataprovider/weather.py, weather_integration.py and api_adapters/openweathermap_adapter.py retained. No new weather measurement engine or x-force placeholder claim. |
| MockWeatherProvider used as real-data substitute | intentionally dropped from new runtime | Authored synthetic scenarios must label their inputs; failed weather sources cannot become mock success. Old module remains historical. |
| TrafficDataProvider SUMO/CSV | replaced-by S/adapters/sumo.py for native SUMO | T/adapters/test_engines.py, U/E1/P3. Generic CSV import engine pending P5/P7. |
| SignalDataProvider / ExternalSignalSourceIntegration | pending P5 source-adapter/sensing | S/dataprovider/signal.py and signal_integration.py retained. ns-3 radio simulation is not external measured signal input. |
| osm_to_sumo conversion | pending P7 full attributed map/network pipeline | S/dataprovider/osm_to_sumo.py retained. Concurrent authoring additions need their own netconvert/source/frame gates. |
| AgentStateCollector / EventCollector / WorkflowStateCollector / StatsCollector | replaced-by kernel WAL plus S/services/projector.py and storage.py | T/platform/test_projection_review.py, U/P1. Fixes typed lifecycle visibility by design; no old collector import needed. |
| StatsAnalyzer / StatsVisualizer | replaced-by S/packs/metrics.py and exact feed inspector for supported metrics | T/packs/test_models.py, U/P5. General plotting/dashboard analytics pending P5/P7. P8 adds missing matplotlib to the legacy extra so StatsVisualizer imports under that extra. |
| ResourceRequestEvent / ResourceReleaseEvent / unused db Store classes | intentionally dropped from new runtime | Dead legacy modules lack a working Event base or users; typed commands/relations and run storage replace their purpose. Source is not deleted in P8. |

## Legacy backend, storage, CLI and UI (inventory §§3–5)

| Legacy capability | Status / new path or milestone | Verification / limit |
| --- | --- | --- |
| Live source ingress for `real_time` engines (review B.9/F P0) | replaced-by S/scenario/loader.py per-engine ingress bindings, S/platform/simulation.py and `/v1/runs/{id}/ingress`, `/watermark` | T/platform/test_realtime.py; [realtime.md](realtime.md). Journal-derived reject/delay receipts, explicit closed-prefix sealing, exact engine-free replay. One shared run watermark; independent per-engine watermarks remain a kernel gap. Wall pacing is separate. |
| SimulationManager / PausableEnvironment / RealSimulationIntegration | replaced-by S/platform/simulation.py and S/services/worker.py | T/platform/test_service_review.py, U/P1. Integer-time boundary pause/resume/stop/realtime pacing; no legacy generators. |
| UpdateService / log_event / workflow_state_diff / spatial_snapshot | replaced-by S/services/projector.py and /v1 committed SSE feed | T/platform/test_projection_review.py, U/F1. Structured facts/relations/events/receipts, not mutable queued display snapshots. |
| RunRepository / manifest/config/log/workflow/spatial/trajectory directories and latest pointers | replaced-by S/services/storage.py WAL/index/pinned snapshots | T/platform/test_service.py, U/P1. Old logs are not kernel replay. Retained legacy RunRepository append/read race fixed in P8; L regressions pass. |
| ConfigRepository / WorkspaceService / ConfigCompiler draft validation/preflight/graph | replaced-by S/scenario/loader.py for executable scenario validation | T/platform/test_platform.py, U/P1. Draft CRUD/preflight/graph authoring API pending P7b. |
| RegistryService / ProxyCompiler / CatalogService custom JSON→Python proxies | replaced-by S/integrations/aerograph/compiler.py and explicit registry extensions | T/integrations/aerograph/test_compiler.py, U. Descriptor catalog/editors pending P7b; no runtime Python code generation. |
| MapService / DashboardService / TimelineService | replaced-by F/pages/RunsPage.tsx, F/viewport/EntityInspector.tsx and MissionTimeline.tsx for recorded state | F1. Full analytics dashboard pending P7. |
| GET / and /api/health, POST /api/runtime/reset | replaced-by new run list/control service; old routes legacy-only | S/services/app.py; T/platform/test_service.py, U. No old global-reset/health contract alias; a dedicated platform health contract pending P8. |
| WS /ws sim_status/log_event/workflow_state_diff/spatial_snapshot output-only | replaced-by GET /v1/runs/{id}/stream SSE with recorded cuts | S/services/app.py; T/platform/test_service_review.py, U/F1. No bidirectional socket control. |
| /api/catalog agents/components/tasks/workflows/compatibility | pending P7b descriptor catalog | Old routes remain in S/visualization/routes/catalog.py; new authoring integration not verified here. |
| /api/registry kind/id CRUD + validate | pending P7b descriptor authoring | Old S/visualization/routes/registry.py remains; raw v1 definitions need conversion. |
| /api/configs id CRUD + graph/validate/preflight + bootstrap default | pending P7b draft API | S/visualization/routes/configs.py retained; scenario validation works now. No implicit default city/airspace in kernel. |
| /api/runs create/list/status/logs/trajectories/spatial/pause/resume | replaced-by /v1/runs create/list/header/commits/stream/pause/resume/stop | S/services/app.py; T/platform/test_service.py and test_service_review.py, U. Exact field/event history replaces trajectory-specific REST responses. |
| Old run reset/delete and completed/active deletion guards | pending P8 run-catalog retention operations | New service has no delete/reset endpoints. Legacy guards remain tested in T/test_visualization/test_run_repository.py (L). |
| /api/simulation start/pause/etc. deprecated 410 | intentionally dropped as control API | Legacy 410 retained (L); new clients use /v1/runs. |
| /api/drones and /api/vehicles mock-backed live entities | intentionally dropped from new service | Committed typed feed is the actual source; retained legacy routes are not new runtime evidence. |
| /api/templates workflows/agents | pending P7b typed templates | S/visualization/routes/templates.py retained; concurrent S/authoring/templates.py is not a verified old API alias. |
| /api/traffic generate_sumo_network / sumo_configs / csv_files | pending P7 scene/network authoring | S/visualization/routes/traffic.py retained; native SUMO execution itself is E1/P3. |
| Legacy metrics directory created without results | replaced-by S/packs/metrics.py replay-derived CLI output | T/packs/test_models.py, U/P5. No promise that the old directory is populated. |
| CLI docs/examples/classes and airfogsim command | pending P8 equivalent catalog/docs/conversion commands | S/cli/main.py and pyproject airfogsim script retained legacy-only. New S/services/cli.py exposes run/replay/metrics/serve; examples subsystem is absent (L failures). |
| main_for_visualization.py auto-installs requirements/npm and launches legacy app | replaced-by direct kernel serve forwarding | main_for_visualization.py --help verified; no automatic installs or legacy backend. |
| OverviewPage / ClassCatalogPage compatibility matrix/critical_path/inline registry | pending P7b descriptor workbench | F/pages/OverviewPage.js and ClassCatalogPage.js retained for legacy backend; they do not prove /v1 support. |
| WorkflowStudioPage / RelationGraph zoom-pan-drag / TemplateTableEditor / ReviewDrawer | pending P7b editor integration | F/pages/WorkflowStudioPage.js and F/components/workbench/RelationGraph.js retained; historical graph/form tests are F1 artifacts only. |
| RunConsolePage / Map2D simulation_plane and geo_osm | replaced-by F/pages/RunsPage.tsx and descriptor-bound Three.js viewer | F1; F/pages/RunConsolePage.js and F/components/workbench/Map2D.js remain old-backend clients. Exact/recorded ENU is supported; general CRS authoring pending P7. |
| TrajectoriesLogsPage replay/list/delete/offline gating | replaced-by F/pages/RunsPage.tsx temporal replay/inspect | F1; legacy F/pages/TrajectoriesLogsPage.js remains. Delete feature pending P8. |
| RegistryDefinitionEditor critical_path/custom proxy definitions | pending P7b typed authoring | F/components/workbench/RegistryDefinitionEditor.js retained legacy; Python proxy semantics are replaced. |
| CRA build, axios client, WorkbenchContext, mutation gates, i18n | replaced-by frontend/package.json Vite/TS plus F/feeds/http.ts for new runs | F1; English/zh-CN retained. New requests surface source errors; old backend pages have separate contracts. |
| GET mock fallback and display-only offline defaults | intentionally dropped for new runtime data | Explicit /viewer-demo content is labeled. Feed failures retain the recorded cut with an error rather than inventing successful observations. |
| Legacy API/client normalization tests, graph/map utilities, locale tests | ported as retained historical frontend tests with Vite migration | F/services/workbenchApi.test.js, F/components/workbench/RelationGraph.test.js and F/i18n/I18nProvider.test.js; recorded F1 gates. P8 did not rerun frontend tests. |
| Legacy core/workflow/workbench/examples pytest suites | ported as legacy-marked suites with optional-extra collection gate | T/test_core, T/test_workflow, T/test_visualization, T/test_examples; L. Missing examples remain explicit failures. |

## Historical defects and retirement limits (inventory §6)

The inventory's split state writers, wrong `source_id` subscription, unreachable
`status_changed` failure transitions, `workflow_created` subscription, fabricated
cron interval, missing airspace/frequency creation APIs, unwritten metrics,
missing examples and dead resource/db modules are not claimed fixed in v1.
New authority/schema/timer/record contracts replace those mechanisms where the
rows above name an implemented replacement; remaining legacy behavior stays
bounded to the optional runtime. The log-tail race is the one compatibility
runtime fix made in P8: readers share the append lock and still reject corrupt
completed records instead of skipping them.

Complete retirement also needs independently reproducible native image bases,
licensed source assets independent of the AeroBench checkout, P7b authoring gates,
optional evaluation/conversion tools and explicit decisions on pending legacy
charging/compute/file/weather/sensing/economic capabilities. Full arbitrary
AeroGraph predicate interpretation, cross-simulator contact authority, native
cancel and long-run bounded feed/history remain pending in their owning milestones.

## P8 verification

See the final command/result record below. P8 used source copies under
`/tmp/aas-p8/platform` and `/tmp/aas-p8/aerokernel` for editable installation so
setuptools never wrote metadata into read-only neighboring repositories. Both
fresh venvs derive from the mandated kernel Python 3.11.12 interpreter. The
platform tests used that interpreter with scratch-installed dependencies; the
legacy suite used the derived legacy venv so its subprocesses see the same extra.
No Docker workload, frontend build, AeroGraph build, git commit/branch/reset,
protected-tree modification or online LLM call was performed by P8 verification.

Clean installation (fresh environment; source-copy layout described above):

```text
$ cd /tmp/aas-p8/platform
$ /tmp/aas-p8/venv/bin/pip install -e ../aerokernel -e '.[server]'
Successfully built aerokernel aeroagentsim
Successfully installed aeroagentsim-1.1.1 aerokernel-0.1.0.dev0 ...
$ /tmp/aas-p8/venv/bin/pip check
No broken requirements found.
$ /tmp/aas-p8/venv/bin/aeroagentsim run scenarios/p1-slice.yaml --out /tmp/aas-p8/runs
{"run": "/tmp/aas-p8/runs/p1-slice-ff9157bd907e", "elapsed_wall_s": 8.015373623929918, "simulated_s": 22.0, "rtf": 2.7447254528870553, "peak_rss_kib": 114084}
$ /tmp/aas-p8/venv/bin/aeroagentsim replay /tmp/aas-p8/runs/p1-slice-ff9157bd907e
{"run": "/tmp/aas-p8/runs/p1-slice-ff9157bd907e", "cut": 1645, "ns": "22000000000", "incomplete": false}
$ /tmp/aas-p8/venv/bin/aeroagentsim serve --help
usage: aeroagentsim serve [-h] [--out OUT] [--scenario-root SCENARIO_ROOT]
                          [--host HOST] [--port PORT] [--frontend FRONTEND]
```

This is a minimal/server environment: no SimPy, NumPy, model SDK or legacy
runtime was installed. Installed native entry points loaded successfully.
Grepping all new runtime packages found zero `from/import aero_bench` or
`airfogsim` statements. Importing Simulation, CLI and server created no legacy
modules in `sys.modules`; importing Environment produced the documented error.
Current workspace source imports were checked separately with the same result.
The workspace contains stale generated `src/aeroagentsim.egg-info` metadata;
forcing `PYTHONPATH=src` can shadow installed entry-point metadata (observed
missing entry points in that source-only check). Clean installs excluded those
stale generated artifacts and resolved all three factories correctly. Cleaning
shared generated metadata is an orchestrator follow-up, not a hidden runtime
fallback or a P8 modification outside ownership.

Fresh test results:

```text
Platform, compiler, adapters, agents, packs, compatibility:
328 passed, 5 deselected in 352.47s
Markers excluded: docker, llm, legacy

Minimal environment, legacy directories plus public compatibility:
2 passed, 1 skipped in 0.28s
Legacy files excluded from collection because the extra is absent;
the skipped test explicitly requires legacy Environment identity.

Legacy extra + dev, all four historical suites and public compatibility:
19 failed, 48 passed in 23.70s
All 19 failures are in tests/test_examples/test_examples_integration.py.

Migration snippets: 6/6 Python blocks passed (3 before/after pairs).
StatsVisualizer import with legacy extra: PASS.
New/updated import-light shim, launcher and collection guards: Ruff check and
format check passed (8 files). Changed Python files parse; local doc links and
136 explicit implementation/test paths resolve.
```

The 19 historical failures are missing example-directory/script assertions,
including trigger/workflow diagrams, image-processing/contract/task-priority/
queue/duplicate examples, object/signal examples, inspection/logistics/benchmark
examples, weather, SUMO and the aggregate file-existence check. The suite reports
these failures even when credentials/native tools are unavailable because the
referenced script files themselves are absent. No fake examples or expected
successes were added. Core, workflow, visualization and compatibility tests pass.
The earlier live-log failure (partial JSON line → HTTP 500) was fixed using the
existing append lock; deterministic split-append and corrupt-record regressions
pass. The initial kernel-interpreter/PYTHONPATH-only legacy attempt also failed
its subprocess import because that test replaced PYTHONPATH; the final derived
legacy-venv run gives subprocesses the complete extra without such injection.

Raw install/test/import/replay logs and both reviewed GLM reports are retained
under `/tmp/aas-p8/`. The JSON output above is the fresh P8 measurement, rather
than a rewording of component-job results. CI YAML/static checks were performed
locally; hosted Actions were not run. Kernel repository/ref variables and the
optional matching AeroGraph checkout must be configured by the orchestrator.
