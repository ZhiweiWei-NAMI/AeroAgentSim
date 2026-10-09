# Shared runtime and console architecture

Status: **normative target design, design only**, 2026-10-08. D1 starts at platform `ff4f740`. MUST denotes a requirement; SHOULD permits a documented alternative. Proposed formats, modules and API additions below are not implemented by this document. The kernel's [DESIGN.md](../../../aerokernel/docs/DESIGN.md) and [K4 ingress amendment](../../../aerokernel/docs/platform-notes/K4.md) govern execution; this specification adds platform behaviour and console contracts without changing kernel semantics. The [traffic accident migration](demo-traffic-accident.md) is the first complete example.

## 1. Architecture decision and present implementation

There MUST be one `Kernel` per run and one generic DES behaviour executor using that kernel. The console authors configuration and sends recorded controls; it does not execute event chains. Physics, weather and decisions are plugins inside the same coordinated run. They may have private native state, but the kernel is the sole authority for published identities, state history, time, delivery and receipts.

The four core responsibilities map as follows. File paths are relative to the platform unless marked kernel.

| Responsibility | Existing implementation to retain | Gap and target change |
| --- | --- | --- |
| Time coordination | `platform/simulation.py` wraps kernel coordinator; kernel horizons, grants, `Instant(ns,microstep)`, timer delivery, reactive waves, sampled levels and seals | No second scheduler. Behaviour advertises DES wakeups and uses kernel timers; native adapters retain exact-stop/hold/latching contracts. Wall pacing never selects event order. |
| External event ingestion | `platform/ingress.py`, `services/{app,worker}.py`, `telemetry` engine; kernel admission, rational clocks, idempotency and WAL | D1 `realtime.md` describes the older shared watermark. K4 and in-flight Q9 replace this with `IngressStream` declarations and typed admission receipts. Add a generic typed event injection gateway; do not bypass ingress through UI mutation. |
| State updates | Kernel per-instance-field writers, transactions, lifetimes, temporal relations, bitemporal reads; `EngineBuild.owned_fields`; kinematic/environment/native adapter producers | Retain one writer for every active field. Declarative chains gain field/edge/lifecycle actions, with authority compiled before binding. Rules cannot overwrite plugin telemetry. |
| Event-chain execution | `engines/workflow.py` DES machines; `threshold.py` sampled comparisons; `agents/decision.py`; `packs/{logistics,inspection}.py` | Workflow machines are explicitly enumerated, guards have a narrow comparator language, and threshold is not a general AeroGraph evaluator. Introduce compiled, type/task/relation-bound chain templates, lifecycle instantiation and conflict rules, reusing Q6 evaluation. Decisions propose typed actions through this path. |

The current workflow already has typed commands/events, owned-field sets, lifecycle actions, state-entry timers, authored-order transition choice, correlation and independently tracked child receipts. Preserve those semantics during conversion. Its ordinary machines are a special case of the new behaviour runtime. `workflow` MUST become a compatibility compiler/shim into that executor; it MUST NOT remain an additional world clock or alternative chain interpreter. Preserve OR trigger categories plus an AND guard, consumed unmatched events, state revisions invalidating stale timers, and children surviving parent state changes. Byte-compatible reexecution of historical workflow WAL is a separate compatibility gate; historical WAL replay does not depend on that compiler.

`threshold` MUST likewise become a shim compiling its supported AST/profile to Q6 definitions and sampled contexts. The supported comparison/presence profile stays explicit; unsupported compound expressions cannot be flattened to thresholds. A run MUST NOT enable both the old threshold producer and its replacement for one event contract.

Q6 `wt-q6/src/aeroagentsim/engines/{predicate,predicate_ast}.py` currently contains a bounded, version-pinned Python AST evaluator and a sampled partition with history, sources/clocks, diagnostics, `entered/exited/level` emission and native differential-test targets. Reuse the evaluator **as a library** for compilation and guard evaluation; keep the sampled partition **as the one evaluation adapter** for native temporal contracts. Do not copy its operators into workflow, decision or the browser. Its code and operator support are in flight; neither this document nor a native definition's existence asserts support for all AeroGraph dialects.

