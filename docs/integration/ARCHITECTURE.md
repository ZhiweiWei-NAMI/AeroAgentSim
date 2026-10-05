# P02 shared-state integration architecture

Status: review draft, 2026-10-05. This is the design contract for subsequent
integration into the existing AeroAgentSim repository. No simulator, server,
paid inference call or physical command was started for this documentation task.

## Design decision

Represent entity state once, produce facts through reusable domain modules, and
evaluate versioned predicates/events against those shared facts. Activities
compose the same module capabilities. Do not enumerate every pair of activities
as a bespoke action-compatibility table.

The graph includes all concepts as typed classes/definitions, instances and
typed edges: entities, fields/facts, agents, strategies, modules, capabilities,
resources, behaviors, commands, rules, predicates, events, constraints and their
sources. The entity–state–predicate–event spine is one inspection path, not the
entire graph. Units, quantities and times may remain typed properties.

For example, delivery affects payload attachment/mass, flight motion, link load,
compute load and energy. Charging additionally depends on facility availability,
contact/connection and charging capability. These effects meet through explicit
state dependencies and execution constraints. A new activity should reuse those
dependencies rather than require a new comparison against every old activity.

Use [TERMINOLOGY.md](TERMINOLOGY.md) for all terms below.

## Five data responsibilities

These are data responsibilities, not a count of Providers, processes, simulator
stages, control decisions or module categories.

1. **Graph definitions:** entity/state specifications, capability applicability,
   units/frames, rule expressions, dependency edges, event definitions and
   activity/behavior composition and typed concept relationships. Version once;
   keep runtime values outside definitions.
2. **Module execution:** declared backends, adapters, commands, scheduling,
   authoritative computations and explicit module couplings.
3. **Runtime records:** identity/lifecycle registry, immutable facts, relations,
   command receipts, availability frontiers, revisions and source evidence.
4. **Rules and events:** bind concrete entities to fixed rules, evaluate the
   existing Atlas semantics with explicit history, retain truth and evidence,
   record occurrences under explicit event policies.
5. **Presentation:** query permitted runtime records and results, show their
   meaning, lineage and missing-data reasons, and follow the host's clock.

Data moves from definitions and execution to runtime facts, then to rule/event
results and presentation. The dependency graph can display this lineage; it is
not an additional physics engine. Replaying a result must never execute an action.

## Separate control chain

The main closed loop is **observable facts + goals + applicable constraints →
agent / strategy → decision → command / behavior composition → pre-execution
constraint admission → module execution → facts / predicates / events →
agent feedback**. This control chain is distinct from the data-responsibility
count, but all its concepts and relationships are represented in the graph.

- Business specifies the order, goal, priority and acceptable completion.
- The agent is outside the entity and relates to one or more entities through
  declared authority and scope. It accepts or rejects incoming requests, manages
  goals/policy and chooses candidate behavior from permitted observations. An LLM
  is an optional decision implementation; its asserted physical outcomes are not
  authoritative entity facts.
- The agent invokes a versioned strategy with observable facts, requests,
  objectives and applicable constraints as inputs and a decision as output.
  Strategy choice does not grant authority or turn a decision into an effect.
- Execution constraints check applicable capabilities, phase, authority,
  resources, lifecycle and required evidence. `Unknown` is handled by an explicit
  policy; it is not automatic admission.
- A command records intent and lifecycle receipts. An accepted command does not
  prove arrival, charging, delivery or a completed physical handover.
- A command can compose reusable behaviors through explicit sequence/parallel
  edges. Each behavior declares required capabilities, resources, module calls
  and constraints. For example, a return command may compose navigation, flight
  and landing; the composition is not proof that those effects occurred.
- Domain execution updates its own authoritative fields. Effect confirmation
  reads independent evidence appropriate to the requested outcome.

