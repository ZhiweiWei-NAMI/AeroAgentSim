# FINAL-R: integrated platform review

**Verdict: the kernel/platform separation and the tested event pipeline work. The consolidation is incomplete; do not retire AeroBench on this evidence.** Generality is demonstrated beyond UAV logistics, but real-time ingress is disconnected at the platform boundary, engine authoring has several avoidable obstacles, and visual superiority over AeroBench has not been established with comparable scenes.

This is a review, not an implementation change. The only repository deliverable is this file. All authored models, installations, logs, runs, builds and screenshots are under `/tmp/aas-final/`. No Docker workload, source edit, AeroGraph build, Git command, commit, branch or reset was run. Python execution used `/mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python` (3.11.12), or the fresh venv derived from it for installation/CLI verification.

The user supplied platform HEAD `0b9b790`, branch `platform/aerokernel`, and kernel branch `main`. Execution used source copies to keep editable-install metadata and test output out of the protected repositories. SHA-256 manifests are `platform-source-hashes.json` and `aerokernel-source-hashes.json`. A subsequent comparison found changes in `src/aeroagentsim/adapters/px4_gazebo.py` and `tests/adapters/test_p5f_substeps.py`, and no kernel changes. Results describe the copied source, not those concurrent PX4 changes. Real-PX4 logistics remains **in progress**, not a newly established failure or success.