`engines/kinematic.py` remains a selectable ENU point-mass physical owner; `environment.py` remains an authored environmental producer. `models/` supplies shared consumption/budget models, not a second energy writer. `agents/{observation,tools,provider,decision}.py` supplies scoped observations, typed tools and bounded recorded model calls. Domain packs may implement geometry, feasibility and acceptance computations; their routine orchestration SHOULD compile to chains instead of duplicating scheduling.

Legacy `agent/`, `component/`, `task/`, `workflow/`, `manager/`, mutable `core/`/SimPy and old workbench APIs are deprecated for new platform scenarios. Their singular package names distinguish them from the current `agents/` package. Do not import them into `platform`, the behaviour executor or kernel. AeroBench's demo `Runtime`, its motion thread and LangGraph world mutation are migration sources, not additional platform runtimes. Retirement remains subject to [CAPABILITIES.md](CAPABILITIES.md), [RETIREMENT.md](RETIREMENT.md) and [review §F](../reviews/PLATFORM-review.md#f-remaining-work); this design does not authorize deletion.

## 2. Compiled behaviour model

Proposed platform modules are `src/aeroagentsim/behaviours/{schema,compiler,bindings,evaluation,records}.py` and `src/aeroagentsim/engines/behaviour.py`. The executor is domain neutral: it understands registry IDs, typed values, chain states, actions, cuts and receipts, never vehicles or accident stages. Packages contain data; plugins supply computations and capabilities.

A chain template MUST declare:

| Element | Normative meaning |
| --- | --- |
| Identity and binding | Stable template ID/revision, role names and compatible types, binding selector, cardinality and multiplicity. |
| Trigger | Typed event, timer, lifecycle/relation assertion, receipt, or predicate with explicit `entered`, `exited`, or `while` semantics and evaluation profile. No implicit polling. |
| Preconditions | Predicates that MUST be known true before activation. Unknown blocks activation with the actual missing source recorded. |
| Chain state | Named finite states, initial and terminal states, local variables with schemas, state-entry revision and retained child actions. |
| Guarded transitions | `from`, trigger, guard predicate, `to`, ordered actions and explicit priority. Guards MUST be known true against the invocation's committed read cut. |
| Actions | `set`, `emit`, `command`, `cancel_command`, `delay`, `cancel_timer`, `assert_relation`, `close_relation`, `create_entity`, `remove_entity`, `complete`. Every action has a stable authored ID. |
| Completion policy | Child receipt requirements (`all`/`any` with explicit child IDs/statuses), deadlines and failure/rejection/cancellation branches. Transport or admission acknowledgement is insufficient. |

`set` can write only fields owned by the behaviour partition for that entity generation. It validates the descriptor, numeric kind, unit/frame binding and interval. It cannot write a chain's state field twice through both transition machinery and an explicit action. `emit` uses a registered event schema and declared topic. Commands use a registered command schema, target and payload, receive kernel command IDs and retain all execution receipts. Delays schedule positive physical durations; a zero-delay continuation uses the next reactive microstep. Timers carry instance ID, state-entry revision and action ID, so canceled or superseded timers cannot advance a new state.

Relations have explicit IDs, endpoints, acquisition and half-open validity; `close_relation` closes a particular assertion, not every similarly named edge. Transfer can atomically close/assert edges only within the executor's edge authority and registry cardinality. Entity creation requires lifecycle authority and an ID domain pinned before binding. Cross-owner creation commits identity first; newly bound owners publish initial facts in a later writer wave. Creation does not let a chain initialize another owner's fields. Removal waits for declared action cleanup, invalidates timers/bindings and obeys kernel incident-relation rules. A chain MUST NOT fabricate a canceled physical command if its plugin cannot cancel it.

A consumed event cannot silently become a durable inbox. A template requiring deferred work MUST store a typed pending item explicitly. Retries are new, bounded, journaled actions with idempotency policy; a failed physical/stateful RPC is never automatically repeated.

### 2.1 Automatic instantiation and lifecycle

Bindings use actual AeroGraph `is_a` ancestry, not one of its seven browsing directories. They may bind entity type/ancestors, task types or declared task-kind fields, and role tuples selected through registered relations. A binding specifies `once_per_entity`, `once_per_relation`, or `once_per_task_episode`; multiple matches across ancestors do not create accidental duplicates. Exact overrides and priority ties follow explicit compiler rules; ties fail validation.

