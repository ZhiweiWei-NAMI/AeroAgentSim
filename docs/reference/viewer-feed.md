# Viewer feed reference

The viewer feed is the contract between a run's journal and any browser
client. Header and commits are projected in
[`src/aeroagentsim/services/projector.py`](../../src/aeroagentsim/services/projector.py);
the console validates them in
[`frontend/src/feeds/http.ts`](../../frontend/src/feeds/http.ts) against
[`frontend/src/contracts/viewer-feed.ts`](../../frontend/src/contracts/viewer-feed.ts).
Contract string: `aeroagentsim.viewer-feed/v1`.

## Transport

- `GET /v1/runs/{id}/header` — one pinned header.
- `GET /v1/runs/{id}/commits?from=N&limit=M` — paged commits (replay/polling).
- `GET /v1/runs/{id}/stream?from=N` — SSE `commit` / `status` / `end` events,
  `Last-Event-ID`-resumable.

Clients must not assume a fixed commit cadence; commit indices are dense
journal indices including empty intent/control records.

## Header

| Field | Meaning |
| --- | --- |
| `contract`, `runId` | Contract string and service run identity. |
| `epoch`, `kernelRunId` | Present when applicable; the kernel identity behind the service run. |
| `types[]` | `{typeId, displayName, ancestors[], directory?}` — browsing metadata from the run's pinned snapshot, not today's mutable source tree. |
| `fields[]` | `{fieldId, displayName, valueType, unit?, frame?, schema, metadata, role?}` — unit/frame come from field metadata. |
| `presentation[]` | Authored viewer bindings (`typeId`, `positionField`, `frame`, `visual`, optional `orientationField`). |
| `start`, `end?` | Canonical start instant; `end` present for terminal runs. |
| `runtimeRegistry`, `messages` | Full pinned registry data. |
| `messageSubjects?` | Authored subject bindings keyed by schema id. |
| `behaviour?` | Pinned behaviour packages: evaluator, package identities, predicates, chains, bindings, and `extensions` (`predicate-truth/v1`, `chain-instance/v1`). |
| `origin?` | Authored geodetic origin when present. |

The header is rebuilt from run-directory artifacts (`runtime.registry.json`,
`scenario.json`, `registry.snapshot.json`, `behaviour.ir.json`); it never reads
a mutable source tree at serve time.

## Commits

Each commit: `commitIndex`, `at` (`{ns, microstep}`; `ns` is a decimal string)
and these arrays:

| Array | Entries |
| --- | --- |
| `created` / `removed` | `{id, generation, typeId?}` entity keys. |
| `facts` | `{entity, fieldId, value, producer, validFrom, validTo?, available, acquired, version:{journalIndex,itemOrdinal}, causes[]}` — one entry per (entity, field) written in the commit. |
| `retracted` | `{entity, fieldId, validFrom, validTo?, available, reason?, ...}`. |
| `edges` | `{edgeId, relationId, source, target, op: assert\|close\|cancel, validFrom?, validTo?, acquired, available, version, causes[]}`. |
| `messages` | `{id, kind: command\|event, schemaId, source, at, payload, subjects?, target?/topic?}` — subjects are typed `$ref` values or declared payload paths, never arbitrary strings. |
| `receipts` | `{commandId, status, result?}`. |
| `predicateTruth?` | Behaviour extension (below). |
| `chainInstances?` | Behaviour extension (below). |
| `kpis` | Cumulative measured KPI map `{name: {value, unit, label}}` at this committed cut. |
| `events` | Business narratives `{at_ns, category, severity, text, entities: [ids], position?: [e,n,u]}` generated from committed event emissions. |

Lossless numbers: integers or floats at or above 2⁵³ in magnitude travel as
`{"$integer": "..."}` / `{"$number": "..."}` tags (optionally wrapped in
`{"$record": {...}}`) instead of losing precision; clients validate and unwrap
them.

## KPIs and business narratives

`kpis` is the additive `aeroagentsim.kpi/v1` projection. It includes task
creation/completion counts, mean and p95 task latency in seconds, energy used
in kJ, distance flown in km, separation conflicts, geofence violations and
weather holds. Lifecycle counts consume `lowalt.task.*` emissions. Conflicts,
geofences and weather consume the common `lowalt.airspace.*` and
`lowalt.weather.*` schemas. Energy and distance are derived from committed
physical facts and their pinned units, rather than engine objects.

A missing energy/position measurement or a latency with no completed tasks
remains `null`. No events of a counted kind in the committed prefix means a
count of zero. Traffic accident reporting, award and accepted capture upload
map to the response-task lifecycle. KPI snapshots at arbitrary paging/SSE
cursors include the earlier journal prefix, so reconnecting does not reset
metrics. `/v1/runs/{id}/kpis` exposes final values for the available prefix and
their time series; `/v1/runs` includes the same summary.

`events` is the additive `aeroagentsim.event-feed/v1` projection. It narrates
committed business emissions once, retaining entity ids from declared
payload fields. This is independent of the typed `messages[].subjects`
contract. Category and severity come from narrative declarations. A camera
position is included only when the event or recorded entity state supplies
one. Internal kernel messages without a business declaration remain in the
raw message feed.

Packs and behaviour packages can supply schema declarations; scenarios pin
overrides in an `event_narratives` mapping. Each declaration supplies `template`, `category`,
`severity` and entity-field bindings. Task narratives retain the kind from
creation so later assignment/completion events use the same category.
Template placeholders must name real payload fields; malformed declared
business events are errors, not invented success messages. See
[templates](../guides/templates.md) for an authoring example.

## Behaviour extensions

`predicateTruth` rows: `{contextId, predicateId, profile, roles, status:
known|required_input|invalid_input, value: boolean|null, diagnostics[],
evaluatedAt, readCut, validFrom, op: assert|close, acquired?}`. A non-`known`
row must carry diagnostics and has `value: null` — unknown is represented, not
hidden.

`chainInstances` rows: `{instanceId, templateId, bindingId,
state, lifecycle: created|transitioned|completed|failed|canceled, revision,
roles, variables, children, transitionId?, trigger?, evaluation?, validFrom,
op?}`. Both arrays carry the same temporal metadata (`validTo`, `available`,
`version`, `causes`) as facts; the temporal store selects the row valid at the
viewer's current instant and cut.

## Subjects and compatibility notes

`messageSubjects` in the header maps a message schema id to bindings of the
form `{path, type_id, generation_path?}`: `path` locates a string or typed
`$ref` member in the payload, `type_id` constrains the resolved entity type,
and `generation_path` optionally locates the recorded generation so a message
can reference an earlier entity generation rather than the live one. String
subjects resolve against entity lifecycle recorded up to the message's commit;
unknown identities are rejected, never defaulted.

Field `displayName`, type `displayName`/`directory`, and fact-level display
hints are presentation-only fields. The console also applies local, cosmetic
label rules (for example humanizing `traffic.road.speed_mps`) on top of the
feed; these affect display names only — retained identities, values and
temporal metadata always come from the recorded feed unchanged.

## Persistent projection index

REST commit paging and SSE reconnects use `viewer-index.sqlite3` beside each
run. This rebuildable index stores projected cuts and one transactionally
consistent checkpoint for lifecycle subjects, KPIs and narratives. A seek
reads indexed cuts directly; newly committed records extend the retained
prefix. Changes to pinned declarations or projection code invalidate the
cache. Journal and registry data remain authoritative.