Constraints are checked both before starting a behavior and while it executes.
The relevant module consumes current applicable constraints and facts, records
the check result, and applies the declared continuation/pause/abort/replan policy.
The graph must identify this enforcement responsibility; providing constraints
to the strategy alone is insufficient. Hard constraints and soft objectives are
distinct: a preference score does not override a hard constraint. The actual
policy for failed, unknown or conflicting checks must be declared; this document
does not invent a universal safety action or arbitration rule.

Feedback retains the evidence and decision context so an agent can reconsider
the next request. An event can inform an agent without automatically becoming
a command, and a retrospective replay cannot re-execute an earlier command.

Do not assume one agent per autonomous entity. Several agents may relate to one
entity only with explicit scope and arbitration. A station agent can decide
queue/admission/power requests; a parcel agent can manage deadline or rerouting
requests while transport remains the vehicle modules' work. This contract does
not choose an unapproved arbitration algorithm or grant those agents access.

The current BENCH Gateway dispatches to the PX4 provider service, whose
`RealPx4Stack` uses the MAVSDK **Action** interface for flight commands. Do not
call this path Offboard. An Offboard controller would be a separate future
integration with its own requirements and evidence.

Constraints have declarative definitions in the graph and enforcement bindings
to the relevant execution modules. Keep physical, resource and regulatory
constraints identifiable. A regulatory source carries its issuer, jurisdiction,
applicable entities/activities, effective interval and source version. No legal
threshold or authority-priority policy is supplied by this document. Preserve
the original design's separate physical-feasibility, permission/compliance and
task-suitability assessments; a single `allowed` flag must not erase their basis.

## Eight organizing categories

These categories guide decomposition and ownership review. They are neither
closed coverage nor a requirement to put all dependencies of a field in one box.

| Category | Module responsibilities | Candidate or inspected boundary |
| --- | --- | --- |
| Flight control | Accepted flight targets, controller mode and command lifecycle | Existing PX4 SITL / MAVSDK Action integration |
| Physics, sensors and payload | Actual motion, forces, contact, sensor evidence; payload mass/inertia/attachment effects | Existing Gazebo/PX4 integration; explicit payload coupling still needs field-level audit |
| Traffic and people | Vehicle/person mobility and traffic interactions | Existing SUMO/TraCI integration; do not assume every human behavior is supplied |
| Network | Directed link/flow observations, packet delivery, queues and communication constraints | Existing ns-3 integration; aggregate receipts are not individual link facts |
| Cloud-edge compute | Workload placement, queueing, processing latency and resource occupancy | SimGrid or another explicit backend is a candidate; no inspected live integration is claimed |
| Energy | Energy accounting, power contributors, battery/charging state and energy estimates | PX4 remaining-fraction observation plus existing declared-parameter logistics energy calculation; broader module coupling is planned |
| Environment, facilities and regulation | World geometry, weather inputs, facilities, occupation and applicable restrictions | Existing world-scene, facility, airspace and scenario contracts; coverage varies per field |
| Business and agent decision | Orders, assignments, goals, custody/accountability and independent agent decisions | Existing business Providers and participant boundaries; fixture custody is not live authority |

Reuse mature backends where they meet the required semantics and evidence
contract. The named candidates are not a promise that every Atlas state is
provided. Decide each missing field from its physical/business meaning and
source evidence rather than from a category label.

## Ownership and coupling

Every state entry needs one computing authority, explicit readers, dependencies,
update cadence/stage, unit/frame, temporal validity and failure behavior.
Different estimates or observations of one phenomenon use distinct state fields.

- Gazebo's declared physics boundary owns payload mass/inertia and attachment
  effects on physical motion. A logistics order may request a load but must not
  directly overwrite actual UAV pose or fabricate an attachment receipt.
- The energy module reads authoritative motion, payload, network and compute
  contributors. A configured integration must define which contributor supplies
  power and how double counting is prevented. It must not silently overwrite
  PX4 battery observations with a calculated Wh estimate.
- Network propagation reads the appropriate physical positions and environment
  state. It owns its packet/link calculations, not the positions.
- Facility admission reads physical presence and resource occupancy. Booking a
  resource does not erase observed occupancy or prove charging connection.
