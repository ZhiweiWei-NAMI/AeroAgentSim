# Metric plugins (API v1)

`aeroagentsim.metrics` is the entry-point family for deterministic metric reducers.
Import the public contracts from
[`aeroagentsim.metrics_api`](../../src/aeroagentsim/metrics_api.py), not service
projectors or private pack state. `METRICS_API_VERSION = 1` is independent of the
scenario contribution API, [run-reader API](run-reader.md), package releases and
journal versions.

Reducers fold recorded evidence into scalar measurements. They are trusted Python,
**not a sandbox**: mutate only the supplied reducer state; do not perform I/O, call
providers, execute engines, change runtime state, consult wall time or use unseeded
randomness. `snapshot` must not mutate state. Discovery and numerical validation do
not prove these author obligations.

## Contract

| Surface | Required meaning |
| --- | --- |
| `MetricDescriptor` | Frozen dataclass with `id`, `label`, `label_zh`, `unit`, `direction`, `aggregation`, `missing_data_policy`. All text fields are nonempty; `direction` is `"higher"`, `"lower"` or `None`. State the population, denominator, accumulation rule and missing-data meaning explicitly. |
| `MetricPlugin(name, reducers, api_version=1)` | Frozen contribution envelope. Names match `[a-z0-9][a-z0-9_-]*`; `reducers` is a tuple. The version must be the supported actual integer: `True`, `1.0` and `"1"` are not version 1. |
| `reducer.descriptors` | Nonempty tuple of `MetricDescriptor` values. Each metric ID has one owner across the selected plugins; duplicate IDs are errors, not automatic merges. |
| `reducer.input_schemas` | Nonempty tuple of nonempty **literal schema-ID prefixes**. Dispatch uses `schema_id.startswith(prefixes)`, not regexes, globs or exact-match-only subscription. A missing schema does not match. |
| `initial()` | Return fresh, independent state for one accumulator. Do not store a run's mutable state on a shared reducer object. |
| `consume(state, message) -> None` | Synchronous, ordered state mutation over `RecordedMessage`, or an equivalent envelope normalized by the accumulator. Return `None`, not replacement state. Validate the recorded fields needed by the metric. |
| `snapshot(state)` | Return a mapping containing **exactly** the declared IDs, with scalar values `int`, finite `float`, or `None`. Booleans, strings, descriptor rows, NaN and infinities are invalid. |

All selected descriptors are visible from initialization, even before matching
evidence. Schema matching controls consumption, not whether a metric appears in
the catalogue. Use `None` for an unobserved measurement; an observed zero is `0`.
Historical builtin population counters explicitly define an empty population as
zero. The host validates the returned values but does not invent a missing-data
policy for a reducer.

A changed scientific meaning requires a **new metric ID**. Renaming a label,
changing a denominator or combining two populations must not silently reinterpret
an existing metric.

## Register an external reducer

An entry point exports a `MetricPlugin` value or a zero-argument factory returning
one. For example, a distribution can declare:

```toml
[project.entry-points."aeroagentsim.metrics"]
acme = "acme_metrics:plugin"
```

`acme_metrics.py`:

```python
import math

from aeroagentsim.metrics_api import MetricDescriptor, MetricPlugin


class ObservedDistance:
    version = "1"
    input_schemas = ("acme.flight.",)
    descriptors = (
        MetricDescriptor(
            id="acme.distance_m",
            label="Observed segment distance",
            label_zh="已观测航段距离",
            unit="m",
            direction=None,
            aggregation="Sum distance_m from acme.flight.segment events, once per message ID.",
            missing_data_policy="None before a segment; a recorded zero measures zero.",
        ),
    )

    def initial(self):
        return {"total_m": None}

    def consume(self, state, message):
        # Prefix dispatch can include other schemas and non-event admissions.
        if message.kind != "event" or message.schema_id != "acme.flight.segment":
            return
        if not isinstance(message.payload, dict):
            raise ValueError("acme.flight.segment requires a mapping payload")
        value = message.payload.get("distance_m")
        if (type(value) not in (int, float)
                or (type(value) is float and not math.isfinite(value))
                or value < 0):
            raise ValueError("distance_m must be finite and nonnegative")
        previous = state["total_m"]
        state["total_m"] = value if previous is None else previous + value

    def snapshot(self, state):
        return {"acme.distance_m": state["total_m"]}


def plugin():
    return MetricPlugin("acme", (ObservedDistance(),), api_version=1)
```

