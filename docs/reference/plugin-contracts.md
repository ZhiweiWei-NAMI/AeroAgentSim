# Plugin contracts

This reference distinguishes supported extension contracts from host internals and planned APIs. A plugin is trusted installed Python code, not a sandbox. Discovery does not grant runtime write authority, validate all configuration, or prove that a native simulator is ready.

See [extension admission](extension-admission.md) for common discovery rules and the family matrix.

## Shared invariants

- **Single writers:** scenario bindings select one writer per entity/field slot. Declare reads, writes, lifecycle authority, relations, commands, events, routes and RNG streams honestly. Publish only owned fields through the kernel SDK; preserve issued cuts, fact validity, acquisition clocks and entity generations.
- **Commands and receipts, not direct writes:** ask the owning engine to perform physical or business actions using its declared commands. Observe receipts and committed state for completion. Submission, acceptance, a model reply or a transport ACK is not successful completion. Do not mutate another engine's private objects or fabricate success.
- **Deterministic computation:** algorithms, consumption models and scenario hooks must be deterministic for the same inputs/configuration. Use stable ordering and simulation time, not wall time, global randomness or Python `hash()` seeds. Engines use declared streams through `ctx.rng(name)`. External/model inputs must enter through their explicit journaled integration, not hidden I/O in an algorithm or hook.
- **Declared real-time ingress:** a `real_time` partition requires a declared ingress stream and policy, including its clock mapping and lateness handling. `run.pacing: realtime` alone does not establish that contract.
- **Reproducibility evidence:** record seeds, effective configuration, plugin/native revisions, calibration and external inputs. The kernel header records the root and declared-stream seeds, engine `version` strings, registry/descriptors and configuration; this is not automatic capture of every installed distribution or external resource revision. Pin compatible package/resource revisions separately where needed.
- **Fail-closed validation:** reject unknown or malformed configuration, unsupported versions, undeclared authority and invalid outputs rather than silently substituting defaults or success. Each factory remains responsible for strict family-specific configuration validation; discovery is not a universal validator.
- **No secrets in scenarios or logs:** scenarios select provider profile names only. Keep credentials in operator-owned environment/file configuration, never scenario resources, prompts, tool payloads, journals, exception text or diagnostic logs. Recorded prompts/responses can contain sensitive supplied content; profile isolation is not a general-purpose redaction guarantee.

See [plugin concepts](../concepts/plugins.md), the [engine guide](../guides/plugins.md), [scenario bindings and ingress](scenario.md), the [kernel Engine contract](../../aerokernel/aerokernel/engine.py) and [SDK](../../aerokernel/aerokernel/sdk.py).

## Engines

| Contract | Implementation and invariants |
| --- | --- |
| Entry-point group | `aeroagentsim.engines`; a callable `build(context: EngineBuild) -> Engine`. [Discovery and build context](../../src/aeroagentsim/platform/plugins.py). |
| Discovery | Installed entries override same-name builtins; duplicate installed names fail. Factory loading is lazy and checks callability; it does not construct the engine or establish service readiness. |
| Build inputs | `EngineBuild` provides `id`, `config`, `registry`, `manifest`, `entities`, `initial`, `partitions`, `models`, `run_directory`. The [host](../../src/aeroagentsim/platform/simulation.py) builds engines in sorted ID order; `partitions` contains previously built declarations, not a complete dependency-injection container. |
| Authority | Use `writers(ref)` / `owned_fields(ref)` and declare actual `Partition` capabilities. The kernel validates bindings against declarations. Commands and receipts mediate requests to other owners; an orchestration engine must not directly set another owner's position or mutate its flight state. |
| Execution | Implement `partitions`, `version`, `reset`, `horizon`, `advance`, `react`, `close`. Advance reaches the exact logical grant; reaction does not advance native physical time. Preserve SDK batch/cut metadata; close is idempotent and must report real cleanup failures. `ContextEngine` supplies `bootstrap`, `step` and `on_inputs` hooks. |
| Validation and ingress | Strictly validate `config` and output schemas. Optional `build.config_schema` is a finite object-schema descriptor for editors, not executable validation or default application. A `real_time` partition needs explicit ingress. Native state-changing RPCs must not be transparently retried after an ambiguous result. |

## Algorithms

