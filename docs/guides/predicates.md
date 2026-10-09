# Write a predicate

A predicate evaluates typed committed state. Use a reactive predicate for conditions such as “task queued”; use a sampled predicate for conditions such as “stationary for three seconds”. The browser authors the expression; the runtime evaluates it.

## Start with a reactive condition

The [minimal task scenario](../../scenarios/behaviours/minimal.yaml) contains this package fragment:

```yaml
predicates:
  ready:
    profile: committed_reactive/v1
    roles: {task: 'example:Task'}
    expression:
      op: eq
      args:
      - {field: example.task.phase, role: task, path: []}
      - {literal: queued}
```

1. Declare a role type that exposes the field you need.
2. Choose the field through the registry rather than inventing its schema.
3. Write the expression and supply any declared parameters explicitly.
4. Reference the predicate in a trigger, guard or conflict rule.
5. Validate the complete scenario; then inspect the recorded truth and diagnostics.

A chain can begin with:

```yaml
# Fragment from chains.queue.
roles: {task: 'example:Task'}
trigger: {predicate: ready, edge: while}
preconditions: [ready]
```

Run the complete file with `aeroagentsim run scenarios/behaviours/minimal.yaml --out runs`. Follow [Your first scenario](../getting-started/first-scenario.md) to copy it and change one experimental factor.

## Choose edge semantics

`while` fires for a known-true evaluation revision. `entered` and `exited` compare consecutive known evaluations; initial true or unknown-to-true does not fabricate an entered edge. A producer/clock change breaks continuity. A repeating chain needs a real dependency change, event or scheduled delay.

Missing or invalid inputs yield a null truth value with diagnostics rather than false. Inspect the role, field, producer and read cut to find the actual source problem. Expressions unsupported by the selected dialect fail authoring validation.

## Add a temporal condition

Temporal operators require retained settled samples with explicit roles, upstream producers and clock mappings. The [predicate demo](../../scenarios/predicates-demo.yaml) is a complete environment-plus-predicate example. The [traffic package](../../scenarios/demos/traffic-accident/behaviours.yaml) declares a finite sampled-context pool for arrival and dwell conditions.

Use `profile: aerograph_sampled/v1` in a behaviour package and connect its adapter event/context to the configured sampled engine. Do not put `hold` or `time` leaves inside `committed_reactive/v1`: that profile is stateless. Keep duration units explicit; the runtime uses integer nanoseconds, while a sampled engine may explicitly select authored seconds through `temporal_unit: s`.

Sampled feedback into its upstream dependency cone needs a positive declared return lag. Changing motion cadence changes what a temporal condition can observe; it does not create observations between samples.

See [Predicates](../concepts/predicates.md), [Predicate dialect](../reference/predicate-dialect.md) and [Author an event chain](behaviours.md).
