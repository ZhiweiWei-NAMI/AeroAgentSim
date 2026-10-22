# Make your own template in 10 minutes

A template turns an existing runnable scenario into a small experiment with
named controls. Start with a scenario that already runs from a public clone,
with a pinned registry snapshot and any behaviour or decision inputs beside
it. Keep every assumption in those files.

`scenarios/templates/` holds the built-in templates, registered as scenario
plugins in `src/aeroagentsim/scenarios/`. For an external template, ship its
inputs in a plugin package instead of adding files to the platform checkout;
see [Scenario plugins](scenario-plugins.md) and [Plugin contracts](../reference/plugin-contracts.md).

1. Create `scenarios/templates/your-id/` and copy your scenario and its inputs
   there. Check that relative registry and behaviour paths resolve from the
   scenario directory. An AeroGraph checkout must not be needed at launch.
2. Add `template.yaml` using the format below. Describe what happens, what to
   watch and what to change in ordinary language. For a new built-in template,
   add its id to `TEMPLATES` in `src/aeroagentsim/scenarios/__init__.py`.
3. Point each knob at an existing scenario member using a JSON pointer. Keep
   bounds small enough that the default run completes quickly.
4. Run `aeroagentsim templates`, then launch with `run-template`. Fetch KPIs or
   compare two API runs to see the effects of your changes.

```yaml
format: aeroagentsim.template/v1
id: your-id
title: A small delivery experiment
title_zh: 小型配送实验
summary: Two drones deliver parcels across a district.
summary_zh: 两架无人机为街区内的客户配送包裹。
category: logistics
difficulty: beginner
duration_s: 60
scenario: scenario.yaml
observe:
  - Watch which drone receives each parcel.
  - Compare the time between task creation and delivery.
observe_zh:
  - 观察每个包裹分配给了哪架无人机。
  - 对比任务创建到包裹送达的时间。
try:
  - Increase the random seed to repeat with different task arrivals.
  - Change the allocation algorithm.
try_zh:
  - 调整随机种子，尝试不同的订单到达过程。
  - 切换任务分配算法。
knobs:
  - id: seed
    label: Random seed
    label_zh: 随机种子
    type: integer
    default: 1
    min: 0
    max: 1000
    pointer: /run/seed
  - id: allocator
    label: Allocation algorithm
    label_zh: 任务分配算法
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
`hook(document, base, value)`. Expose it in a scenario plugin's `apply_hooks`
mapping as `your-plugin.your-hook`, and use that name in the knob's `apply`
field. Knob values still pass typed validation. See the
[scenario plugin guide](scenario-plugins.md) for packaging and hook rules.

## Additive Chinese metadata (2.1)

The English fields above stay required. Chinese siblings are optional, so
existing `aeroagentsim.template/v1` manifests and English clients still work.
All ten shipped templates include Chinese titles, summaries, observation and
experiment bullets, and knob labels.

| Optional field | Shape and validation |
| --- | --- |
| `title_zh`, `summary_zh` | Nonempty strings. |
| `observe_zh`, `try_zh` | Nonempty string lists, with the same length and order as the English bullets. |
| `knobs[].label_zh` | Nonempty display label; `id` and `pointer`/`apply` are unchanged. |
| `knobs[].description_zh` | Nonempty string; requires an English `description`. |
| `knobs[].options_zh` | For `choice` knobs only: nonempty labels aligned one-for-one with `options`. Values submitted to the API must still be the original options. |

`GET /v1/templates` and `GET /v1/templates/{id}` return these fields unchanged,
alongside existing metadata and `thumbnail_url` when a thumbnail is declared.
A Chinese client chooses the `_zh` field when present and falls back to the
English sibling when absent. `options_zh` is useful for non-algorithm choices;
for example, the traffic template labels `scripted` as `预设决策` and `live`
as `实时模型`, without changing either value.

`GET /v1/algorithms` returns slots containing
`{id, label, description, label_zh?, description_zh?}`. Every shipped algorithm
has both Chinese fields, whether discovered through an installed entry point
or the builtin fallback. Third-party factories can advertise nonempty
`label_zh` and `description_zh` attributes without being instantiated. A plugin
that overrides a builtin id keeps its own metadata; it does not inherit that
builtin's Chinese description.

Template API, CLI and experiment runs pin the authored title in run metadata
as `template.title`, plus `template.title_zh` when present. Run-list and
comparison rows expose `title_zh` and `template_title_zh` from that pinned
value; old runs without a translation omit them. Experiment reports also
preserve optional `template_title_zh`. Editing today's template does not
rename an earlier run.

## Bilingual business narratives

Use the common committed events from [the shared event
contract](../reference/viewer-feed.md) for task, airspace, energy and weather
activity. For a custom business event, declare its narrative in the scenario
or pack, for example:

```yaml
event_narratives:
  example.parcel.delivered:
    template: "{vehicle} delivered {parcel} at {place}"
    template_zh: "{vehicle}在{place}送达包裹{parcel}"
    category: logistics
    severity: info
    entities: [vehicle, parcel, place]
```

`template_zh` is an optional nonempty string using exactly the same set of
placeholder names as `template`; their order may differ. Only plain `{field}`
placeholders are allowed, not attribute access, conversions or format specs.
Custom placeholders must exist in the actual event payload. The shared lowalt
pack additionally derives `{noun}`, `{done}`, `{origin}` and `{destination}`
from recorded task creation, with Chinese phrases for all eight task kinds.
Entity labels/ids and authored payload strings remain intact in both languages.
A scenario override replaces the whole declaration, so supply its own
`template_zh` rather than relying on a potentially stale pack translation.

The lowalt pack's internal `EVENT_NARRATIVES` table uses `text`/`text_zh` for
its templates; scenario, behaviour-package and traffic declarations use
`template`/`template_zh` as above. The projector normalizes both forms. All
lowalt and traffic narrative declarations ship with Chinese translations.

The existing additive `aeroagentsim.event-feed/v1` projection appears as
`events[]` inside both REST commits and SSE commits, not as a separate endpoint.
Its rows are `{at_ns, category, severity, text, text_zh?, entities, position?}`.
`text` remains the English rendering; `text_zh` is present only when the active
declaration supplies a translation. Clients choose `text_zh` for Chinese and
fall back to `text` otherwise; no language query parameter is required.
Missing interpolation values are errors, never invented success messages.

Add a position only when it comes from the payload or committed physical facts.
Narratives make events readable; they do not change simulation state, entity
links, categories, severity or KPI meanings.

```sh
aeroagentsim run-template your-id --set seed=2 --set allocator=auction --headless
curl -H 'Content-Type: application/json' \
  -d '{"knobs":{"seed":2},"workspace":true}' \
  http://127.0.0.1:8002/v1/templates/your-id/runs
```

The workspace response gives an editable Studio draft; the run response gives
the journal-backed experiment. Keep the traffic accident demo as a reference
for named hooks and a complete report → award → upload event chain.
