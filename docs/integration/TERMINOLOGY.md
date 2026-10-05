# P02 integration terminology contract

Status: review draft, 2026-10-05. Applies to the P02 / Atlas / BENCH integration
in this repository. It defines the vocabulary for subsequent implementation;
it does not assert that the integration is already implemented.

## Fixed vocabulary

Use **module / 模块** for a software or domain component. Do not switch between
“module” and “model” when referring to the same component. Reserve “model” for
a specifically named mathematical approximation, learned predictor, geometry
asset, or an existing code identifier. An energy module may contain a declared
energy equation; the equation and the module are different things.

| Term | Chinese | Definition and boundary |
| --- | --- | --- |
| Entity | 实体 | An identified thing with a declared type, lifecycle and capabilities: UAV, person, parcel, facility, area, directed link or compute resource. Identity is not its display name or array position. |
| Agent | 代理 | An independent decision-layer participant outside an entity. It holds goals/policy/decision state, accepts or rejects incoming requests, and invokes modules through declared relations and authority. It may relate to one or several entities; no one-agent-per-entity rule is implied. |
| Strategy | 策略 | A decision procedure invoked by an agent: permitted observable facts, requests, objectives and constraints enter; a decision leaves. It does not itself turn a requested outcome into a physical fact. |
| Objective | 目标 | A desired outcome and its evaluation conditions, such as destination, deadline or quality. A desired value is separate from an accepted command and the actual outcome. |
| Task | 任务 | A scoped unit of requested work with identity/attempt, participants, objective, lifecycle and completion evidence. It can contain several activities or stages. |
| Constraint | 约束 | A declarative restriction or requirement with applicability, version and evidence. Physical, resource and regulatory constraints are defined in the graph and enforced by the relevant execution module; a declaration alone does not enforce them. |
| Regulatory source | 法规依据 | The source record for a regulatory requirement, with issuer, jurisdiction, applicable entities/activities, effective interval, clause/source reference and version. It is not an assumed universal rule or engineering threshold. |
| State / state specification | 状态 / 状态规格 | A declared attribute or relation field, including type, quantity meaning, unit/frame when applicable, applicability and ownership. In this contract, the shorthand “状态” means this field/specification, not its runtime value. |
| Fact | 事实记录 | A runtime record assigning a value to an entity's declared state field, with identity, validity, availability and source. It is the record of the state value, not a second independently simulated physical value. |
| Module | 模块 | A reusable software/domain component that produces or updates facts, derives named facts from other facts, or executes authorized commands. Its declared responsibility is not determined by an activity's name. |
| Provider | 提供方接口 | BENCH's existing executable integration boundary for a module/backend. Keep the identifier `Provider` where it is part of the code contract. A module category need not correspond one-to-one with a Provider process. |
| Rule | 规则 | A versioned, fixed expression over facts, parameters, references and explicit history. It specifies how a result is computed. |
| Predicate | 谓词 | A named proposition evaluated by its fixed rule over bound facts. Its truth is `True`, `False` or `Unknown`. It neither changes facts nor dispatches commands. |
| Event definition | 事件定义 | A versioned definition of a change, occurrence or script condition, including its trigger and temporal meaning. A true state predicate alone does not specify an occurrence policy. |
| Event record | 事件记录 | A recorded occurrence under an event definition, with identity, participating entities, occurrence time, evidence availability and supporting facts/rule revision. |
| Behavior | 行为 | A reusable execution unit or process, such as navigation, flight, landing or photography, with required capabilities, resources, modules and constraints. A behavior definition is separate from its execution instance and observed effect. |
| Command | 指令 | An execution request with target, arguments and lifecycle receipts. It may request one behavior or compose several behaviors through explicit sequence/parallel edges. Accepted, applied and physically completed are different states. |
| Activity | 活动 | A task-level composition of goals, behaviors, facts, predicates and events, such as delivery or inspection. Activities reuse modules and state specifications. |
| Observation | 观测 | Source evidence made available through an authorized observation boundary. Measured, simulated, declared and estimated values must retain their different provenance. |
| Evidence | 证据 | A traceable source record supporting a fact, decision, receipt or rule result within its stated proof scope. A reference-shaped string alone does not verify that scope. |
| Relation | 关系 | A declared, typed connection between exact entity/agent/resource references with roles, direction, validity and authority where applicable. Contact, custody and control are distinct relation types. |
| Capability | 能力 | A scoped, versioned declaration of what an entity or module can support. It is applicability information, not permission, availability or successful execution. |
| Resource | 资源 | An identified capacity-bearing facility, device, slot, compute/network allocation or other consumable/reservable supply. Capacity, reservations, observed occupancy and available quantity are distinct; capacity alone proves neither availability nor permission to use it. |
| Authority / permission | 权限 / 管控权限 | An explicit right and control scope to issue a command or update an authorized record, tied to the actor, target, operation and applicable validity/conditions. It does not create resource capacity or prove physical feasibility. No priority or grant policy is inferred. |
| Computing authority | 计算权属 | The selected authoritative producer of a particular state field in an exact run/entity-generation/validity scope. This ownership designation is separate from an agent's command permission and a resource's capacity. |
| Presentation | 展示 | A read-only projection of permitted facts, rule results and event records. Rendering or animation does not create authoritative facts. |

