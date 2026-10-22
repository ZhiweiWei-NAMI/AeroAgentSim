# Preparation plugins (API v1)

`aeroagentsim.preparers` is the entry-point family for deterministic scenario
preparation **after the final seed is selected and before generic compilation**.
Import its companion contracts from
[`aeroagentsim.preparation_api`](../../src/aeroagentsim/preparation_api.py).
`PREPARATION_API_VERSION = 1` is independent of scenario document v1/v2,
`aeroagentsim.scenarios`, package releases, engine APIs and journal versions.

Preparers are trusted Python, **not a sandbox**. Use only authored data, the
resolved seed and supplied immutable resource bytes. Do not call providers,
install dependencies, start processes/servers/engines, write files, consult wall
time or use unseeded/global randomness. Use a local seeded RNG and stable ordering
when sampling. Validation detects some contract violations; it does not prove
purity or prevent arbitrary Python side effects.

## Select preparation in scenario/v2

V2 requires the explicit selector below. This is a fragment, not a complete
scenario; retain the registry, run and other required [scenario fields](scenario.md).

```yaml
format: aeroagentsim.scenario/v2
preparation:
  plugin: explicit
  api_version: 1
  config: {}
```

| Selector field | Meaning |
| --- | --- |
| `plugin` | Nonempty discovery name in `aeroagentsim.preparers`; unknown names fail. |
| `api_version` | Literal integer `1`; `true`, `1.0`, strings and unsupported versions fail. |
| `config` | Required mapping, validated by the selected preparer; malformed/unsupported config must fail rather than be silently rewritten. |
| `resources` | Optional mapping of nonempty resource aliases to nonempty local path references; see below. |

Unknown selector keys are rejected. Builtins are:

| Name | Revision / behavior |
| --- | --- |
| `explicit` | `explicit/v1`: identity preparation with empty config. Rejects top-level `algorithms` and `generators`; does not infer lowalt expansion from engine names or metadata. |
| `lowalt-legacy` | `lowalt-legacy/v1`: empty-config compatibility adapter for historical lowalt shorthand, cooperation and embedded rescue expansion, including its city/class policies. May also be explicitly selected by v2. |

A v1 document cannot contain `preparation`. It always uses the **fixed builtin**
`lowalt-legacy/v1` adapter without installed-preparer discovery, even if an
installed distribution replaces that discovery name. This preserves historical
v1 admission rather than activating installed code from an old document. V2
explicitly opts into discovery and its override rules.

The generic loader owns registry/schema/binding/clock admission, not lowalt,
city or vehicle-class policy dispatch. Selecting a preparer neither builds an
engine nor grants runtime write authority. Engine configurations still pass their
normal validation and ownership contracts.

## Public contract

| Surface | Contract |
| --- | --- |
| `PreparerPlugin(name, prepare, revision, api_version=1, validate_config=..., entry_point=None, distribution=None)` | Frozen descriptor. Names match `[a-z0-9][a-z0-9_-]*`; revision is nonempty text. `prepare` and `validate_config` must be synchronous callables. Discovery populates entry-point/distribution identity. |
| `validate_config(config) -> None` | Validate a detached config mapping without mutating it. The default validator rejects every nonempty config. A non-`None` return or detected mutation is an error, not normalization. |
| `prepare(authored_document, final_seed, read_only_resources) -> PreparedScenario` | Return new detached output. Do not mutate or retain host inputs. V2 config is available in `authored_document["preparation"]["config"]`. |
| `ReadOnlyResources` | `Mapping[str, bytes]`; the driver passes an immutable mapping snapshot with actual `bytes` values, not filesystem handles or permission to read arbitrary files. |
| `PreparedScenario(document, contribution_selections=(), resource_manifest={})` | Frozen result envelope containing a detached document, a tuple of detached selection mappings and a detached digest mapping. Nested dictionaries are detached, not deeply immutable. This is not a compiled `Scenario`, registry or run session. |
| `resource_manifest(resources)` | Return every supplied alias, sorted by name, mapped to `"sha256:" + sha256(actual_bytes).hexdigest()`. The returned result must match this manifest exactly. |
| `document_digest(value)` | `sha256:` digest of sorted, compact, finite UTF-8 JSON with `ensure_ascii=False` and no terminal newline. Not a source-code digest. |

A preparer's document must preserve the admitted input `format`, the final
`run.seed`, and, for v2, the entire explicit `preparation` selection. It cannot add
unsupported top-level fields. Output and additional selections must be finite,
JSON-compatible data. Generic compilation subsequently validates the executable
scenario; successful preparation alone is not successful engine admission.

Additional `contribution_selections` are declarative evidence, not implicit plugin
activation. Each selection requires nonempty `family` and `name`; duplicate
`(family, name)` pairs, including a conflict with the driver's own preparer
selection, fail. They do not grant bindings, install plugins or bypass family
configuration validation.

Bump the preparer's `revision` when implementation semantics change and publish
compatible distribution/resource versions. A revision or implementation name is
not a hash of its Python source.

## Register an external preparer

An entry point exports a `PreparerPlugin` value or a zero-argument factory
returning one. For example:

```toml
[project.entry-points."aeroagentsim.preparers"]
acme = "acme_preparation:plugin"
```

`acme_preparation.py` can start with this identity implementation and add its own
pure elaboration. It does not demonstrate a domain model or generate measurements:

```python
import copy

from aeroagentsim.preparation_api import (
    PreparedScenario,
    PreparerPlugin,
    resource_manifest,
)


def prepare(authored_document, final_seed, read_only_resources):
    document = copy.deepcopy(authored_document)
    # Add deterministic domain elaboration here, using final_seed and supplied
    # bytes only. Keep format, preparation selection and final run.seed intact.
    return PreparedScenario(
        document,
        resource_manifest=resource_manifest(read_only_resources),
    )


def plugin():
    return PreparerPlugin(
        name="acme",
        prepare=prepare,
        revision="acme/v1",
        api_version=1,
    )
```

