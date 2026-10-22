# Kernel runtime-data growth: measurement first

**No performance budget has been set. No budget will be set until measurements
are recorded and reviewed.** This change supplies measurement tooling and structural
tests, not benchmark results, latency claims, allocation limits, or speedup targets.
The benchmark and tests were not executed as part of this generate-only change.

## Run the benchmark

Requires Python 3.10+ and only the standard library. From the repository root,
select the checked-out kernel explicitly; an editable installation is unnecessary:

```sh
PYTHONPATH=aerokernel PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 \
  python aerokernel/benchmarks/runtime_data_bench.py \
  --commands 4 --history 64 --appends 16 \
  --partitions 16 --subscribers 1 --steps 100 --replay
```

The script emits one JSON document on stdout. Redirect it to a **new** result file
outside the source tree to record a measurement. These are example workload sizes,
not acceptance thresholds. Long-history seeding itself can be expensive; start
small and increase one dimension at a time:

```sh
# Isolate history depth; repeat with --history 0, 100, 1000, ...
PYTHONPATH=aerokernel python aerokernel/benchmarks/runtime_data_bench.py \
  --workload actions --commands 1 --history 1000 --appends 10

# Hold the interested-recipient count fixed while increasing total partitions.
PYTHONPATH=aerokernel python aerokernel/benchmarks/runtime_data_bench.py \
  --workload routing --partitions 128 --subscribers 1 --steps 1000 --replay
```

Use `--help` for all options. `--commands` controls the number of retained command
histories; `--history` is existing feedback entries **per command**, excluding the
three setup receipts (submitted, accepted, executing). `--appends` adds that many
rounds of one feedback per command. `--partitions` includes the writer;
`--subscribers` selects interested readers from the remaining partitions (zero is
allowed). `--steps` controls fixed-step runtime length, at 1,000,000 simulated ns
per step. `--provenance lean|full` selects kernel provenance, defaulting to lean.
`--revision` is an optional caller-supplied checkout label, not independently
verified provenance. Do not use Python `-O`; semantic checks must stay enabled.

Repeat each size in a fresh process and retain every sample, with its parameters
and provenance. Compare matching source digests, Python/platform, provenance mode,
subscriber count and replay setting. Record host load and available memory
separately; CPU count alone does not describe capacity or container CPU quotas.
Do not extrapolate completion from a failed or interrupted run. Ordinary workload
exceptions produce a nonzero exit status and a `failure` object, with
`outcome: null`; incomplete phases have `completed: false`. Abrupt process
termination may produce no JSON. CLI errors go to stderr.

## Workloads and measurement boundaries

| Workload / phase | Actual work |
| --- | --- |
| Actions: `seed_histories` | Construct commands through pending, submission, a registered synthetic dispatch, accepted and executing receipts; append the requested existing feedback using real `Actions` methods. |
| Actions: `read_initial_views` | Retain immutable public action/history views and their original action store. |
| Actions: `append_feedback` | Fork once per round and append feedback to each command. No fixture reads are added inside the append loop. |
| Actions: `verify_action_views` | Check retained views, prefix equality, history lengths, payload sequence and unchanged receipt heads; hash history entries in order. Feedback leaves actions executing, not completed. |
| Routing: `bind_and_start` | Bind a real kernel, one fixed-step field writer and declared readers; bootstrap one entity and one fact version. |
| Routing: `run_steps` | Publish one changed field per step, route dirty work, dispatch readers and retain the in-memory journal through real coordinator waves. Idle partitions remain present. |
| Routing: `verify_routing` | Check the sealed frontier, fact count/value, unchanged initial view and actual field-dirty deliveries to each reader; obtain and hash the journal. |
| Routing: `replay_and_compare` (opt-in) | Offline replay, checking the sealed frontier, final value, record count and every regenerated record against execution. |

Action probes use internal `Actions` directly: they isolate history costs but do
not measure WAL, scheduler or `StateView` overhead. Dispatch envelopes and item
references are synthetic fixtures registered through `Actions.record_dispatch`,
not coordinator-issued evidence. They do not generate a journal, so their
`replay_checked` is `null`, not a successful replay claim. Here `--provenance`
changes action authorization checks only, not transactional/WAL storage. Use the
routing workload for full-versus-lean provenance memory comparisons: it includes
candidate copying, scheduling, encoding and an **in-memory** WAL, not just routing
latency or file/fsync work.
The coordinator advances every partition at each physical boundary, including
unsubscribed readers, so partition sweeps also include their invocation/WAL costs.
Reader callbacks count actual field-dirty deliveries; the outcome reports these
counts and how many readers received them. Its replay check is `null` when not
requested. Neither probe invokes providers or uses
random workload inputs. Fixed parameters and source/interpreter yield deterministic
work and semantic output; wall time, memory and hardware fields are not expected
to be identical. Journal hashes include interpreter provenance in the header.

Each phase reports:

- `operation_counts`: scoped wrappers around actual calls, without storing call
  arguments or results. These are not estimates from input sizes.
- `wall_s`: `perf_counter` elapsed time **with instrumentation and tracemalloc**;
  not uninstrumented kernel throughput. Wrapper installation/removal is excluded.