Declare the literal `api_version=1` the plugin implements; do not copy the host's
current version constant and thereby claim compatibility with a future API.
This example has no custom checkpoint methods; it uses the history fallback below.

`discover_metric_plugins()` returns the sorted builtin/installed union. Installed
names override same-name builtins. Duplicate installed names fail **before any
entry-point load**; wrong exported types, non-zero-argument factories and mismatched
entry-point/plugin names fail rather than falling back to a builtin. Duplicate
plugin names or metric IDs in an accumulator's explicit selection also fail.

`MetricAccumulator()` discovers contributions. `MetricAccumulator(plugins=(...))`
uses that explicit selection instead; `plugins=()` selects none. Different
reducers may consume the same schema but must declare disjoint output IDs.

## Original messages and input boundaries

`RecordedMessage` comes from [`aeroagentsim.runs`](run-reader.md#recordedmessage).
Its cursor is `(record_index, item_ordinal)`; IDs are opaque; payloads are detached
JSON-compatible trees with lossless Python integers and full reference dictionaries.
Use recorded `source_kind`, `source`, `kind`, schema and times when the metric
requires them. Do not parse message IDs to infer a producer, substitute availability
for occurrence time, or convert absent values to zero.

The public reader returns original **emissions and admissions**, not an
emission-only or partition-only stream. Pending ingress commands may have
`available_ns=None`; cancellations may have no schema, payload or times. It removes
delivery/inbox copies and derived console aliases, selecting canonical identity
before filtering and paging. Later publication does not enrich an earlier
admission. A reducer must not assume every input is an ingress request, nor that
every admitted command was published, accepted or completed.

The accumulator dispatches by schema prefix only: it does not impose a universal
kind/source filter. Reducers must apply their own population rules. In particular,
rescue radio accounting needs command messages; a global event-only filter loses
that evidence. Default consumption deduplicates successful inputs by opaque
`message_id`, not by payload equality; equal payloads with distinct IDs remain
distinct. Preserve journal/item order when feeding chunks.

A message-only consumer can use the public reader without invoking service storage:

```python
from aeroagentsim.metrics_api import MetricAccumulator
from aeroagentsim.runs import open_run
from acme_metrics import plugin

accumulator = MetricAccumulator(plugins=(plugin(),))
with open_run("runs/example", api_version=1) as run:
    snapshot = run.snapshot()
    accumulator.feed(snapshot.iter_messages())
values = accumulator.snapshot()  # {"acme.distance_m": number_or_None}
```

This requires the reader's explicit acknowledged-prefix directory contract. It is
not semantic replay verification. A message-only fold does **not** supply physical
fact or relation evidence needed by some service KPIs.

### Host evidence adapter: facts, retractions and edges

The [service KPI adapter](../../src/aeroagentsim/services/kpis.py) retains raw
committed-operation support. It sends original `Emit` messages to the same
accumulator, ignores delivery-only copies, and separately adapts these operations:

| Host evidence schema | Minimal detached payload |
| --- | --- |
| `aeroagentsim.metrics.fact/v1` | `entity` as a full reference dictionary, `field_id`, `value`, the pinned field `schema` and `metadata`, entity type ancestry in `types`, and pinned `presentation` bindings. |
| `aeroagentsim.metrics.retract/v1` | `entity`, `field_id`. |
| `aeroagentsim.metrics.edge/v1` | Asserted edge `relation_id`, full reference dictionaries `source` and `target`. |

These envelopes have `kind="evidence"`, the source operation's cursor and record
instant, and synthetic IDs `metric-evidence/<record_index>/<item_ordinal>`.
Unrecorded envelope fields remain `None`. They are **not emitted kernel messages**,
are not returned by `Snapshot.iter_messages()`, and must not be counted as bus
traffic or fabricated by scenario engines. This is a minimal host adapter, not a
complete public fact-validity/edge-history API.

The host folds this evidence with message-ID deduplication disabled to preserve
historical physical baseline semantics. Retractions reset baselines without
subtracting accumulated totals; replaying old fact-bearing records is therefore
not generally idempotent. The feed index owns the committed prefix and resume
coordinate. Do not infer ACK bounds from an envelope or replay arbitrary old
physical frames into an advanced state.

## Checkpoints, versions and failure

The required reducer protocol is only `initial`, `consume`, `snapshot`. Optionally,
provide **both** `checkpoint(state)` and `restore(checkpoint) -> state` plus a
nonempty string `version`. Checkpoints must be detached, finite JSON data and
preserve every baseline, classification and semantic deduplication field the
reducer needs. Keep these methods deterministic and free of I/O.

Without that pair, `MetricAccumulator.cursor()` retains each reducer's matching
input history. Restoration creates fresh state and replays that history in order.
This supports small third-party reducers without a serialization API, but its
memory/checkpoint cost grows with matching history. Custom checkpoints can reduce
that cost. The accumulator also preserves its message-ID deduplication state.

Use `restore_cursor(cursor)` with the same selected plugins/reducers. Unsupported
cursor versions or changed selection identities fail rather than silently
reinterpreting state. Identity includes plugin name/API version, reducer order,
implementation module/class, reducer revision, input prefixes and descriptors.
Automatically discovered selections additionally retain entry-point/distribution
identity in their cursors; explicit in-process selections remain the caller's
responsibility to pin. Basic reducers without a revision default to `"1"`;
checkpoint-enabled reducers require an explicit version. Bump revisions when
implementation or checkpoint interpretation changes. Service checkpoint
representations are internal, not a promise to accept every historical private
`KPIProjector` cursor forever.

If a reducer raises during `consume`, or returns a non-`None` value, the fold may
already be partially mutated. The accumulator is then **poisoned**: the original
error propagates, further consumption and checkpoint creation fail, and there is
no automatic retry or rollback to pretend the input succeeded. Diagnostic
`snapshot()` calls remain possible, subject to their normal validation, but are
not a successful committed result. Restore a known-good checkpoint successfully
or rebuild from committed evidence before continuing. Invalid snapshot keys or
values raise instead of being replaced with null/zero. The service feed publishes
cuts and projector checkpoints transactionally, so failed aggregation must not
advance its committed results coordinate.

### Cache identity is not a pinned run lock

The [feed cache signature](../../src/aeroagentsim/services/feed_index.py) includes
a fingerprint of **all selected contributions**, including ones not yet matched by
any message. It records plugin names/API versions, entry-point identities,
distribution names/releases when available, and reducer implementation names,
revisions, input prefixes and descriptors. This replaces a fixed list of pack
source filenames.

Publish a new distribution release and update reducer revisions when their
implementation changes; in source checkouts, unchanged reducer revisions are not
a reliable detector of edited pack code. The metric fingerprint is JSON identity
data, **not a digest of reducer source**. The cache signature separately includes
a SHA-256 digest of effective narrative declarations, alongside source-file
identities. These identities/digests invalidate derived caches; they do not pin
plugins into a run, authenticate evidence or replace separately pinned compatible packages and
resources. A rebuild uses the currently selected implementations. A metric with a
new meaning still needs a new metric ID, not just a cache-version bump.

## Builtin meanings and compatibility

The shipped discovery names are:

| Plugin | Owned measurements |
| --- | --- |
| [`inspection`](../../src/aeroagentsim/packs/inspection_metrics.py) | Observed battery swaps, inspection observations and patrol visits. These are work counters, not the legacy polygon-coverage report. |
| [`logistics`](../../src/aeroagentsim/packs/logistics_metrics.py) | First-delivery on-time rate using committed logistics/food deadlines; deliveries without one remain unmeasured. |
| [`lowalt`](../../src/aeroagentsim/packs/lowalt/metrics.py) | Airspace/weather/cooperation counters, spraying distance and response time; physical energy/distance evidence; rescue measurements. |
| [`tasks`](../../src/aeroagentsim/packs/task_metrics.py) | Shared task lifecycle counts, completion rate, pooled mean/p95 latencies and the medical subset. |
| [`traffic_accident`](../../src/aeroagentsim/packs/traffic_accident/metrics.py) | Traffic report, award, stored capture and accepted upload event counts. |

`tasks` owns the common task IDs once. Lowalt and traffic evidence contributes to a
single namespaced population: ratios use pooled counts, mean uses pooled
first-completion samples, and p95 linearly interpolates at `(n-1)*0.95` over the
pooled samples. It does not average per-plugin means or percentiles. Traffic
latency is reported-incident to accepted-upload occurrence time; medical latency
requires recorded medical-kind creation before completion.

Rescue's historical scalars deliberately retain **last-writer** meanings:
`rescue_success` is the success of the latest first-per-incident outcome, not a
multi-incident success rate. Measured confirmation/delivery intervals can be
replaced by later outcomes, including explicit `None`. Radio packet IDs and
outcome incident IDs have their own deduplication rules. Bus bytes count original
`data_json` UTF-8 bytes, not reserialized JSON; bus counts include rescue commands.

Missing evidence stays distinguishable from observed zero. Task/population event
counts retain historical empty-zero semantics; energy/distance start `None` and
become zero upon a first qualifying baseline. Swaps/inspection/patrol become
measured only after their specified start/creation/observation evidence. Entirely
absent rescue evidence leaves all rescue feed values `None`; after rescue is
observed its empty counters are zero, while unknown success/times stay `None`.

## Service, comparison and CLI shapes

Reducers return **scalar mappings**. The service wraps them in the unchanged
[viewer/KPI shape](viewer-feed.md#kpis-and-business-narratives):
`{metric_id: {value, unit, label}}`. The full descriptor catalogue remains available
on the accumulator/projector; the feed does not replace each metric row with the
new descriptor dataclass. Run summaries and comparisons consume the same KPI
projection. The comparison metadata catalogue includes installed descriptors even
when their samples are all `None`. An available-prefix `final` map is not a claim
that `results_final` is true; terminal status and completed KPI/Story projection
remain separate.

The [CLI routing](../../src/aeroagentsim/services/kpis.py) is intentionally explicit:

| Input/surface | Output and admission |
| --- | --- |
| `aeroagentsim metrics <service-run-directory>` with `runtime.registry.json` | Uses generic `project_run(..., include_series=False)`, the same indexed KPI projection as live views/comparison. Returns `aeroagentsim.kpi/v1` with descriptor-wrapped `final`, `series: []` and result-finality fields. It may update the derived service index. |
| `aeroagentsim metrics <journal.jsonl>` or a directory without the service registry sidecar | Uses the [legacy pack-report facade](../../src/aeroagentsim/packs/metrics.py), with complete replay-integrity checking and incomplete-journal rejection. |
| `aeroagentsim.packs.metrics.compute(kernel)` / `.metrics(path)` | Retains the legacy `{run_id, duration_s, <engine_id>: {pack, ...}, ...}` report, with configured rescue scalars at top level, not the service KPI envelope. |

The old report is not an alias for the new service shape. Its logistics rate uses
on-time deliveries divided by **released orders**; the service rate uses deliveries
with a committed deadline. Its inspection section retains polygon coverage and
acquisition/revisit statistics. Configured rescue report counters retain zero when
no rescue evidence exists, unlike the wholly unmeasured service rescue rows. Do not
merge these shapes or denominators under the guise of plugin extraction.

See [plugin contracts](plugin-contracts.md) for other extension families. Metric
plugins grant no runtime write authority, and no service/index/pack-private object
becomes a public plugin API merely because the host uses it.
