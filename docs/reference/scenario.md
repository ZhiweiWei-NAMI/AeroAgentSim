# Scenario reference

A scenario is the single authored input to the runtime. The loader in
[`src/aeroagentsim/scenario/loader.py`](../../src/aeroagentsim/scenario/loader.py)
resolves the final seed, prepares the document, then compiles its registry,
binding manifest and engine configs before any kernel or engine starts.
Authoring errors are reported at load; engine and external-service errors can
also occur during execution.

## Top-level document

The exact `format` string selects preparation admission; it is independent of
the preparation plugin API version:

| Format | Preparation selection |
| --- | --- |
| `aeroagentsim.scenario/v1` | Existing closed field set and fixed builtin `lowalt-legacy/v1` compatibility. No `preparation` key or installed-preparer selection. |
| `aeroagentsim.scenario/v2` | Requires `preparation: {plugin, api_version: 1, config: {}}`; optional `preparation.resources` declares additional input files. Selection uses the `aeroagentsim.preparers` family. |

See [preparation plugins](preparation-plugins.md) for the callable contract,
resource bytes and evidence boundaries. The following fields describe the
prepared scenario; a preparer may elaborate authored shorthand into this shape.

| Key | Required | Shape / notes |
| --- | --- | --- |
| `format` | yes | Exact admitted v1 or v2 format above; preparation preserves it. |
| `preparation` | v2 only, required | `{plugin, api_version, config}` + optional `resources`; rejected in v1. Not an engine declaration. |
| `id` | yes | Nonempty string; scenario identity. |
| `registry` | yes | Mapping; exactly one of `snapshot` or `compile` (below). |
| `entities` | yes | List of `{id, type, facts}` + optional `label`, `label_zh`, `business`, `vehicle_class`; unique ids; `type` must be a registered type; each fact validated against its field schema and must apply to the type. Optional metadata fields, when present, must be nonempty strings and are exposed verbatim in the pinned scenario and the viewer header; nothing is invented for entities that omit them. |
| `bindings` | yes | `{lifecycle}` required; optional `rules`, `exact`, `commands`, `cohorts`, `relations`, `obligations`, `samples`, `cancel_sources`, `lifecycle_participants` (table below). |
| `engines` | yes | Mapping of engine id → `{plugin, config}`, optional `ingress`. Configs are plugin-specific. |
| `run` | yes | `{pacing, seed, until_ns, advance_ns}`; `pacing` is `fast` or `realtime`; seed/`until_ns` are integers ≥ 0, `advance_ns >= 1`. Authored `run` is required before preparation. |
| `outputs` | yes | `{durability}`; `flush` or `fsync`. |
| `presentation` | no | List of viewer bindings; see below. |
| `origin` | no | `{lat, lon, alt}`; required when any presentation uses `wgs84`. |
| `clock_mappings` | no | List of `{mapping_id, clock_id}` + optional `offset_ns, p, q, rounding`. Must retain the identity `canonical` mapping. |
| `ingress_streams` | no | Named stream declarations; see below. |
| `behaviours` | no | Behaviour-package specs; requires exactly one `behaviour` engine. |
| `provenance` | no | `lean` (default) or `full`. |
| `algorithms`, `generators` | no | Legacy pack shorthand interpreted by `lowalt-legacy`; the builtin `explicit` preparer rejects these keys. |
| `event_narratives` | no | Authored event display declarations; see [viewer feed](viewer-feed.md#kpis-and-business-narratives). |
| `city` | no | Display metadata; supported values are preparer/presentation policy, not generic loader dispatch. |

Unknown top-level keys are rejected. The YAML loader is YAML-1.2-style for
booleans (`yes`/`no`/`on`/`off` are strings, `true`/`false` are booleans) and
rejects duplicate keys and non-string keys in ordinary mappings. Special YAML
collections (`!!pairs`, `!!omap`, `!!set`) retain their SafeLoader semantics.

V1 retains historical YAML value admission in otherwise loadable fields; it is
**not JSON-only**. For example, `engines.calendar.config.start_date: 2026-07-13`
loads as a Python `datetime.date` and remains that type in the prepared/effective
document and engine config. Its historical finiteness walker checks dictionaries
and lists but does not descend into tuples or sets. Thus previously admitted
non-finite floats inside pairs/ordered maps or sets remain admitted; ordinary
numeric fields still require finite values. The **legacy v1 digest** is total over
SafeLoader values, using deterministic, type-tagged canonical evidence when finite
JSON encoding fails. Neither that digest nor `PreparedScenario` output validation
adds v1 admission restrictions or rewrites values.

V2 remains strict: authored and prepared documents must be finite, JSON-compatible
data, including nested values. Quote dates/timestamps (for example, `"2026-07-13"`)
to keep them strings. See [digest encoding](preparation-plugins.md#public-contract).

## Preparation and final seed

`load_scenario(source, *, base=None, final_seed=None)` loads a new run or preview.
`None` uses authored `run.seed`; an explicit `0` is a real override. The final
seed must be an actual nonnegative integer, not a boolean, and is resolved before
any preparer samples entities or expands configuration. For a mapping input,
`base` resolves resource paths; file inputs use their parent directory.

### Opt-in scenario/v2

For v2, select a preparer explicitly:

```yaml
# Fragment; retain the scenario's registry, entities, bindings, engines and run.
format: aeroagentsim.scenario/v2
preparation:
  plugin: explicit
  api_version: 1
  config: {}
```

The builtin `explicit` preparer is an identity, not lowalt inference. To opt into
the historical expansion policy in v2, select `plugin: lowalt-legacy` with the
same API version and empty config. V1 instead uses the fixed builtin adapter,
even if an installed entry point replaces that name for v2 discovery. Generic
compilation does not dispatch by lowalt plugin names, city or vehicle class;
pack-specific compatibility checks belong to the selected preparer.

V2 preparer discovery uses the shared `aeroagentsim.extensions.discovery.discover`
helper. Duplicate or malformed declarations are rejected before any provider is
loaded or any factory is called. Installed names replace whole same-name builtins,
without fallback; [discovery evidence and rules](preparation-plugins.md#discovery-and-overrides)
remain separate from engine activation.

[`TemplateRegistry.apply`](../../src/aeroagentsim/authoring/templates.py) validates
a disposable preview but returns a `PreparedTemplate` whose `.document` remains
the authored, knob-applied input. `.prepare(final_seed=0)` returns a new `Scenario`
without replacing that input. Prepare each replicate from the authored document;
changing only the seed of an already sampled preview does not regenerate its
fleet. Existing experiment cells apply seed/horizon overrides before loading.

The returned `Scenario.preparation` retains preparation output, contribution
selection evidence and resource hashes separately from `Scenario.document`.
Generic behaviour/registry compilation may elaborate the latter further. This
in-memory evidence is **not yet a common durable run lock**; see the
[evidence limits](preparation-plugins.md#evidence-and-persistence-boundary).
Historical run reading does not call preparers.

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
| `snapshot` | Path to a registry snapshot (relative to the scenario file). Snapshots work without a private AeroGraph checkout; existing run artifacts retain its compiled descriptors. Its input bytes are also supplied to preparation as `registry.snapshot`. |
| `compile` | `{root, types}` + optional `fields, relations, policy`; compiles from an AeroGraph source tree at load. Compilation is after preparation, not part of a preparer's resource manifest. |
| `digest` | Accepted legacy claim; currently **not compared** against the loaded/effective registry digest. Preparation resource hashes do not add enforcement of this field. |
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

## Fleet generators

The `lowalt-legacy` preparer expands the historical low-altitude fleet declaration
`generators.fleet` into concrete entities before generic validation. This policy
applies implicitly to v1 or by explicit v2 selection; it is not an expansion
performed by the builtin `explicit` preparer. Required keys are `count`, `prefix`,
`type`, `facts`, `starts`, `spacing_m`. Optional metadata keys are:

| Key | Notes |
| --- | --- |
| `label`, `label_zh` | Nonempty format strings; `{index}` (the only substituted field) is the 1-based position, e.g. `"Drone P{index}"` → `Drone P1`, `Drone P2`, … |
| `business` | Nonempty string copied verbatim to every generated entity. |
| `vehicle_class` | Nonempty string copied verbatim to every generated entity; it must agree with the authored `lowalt.vehicle_class` fact, which remains the authoritative motion/energy class. |

Generated entities carry `id` (`prefix` + 1-based index), `type`, the expanded
`facts`, and any provided metadata field. No metadata value is defaulted or
invented; an entity without the key stays without it in the pinned scenario and
the viewer header (`entities` array: `id`, `type`, and `label`/`label_zh`/
`business`/`vehicle_class` only when authored).

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
`registry.snapshot.json` and `runtime.registry.json` — saved for this run.
Existing source/effective-document persistence is unchanged: storage writes
`Scenario.source` to `scenario.yaml` and the runtime `Scenario.document` to
`scenario.json`. Host transformations such as capture-storage scoping can replace
the source, so the YAML is not a universal byte-for-byte copy of the original file.

Preparation does not currently persist a separate contribution/resource lock in
these artifacts; durable common-lock wiring is a follow-up. The journal remains
the execution record, and configuration alone does not reconstruct live external
inputs. See [HTTP API](http-api.md) and [CLI](cli.md).

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

The shipped presentation convention `city: huangpu` selects the shared city
display in local ENU metres. It does not change entity positions, field ownership
or simulation physics. The `lowalt-legacy` preparer retains the historical
`huangpu`-only check; the generic loader does not impose that pack policy.
See [visualization](../guides/visualization.md#shared-huangpu-city).

The same compatibility preparer requires authored `vehicle_class` to agree with
`facts.lowalt.vehicle_class` when both are present. This is a lowalt policy, not
selection of physics by generic metadata; other preparers must define their own
domain checks and still pass generic schema/ownership admission.

## Low-altitude operational rules

Lowalt templates declare `lowalt.rules` inline in `behaviours`, composed with
other packages under one behaviour engine. Template JSON pointers edit the
package's battery reserve, horizontal separation and per-class wind predicate
parameters. See [editing the rules](../guides/lowalt-rules.md) for their event
chains, committed detection fields and ownership. The battery recall flag
`lowalt.returning_to_charge` prevents new task assignments until the real
charging command finishes.
