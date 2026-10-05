# Rule AST, binding and result contract

Status: integration specification, 2026-10-05. This is the required authoring,
inspection, validation and export behavior. It preserves the existing Atlas
engine; it is not a replacement evaluator or a claim of full native-catalog
compatibility. Source evidence and unresolved boundaries are explicit below.

## Source authority

The original project plan §7 documents `state`, `const`, `param`, `op`, `args`,
predicate/event references and `window_s`. An earlier direct inspection of native
`runtime/engine.py` additionally verified target `rule`, independent
`applicability_rule`, non-dict literals, `op: literal`/`value`, `op: unknown`,
`duration_seconds`, `max_gap_s`, `scope`, `enter_s` and `clear_s`.

Original source bytes are not currently available for a fresh exhaustive grammar
or operator-registry audit. The native graph export implementation
`export_graph.py` has not been inspected. Thus the node/edge labels below are
explicit integration representation requirements, not claimed existing exporter
enums. The implemented PR10 adapter and root integration source confirm the
binding, field-map, parameters and explicit-history interfaces described in
[TYPED_GRAPH_RELATIONS.md](TYPED_GRAPH_RELATIONS.md).

## Four structures that must stay separate

1. **State specification and fact:** a state is a typed field. A fact assigns one
   runtime value with exact subject, source, unit/frame, validity and availability.
   A structured value may contain typed members or references; that does not make
   it a composite predicate. Supported scalar/structured shapes depend on the
   declared field and adapter contract.
2. **Derived state:** a module calculates a named state value from other facts,
   with one computing authority, declared algorithm/dependencies and provenance.
   The output remains a state value, not a Boolean proposition merely because it
   was calculated. Unsupported structured/derived shapes remain future work,
   not silently flattened or accepted as native rule syntax.
3. **One predicate using several state inputs:** a rule can compare or combine
   several numeric, geometric or relation-derived inputs before producing one
   proposition. The graph retains all leaves and nested calculations; multiple
   inputs do not imply a logical conjunction of several predicates.
4. **Logical composition of predicates:** a rule references named predicates,
   then combines their results through declared native operators. Preserve the
   referenced definitions, binding/parameter context and native three-valued
   behavior. This is different from a multistate numeric expression.

An event definition also has a rule expression. Its evaluated truth is separate
from an occurrence record under a declared temporal/occurrence policy. A command
or behavior sequence is an execution structure, not the rule AST.

## Native expression structure to retain

The general structure is **state/parameter/constant leaves and referenced target
results → nested operator expressions → a predicate or event's rule root**.
The root may also be a leaf, literal or target reference without an operator.

| Source form | Integration expression kind | Required preservation |
| --- | --- | --- |
| `state` leaf | State reference | Exact native state ID, source AST path, expected field signature and binding slot |
| `param` leaf | Parameter reference | Exact parameter ID, lexical scope, declared type/quantity/unit and source default if one exists |
| `const` leaf | Constant | Exact typed value and source representation; never replace it with an authored runtime fact |
| Non-dict literal | Literal | Original literal type/value; no Boolean/number coercion or invented object wrapper in native export |
| `op: literal`, `value` | Explicit literal expression | Preserve both the operator form and value as authored in source |
| `op: unknown` | Explicit unknown expression | Preserve native explicit-unknown semantics; do not treat as false or missing declaration |
| `predicate` / `event` reference | Target reference | Exact target kind/ID/revision and parameter/binding context; use the reference result as an expression input |
| `op` and `args` | Operator expression | Native operator ID, ordered operands, nested structure, signature and all source-specific properties |
| Target `rule` | Main rule root | Exact root expression, target ID/kind/revision and declared parameters |
| Target `applicability_rule` | Applicability root | Independent expression and `scope_status` result; do not merge it into main truth |
| Temporal/control fields | Native control properties/expressions | `window_s`, legacy `duration_seconds`, `max_gap_s`, `scope`, `enter_s`, `clear_s` where present, without invented defaults or alias precedence |

For example, the earlier source inspection verified
`hu.predicate.actor_moving` as an operator `gt` with ordered arguments
`{state: hu.actor.speed_mps}` then `{param: moving_mps}`, and a declared parameter
value `0.2`. That is a source example with its original units/signature to be
retained; it is not a universal operational or legal speed threshold.

Do not infer that every geometric or relation requirement has a dedicated native
AST node. Native relation/structured-state node syntax has not been established
by the available evidence. Use explicit typed selectors and supported original
operators, or report that exact construct as unsupported.

## Definition graph versus runtime graph

Definition nodes/edges show what a rule can read and how it computes:

- A predicate/event definition uses its main rule root and optional applicability root
- State leaves refer to state specifications; parameter leaves to parameter definitions
- Operator nodes retain each operand's index/role and direction
- Target-reference nodes refer to predicate/event definitions
- Direct dependencies and reference-expanded dependencies are both retained
- A relation-derived selector declares its relation schema and ordered endpoint roles
- Temporal nodes/properties state the actual native history requirements

