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
| `projection_provenance?` | Separately versioned definition pins, current identities and the reader source cut for this commit (below). |

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

## Optional projection provenance

REST/SSE commit objects and the top-level `/v1/runs/{id}/kpis` response may carry
`projection_provenance`, with `format: "aeroagentsim.projection-provenance/v1"`.
Existing viewer-feed/v1 and KPI/v1 fields, numerical definitions, nulls and finality
semantics are unchanged. KPI series rows and the viewer header are unchanged.
Summary-only `kpi_result(..., include_series=False)` results (including run-list
summaries) omit provenance: these remain byte-stable numerical payloads across
identical service/headless journals, independent of run-local IDs, inodes, and pin
presence. The full KPI endpoint and the read-only diagnostic expose provenance.

The field contains `source_cut`, `pinned_definitions`, `current_definitions` and
`mode`. The source uses the [public reader's identities](run-reader.md): service
and kernel run IDs, epoch, local journal identity, recorded configuration/registry
digests, inclusive `cut_index` and `cut_bytes`. Each commit identifies its own cut;
the KPI response identifies its final projected prefix, not a later live ACK.
Index-only runs without a reader ACK and service-readable sources the public
reader cannot admit (for example symlinked journals, manifests or ACK sidecars)
omit this optional response field rather than invent a cut or fail a previously
readable feed. Reader v1 admission itself is unchanged. Malformed projection pins
are rejected, not interpreted as known definition drift.

| `mode` | Meaning |
| --- | --- |
| `original` | A complete run-time projection pin exists and matches the identities used for this projection. |
| `recomputed-current-code` | A complete run-time pin exists, but the projected definitions differ. Both recorded and current identities remain visible. |
| `legacy-unpinned` | No projection pin exists. `pinned_definitions` is `null`; current definitions are not evidence of historical meaning. |

New service worker executions durably publish `projection-pins.json` before
starting the session, after its authored artifacts exist. A unique temporary file
is flushed/fsynced, atomically installed without replacing an existing pin, and
its directory fsynced before the first world-state ACK; publication failure aborts
execution. This ordering applies even with journal `flush` durability.

The pin records viewer-feed/v1 and KPI/v1 identities, a content digest of host
projection/interpreter code, metric reducer descriptors/revisions and contribution
identities, authored input digests, and the
presentation snapshot digest (when present) plus provider digest. HTTP launch
already records `presentation.snapshot.json`; its recorded provider selection is
preserved. Older runs, including those with only presentation pins, are not
backfilled. Headless `RunSession`/`Simulation` paths do not acquire projection pins
through this service-only writer.

A matching pin proves matching **recorded identities**, not an archived historical
executable, authentication, all transitive dependency versions, run success, or
byte-identical retained results. Reducer implementations still rely on honest
revision/distribution changes. Cache invalidation alone never proves `original`.
Missing measurements stay `null`; recorded links are not causal labels.

The read-only `projection_provenance(directory)` diagnostic can report changed
presentation provider identities without projecting any numbers. This does **not**
bypass presentation snapshot admission: service feeds still reject missing or
changed pinned providers rather than silently substituting newer DATA. Its
`recomputed-current-code` mode describes a definition mismatch, not proof that a
recomputation succeeded. See the [DTO reference](run-reader.md#projectionprovenance).

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
prefix. Definition identities and reader admission/omission are computed once
per source/definition signature; provenance is published transactionally with each
cut and the checkpoint. Warm pages reuse those bytes without reconstructing storage
offsets or reparsing the journal header for metadata. Source identity and ACK bounds
are still checked, as are presentation admission and authored-content changes.
Changes to pinned declarations or projection code invalidate the cache. Journal
and registry data remain authoritative.

**Pre-existing limitation:** service feed reads are not source-read-only. REST
paging, KPI evaluation and SSE's cold-cache fallback can create this SQLite file
and its `-wal`/`-shm` sidecars **inside the source run**, including archived runs;
they require a writable cache location there. This PR does not relocate or redesign
the feed cache. The file-free guarantee belongs to the standalone
`projection_provenance(directory)` diagnostic (and the core reader), not these
service endpoints. Use that diagnostic when inspecting source evidence without
creating derived files.
