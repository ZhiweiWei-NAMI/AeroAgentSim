# Behaviour packages

A behaviour package is an authored YAML document that the behaviour engine
compiles into an immutable IR at scenario load. Sources of truth:
[`src/aeroagentsim/behaviours/compiler.py`](../../src/aeroagentsim/behaviours/compiler.py)
and [`src/aeroagentsim/behaviours/schema.py`](../../src/aeroagentsim/behaviours/schema.py).

- Package format: `aeroagentsim.behaviour-package/v1`
- Compiled IR format: `aeroagentsim.behaviour-ir/v1`
- Evaluator version: `aerograph-predicate/1`
- Compile profiles: `committed_reactive/v1` (stateless Boolean AST) and
  `aerograph_sampled/v1` (temporal AST over a pinned sample context).

Compilation is strict: unknown keys, unknown roles, fields that do not apply to
a role's type, and unowned field writes all fail at the authored path
(`<source>: <json path>: message`) before the run starts.

## Package document

| Key | Required | Shape |
| --- | --- | --- |
| `format` | yes | `aeroagentsim.behaviour-package/v1` |
| `id` | yes | Nonempty package identity; must be unique within the resolved import closure. |
| `revision` | yes | Integer. |
| `predicates` | yes | Mapping name → definition (below). |
| `chains` | yes | Mapping name → chain definition (below). |
| `bindings` | yes | List of chain bindings (below); unique ids. |
| `budgets` | yes | `{max_transitions_per_instance_per_ns, max_instances}` + optional `max_timers_per_instance, max_creations_per_instance`; integers. |
| `registry` | no | `{snapshot}` — registry requirements this package needs. |
| `evaluator` | no | Optional `{version, dialect}`; `dialect` is `original` or `expanded`. |
| `conflicts` | no | Declared conflict rules. |
| `injection_points` | no | Operator ingress points validated at run time (see below). |
| `bootstrap_relations` | no | `{assertions}` + optional `owner, acquired_ns, valid_from_ns`; stamps must be 0 — later assertions are injected as events. |
| `imports` | no | Import closure; external files are `{path}`. Cyclic imports are rejected; duplicate package ids must be identical. |
| `feedback` | no | Sampled feedback: `{profile: aerograph_sampled/v1, return_lag_ns, partition}`. |
| `sampled_contexts` | no | Finite sampled-context pool (below). |
| `requires_compiler_features` | no | Names from the compiler's supported-feature list; unknown features are rejected. |

## Predicates

```yaml
# Fragment — predicate definitions.
predicates:
  too-close:
    profile: committed_reactive/v1
    roles: {self: aas:TrafficRoadVehicle, other: aas:TrafficRoadVehicle}
    expression:
      op: lt
      args:
        - {field: traffic.road.safe_gap, role: self}
        - {literal: 2.0}
```

| Key | Required | Notes |
| --- | --- | --- |
| `profile` | yes | `committed_reactive/v1` or `aerograph_sampled/v1`. |
| `roles` | yes | Role name → type id. |
| `expression` | yes | Predicate-dialect AST; see [predicate dialect](predicate-dialect.md). |
| `parameters` | no | Values for `parameter` leaves used in the expression. |
| `use` | no | Declaration of where the predicate is referenced. |
| `adapter` | required for `aerograph_sampled/v1` | `{event}` plus exactly one of `context` or `contexts` (a finite, unique pool). |

Reactive predicates must be stateless: temporal operators (`hold`, `delta`,
`entered`, …) and `time` leaves are rejected in `committed_reactive/v1`;
temporal evaluation needs the sampled profile, which pins a sample context and
evaluates over retained settled samples.

## Chains

A chain is a state machine instance template:

| Key | Required | Notes |
| --- | --- | --- |
| `roles` | yes | Role → type id. |
| `trigger` | yes | Initial trigger, exactly one category of `predicate, event, timer, receipt, lifecycle, instance, deadline`; template timers need explicit `after_ns`. |
| `initial`, `terminal`, `states` | yes | Unique state names; `initial` in `states`; `terminal` ⊆ `states`. |
| `transitions` | yes | `{id, from, on, to, actions}` + optional `guard, priority`. `on` is a trigger; `guard` names a predicate. Timer/receipt/deadline triggers must reference actual compatible child actions. |
| `preconditions` | no | Predicate names. |
| `deadline_ns` | no | Integer instance deadline. |
| `transition_policy` | no | Only `first_enabled` (default). |
| `completion_policy` | no | `{children, statuses, policy}` + optional `terminal_states`; `policy` is `all` or `any`. |
| `variables` | not allowed | Typed variables are declared on bindings, not on the chain. |

### Actions

Action kinds: `set`, `emit`, `command`, `cancel_command`, `delay`,
`cancel_timer`, `assert_relation`, `close_relation`, `create_entity`,
`remove_entity`, `complete`. Every action has an `id` and `kind`;
`set` requires field ownership by this partition; `command` takes either a
`capability` (resolved from engine config to exact schema+target) or explicit
`schema`+`target`; `complete` takes an explicit terminal `status`
(`completed`, `failed`, `canceled`). Behaviour record schemas
(`aas.behaviour.*`) may not be emitted by authored actions — the executor
records them.

### Bindings

Each binding is `{id, chain, match, multiplicity, on_unbind}` + optional
`variables, episode_field, episode_role, selection`. `match` selects the role
fillers via `is_a`, `entity`, `field`/`equals`/`in`, or `relation` with
`source_role`/`target_role`. Variables declared here are typed through their
source expressions.

## Sampled context pool

`sampled_contexts[]` entries are `{id, roles, sources, clocks, predicates}` +
optional `same_identity_roles`. Roles, sources and clocks must name the same
finite tuple; clocks are explicit `[clock_id, mapping_id]` pairs. Pool
expansion happens once at load (`bindings.samples` entries are generated); no
contexts are created or rebound during execution.

## Injection points

Declared `injection_points` are the only authored ingress the behaviour engine
accepts. At admission time the service validates the incoming command against
the pinned manifest: the `injection_point` id must resolve uniquely for the
command schema, target engine and named stream must match the declaration, and
the payload must validate against the point's emitted event schema. See
[HTTP API](http-api.md#live-ingress-and-watermarks).

## Run-time recording

The executor records predicate evaluations and chain lifecycle events into the
journal; the projector surfaces them to viewers as `predicateTruth` and
`chainInstances` extension arrays. See [viewer feed](viewer-feed.md).

## Example

The complete [minimal task scenario](../../scenarios/behaviours/minimal.yaml) includes an inline package. Its activation transition writes an owned phase field and schedules completion:

```yaml
# Fragment from chains.queue.transitions.
- id: assign
  from: waiting
  on: {instance: activated}
  to: assigned
  priority: 0
  actions:
  - {id: assignment, kind: set, entity: {$role: task}, field: example.task.phase, value: assigned}
  - {id: work, kind: delay, duration_ns: 10}
```

The delay is ten nanoseconds. The full file also defines the role, phase schema, completion and interruption transitions, binding, budgets and injection point. For a larger example, see the [traffic package](../../scenarios/demos/traffic-accident/behaviours.yaml).