At bootstrap, on committed entity creation, relation assertion, task episode activation, and changes to selector fields declared as dependencies, the binding manager computes affected tuples in stable identity order. Its key is `(run,epoch,packageDigest,bindingId,templateId,role EntityRefs,episode/edge assertion version)`. A deterministic encoded ID or digest of that tuple identifies the instance; wall UUIDs are forbidden. Each instance has a monotonically recorded revision. Reasserting the same edge version does not instantiate again; a new task episode or entity generation does.

Instances are journaled as ordinary typed records. Proposed registry overlay type `aas:BehaviourInstance` has behaviour-owned fields `aas.behaviour.template_id`, `aas.behaviour.binding_id`, `aas.behaviour.roles`, `aas.behaviour.state`, `aas.behaviour.revision`, `aas.behaviour.status` and `aas.behaviour.children`. Instance lifecycle events are `aas.behaviour.created/transitioned/completed/failed/canceled`. These are proposed portable platform descriptors, not native AeroGraph definitions. The lifecycle controller creates an instance before its fields are published. Source entities without a 3D pose remain valid binding subjects.

A binding becoming inapplicable does not erase history. The binding's explicit policy is `close_after_cleanup` or `retain_until_terminal`; removal of a role generation prevents new live commands immediately. Pending child actions remain correlated until their real terminal receipts or a recorded timeout. At restart, replay reconstructs instances; it does not instantiate them again. Resuming interrupted execution requires a separate checkpoint/native-restore capability, presently unverified.

### 2.2 Predicate evaluation and conflict rules

“Continuous evaluation” means observing every relevant **committed** state change and declared validity/timer boundary. It does not mean evaluating interpolated 3D coordinates or claiming knowledge of physical motion between published samples. The compiler builds a dependency index; only dirty contexts and their deadlines are evaluated. Unrelated commits and viewer polling cause no evaluations.

Two explicitly named profiles reconcile event-chain cascades with the existing kernel/Q6 contract:

| Profile | Evaluation boundary and edge semantics | Restrictions |
| --- | --- | --- |
| `committed_reactive/v1` (new orchestration) | Stateless Boolean body evaluated through Q6's library after a relevant committed wave; known false→true is `entered`, true→false is `exited`, including successive microsteps at the same ns | The wrapper journals previous/current cuts, identities, sources and clocks. It is a declared behaviour edge contract, **not** native AeroGraph temporal `entered`. No native temporal/window operators in this profile. Initial true and unknown→true produce no edge. |
| `aerograph_sampled/v1` | Q6 sampled partition evaluates once per context/physical time after the full upstream cone settles; use native temporal history and its pinned dialect | Native source/clock/role continuity and known baselines remain required. Unknown breaks an edge baseline. Compiler maps package `while` to Q6 `level`; already edge-producing native event ASTs are not wrapped in another edge detector. |

`while` enables at most once per eligible evaluation revision while known true, including an explicit first evaluation. It does not automatically reactivate itself because it wrote a field. A repeating behaviour needs a positive timer or a new external/dependency change. Selector/role/source/clock changes reset the relevant baseline. `entered/exited` fire once per recorded transition identity; replayed/deduplicated delivery cannot fire twice.

Sampled evaluator→chain→upstream feedback requires a positive declared return lag or a supported coupling profile. The current kernel rejects zero-lag sampled cycles; do not remove that validation or secretly add 1 ns. The UI shows the chosen lag and its scientific effect. A reactive cycle is legal only if all members support reactions without physical reintegration and stays within kernel microstep limits. Supporting temporal resampling on every microstep would require a separately versioned semantics change; D1 does not require it.

The compiler may place sampled-feedback delivery in a lagged partition while keeping other chains reactive; these partitions use the same executor implementation and kernel, not another behaviour runtime. Declare the lag in the actual partition/message dependency graph, not just a delay hidden inside an action. The sampled upstream cone includes behaviour-owned target/task fields as well as physical state. A single broad zero-lag behaviour subscription cannot claim a delayed feedback path merely because a later physical command latches to a grid.

Native sampled roles also pin producer **and clock/mapping per role**. If one entity's fields have different acquisition policies, bind separate role aliases to the same EntityRef: for example PX4 Gazebo pose and boundary-observed MAVSDK velocity/battery. Preserve their separate clocks and require the authored identity match; do not normalize them to a fabricated common sensor timestamp. Dynamic chain instantiation itself uses reactive records and requires no dynamic kernel sample registration. For sampled guards, the baseline implementation MUST predeclare a finite set of role tuples/contexts without rebinding their epoch identity; unbounded new sampled tuples require a generic kernel context-lifecycle extension and Q6 adapter work, explicitly outside current K4. This limitation does not restrict automatic reactive chains for new entities.

