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
        patches=({"op": "replace", "path": "/id", "value": text},),
        resources=(ResourceProposal("notes/identifier.txt", text.encode("utf-8")),),
        effects=({"group": "changed", "kind": "configuration", "id": "example:identifier",
                  "label": "Scene identifier", "label_zh": "场景标识"},),
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
            description="Propose a scenario identifier and text resource",
            description_zh="提议场景标识和文本资源",
            compile=annotate,
        ),),
    )
```

The host still validates the resulting **whole scenario**. A valid operation
schema does not make arbitrary scenario fields or engine configurations valid.
The example edits the supported scenario `id`; a top-level `title` is not a
scenario field and remains a loader error.

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

### Explicit compatibility exception: `set_region` rejection

Builtin behavior and diagnostic copy otherwise follow `origin/main`. One
intentional diagnostic correction is retained: requesting `set_region` (or
`lowalt.set_region`) through a host context with `allow_region=False` raises an
`OperationError` at field **`op`**, with this exact bilingual message:

```text
set_region is an assistant proposal operation. Use the Region map's /region preview/apply endpoint for form edits / set_region 用于助手方案；表单请使用区域地图的 /region 预览和应用接口
```

The older branch called the three-argument `_fail` helper with two arguments,
accidentally producing a `TypeError` diagnostic at field `params`. That bug is
**not** compatibility behavior to restore. The corrected field and text above
are an explicit exception to diagnostic parity; this does not enable region
edits through the form compilation path.

## HTTP descriptors

`GET /v1/assistant/operations` returns a read-only, data-only projection of the
same discovered `OperationCatalogue` used by assistant tools and compilation:

```text
{api_version: 1, families: [{name, operations: [
  {id, title: {en, zh}, summary?: {en, zh}, parameters, prerequisites, aliases?}
]}]}
```

- `families` contains `lowalt` and `traffic` (builtin unless replaced) plus other
  installed families, in discovery order. Whole-provider replacement and all
  discovery/admission checks apply; there is no separate HTTP operation table.
- `id` is canonical. `title` comes from `title`/`title_zh`; `summary` comes from
  `description`/`description_zh`. V1 operation specs require both descriptions,
  so this implementation includes `summary` on every operation.
- `parameters` is the detached parameter JSON Schema, not evaluated defaults.
- `prerequisites` is the catalogue's array of
  `{operation, params, conditional, note, warning}` records. `operation` is a
  canonical ID; `conditional: true` describes a predicate, not its evaluated
  result for a scene. Absent notes/warnings remain JSON `null`. No condition is
  invoked by this request.
- Optional `aliases` lists only explicit, still-effective catalogue aliases for
  that operation. It lets legacy recipes retain their copy without client-side
  suffix matching. A retired operation's alias is not redirected to another
  installed family.

For example, with the `example` family above installed, its operation entry is:

```json
{
  "id": "example.annotate",
  "title": {"en": "Annotate scene", "zh": "标注场景"},
  "summary": {
    "en": "Propose a scenario identifier and text resource",
    "zh": "提议场景标识和文本资源"
  },
  "parameters": {
    "type": "object",
    "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 80}},
    "required": ["text"],
    "additionalProperties": false
  },
  "prerequisites": []
}
```

The GET invokes discovery, but not operation compilers, applicability predicates,
prerequisite conditions, read projections or dependency callbacks. It does not
start a model provider, create a conversation, compile/apply a proposal or start a
run. No executable Python callbacks, plugin JavaScript, private staging adapters
or model credentials are returned. Installed Python remains trusted code;
this endpoint is not a sandbox for discovery factories.

Discovery failures fail the whole request rather than returning an empty or
partial list. The existing assistant HTTP boundary preserves its error text in
`{"detail": "..."}` for handled errors: contract `ValueError`/`TypeError` failures
return HTTP 422, and missing files return HTTP 404. Unhandled loader/factory
failures remain HTTP 500, not successful discovery.

The browser fetches this snapshot once per conversation in its `AssistantApi`
instance, sharing the request across refreshes, plan versions and locale changes.
It attaches copy as client-only display metadata; operation recipes, diffs,
validation and approval state are unchanged. Session creation, session reads and
plan reads return without waiting for this optional copy, so a stalled descriptor
fetch cannot block messaging or event polling. Pending copy renders the same neutral
ID labels as unavailable copy; mounted summary and changes views update when the
shared request settles, without another session read. Cancelling a consumer or
unmounting a view does not abort the shared descriptor request.

Failed reads remain failures in that cache and are shown with a quiet unavailable
notice when a plan is displayed; reload or a new conversation obtains a fresh
snapshot. Unknown IDs (including missing legacy aliases) render with their exact
ID and **Unknown operation / 未知操作**, never a guessed name. Titles and operation
summaries are plain text in the active locale, not execution or completion claims.

This HTTP slice does **not** generalize assistant spatial overlays. Presentation
v1 already declares pose fields, frames and optional role hints, but
[`apply_header_presentations`](../../src/aeroagentsim/presentations.py) resolves
those into run-header data. The assistant plan-preview response currently carries
the compiled scenario and geography, not resolved family poses or the resolved
registry/type context needed to interpret them. Consuming them here would widen
the preview contract, so `frontend/src/assistant/overlay.ts` remains unchanged in
this step. Broader generic
forms/cards and presentation-driven assistant previews remain **planned**.

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

A family may also contribute the pure
`AssistantOperationPlugin.dependencies(context, operations)` callback. It
receives a read-only operation tuple with aliases resolved to canonical IDs and
returns a mapping of operation indices to tuples of prerequisite indices. Indices
must refer to other operations in that snapshot; invalid edges and cycles fail
closed. These edges order existing recipes, rather than installing prerequisites.
Both compilation entry points consume the same catalogue dependency graph.
Builtin creation/forward references, region/component ordering and duration-first
release defaults live here, not in the private resource-staging adapter. Their
compatibility edges are limited to effective specs selecting the shipped builtin
compiler semantics. Namespace/name spelling or copied display tags do not select
those semantics: an installed replacement `lowalt` provider receives no builtin
edges merely because builtin traffic remains installed.

### Pure compile context

The **public** `assistant_api.CompileContext` contains only snapshots:

- `document`, `controls`, and optional JSON-shaped `data`;
- `resources`: portable relative names mapped to already-read bytes;
- `operation_id`, `locale`, and optional **string** `agent_profile` selector.

Nested mappings are read-only and sequences are snapshotted as tuples. Use
`thaw()` for editable copies. Arbitrary objects, including provider clients,
stores, paths, callbacks and LLM handles, are rejected as context data. A profile
selector is authored configuration, not a resolved provider or its credentials.

Compilers, dependency callbacks and applicability/prerequisite predicates must
be deterministic and perform **no I/O**: no files, sockets, environment-dependent discovery, provider
calls, engine construction, workspace writes or runtime commands. Resources
must already have been supplied by the host. A compiler must not mutate its
inputs or obtain runtime authority.

For in-memory integration, `assistant.compiler.compile_operations(context,
operations, catalogue=...)` expands prerequisites, applies the shared pure
dependency ordering, and combines proposals without discovery, resource I/O or
scenario loading. For builtins whose resource inputs are already supplied, its
ordering semantics match `compile_plan`: for example, a later `set_duration`
sets the default Poisson release end before `add_orders`, and forward creation
references are resolved first. The host also supplies the effective recipe
snapshot as `context.data["operations"]` for pure creation-ID lookup. In-memory
compilation does not perform template hooks, generator expansion or whole-scenario
validation; recipes needing host-prepared resources still require those inputs.
Do not apply a result containing error issues.

The older `assistant.compiler.CompileContext` is a **host staging context** used
by workspace/Form/tool integrations, not the context handed to operation
compilers. `compile_plan` deliberately performs I/O outside the pure boundary:
it resolves the base, reads/copies resources into isolated plan files, invokes
the selected pure compiler, stages proposals, loads the resulting scenario and
prepares the approval diff. The shipped packs retain private compatibility
adapters for template hooks, region previews, contract snapshots, fleet removal
and legacy generator expansion. Their services never enter the public compile
context. `_host`, `_dependency_applies` and the builtin named read-adapter callback
map are private compatibility seams, **not** stable external v1 APIs.
`compile_plan` returns the existing host plan artifact, not the public
per-operation result value. Its
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
component/region diff presentation. Confirm applies the final validated removal
for each exact workspace-relative path, after the usual plan digest, validation,
workspace identity and revision checks and before saving the version. Removal
records are covered by the plan digest. Confirm rejects symlinked removal
ancestors and leaf targets, including internal aliases into `versions/`, before
copying any staged files. Unlinks use pinned no-follow parent-directory handles;
host-owned/excluded paths and earlier immutable versions remain protected. A
subsequent write to the same resource cancels its removal.

Expectations use the existing schema/`where` match format, optional bounded
counts, release-based demand, dependencies and conditional alternatives. A
result/combined expectation set is limited to 4096 entries. Public contribution
IDs must be unique; identical compile/read projections may share one ID, but
conflicting duplicates fail. The host preserves the legacy repeated weather
expectation IDs/rows for builtin simultaneous gusts across multiple profiles;
this private read-compatibility allowance does not relax public `CompileResult`
admission or permit cross-provider ID collisions. Callables and nonfinite data
are not expectation predicates. If a family needs expectations reconstructed
from a pinned run input, contribute its pure read projection too; a chat recipe
alone is not pinned runtime evidence.

## Contributed reads

Optional `AssistantReadSpec` projections provide pure `summarize(context)`,
`expectations(document, summary)`, `interactions(summary)` and missing-event
explanations. Summary card lists concatenate, dictionaries merge and card IDs
owned by different providers conflict. Repeated rows from the same provider
remain intact, including legacy multi-profile gust IDs. `priority` can order
contributed sections. Families can provide bilingual topic/section copy through
metadata. Collection ownership is retained in-memory and persisted separately in
plan data, without adding serialized scene fields. Session restart restores it;
older ownership-free proposals reconstruct it from the proposal document and
plan resources, not the live draft. Expectation/interaction callbacks receive
their own contributed card collections alongside shared scene context. A card
is not permission for another family to manufacture an expectation. Without
ownership, only explicit matching domain tags can assign collection cards;
untagged cards are never implicitly lowalt-owned.

Traffic owns its activation expectation, setup interaction, actor/incident lanes
and stage receipts, even when lowalt reads are replaced. Lowalt owns its
cooperation/weather conditions. Private builtin projection ordering preserves
the original workload/cooperation → traffic setup → other events → traffic
receipts sequence and lane copy. Explicit contributed spatial priorities preserve
the original lowalt → aircraft → road → incident → studio position precedence
for both cards and host `find_places(near=...)`. Projection failures return normal
located compiler diagnostics, rather than escaping `compile_plan`.

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
