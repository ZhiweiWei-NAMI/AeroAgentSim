# Typed graph relation contract

For full expression structure, the definition/runtime distinction and lossless
validation/export requirements, see [RULE_AST_CONTRACT.md](RULE_AST_CONTRACT.md).

Status: proposed schema contract, 2026-10-05. This defines the full concept graph
for authoring/review. It does not assert every edge or execution function is
implemented. The implementation registry must declare and validate each edge's
source kind, target kind, direction, role, definition/instance level and required
properties. Do not store every relationship as an untyped `depends_on` edge.

## Nodes and instances

Provide typed definitions/classes and applicable instances for entity, state
specification, fact, agent, strategy, objective, task, module, capability,
resource, behavior, command, rule, predicate, event, constraint, regulatory source
and legal clause. Decision, receipt, constraint-check and evaluation-result
records have explicit kinds and provenance. An occurrence is distinct from an
event definition; a behavior execution is distinct from its reusable definition.

An `instance_of` edge points from an instance to the matching definition/class.
Some records, such as an observed fact or a command receipt, arise only at runtime
unless explicitly declared as fixture input. Scalar values, quantities, units,
clock-domain references and intervals can be typed properties; they do not need
to become independent nodes just to increase the graph's layer count.

## Core semantic edge registry

The names below are the proposed fixed semantic edge IDs. Precise storage layout
is implementation-owned, but any changed spelling requires one explicit mapping
shared by editor, validator, runtime and export.

| Edge | Allowed source → target | Required meaning / role |
| --- | --- | --- |
| `instance_of` | Typed instance → corresponding definition/class | Instance/definition kinds must match; no cross-kind cast |
| `owns_state` | Entity instance/type → state specification | Entity is the field's subject/applicability owner; this does not give it computing or command authority |
| `instantiates_state` | Fact → state specification | Fact supplies a typed runtime value; exact subject Ref and source/time metadata remain required |
| `calls_strategy` | Agent → strategy | Versioned strategy invocation/selection under the agent's declared observation and authority scope |
| `input_to` | Fact/objective/constraint/request → strategy | Role required: observation, objective, applicable_constraint or request; definition-level edges specify signatures, instance-level edges reference actual inputs |
| `output_must_satisfy` | Strategy → constraint | Requirement on strategy output; distinct from receiving the constraint as an input and from module enforcement |
| `produces_decision` | Strategy invocation → decision | Exact input context and strategy revision; decision is not a physical effect |
| `generates_command` | Decision → command | Declared target and authorized control scope; no assumed permission from a decision alone |
| `composes_behavior` | Command definition/instance → behavior definition/instance | Explicit group/mode: ordered, parallel or conditional; order/condition references when applicable; no definition/instance level mixing |
| `executed_by` | Behavior → module | Module capability and command interface must satisfy the behavior's requirement |
| `requires_capability` | Behavior → capability | Capability requirement, distinct from an entity's scoped capability claim |
| `requires_resource` | Behavior → resource | Capacity dimension, amount/unit and reservation/use interval; this edge does not prove availability |
| `constrained_by` | Behavior → constraint | Applicability and enforcement binding are required; declaration alone is insufficient |
| `reads_fact` | Strategy invocation / rule evaluation / predicate evaluation / constraint check / module execution → fact | Exact scope and role, including visibility and time cutoffs; no hidden-truth access |
| `uses_constraint` | Constraint check → constraint | Version, applicability and scope: pre-start or in-execution continuation |
| `produces_check_result` | Constraint check → check result | Inputs/evidence, verdict and declared unknown/conflict handling |
| `permits_or_blocks` | Check result → behavior execution | Decision applies to start or continue; preserve permitted/blocked/unresolved status and declared enforcement policy |
| `updates_state` | Module → state specification | Selected computing authority; execution realizes an update by producing a fact for that field, not by rewriting the field definition |
| `produces_fact` | Module execution → fact | Source record/revision, subject identity, valid time and availability; fixture/real provenance is explicit |
| `evaluated_by` | Predicate → rule | Fixed native semantics and revision; facts are inputs through explicit bindings |
| `evidenced_by` | Event occurrence → fact / rule result / check result | Proof scope and occurrence policy; event definitions instead declare dependency signatures |
| `feeds_back` | Fact / result / event occurrence → agent | Permitted observation and feedback routing; no automatic command dispatch |
| `based_on` | Constraint → legal clause / regulatory source | Source identity/version and jurisdiction/effective scope; a source link alone proves no current applicability |
| `waits_for` | Behavior execution → predecessor behavior execution | Genuine execution dependency with the required completion condition; distinguish successful completion from merely terminal status |
| `controls` | Agent → entity | Explicit authority reference, operation scope and validity; overlapping scope requires declared arbitration |

