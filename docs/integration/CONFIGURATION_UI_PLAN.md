# Unified configuration workbench implementation contract

Status: implementation handoff, 2026-10-05. Build in the existing
`frontend/console-prototype`; do not create a separate application or replace
the BENCH monitoring viewer. The immediate deliverable is author/review,
validation, export and controlled fixture execution. Full real integration is
not a prerequisite.

## Existing foundation to reuse

The current console already has editable/frozen configuration, local versions,
diff/import/export, bilingual labels, explicit adapter boundaries, fixture replay,
one selection/cursor context and an observation export. Reuse the existing:

- `src/config.js`: configuration, normalization and validation responsibilities
- `src/app.js`: authoring/review surfaces and shared selection
- `src/runtime.js`: controlled fixture preparation and replay boundary
- `src/observation-contract.js`: typed observation export and provenance
- `src/i18n.js`, `src/translations.js`: stable Chinese/English terminology
- `tests/config.test.js`, `runtime.test.js`, `observation-contract.test.js`,
  `i18n-ui.test.js`, `ui.test.js`: existing regression boundaries
- Parent workbench `/configuration/index.html` entry: keep this route working

Refactor these files into focused modules when warranted, preserving current
saved-config and import behavior through an explicit versioned migration. Do not
silently reinterpret old configurations as the new authority scheme.

## One logical configuration

The implementation lead's selected direction is an optional `graph` property in
the existing `aero-console.config/v1`, carrying
`schema_version: aeroagentsim.unified-graph/v1`. Legacy saved/imported documents
stay unchanged until explicit adoption. Planned implementation files are
`src/graph-config.js`, `src/graph-runtime.js`, `src/graph-workbench.js` and
`src/schemas/unified-graph-v1.schema.json`. These are implementation handoff
decisions, not verified file or test-passage claims in this docs-only change.

Use one versioned authoring document with stable references. Separate reusable
definitions, configured instances and runtime records rather than embedding agent,
strategy, module or rule copies inside every entity. The following are semantic
collections/requirements; implementation may choose precise JSON key spelling
once and then keep it consistent across UI, validation and export.

All concepts belong to one typed graph, with class/definition nodes, instances
and typed edges. The graph is not limited to state/predicate nodes. Node forms
and edge forms must expose their allowed endpoint kinds and roles. Scalar
quantities, units and time may be typed properties. Definition/instance status
and authored-fixture versus runtime-evidence provenance must remain explicit.
Use [TYPED_GRAPH_RELATIONS.md](TYPED_GRAPH_RELATIONS.md) for the proposed semantic
edge registry, roles and execution-dependency distinctions.
Use [RULE_AST_CONTRACT.md](RULE_AST_CONTRACT.md) for source-derived expression
inspection, binding, parameter/time semantics and lossless AST round trips.

| Collection | Required semantics |
| --- | --- |
| Entities | Stable typed identity; declared lifecycle and capabilities; no implicit internal agent |
| Capabilities | Typed capability definitions and scoped instance claims; requirements are distinct from authorization |
| State specifications | Field identity, quantity/type, unit/frame, entity/relation applicability and temporal/availability contract |
| Module definitions and instances | Inputs, output fields, command interface, applicable capabilities, configured parameters and backend binding |
| State-source bindings | Entity/relation field → one module output/authority; explicit conversions and input dependencies; no inferred producer from ID text |
| Agents | Independent identities, permitted observation scopes, strategy references, goals/tasks and explicit entity-control relations |
| Strategies | Versioned input/output signatures and parameter contract; observations/requests/objectives/constraints → decision |
| Goals/tasks/activities | Goal conditions, task/attempt identities, participants, stages, deadlines and completion/recovery evidence |
| Authority/control relations | Agent and entity refs, permitted command scope, validity and authority reference; an explicit arbitration reference if scopes overlap |
| Resources | Capacity dimensions, units, reservation/occupancy semantics and owning module; resource declaration is not live availability |
| Constraints and sources | Applicability, requirement, source/issuer/version; enforcement module; regulatory jurisdiction and effective interval where applicable |
| Constraint checks and results | Facts plus applicable constraints, start/continue scope, responsible enforcement module, result evidence and declared pause/abort/replan policy; hard constraints separate from soft objectives |
| Predicates/rules | Versioned target definition/reference, parameter signature and exact state/entity bindings; native evaluator requirement |
| Event definitions | Predicate/occurrence/sequence dependencies, temporal policy, participants and evidence; no implicit actuator effect |
| Behaviors | Reusable execution-unit/process definitions and instances; required capabilities, resources, modules and constraints |
| Command interfaces/routes | Target, argument/result signature, one behavior or explicit sequence/parallel behavior composition, authorization/resource dependencies, receipt/effect contract |
| Typed relationships | Class/instance, produces/reads, agent control, requires, invokes, evaluates, supports, sequence/parallel and other declared edge types with validated endpoint kinds |
| Execution profiles | Explicit backend bindings and requirements; each module marked fixture, stub or real; no hidden fallback |

Existing exact Ref and fact-envelope semantics apply at runtime. Authoring can
declare hypothetical instance identities, units and frames explicitly; never
present those declarations as recovered live-source metadata.

## Review interactions

Keep the entire closed loop visible: observable facts/goals/applicable
constraints → agent/strategy → decision → command/behavior composition →
pre-execution admission → module execution → facts/predicates/events → feedback.
Constraint input to a strategy must not be mistaken for module enforcement.

