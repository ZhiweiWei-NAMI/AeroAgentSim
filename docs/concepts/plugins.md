# Plugins

Every capability in a run is a **plugin engine** built from a factory and
bound into the one kernel by the scenario. Engines own fields, submit typed
commands, emit typed events and receive kernel receipts. The kernel remains
the sole authority for identities, state history, time, delivery and
receipts.

## Discovery and builtins

Distributions register factories in the `aeroagentsim.engines` entry-point
group; installed entries take precedence over builtins with the same name,
and duplicate installed names are rejected. The builtins
(`src/aeroagentsim/platform/plugins.py`) include:

| Plugin | Role |
| --- | --- |
| `kinematic` | Selectable ENU point-mass physical owner (motion, energy) |
| `environment` | Authored environmental producer (e.g. weather fields) |
| `behaviour` | Compiled event-chain runtime |
| `predicate` | Sampled AeroGraph predicate evaluation |
| `records`, `telemetry`, `ingress-consumer` | Record keeping, live observation fields, stream echo |
| `decision`, `langgraph`, `agent-assignment` | Model-driven and rule-driven agent decisions (see [Agents](agents.md)) |
| `logistics`, `inspection`, `capture`, traffic packs | Domain packs (routes, budgets, camera capture) |
| `sumo`, `px4_gazebo`, `ns3` adapters | Native simulator adapters (`src/aeroagentsim/adapters/`) |

A factory may expose a portable `config_schema` attribute (a finite JSON
mapping) so the console can render configuration forms; factories remain
responsible for validating real configuration.

## Single-writer field ownership

Each `(entity, field)` has exactly one writer at a time, declared in the
scenario `bindings.rules` and resolved against engine partitions at load.
Key consequences:

- A physical move is `command(owner, schema, payload)` followed by receipts
  and observed-state guards — never a direct `set(position)` by an
  orchestration engine.
- Business and physical engines can own different fields of the same entity.
- Behaviour `set` actions are validated against `EngineBuild.owned_fields`;
  creating/removing entities requires lifecycle authority, asserting
  relations requires relation-writer authority.
- A replacement profile (e.g. kinematic → SUMO) is selected at **run
  creation**, then consumers recompile. There is no runtime writer hot swap:
  changing profiles during a run means a new run/epoch with explicit initial
  conditions.

## Plugin contracts

| Contract | Meaning |
| --- | --- |
| Configuration descriptor | Versioned config; field slots, input dependencies, lifecycle domains, supported commands/results/events, cancellation support. Advertised availability is not readiness. |
| State supply | Exact fields, units/frames, source clocks/mappings, validity and acquisition policy. No fabricated samples during holds. |
| Control responses | Typed submitted/accepted/executing/terminal receipts; success states its actual completion criterion. |
| Environmental inputs | Explicit input fields and latching/coupling policy; weather never implicitly drives another simulator. |
| Coordination | Partition timing mode: DES, fixed-step, lockstep or real-time streams. Every real-time engine needs a declared ingress stream. |
| Reproducibility | Native/plugin revisions, seeds, calibration and external inputs recorded; close/cleanup errors affect run status. |

A physical adapter's authored calibration is separate from measured native
energy or telemetry; the platform does not normalize SUMO's XY planar output
into ENU or PX4's battery record into joules — transforms need separate,
explicitly owned producer fields.

## Models

Consumption/energy models (`src/aeroagentsim/models.py`) are a separate
entry-point family. A model computes joules deterministically from declared
input fields; its **host engine** publishes the facts, preserving the
single-writer invariant. See [Reference: CLI](../reference/cli.md) and the [weather guide](../guides/weather.md) for a model configuration example.

## External engines

SUMO, PX4/Gazebo and ns-3 run in separate containers with host adapters
(`src/aeroagentsim/adapters/`). Generic native adapters work when the
separate scenario is correctly configured. The shipped demo profiles for
SUMO/PX4 are currently **configuration contracts**, not runnable native
replacements — selecting them reports exactly what configuration is
required. See [guides: containers](../guides/containers.md),
[SUMO](../guides/sumo.md) and [PX4/Gazebo](../guides/px4-gazebo.md).

## Where to go next

- [Architecture](architecture.md) — how plugins fit the kernel.
- [Behaviours](behaviours.md) — the orchestration engine's ownership rules.
- [Plugins guide](../guides/plugins.md) — writing your own engine.