The semantic spine is **entity → state → predicate → event**. Fact records
instantiate state specifications during a run. Rules explain the predicate or
event computation. Modules supply the facts. Commands request behaviors through a
separate control chain. These are relationships, not interchangeable labels.

The semantic spine is only one path through the **complete typed graph**. All
listed concepts have declared node/class types, instances where applicable, and
typed relationships. Quantities, units and time can be properties with declared
types; they need not become extra ontology layers. Separate definition nodes from
runtime records. Preserve existing API names such as MAVSDK `Action`; that proper
interface name does not redefine the graph's behavior or command concepts.

Use **事实值** for the runtime value inside a fact record. Thus “battery remaining
fraction” is a state field, `0.42` is one runtime fact value, and its source/time/
identity envelope makes it a usable fact record. No second physical value is
introduced by this terminology split.

An entity does not contain its agent as an implicit identity attribute. Declare
agent-to-entity relations and control scope explicitly. Multiple agents may relate
to the same entity only with explicit scope and arbitration; this does not grant
unrestricted writes. The particular arbitration policy remains to be specified.
A charging-station agent can decide queue/admission/power requests. A parcel agent
can manage transport goals, deadlines and rerouting requests; vehicle/physical
modules still perform transport. Neither example implies that such agents are
already implemented or that every entity requires one.

## Identity and fact envelope

For the PR10 binding boundary, preserve the exact structured `Ref`:
`{run_id, epoch, id, generation, ref_type}`. A dotted identifier is opaque;
integer generation `1` and string generation `"1"` are distinct. Relations have
explicit, role-labelled endpoint references, including direction where relevant.
Capabilities describe applicability; they do not grant action permission.

A fact's conceptual envelope includes:

- Entity or relation Ref, state-specification ID and schema revision
- Typed value and explicit availability/quality status
- Quantity semantics, unit and coordinate frame where applicable
- Sample/occurrence time and declared validity interval
- Availability time in a named evidence domain and availability commit cutoff
- Computing authority, source record pointer, revision and provenance
- Supersession or correction relationship when one is explicitly declared

This is a semantic contract, not a claim that current BENCH `StateSample` already
exports all these fields. The source mapper must document each source field and
each missing field. Never fabricate epoch, generation, revision or availability
metadata from a run digest, display name or replay cursor.

Valid time answers “when does this value apply?” Availability answers “when was
this evidence usable by this consumer?” Keep both. Do not compare distinct clock
domains without a declared mapping. No automatic interpolation, indefinite hold,
future-evidence leakage or last-arrival-wins conflict resolution is permitted.

## Truth and missing information

`Unknown` is a truth result, not `False`, zero, an empty relation, or permission to
act. Atlas's existing three-valued semantics remain unchanged. A decisive
conjunction or disjunction may still decide with an unavailable leaf under the
native semantics; the adapter must not replace the evaluator with a new rule.

