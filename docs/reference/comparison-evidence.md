# Experiment and comparison evidence

These are **descriptive comparisons of recorded evidence**, not causal estimates,
proof of mission completion or guarantees of deterministic live inference.
The existing `aeroagentsim.experiment/v1` and
`aeroagentsim.run-comparison/v1` fields retain their meanings; evidence fields are
additive. Alignment remains **`recorded_run_end`**: each run's selected recorded
prefix end, not a shared simulated-time horizon.

## Bounded experiment plans

`ExperimentManager.create` validates these per-experiment limits before copying
axes, materializing their Cartesian product, applying template configurations or
starting workers:

| Coordinate | Inclusive limit |
| --- | --- |
| `jobs` (active cell processes) | 1–32 |
| Configurations (product of axis lengths; an empty `vary` means one) | 1–256 |
| `seeds` (replicates per configuration) | 1–1,000 |
| Total configuration × seed cells | 4,096 |

Booleans are not integer counts. Invalid counts are rejected, not silently
clamped. Typed knob validation and positive finite horizon validation still
apply. These are per-experiment limits, not a global host quota. At most `jobs`
cells are submitted at once; there is no unbounded process-pool task backlog.

Matched seeds `0..seeds-1` select the platform's seeded simulation inputs/RNG
streams. They **do not control live model/provider randomness**, network timing,
external services or every plugin's random state. Equal seeds are not a promise
of identical model replies or identical outcomes.

## Cancellation and actual outcomes

`close()` stops only experiments owned by that manager and signals all their
coordinators before waiting. Admission after manager closure is rejected.
For active cells the shutdown policy is:

1. Request cooperative cancellation; poll at 50 ms intervals and allow one second
   to stop at a safe boundary.
2. Terminate remaining cell processes and allow one shared second to exit.
3. Kill survivors and allow one shared second to reap them.

The phase deadlines are shared across active cells, not multiplied by `jobs`.
Only process control/reaping occurs inside them; artifact parsing, ACK-index
recovery and report writing are deferred until live siblings have been controlled.
Those operations add to `close()` latency: its total wall time is not capped at
three seconds. These are process-control budgets under responsive OS scheduling,
not hard real-time guarantees against a stalled filesystem or unkillable OS task.
External/native descendants and already-issued model requests may have effects
that process termination cannot undo.

An interrupted cell retains its **last published acknowledged byte/index
prefix**. Visible unacknowledged tail bytes are not promoted to measurements,
completion or an ACK. Recovery bounds the derived index/final cursor to that
prefix; it does not invent a successful stop record. A cell cancelled before run
creation has no run identity or measurements. Cells not started are explicitly
identified rather than represented as successful runs.

Persisted terminal outcomes (`completed`, `faulted`, `stopped`, `interrupted`,
`input_timeout`) remain authoritative, even if result transport or KPI projection
fails afterward. Such a collection failure is recorded separately as
`collection_error`; a completed simulation is not rewritten as a different
terminal outcome just because its measurements are unavailable. Interrupted and
failed cells have no synthesized KPI values. Observed execution failures are saved
as `execution_outcome` before potentially blocking cleanup; this is not an early
success/finality marker. If cleanup is killed, recovery retains that fault/timeout
and records cleanup interruption separately. A run whose termination cannot be
confirmed retains its observed status (or `null`) with
`termination_confirmed: false` and `cancellation_requested: true`; it is not
represented as confirmed dead or assigned an invented terminal interruption.

## Experiment samples

The JSON report retains each cell's run identity, actual status/error and known
`cut` / `cursor`. The cursor is the **next** journal record index; a cut's `index`
is inclusive. Each aggregate metric keeps `mean`, sample `std`, `n`, `unit`,
`label`, and descriptive `best`, and adds:

| Field | Meaning |
| --- | --- |
| `definition`, `revision` | Recorded metric identity when supplied; otherwise `null`. Display defaults are not historical definition pins. |
| `compatibility`, `comparison_compatibility` | Compatibility within that configuration and across configurations, respectively. |
| `samples` | Included run/seed/cut evidence, actual value and descriptor. |
| `sample_exclusions` | Excluded cells and reasons: terminal failures/interruption, collection errors, absent measurements or explicit unmeasured/null values. |
| `sample_counts` | Requested, included, excluded and still-pending replicate counts; requested/pending stay `null` if the plan count is unknown. |

