# Guide: Behaviour packages

Behaviour packages are finite state machines that react to committed facts,
events, timers, receipts and predicates. Format and runtime live under
[../../src/aeroagentsim/behaviours/](../../src/aeroagentsim/behaviours/) with
the executor in
[../../src/aeroagentsim/engines/behaviour.py](../../src/aeroagentsim/engines/behaviour.py).
Format: `aeroagentsim.behaviour-package/v1`; evaluator version:
`aerograph-predicate/1`.

## Anatomy

A package declares `id`, `revision`, a `registry` snapshot reference, the
evaluator, `budgets`, `predicates`, `chains`, `bindings`, `conflicts`,
optional `injection_points` and optional `feedback`/`sampled_contexts`. A
complete minimal package is
[../../scenarios/behaviours/minimal.yaml](../../scenarios/behaviours/minimal.yaml)
and the larger flagship example is the demo's
[../../scenarios/demos/traffic-accident/behaviours.yaml](../../scenarios/demos/traffic-accident/behaviours.yaml).

Scenario wiring: exactly one engine with `plugin: behaviour`, plus a top-level
`behaviours:` list referencing packages (inline documents or `{path}` files;
imports resolve once at load, cycles are rejected).

```yaml
engines:
  behaviour:
    plugin: behaviour
    config:
      capabilities:
        traffic.air.move_to:
          schema: traffic.air.move_to
          target: air_motion
behaviours:
- path: behaviours.yaml
```

## Bindings and instances

Each binding selects role tuples from live entities at the committed cut:

```yaml
bindings:
- id: road-follow
  chain: traffic.road_follow
  match:
    vehicle: {is_a: aas:TrafficRoadVehicle}
    task:
      relation: traffic.task-assignee
      target_role: vehicle
      source_role: task
  variables:
    route_id: {field: traffic.road.route_id, role: vehicle}
  multiplicity: once_per_entity
  on_unbind: retain_until_terminal
```

- `multiplicity` is `once_per_entity`, `once_per_relation` or
  `once_per_task_episode` (an episode key from a declared field).
- Instance identity is a stable internal key over (run, epoch, package,
  binding, chain, sorted roles, episode); rebinding occurs on lifecycle or
  selector-relevant commits.
- Variables read declared fields when their producers commit; until then the
  instance records `status: waiting_inputs` — no value is synthesized.
- A binding can select by minimum rank:
  `{policy: minimum, field, role, group_roles, tie_break: EntityRef, limit: 1}`
  (used by the accident package for minimum eligible ETA).

## Chains, triggers and actions

Trigger categories: `predicate` (with `edge: entered|exited|while`), `event`,
`timer`, `receipt` (explicit `status` list, `policy: all|any`),
`lifecycle` (`created`/`removed`), `instance` (`activated`/`continued`),
`deadline`. Only `first_enabled` transition policy compiles.

Actions: `set` (owned fields only), `emit`, `command` (via a resolved
`capability` or explicit `schema`+`target`), `cancel_command` (the physical
owner must advertise cancellation — none is fabricated), `delay`,
`cancel_timer`, `assert_relation`, `close_relation`, `create_entity`,
`remove_entity`, `complete`.

Deadlines (`chain.deadline_ns`, per-command `deadline_ns`) fire as typed
`deadline` triggers that transitions may consume; they mark overdue work and
never synthesize a physical failure.

Budgets: `max_transitions_per_instance_per_ns` (explicit; the minimal example uses 64), `max_instances`,
optional `max_timers_per_instance`/`max_creations_per_instance`, plus the
kernel microstep bound. Exceeding a budget faults with instance and cause
context instead of silently dropping work.

## Sampled predicates and feedback lag

A sampled predicate (`profile: aerograph_sampled/v1`) consumes frames from a
`predicate` engine through `adapter: {event, context}`, or from a finite
package pool:

```yaml
sampled_contexts:
- id: capture.alpha
  roles: {pose: uav.alpha, task: incident-capture-01}
  sources: {pose: air_motion, task: behaviour}
  clocks: {pose: [canonical, canonical], task: [canonical, canonical]}
  predicates: [traffic.p.arrived, traffic.p.dwell_ready]
feedback:
  profile: aerograph_sampled/v1
  partition: behaviour
  return_lag_ns: 66666667
```

The declared return lag applies to the **shared behaviour recipient
partition**: all its incoming messages and receipts receive that lag, while
field reactions stay reactive. This is the actual dependency graph, not a
hidden relay; multiple packages must agree on the value.

## Injection points

Declare an injection point to admit typed external events mid-run (see
[external-events guide](external-events.md)):

```yaml
injection_points:
- id: accident
  stream_id: operator
  command: aas.runtime.inject_event
  target: behaviour
  emits: traffic.inject.accident
```

## Conflict rules

A conflict rule binds roles, references a predicate with an edge
(`entered`/`exited`), and on a fresh match records
`aas.behaviour.conflict` and emits its typed event; chains subscribe through
ordinary event triggers.

## Limits

- Reactive evaluation is stateless: no inferred motion between commits, no
  temporal operators; use sampled contexts for those.
- Generic `choose`/`stable_id` selector expressions are rejected; selection is
  the binding `minimum` policy or finite authored IDs.
- Completion policies check real child receipt statuses at terminal entry;
  unsatisfied requirements raise instead of completing on transport
  acknowledgement.
- Behaviour engines cannot write fields owned by other partitions — field
  ownership is single-writer and validated at compile time and runtime.

See also: [predicates guide](predicates.md), [agents guide](agents.md).
