# Guide: Writing engine plugins

An engine plugin is a factory callable that receives an `EngineBuild` and
returns any kernel `Engine`. Discovery and construction live in
[../../src/aeroagentsim/platform/plugins.py](../../src/aeroagentsim/platform/plugins.py)
(`EngineCatalog`, `EngineBuild`). Read
[concepts/plugins](../concepts/plugins.md) for the model first.

## Registering a plugin

Register a factory in the `aeroagentsim.engines` entry-point group. The
shipped registrations in [../../pyproject.toml](../../pyproject.toml):

```toml
[project.entry-points."aeroagentsim.engines"]
behaviour = "aeroagentsim.engines.behaviour:build"
environment = "aeroagentsim.engines.environment:build"
predicate = "aeroagentsim.engines.predicate:build"
kinematic = "aeroagentsim.engines.kinematic:build"
# ... plus adapters, packs and agents modules
```

Catalog behaviour:

- Installed entry points take precedence over builtins with the same name;
  duplicate installed names are rejected.
- Catalog discovery never constructs an engine or starts an external
  simulator — `factory()` only imports the declared callable.
- An installed distribution needs no platform changes to become discoverable.
  For a scenario-local plugin you can also inject a real `EntryPoint` into a
  catalog's `entries` collection (the adapter runner does this in
  [../../src/aeroagentsim/adapters/__init__.py](../../src/aeroagentsim/adapters/__init__.py));
  no global catalog mutation.

## The EngineBuild contract

Your factory receives an `EngineBuild` with:

- `id`, `config` — engine ID and the scenario `config` mapping (validate it
  yourself; strict validation is your responsibility).
- `registry` — the compiled `MemoryRegistry`; use `registry.field(id)` for
  descriptors and `registry.validate(schema, value)` for payload checks.
- `manifest` — the kernel binding manifest (run/epoch, writer rules).
- `entities`, `initial` — scenario entities and authored initial facts.
- `partitions`, `models`, `run_directory` — previously built partitions,
  motion models and the run output directory (may be `None`).

Two helpers matter for authority:

- `build.writers(ref)` resolves authored instance selectors to partition
  names for each field slot — this is how ownership is decided before the
  kernel validates your declarations.
- `build.owned_fields(ref)` returns the field slots your plugin writes for an
  entity. Reject any configured output field not in this set.

Declare a `Partition` with `produces` (fields you write), `consumes`,
`commands`, `emits`, `message_targets`, lifecycle flag and `Timing`
(`lockstep`, `real_time`, or implicit DES). Field ownership is single-writer:
the kernel rejects a second writer for an owned slot.

## Implement the engine hooks

A factory returns an engine whose partition declarations match the scenario bindings. The [environment engine](../../src/aeroagentsim/engines/environment.py) is a complete implementation: it validates every configured field and value, initializes owned facts and schedules authored boundaries without polling.

This constructor/bootstrap **fragment** illustrates the same pattern; a complete class must validate `fields`, define its model and implement its relevant hooks:

```python
from aerokernel import Partition
from aerokernel.sdk import ContextEngine, EngineContext
from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.platform.plugins import EngineBuild

class MyEngine(ContextEngine):
    def __init__(self, build: EngineBuild, fields: tuple[str, ...]) -> None:
        self.build = build
        super().__init__(
            Partition(build.id, build.id, produces=fields, lifecycle=True),
            policies=policies(fields),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)
```

The canonical policies explicitly stamp acquisition at the current simulation time and retain validity until replacement. Choose different policies when your source has its own acquisition clock or validity interval. `bootstrap_owned` creates controlled entities and publishes their authored initial facts; it does not generate missing model values.

Implement `step(ctx)` for integration or internal DES work and `on_inputs(ctx)` for events, dirty fields and commands. Publish through `ctx.set`, schedule actual boundaries with `ctx.wake_at`, and define command handlers and receipts through the SDK. Inspect the [kinematic engine](../../src/aeroagentsim/engines/kinematic.py) for motion/receipts and the [telemetry engine](../../src/aeroagentsim/engines/telemetry.py) for live ingress. Test observable state, completion and failure behaviour in a small complete scenario before using the plugin in a larger study.

## Optional configuration schema

Expose a portable JSON-schema-like object as `config_schema` on the factory
so Studio renders form controls:

```python
build.config_schema = {
    "type": "object",
    "properties": {
        "source_field": {"type": "string", "title": "Source field"},
        "interval_ns": {"type": "integer", "minimum": 1},
        "mode": {"type": "string", "enum": ["sample", "continuous"]},
    },
    "required": ["source_field", "interval_ns"],
}
```

It is data, not a callback: root `type: object`, finite mapping, no defaults
invented. The catalog returns it unchanged as `engines[].config_schema`; your
factory still validates the real configuration. The full scenario YAML editor
and raw-JSON configuration editor remain available regardless.

## Protocol plugins (native simulators)

For external simulators, follow the adapter pattern in
[../../src/aeroagentsim/adapters/](../../src/aeroagentsim/adapters/):
`lockstep_base.LockstepEngine` provides the lockstep timing contract
(hello/reset/advance/command over a strict JSON protocol, exact boundary
confirmation, no stateful retries, fault on mismatch). Register your factory
the same way and see the [containers guide](containers.md) for packaging the
service.

## Checklist

- [ ] Factory registered under `aeroagentsim.engines`, callable, import-safe.
- [ ] Strict config validation with clear `ValueError` paths.
- [ ] Only owned fields written; `writers()`/`owned_fields()` checked.
- [ ] Partition declares `produces`/`commands`/`emits` honestly.
- [ ] No hidden retries of stateful operations; typed failures instead.
- [ ] Deterministic ordering (no wall-clock in the DES path).

See also: [behaviours guide](behaviours.md) for consuming your engine from
chains, [agents guide](agents.md) for granting it commands.