A conflict rule binds roles, references a predicate and edge, and emits a typed conflict event with those roles, evaluation version, evidence versions, occurrence and availability. Chains subscribe to that event. Examples are occupancy conflicts, two requests for one resource, or task interruption conflicts; the generic executor does not implement those domain calculations. Conflict resolution may set behaviour-owned reservation/task fields or command a physical owner. It MUST recheck the current guard before committing a proposal based on an older decision.

Known false, unknown, absent facts, null values permitted by a schema, and invalid input are distinct. Complete empty relation queries can establish `exists=false`; missing field/source/clock coverage cannot. Diagnostics MUST identify the entity, field/context, configured producer and failing authored path. Required data paths MUST be repaired by supplying the producer, valid history, transform or supported computation; marking the display UNKNOWN is not an integration repair. Optional unavailable inputs may block a branch only when that policy is authored and visible. No default physical sample or success is synthesized.

## 3. Ordering, causes and replay

The kernel orders physical advance, lifecycle/controller waves, ordinary reactive waves, sampled levels and seals. Behaviour MUST fit these phases and cannot impose a vehicle→network→business global stage sequence. Q9/K4 may service safe independent partitions at a selected boundary while another stream waits; the global seal still waits for all relevant streams and due work.

Within one behaviour invocation, candidates are ordered by `(priority descending, bindingId, instanceId, transition authored index, trigger delivery order)`. Delivery order is the kernel's order, not thread/model completion order. One transition per instance per reaction is the default. Multiple enabled transitions use `first_enabled`; another policy must be explicitly compiled and deterministic. All guards read the same immutable invocation cut; an action cannot read an earlier action's uncommitted write. Dependent continuation runs in a later microstep. Conflicting sets or reservations proposed by different instances in one executor wave are arbitrated in this stable order and losing proposals receive a typed conflict result; they cannot rely on last-write-wins.

Same-instant outputs become inputs only in later microsteps. Physical integration happens once per partition per selected physical boundary. Kernel `max_microsteps` (currently default 1024) bounds the whole cascade; a package also declares a positive per-instance transition budget (compatibility default 64) and timer/creation budgets. Exhaustion faults with instance/transition/cause trace and retains the valid prefix; work is not silently dropped or advanced by an invented delay.

Every fact/event/edge/receipt produced by behaviour MUST cite its initiating event/timer/lifecycle record, predicate evaluation and consumed fact/relation versions, chain revision and package/transition/action identity. Native outputs additionally retain source acquisition stamps/mappings and availability. Configuration writes cite their declared inputs. A library returning a Boolean without evidence is insufficient. Evaluation/lifecycle records themselves are kernel events/facts or sampled frames, so the causal path is queryable in one WAL rather than a private console log.

Freeze scenario, resolved behaviour package/IR, registry source/runtime snapshots, evaluator revisions, plugin configs, frames, RNG seed/streams, ingress policies, decision records and asset IDs with each run. Replay reconstructs the exact recorded prefix using kernel replay and feed projection: **zero engine, model, source sensor, native simulator or RNG calls**. Derived predicate/chain views consume recorded evaluations/transitions; they do not recalculate using a newer predicate library. Deterministic offline reexecution is a separate test using identical recorded inputs and schedule. Live receipt ordering is recorded even when model completion latency differs. An incomplete/faulted journal remains incomplete/faulted.

### 3.1 External injection and per-stream ingress

External sources submit typed commands to a platform gateway/field owner; a gateway validates the named injection point and emits its declared typed event through the kernel. Events retain their original source occurrence stamp and later publication/dispatch availability. A UI, file schedule, telemetry source or decision reply cannot append arbitrary facts or events directly to the store. Proposed generic command `aas.runtime.inject_event` carries an injection-point ID and a schema-validated event payload; its supported event schemas/targets are compiled into the gateway manifest, not supplied unrestricted by a caller.

