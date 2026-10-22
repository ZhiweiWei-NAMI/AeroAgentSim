# Assistant operation families v1

Assistant families contribute semantic authoring operations, their parameter
contracts, prerequisites, read projections and conditional expectations. They
configure **proposals**, not running missions. An external family does not need
a new branch in the host compiler.

The public contract is [`aeroagentsim.assistant_api`](../../src/aeroagentsim/assistant_api.py):

- `ASSISTANT_API_VERSION = 1`
- Entry-point group: `aeroagentsim.assistant_operations`
- Frozen descriptors: `AssistantOperationPlugin`, `OperationSpec`, `Prerequisite`,
  `CompileContext`, `CompileResult` and `ResourceProposal`.

## Register a family

An entry point exports an `AssistantOperationPlugin`, or a zero-argument factory
returning one. The entry-point name must equal the descriptor's `name`.

```toml
[project.entry-points."aeroagentsim.assistant_operations"]
example = "example_operations:plugin"
```

```python
from aeroagentsim.assistant_api import (
    AssistantOperationPlugin, CompileResult, OperationSpec, ResourceProposal,
)


def annotate(context, params):
    text = params["text"]
    return CompileResult(
        patches=({"op": "add", "path": "/title", "value": text},),
        resources=(ResourceProposal("notes/title.txt", text.encode("utf-8")),),
        effects=({"group": "changed", "kind": "configuration", "id": "example:title",
                  "label": "Scene title", "label_zh": "场景标题"},),
    )


def plugin():
    return AssistantOperationPlugin(
        name="example", api_version=1,
        operations=(OperationSpec(
            id="example.annotate",
            parameters={"type": "object", "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 80},
            }, "required": ["text"], "additionalProperties": False},
            title="Annotate scene", title_zh="标注场景",
            description="Propose a scene title and text resource",
            description_zh="提议场景标题和文本资源",
            compile=annotate,
        ),),
    )
```

The host still validates the resulting **whole scenario**. A valid operation
schema does not make arbitrary scenario fields or engine configurations valid.

## Discovery and ownership

[`discover_assistant_plugins()`](../../src/aeroagentsim/assistant_api.py) follows
scenario-plugin discovery:

1. Collect installed entry points. Duplicate installed names fail **before any
   installed entry is loaded or builtin factory invoked**.
2. Merge code `BUILTINS` with installed entries, in sorted provider-name order.
   An installed name replaces the **whole** builtin provider; it does not merge
   that provider's operations or retain its private staging adapter.
3. Require an actual descriptor (or factory returning one), matching namespace
   and name, and the actual integer API version `1`. Booleans, strings and floats
   are not API versions.
4. Reject duplicate/conflicting operation IDs and foreign operation namespaces.

Names match `[a-z0-9][a-z0-9_-]*`; operation IDs are `<family>.<op>` with that
vocabulary on each side. For example, `lowalt.add_place`, `traffic.set_wind` and
`example.annotate` are distinct canonical IDs.

[`OperationCatalogue`](../../src/aeroagentsim/assistant/catalogue.py) is a snapshot
of the effective providers. Build it before entering pure compilation. A
compiler invocation on that snapshot does not rediscover or import providers.

### Old plans and aliases

`LEGACY_ALIASES` explicitly pins each old unqualified name to its canonical
operation, for example `add_place → lowalt.add_place` and
`set_duration → lowalt.set_duration`. No suffix matching or automatic external
aliases exist. Installing `example.set_duration` cannot steal `set_duration`.

Custom alias declarations must identify an existing canonical target. Repeating
an alias is an error, including through an iterable of declarations. When an
installed replacement retires a builtin operation, its pinned legacy name
becomes unknown; it is never silently redirected to another family.

Legacy capability rows retain their old names, parameter schemas, examples and
copy. Tool admission also accepts canonical IDs. The compatibility
`assistant.operations.OPERATION_SPECS` import is a derived catalogue view, not a
second semantic operation table.

## Operation contract

`OperationSpec` contains:

| Field | Contract |
| --- | --- |
| `id` | Canonical, provider-owned namespaced ID |
| `parameters` | Object-rooted parameter JSON Schema |
| `title`, `title_zh`, `description`, `description_zh` | Nonempty English/Chinese copy |
| `applicable(context, params)` | Pure predicate returning an actual `bool`; false rejects the operation |
| `prerequisites` | Tuple of explicit `Prerequisite` descriptors |
| `compile(context, params)` | Pure function returning `CompileResult` |
| `topic`, `display` | Optional capability grouping and compatibility display data |

V1 admits a local JSON Schema vocabulary: object properties, required fields,
additional properties, array items/length/uniqueness, string length/pattern,
numeric bounds/multiples, enum/const, type unions, anyOf/oneOf/allOf/not and
annotation keywords (`title`, `description`, `default`, `examples`, `$schema`,
`$id`). Unsupported keywords are rejected rather than silently ignored. In
particular, `$ref` and remote schema resolution are not supported. Parameters
are checked before the compiler callback; defaults are descriptions, not
silently supplied parameter values. Integer admission requires an actual integer
(not a boolean), numbers must be finite, and positive string `minLength` also
retains the existing nonempty-after-trimming check.

### Prerequisites are proposals too

A `Prerequisite` declares a canonical `operation`, fixed `params`, a pure `when`
predicate and an `automatic_id`. Optional bilingual `note`/`warning` text and a
pinned `display_operation` retain legacy plan copy.