The report also exposes actual `outcome_counts` and excluded-run evidence.
Only completed cells without run or collection errors contribute numeric samples.
Unequal `n` is intentional and visible; missing values are **not zero-filled**.
A genuine observed count of zero remains zero. No samples means `mean = null`;
fewer than two samples means `std = null`. Counts here describe run-level samples,
not the number of business events underlying a KPI. Best flags are descriptive,
not significance tests, a combined ranking or a claim of controlled revisions.
The CSV retains its existing eight-column prefix and appends definition/revision,
compatibility and JSON-encoded sample/exclusion evidence. The JSON report carries
the full counts and outcomes; the Markdown report remains a summary.
Stored legacy reports receive unknown-compatibility annotations on read, without
recalculating their numbers or inferring missing sample evidence/exclusion counts.

## Compatibility of supplied comparison rows

`compare_runs(rows)` accepts existing projected run rows and preserves their
`values`, including real failed-run **prefix** measurements supplied by a caller.
Unlike experiment aggregation, it does not filter those prefixes as failed
replicates. `basis: "provided_rows"` makes this distinction explicit. It cannot
independently establish that a caller's summary and cursor came from one cut.

Each KPI table row adds `compatibility` and an `evidence` mapping keyed by run id.
Evidence includes the run id, supplied cut/cursor, definition/revision, run-level
`n`, `sample_exclusions`, status, and recorded source identities/revisions where
available. Absent source evidence remains `null`. For ordinary individual rows,
`n` is one for a numeric value and zero for a missing/null value; no failed prefix
is excluded by this function. Aggregate adapters can supply actual `n` and
`sample_exclusions`, including explicit unknown/null counts, without replacement.
Known counts must be nonnegative integers, not booleans. An independently observed
`results_cursor` is not promoted to a KPI source cursor.

Known unit, definition or definition-revision conflicts raise `ValueError`, even
if another row has unknown metadata. `definition_id` and `definition_revision`
are accepted aliases; structured definitions may carry unit/revision. Matching
units alone are insufficient: all three identity fields must be known and match
to assert `compatibility: "compatible"`. Legacy or partly unknown rows use
**`compatibility: "unknown"`**. Missing metrics and measurements remain null.
Labels are presentation, not metric identities. Compatibility of declared metric
meanings does not establish equal engine/model/resource revisions, horizons,
sample sizes, environmental conditions or experimental validity.

## Fixed-cut Python comparison

The implemented Python entry point takes **explicit immutable reader snapshots**,
not arbitrary caller-trusted byte offsets or newly fetched latest summaries:

```python
from aeroagentsim.runs import open_run
from aeroagentsim.services.comparison import compare_runs_at_cuts

with open_run(path_a) as run_a, open_run(path_b) as run_b:
    cut_a = run_a.snapshot()
    cut_b = run_b.snapshot()
    result = compare_runs_at_cuts({"run-a": cut_a, "run-b": cut_b})
```

Keys must match recorded service run ids when known. One to twenty snapshots are
accepted (a singleton also supports experiment-cell projection). Reader contexts
must stay open during the call. Header-only cuts are rejected. See the
[reader contract](run-reader.md) for ACK admission, format and integrity checks.

`basis: "fixed_cuts"` means KPIs, exact configuration differences and
`recorded_ends` are derived only from those cuts and their embedded header
registry/configuration. Later appends or ACK advancement do not extend the
selected snapshots, even between projecting different rows. Captures are per-run,
not a globally simultaneous snapshot. Results are detached ordinary Python data.
No engines, providers, current template registry, latest service summaries or
SQLite projection cache are used, and no source artifacts are written.

KPI projection is explicitly labelled `current_code_recomputation`. The current
reducer does not supply historical metric definition/revision pins; these remain
unknown rather than being invented from its contract name. Recorded engine
versions/digests are retained, but they do not enumerate all model/resource/code
revisions. Template knob aliases are not reconstructed (`knobs` is empty); inspect
`configuration` and `config_diff` for the exact recorded declarations.
Snapshot evidence does not contain host status: `status` stays `null`, including
in `recorded_ends`. An ACK or a `run_stop` record is not inferred to mean success,
terminality or `results_final`.

**HTTP integration is planned, not included here.** The existing routes in
`services/app.py` are unchanged. In particular, this Python API does not by itself
make the existing live `/v1/runs/compare` route fixed-cut; that route still gathers
summaries and recorded ends separately. Its existing response fields are retained,
with the additive supplied-row evidence described above.

## Non-guarantees

Neither API provides a common-horizon comparison, significance/confidence claims,
causal labels, deterministic live inference, authenticated provenance, full
crash-surviving model-call auditing or rollback of external effects. Recorded
links are evidence links, not causal attribution. Model replies, acknowledgements,
command acceptance, terminal status, projection finality and mission completion
remain distinct.
