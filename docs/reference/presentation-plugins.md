# Presentation plugins (API v1)

The `aeroagentsim.presentations` entry-point family supplies display data for recorded runs. Import the
public contracts from [`aeroagentsim.presentation_api`](../../src/aeroagentsim/presentation_api.py).
The data format is `aeroagentsim.presentation/v1`; `api_version` is the actual
integer `1`, independent of the scenario, metric and viewer-feed versions.

Presentation does not grant engine write authority, compute measurements or turn
stored artifacts into accepted work. Templates are **plain-text data**, not HTML,
JavaScript, Python expressions or renderer callbacks. Installed Python factories,
normalizers and control readers are trusted code, **not a sandbox**.

## Register a provider

An entry point exports a `PresentationPlugin` or a zero-argument factory:

```toml
[project.entry-points."aeroagentsim.presentations"]
acme = "acme_presentation:plugin"
```

`acme_presentation.py` can provide a narrative and a read-only control adapter:

```python
from collections.abc import Mapping

from aeroagentsim.presentation_api import PresentationPlugin
from aeroagentsim.presentation_controls import Unavailable, known


def read_horizon_seconds(document):
    run = document.get("run")
    until = run.get("until_ns") if isinstance(run, Mapping) else None
    if type(until) is not int or until < 0:
        return Unavailable("No explicit nonnegative run.until_ns")
    return known(until / 1e9, {"pointer": "/run/until_ns", "value": until})


def plugin():
    return PresentationPlugin(
        name="acme", api_version=1, revision="1",
        data={
            "format": "aeroagentsim.presentation/v1",
            "labels": {"Loading bay": {"en": "Loading bay", "zh": "装卸区"}},
            "narratives": {
                "acme.delivery.finished": {
                    "template": "{vehicle} completed delivery to {destination}",
                    "template_zh": "{vehicle}完成向{destination}配送",
                    "category": "logistics", "severity": "notice",
                    "selectors": {
                        "vehicle": {"path": ["vehicle"], "label": True},
                        "destination": {"path": ["destination"], "label": True},
                    },
                    "entities": ["vehicle", "destination"],
                    "position": "vehicle",
                },
            },
        },
        control_readers={"acme.horizon_seconds": read_horizon_seconds},
    )
```

The scenario must separately register and emit `acme.delivery.finished`, with
`vehicle` and `destination` identity strings or typed references. Presentation
registration neither declares a kernel message nor creates an event. Position is
attached only when recorded ENU evidence is available.

`PresentationPlugin` is frozen. Its `data` is detached, recursively read-only
finite JSON: mappings become read-only mappings and arrays become tuples. Names
match `[a-z0-9][a-z0-9_-]*`. Declare the literal API version implemented; do not
copy a future host version constant. Bump `revision` when provider behavior changes.

`discover_presentation_plugins()` returns sorted, frozen provider clones with
observed `.source` metadata: builtin/installed kind, entry-point value and
available distribution name/version. Builtins `lowalt` and `traffic_accident` load
lazily. Installed same-name providers replace the **whole** builtin, not a merge.
`names=(...)` restricts provider loads. The shared
[`extensions.discovery`](../../src/aeroagentsim/extensions/discovery.py) helper
collects declarations and rejects global duplicates first; all declared names are
validated before filtering/loading, even for `names=()`. Unselected factories are
never loaded. Wrong exports, name mismatches, malformed data and conflicting owned
IDs fail without builtin fallback. Discovery is isolated from other families.
Service startup checks names without loading factories; only new
launches and legacy summaries discover fully, while pinned reads load saved names.

## Finite data contract

Only `format` is required at the root; optional sections are:

| Key | Meaning |
| --- | --- |
| `labels` | Exact vocabulary/descriptor ID → `{en, zh}`. Known type/field IDs update header `displayName`/`displayName_zh`; arbitrary names are not translated by guessing. |
| `numbered_labels`, `label_groups` | Finite label families; groups declare `prefix`, `names`, `numbered`, with bilingual values. |
| `enums` | Exact schema → field → recorded scalar spelling → `{en, zh}`. Cosmetic vocabulary, not a change to recorded values. |
| `narratives` | Exact schema → bilingual declaration: `template`, `template_zh`, `category`, `severity`; optional selectors, entity/position bindings and variants. |
| `poses` | Declarative type/position/orientation/visual bindings, added only for types without an authored binding. |
| `metrics` | Formatting hints: optional bilingual `label`, text `unit`, `decimals` (0–9), `style` (`number`, `percent`, `duration`). They do not calculate or change measurements. |
| `header` | Finite engine interval, initial lighting and legacy resource scene hints. |

Bilingual templates must have identical sets of bare `{field}` placeholders.
Attribute access, indexing expressions, conversions and format specifications are
not a template language. Severity is `info`, `notice`, `warning` or `critical`.
Provider categories are nonempty DATA strings. Unknown viewer-feed/v1 category
styles emit generic `mixed`, never a guessed business kind; DATA retains the
domain token. Legacy scenario/behaviour declarations keep their category validation.

A selector has a required `path` and these optional keys:

| Key | Bound / behavior |
| --- | --- |
| `path` | 1–8 literal members: nonempty field names or integer indices `0..4095`. No wildcards, recursive descent or `..`. |
| `label` | Boolean: request generic identity-label rendering. |
| `enum` | Finite scalar → `{en, zh}` map. Numeric/boolean keys use exact JSON spelling, not rounded display text; unknown string tokens stay literal unless `label` is explicit. |
| `prefer_payload` | Boolean: an explicit payload member named by the placeholder renders ordinarily, bypassing derived selector transforms; otherwise use `path`. Retains legacy task payload precedence. |
| `fallback` | `{en, zh}` cosmetic text, such as “its destination”; never evidence of an unrecorded facility, position or success. |
| `join` | One item template with exactly `template`, `template_zh`, `selectors`, `separator: {en, zh}`; item selectors cannot join again. At most 4096 input items. |

Without a selector, `{field}` refers to the direct payload field. `variants` is a
one-level mapping of named declaration overrides, selected by normalization—not
by executable predicates. `category_field`/`category_map` select a category from
normalized data; `entity_list` can name a normalized list of entity identities.
The host owns labels, interpolation, list formatting, entity links and position
lookup. Scenario `event_narratives` overrides retain precedence over provider and
behaviour-package declarations. Ordinarily, overrides do not inherit missing
Chinese text or builtin variants; variants are still validated. A recipe can
explicitly list `legacy_variants` (unique declared variant names) that still apply
to legacy authored overrides. This compatibility policy preserves evidence-dependent
neutral road-conflict wording and actor position even for authored templates,
missing Chinese text or severity/category-only edits; the road variants retain
authored category/severity. Other overrides do not inherit translations or variants
accidentally. Identical legacy pack declarations copied into a scenario retain
their recipe; authored entity/position bindings keep their direct-payload semantics.

### Validation and absent evidence

Provider construction validates shape before freezing. Use
`admit_presentation_plugins(plugins, registry, document=scenario_document)` for
registry-aware family admission, including normalized payload schemas, pose
bindings and origin requirements. Referenced paths in available schemas must
exist and have suitable types; unrelated catalogue entries do not activate an
absent schema. `validate_presentation(data)` validates standalone DATA shape.

Absence is not malformed data: an omitted optional section contributes nothing;
a present wrong shape, unknown key, invalid path or broken declared binding is an
error. Missing required template evidence must not become an invented success
message. An absent entity link or unavailable recorded position stays absent.

`poses` use `typeId`, `positionField`, `frame`, `visual`, with optional
`orientationField`, `role`, `camera`, `optional`. Position/orientation require
applicable three-/four-vectors and consistent frames; local ENU/NED position units
must be metres when declared. Visuals are `marker`, `model` or `label`; models
require a host-owned `/v1/` or `procedural:` asset, and scale must be finite and
positive. Camera hints accept nonnegative `priority` and `distance_m`.