- A business custody transaction reads authorized handover evidence; proximity
  and physical contact alone do not transfer responsibility.

For cyclic dependencies, declare a scheduler policy: previous closed tick,
explicit substeps, or a bounded co-simulation solve with convergence/failure
rules. Do not rely on Python call order or let several modules write the same
field. Record which closed inputs produced each output.

## Existing BENCH execution boundary

The exported BENCH formal path remains:

`strict specifications → immutable ResolvedRun → preflight → executor →
Provider barrier / Gateway → sealed artifacts → independent verifier → public
projection → read-only viewer`.

The existing runtime stage order is `motion → network → business_environment`
(`aero-bench/aero_bench/providers/stages.py`). Three stage names do not imply
three semantic categories, five data layers or eight processes. Retain this
executable contract until an intentional scheduler extension is implemented.

`SceneStateAssembler` joins declared entity ownership and motion contributions.
The source mapper must preserve scenario/run identity and stage/scene evidence.
Current `StateSample` is not a universal Atlas fact envelope. A production mapper
still needs explicit lifecycle, generation, field signatures, availability and
authority handling. See the [source inventory](MODULE_STATE_INVENTORY.md).

The PR10 prototype is a useful read-only binding contract: immutable `Snapshot`,
typed `StateCell`, exact `Ref`, explicit field projection and caller-supplied
native Atlas evaluator. It is on a separate unmerged PR and is not silently
copied or merged by this documentation change. Its fixture ledger must not be
wired directly to real physical execution.

## Representative compositions

All names below are conceptual examples, not an assertion that a matching
catalog ID or runtime binding already exists.

### Charging

Read UAV physical pose/contact, facility connector capability and availability,
reservation/occupancy, battery observation and declared energy limits. Evaluate
charging eligibility from fixed rules. Request charging through the responsible
module. Record connection/current/energy evidence when that source exists.
Pose proximity or `ground_charge` planning arithmetic cannot prove electricity
flow. Missing connection/current evidence remains `Unknown`. Emit a charge-start
or charge-complete event only under its declared evidence/transition policy.

### Parcel logistics

The order identifies a parcel and desired destination. Agent decision requests
pickup/transport/handover. Physics owns attachment and motion; custody authority
owns the responsible-holder relation; business owns order completion. Binding
joins these facts using exact generations and times. A dropped parcel may remain
the same party's responsibility. A delivery command receipt is not delivery proof.

### Link degradation

Read directed endpoint identities, link/flow properties and the declared window
of packet evidence. Evaluate the fixed degradation rule against its exact inputs.
An RSSI threshold does not by itself establish delivery loss; provider aggregate
throughput must not be copied onto every link. Record onset/recovery using the
event's explicit temporal policy and evidence availability. Agent decision may
then request hold/return under the execution policy.

### Return

Business/agent decision sets a desired return goal. Constraint admission and
MAVSDK receipts establish an accepted command target. PX4/Gazebo evidence supplies
actual motion. Return/arrival/landing rules read actual pose, velocity, contact
and applicable completion evidence; they do not read a goal field as actual
position. Link recovery or a command acknowledgement alone does not prove return.

## Next gate

The immediate priority is configuration authoring and review in the existing
`frontend/console-prototype`, using the same logical configuration that later
execution adapters will consume. It is not full real-backend integration first.
See [CONFIGURATION_UI_PLAN.md](CONFIGURATION_UI_PLAN.md) for schemas, validation,
export and controlled fixture execution. All modules may initially have explicit
fixture/stub bindings; those bindings must remain visibly separate from formal
BENCH execution and real verification.

Review the terminology and ownership inventory, then implement configuration
authoring/static checks and a controlled fixture path. The original Atlas source
access gap blocks full catalog recount and native parity, but does not block the
UI/schema work. Continue the audit in [MAPPING_AUDIT.md](MAPPING_AUDIT.md) when
source is available. Require genuine provider replay before live admission or
control claims. This draft does not claim full catalog mapping, physical safety,
current host connection or live P02 closure.