Binding errors, unsupported fields, stale data, conflicting authority, wrong
generation and missing evidence are separate diagnostic statuses. Keep those
reasons beside truth rather than inventing additional truth values. Positive
relation existence can use a valid row; absence requires complete, current,
exact-scope coverage. Physical contact does not prove responsible custody.

## One field, one computing authority

For an exact run, entity generation, state field and validity scope, select one
computing authority. Record other inputs as dependencies or distinctly named
observations. Never let two modules independently overwrite “the same” state.
An authority change is an explicit versioned handover, not a timestamp race.

The following fields remain distinct:

| Field concept | Owner role | Meaning |
| --- | --- | --- |
| `mission.goal_target` | Declared agent decision scope | Desired mission destination or objective |
| `flight.accepted_target` | Flight-command module | Target actually accepted for execution, tied to a command receipt |
| `motion.actual_pose` | Declared physical-motion authority | Resulting pose from the declared simulation/observation source |
| `parcel.responsible_custodian` | Custody authority | Accountable holder under a versioned custody transaction |
| `parcel.physical_attachment` | Physical module | Actual attachment constraint and its physical evidence |
| `energy.estimated_remaining_wh` | Declared energy module | Computed energy estimate under explicit assumptions |
| `battery.observed_remaining_fraction` | Battery-observation authority | Reported remaining fraction; not measured Wh or a full energy account |

These are proposed canonical semantic names, not claims that these exact API
keys currently exist. An adapter maps source names explicitly.

Agent relations should declare the agent Ref, target entity Ref(s), goal/policy
reference, authorized command/control scope, relation validity and authority
reference. Where scopes overlap, an explicit arbitration reference is required.
These are relationship requirements, not an invented authorization or priority
policy. Agent decisions and command receipts must remain separate from the
entity's resulting physical facts.

## Strategy, constraint and source interfaces

The conceptual strategy boundary is
`strategy(observable_facts, requests, objectives, applicable_constraints) → decision`.
The agent invokes it and records the chosen strategy revision and input context.
This boundary does not prescribe an algorithm or silently grant hidden truth,
new control scope or authority to a strategy.

A constraint definition records its scope and requirement. Its execution binding
names the module responsible for enforcement and the evidence that enforcement
or effect checking will produce. If that binding is missing, report the constraint
as declared but not enforced. Do not silently invent priority/arbitration rules.

Keep three assessment results separate: physical feasibility, permission/
compliance under the selected applicable constraints, and task suitability.
Constraint applicability (`applicable`, `not_applicable`, `applicability_unknown`)
is metadata, separate from predicate truth. No engineering example is legal
advice or proof of approval. Real regulatory content requires a verified source,
applicability and effective version; this document supplies no legal thresholds.

## Scope language

Report four independent evidence dimensions: **declared**, **implemented**,
**tested**, and **real-host connected**. Name the commit, test scope, run and
artifact for each claim. A schema field is only declared; source code establishes
implementation, not a passed test. A fixture test is not a live-host test. A past
real run is not evidence that a current host is connected or running.

The 30+ activity and 1000+ predicate scale is a design requirement. It is not a
coverage result. Count catalog definitions, bindings, source-backed fields,
executable rule tests and live integrations separately. The eight module
categories in [ARCHITECTURE.md](ARCHITECTURE.md) organize work; they are not a
proof of completeness or eight compulsory exclusive state buckets.

## Change discipline

Use this vocabulary in new integration documents, schemas and UI labels. Keep
existing source identifiers unchanged until an explicit migration is approved.
Any semantic change needs a revised definition and mapping, not a synonym swap.
Conflicting old documents must be marked as historical or explicitly amended;
do not silently reinterpret their implementation claims.

See [the architecture](ARCHITECTURE.md), [source inventory](MODULE_STATE_INVENTORY.md)
and [mapping audit](MAPPING_AUDIT.md) for application and verification boundaries.
The immediate author/review implementation is described in
[the configuration workbench plan](CONFIGURATION_UI_PLAN.md).