The registry may add domain-specific edges with declared signatures. Do not
infer new authority, execution ordering or causal relationships from display
positions, node colors, object names or generic arrows.

## Multi-edge identity and metadata

This is a directed typed **multigraph**. The same pair of nodes may have several
distinct relationships, including receiving a constraint and requiring output
to satisfy it. Deduplicate only identical edge IDs under the declared revision,
never the source/target pair. Edges carry a stable ID, relation type, source and
target refs, role, definition/instance level, condition, validity/effective time,
scope, version and source/evidence reference as applicable. Do not use absent
conditions or scope as implicit universal permission.

`input_to`, `output_must_satisfy`, `constrained_by` and a check result's
`permits_or_blocks` are separate relations even when they concern the same
constraint. A behavior's `waits_for` relation is actual execution ordering, not
an undifferentiated graph dependency.

## Definition and evaluation matrix, grounded in available sources

Evidence keys:

- **P7:** original project plan, version 2026-10-04, §7.1–7.3. Its documented
  native DSL uses `state`, `const`, `param`, `op`, `args`, predicate/event
  references and `window_s`. This is inspected design evidence, not a fresh
  extraction from the unavailable catalog or native engine implementation.
- **S:** `src/aeroagentsim/integration/semantics.py` at `e8ea3ab3`: `SemanticBinding`
  (lines 30–59), `EvaluationRequest` (72–79), projection/`field_map` (155),
  `SemanticSession` (175–274).