- `tracemalloc_start_bytes`, `tracemalloc_end_bytes`, `tracemalloc_peak_bytes`:
  Python-traced bytes. Tracing starts afresh per workload and the peak resets per
  phase, **not** the live allocation baseline. Thus append/run peaks include
  retained setup objects and views. Subtracting start from peak describes extra
  traced headroom, not total allocations. These are not RSS or native allocation
  counts; imports and allocations predating tracing are excluded. Verification,
  journal materialization and optional replay have separate phase metrics.

Tracing is process-wide: phase baselines also retain instrumentation objects and
previous phase result dictionaries. Replay runs with the live kernel, initial view
and materialized journal still retained; it is not standalone replay memory.
Kernel construction, between-phase setup and final cleanup are outside phase wall
timing. Do not sum phase peaks or treat summed phase times as total process cost.

The `hardware_provenance` block records Python version/implementation/build,
executable, platform, machine, processor (when available), `os.cpu_count()`, hash
seed setting, kernel version and actual imported package path. It also hashes the
benchmark file and sorted kernel Python source paths/content. Missing metadata is
JSON `null`, never fabricated zero. Source hashes identify content; they are not
an authenticity guarantee or proof of reproducible results. The top-level
`budget` remains `null`.

### Counter definitions and limits

| Counter | Meaning |
| --- | --- |
| `actions_{pending,submit,receipt,feedback,clone,action}_calls` | Invocations of each named `Actions` method; `actions_action_calls` includes reads made during feedback and authorization. |
| `action_history_freeze_calls` | Calls to `messages.freeze` while `Actions.action` is active: in the current implementation, one per materialized history entry. |
| `messages_freeze_calls`, `messages_normalize_calls` | All calls through those module-local bindings; excludes recursive value-tree calls and other modules' bindings. |
| `action_state_replace_calls` | Calls through `messages.replace`; counts replacement requests, not bytes or all Python allocations. |
| `overlay_fork_calls`, `store_clone_calls` | Calls to these primitives; an overlay fork may flatten an index, so a constant call count does **not** imply constant work. |
| `notify_field_calls`, `dirty_matches_calls` | Invocations of field notification and dependency matching, respectively. |
| `routing_partition_checks` | `dirty_matches` calls inside `notify_field`, excluding unrelated cause-validation checks. |
| `field_dirty_items` | Dirty items actually added by `notify_field`; excludes lifecycle and receipt notifications. |
| `candidate_add_calls` | All calls to `Candidate.add`, including other transaction item kinds. |

Counters count method entry (including a call that later fails). They do not count
all tuple copies, sort comparisons, dependency predicates, recursive freezing,
allocator events or overlay-flattened entries. Timing and memory capture costs
outside those counters but cannot attribute them individually. Wrapper scopes are
serial and restored after each phase; this is not a concurrent profiling API.
The routing outcome also records retained record, fact-version and coordinator
trace lengths, without changing coordinator behavior.

## Structural tests and optimization candidates

`aerokernel/tests/kernel/test_action_growth.py` checks operations rather than wall
time. Existing bounded sub-operations have ordinary assertions: submission does
not traverse unrelated histories, feedback calls the `messages.normalize` binding
once for its new payload and requests at most three action-state replacements,
and wholly unconsumed fields skip dependency matching. These counts follow the
current call paths; they do not bound total allocations or the work performed
inside a replacement.
Desired history-independent lookup/feedback materialization and
irrelevant-partition-independent routing checks are marked `xfail(strict=False)`
with the specific current reason; these are open structural goals, not numeric
performance budgets. Immutability, ownership, rollback/snapshot isolation and
replay checks remain ordinary tests, not expected failures.

For later validation in an environment with the project's test dependencies,
run from the repository root (not executed for this change):

```sh
PYTHONPATH=aerokernel python -m pytest aerokernel/tests/kernel/test_action_growth.py -rxX
```

Static source inspection confirms the R2 §F11 / R2-J concerns in these paths:

- [`Actions.action`, `_authorized`, `feedback`](../../aerokernel/aerokernel/messages.py):
  feedback obtains the action twice; each read thaws/freezes every old history
  entry, then the update concatenates a tuple. A possible follow-up is separating
  trusted internal access from public immutable views, or immutable persistent
  history storage. Neither is implemented here: returned operation dictionaries
  are subsequently enriched by transactions, so merely sharing them or dropping
  the freeze is unsafe without a complete ownership audit.
- [`Candidate.notify_field`, `dirty_matches`](../../aerokernel/aerokernel/transactions.py):
  interested fields trigger sorted scans of all partitions. A field-to-recipient
  index is a candidate only; preserve sorted recipient/dependency order, type and
  ID matching, lag, value-only filtering and validity-boundary behavior.
- [`Kernel._wave`, `_check_microstep`](../../aerokernel/aerokernel/coordinator.py):
  trace entries accumulate per wave for microstep diagnostics. Trace policy is
  outside this write set; the long-run outcome exposes its size only.

No kernel optimization is included. Before setting budgets or changing storage,
record a size sweep with these counters and memory/time fields, identify the
limiting path, and review both provenance modes and the existing action, storage,
rollback and replay suites. Source-derived growth risks are not measured latency
regressions. No measurement results are recorded in this document yet.
