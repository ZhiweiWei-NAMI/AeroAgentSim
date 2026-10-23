# Public run reader (API v1)

`aeroagentsim.runs` reads local recorded evidence without importing service storage,
projectors or kernel codecs in consumer code. It does not execute a simulation.

```python
from aeroagentsim.runs import RUN_READER_API_VERSION, open_run

assert RUN_READER_API_VERSION == 1
with open_run(directory, api_version=1) as run:
    snapshot = run.snapshot()  # one validated, acknowledged byte/index cut
    for message in snapshot.iter_messages(kind="event", schema_prefix="rescue."):
        consume(message.message_id, message.payload, message.cursor)
```

The reader API version is independent of scenario `plugin_api.API_VERSION`, package
versions and journal versions. The three core reader envelope dataclasses are
frozen and carry `api_version = 1`. Incompatible envelope meanings require a new
reader API version. The additive `ProjectionProvenance` DTO below has its own
`format`; it does not change `RUN_READER_API_VERSION`.

## Opening and capturing

`open_run(directory, *, api_version=1)` returns a `Run` context manager. `directory`
is a local directory, not a URL or archive. Wrong or non-integer API versions
(including booleans) raise `ValueError`. `run.header` is pinned when opened;
`run.snapshot()` captures a **new** acknowledged cut on each call.

V1 requires the run owner's explicit `committed-prefix.json` ACK sidecar. It never
promotes visible WAL bytes, `index.json`, terminal status or a caller-supplied cut
to an acknowledgement. Older/headless runs with only an offset index are rejected,
even if terminal. In particular, a headless `Simulation` or `RunSession` does not
itself guarantee that the ACK sidecar was published. Legacy index-only admission,
archive reading and recovery of lost-ACK tails are **not available in v1**.

A snapshot reads the ACK coordinate once, checks that its bytes end on a complete
record, and reconciles its inclusive index against a dense journal sequence
starting at header index zero. It builds offsets and frame hashes in memory;
missing, stale or corrupt derived indexes do not matter. Missing/invalid ACKs,
including an empty coordinate, fail rather than produce an empty successful export.
An ACK containing only a complete header at index zero is valid.

A snapshot's cut never advances, even if the journal and ACK later grow. Request
another snapshot to see a newer cut. New captures also reconcile previously retained
frame hashes; ACK rollback or rewritten retained evidence raises `RunReplaced`.
Unacknowledged complete records and partial tails are invisible. Snapshots and
iterators are usable only inside their `Run` context; closing it invalidates reads
and releases open iteration streams.

## Envelopes and iteration

### `RunHeader`

| Field | Meaning |
| --- | --- |
| `service_run_id`, `kernel_run_id`, `epoch` | Recorded service identity and kernel namespace; no ID is derived from a directory name. |
| `journal_format`, `journal_major`, `journal_minor` | Admitted WAL format and version. |
| `journal_codec` | Exact header codec ID, otherwise the recorded service codec name; `None` if neither was recorded. |
| `semantic_version`, `policy_version` | Exact header values, or `None`; no version is invented for old headers. |
| `provenance` | Effective admitted `lean` or `full`, not a scenario override or viewer label. Historical admitted full formats define full provenance without a separate header field. |
| `configuration_digest`, `registry_digest` | Recorded service configuration digest and effective header registry digest; not newly computed hashes or an authored registry-digest claim. |
| `created_at` | Recorded service `created_at` string, if present; never filesystem creation/modification time. |
| `journal_identity` | Local `(device, inode)` source identity, not a portable signature or attestation. |
| `engine_versions` | Recorded `(engine_id, version)` pairs as an immutable tuple, or `None`; not every installed dependency revision. |

An absent service manifest is permitted; its unknown fields are `None`. Malformed
JSON/non-object metadata and invalid consumed metadata fields fail. Unrelated
service fields are not validated. The kernel header remains authoritative for
provenance.

### `Snapshot`

`header`, `cut_index` (inclusive last ACKed record) and `cut_bytes` identify the
captured source cut. `snapshot.iter_records(start=0, limit=None)` yields detached,
expanded diagnostic dictionaries, including the header at zero. `start` is an
inclusive **journal record index**, not a byte offset or a filtered-row count.
`limit` is the maximum number of records; `None` reads through the cut, zero yields
nothing, and starting beyond the cut yields nothing. Negative, boolean or
non-integer bounds raise `ValueError`.

```python
with open_run(directory) as run:
    snapshot = run.snapshot()
    start = 0
    while start <= snapshot.cut_index:
        page = list(snapshot.iter_records(start=start, limit=64))
        consume_records(page)
        start = page[-1]["index"] + 1
```

Record dictionaries preserve admitted kernel diagnostic vocabulary, not viewer
aliases. Callers needing only messages should use the normalized envelope below
rather than depend on internal operation shapes.

