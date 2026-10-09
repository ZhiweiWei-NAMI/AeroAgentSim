# Behaviours and chains

Behaviours are the orchestration layer: typed **chain templates** compiled
from declarative packages, automatically instantiated over scenario entities,
and executed as a DES engine on the shared kernel
(`src/aeroagentsim/behaviours/` + `src/aeroagentsim/engines/behaviour.py`).
The executor is domain neutral — it understands registry IDs, typed values,
chain states, actions and receipts, never vehicles or accident stages.

## Package format

A package is `aeroagentsim.behaviour-package/v1` (YAML), declared additively
in the scenario under `behaviours:`. It contains predicates, chain templates,
bindings, conflict rules, declared commands/events, injection points and
budgets. Compilation validates everything at authored load time — ownership,
capability resolution, expression typing, trigger categories, budgets — and
fails closed before any world binding. The resolved IR is frozen into the run
directory as `behaviour.ir.json`.

A scenario enables exactly one `plugin: behaviour` engine; each `set` action
writes only fields that engine owns (see [Plugins](plugins.md)).

## Chains, triggers and actions

A chain declares roles, one trigger, named states with guarded transitions,
and ordered actions:

- **Triggers** (exactly one per transition): predicate edge (`entered` /
  `exited` / `while`), typed `event`, `timer`, child `receipt` with an
  explicit status list and all/any policy, entity `lifecycle`
  (`created`/`removed`), `instance` (`activated`/`continued`), or `deadline`.
- **Actions** (each with a stable authored ID): `set` (owned fields only),
  `emit` (registered schema/topic), `command` (kernel command ID, receipts,
  optional deadline), `cancel_command` (only when the owner advertises
  cancellation), `delay` (positive duration; `0` means the next microstep),
  `cancel_timer`, `assert_relation` / `close_relation`, `create_entity` /
  `remove_entity`, and `complete`.
- **Deadlines** fire as typed triggers that mark overdue work — they never
  synthesize a physical failure or cancel receipt.
- **Completion policy** checks real child receipt statuses at terminal entry;
  transport acknowledgement is never success.

A dormant chain with a `while` predicate trigger retries when a precondition
receives a new truth revision and all preconditions and the trigger are known
true. This includes a trigger already sampled by another chain. Unrelated
commits do not retry it. `entered` and `exited` activation triggers and active
transition triggers still require their own fresh matching predicate revision.

## Automatic bindings and instances

Bindings select role tuples from live entities at the invocation's committed
cut, using real `is_a` ancestry, exact IDs, field selectors (`equals`/`in`)
and relation joins. Multiplicities are `once_per_entity`,
`once_per_relation` or `once_per_task_episode`. Instance identity is a stable
bounded key over `(run, epoch, package, binding, chain, sorted roles,
episode)`, so reasserting the same relation edge does not re-instantiate
while a new episode or entity generation does.

Instance lifecycle is journaled as ordinary typed records with statuses
`dormant → active → completed | failed | canceled`, plus `waiting_inputs`
(declared variables not yet committed — the instance wakes when the real
fact arrives, with no synthesized value) and transient `cleanup`.

## Ordering, conflicts and budgets

- Instances are scanned in `(bindingId, instanceId)` order; enabled
  transitions execute in `(-priority, bindingId, instanceId, authored index)`
  order, one transition per instance per invocation. Guards all read the same
  immutable invocation cut.
- Conflicting `set` slots are arbitrated in that order: the first candidate
  reserves each `(entity, field)` slot; a later collision is skipped with a
  typed `aas.behaviour.action_conflict` record. Losing proposals are visible
  facts, not last-write-wins.
- Budgets fault with instance/transition/cause context instead of silently
  dropping work: kernel `max_microsteps` (default 1024), per-package
  `max_transitions_per_instance_per_ns`, `max_instances`, and timer/creation
  budgets.
- **Conflict rules** evaluate predicates over bound roles and, on a fresh
  matching edge, emit a typed conflict event that chains subscribe to.

## Records, causes and replay

Every action emits an `aas.behaviour.action_started` record before its
effect; lifecycle, truth, conflict and receipt records all carry causal
references. Nothing lives in private console state — the journal is the only
source. Replay reconstructs instances, states, evaluations and receipts from
records with **zero** evaluator, engine or model calls.

## Runnable example

`../../scenarios/behaviours/minimal.yaml` is a complete nonspatial scenario:
one `example:Task` entity, a reactive `ready` predicate, a queue chain that
assigns → delays 10 ns → completes, a higher-priority interrupt transition on
a typed event, a conflict rule, and an `operator` ingress stream feeding a
single injection point. Run it:

```bash
aeroagentsim run scenarios/behaviours/minimal.yaml --out runs
aeroagentsim replay runs/<printed-run-directory>
```

## Where to go next

- [Predicates](predicates.md) — trigger evaluation profiles.
- [External events](../guides/external-events.md) — injection points and
  ingress streams.
- Package reference: [Reference: behaviour package](../reference/behaviour-package.md).
