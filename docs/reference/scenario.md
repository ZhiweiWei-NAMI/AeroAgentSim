# Scenario reference

A scenario is the single authored input to the runtime. The loader in
[`src/aeroagentsim/scenario/loader.py`](../../src/aeroagentsim/scenario/loader.py)
compiles it into a pinned registry, a binding manifest, and engine configs
before any kernel or engine starts. This page lists the accepted fields and
shapes. Authoring errors are reported with their paths at load; engine and external-service errors can also occur during execution.

## Top-level document

Format identifier: `format: aeroagentsim.scenario/v1` (exact string required).

| Key | Required | Shape / notes |
| --- | --- | --- |
| `format` | yes | Must equal `aeroagentsim.scenario/v1`. |
| `id` | yes | Nonempty string; scenario identity. |
| `registry` | yes | Mapping; exactly one of `snapshot` or `compile` (below). |
| `entities` | yes | List of `{id, type, facts}`; unique ids; `type` must be a registered type; each fact validated against its field schema and must apply to the type. |
| `bindings` | yes | `{lifecycle}` required; optional `rules`, `exact`, `commands`, `cohorts`, `relations`, `obligations`, `samples`, `cancel_sources`, `lifecycle_participants` (table below). |
| `engines` | yes | Mapping of engine id → `{plugin, config}`, optional `ingress`. Configs are plugin-specific. |
| `run` | yes | `{pacing, seed, until_ns, advance_ns}`; `pacing` is `fast` or `realtime`; seed/`until_ns` are integers, `advance_ns >= 1`. |
| `outputs` | yes | `{durability}`; `flush` or `fsync`. |
| `presentation` | no | List of viewer bindings; see below. |
| `origin` | no | `{lat, lon, alt}`; required when any presentation uses `wgs84`. |
| `clock_mappings` | no | List of `{mapping_id, clock_id}` + optional `offset_ns, p, q, rounding`. Must retain the identity `canonical` mapping. |
| `ingress_streams` | no | Named stream declarations; see below. |
| `behaviours` | no | Behaviour-package specs; requires exactly one `behaviour` engine. |
| `provenance` | no | `lean` (default) or `full`. |

Unknown top-level keys are rejected. The YAML loader is YAML-1.2-style for
booleans (`yes`/`no`/`on`/`off` are strings, `true`/`false` are booleans),
rejects duplicate keys and non-string mapping keys, and rejects any
non-finite numeric value anywhere in the document.

## Numeric strictness

All numeric validation is type-exact: `type(value) is int` for integer slots
(no `1.0`, no numeric strings) and finite `int|float` for numeric slots.
Integer fields validated this way include `run.seed`, `run.until_ns`,
`run.advance_ns`, `bindings.commands[].at_ns`, ingress watermarks and
`allowed_lateness_ns`. Durations expressed in predicate parameters must map
exactly to nonnegative integer nanoseconds (see
[predicate dialect](predicate-dialect.md)).

## Registry section

| Key | Shape |
| --- | --- |
| `snapshot` | Path to a registry snapshot (relative to the scenario file). Snapshots work without a private AeroGraph checkout; the run pins the compiled types/fields/messages/relations it contains. |
| `compile` | `{root, types}` + optional `fields, relations, policy`; compiles from an AeroGraph source tree at load. |
| `types[]` | `{id, parents, abstract}` with an explicit bool `abstract`. |
| `fields[]` | `{id, type, schema, metadata}`; `type` is the declaring type id. |
| `messages[]` | `{id, kind, schema}` + optional `result_schema, feedback_schema, cancel_support, subjects`. |
| `message_subjects` | Mapping of message id → subject bindings (see [viewer feed](viewer-feed.md)). |
| `relations[]` | `{id, source_type, target_type, targets_per_source, sources_per_target, identity_policy, metadata}`; cardinalities are `{minimum, maximum}` and may not weaken a compiled source relation. |
| `field_metadata` | Per-field overlay; only `frame` (`enu`, `ned`, `wgs84`) and `transform_revision` may be added. |