### `RecordedMessage`

`snapshot.iter_messages(kind=None, schema_prefix=None, start=0)` yields original
admissions/emissions in journal/item order. Both filters must match when supplied;
`schema_prefix` uses literal string-prefix matching. `start` has the same inclusive
record-index meaning as `iter_records`; it is not a message ordinal. Identity
selection happens **before** filtering and paging, so a later publication does not
reappear merely because its original admission precedes `start`.

| Field | Meaning |
| --- | --- |
| `cursor` | `(record_index, item_ordinal)`, with a zero-based ordinal in the source record. |
| `message_id`, `kind`, `schema_id` | Original opaque identity, message kind and schema where recorded. |
| `route`, `topic`, `target` | Recorded original route. An Emit route matching a recorded partition is a target; other event routes are topics. Subscriber recipients are not substituted for event topics. |
| `source_kind`, `source` | Recorded producer namespace/name. For ingress admissions, an omitted source may come from the recorded header's `ingress_source`/`control_source`; IDs are never parsed to infer a producer. |
| `origin` | Untagged `{"record_index": int, "item_index": int}` reference, or `None`; not necessarily the envelope's own cursor. |
| `at_ns`, `microstep` | Recorded occurrence/requested activation instant, as Python integers. |
| `available_ns`, `available_microstep` | Recorded publication instant, distinct from occurrence, queue eligibility or delivery. |
| `sequence`, `source_stamp` | Recorded sequence and untagged rational-clock stamp dictionary, or `None`. |
| `payload` | Detached plain JSON-compatible Python tree: dictionaries/lists/scalars/null. Integers remain lossless Python `int`s; entity references remain dictionaries, retaining their namespace and generation. |

Unknown values are always `None`, never inferred from current state, planned
boundaries, filesystem times or ID spelling. No viewer `$integer` tags or float
conversions are introduced. Payload keys such as `$type` and `$ref` remain payload
keys, not executable class lookups. Envelope freezing does not freeze the detached
payload/reference dictionaries; mutating them cannot change subsequent reads.

Each opaque message ID appears once, not once per delivery or once per distinct
payload. Equal-payload messages with different IDs remain distinct. Dispatch/inbox
copies, rejected requests without an admitted ID, receipts and derived console
aliases are not extra messages. A pending ingress reservation is an original
admission even before publication; its availability remains `None`. An explicit
Message in the **same atomic record** is preferred over its offline-request stub.
Later records never enrich an earlier canonical admission. Host cancellation
admissions and partition cancellations can therefore have IDs but no recorded
schema, payload or message times; those fields stay `None`.

### `ProjectionProvenance`

```python
from aeroagentsim.runs import ProjectionMode, projection_provenance

provenance = projection_provenance(directory)
if provenance.mode is ProjectionMode.RECOMPUTED_CURRENT_CODE:
    compare(provenance.pinned_definitions, provenance.current_definitions)
```

This frozen DTO is exported from `aeroagentsim.runs` and defined in
[`runs/projections.py`](../../src/aeroagentsim/runs/projections.py). Its separately
versioned `format` is `aeroagentsim.projection-provenance/v1`, not a new reader API
version. Nested dictionaries/lists are detached caller-owned JSON trees, not
recursively frozen objects. `dataclasses.asdict()` produces a serializable view;
`ProjectionMode` is a string enum.

| Field | Meaning |
| --- | --- |
| `source_cut` | Reader-derived `service_run_id`, `kernel_run_id`, `epoch`, local `journal_identity` (`[device, inode]`), recorded `configuration_digest`/`registry_digest`, inclusive `cut_index` and `cut_bytes`. `None` if no ACK sidecar exists; no offset index or visible WAL is substituted. |
| `pinned_definitions` | The complete run-time projection definition record, or `None` when absent. A presentation snapshot alone is not a complete projection pin. |
| `current_definitions` | Identities of current host interpreters and discovered contributions, plus the authored inputs/presentation snapshot available in the run directory. No projected numbers are computed. |
| `mode` | `ProjectionMode.ORIGINAL` (`original`) only with a matching complete pin; `RECOMPUTED_CURRENT_CODE` (`recomputed-current-code`) with a differing pin; `LEGACY_UNPINNED` (`legacy-unpinned`) without a complete pin. |

Definition records contain `viewer_feed: "aeroagentsim.viewer-feed/v1"`,
`kpi: "aeroagentsim.kpi/v1"`, `implementation_digest` (host interpreter source
content), `metric_reducers` (the public metric contribution fingerprint, including
reducer implementation, revision, input schemas and full descriptors),
`presentation_snapshot_digest`, `presentation_provider_digest`, and
`authored_digests` for scenario/runtime-registry/behaviour inputs. Absent historical
inputs have `None` digests, not hashes of invented empty documents. A snapshot
digest describes the stored DATA; the provider digest describes currently available
providers in that snapshot's recorded name/order selection. It can differ without
replacing or rewriting the stored snapshot.