Compile Q9's additive scenario `ingress_streams` declarations into K4 `IngressStream(id,policy,mapping_id,engine_ids)`. Each has an explicit clock mapping, initial inclusive closed prefix, `lateness: reject|delay`, finite `timeout_s` and optional `allowed_lateness_ns`. Operator, decision and camera sources SHOULD use different streams when their closure policies differ. An engine may receive several streams and a stream may influence several engines; kernel dependency propagation determines safe service, not a console-selected shortcut. Every real-time partition needs a declared stream binding. Offline scheduled commands use the ordinary recorded submission path; they need no pretend live watermark.

The console uses `POST /v1/runs/{id}/ingress` with `stream_id`, command schema/target, exact `at_ns`, typed payload, rational `source_stamp` and idempotency key. An acknowledged typed receipt includes stream, journal index, disposition, mapped occurrence, command ID and actual reservation/activation/displacement, or `LATE_INGRESS` rejection with no command ID. Its success means admission only. Gateway and downstream owner execution receipts remain separate. K4 idempotency is scoped by stream and original request/stamp, including rejections; transport reconnection reuses that key rather than issuing a second action.

`POST /v1/runs/{id}/watermark` asserts an inclusive closed canonical prefix for the named stream. Source progress is a separate assertion: the worker calls `advance_source_progress(stamp,stream_id=...)` and kernel closes exactly mapped progress minus the pinned allowance. Subtract once, never clamp a regression or derive progress from the latest received input. The operator source manager must explicitly close a submitted/control queue prefix before requesting the next advance; timestamps, elapsed wall time and viewer polling alone cannot establish closure. Model/camera source managers likewise assert readiness/progress/deadline closure explicitly, so an idle UI or missing reply does not silently release an unsafe seal.

Injection is allowed during running or effective pause at any admissible unclosed boundary. At/before the sealed or stream-closed prefix, reject or displace according to the pinned policy, preserving the original stamp. It cannot retroactively change a historical cut. Paused submissions can be admitted and queued without implying execution; execution resumes at a legal boundary. A pause request during a watermark wait remains pending until safe settlement or the real timeout/fault, as in the existing service. K4 partial safe progress is visible with its actual cut while global seal is pending. Live journal 1.3 and older offline 1.2/1.1 replay compatibility must be retained; implementation must pin matching kernel/platform revisions rather than infer compatibility from the current package version constraint.

## 4. Ownership and replaceable domain plugins

Bind one writer for each `(EntityRef,fieldId)`, independently from entity lifecycle and relation authority. Behaviour owns logical task/chain/reservation fields. A physical move is `command(owner, schema, payload)`, followed by receipts and observed-state guards, never `set(position)`. A weather adjustment is a command or stamped input to its environmental owner. Initial facts MUST be published by their selected owners too.

Each plugin MUST advertise and pin:

| Contract item | Requirement |
| --- | --- |
| Configuration/capability descriptor | Versioned schema available to the installed engine catalog; field slots, input dependencies, lifecycle domains, commands/results/events, cancellation and unsupported capabilities. Availability is not readiness. |
| State supply | Exact fields and units/frames, source/native clocks/mappings, validity/acquisition policy and finite native step/hold boundaries. Dynamic identities use kernel lifecycle waves. |
| Control responses | Typed submitted/accepted/executing/terminal receipt path, target identity/generation, native correlation and errors. Receipt success states its actual completion criterion. |
| Environmental inputs | Explicit input fields, spatial sample selection/transform, latching/coupling policy and retained causes; weather never implicitly drives another simulator. |
| Coordination | Kernel `Partition`/horizon contract: DES, fixed-step, lockstep or real-time streams. Confirm native frontiers and faults; no fabricated samples during holds. |
| Reproducibility | Native/plugin revisions, reset seed, calibration, external inputs and assets recorded; close/cleanup errors affect run status. |

A replacement profile changes the selected owner and its commands at **run creation**, then recompiles consumers/ownership. Kinematic road follower↔SUMO and point-mass UAV↔PX4/Gazebo must expose compatible normalized slots, or bind an explicit conversion producer to **different** fields. SUMO's current XY field cannot masquerade as ENU; PX4's battery record cannot masquerade as joules. Transform/energy estimates have separate descriptors/provenance and consume actual native facts. Both plugins cannot own the same slot simultaneously. Runtime writer transfer is outside kernel v0.2: switching the dropdown during a run requires a new run/epoch with explicit initial conditions, not an invisible hot swap.