| Contract | Implementation and invariants |
| --- | --- |
| Entry-point group | `aeroagentsim.algorithms`; `factory(**config) -> instance`. [Loader](../../src/aeroagentsim/algorithms/__init__.py) and [protocols](../../src/aeroagentsim/algorithms/protocols.py). |
| Resolution | For a supported slot, resolve `<slot>.<name>`, then unqualified installed name, then `module:Class` or builtin. Duplicate installed names and unsupported slots fail; the required slot method must be callable. |
| Methods | `allocation.allocate(snapshot) -> tuple[Assignment, ...]`; `path_planning.plan(snapshot, start, goal) -> tuple[Point, ...]`; `conflict_resolution.resolve(snapshot, conflicts) -> tuple[Resolution, ...]`; `charging.charge(snapshot) -> tuple[Charge, ...]`. |
| Authority and determinism | Frozen snapshot inputs and proposal outputs do not confer write authority. Compute deterministically; the consuming engine validates/applies proposals through its existing ownership and command contracts. Validate constructor configuration explicitly. |
| Integration limits | Installation does not make every engine a slot consumer. The [verified consumer catalog](../../src/aeroagentsim/algorithms/consumers.py) covers specific low-altitude factories. The shipped [LLM allocator](../../src/aeroagentsim/algorithms/llm.py) requires its journaled LangGraph request/result adapter; it is not a direct synchronous `allocate` implementation for arbitrary hosts. |

## Models

| Contract | Implementation and invariants |
| --- | --- |
| Entry-point group | `aeroagentsim.models`; `factory(build: ModelBuild) -> ConsumptionModel`. [Contracts and catalog](../../src/aeroagentsim/models.py). |
| Build inputs | `ModelBuild(config, energy, registry, entities)` carries selected configuration and descriptors. This family supplies consumption contributions, not an arbitrary physics-engine registry. |
| Methods | Declare `dependencies: tuple[str, ...]`; implement `consumption(ctx, ref, segment) -> float` and `budget(ctx, ref, segments) -> float`. `Segment` contains ENU displacement in metres, elapsed seconds and actual distance in metres; consumption is in joules. |
| Authority and determinism | Read only declared dependencies through the context. Do not mutate kernel state; the host engine publishes facts and retains reserve policy and single-writer authority. Budget calculations use the currently latched environment unless the model explicitly implements a forecast. |
| Validation and selection | Duplicate entries for the selected name fail; declared dependency fields must exist. Factories validate configuration and numerical assumptions. Installed overrides apply to explicit model selections; an unchanged legacy scenario without `consumption_model` keeps the built-in linear model. |

## Scenarios

| Contract | Implementation and invariants |
| --- | --- |
| Entry-point group | `aeroagentsim.scenarios`; an exported `ScenarioPlugin` value or zero-argument callable returning one. The entry-point name must equal `ScenarioPlugin.name`. [Public API](../../src/aeroagentsim/plugin_api.py), [builtins](../../src/aeroagentsim/scenarios/__init__.py), [template integration](../../src/aeroagentsim/authoring/templates.py). |
| Version and namespace | `ScenarioPlugin(name, templates=(), apply_hooks={}, api_version=API_VERSION)` requires `API_VERSION = 1` and an actual integer version, not a boolean. Names use lowercase letters, digits, `_` and `-`, starting with a letter or digit; hook names must start with `<plugin-name>.` and include a nonempty suffix. The hook mapping is copied into a read-only snapshot. |
| Resources | `templates` is a tuple of `TemplateSource(directory: Path, scenario: Path \| None = None, extra_knobs: KnobProvider \| None = None)`. An explicit scenario override must be absolute. Package authors supply readable template/scenario resources and keep their paths valid for the registry's lifetime. |
| Hooks | `ApplyHook(document, base: Path, value) -> None` deterministically edits only the supplied document. No I/O except reading files beneath `base`, the scenario source directory; invalid values raise `ValueError` with a `knob <id>:`-style message. `KnobProvider(document) -> list[dict]` derives extra knob declarations deterministically. These restrictions are author obligations, not a Python sandbox. |
| Discovery and collisions | Installed names replace same-name builtins; duplicate installed names fail. Discovery sorts plugin names and validates the returned type and name. `TemplateRegistry` rejects duplicate template IDs/hooks. With an explicit root, it loads that root's manifests rather than adding every plugin template; discovered hooks and matching source providers remain available. |
| Validation and scope | Knob values are validated before pointer/hook application; missing hooks and unknown overrides fail, and the resulting scenario passes `load_scenario`. Templates configure existing engines, algorithms and models; they do not acquire runtime authority or register assistant operations, metric reducers or presentation providers. Keep secrets out of all resources and generated documents. |

