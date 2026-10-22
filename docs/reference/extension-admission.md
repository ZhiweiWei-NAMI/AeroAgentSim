# Extension admission

This is the common admission reference for extension authors and new family
implementations. Installed extensions are **trusted Python**, not sandboxed data.
Admission does not grant kernel write authority or prove runtime readiness. See
[plugin contracts](plugin-contracts.md) for execution, ownership and determinism.

## Shared rules

The implementation is [`extensions/discovery.py`](../../src/aeroagentsim/extensions/discovery.py).
Scenario and metric discovery use it directly; the engine catalog uses its
metadata enumeration and lazy resolution without constructing engines.

1. **Enumerate once, before loading.** Collect every installed entry in the
   requested group. Two installed declarations with the same name are an error,
   even if they come from the same distribution or point to the same object.
   `DuplicateEntryPointError` is a `ValueError`; its diagnostic names the provider
   and all declaring distributions, with available versions and entry-point
   values. Unknown metadata remains `None`. No builtin factory or installed
   entry is loaded before ambiguity is rejected.
2. **Sort the union.** Resolve the sorted union of builtin and installed names.
   One installed entry replaces the **whole** same-name builtin provider; do not
   call the shadowed builtin or merge back its hooks, templates or reducers. An
   invalid installed replacement fails, with no fallback to the builtin.
3. **Validate the family's contract.** Versioned scenario/metric providers must
   have names matching `[a-z0-9][a-z0-9_-]*`, return the expected descriptor type,
   and have a name equal to their entry-point name. A zero-argument factory may
   return that descriptor. Require an actual integer `api_version=1`: booleans,
   floats, strings, `None` and unknown integer versions are rejected, not coerced
   or downgraded. Discovery rechecks names and versions even for descriptor
   instances. Legacy callable families are not retroactively versioned.
4. **Snapshot declarations, not runtime state.** `DiscoveryResult` is frozen;
   its names, loaded-provider sequence, evidence and conflict diagnostics are
   tuples. `ScenarioPlugin` snapshots its hook mapping read-only, template
   sources are frozen, and metric descriptors are frozen. Executable callbacks
   and reducer implementations are not deep-copied, made pure or sandboxed by
   discovery. Engine configuration-schema reads return detached normalized data.
5. **Reject contributed-ID collisions at composition.** Replacing one provider
   does not authorize overwriting another provider's IDs. `TemplateRegistry`
   rejects duplicate template IDs and hook keys; `MetricAccumulator` rejects
   duplicate metric IDs, including across different providers. These checks stay
   at composition because they require family-specific resources/descriptors.
   Engines contribute catalog names, not a second collection of versioned IDs;
   duplicate engine names fail at discovery, and registry/partition authority is
   separately validated by the kernel. Discovery never grants namespace takeover.

The existing scenario/metric discovery functions still return tuples with their
original signatures. Descriptor validation and factory failures retain their
`ValueError`/`TypeError` messages; duplicate-name errors retain the old message
prefix and append distribution evidence. Family API versions are independent:
scenario v1 does **not** version the engine/kernel Python ABI. Authors should
write literal `api_version=1`, not copy whichever host constant is installed.

## Family matrix

“Unversioned” means there is no descriptor `api_version` negotiation, not that
all package releases or implementations are compatible. Builtin sources and
installed-only engine declarations are linked below the table.