Kinematic's coupled position/velocity owner stays intact; its existing shared consumption interface may supply energy, or energy may have its own explicitly lagged owner. Weather may be the current `environment` profile producer, a forecast model or live stream. Authored calm values are declared model inputs. Missing weather is not calm. Default/native profiles must state which effects are implemented: the current wind contribution changes modeled energy, not ground-track drift; native weather-to-Gazebo injection is not established by selecting a weather entity.

## 5. Behaviour package and compiler

Proposed format is `aeroagentsim.behaviour-package/v1`, in YAML or equivalent JSON. A scenario's additive `behaviours` list references `{path}` packages or inline package mappings. Compile imports once from the configured scenario/workspace root; save the resolved closure/IR. A package contains registry requirements/explicit overlays, predicates, chain templates, bindings, conflict rules, declared commands/events, injection points and budgets. External references use declared paths; all times specify ns or an explicit temporal unit.

This fragment demonstrates the new surface; referenced descriptors/commands must be supplied by its registry overlay. It is **not input accepted by today's loader**. `$role`/`$trigger`/`$variable` values are typed compiler expressions, not string substitution or arbitrary Python/JavaScript.

```yaml
format: aeroagentsim.behaviour-package/v1
id: example.task-response
revision: 1
registry: {snapshot: registry.snapshot.json}
evaluator: {version: aerograph-predicate/1, native_references_ref: scenario.predicate_sources}
budgets: {max_transitions_per_instance_per_ns: 64, max_instances: 10000}
predicates:
  example.task.ready:
    profile: committed_reactive/v1
    roles: {task: 'example:Task'}
    expression:
      op: eq
      args: [{field: example.task.phase, role: task, path: []}, {literal: queued}]
chains:
  example.respond:
    roles: {task: 'example:Task'}
    trigger: {predicate: example.task.ready, edge: while}
    preconditions: [example.task.ready]
    initial: waiting
    terminal: [done, failed]
    states: [waiting, moving, done, failed]
    transitions:
      - id: launch
        from: waiting
        on: {instance: activated}
        guard: example.task.ready
        to: moving
        actions:
          - {id: mark, kind: set, entity: {$role: task}, field: example.task.phase, value: moving}
          - id: travel
            kind: command
            capability: example.motion.goto
            payload: {task: {$role: task}, destination: {$variable: destination}}
            deadline_ns: 30000000000
      - {id: finish, from: moving, on: {receipt: travel, status: succeeded}, to: done, actions: []}
      - {id: fail, from: moving, on: {receipt: travel, status: [failed, rejected]}, to: failed, actions: []}
      - {id: timeout, from: moving, on: {deadline: travel}, to: failed, actions: []}
bindings:
  - id: respond-to-tasks
    chain: example.respond
    match: {task: {is_a: 'example:Task'}}
    variables: {destination: {field: example.task.destination, role: task}}
    multiplicity: once_per_task_episode
    episode_field: example.task.episode
    on_unbind: retain_until_terminal
conflicts: []
injection_points:
  - {id: task-release, stream_id: operator, command: example.release, target: behaviour, emits: example.released}
```

`capability` resolves to one configured command schema/owner at compile time; it is not an inferred registry capability implementation. Physical `goto` completion may need an additional observed arrival/stopped guard before business completion, as in the demo. Command deadlines mark overdue chain work and request cancellation only when advertised; they do not invent a physical failure/cancel receipt.

Validation MUST resolve real ancestry and preserve source review disposition, admit local proposals explicitly, check concrete instantiable types/field applicability, relation directions/cardinality, predicate dialect/operator support, roles/parameters/history/source clocks, message/result schemas, ownership/create domains, selector overlap/multiplicity, timers/limits, full dependency cones and sampled cycles. It MUST validate typed expression substitutions as well as literal payloads. Report authored file/YAML path and required repair. Validate/compile on authored changes; structural checks are not repeated every tick. Runtime still validates actual messages/native outputs/commits where needed.

Studio stores scenario plus behaviour files and layout metadata in its draft workspace. Forms edit types, states, trigger edges, guards, actions, bindings and ownership; graph editing connects the same stable IDs. Both edit one model, not two serialized interpretations. Unsupported editor constructs remain visible in the raw YAML/JSON editor and are preserved. Export configuration files; import must preserve IDs, temporal/numeric kinds, unknown extension data permitted by the version, and authored semantics. Formatting/comments may normalize; graph coordinates stay separate authoring metadata. Editing a draft invalidates its validation, but never modifies a running scenario or replay input.

