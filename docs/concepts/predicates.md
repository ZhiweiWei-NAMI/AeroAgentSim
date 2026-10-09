# Predicates

Predicates are Boolean expressions over typed kernel state, executed against
committed facts. AeroAgentSim uses the AeroGraph predicate dialect
(`aerograph.predicate-definition/v1`) evaluated by a bounded, version-pinned
AST evaluator (`src/aeroagentsim/engines/predicate.py` +
`predicate_ast.py`). An unsupported expression fails scenario load with its
target ID and construct name — nothing silently degrades.

## Two execution models

How a predicate is evaluated depends on which engine hosts it.

| | `predicate` engine (sampled) | Behaviour reactive profile |
| --- | --- | --- |
| Evaluation time | Once per context after a sampled pass over settled upstream state | After every relevant committed state wave |
| Dialect | Full supported set, including temporal/window operators (`hold`, `delta`, `rise`, `fall`, `rate`, `stable_window`, …) for the declared native dialect | Stateless body only: temporal operators and `time` leaves are rejected at compile time |
| Edge detection | Native (`entered`, `exited`, `transition_event`) where the expression produces one; otherwise a wrapper compares successive frames | Wrapper-local `entered`/`exited`/`while` over previous/current truth |
| Records | Portable truth records re-emitted as `aas.behaviour.predicate_evaluated` on `aas.behaviour.sampled` | `Truth` records on `aas.behaviour.records` |
| Temporal history | Native, via the pinned sampled context | None — no inference about unseen motion |

The sampled path needs an explicit wiring: a `predicate` engine with its
`target`, `context`, `parameters` and a matching `bindings.samples` entry
that pins role bindings, sources, clocks and upstream owners. Sampled
contexts are **predeclared finite pools**; a runtime tuple outside the pool
yields a required-input diagnostic, never an invented context.

## Truth, unknown and diagnostics

Every evaluation produces a truth record with `status` and `value`:

- `known` with a real Boolean result, or
- `required_input` / `invalid_input` with a **null** value and diagnostics
  naming the role, entity, field and configured producer.

Unknown, absent facts and invalid input are distinct and never collapse into
`false`. No default value or fabricated Boolean is ever supplied. Records
carry `readCut`, per-field `acquired` clock/mapping/producer stamps and
half-open validity pairs; closing an interval is a new version known at its
own commit, never a retroactive edit.

## A minimal reactive predicate

From the runnable example `../../scenarios/behaviours/minimal.yaml`:

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

Inside a behaviour package this predicate is a chain trigger:

```yaml
chains:
  queue:
    roles: {task: 'example:Task'}
    trigger: {predicate: ready, edge: while}
    preconditions: [ready]
    # States and transitions are in the complete scenario.
```

Edge semantics in the reactive profile are wrapper-local:

- `while` — known true at this evaluation revision; fires at most once per
  revision and does not self-reactivate by writing fields. A repeating chain
  needs a positive delay, a new event or a dependency change.
- `entered` / `exited` — compare with previous truth. The baseline is
  deliberately discontinuous: no prior truth, a non-`known` prior status, or
  a changed source producer/clock breaks continuity instead of producing a
  spurious edge. Initial true and unknown→true produce no `entered`.

## Evaluation profiles and feedback lag

- `committed_reactive/v1` — stateless evaluation after each committed wave;
  reverse indexes dispatch only dirty `(entity, field)` contexts, so
  unrelated commits and viewer polls cause no evaluations.
- `aerograph_sampled/v1` — consumed from a sampled `predicate` engine's recorded
  frames, never recalculated reactively.

A sampled chain that feeds back into its own upstream cone needs a declared
positive return lag (`feedback.return_lag_ns`). The kernel rejects zero-lag
sampled cycles. In the current implementation the lag applies to the shared
behaviour recipient partition — all its incoming messages and receipts get
it — and the demo uses 66,666,667 ns lag with 1 Hz motion publication.

## Where to go next

- [Behaviours](behaviours.md) — chains that consume predicate edges.
- [Predicates guide](../guides/predicates.md) — authoring workflows.
- Dialect reference: [Reference: predicate dialect](../reference/predicate-dialect.md).