| Family | Entry-point group | Descriptor API | Builtin providers | Override / admission semantics |
| --- | --- | --- | --- | --- |
| Scenarios | `aeroagentsim.scenarios` | `ScenarioPlugin`, version **1** | `air-taxi`, `city-day`, `crop-spraying`, `flood-rescue`, `food-delivery`, `medical-delivery`, `parcel-delivery`, `powerline-inspection`, `security-patrol`, `traffic-accident`; hook-only `lowalt`, `traffic`, `rescue` | Shared discovery; whole-provider replacement; template/hook ID collisions rejected during composition. |
| Metrics | `aeroagentsim.metrics` | `MetricPlugin`, version **1** | `inspection`, `logistics`, `lowalt`, `tasks`, `traffic_accident` | Shared discovery; whole-provider replacement; reducer metric IDs must be disjoint. |
| Engines | `aeroagentsim.engines` | Unversioned callable `build(EngineBuild)` | `agent-assignment`, `behaviour`, `decision`, `environment`, `ingress-consumer`, `inspection`, `kinematic`, `langgraph`, `logistics`, `lowalt_airspace`, `lowalt_city_capture`, `lowalt_energy`, `lowalt_incident_capture`, `lowalt_tasks`, `predicate`, `records`, `telemetry`, `threshold`, `traffic_camera_capture`, `traffic_capture_bridge`, `traffic_decision_failures`, `traffic_decisions`, `workflow` | Shared enumeration/duplicate checks and lazy loading; installed factory replaces builtin; callability checked, no factory invocation until build. No descriptor-name or API-version requirement is invented. |
| Algorithms | `aeroagentsim.algorithms` | Unversioned slot protocols | Allocation: `nearest`, `auction`, `hungarian`, `llm`; path planning: `straight`, `corridor_astar`, `coverage`, `survey`, `patrol`; conflict resolution: `altitude_layers`, `hold_and_go`; charging: `threshold_return` | Runtime migration deferred: qualified `<slot>.<name>` entry wins over unqualified installed name, then `module:Class` or builtin; constructors receive configuration. Duplicate installed names fail. |
| Consumption models | `aeroagentsim.models` | Unversioned `ConsumptionModel` protocol | `linear`, `wind` | Runtime migration deferred: installed factory replaces an explicitly selected name; duplicates are rejected only for the selected name. A legacy scenario without explicit `consumption_model` retains builtin linear behavior. |
| Assistant operations (**planned; absent in this revision**) | Proposed `aeroagentsim.assistant_operations` | Planned independent version 1 | No discoverable providers | No admission or override API shipped here. Internal assistant operation tables are not this family. |
| Presentations (**planned**) | Proposed `aeroagentsim.presentations` | Not shipped | No discoverable providers | Existing scenario presentation data does not establish a discoverable provider family. |
| Registry overlays (**planned**) | Proposed `aeroagentsim.registry_overlays` | Not shipped | No discoverable providers | Existing inline descriptors/snapshots do not establish an overlay plugin admission API. |

Sources: [scenario builtins](../../src/aeroagentsim/scenarios/__init__.py),
[metrics](../../src/aeroagentsim/metrics_api.py),
[engine catalog](../../src/aeroagentsim/platform/plugins.py) and its
[import-light builtin declarations](../../src/aeroagentsim/extensions/engine_builtins.py),
[algorithm loader](../../src/aeroagentsim/algorithms/__init__.py),
[models](../../src/aeroagentsim/models.py) and
[installed entry-point declarations](../../pyproject.toml).

The host still self-registers some engines as installed entries. A second
installed `lowalt_tasks` therefore fails; it does not merely replace the builtin.
Use a distinct engine name for a separately installed implementation. Installed-
only host names such as `capture`, `ns3`, `px4_gazebo`, `sumo`, `traffic_assessment`,
`traffic_road_motion` and `traffic_route_inputs` remain available via metadata.
Scenario builtins are not self-registered, so external `flood-rescue` and `rescue`
providers can replace those builtin providers independently.