## 6. One console and two synchronized views

Extend `authoring/{api,workspace,catalog,templates}.py`, `frontend/src/studio/{StudioPage,EngineConfigForm}.tsx` and `pages/RunsPage.tsx`; do not start a separate accident server/UI. Q7 owns graph/topology/state presentation in flight; reuse its components/store contract after integration. The existing legacy `RelationGraph.js` is not evidence of a new-runtime graph view.

The console workflow is **configure → validate → run → operate → inspect**:

1. Configure scenario entities, relations, active state fields/writers, behaviour packages and plugin profiles. Browse actual AeroGraph ancestry and review provenance. Forms/graph/raw files round-trip.
2. Validate pinned registry, compiled chains, capabilities, ownership and timing; show missing producers/unsupported native capabilities with precise paths. A successful authoring preview is not a native-engine readiness result.
3. Run the frozen validated inputs through `POST /v1/runs`; one worker owns one kernel. Link the same run from Studio to Runs and AgentConsole.
4. Operate with pause/resume/stop, typed event injection and typed commands/cancel. Show requested vs effective pause, admission vs execution receipts, stream/source stamps and late displacement. New authoring changes create a new run. Playback pause is separate from simulation pause.
5. Inspect graph, 3D, fields, chain state, predicate evaluations, decision/tool records, receipts and cause paths at one selected cut. Replay uses the same console.

The graph contains entities, actual temporal relations, predicate contexts/truth, events and chain instances/states. Tasks, coordinators, weather samples and records with no pose remain graph nodes. Do not draw ontology inheritance as a running relation or planned edges as committed assertions. Draft graph and run graph are explicitly labeled.

The Three.js view uses `RunHeader.presentation` only for spatial placement and declared frame transforms/assets. The selection key is `(runId,epoch,id,generation)`, shared with the graph, inspector and decision links. Selecting a nonspatial node opens its inspector and preserves the 3D camera; it does not give the entity an origin pose. A spatial selection highlights the same generation in both views.

One temporal store owns `valid_at Instant`, `known_at Cut`, selected entity and live-follow/cursor state. Seeking a chain transition selects its actual journal cut/microstep in both panes. A frozen historical cut stays frozen while the live tail grows. Inspectors, graph labels, metrics and HUD numeric values MUST show exact recorded values and absence; no interpolation, fallback zero, reevaluation or guessed event subjects. Optional 3D animation interpolates only display geometry within supported valid samples and is labeled display time; research overlays/capture use an exact cut. Discontinuities/retractions/removal/generation barriers remain honored. Transport errors preserve the last received cut and visible error, not simulated progress.

### 6.1 Additive viewer feed extension

Keep `aeroagentsim.viewer-feed/v1` and its current entities/facts/edges/messages/receipts. Add optional header `epoch` (for full shared selection identity) and `behaviour` with package IDs and evaluator versions, predicate descriptors (roles, profile, expression reference), chain templates/bindings, and `extensions: ["predicate-truth/v1","chain-instance/v1"]`. Older feeds lacking these additions display “no recorded predicate/chain data”; absence is not false or an empty successful execution. For older headers the run manifest supplies epoch; never guess it from a selected entity ID.

Proposed optional commit arrays, projected from WAL records rather than computed by the browser:

| Array | Required record content |
| --- | --- |
| `predicateTruth` | `contextId`, `predicateId`, bound role EntityKeys, `profile`, `status: known\|required_input\|invalid_input`, `value: boolean\|null`, diagnostics, `evaluatedAt`, `readCut`, `acquired` clocks, `available`, `version`, `causes`, `validFrom/validTo` and `op: assert\|close`. Unknown/invalid requires null result plus diagnostics, never a fabricated field value. |
| `chainInstances` | `instanceId`, template/binding/package ID, role EntityKeys, lifecycle `created\|transitioned\|completed\|failed\|canceled`, `state`, exact revision, typed variables, child action/command IDs and receipt references, `transitionId`, trigger/evaluation references, `available`, `version`, `causes`, `validFrom/validTo`. |