Required operations are visible in the effective operation list, notes and
compiled diff. Explicit prerequisites execute before dependants even when
authored later. Cycles and missing prerequisite IDs fail closed. Conditions are
evaluated against the supplied input snapshot; they are not runtime monitors or
claims that a task has been completed. The builtin lowalt prerequisite retains
the visible `add_lowalt_components` recipe and its existing warnings.

### Pure compile context

The **public** `assistant_api.CompileContext` contains only snapshots:

- `document`, `controls`, and optional JSON-shaped `data`;
- `resources`: portable relative names mapped to already-read bytes;
- `operation_id`, `locale`, and optional **string** `agent_profile` selector.

Nested mappings are read-only and sequences are snapshotted as tuples. Use
`thaw()` for editable copies. Arbitrary objects, including provider clients,
stores, paths, callbacks and LLM handles, are rejected as context data. A profile
selector is authored configuration, not a resolved provider or its credentials.

Compilers and applicability/prerequisite predicates must be deterministic and
perform **no I/O**: no files, sockets, environment-dependent discovery, provider
calls, engine construction, workspace writes or runtime commands. Resources
must already have been supplied by the host. A compiler must not mutate its
inputs or obtain runtime authority.

For in-memory integration, `assistant.compiler.compile_operations(context,
operations, catalogue=...)` expands/orders prerequisites and combines proposals
without discovery, resource I/O or scenario loading. It is not whole-scenario
validation. Do not apply a result containing error issues.

The older `assistant.compiler.CompileContext` is a **host staging context** used
by workspace/Form/tool integrations, not the context handed to operation
compilers. `compile_plan` deliberately performs I/O outside the pure boundary:
it resolves the base, reads/copies resources into isolated plan files, invokes
the selected pure compiler, stages proposals, loads the resulting scenario and
prepares the approval diff. The shipped packs retain private compatibility
adapters for template hooks, region previews, contract snapshots, fleet removal
and legacy generator expansion. Their services never enter the public compile
context. `_host` and the builtin named read-adapter callback map are private
compatibility seams, **not** stable external v1 APIs. `compile_plan` returns the
existing host plan artifact, not the public per-operation result value. Its
optional `contributions` record retains proposed effects, configuration
references and staged resource paths/digests; empty records are omitted from
legacy artifacts.

### Compile results

All result collections are tuples, snapshotted read-only:

| Field | Meaning |
| --- | --- |
| `patches` | JSON Pointer `add`, `replace`, `remove` proposals; no executable patch callbacks |
| `resources` | `ResourceProposal(path, content)`; bytes write a file, `None` removes it |
| `issues` | Error/warning `severity`, located `field`, bilingual `message` |
| `effective_operations` | Optional explicit recipe returned by the family; empty retains the requested operation |
| `effects`, `references` | Proposed/configured effects and recorded links, not causal labels |
| `expectations` | Conditional finite event/payload matches, not measured outcomes |
| `controls` | Optional replacement control metadata |

Resource paths must be portable relative paths, with no absolute, traversal,
empty, drive or backslash components. Host-owned workspace/plan records
(`draft*.json`, `versions/`, `validation-*`, `__pycache__/` and the root
`region.plan.json`) are not public resource targets. The host stages only inside
isolated plan files. Public family writes/removals produce explicit resource rows in the
approval diff; builtin compatibility adapters retain their existing
component/region diff presentation.

Expectations use the existing schema/`where` match format, optional bounded
counts, release-based demand, dependencies and conditional alternatives. A
result/combined expectation set is limited to 4096 entries. IDs must be unique;
identical compile/read projections may share one ID, but conflicting duplicates
fail. Callables and nonfinite data are not expectation predicates. If a family
needs expectations reconstructed from a pinned run input, contribute its pure
read projection too; a chat recipe alone is not pinned runtime evidence.

## Contributed reads

Optional `AssistantReadSpec` projections provide pure `summarize(context)`,
`expectations(document, summary)`, `interactions(summary)` and missing-event
explanations. Summary card lists concatenate, dictionaries merge and conflicting
card IDs fail. `priority` can order contributed sections. Families can provide
bilingual topic/section copy through metadata. Lowalt and traffic own their
existing domain summaries and expectations, including traffic stage receipts
and lowalt cooperation/weather conditions.

## Guarantees and non-guarantees

- **Approval remains host-owned.** Compilation changes neither the source
  workspace nor a running mission. Existing revision-conflict, whole-scenario
  validation and explicit Confirm/apply checks remain in place. Confirm saves a
  draft/version; starting a run is separate.
- **Evidence scopes stay separate.** A model reply, command admission/ACK,
  acceptance and measured completion are different records. Only matching
  committed evidence satisfies an expectation. Zero expectations remain
  **unassessed**. Missing values stay unknown/`None`, not zero.
- **Provider failures remain failures.** This contract adds no model call and no
  stub-success fallback. The existing configured-provider harness behavior is
  unchanged.
- **No authority or execution guarantee.** An operation, ontology contract,
  reference, summary, expectation or accepted command does not implement an
  engine, grant observation/action rights, guarantee execution, or establish
  causation. Runtime owners and receipts remain authoritative.
- **No Python sandbox.** Installed plugins are trusted Python. Purity is a
  contract obligation, supported by I/O-denial tests of the pure seam, not an
  OS-level confinement claim. Discovery and host resource preparation can do I/O.
- **No release/plugin lock claim.** API version 1 does not pin package, kernel,
  resource, model or installed-family revisions. Persisted contribution locks,
  an installed-wheel acceptance job and generic frontend contribution rendering
  are **planned**, not delivered by this contract.
