# Consumption contributions and environment profiles

Kinematic integration, command admission and logistics route budgeting share one
selected consumption model instance. Models compute joules; their host engine
publishes facts and keeps the kernel's single writer per field invariant. An
inspection pack samples committed poses and has no separate energy formula: its
scenario's motion admission and integration use this same interface.

## Model contract and registration

`aeroagentsim.models` entry points register factories with this signature:

```python
from aeroagentsim.models import ConsumptionModel, ModelBuild

def build(context: ModelBuild) -> ConsumptionModel:
    return MyConsumption(context)
# Distribution metadata: aeroagentsim.models / my_model = my_package:build
```

`ModelBuild` carries model `config`, host `energy` configuration, the selected
registry and entity references. The returned object implements:

```python
@property
def dependencies(self) -> tuple[str, ...]: ...
def consumption(self, ctx, ref, segment) -> float: ...
def budget(self, ctx, ref, segments: tuple[Segment, ...]) -> float: ...
```

`Segment(displacement, elapsed_s, distance_m)` carries an ENU three-vector in
metres, seconds, and actual travelled path length in metres. Path length can
exceed displacement magnitude. Results must be finite, nonnegative joules.
Models must be deterministic and read through `ctx.get(ref, field)`, declaring
those fields in `dependencies`. The host adds dependencies to its partition;
reads automatically attach the committed fact versions to produced facts and
receipts. Reads before `super().step` or `super().on_inputs` are retained too.
Models compute contributions without changing command state or publishing facts.

`Simulation` allocates `EngineBuild.models[motion_engine_id]` before constructing
engines. Each `MotionModel` carries trajectory speed, acceleration, grid step and
the shared consumption object. Logistics resolves its command target's object,
including its weather dependencies. Its approach, transport, return, dwell and
polling allowance use that object; it adds the pack's declared reserve policy.
Historical duplicate calibration keys in existing scenario YAML remain for
manifest compatibility; a kinematic target's model supplies the actual budget.
A native adapter's authored planning calibration is separate from measured native
energy; this interface does not replace that adapter's native command admission.

## Selection, units and bounds

The following fragment belongs under a kinematic engine's `config`:

```yaml
energy: {capacity_j: 100000.0, idle_w: 20.0, per_m_j: 5.0}
frame: {convention: enu, unit: m, transform_revision: local-frame/v1}
consumption_model:
  plugin: wind
  config:
    entity: wind
    field: oo:digital_twin.wind.horizontalVelocity
    vector_index: 0
    coefficient_w_per_m_s2: 0.5
reserve_j: 100.0
```

Without `consumption_model`, the original linear model and operation order apply.
`linear` requires empty model `config` and computes `idle_w * seconds + per_m_j *
distance`; its budget aggregates segments before multiplying. `wind` adds
`coefficient * exposure² * seconds`, with exposure `max(0, -wind · direction)`
when moving and the wind norm when stationary. Tailwind and crosswind have no
additional moving cost or energy credit. Ground-track position and velocity do
not drift with wind.

Energy is J, idle power W, distance m, speed and wind m/s, acceleration m/s²;
the wind coefficient is W/(m/s)². Capacity, speed and acceleration must be
positive; consumption coefficients and reserves are finite and nonnegative.
Custom models need `capacity_j`; they may put other calibration parameters in
their own model `config`, without supplying linear coefficients.

All vectors must already be in the scenario's ENU frame. `transform_revision`
identifies the authored transform; models do not perform coordinate or unit
conversion. AeroGraph's `oo:WindField` field
`oo:digital_twin.wind.horizontalVelocity` contains nested horizontal two-vectors.
`vector_index` explicitly selects a sample; this horizontal model ignores vertical
wind, rather than claiming to measure it as zero. Flat ENU two- or three-vectors
can also be bound. Selecting a sample is not spatial interpolation.

Position and velocity remain one trajectory writer because this analytic model
maintains their coupled private trajectory state. Energy may belong to an
independent engine: Kinematic then declares no energy output, reads that writer's
actual joule facts for admission and exhaustion, and does not integrate energy.
An energy producer that reads motion may need positive dependency lag to satisfy
the kernel's cycle rules; optional `energy_lag_ns` declares Kinematic's energy
read lag (default zero). Lag is part of the physical sampling contract.

`reserve_j` defaults to zero. Insufficient predicted cost plus reserve rejects the
command through its typed result schema. When cost fits but the reserve would
be violated, the receipt reason is `energy reserve would be violated`.

## Environmental producer

The generic `environment` engine preserves the selected field schemas, including
AeroGraph nested vectors. Example engine entry:

```yaml
weather:
  plugin: environment
  config:
    produces: [oo:digital_twin.wind.horizontalVelocity]
    lifecycle: true
    profiles:
      wind:
        oo:digital_twin.wind.horizontalVelocity:
          mode: gust
          value: [[0.0, 0.0]]
          gusts:
            - {start_ns: 1000000000, end_ns: 2000000000, value: [[-10.0, 0.0]]}
```

Bind the `wind` entity's field writer and lifecycle controller in the scenario's
manifest. `calm` and `constant` retain an explicitly supplied typed `value`;
`gust` uses that base between nonoverlapping, half-open intervals. Timers publish
changes at their actual boundaries and retain timer causes, without polling.
Initial facts are optional: the profile is the generating source. If an initial
fact is supplied, it must match the effective profile at time zero. No missing
weather is interpreted as calm, and no null or unknown reading becomes zero.

## Calibration limits and compatibility evidence

The wind model is an illustrative exposure penalty, not calibrated aerodynamics,
propulsion, turbulence or a forecast. Budgets use the currently latched weather
sample. A later gust can invalidate an admitted reserve; integration uses actual
latched samples and can exhaust energy. Align profile boundaries to the motion
grid for resolved exposure, or quantify discretization error. Neither current
weather budgeting nor the coefficient guarantees sufficient energy for arbitrary
future weather. Measure coefficients and validate the chosen operating envelope
before interpreting energy as a vehicle prediction.

Under the same CPython 3.11 kernel and advance schedule, full journals before and
after this change are byte-identical with no new model selected:

| Scenario | Journal bytes | SHA-256 (before = after) |
| --- | ---: | --- |
| p1-slice | 8,889,017 | `0561b4b01858148bb4d249a38d8aa301561d5a0187ed18219cc4a63cc86a9a4d` |
| logistics-small | 39,653,994 | `43c79696556ac1b356a411dfcd2eed44245c3c7ce5ba1fe11ac6d22f2f838144` |

Full journals, comparison script and exact gate logs reside in `/tmp/aas-q/q5/`.
