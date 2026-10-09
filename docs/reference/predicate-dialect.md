# Predicate dialect reference

Predicate expressions are bounded JSON/YAML ASTs evaluated by the predicate
engine ([`src/aeroagentsim/engines/predicate_ast.py`](../../src/aeroagentsim/engines/predicate_ast.py),
[`predicate.py`](../../src/aeroagentsim/engines/predicate.py)). The dialect is
enumerated and closed: unknown operators, malformed nodes and oversized ASTs
(depth > 128, > 100 000 nodes) are rejected at load with the authored path.

## Node shapes

Leaves (exactly one key per node):

| Leaf | Keys | Meaning |
| --- | --- | --- |
| literal | `literal` | Constant value (deep-copied, never resolved). |
| parameter | `parameter` (+ `sourceName`, `sourceDefaultPresent`, `sourceDefault` accepted) | Named parameter from the definition's `parameters` mapping; undeclared parameters are rejected. |
| field | `field, role` (+ optional `path`) | Value of a role-bound field at the sampled frame; `path` indexes into record/array schemas. |
| relation | `relation, sourceRole, targetRole` | Whether the declared relation edge exists between the bound endpoints. |
| var | `var` (+ optional `path`) | Quantifier/binding variable; must be bound in scope. |
| time | `time: true` | Sample time in nanoseconds. |

Operator nodes are `{op, args}` + optional `nativeOperator` and, for temporal
operators only, `asScope` (a `sequence` node used as the identity scope for
temporal windowing).

## Operators

Canonical operators (availability also depends on the dialect):

| Op | Arity | Op | Arity |
| --- | --- | --- | --- |
| `not` | 1 | `eq` `ne` `lt` `lte` `gt` `gte` | 2 |
| `sub` `div` `pow` | 2 | `add` `mul` `min` `max` | variadic ≥ 1 |
| `abs` `sqrt` | 1 | `clamp` `between` | 3 |
| `in` `contains` `subset` `disjoint` `set_equal` | 2 | `implies` `is_unknown` | 2 / 1 |
| `if` | 3 | `len` `count` `unique_count` `sum` | 1 |
| `vector_norm` `date_time` | 1 | `dot` `cross` `distance` | 2 |
| `and` `or` `norm` | variadic ≥ 1 | `sequence` | scope only |
| `interval_overlap` `interval_contains` | 2 | `same_identity` | variadic |
| `all` `any` | quantifier nodes `{op, array, var, predicate}` + optional `applicabilityExpression` | `sequence` | variadic (only valid inside an `asScope`) |

Unknown values: `None` is an undetermined result, never a substituted
observation. `and`/`or` in the `original` dialect use three-valued logic
(`false AND unknown` is `false`); the `expanded` dialect propagates `None`.
Comparisons, arithmetic and set operations return `None` when any operand is
unknown. `is_unknown` is the only way to test for absence; `div` by zero
returns `None`, `sqrt` of a negative returns `None`.

Temporal operators (`args[0]` = expression, `args[1]` = duration in ns, optional
`args[2]` = max allowed sample gap in ns, or `literal: null` for no limit):

| Op | Meaning |
| --- | --- |
| `hold` / `all_window` | True throughout the trailing window. |
| `any_window` | True at some sample in the trailing window. |
| `count_window` | Count of true samples in the trailing window. |
| `delta` / `rate` | Value change / per-second rate between the window anchor and now. |
| `stable_window` | Value unchanged across the whole window. |
| `rise` / `fall` / `changed` | Boolean transition between consecutive samples. |
| `entered` / `exited` | Same as rise/fall on a scoped identity (1–3 args). |

Durations must map exactly to nonnegative integer nanoseconds: literals or
declared parameters only — computed duration expressions are rejected. With
`temporal_unit: s`, authored second values are converted to exact ns at load
(`rate` is rescaled correspondingly; `time` leaves are divided by 1e9).
Nested windows recurse from the actual outer anchor, not from *now minus the
sum of durations*, so irregular sampling gaps cannot shorten a window silently.

## Supported / unavailable values per dialect

Each predicate definition declares `execution.nativeDialect`; the loader
rejects operators and constructs unavailable in that dialect.

| Dialect | Available | Not available |
| --- | --- | --- |
| `original_compact_ast` | arithmetic, comparisons, logic, `in`, `set_equal`, `unique_count`, `len`, `sum`, `norm`, `distance`, `clamp`, `between`, all temporal operators, `rise/fall/changed` | `all`, `any`, `implies`, `contains`, `count`, `vector_norm`, `cross`, `same_identity`, `date_time`, `interval_overlap`, `interval_contains`; also `relation`, `var`, `time` leaves and non-empty field `path`s |
| `expanded_typed_ast` | quantifiers, `implies`, `contains`, vector ops, intervals, `same_identity` | temporal operators and `rise/fall/changed`, `if`, `is_unknown`, `in`, `set_equal`, `unique_count`, `len`, `sum`, `norm`, `distance`, `clamp`, `between` |

The two dialects also differ in unknown propagation (`original` uses
three-valued `and`/`or`, `expanded` propagates unknowns), and this difference
is deliberate and retained.

## Diagnostics and temporal selection

At each sampled frame the engine collects diagnostics instead of guessing:

- `required_input` — a role's entity generation is not alive, or a bound field
  has no valid committed fact.
- `invalid_input` — a fact or relation edge came from a different producer or
  clock than the pinned sample spec, or evaluation itself raised.

If any diagnostic is present the frame's value is `None` and the recorded
status is `invalid_input` or `required_input`; only a clean evaluation is
`known`. Emission transitions (`entered`, `exited`, `level`) compare only
consecutive *known* frames, so an unknown never fabricates a transition.

Temporal windows select samples by their exact recorded nanosecond times
(strictly increasing). A window whose anchor sample does not exist yet
evaluates to `None` (`complete == false`); `asScope` requires all samples in
the window to share the same scoped identity, and an exceeded max gap makes
the result unknown.

## Example

```yaml
# Fragment — a sampled predicate: true when the role entity's speed has
# stayed below 0.5 m/s for the trailing 5 seconds, expressed as nanoseconds.
predicates:
  stationary:
    profile: aerograph_sampled/v1
    roles: {v: aas:TrafficRoadVehicle}
    parameters: {window_ns: 5000000000}
    adapter: {event: aas.behaviour.sample_signal, context: intersection-a/stationary}
    expression:
      op: hold
      args:
        - op: lt
          args:
            - {field: traffic.road.speed_mps, role: v}
            - {literal: 0.5}
        - {parameter: window_ns}
```

Note: this fragment shows the predicate definition only; a runnable scenario
also needs the matching sample context, sources and clocks (see
[behaviour packages](behaviour-package.md)).