## Bindings

Binding sections are ownership declarations: they say which partition may write
which fields, relations and lifecycles. Field ownership is single-writer; the
runtime rejects an initial fact with no declared writer binding.

| Section | Required keys | Optional |
| --- | --- | --- |
| `rules` | `writer, type, fields` | `ids` (glob), `priority` (int) |
| `exact` | `entity, field, writer` | — |
| `lifecycle` | `controller, type` | `ids`, `priority` |
| `commands` | `schema, target, at_ns, payload` | — (bootstrap commands) |
| `relations` | `writer, relation, type` | `ids` |
| `obligations` | `controller, relation, direction, type` | `ids` |
| `samples` | `context, partition, upstream, bindings, sources, clocks, parameters` | — |
| `cohorts` | list of lists of entity ids | — |
| `cancel_sources`, `lifecycle_participants` | string lists / type → string list | — |

## Engines, ingress and provenance

Each engine is `{plugin, config}`; `config` must be a mapping. Plugins are
registered via entry points (see `pyproject.toml`): `behaviour`, `predicate`,
`kinematic`, `environment`, `langgraph`, `capture`, `px4_gazebo`, `sumo`,
`ns3`, and the traffic-accident pack engines.

Ingress is declared either per engine (`engines.<id>.ingress`) or as a named
shared stream in `ingress_streams`:

| Key | Required | Notes |
| --- | --- | --- |
| `id` | streams only | Unique stream id; defaults to the engine id for engine-declared ingress. |
| `engine_ids` | streams only | Unique nonempty engine ids; repeated declarations merge consistently. |
| `mapping_id` | yes | Must reference a declared clock mapping. |
| `initial_watermark_ns` | yes | Integer ≥ 0. |
| `lateness` | yes | `reject` or `delay`. |
| `allowed_lateness_ns` | no | Integer ≥ 0. |
| `timeout_s` | no | Positive number; if the wait budget expires the run ends `input_timeout`. |

`provenance` selects journal detail: `lean` (default) records committed
outcomes; `full` opts into richer per-item provenance. The scenario value is
the pinned default; a caller (CLI `--provenance`, API) may override it with an
explicit valid value.

## Presentation

Each entry is `{typeId, positionField, frame, visual}` + optional
`orientationField`. `positionField` must be a three-vector field applicable to
the type; `orientationField` a four-vector quaternion; `frame` is `enu`, `ned`
or `wgs84` and must not conflict with the field's declared frame. `visual` is
`{kind}` + optional `asset, scale, color`; `kind` is `marker`, `model` or
`label`, and `model` requires `asset`; `scale` must be positive.

## Behaviours

When `behaviours` is present, exactly one engine with `plugin: behaviour` must
exist. Specs are resolved relative to the scenario directory (or `path:`-referenced
files), and the compiled package types/fields/messages
overlay the registry. A binding rule and lifecycle rule for the behaviour
engine are appended automatically. See
[behaviour packages](behaviour-package.md).

## Run directory artifacts

A started run directory (`runs/<name>/`) contains `journal.jsonl` (the WAL),
`manifest.json` (status, ids), `index.json`, `scenario.yaml`/`scenario.json`,
`registry.snapshot.json` and `runtime.registry.json` — saved for this run. The journal is the execution record; configuration alone does not reconstruct live external inputs. See [HTTP API](http-api.md) and [CLI](cli.md).

## Example

The complete [minimal task scenario](../../scenarios/behaviours/minimal.yaml) is runnable without external services. Its entity and ownership declarations include:

```yaml
# Fragment; use the linked file for the complete package and registry.
entities:
- {id: task-1, type: example:Task, facts: {example.task.phase: queued}}
bindings:
  rules:
  - {writer: behaviour, type: example:Task, fields: [example.task.phase]}
  lifecycle:
  - {controller: behaviour, type: example:Task}
engines:
  behaviour:
    plugin: behaviour
    config:
      produces: [example.task.phase]
      capabilities: {}
```