Runtime records show what one evaluation did:

- A binding chooses exact entity/relation references and generations
- Its `field_map` maps native state IDs to selected prepared input slots
- Parameters, query time, available history and evaluation revision are explicit
- Prepared inputs retain actual fact references, unavailable reasons and source context
- Results reference their definition/binding/revision and the facts actually used
- Main truth, applicability status and diagnostic/binding status are separate

Static `depends_on` does not mean a fact was available at runtime. Runtime
`reads_fact` or `based_on` does not redefine the rule. Entity role binding, field
mapping and input provenance are not evidence of physical causation.

## Requirements and references

The earlier native-engine inspection found that `requirements` collects state
IDs only. `dependencies` collects referenced target IDs/bound-call labels. Keep
both and their scope; neither is the full graph.

The adapter's observed `engine.requirements[target_id]` check ensures required
state fields have explicit bindings. It cannot by itself reconstruct parameters,
constants, operator nesting/operand order, applicability expressions, temporal
controls or all typed relationship semantics. The UI must derive those from the
pinned target definitions/AST, not draw a hand-maintained approximation.

## Validator contract

The validator must distinguish authoring validity, verified native compatibility
and runtime readiness. It may author/import a preserved unsupported expression
with a precise diagnostic; it must not mark that expression native-executable.

| Check | Required result or diagnostic |
| --- | --- |
| Target/root identity | Unique target definition/revision and valid root; main/applicability roles are distinct |
| State and relation reference | Exact defined field/schema and typed endpoint roles; unresolved IDs stay visible |
| Operator signature | Pinned operator exists; argument count/types and output type match; unknown operator remains unsupported |
| Operand order | Preserve every index/named role and nested subexpression; commutative appearance is not permission to reorder |
| Parameter scope | Every reference resolves in the correct lexical/binding context; explicit source defaults only; type/unit checked |
| Constant/literal typing | Preserve literal type and finite-number policy; no Boolean-as-number or null-to-zero substitution |
| Quantity and coordinates | Compatible quantities, units, frame and altitude reference, or a declared validated conversion |
| Entity/relation bindings | Exact run/epoch/generation/lifecycle, endpoint order and field-map signature; no display-name or ID-prefix inference |
| Target references/cycles | Resolve references and retain paths; reject unsupported/illegal cycles under the pinned native rules, not a guessed permissive policy |
| Temporal semantics | Preserve controls, time basis, window/boundary policy and same-binding history; missing semantics remain unverified |
| Validity and availability | No future, stale or cross-domain evidence without an explicit mapping; source correction gets an explicit evaluation revision |
| Three-valued truth | Native True/False/Unknown retained; diagnostic statuses and scope_status stay separate |
| Definition/runtime separation | Structured field, derived field, multistate predicate, composite predicate and event occurrence remain different kinds |
| Export parity | Imported source expression and exported source expression preserve semantics and all supported native properties |

Do not hard-code new truth logic in this validator. In particular, an unavailable
leaf can coexist with a decisive native conjunction/disjunction result. Only the
native evaluator determines the result under its supported semantics.

## UI and export contract

The AST view exposes leaves, constants, parameter definitions/values, ordered
operator arguments, referenced targets and temporal controls. It permits focus
on direct dependencies or a source-traceable reference expansion. Main-rule and
applicability paths are visually distinguishable. Selecting a runtime result
shows its concrete entity/role/time/parameter binding and fact lineage.

Graph edges are typed and directed, with role and source AST path. Multiple
different edges may connect the same nodes; IDs, not node pairs, identify edges.
Generated display edges must be derived from authoritative AST/reference fields
and never become a competing second source of definition truth.

An export retains schema and native catalog/engine revision, target IDs,
definition/instance distinction, the source-native AST, bindings, parameters,
time/history policy, module-source requirements and unsupported diagnostics.
Preserve unrecognized source properties as opaque data where safe, or reject
lossy export explicitly. Do not silently drop them, normalize ambiguous aliases,
reorder operands or replace a reference with an assumed equivalent expression.

## Round-trip and native parity acceptance

1. Import a pinned native target and record its original expression/metadata.
2. Build the typed graph from that data; validate its dependency and binding views.
3. Export without semantic changes and compare all expression forms, operand
   ordering, parameters/defaults/scopes, references, controls and applicability.
4. Re-import and confirm stable graph/reference meaning and identity.
5. Where the unchanged native engine is available, evaluate original and exported
   forms over matching positive, negative, unknown, boundary and historical cases.
6. Keep native test outcomes separate from adapter projection, fixture routing
   and frontend interaction tests. Missing native source/evaluator blocks parity
   claims, not clearly labelled authoring work.

The current source audit establishes no exhaustive operator catalog, original
exporter enum, native relation-node grammar, or every rule's reference/temporal
semantics. Those exact gaps remain visible for the full mapping audit. Do not
rewrite the engine to make the partial representation appear complete.