`projection_provenance(directory)` is a filesystem-read-only **metadata diagnostic**:
it creates no sidecars, repairs no indexes and opens no SQLite databases. With an
ACK, it captures and validates a snapshot through `open_run`; malformed/empty ACKs
raise reader errors, not an unknown or empty successful cut. Without an ACK, it
compares definitions only and returns `source_cut=None`; the raw reader's index-only
admission policy is unchanged. Invalid/unsupported projection pin records and
invalid presentation snapshots raise `ValueError`; unavailable or invalid current
contributions also fail rather than imply matching definitions.

Unlike raw `open_run`, this opt-in helper discovers trusted metric/presentation
plugin factories to identify current definitions. It does not instantiate reducers'
state, execute reducer/projection callbacks, construct simulation engines, or call
network clients. Plugin factories are trusted Python, not a sandbox. Merely importing
`aeroagentsim.runs` does not trigger this discovery.

New service workers record `aeroagentsim.projection-pins/v1` in
`projection-pins.json` at the new-run evidence-writing point before execution; no
read path backfills missing pins. Earlier service runs (including presentation-only
pins) and unintegrated headless hosts remain `legacy-unpinned`. Matching identities
do not prove historical executable availability, all dependency revisions,
authentication, numerical correctness or run success. Changed plugin implementations
without an honest revision/distribution bump cannot be detected by declared identity.
A diagnostic mismatch does not prove successful recomputation: HTTP feeds still
reject missing/changed pinned presentation dependencies. See
[viewer-feed exposure and modes](viewer-feed.md#optional-projection-provenance).

## Admission, checks and errors

Supported families are full JSON journals 1.1–1.3, lean JSON 1.4, full 2.0
`positional-deflate/v1` (semantic version 2 or 3), and lean 2.1
`canonical-deflate/v1` (semantic version 4). Lean policy versions must be 2 or 3.
Unsupported versions/codecs are rejected, not guessed from missing payload fields.
Compression and strict JSON parsing use existing kernel decoders and the recorded
frame/digit/depth budgets. Bootstrap headers and service manifests are bounded at
8 MiB; ACK sidecars must be regular files of at most 4 KiB. Symlinked journal,
manifest and ACK files and non-regular source files are not supported. No paths
inside recorded configuration or payloads are followed.

The following admission/error guarantees apply to the core reader, not plugin
discovery or definition-pin parsing in the opt-in diagnostic above. All expected
core read failures derive from `RunReadError`:

| Error | Meaning |
| --- | --- |
| `UnsupportedRunFormat` | Unsupported format, version, codec, provenance policy or source container. |
| `NoCommittedPrefix` | Missing/unreadable/invalid ACK, missing journal, bytes beyond EOF, an incomplete acknowledged boundary, or byte/index disagreement. |
| `CorruptRecord` | Malformed acknowledged metadata, JSON/frame/message, or non-contiguous record coordinates. |
| `RunReplaced` | Directory/journal identity changed, retained bytes were truncated, the header changed, or an accessed captured frame differs from its retained hash. |
| `RunReadError` | Other source I/O failures or use after context closure. |

Opening admits the header; capturing validates the acknowledged frames and
recognized original message envelopes. Reads check source identity, retained length
and hashes of accessed frames.
A same-inode edit in an unaccessed frame is not a whole-run integrity scan. Concurrent
in-place rewriting is not supported; ordinary append and atomic ACK publication are.

## Guarantees and non-guarantees

- **Filesystem-read-only core reader:** no writes, renames, touches, index repair on
  disk, SQLite caches, engines, plugin discovery, providers or network calls. The OS may
  update access times as a consequence of ordinary reads; access-time preservation
  is not promised. Directory entries, file bytes, sizes and modification times are
  not changed by the reader.
- **Decoding is not semantic replay verification.** The reader checks the admitted
  format, framing, recognized original message envelopes, coordinates and captured
  source stability. It does not regenerate/verify effects or prove schema/authority
  conformance of the entire history, and offers no replay-verification API in v1.
- **Integrity is not authentication.** Frame hashes detect changes relative to this
  local capture, not a consistently rewritten history or a trusted signer.
- **A readable ACK is not run success.** Terminal/faulted/interrupted status,
  projection progress, `results_final`, replay completeness, command acceptance and
  command success are separate concepts. Faulted/interrupted prefixes remain
  readable. This API provides no status/control/finality projection.
- **Lean detail is unavailable.** It cannot restore discarded per-output read/cause
  evidence, prove actual reads, or establish scientific causal attribution.
- **Evidence is not automatically secret-free.** Recorded configuration and
  prompts/payloads may contain sensitive supplied data. Access control and clearly
  identified redacted derived exports remain the consumer/operator's responsibility.