`resolve_poses({"format": "aeroagentsim.presentation/v1", "poses": poses}, registry)`
returns `(bindings, diagnostics)`. Missing optional types/fields degrade to a
generic labelled view; malformed vectors, units, frames or visuals still fail.
WGS84 also needs an actual scenario origin: an optional pose without one degrades
with a diagnostic; a required pose fails. No transform is inferred.

The header retains authored bindings and adds resolved poses only for other
types. When poses, metric hints or diagnostics are contributed, it also emits
`presentationHints: {format, digest, poses, metrics, diagnostics}` (`format` is the
DATA format). The extension is otherwise absent, preserving builtin bytes.
Not every viewer consumes every camera/metric hint; this does not weaken
[authored scenario admission](scenario.md#presentation).

## Optional pure normalization

Use data selectors first. Nontrivial legacy task/rescue reduction may additionally
use `normalizers=(adapter, ...)` on `PresentationPlugin`. The
`NarrativeNormalizer` contract is:

- Nonempty `id`, `version`; tuples of **exact** `input_schemas` and `fact_fields`
  (unlike metric reducers' prefix subscriptions).
- `output_schemas` declares a valid normalized **record** schema for each input schema.
- `initial()` creates independent finite JSON state.
- Synchronous `consume(state, record)` may mutate only its own state. For events,
  return `{payload, variant?}`, or `None` to commit state without emitting a view.
  Facts **must** return `None`. Output fields are normalized data, never prose.

Inputs are detached, recursively read-only JSON records: events carry
`kind`, `schema`, `payload`, `evidence`; facts carry `kind`, full `entity` identity,
`field`, `value`, `evidence`. No kernel `Message`, `FactWrite`, registry, projector,
renderer, label callback or provider object crosses this boundary. Keep committed
item order and retain all necessary state in the JSON state. Do not perform I/O,
call models/providers, read clocks, use unseeded randomness or mutate runtime
state. Validation does not enforce purity of trusted installed Python.

Normalizers may classify or unpack recorded evidence; they cannot create kernel
facts, accepted uploads or new evidence. In particular, prior knowledge of an
incident is not proof that a later road-conflict event was caused by it.

## Read effective controls; never apply hooks

[`presentation_controls`](../../src/aeroagentsim/presentation_controls.py) is a
separate read registry, not the authoring apply-hook registry. For the provider
above:

```python
from aeroagentsim.presentation_controls import ControlRegistry, read_controls
from acme_presentation import plugin

provider = plugin()
registry = ControlRegistry(
    controls=(
        {"id": "horizon_ns", "pointer": "/run/until_ns"},
        {"id": "horizon_s", "apply": "acme.horizon_seconds"},
    ),
    readers=provider.control_readers,
)
observations = read_controls({"run": {"until_ns": 90_000_000_000}}, registry)
```

Each declaration needs a unique `id` and **exactly one** RFC 6901 `pointer` or
`apply` reader name. Here `apply` only looks up a read adapter; no authoring hook is
called. Readers receive a recursively read-only JSON snapshot. Return
`known(value, evidence)` (a `Known` with a canonical-evidence SHA-256 digest) or
`Unavailable(nonempty_reason)`. Bind evidence to the actual pinned runtime
consumer, not a stale authored copy. Builtin traffic physics readback requires
`traffic_road_motion` for `road_motion` and `kinematic` for `air_motion`, with
explicit equal positive steps and both consumer identities in its evidence.
Replacement implementations need their own trusted readers. Constant traffic wind
controls accept only `calm`/`constant` profiles; gust profiles are `Unavailable`,
not their baseline component.

Missing pointer members, missing readers and unsupported/ambiguous source evidence
are unavailable, not historical defaults. Explicit zero, false or null are not
missing. Malformed declarations, non-finite/live values, exceptions and invalid
reader returns are errors; they are not silently converted to unavailable.

Template-associated run summaries add:

```json
{"effective_controls": {
  "horizon_s": {"status": "known", "value": 90.0, "evidence_digest": "<64 lowercase hex characters>"},
  "other": {"status": "unavailable", "reason": "No explicit pinned source"}
}}
```

This is additive: old `knobs` and historical `template.knobs` are unchanged.
Pinned controls use detached snapshot declarations, not today's template catalogue.
If a pinned dependency is unavailable or has drifted, the summary remains readable:
readable pointers stay Known, hooks become Unavailable, `results_unavailable` gives
an explanation, `results_final` is false and `results_cursor` is null. KPIs are
omitted even with `include_kpis=true`; stale cached measurements are not served.
Malformed snapshots still fail. Legacy reads use current declarations/readers;
no declarations means `{}`. An authoring hook alone does not supply an inverse reader.

## Family snapshots, selection and cursors

`presentation_snapshot(plugins, controls=tuple_of_declarations)` creates a detached
`aeroagentsim.presentation-snapshot/v1` document—**not** the DATA format. It records
provider source, name/API/revision/data, normalizer implementation/ID/version and
input/output metadata, control-reader implementations, optional control
declarations, and a `sha256:` digest. No executable implementation is embedded.
Stored metadata, output schemas and owned IDs validate without importing stored code.

The HTTP service's `app.launch` path admits bindings and writes
`presentation.snapshot.json` (including controls) plus its expected digest in the
run manifest **before worker start**. Direct `RunSession` and experiment paths
have not been changed to pin; not every launch has this sidecar.

[`read_presentation_snapshot` and `presentation_selection`](../../src/aeroagentsim/presentations.py)
read/validate the sidecar and select its provider names.
`verify_presentation_snapshot(snapshot, available)` rejects missing providers or
source/API/revision/DATA/normalizer-metadata drift. Pinned directory projection
loads only those names: an extra installation does not activate a new provider.
Malformed snapshots, missing marked sidecars and manifest-digest mismatches fail.
Only unmarked directories without a sidecar use legacy current discovery; an
unmarked but present sidecar is still validated. Never remove a pin to force compatibility.

`NarrativeProjector.from_directory(..., plugins=...)` and header projection verify
an explicit selection against a present pin too; `()` cannot bypass one. Without
a pin, `None` discovers providers and `()` selects none. The direct constructor
also supports explicit plugins. Narrative cursors retain their display-contract
digest and reject mismatches; cache identity covers even empty narrative catalogues.

**This is a presentation-family pin, not a cross-family/executable run lock.**
Compatible implementations must still be installed. Common cross-family locking,
source/byte integrity and automatic implementation restoration remain deferred.
Bump provider revisions and normalizer versions when code/checkpoint meaning
changes: metadata digests do not hash source, certify purity or authenticate
recorded evidence. Current installed Huangpu assets remain the exception below.

### Legacy city resource exception

The finite `header` dialect supports `engine_gusts` (`plugin`, `fields`), `lighting`
(`type_id`, `field_id`, allowed `values`) and `scenes` (`when`, `resource`, `visuals`).
Engine hints union, sort and merge touching authored intervals; empty unions add
no field. Lighting uses the first authored entity of the exact declared type that
carries the fact. Scene selectors compare literal top-level scenario text values;
visual maps bind exact type IDs to one or both `model`/`catalog` names. Model names
match `[a-z0-9][a-z0-9_-]*`, not arbitrary paths.

The only named resource is `legacy-huangpu/v1`, implemented by the finite
[host compatibility adapter](../../src/aeroagentsim/presentation_resources.py),
not a provider factory/callback or arbitrary import/file path. It intentionally
reads **current installed assets**, retaining lite/detailed scene selection,
installed-pack metadata and the model catalogue. Installing assets can therefore
change an old run's city presentation. HTTP URL absolutization also remains a
transport adapter. These are explicit exceptions to a fully pinned-header claim;
simulation facts and event evidence are unchanged.

Frozen `studio-scene.json` geometry takes precedence **before any city resource
lookup**. The generic `authoring.scene_header` adapter remains responsible for
run-local scene geometry. Header extraction retains builtin viewer-feed/v1
bytes; contributed hints use only the optional DATA extension described above.

See [viewer feed](viewer-feed.md), [metric plugins](metrics-plugins.md) and
[plugin contracts](plugin-contracts.md) for the separate measurement, transport
and runtime extension boundaries.