Predicate intervals are half-open **recorded evaluation-truth intervals**, ending at the next relevant evaluation or validity boundary; they do not prove truth between unobserved native samples. Closing an open interval is a new version known at its actual commit, not a retroactive edit of earlier cuts. Chain-state intervals use the same bitemporal rule. Messages carry explicit typed subjects; string IDs require declared schema subject paths. Times/revisions needing arbitrary precision use the existing lossless wire conventions. Empty commits remain in contiguous feed indexing. SSE and paged replay use the same projector/store, with extension decoding tests; the final next cursor remains authoritative.

## 7. LLM and LangGraph decision plugins

LangGraph is an optional decision-session plugin under the runtime, not a time coordinator or field owner. Its graph state is an immutable authorized observation plus typed proposed decisions, node/call identities and receipts. `agents/observation.py` grants constrain reads; `agents/tools.py` validates tools against the pinned registry. No node receives a mutable world, wall-time simulation clock, browser canvas or unrestricted plugin client.

Journal node starts/finishes, observation/read cuts and evidence versions, public instructions/prompts, model/revision/settings, actual replies/usage/latency, tool validation, proposal commands/events and correlated receipts. Record public decision summaries, not hidden model reasoning. Concurrent candidate calls use stable node IDs; reductions sort by identity, not nondeterministic completion order. The join requires declared successful results or explicit failed/deadline results, not an implicit empty/default bid.

Two latency profiles are explicit. `offline_blocking` reuses the current synchronous decision partition: simulation stays at the scheduled instant while the call runs within a total wall deadline. `online_ingress` runs calls outside the coordinator against pinned observations; replies return on named K4 decision streams at recorded availability with deadlines/source-progress assertions. It requires new integration; the current decision engine is not already asynchronous. Online acceptance revalidates current task/resource guards and journals stale-result rejection. Neither profile uses a second motion thread to advance state. Changing profiles changes simulated latency and must be visible in scenario/report.

Every graph invocation has wall/model/token/tool-round budgets plus an explicit simulated deadline for online replies. Late replies cannot publish after the invocation closes. Invalid/failed responses are recorded and route to authored failure/recovery chains; they are not valid refusals, zero estimates or winner selections. Tools yield typed commands/events, followed by actual owner receipts. Rule mode and recorded/stub decision mode are **explicit selected alternatives**, never automatic fallbacks for unavailable live LLMs. WAL replay makes zero LangGraph/provider calls; fixture reexecution validates fixture IDs/input expectations and fails on a missing fixture.

For the accident demo, retain reporter report/route explanation, Alpha/Bravo bid recommendations and optional edge award explanation as model decisions. Configure detection, region membership, immutable medical-task interruption policy, feasibility, broadcast, bid join/deadline, unique eligible award, physical receipt/arrival/dwell, capture correlation, upload acceptance and completion as rules. A compatibility profile may retain all four original model calls, but constraints are enforced on typed outputs and committed state. The recommended profile chooses the lowest feasible ETA with stable ID tie-break as a rule; changing the legacy discretionary award to this policy is an explicit scenario revision.

## 8. Boundaries, assumptions and verification status

This design uses read-only source inspection, not runtime/native/frontend execution. Q6, Q7 and Q9 were read in their named worktrees on 2026-10-08 and may change before integration. Their final module exports/feed layout and Q6 supported operator set must be pinned by implementation. Q7 had no completed new-runtime graph module visible during the inspection; component paths for that integration are intentionally not asserted.

Reactive evaluation records/automatic dynamic bindings, the behaviour format/compiler, instance overlay, inject gateway, graph editing, feed additions and online LangGraph integration are new work. Current pinned sampled contexts do not provide automatic Q6 context creation for future relation tuples; the baseline predeclares their finite domain as specified above. No kernel writer transfer, native rollback, checkpoint resume, arbitrary predicate dialect or weather-native coupling is assumed.

Two concurrent workbuddy-dsh writer invocations with separate runtime/demo draft ownership were launched using `workbuddy/glm-5.3-flash`, `maxTokens: 131072`, no effort setting, and project/source context in their prompts. Both initial attempts and both retries exited 1 with `TRANSPORT` at `http://127.0.0.1:8788/v1`, before a model response or draft; configurations/stdout/stderr are under `/tmp/aas-q/d1/`. No completed GLM session/result is claimed. D1's documents were completed by direct source review. Only the two requested Markdown files are changed; Python lint/type/test suites and frontend gates are not applicable to this design-only diff and are not claimed as passed.
