# Make your own template in 10 minutes

A template turns an existing runnable scenario into a small experiment with
named controls. Start with a scenario that already runs from a public clone,
with a pinned registry snapshot and any behaviour or decision inputs beside
it. Keep every assumption in those files.

1. Create `scenarios/templates/your-id/` and copy your scenario and its inputs
   there. Check that relative registry and behaviour paths resolve from the
   scenario directory. An AeroGraph checkout must not be needed at launch.
2. Add `template.yaml` using the format below. Describe what happens, what to
   watch and what to change in ordinary language.
3. Point each knob at an existing scenario member using a JSON pointer. Keep
   bounds small enough that the default run completes quickly.
4. Run `aeroagentsim templates`, then launch with `run-template`. Fetch KPIs or
   compare two API runs to see the effects of your changes.

```yaml
format: aeroagentsim.template/v1
id: your-id
title: A small delivery experiment
summary: Two drones deliver parcels across a district.
category: logistics
difficulty: beginner
duration_s: 60
scenario: scenario.yaml
observe:
  - Watch which drone receives each parcel.
  - Compare the time between task creation and delivery.
try:
  - Increase the random seed to repeat with different task arrivals.
  - Change the allocation algorithm.
knobs:
  - id: seed
    label: Random seed
    type: integer
    default: 1
    min: 0
    max: 1000
    pointer: /run/seed
  - id: allocator
    label: Allocation algorithm
    type: choice
    default: nearest
    options: [nearest, auction]
    pointer: /algorithms/allocation
kpis: [tasks_completed, mean_task_latency_s, energy_used_kj]
```

The example assumes your scenario already declares `/run/seed` and
`/algorithms/allocation` and has the corresponding runnable algorithms.
Knobs change configuration; the registry does not create an engine for you.
Supported knob types are `integer`, `number`, `choice` and `boolean`. JSON
pointer segments escape `~` as `~0` and `/` as `~1`; array members use their
index. For changes involving multiple fields or structure, use an explicitly
registered named `apply` hook instead of a pointer. Hooks receive a copied
document, its base directory and the validated value as
`hook(document, base, value)`. Register it with
`registry.register_apply_hook("your-hook", hook)` before application. Knob
values still pass typed validation.

Use the common committed events from [the shared event
contract](../reference/viewer-feed.md) for task, airspace, energy and weather
activity. For a custom business event, declare its narrative in the scenario
or pack, for example:

```yaml
event_narratives:
  example.parcel.delivered:
    template: "{vehicle} delivered {parcel} at {place}"
    category: logistics
    severity: info
    entities: [vehicle, parcel, place]
```

Every placeholder must exist in the actual event payload. Add a position only
when it comes from that payload or committed physical facts. Narratives make
events readable; they do not change simulation state or KPI meanings.

```sh
aeroagentsim run-template your-id --set seed=2 --set allocator=auction --headless
curl -H 'Content-Type: application/json' \
  -d '{"knobs":{"seed":2},"workspace":true}' \
  http://127.0.0.1:8002/v1/templates/your-id/runs
```

The workspace response gives an editable Studio draft; the run response gives
the journal-backed experiment. Keep the traffic accident demo as a reference
for named hooks and a complete report → award → upload event chain.
