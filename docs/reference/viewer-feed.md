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

Lossless numbers: integers or floats at or above 2⁵³ in magnitude travel as
`{"$integer": "..."}` / `{"$number": "..."}` tags (optionally wrapped in
`{"$record": {...}}`) instead of losing precision; clients validate and unwrap
them.

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