Select it with `plugin: acme`, `api_version: 1`, `config: {}` in a v2 document.
For nonempty config, provide a strict `validate_config` that rejects unsupported
keys/types and returns `None` without mutation. Declare the literal API version
implemented; do not copy the host's current constant and accidentally claim
compatibility with a future version.

### Discovery and overrides

`discover_preparers()` traverses the sorted builtin/installed union. An installed
name replaces the **whole** same-name builtin descriptor; load/factory/version
failures do not fall back to that builtin. Duplicate installed names fail before
any entry-point load. Wrong exported types, mismatched entry-point/plugin names
and factories that cannot be called without arguments also fail.

Discovery loads exports/factories but does not call `prepare`. Ordinary plugin
imports/factories must therefore avoid execution side effects too. Discovered
descriptors retain the entry-point reference and, when available, distribution
`(name, version)` identity. These are evidence, not additional activation.

The current implementation isolates rules mirrored from metrics discovery in
`preparation_api.py`. A shared discovery helper is not yet used; migration to it
is planned, not an existing cross-family guarantee.

## Resource bytes and manifests

Before invoking a preparer, the host acquires read-only input bytes:

- An authored `registry.snapshot` automatically supplies the alias
  `"registry.snapshot"`.
- V2 `preparation.resources` adds aliases for other declared files. An alias
  cannot collide with the automatically supplied snapshot alias.
- Paths resolve relative to the document base and support the existing
  `${ENV_NAME}` source-path substitution. This is not a path sandbox; trusted
  callers remain responsible for their allowed filesystem scope.

For example, add this optional selector member when the preparer consumes that
file:

```yaml
preparation:
  plugin: acme
  api_version: 1
  config: {}
  resources:
    district: inputs/district.json
```

The mapping passed to `prepare` contains the captured bytes, not these path
strings. A direct `prepare_scenario` caller may instead supply
`read_only_resources`; its keys must match **exactly** the names declared by the
document and every value must be actual `bytes`. The driver snapshots the mapping
and computes its own expected manifest; a plugin's claimed hashes, missing aliases
or extra aliases are not accepted as evidence of the supplied bytes. Use
`resource_manifest(read_only_resources)` in the result, including when it is empty.

The manifest covers **preparation inputs only**. It is not a complete runtime
asset inventory, compiled-registry digest, proof of later file reads using the
same bytes, package lock or authentication. Generic compilation still resolves its
own registry/behaviour inputs; coordinate/pin those resources separately. In
particular, `registry.compile` runs after preparation and `registry.digest` remains
an accepted legacy claim that is **not newly enforced** by these hashes.

## Driver and final-seed ordering

The driver is
[`aeroagentsim.scenario.preparation.prepare_scenario`](../../src/aeroagentsim/scenario/preparation.py):

```python
def prepare_scenario(
    authored_document,
    final_seed=None,
    read_only_resources=None,
    *,
    base=None,
) -> PreparedScenario: ...  # No registry compilation or engine startup.
```

It deep-copies authored input, admits the document version/closed keys, resolves
and validates the final seed, validates config, snapshots declared resources,
then invokes preparation. It rejects detected input mutation, wrong result types,
changed format/selection/seed, incorrect resource manifests and conflicting
selection identities. Driver admission errors are reported as `ScenarioError`;
arbitrary plugin exceptions can also propagate. There is no automatic preparer
fallback or fabricated successful result.

`final_seed=None` uses authored `run.seed`; `final_seed=0` overrides it with zero.
The seed must be an actual integer ≥ 0: booleans, numeric strings, floats and
negative values fail before sampling. Seed selection must happen before
preparation; replacing only a compiled scenario's seed cannot regenerate already
sampled entities or rescue fleet profiles.

Use the loader for full admission, or a template's authored input for a new preview:

```python
from aeroagentsim.scenario import load_scenario

scenario = load_scenario(document, base=source_directory, final_seed=0)
# With a PreparedTemplate from TemplateRegistry.apply(...):
preview = template.prepare(final_seed=0)
```

`PreparedTemplate.document` remains authored and knob-applied. Registry application
validates a disposable preview at its authored seed; `.prepare()` returns a fresh
`Scenario` without replacing that document. Existing experiment cells similarly
set seed/horizon on a fresh authored copy before loading. Always restart from
that authored input, not a previously expanded preview.

## Evidence and persistence boundary

`load_scenario` retains a detached `PreparedScenario` on `Scenario.preparation`.
Its driver-added first selection records the preparer family/name/API/revision,
entry point, distribution when available, implementation name, config digest,
authored-document digest, final seed, resource-manifest digest and contribution
digest. The authored digest precedes any final-seed override; the contribution
digest covers preparation output and the preparer's additional selections/resource
manifest, not later generic compilation.

`Scenario.document` is a separate copy used for further behaviour, registry and
binding admission. No contribution-evidence keys are injected into legacy v1
scenario documents. V2 retains its authored selector and format. Selection evidence
is retained **in memory**; common durable run-lock wiring is a planned follow-up,
not an artifact written by this API. Existing runtime-document/registry persistence
is unchanged; see [run artifacts](scenario.md#run-directory-artifacts).

Historical [run reading](run-reader.md) is unchanged and does **not** invoke
preparers, engines or providers. Loading an authored scenario for a new run is a
different operation from reading recorded evidence. A recorded configuration or
an in-memory selection does not by itself capture live external inputs or prove
that a historical environment can be recreated.
