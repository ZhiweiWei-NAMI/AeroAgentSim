# Guide: Weather and environmental profiles

The `environment` engine publishes authored, time-varying environmental values
with exact DES boundaries. Source:
[../../src/aeroagentsim/engines/environment.py](../../src/aeroagentsim/engines/environment.py).

## Concept

An environment engine owns declared field IDs (single-writer field ownership
still applies) and publishes one of three authored modes per entity/field
pair:

- `calm` / `constant` — one explicit value for the whole run.
- `gust` — a base value plus disjoint half-open `[start_ns, end_ns)` intervals
  holding substitute values.

There is no integration loop or interpolation: the engine schedules a timer at
each interval boundary, publishes at actual boundaries with real timer causes,
and commits nothing between them. Unit, frame and schema authority stays in
the registry — every authored value is validated against the field descriptor
during engine construction, before advancement.

## Visual weather conditions (lowalt scenes)

Lowalt scenes may journal rain, visibility and time-of-day light as constant
profiles on the same `lowalt:Environment` entity that carries
`lowalt.wind`, via the assistant's `set_weather` operation:

- `lowalt.precipitation` (kg m⁻² s⁻¹; mm/h ÷ 3600),
  `lowalt.visibility` (metres; 50000 = clear), `lowalt.light`
  ("day" | "dusk"). They are declared in the lowalt overlay with
  `aerograph:` metadata pointing at the matching AeroGraph fields.
- Rain, visibility and light are **visual only** — shown in the 3D view and UAV
  captures; flight, sensing and task outcomes don't use them（降雨、能见度与光照
  仅为视觉呈现——显示在三维视图和无人机拍摄中；飞行、感知和任务结果不使用它们）.
  Wind and gusts are different: they are simulated inputs that drive flight
  holds and energy use (风与阵风是驱动飞行暂停和能耗的仿真输入).
  For fire incidents, smoke and flames are likewise visual only
  （火情的烟雾与火焰同样仅为视觉呈现）.
- `lowalt.light` is a journaled presentation field. The viewer follows its value
  at the viewed cut; the run header's `scene.lighting` echoes its initial value.
- `set_wind.from_deg` states the meteorological direction the wind comes FROM
  (clockwise from north); the compiler converts it to the ENU vector. Gusts
  keep the background direction.

## Authoring an environment engine

Configuration fragment (the wind region from the traffic-accident demo):

```yaml
engines:
  weather:
    plugin: environment
    config:
      produces: [oo:digital_twin.wind.horizontalVelocity]
      lifecycle: true
      profiles:
        wind.region:                       # entity ID
          oo:digital_twin.wind.horizontalVelocity:
            mode: calm
            value: [[0.0, 0.0]]            # list of horizontal wind vectors
bindings:
  rules:
  - writer: weather
    type: oo:WindField
    fields: [oo:digital_twin.wind.horizontalVelocity]
    ids: '*'
```

A gust profile for the same field:

```yaml
profiles:
  wind.region:
    oo:digital_twin.wind.horizontalVelocity:
      mode: gust
      value: [[0.0, 0.0]]
      gusts:
      - start_ns: 5000000000        # half-open [start, end)
        end_ns: 9000000000
        value: [[3.5, 1.2]]
```

Validation rules the loader enforces:

- config keys are exactly `produces`, `lifecycle`, `profiles`.
- `produces` is a nonempty list of distinct field IDs.
- each profile binds a real scenario entity, and the field must be one this
  partition writes (`EngineBuild.owned_fields`).
- gust intervals must be nonempty, nonnegative, ordered and non-overlapping;
  `0 <= start < end`.
- the entity's authored initial fact must match `value_at(0)`.
- every field in `produces` must have a profile for every entity it applies to.

## Consuming weather: wind energy model

Motion engines consume environment fields through a typed consumption model.
The demo's UAV engine (see
[../../scenarios/demos/traffic-accident/scenario.yaml](../../scenarios/demos/traffic-accident/scenario.yaml))
declares:

```yaml
consumption_model:
  plugin: wind
  config:
    entity: wind.region
    field: oo:digital_twin.wind.horizontalVelocity
    vector_index: 0
    coefficient_w_per_m_s2: 0.5
```

`WindConsumption` is registered in the
[../../pyproject.toml](../../pyproject.toml) `aeroagentsim.models` entry-point
group (`linear` and `wind` are the shipped models). The kinematic engine adds
this wind term to its explicit joule bookkeeping; energy is authored physics
accounting, not a claim about real aircraft consumption.

## Design notes and limits

- Weather is **energy_only** in the shipped kinematic demo profile: wind
  affects the authored energy budget, it is not injected as a physical force
  into native simulators. The PX4 demo profile lists native wind injection as
  explicitly unverified.
- Values change only at authored boundaries; a consumer sampling mid-interval
  reads the interval's value.
- The environment engine produces observation-role values; it does not model
  atmosphere propagation, precipitation or sensor effects. Extend with your
  own plugin via the `aeroagentsim.engines` entry point if you need that
  (see [plugins guide](plugins.md)).
- Adverse-weather policy reactions (for example slowing traffic) are authored
  behaviour, not automatic: wire a predicate over the weather field in a
  behaviour chain (see [behaviours guide](behaviours.md)).

## Minimal runnable scene

The predicates demo
[../../scenarios/predicates-demo.yaml](../../scenarios/predicates-demo.yaml)
uses an `environment` engine to publish a speed profile with a gust, and a
`predicate` engine to emit an event when a predicate enters — a compact
template for environment + derived-event wiring.

See also: [predicates guide](predicates.md), [external events](external-events.md).