Path notation: **P/** = platform `src/aeroagentsim/`; **K/** = sibling `aerokernel/aerokernel/`; **F/** = platform `frontend/src/`. Line references refer to the reviewed copies; these are retained in scratch if a concurrent edit moves a line. Background: `/tmp/aas-survey/REPORT.md`, especially Round 2.

## A. Owner goals

| Goal | Verdict | Evidence and specific gap |
|---|---|---|
| Extensible general-purpose platform integrating AeroGraph and events across state engines | **Partially met** | Ten source-selected types spanning all seven browsing directories ran in two new models. External entry points and public kernel SDK worked without core edits. Compiler preserves ancestry, provenance and review admission; fields and relation endpoints are validated. The threshold engine supports seven comparison/presence operators, not arbitrary AeroGraph ASTs (`P/engines/threshold.py:56`). A `real_time` plugin cannot be constructed through ordinary `Simulation`: its required ingress policy cannot be supplied (`P/platform/simulation.py:54`, `K/coordinator.py:200`). Studio omits installed third-party engines (`P/authoring/catalog.py:83`). |
| Replace difficult agent/component/task/status physics maintenance with PX4/Gazebo, SUMO and ns-3 | **Partially met** | One field writer and committed facts replace competing physical writers; new packages have zero imports from the five legacy directories or `core`. Native host adapters and container services exist, including receipts, clock mappings and lagged mobility input. This review ran their non-Docker contract tests, not native physics. Wind is absent from the supplied point-mass model; no general weather-to-native-physics binding or cross-simulator contact authority was demonstrated. Current real-PX4 logistics integration is a pending gate. |
| Merge AeroBench frontend/adapters/run control/replay so AeroBench ceases to be a separate project | **Partially met** | CLI, run service, WAL replay, Three.js viewer and three native factories are inside AeroAgentSim. New runtime packages do not import `aero_bench`. Current Dockerfiles use public pinned bases rather than the old private AeroBench bases. Retirement still requires completing/deciding the capability checklist and packaging asset/map inputs independently. `tools/sync_assets.py:20` defaults to AeroBench assets; `P/authoring/api.py:19` defaults to OSM extracts in the neighboring old AeroAgentSim tree. |
| Three.js frontend clearly better than AeroBench | **Partially met** | Display interpolation, quaternion slerp, generic type bindings, exact stamped inspectors, floating origin, LOD and explicit demo separation improve the architecture. Fresh build/typecheck and 68 frontend tests passed; both new scenarios rendered without page/console errors. A nonspatial computation shows an empty 3D ground plus inspector, not a topology view. Entity message correlation failed to display relevant recorded messages in our models. No matched-city visual comparison or hardware-GPU frame budget was measured. A claim of clear visual superiority would exceed the evidence. |
| Independent kernel; AeroAgentSim is the final platform | **Met** | Kernel is a separate distribution with `dependencies=[]`, Python `>=3.10`, and 33 runtime Python modules. AST inspection found zero non-stdlib dependencies and zero platform/benchmark imports. `Simulation` delegates state/time to `Kernel`; it does not create a second physics store. PX4/SUMO/ns-3 factories are platform entry points, not kernel imports (`pyproject.toml:52`, `P/platform/plugins.py:87`). Publication/version pinning remains release work, distinct from architectural independence. |

The `number` contract is deliberately strict float, unlike JSON Schema's inclusive numeric type (`K/registry.py:375`). This review treats that as documented-contract/onboarding friction, not proof of corrupted scientific results. Admitted `proposal:em:EMPropagationChannel` remained a research candidate in the pinned registry; admission did not turn it into an approved production type.

## B. Two new generality scenarios

These are explicitly authored research models, not external measurements, default data, or substitutes for failed native runs. Every input below is declared. Missing inputs in the new computation engine raise a source-specific error; no missing/null/unknown input becomes zero or false.

The compiler selected ten actual AeroGraph types and four native fields, producing a snapshot with **26 types including ancestry**. Four fields were native: `mn.job.condition`, `mn.node.ready`, `oo:digital_twin.wind.horizontalVelocity`, and `he.aircraft.position_enu_m`. Remaining model fields/messages and the offload relation were explicit scenario extensions. Candidate admission used `Policy(admit_proposed=True)` and was recorded in `stress.snapshot.json`.

| Scenario | Source-selected types/directories | Actual calculation and outcome |
|---|---|---|
| `offload.yaml` | `oo:ComputeJob` (network/compute), `oo:ComputeNode` (physical), `proposal:em:EMPropagationChannel` (EM), `oo:Regulation` (constraints), `oo:Order` (business), `oo:ComputeResourceReport` (information): **six directories**, zero position facts | SINR changes −5→20 dB at 100 ms; explicit computation permission changes false→true at 300 ms. The job remains waiting until both constraints hold. Transfer plus compute delay is `8e6/1e8 + 6e8/2e9 = 0.38 s`; completion occurs at **680 ms**, with a typed placement edge `job → edge`. A separate business workflow accepts at **730 ms**. This permission is an authored computational policy; it does not infer legal authorization from a regulation's mere existence. |
| `wind.yaml` | `oo:WindField` (environment), `oo:UAV` (physical), `oo:Order` (business), `oo:MissionEnergyRequirement` (constraints), `oo:HardwareWeatherObservation` (information): **five directories** | Fixed-step point-mass motion covers 4 m. The environment workflow supplies horizontal wind `[−10,0] m/s` over 0.5–1.5 s. Authored load law `P_extra = 0.5 × |wind|² W` adds **50 J** over that interval. At 3 s, energy is **940 J** versus **990 J** for the explicitly authored calm counterfactual, from 1,000 J initial energy. Stored floats are `939.9999999999994` and `989.9999999999994`. Arrival and independent business acceptance occur; the fixed reserve descriptor and authored weather record are passive data, not invented native sensor outputs. |

Union: **all seven directories**, with nonspatial and passive entities. Type source anchors: `AeroGraph/entity-directory/data/concepts.json:9217` (job), `:9331` (node), `:9436` (report), `:15218` (weather observation), `:19176` (requirement), `:21775` (order), `:26300` (regulation), `:34120` (wind), `:35275` (EM channel). These navigation classifications are not inheritance.

An installed scratch distribution registers `final_offload`, `final_wind`, `final_static`, and the delayed-decision wrapper in `aeroagentsim.engines`. The calculation engine uses public `Partition`, `Dependency`, `ContextEngine`, `FactPolicy`, `EngineContext`, manifest and registry APIs. The wind extension subclasses the supplied `Kinematic` implementation because it has no composable energy hook. This is an implementation dependency, not a stable SDK extension point.

Authoring cost, counted from delivered scratch files:

- `plugin/final_plugin.py`: **91 physical lines / 80 nonblank noncomment lines**. Offload class: **32 lines**; wind subclass: **18**; passive bootstrap engine: **7**; explicit delayed-decision factory: **7**. The rest is public-API bootstrap/stamp policies/imports/factories.
- Entry-point distribution metadata: **14 TOML lines**. No core, kernel, viewer or AeroGraph edits.
- Generated YAML: **298 lines** for offload, **375** for wind. Wind carries copied P1 message descriptors, including unused ones; these counts include that avoidable boilerplate. Scenario-generation script: **67 lines**; assertions/replay/counterfactual driver: **41**. These are not 298/375 lines of model logic.

Two runs of each scenario produced identical bytes and replayed every final applicable field:

| Scenario | Final cut | Journal bytes | SHA-256 |
|---|---:|---:|---|
| Offload | 43 | 159,401 | `65d9b9a8043e94cb75429d052ff9f31e1bbd50b20de001d9f1842b0b5b15664d` |
| Wind | 113 | 364,812 | `190ac77aa8846e5e06841bb880462351e5294328bb0fb92d1b14cc755ab0338a` |

Measured run+close+replay was approximately 0.19/0.41 s in the first pair; these tiny authored workloads are not scaling benchmarks. Logs/results: `stress-log.txt`, `stress-results.json`, `offload-run{0,1}.jsonl`, `wind-run{0,1}.jsonl`. A later replacement of an imported bootstrap helper with the same public APIs preserved all four journals byte-for-byte.

### Friction actually encountered

1. **Motion/energy cannot be split among plugin owners.** Reassigning just energy produces `ScenarioError: engines.motion.config: kinematic.ownership.uav-1: all model slots must share one writer` (`P/engines/kinematic.py:284`). The kernel permits field-wise ownership, but this engine requires all three slots. Wind consumption therefore needed a subclass. Its model remains ENU, three-dimensional, point-mass movement (`:18`, `:189`); that is a model restriction, not a kernel restriction.
2. **Extending energy also requires causal-envelope care.** `Kinematic.step` resets `ctx.inputs` per mover (`:452`), dropping a weather read performed before `super().step`. The new subclass explicitly attaches that weather version to its fact proposals. Merely changing the energy formula would calculate the right value with incomplete provenance. Its command energy admission still derives from the base model and cannot forecast future wind; production route budgeting must share the extended model rather than copy the old formula.
3. **Registry validation loses the useful field path.** Authoring an integer `−5` for a `number` field yields only `ScenarioError: scenario: VALUE_SCHEMA: expected strict number`; a wrong state enum similarly yields `VALUE_ENUM` without the entity/field. Floats and the declared enum values resolve the inputs. `P/scenario/loader.py:359` does not annotate the validator error. The native `mn.node.ready` field is a string, so a boolean readiness assumption would also be wrong.
4. **A missing sampled context escapes as bare `StopIteration`.** Removing the sample binding while retaining the threshold engine gives `StopIteration: ` (`P/engines/threshold.py:38`; `P/platform/simulation.py:38` catches only ValueError/KeyError/TypeError). Unsupported `and` AST is clearer: `ScenarioError: engines.sample-radio.config: threshold.ast: unsupported sampled comparison profile`. Unknown plugin errors identify the engine and distribution requirement. Exact probes are in `friction-errors.json` / `friction_probe.py`.
5. **A compound link/policy expression needs a plugin.** Threshold evaluates one selected field/relation against a parameter, with the selector role named `subject` (`P/engines/threshold.py:84`). SINR-plus-permission-plus-node availability was calculated in the offload engine. Pinning an AeroGraph JavaScript source hash does not execute its arbitrary AST; it is provenance for the selected profile.
6. **Installed plugins disappear from Studio's selector.** Runtime catalog listed all four `final_*` factories plus three native factories; Studio listed only kinematic/PX4/SUMO/ns-3/workflow/decision/logistics/inspection. Its hard-coded tuple also omits records and threshold (`P/authoring/catalog.py:83`). YAML/API execution works; UI discovery does not.
7. **Entity-local messages need embedded typed subject references.** The offload job completion and wind command/arrival messages use permitted string entity IDs. Their journals contain them, yet the inspector/timeline says **“No recorded items” / “0 recorded commands / events.”** Projection discovers only embedded `$ref` values (`P/services/projector.py:202`); inspector/timeline filter exclusively by those subjects (`F/viewport/EntityInspector.tsx:13`, `F/viewport/MissionTimeline.tsx:8`). Add documented semantic subject bindings or explicit typed subjects; do not guess arbitrary strings or fabricate messages.
8. **Nonspatial data is supported, but the presentation is spatial-first.** Offload renders an empty ground/grid with a correct job/edge inspector and “6 entities · no spatial binding.” No fake pose was created (`F/viewport/entities.ts:69`). A graph/network/queue presentation would make this a development platform for such models rather than a 3D viewer beside an inspector.
9. **True live ingress is not reachable from the platform SDK.** Setting the scratch passive plugin to `Timing('real_time')`, even with `run.pacing: realtime`, yields `KernelError: INGRESS_POLICY: real_time requires a declared watermark policy`. Kernel supports that policy; `Simulation` neither accepts nor supplies it, and the scenario schema has no ingress-policy binding (`P/platform/simulation.py:21`, `:54`; `P/scenario/loader.py:167`; `K/coordinator.py:200`). Wall-clock pacing is a separate feature. Reproducer: `realtime-gap.json`, passive plugin `mode` config.

Browser verification used the real `/v1/runs` worker/service and the built frontend, not `/viewer-demo`: `browser-review.mjs`, `browser-results.json`, `offload-viewer.png`, `wind-viewer.png`. Both pages had zero captured console/page errors and displayed exact committed values/relations. Worker `RunSession` uses additional host advance boundaries, so its journal cut differs from direct `run_until`; byte determinism here means identical inputs **and advance protocol**, not identical bytes across different host grant sequences.

## C. Cross-engine causality, replay and determinism

`cross-engine.yaml` combines kinematic motion, assignment/workflow, independent acceptance, record production, a fake LLM decision provider and sampled threshold evaluation. The fake provider is explicitly injected at the provider method in the scratch driver; it makes no network/model call and is not a production fallback.

The first feedback configuration failed with **`SAMPLE_CYCLE: zero-lag sampled feedback path`**. A threshold event can cause another decision/command back into its upstream engines, so a delay or coupling policy is required. This is a useful rejection, not missing ordering. The decision configuration has no lag parameter; a seven-line external factory declares **100 ms incoming message lag**. Workflow separately already exposes `message_lag_ns` (`P/engines/workflow.py:101`), so a delay on that edge is another option with different research meaning. The sampled manifest names the complete upstream cone, including the decision partition.

Measured order in the successful run:

| Simulation ns / microstep | Committed consequence |
|---|---|
| 1 / 1 | First fake decision, with simulation time held during a 10 ms wall wait. |
| 1 / 2 | Workflow submits the typed move command and records order execution. Fixed-step recipient latches control to its grid. |
| 2,100,000,000 / 3 | Sampled `aas.p1.reached_x` emits with observed interval **[2.0 s, 2.1 s]**. This is not an exact continuous crossing-time claim. |
| 2,200,000,000 / 2 | Decision receives that event after the declared 100 ms lag. |
| 5,100,000,000 / 1 | Kinematic engine reports actual arrival. Business state subsequently becomes `awaiting_acceptance`; arrival alone does not accept the order. |
| 5,200,000,000 / 2 | Independent acceptance workflow emits its business acknowledgement; decision receives the delayed arrival event. |
| 5,200,000,000 / 3 | Order becomes accepted and its business command succeeds. |
| 5,300,000,000 / 2 | Decision receives the delayed terminal receipt. |

The provider was called **four times**. Each 10 ms wall wait preserved the kernel's ns/microstep exactly. **2,181 explicit cause references** were checked to precede the citing item. Kernel enforces declared read cuts/lag (`K/state.py:481`), whole-wave ownership (`K/transactions.py:612`), and journal append before state publication (`K/coordinator.py:384`). This run does not establish external numerical reproducibility or Gazebo–SUMO contact physics.

Two full runs produced **5,566,893 identical journal bytes**, SHA-256 `964bbe4dd941bf8c8cadcc6cc80fbc9f315387e30861cc4cb2b498b20d4e810b`. Replay was run with the provider replaced by a function that would fail if called: zero calls occurred. Final fields and cuts matched. Independent complete-prefix replay at **cuts 22 / 210 / 391 / 947**, corresponding to **1 ns / 2 s / 4 s / 10 s**, checked **50 applicable fields per cut** against full replay queried with that exact knowledge cut. All four prefixes were complete.

Evidence: `cross_engine.py`, `cross-results.json`, `cross-{0,1}.jsonl`, `cross-prefix.py`, `cross-prefix-results.json`, `cross-zero-lag-failure.txt`. Separate kernel hash-seed tests `[0,1,71]` passed after providing the scratch checkout's expected interpreter path. Native deterministic physics, real LLM determinism and cross-host floating-point equality were not tested.

## D. Maintainability and coverage

Counts use Python AST import targets and physical `.py` lines, including docstrings/blank lines; generated metadata/caches/venvs are excluded. Dynamic factory imports are excluded from the import count. Full per-package counts/imports/function lengths are in `code-metrics.json` and `imports.json`.

| Scope | Python files | Physical lines | Internal AeroAgentSim import targets |
|---|---:|---:|---:|
| Legacy agent/component/task/workflow/manager | 43 | **12,589** | **125** |
| Additional legacy core | — | **5,468** | Not included in the five-directory import count |
| New platform/scenario/engines/services/integrations/adapters/agents/packs/authoring | 53 | **11,998** | **89** |
| Independent kernel runtime | 33 | **10,827** | **0** platform imports |

The five old directories repeatedly depend on mutable `core`: **64 import targets** (agent 7, component 9, task 9, workflow 20, manager 19). The new runtime has **zero** imports from those five directories, legacy `core`, or legacy `dataprovider`. New+kernel totals **22,825 lines**, compared with old five+core **18,057**: approximately **26.4% more**, not a reduction claim. The new side also implements registry compilation, replay, services and adapters, so this is not equal functionality per line.

Maintenance improved principally through authority and boundaries. Writer enforcement makes physical facts authoritative; viewer refresh cannot drive integration. It did not remove complexity. Largest functions include kernel transaction operation handling **307 lines**, replay-record processing **307**, RPC projection **258**, workflow constructor **263**, kinematic constructor **212**, service app factory **241**, and decision execution **194**. Bootstrap/stamp boilerplate, replay/transaction symmetry, and long-lived history require continued care. These sizes are measured, not proof that those functions are incorrect.

For **wind affecting flight energy**, the review prototype changed **two logical places**: the new plugin and its scenario/registry bindings, plus distribution metadata and a generated pinned snapshot. It changed **zero** existing core/engine/viewer files. This is a smaller change surface than the survey's approximately seven legacy layers, but is not a complete physical wind implementation. Production integration still needs an input producer, a stable energy-contribution interface, scenario binding and tests; logistics route budgeting (`P/packs/logistics.py:120`, `:177`) must consume that same model. Otherwise a correctly calculated wind cost can disagree with duplicated mission admission estimates. Pose drag, control, native Gazebo wind and sensor calibration add further model work.

Coverage results (non-Docker; branch coverage enabled):

**Final platform result: 367 passed, 5 deselected, 939.80 s.** Logs: `pytest-platform.txt`, `coverage-platform.json`.

| Package | Statement coverage | Branch coverage |
|---|---:|---:|
| platform | 140/170 (82.4%) | 33/48 (68.8%) |
| scenario | 214/247 (86.6%) | 102/134 (76.1%) |
| engines | 796/943 (84.4%) | 370/514 (72.0%) |
| services | 405/471 (86.0%) | 129/176 (73.3%) |
| integrations | 820/937 (87.5%) | 368/460 (80.0%) |
| adapters | 682/1041 (65.5%) | 204/360 (56.7%) |
| agents | 497/591 (84.1%) | 191/270 (70.7%) |
| packs | 607/677 (89.7%) | 223/288 (77.4%) |
| authoring | 618/762 (81.1%) | 219/326 (67.2%) |
| **New packages total** | **4779/5839 (81.8%)** | **1839/2576 (71.4%)** |
| Kernel | 5021/5312 (94.5%) | 1937/2238 (86.6%) |

The combined statement+branch figure is **78.6%** (pytest rounds to 79%). Adapter coverage is the lowest at 65.5% statements / 56.7% branches. CLI and worker modules also have unexercised paths; the separate quickstart/browser runs were not added to this coverage data. Kernel coverage: `coverage-kernel.json`.

Kernel initially reported **584 passed / 5 failed / 2 skipped**. The three hash-seed failures expected `scratch/aerokernel/.venv/bin/python`, absent because the venv was intentionally not copied; the two audit tests required their output beneath the kernel workspace, whereas the first basetemp was a sibling. A scratch interpreter symlink and basetemp under `scratch/aerokernel/` resolved all five: **15/15 determinism tests** and **2/2 audit retests** passed. No source was changed. Kernel totals above retain the original coverage run rather than blending retest line coverage.

Platform test collection first failed at three HTTP-client imports: the fresh unconstrained FastAPI/Starlette selection required **`httpx2`**, while the declared dev extra contains only `httpx`. Adding `httpx2` and pytest-asyncio in scratch enabled the final run. This is a dependency/onboarding defect for development installations, not a README CLI failure. There were also two review-command path typos before collection; those were corrected and are not counted as platform failures.

Coverage is of Python code exercised by non-Docker tests, including protocol fakes; it is not native simulator coverage, frontend coverage, validation of every AeroGraph type/field/predicate, or a full migration guarantee. No legacy-extra coverage was run. A fair claim is that the new tested boundary is maintainable without entering legacy state machinery, not that every retained legacy feature has migrated.

## E. README quickstart and frontend

Followed the README order using fresh `/tmp/aas-final/venv` and sibling source copies (`platform/`, `aerokernel/`) so `.venv`, egg-info, runs and frontend output would not be created in the reviewed repositories. The copied scenario still points to the actual read-only AeroGraph source; compiler provenance reads Git files directly and invokes no Git process (`P/integrations/aerograph/source.py:157`). Path/output adaptations are the only deviation from the literal workflow.

| Step | Actual result |
|---|---|
| Required Python → fresh venv; `pip install -e ../aerokernel -e '.[server]'` | **Passed**. Kernel/platform installed independently, without SimPy/NumPy/model SDK. `pip check`: no broken requirements. Install log: `install.txt`. |
| `aeroagentsim run scenarios/p1-slice.yaml --out runs` | **Passed**. `runs/p1-slice-714cb180045b`; **22 simulated s / 7.886 wall s = 2.790× RTF**, peak RSS **113,360 KiB**. |
| Replay the printed directory | **Passed**. Cut **1645**, time **22,000,000,000 ns**, `incomplete:false`. |
| `aeroagentsim serve --help` | **Passed**. Run/source/frontend/host/port options exposed. |
| README Python `Simulation` snippet | **Passed**, printed **1,000,000,000**. |
| `cd frontend; npm ci` | **Passed** using scratch npm cache, 407 installed packages. |
| `npm run build` | **Passed**, fresh output redirected to scratch; **23.80 s**. Typecheck also passed. |
| Serve the built frontend and inspect actual runs | **Passed** on scratch port 18764; both new worker-backed scenarios completed and rendered, with zero captured page/console errors. |
| Supplemental frontend tests | **16 files / 68 tests passed**, 7.75 s. |

**No README quickstart functional failure was observed with the matching local AeroGraph checkout.** It is still host-specific: the headline scenario pins `/mnt/data2/weizhiwei/AeroGraph` and a native evaluator source hash. A sibling kernel checkout or published matching distribution is mandatory. `INSTALL.md:55` explains this and supplies source/snapshot alternatives; it is not an undisclosed fallback. The older `docs/getting_started.rst:14` teaches a dev-only installation followed by legacy `Environment`, without the legacy extra, and should be migrated or visibly labeled.

The frontend's new maintained TS/JS source (excluding generated/vendor/build/tool output) is **88 files / 11,505 lines**; the similarly filtered AeroBench source is **157 / 55,850**. Old hotspots are `app.ts` **4,332 lines** and `map.ts` **4,093**; the new largest maintained file is **592**. These indicate decomposition and narrower scope, not equal feature completeness. `F/viewport/samples.ts:41` separates lerp/slerp from exact facts; `F/viewport/bindings.ts:4` uses declared type ancestry instead of drone kind names. Our screenshots show readable stamped values but a minimal marker/base scene. Full City Studio/road/terrain/building authoring, usable nonspatial views and comparative visual/performance acceptance remain necessary for the owner's stronger frontend goal.

Two concurrent WorkBuddy DSH audits actually completed with exit status 0 and separate reviewed artifacts: `glm-maint/findings.md` and `glm-ui/findings.md`, plus stdout/reasoning logs. Their scratch configurations select **`workbuddy/glm-5.3-flash`**, **131072 maxTokens**, with no effort parameter. Initial attempts encountered a read-only global profile and then a relative plugin symlink; copying configuration and resolving that symlink in scratch made both sessions run. The UI agent could not see the survey in its sandbox; its reconstructed background was not substituted for this review's direct survey read. I verified headline claims, corrected its old-AAS-map versus AeroBench-path conflation, and did not promote its unproven multi-partition-reset consequence to a runtime bug.

## F. Remaining work

P0 gates distinguish **merging a dependable platform** from **retiring its migration source**. Existing unverified PX4 work is tracked as pending, without assigning it a fabricated regression.

| Priority / gate | Specific remaining work | Concrete next step |
|---|---|---|
| **P0 — main merge** | Platform cannot bind a `real_time` engine despite kernel support. | Expose and pin ingress policy/clock/watermark configuration through Scenario/Simulation and run service. Connect actual live submissions and sealing to that policy; run one late-input/reject/delay/replay example through ordinary platform construction. Keep wall pacing separate. Use `realtime-gap.json` as the acceptance counterexample. |
| **P0 — main merge** | Fresh development HTTP tests fail with the current unconstrained dependency resolution. | Declare the actual test-client dependency or constrain a compatible server/dev set. Run a clean `.[server,dev]` install and the non-Docker suites in CI without ad-hoc extra installs. |
| **P0 — consolidation/retirement** | Real-PX4 logistics is changing concurrently; this review did not validate the final native business/physics path. | Finish that job, pin both package revisions and image inputs, then run native pickup/flight/drop/dwell/independent acceptance plus failure/replay gates. Publish native states, receipts, custody and timing evidence. Re-run changed adapter contract tests on the pinned result. |
| **P0 — retirement** | AeroBench capabilities/assets are not fully independent or dispositioned. | Reconcile `CAPABILITIES.md` against the actual tree; migrate required City Studio/scenario/conversion/deployment features or obtain explicit scope decisions. Replace asset/map defaults with packaged, attributed inputs or configuration outside the old tree. Demonstrate a clean run/build/authoring environment with the old project unavailable before deletion. Current Dockerfiles already use public pinned bases; do not repeat the stale claim that they still require AeroBench bases. |
| **P1** | Wind/energy extensions require implementation subclassing and manual provenance repair; logistics duplicates estimates. | Add a stable contribution/energy-model interface with one writer and retained input causes; have command/route budgeting consume the same model. Add a weather producer and calm/gust/reserve tests with stated units/frames and calibration limits. |
| **P1** | Sample context/type/value errors do not reliably identify the authored location. | Resolve contexts explicitly and return `ScenarioError` with engine/context; wrap registry errors with entity/field path. Preserve genuine error causes and missing data. Document strict-float semantics or implement an explicit compiler normalization policy. |
| **P1** | Studio engine discovery and entity message correlation fail for valid plugins/messages. | Enumerate installed catalog entries and provide plugin configuration descriptors; define semantic subject bindings in message descriptors/feed projection. Cover nonspatial jobs and legacy string-key schemas without guessing subjects. |
| **P1** | Complete arbitrary AeroGraph predicate/event semantics remain absent. | Implement a version-pinned evaluator plugin for a bounded supported dialect with native differential tests, temporal history, roles, source clocks and relation evidence. Extend the supported set explicitly; do not reinterpret a compound AST as a scalar threshold. |
| **P1** | Full frontend quality/authoring replacement is not established. | Use the same licensed city/assets and run traces in old/new viewers; measure hardware-GPU frame times, interpolation, picking, camera modes and editing workflows. Add graph/queue/network views or an inspector-first nonspatial layout. Keep exact research facts separate from display effects. |
| **P1 — capability dependent** | Cross-engine contacts and native cancellation remain unsupported. | For scenarios requiring them, choose contact authority and explicit geometry/mobility coupling; implement measured command cleanup/cancel receipts before advertising those capabilities. Until then keep unsupported capabilities explicit. |
| **P2** | Kernel/feed/decision history grows without a bounded retention mechanism; large constructors/transaction/RPC/replay functions remain. | Measure a long run's memory and seek latency, introduce indexed/paged history with replay-preserving limits, and bound decision receipt/prompt history. Refactor measured hotspots around stable contracts rather than adding repeated validation to every tick. |
| **P2** | Release/onboarding documentation and multi-partition reset ergonomics lag. | Publish/pin a matching kernel release, update legacy RST tutorials, add a minimal installed-plugin example, and specify whether reset supplies per-partition views or a documented restricted bootstrap API (`K/coordinator.py:474`). Verify that contract with a multi-partition SDK example rather than assuming the first-view behavior is already a proven failure. |

The evidence supports continued consolidation on this architecture. It supports neither deleting AeroBench now nor claiming complete native physics, arbitrary ontology execution or a visually superior full development workbench.

## Reproduction record

Scratch root is `/tmp/aas-final`; all paths below stay there except read-only source imports. Complete logs include initial environment/path failures and their corrections.

```bash
PY=/mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python
# Source copies exclude .git, .venv, caches, generated egg-info and node_modules.
$PY -m venv --symlinks /tmp/aas-final/venv
cd /tmp/aas-final/platform
/tmp/aas-final/venv/bin/pip install --cache-dir /tmp/aas-final/pip-cache \
  -e ../aerokernel -e '.[server]'
/tmp/aas-final/venv/bin/aeroagentsim run scenarios/p1-slice.yaml --out runs
/tmp/aas-final/venv/bin/aeroagentsim replay runs/p1-slice-714cb180045b
/tmp/aas-final/venv/bin/aeroagentsim serve --help

# Additional installed scratch plugin and test-only dependencies:
/tmp/aas-final/venv/bin/pip install --cache-dir /tmp/aas-final/pip-cache \
  -e /tmp/aas-final/plugin httpx httpx2 pytest-asyncio
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=/tmp/aas-final/platform/src:/tmp/aas-final/aerokernel:/tmp/aas-final/plugin:/tmp/aas-final/venv/lib/python3.11/site-packages
$PY /tmp/aas-final/make_stress.py
$PY /tmp/aas-final/run_stress.py
$PY /tmp/aas-final/cross_engine.py
$PY /tmp/aas-final/cross-prefix.py
$PY /tmp/aas-final/friction_probe.py

COVERAGE_FILE=/tmp/aas-final/.coverage-platform $PY -m pytest \
  tests/platform tests/integrations tests/adapters tests/agents tests/packs tests/authoring \
  -m 'not docker and not llm and not legacy' -p no:cacheprovider \
  --basetemp=/tmp/aas-final/pytest --cov-branch \
  --cov=aeroagentsim.platform --cov=aeroagentsim.scenario --cov=aeroagentsim.engines \
  --cov=aeroagentsim.services --cov=aeroagentsim.integrations --cov=aeroagentsim.adapters \
  --cov=aeroagentsim.agents --cov=aeroagentsim.packs --cov=aeroagentsim.authoring \
  --cov-report=json:/tmp/aas-final/coverage-platform.json --cov-report=term

cd /tmp/aas-final/aerokernel
PYTHONPATH=/tmp/aas-final/aerokernel COVERAGE_FILE=/tmp/aas-final/.coverage-kernel \
  $PY -m pytest tests -p no:cacheprovider --basetemp=/tmp/aas-final/kernel-pytest \
  --cov=aerokernel --cov-branch \
  --cov-report=json:/tmp/aas-final/coverage-kernel.json --cov-report=term

cd /tmp/aas-final/platform/frontend
npm ci --cache /tmp/aas-final/npm-cache
npm run build -- --outDir /tmp/aas-final/frontend-dist-fresh
npm run typecheck
npm test -- --maxWorkers 4 --minWorkers 1
# server.txt records the scratch-only HTTP service on port 18764.
TMPDIR=/tmp/aas-final/browser-tmp node /tmp/aas-final/browser-review.mjs
```

Drivers use exclusive new journal paths; preserve/rename their previous scratch outputs before repeating them. `make_stress.py` emits the initial scenario form; `run_stress.py` corrects it to the declared native order/command enums before execution. `cross_engine.py` pins its own snapshot and authors the sampled feedback/lag declaration. Those review-authoring corrections, and an initial browser assertion expecting rounded `940` instead of the exact stored float, are not product failures.
