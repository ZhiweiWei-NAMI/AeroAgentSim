# Your first scenario

Start with a small task lifecycle before adding spatial models or external services. The complete [minimal scenario](../../scenarios/behaviours/minimal.yaml) defines a task entity, a behaviour engine, a state predicate and an external interruption. It uses its own [registry snapshot](../../scenarios/behaviours/registry.snapshot.json).

```bash
aeroagentsim run scenarios/behaviours/minimal.yaml --out runs/first-study
```

The command prints the run directory. Inspect its `journal.jsonl`, or replay it:

```bash
aeroagentsim replay runs/first-study/<run-id>
```

## Read the configuration

These fragments come from the complete file; they are not standalone scenarios.

```yaml
entities:
  - id: task-1
    type: example:Task
    facts:
      example.task.phase: queued
```

The registry defines the phase as a string enum. The `ready` predicate checks whether the task is queued. A behaviour binding matches its type and instantiates the queue chain. Activation assigns the task and schedules a completion timer ten nanoseconds later.

The `rules` capability binding grants the behaviour engine ownership of the phase field. Its `lifecycle` controller binding makes the entity available to that engine. Another engine cannot also own the same field.

```yaml
run:
  pacing: fast
  seed: 7
  until_ns: 20
  advance_ns: 5
```

Times are integer **nanoseconds**. This deliberately tiny logical-time experiment is not a twenty-second run.

The operator stream permits progress through time 20. A declared command interrupts the task at time 5, before its completion timer. The chain reaches `interrupted`; a predicate rule emits a conflict event.

## Change one factor

Copy both files to preserve the relative snapshot reference:

```bash
mkdir -p scenarios/first-study
cp scenarios/behaviours/minimal.yaml scenarios/first-study/scenario.yaml
cp scenarios/behaviours/registry.snapshot.json scenarios/first-study/
```

In the copy, change the command's `at_ns: 5` to `at_ns: 15`. Run `aeroagentsim run scenarios/first-study/scenario.yaml --out runs/first-study` again. Completion now precedes interruption, so the task reaches its terminal `completed` state. This creates a new run; replay of the earlier run retains its earlier outcome.

Next, add a [predicate](../guides/predicates.md), extend the [event chain](../guides/behaviours.md), or configure a [domain plugin](../guides/plugins.md). See the [scenario reference](../reference/scenario.md).