- **A:** [PR10 adapter.py at 54c5cef](https://github.com/ZhiweiWei-NAMI/AeroAgentSim/blob/54c5cef4673f87dc738790ed3f026651f04e5ee7/validation/predicate-binding-prototype/adapter.py):
  compiled field map (244–250), required-field check (303–305), explicit history
  validation and native invocation (313–327), explicit catalog loading (341–373).
- **C:** the PR10 `contracts.py`/README at the same commit: typed state selectors,
  role-labelled relation endpoints, generation-safe Ref, scoped coverage and
  valid/available time. These are adapter contracts, not proof of native relation
  AST node support.
- **N:** newly fixed integration semantics in this documentation. It is a schema
  proposal pending implementation, not an existing native-engine claim.
- **H:** the earlier source audit of native `runtime/engine.py`, reported during
  this task. It inspected `_compile`, requirements and dependencies before those
  source bytes became unavailable. Its findings below are historical inspected-
  source evidence, not a fresh local reread or an audit of `export_graph.py`.

H confirms the AST leaf/reference/operator keys in P7. A target has a `rule`
expression; the root can be an operator, state leaf, target reference or constant.
The compiler also accepts non-dict literals, `op: literal` with `value`, and
`op: unknown`. Temporal/control inputs include `window_s` (legacy alias
`duration_seconds`), `max_gap_s`, `scope`, `enter_s` and `clear_s`.
`applicability_rule` is a separate expression whose result is `scope_status`, not
the main rule truth. The execution expression structure therefore retains leaves,
references and nested operator arguments all the way to each target's rule root.
The semantic edge IDs in this document are proposed integration IDs, not claims
about an original exporter edge-type enum that has not been inspected.

| Typed edge / direction | Required properties | Validator requirement | Runtime / compile interpretation | Evidence / limit |
| --- | --- | --- | --- | --- |
| Predicate definition → state definition, `depends_on` with role `state` | Native target/field IDs, definition revision, direct/reference path | Field exists with expected type/quantity; preserve exact native IDs | Static dependency extracted from rule's state leaves; never confused with an observed value | P7; A checks required field binding, but full native AST extraction remains unverified |
| Predicate definition → relation definition, `depends_on` with role `relation` | Relation schema, ordered roles/direction, selector and revision | Exact endpoint signatures; absence requires matching coverage contract | Static relation requirement resolved through explicit typed selectors | C/N; do not pretend the current native DSL has a relation AST node unless source confirms it |
| Predicate/event definition → rule definition, `uses_rule` | Rule identity/revision, target kind and role `main` or `applicability` | Exactly declared target expression and compatible result signature | Main rule truth and applicability `scope_status` are evaluated separately; a root need not be an operator | P7/H/N; native catalog IDs must be imported, not guessed |
| Rule expression → operator expression, `has_operator` | Native operator ID/signature, definition revision | Operator exists in pinned registry; typed arity/arguments | Calls the existing native operator, not a new substitute evaluator | P7; complete operator inventory unavailable |
| Operator expression → child expression, `has_argument` | Ordered argument index/name and expected type | Unique argument slots and native arity/type rules | Retains actual AST argument structure/order | P7; compound expressions cannot be flattened into unrelated labels |
| Predicate/rule expression → referenced predicate/event definition, `references_target` | Referenced ID/kind/revision, lexical parameter environment, source AST path | Resolve target and its dependencies; detect illegal cycles according to native semantics | Reuses referenced rule with its declared context; does not clone it per entity | P7; S/A preserve target IDs and parameters but do not prove every native reference is expanded |
| Rule expression → parameter definition, `uses_parameter` | Native parameter ID, quantity/type/unit, explicit scope/default | Required parameter resolution; no invented threshold or silent default | Reads binding/lexical parameter value without overwriting facts | P7, S.parameters, A native `parameters` argument |
| Rule expression → constant, `uses_constant` | Literal value/type/unit and source path | Literal type and operator compatibility; support source-native literal forms | Constant from original definition, distinct from mutable state or parameter; explicit unknown is not a fabricated constant value | P7/H |
| Temporal expression → history/control requirement, `requires_history` | Native operator and source `window_s`/`duration_seconds`, `max_gap_s`, `scope`, `enter_s`, `clear_s` as applicable; boundary policy and clock domain | Explicit compatible history and source-defined control parameters; unknown when required history is absent; no arbitrary truncation | Native temporal evaluation over preserved samples; controls are not ordinary measured facts | P7/H, S, A; precise operator/control semantics require the pinned original engine revision |
| Binding → entity/relation roles, `binds_role` | Ordered role, exact Ref/endpoints, generation, lifecycle/capability scope | Exact identity and applicability; no ID-prefix inference | Chooses concrete participants for one evaluation binding | S.actor_ids and C; older S actor-ID contract is narrower than C's structured Ref |
| Binding → state field projection, `binds_field` | Native field ID → exact selector/output slot, unit/frame conversion | Every required field explicitly bound; type/authority/time/source validation | Immutable snapshot projection; missing input remains unavailable | S.state_fields/field_map; A244–250,303–305 |
| Binding → parameter value, `binds_parameter` | Parameter ID/type/value, scope and revision | Match declared signature and preserve lexical environment | Supplies native parameters separately from states | S.parameters; A326–327 |
| Evaluation → time/history context, `uses_history` | Query time, same-binding earlier samples, validity/availability cutoffs, clock mapping and evaluation revision | No future evidence, cross-generation mixing or precision collapse | Supplies `t` and `history` to the unchanged native evaluator | S72–79,175–274; A313–327 |
| Evaluation result → predicate/event definition, `result_of` | Exact target/binding/definition/evaluation revisions | Target exists; result belongs to same context | A truth/result record is separate from its reusable definition | S.SemanticEvidence; A native output; N explicit graph representation |
| Evaluation result → fact, `based_on` | Exact fact/source/revision and role; prepared input snapshot | Result lineage references the facts actually available for that evaluation | Runtime provenance; not the static predicate-to-state dependency | S/A prepared inputs; N graph edge |
| Evaluation execution → fact, `reads_fact` | Input slot, identity, time and availability context | Same binding and allowed visibility; stale/conflicting/unavailable inputs retain reasons | Reads runtime records only; does not rewrite rule definitions | S/A/C |
| Event occurrence → event definition, `instance_of` | Event definition/revision, occurrence identity, participants and time | Declared occurrence policy and exact evidence | Emits a record only when occurrence semantics are implemented; truth alone is insufficient | P7/N; root foundation explicitly emits no occurrences pending native policy |
| Event occurrence → facts/results, `evidenced_by` | Supporting records, temporal bounds and proof scope | Traceable source lineage and occurrence policy | Evidence for the recorded occurrence; cannot serve as command success by assumption | N plus existing evidence boundaries; live occurrence implementation unverified |

This matrix covers the necessary relationship families supported by the
available design and adapter evidence. It is **not a verified complete matrix of
the unavailable original catalog/AST**. Node kinds for check/evaluation results
can remain typed runtime records instead of forcing every record into a permanent
authoring node. Static definitions and runtime records must still be distinguishable.

## Source-driven extraction and unsupported cases

The implementation must derive graph edges from authoritative reference fields
and a pinned native rule AST/registry where available. Do not maintain a separate
hand-written picture that can disagree with the executable definition. For each
rule, preserve source location, operator/argument structure, state leaves,
parameter definitions/lexical scope, referenced targets, entity/relation binding
and temporal requirements. Resolve referenced rules using the native semantics
and retain both direct and transitive dependency paths.

Compare extracted required **state fields** with the native
`engine.requirements[target]` interface observed in PR10. H reports that
requirements contains state leaves only, while dependencies contains referenced
targets/bound-call labels. Neither is a complete inventory of parameters,
constants, operators, temporal controls or applicability expressions. Do not
reconstruct the full graph from requirements alone. Report disagreement as a schema/definition mismatch,
not a reason to drop fields. Unsupported operators, absent rule definitions,
unknown parameter/clock semantics or unresolved relationship selectors remain
explicit diagnostics. UI can author these declarations with readiness gaps;
it cannot claim native execution or full coverage until the original source and
evaluator are pinned and verified.

## Composition and constraint invariants

- Ordered, parallel and conditional behavior composition is explicit. Conditional
  branches bind their condition/result and declared unknown policy; execution
  must not guess which branch to take.
- `waits_for` is a prerequisite edge in execution, not a claim that one event
  caused another. Reject unresolved references and unintended dependency cycles.
- A constraint can be a strategy input and also be enforced before/during
  execution. The former does not replace the latter.
- Hard constraints and soft objectives retain separate kinds. A soft-objective
  ranking cannot silently override a blocking hard constraint.
- A start/continue check result connects to its enforcement module and applicable
  behavior instance through declared configuration/records. Failed, unknown and
  conflicting results follow only the declared pause/abort/replan policy.
- Module execution produces facts; the fact is an instance value of a declared
  state field. An entity owning that field, a module computing it and an agent
  having command permission are three different relationships.
- Replay is read-only. An event or feedback edge may be displayed without
  invoking the current agent or repeating its command.

## Representative return path

The return objective and permitted current facts feed a strategy called by the
UAV-related agent. Its decision generates a return command. The command composes
navigation, flight and landing behaviors with explicit execution dependencies.
Each behavior requires capabilities/resources and is constrained by applicable
requirements. Start checks admit or block execution; in-execution checks apply
the configured continuation/pause/abort/replan policy. Modules then produce
actual motion and receipt facts. Predicate/rule evaluation and event occurrence
records feed back to the agent. Desired destination, accepted target and actual
position remain distinct throughout.

This is a conceptual fixture/configuration example. No actual source IDs,
legal thresholds, controller mode, arbitration policy or live success is inferred.