The actual scenario API supersedes the earlier template-bundle proposal. There is **no `aeroagentsim.templates` entry-point contract**. Template manifests still use `aeroagentsim.template/v1`; see the [template guide](../guides/templates.md) for document fields and knobs.

## Provider profiles

| Contract | Implementation and invariants |
| --- | --- |
| Discovery | **Not an entry-point family.** Scenario configuration is `provider: {profile: <name>}` (or the default profile); no endpoint, model or credential selector is accepted there. [Resolver and protocol](../../src/aeroagentsim/agents/provider.py), [provider guide](../guides/agents.md#the-provider). |
| Python implementation | `Provider` exposes `model` and `complete(messages, tools, *, timeout_s, max_tokens) -> dict`. `OpenAIProvider.from_config` resolves trusted operator configuration; the Python protocol itself is not a separately version-negotiated plugin ABI. |
| Built-in profiles | `default` requires `AAS_LLM_MODEL`, with optional `AAS_LLM_BASE_URL` / `AAS_LLM_API_KEY`. `live-llm` requires `AEROAGENTSIM_LLM_BASE_URL` / `AEROAGENTSIM_LLM_MODEL`; optional `AEROAGENTSIM_LLM_API_KEY_ENV` names the credential environment variable. |
| Named profiles | Operator profiles come from the `AEROAGENTSIM_PROVIDER_CONFIG` JSON file and/or `AEROAGENTSIM_PROVIDER_PROFILES` JSON map; environment entries override file entries by profile name. Each entry requires `base_url` and `model`, with optional `api_key_env` or mutually exclusive `api_key_file`. Unknown names or malformed configuration fail. |
| Security and completion | Credentials stay operator-side and must never enter scenario/log evidence. Requests have bounded time/token/response-size handling and typed failures; upstream error bodies are not surfaced. No automatic stateful retries or silent live-to-stub success fallback. A provider response is decision evidence, not command completion. |
| Reproducibility | Live replies are external inputs, not deterministic algorithm results. Use the agent engine's journaled observation/prompt/response/validation/output path; replay does not call providers. Online delayed-reply integration is not implemented in the current decision engine. See [agent execution and replay](../concepts/agents.md). |

## Public local run reader

[`aeroagentsim.runs.open_run(directory, api_version=1)`](run-reader.md) is available for read-only local snapshots of an explicitly acknowledged journal prefix. `RUN_READER_API_VERSION = 1` versions its frozen header/snapshot/message envelopes independently of the scenario API. It never rebuilds indexes on disk or runs engines/providers; an ACK is not authentication, projection finality or run success. Index-only legacy runs without an ACK are rejected in v1.

## Metrics reducers

[`aeroagentsim.metrics`](metrics-plugins.md) is available as a versioned reducer entry-point family. Import `MetricDescriptor`, `MetricPlugin`, `Reducer` and `MetricAccumulator` from [`aeroagentsim.metrics_api`](../../src/aeroagentsim/metrics_api.py); plugins declare the literal `api_version=1`. Installed entries export a `MetricPlugin` or zero-argument factory, must match its name, and override same-name builtins; duplicate installed names fail before loading. Reducers consume detached `RecordedMessage` envelopes through schema-prefix dispatch and return scalar mappings of finite numbers or `None`, with explicit aggregation and missing-data policies. Descriptor IDs cannot collide. Optional checkpoints or the input-history fallback preserve ordered reduction and deduplication. Implementation revisions and distribution releases participate in cache invalidation, not a pinned run lock. The service projector, its host-only physical-evidence adapter and private builtin state remain implementation details; use the [metric-plugin reference](metrics-plugins.md) for the public contract and legacy report boundaries.

## Stability

These classifications describe the supported boundary, not a guarantee that every public Python symbol has an independently negotiated ABI.

| Classification | Surfaces | Compatibility rule |
| --- | --- | --- |
| **Public-stable, explicitly versioned** | Document contracts such as `aeroagentsim.scenario/v1`, `aeroagentsim.template/v1`, `aeroagentsim.behaviour-package/v1`, registry snapshots, documented viewer/event/KPI feeds, capture/native wire contracts and admitted journal formats; the scenario contribution API in `aeroagentsim.plugin_api` with `API_VERSION = 1`; the [public local run reader](run-reader.md) in `aeroagentsim.runs` with `RUN_READER_API_VERSION = 1`. | Consume the documented admitted version. Incompatible changes need an explicit new format/API version and migration/admission policy; preserve old-run interpretation rather than reinterpreting old payloads. See [scenario](scenario.md), [behaviour packages](behaviour-package.md), [viewer feed](viewer-feed.md), [HTTP API](http-api.md) and [kernel design](../../aerokernel/docs/DESIGN.md). |
| **Public-unversioned Python SDK** | `EngineBuild` / engine catalog, kernel `Engine` / `Partition` / `ContextEngine`, registry descriptors, algorithm/model protocols, `TemplateRegistry` and direct hook registration, scenario/behaviour loading facades, provider and graph hooks, exported kernel record/replay helpers. | Intended extension surfaces, but no separate Python ABI negotiation. Pin compatible platform/kernel/plugin distributions and resource/native revisions. `API_VERSION` does not version these other families. |
| **Public-unversioned operational surfaces** | Documented HTTP metadata/control routes and optional additive response fields; agent record envelopes. | The `/v1` route family does not make every payload a closed, independently versioned contract. Prefer version-tagged read payloads where available and tolerate documented additive fields. |
| **Internal** | `RunStorage`, `committed_prefix`, service indexes/checkpoints, projector implementations, KPI/story reducers, assistant operation tables/compiler dispatch, private pack state such as `Flight` and another engine's `owner.flights`. | Do not build external plugins against these or mutate another owner's objects. A documented feed remains public even though its projector implementation is internal. Use supported commands and documented reads instead. |

### Validation caveats

Fail-closed requirements must not be confused with checks that do not exist. In particular, [`load_registry`](../../src/aeroagentsim/scenario/loader.py) accepts `registry.digest` but currently **does not compare it** against the loaded/effective registry digest. The kernel recording its effective registry digest is not validation of that authored claim. Do not rely on the field for integrity admission.

Factory callability, slot-method checks and schema descriptors likewise do not establish full semantic conformance. Determinism, hook purity, secret hygiene and external revision capture remain plugin/operator responsibilities where not mechanically enforced.

## Owner-mediated mission control

| Capability | Availability |
| --- | --- |
| Owner-mediated mission control | [available for the lowalt Tasks owner (v1)](mission-commands.md). Reserve/divert/resume/release use kernel commands and committed-effect receipts; embedded Rescue migration and payload-consume commands remain planned. |

## Planned extension points — not available

These are design directions, not groups or interfaces a distribution can register today.

| Planned capability | Current boundary / supported alternative |
| --- | --- |
| Registry overlay plugins | No discoverable `aeroagentsim.registry_overlays` family or `registry.overlays` selection contract. Use explicit inline descriptors or a complete registry snapshot supported by [the loader](scenario.md#registry-section). An overlay filename alone is not discovery. |
| Assistant families | No external operation/compiler/expectation registration contract. Existing template controls can use pointers/hooks; new semantic assistant operations still require host changes to [dispatch](../../src/aeroagentsim/assistant/compiler.py). |
| Presentations | No `aeroagentsim.presentations` provider registry. Existing declarative scenario `presentation` bindings and `event_narratives` are available, but do not register new browser renderers or arbitrary remote JavaScript. |

Installing a scenario plugin does not make these planned capabilities available. Keep authoring contributions, runtime ownership and read/presentation integrations separate.

## Versioning rules

- Import `API_VERSION`, `ScenarioPlugin`, `TemplateSource` and hook protocols from [`aeroagentsim.plugin_api`](../../src/aeroagentsim/plugin_api.py).
- The supported value is **`API_VERSION = 1`**. `ScenarioPlugin` construction rejects a mismatched/non-integer `api_version` before the contribution can be applied. Discovery rejects wrong return types, mismatched names and duplicate installed names; there is no permissive downgrade or version-coercion path.
- A factory written for version 1 should declare `api_version=1`, not copy the host's current `API_VERSION` blindly. An incompatible host must reject it rather than silently claim compatibility.
- This version covers scenario contribution shapes and obligations. It is not an engine/algorithm/model/provider ABI version, package release version or native protocol revision. Pin those dependencies separately.
- Keep payload/configuration meaning stable within a declared contract; make incompatible revisions explicit. Record the revisions needed to explain a run rather than assuming a profile name or package name identifies immutable behavior.