Provider profiles are **not** an entry-point family. Profile resolution and
credentials retain their [existing operator-owned contract](plugin-contracts.md#provider-profiles).
The public run reader and mission command schemas are also not discovery groups.

## Helper and evidence

For a versioned family, configure the helper rather than copying enumeration:

```python
from aeroagentsim.extensions import discover
from aeroagentsim.plugin_api import ScenarioPlugin
from aeroagentsim.scenarios import BUILTINS

result = discover(
    "aeroagentsim.scenarios",
    BUILTINS,
    expected_type=ScenarioPlugin,
    api_version=1,
    name_of=lambda plugin: plugin.name,
    factory_label="scenario plugin",
)
plugins = result.providers  # sorted tuple; same descriptor instances
```

`entries=` permits existing family adapters to supply their metadata query
results. `builtin_loader=` adapts legacy module-path catalogs without eagerly
importing all engines. `resolve(name)` explicitly loads one provider and always
rejects group-wide duplicates first. It does not construct an engine: that
family's expected type is a callable, not an engine instance. New versioned
families should use the strict default name syntax, not the legacy escape hatch.

[`Contribution`](../../src/aeroagentsim/extensions/records.py) is a frozen record:
`(group, name, source, distribution, version, entry_point)`, with `source` equal to
`builtin` or `installed`. `result.contributions` records **all declarations**:
shadowed builtins and every duplicate remain distinct. It is not a list of
activated providers. Distribution objects are reduced to detached text; missing
release/provenance data is `None`, never an inferred version or zero. Metric cache
fingerprints use these snapshots without changing their checkpoint format.

## Metadata-only inventory

`discover(group, builtins, load=False)` never calls entry-point loaders, builtin
loaders or provider factories. `providers` is empty because no provider was
inspected. It returns immutable conflicts rather than choosing a duplicate's
winner; runtime consumers call `require_unique()` to raise the typed error.
Provider types, versions, contributed IDs, resources and readiness remain
**unverified** in this mode.

[`plugin_inventory()`](../../src/aeroagentsim/services/plugins.py), the CLI and
`GET /v1/plugins` use that mode for scenarios, engines, algorithms, models and
metrics. Engine builtin paths live in an import-light declaration module, also
re-exported as `platform.plugins.BUILTINS`, so inventory need not import platform
or model implementations. Rows are sorted by name. Duplicate rows retain every
declaring distribution in `conflict`, including repeated names or unknown (`null`)
distributions; `status: ok` means only that the name is unambiguous. An installed
row hides the builtin implementation, not a second installed declaration.

The response adds `metadata_only: true` and the `metrics` family. Legacy scenario
`templates` / `apply_hooks` array fields remain empty **placeholders**, not a claim
that the provider contributes nothing. Complete lists require deliberately loading
scenario providers through discovery / `TemplateRegistry`, outside inventory.
This changes the former eager inventory: consumers must honor `metadata_only`
before treating those arrays as complete. Updating the existing console's
contribution-detail display and old exact-schema/eager-inventory test assertions
is a follow-up; neither is silently changed in this backend-only write set.

## Deliberate follow-ups

- **Algorithms and algorithm catalog:** preserve qualified/unqualified lookup,
  direct `module:Class` loading, protocol-method classification and configured
  construction before migrating. The [service catalog](../../src/aeroagentsim/services/algorithm_catalog.py)
  currently imports installed factories for metadata; it is not the metadata-only
  plugin inventory. The [consumer catalog](../../src/aeroagentsim/algorithms/consumers.py)
  verifies specific engine factory identities without loading them; an override
  must not inherit a builtin's consumption claim. These sites remain unchanged.
- **Models:** preserve selected-name-only duplicate rejection and the implicit
  legacy-linear exemption. Applying group-wide runtime admission there would
  change behavior, so only their inventory enumeration is shared in this PR.
- **Packaging / engine descriptors:** redundant self-entry-point cleanup and any
  separately versioned engine descriptor require their own compatibility work.
  Existing callable engine names, local catalog registration and lazy builds stay
  supported; this PR does not introduce engine `api_version` checks.
- **Assistant operations:** inventory wiring follows an actual landed public
  family, not an assumed one. `assistant_api.py` is absent in this revision.
- **Run contribution lock — PLANNED, not implemented here:** explicitly select
  contributions and pin provider/family version, implementation/distribution
  revision, entry point, selected configuration/resource digests and effective
  contribution digest into run evidence. Coordinate the existing journal/storage
  format, not a second WAL. Installation evidence and metric cache fingerprints
  do not pin a run or reproduce an old implementation. Never record credentials
  or resolved secret-bearing provider configuration in such a lock.

No admission result, provider reply, acceptance or ACK proves successful mission
completion. Missing measurements remain unknown (`None`), not zero or success;
recorded links are evidence references, not causal labels.