Keep one editor selection and one inspectable dependency graph. Provide two
linked review paths rather than another unexplained layer count:

1. **Entity → state field → source module → predicate → event.** Selecting an
   entity reveals its declared fields, current binding status, authority, units,
   time semantics, downstream rules and event dependencies. Clicking a field
   selects its source binding; unbound fields remain visibly unbound.
2. **Task → agent → strategy → authority/resources/constraints → command.**
   Selecting a task reveals its decision owner(s), strategy signature, controlled
   entity scope, required resources/constraints and execution route. Expand a
   command to its behavior sequence/parallel composition and each behavior's
   required capabilities/modules. Clicking an event or command does not execute
   anything.

Support create/edit/remove/reference selection, clear validation diagnostics,
save/version/diff/import/export, and a review summary. Reference removal should
expose broken dependents rather than silently delete them. Show original six-axis
coverage tags and eight module categories as different optional organization
views; neither is a substitute for the dependency graph.

## Static validation

Return location-addressable diagnostics with code, severity, object/field refs,
reason and suggested correction. At minimum check:

1. Unique IDs and resolvable references; entity/relation endpoint type and direction
2. Declared state fields bound to module outputs, with exactly one computing
   authority per scope; missing or conflicting bindings remain explicit
3. Module and strategy input/output signatures; required parameters; strategy
   observations cannot exceed its agent's declared visibility
4. Units, quantity semantics, coordinate frame and clock/validity/availability
   compatibility; conversions must be explicit
5. Agent control scope versus command target/route; overlapping control scopes
   require explicit arbitration metadata, without inventing its policy
6. Resource quantities/dimensions, known capacity constraints and reservation
   intervals; static checks cannot prove runtime occupancy is absent
7. Constraint applicability metadata, source version and enforcement binding;
   no legal truth inferred from an engineering threshold
8. Predicate dependency signatures and event temporal definitions; rule results
   cannot be routed directly as physical facts or command completion evidence
9. Module dependency cycles require an explicit scheduler coupling policy;
   they are not silently made acyclic by dropping dependencies
10. Backend capability requirements and mode labels; a stub cannot satisfy a
    real-source evidence requirement
11. Typed edge endpoint compatibility, definition/instance references, behavior
    composition order/parallel structure and each behavior's capability/module/
    resource/constraint requirements; malformed cycles are explicit errors
12. Pre-start and in-execution constraint checks, selected enforcement modules,
    declared failure/unknown/conflict policies, and hard-constraint versus
    soft-objective classification; absence is diagnosed, not filled with a
    guessed pause/abort/replan policy

Keep separate readiness results: **authoring validity**, **fixture executability**,
**native-rule verification**, and **real-backend readiness**. A valid authoring
document can contain unresolved real-source requirements; it must not display
them as satisfied. A runtime-evidence condition returns unknown/unverified when
static data cannot establish it.

## Controlled fixture execution

The fixture profile may instantiate all modules as explicit fixtures or stubs.
Freeze the selected configuration and retain a digest/revision before execution.
Emit facts, decisions, command-lifecycle records and events only within each
fixture's declared semantics, with `fixture` provenance and synthetic identity/
time. Stub outputs remain unavailable. Preserve one host-owned replay cursor.

Do not implement a substitute Atlas engine or pretend authored predicate values
were evaluated natively. A rule with no verified evaluator binding remains
`Unknown` / not executed; boundary validation can still run. If controlled test
rules are used for execution tests, label their distinct fixture definition IDs
and evaluator explicitly. They are not catalog coverage or native parity.

An explicit fixture decision/command exercise may validate request routing and
scope rejection, as well as declared start/continue constraint admission and
feedback branches. It cannot start BENCH, connect a real provider, invoke paid
inference, modify actual custody or claim physical success. This is an authoring
test profile, not a fallback Provider inside formal BENCH execution.

## Export and later execution

Export the same logical configuration with its schema version and digest,
resolved references, required module capabilities, selected execution profile,
unresolved source/evaluator requirements and validation report. No credentials
or inferred server endpoint belongs in this export.

Future real backends bind to the same module interfaces and logical graph.
Selecting or editing a backend profile creates a visible configuration revision
and revalidates capabilities, authority and evidence requirements. It does not
silently mutate a fixture into a real run. The existing BENCH resolver/preflight
and sealed-evidence requirements remain mandatory for formal execution.

## Acceptance tests for the implementation

- Both languages show the fixed terms and preserve technical identifiers
- Entity and task review paths resolve to the same selected objects/references
- Broken references, wrong units/types/frames, unbound fields, duplicate writers,
  incompatible strategy I/O and control-scope overlap produce precise diagnostics
- Fixture/stub mode is visible in authoring, run review, replay and export
- No event/graph click sends a command; no fixture run reaches a real backend
- Save → export → import preserves the canonical logical configuration; invalid
  or old versions are rejected or explicitly migrated, never silently rewritten
- Editing after a frozen fixture run leaves that run's config/evidence unchanged
- Static-valid and fixture-ready never imply native Atlas parity or real readiness
- Hard-constraint violations are never hidden by a soft-objective score; the
  fixture exercises declared continuation/pause/abort/replan feedback policies
- Existing replay gap, generation/context, shared-selection and language-switch
  regressions continue to pass
- Check desktop/mobile layout in an available browser; distinguish DOM tests
  from an actual visual pass and report any verified environment block

The implementation lead owns exact schema code, migration details and UI layout
within these contracts. This document does not claim the listed work is already
implemented or tested.
