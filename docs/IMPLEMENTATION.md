# aerokernel milestone 1 implementation

K2-M1F implements and repairs the **M1 subset** of [DESIGN.md](DESIGN.md).
DESIGN remains normative for full v0.1. Runtime dependencies are exclusively
Python's standard library; the package targets Python >=3.10. It contains no
ontology taxonomy, vehicle assumptions, upstream imports, simulated success,
missing-value defaults, implicit interpolation, or measurement fallback.

## DESIGN section status

| Section | M1 status | Delivered behavior / remaining scope |
| --- | --- | --- |
| §1 Boundary/authority | Done | Independent kernel; declared registry/bindings; private atomic candidate publication. |
| §2 Time/coordination | M1 done; broader profiles deferred | Integer Instants/Cuts, rational mappings, DES/fixed-step barriers, native holds/input cuts, lifecycle/cohort priority, lag/latch, coverage and microstep bounds. Lockstep, real time, nonzero grid origins and numerical splitting remain unavailable. |
| §3 Identity/state | Done | Cross-type generation allocation, controllers/single writers, explicit active fields, complete bitemporal versions/retractions, prefix-resolved lifecycle and cleanup readiness. |
| §4 Atomic commits/relations | Atomic commits done; relations M2 | Whole-wave validation, including textual-ID lifecycle preflight across batches; WAL before visibility. Relations/cardinality/obligations remain M2. |
| §5 Messages/actions | Done | Typed routing, per-recipient lag/grid/dispatch, scoped causes, intents/returns, offline requests, live reservations/idempotency, receipts/feedback/cancel decisions and cleanup. |
| §6 Engines/RPC | In-process done; RPC M2 | Serial calls, recorded grants/frontiers/read cuts, reset once, close. External transport/timeout/taint protocols remain M2. |
| §7 Registry | Core done; normalization external | Immutable descriptors/DAG/digest and portable schemas; no AeroGraph compiler or domain content. |
| §8 Notifications | Ordinary notifications done; sampling M2 | Field/value-only/lifecycle/action dirty, actual validity start/expiry transitions, partition-owned one-shot timers. SampleFrame/cones/entered remain M2. |
| §9 Journal/replay/RNG | Done for M1 | Versioned file/stream WAL, complete atomic effects, engine-free prefix replay, pinned budgets and scoped seeded streams. No crash-resume or external rollback promise. |
| §10 Invariants/errors | M1 invariants covered | Stable errors, independent temporal/action/work/determinism checks. I6/I11/I13 require the deferred relation/RPC/sample implementations. |
| §11 API | Done for M1 | Kernel API plus production `aerokernel.sdk`; proposals confer no authority or publication access. |
| §12 Worked trace | M1 adaptation | Native mover + DES orders + ordinary zone reactor; the original 12-transaction/receipt trace remains exact. No fulfills relation or sampled entered claim. |
| §13 Full v0.1 | Incomplete | The explicit M1 subset does not override the remaining normative requirements. |

## Engine SDK

`aerokernel.sdk.SimpleEngine` owns reusable invocation/frontier bookkeeping.
`ContextEngine` supplies `bootstrap(ctx)`, `step(ctx)` and `on_inputs(ctx)` hooks.
`@handles(schema, decode)` registers typed command handlers only for schemas
already declared on the partition. The default input handler rejects unsupported
event/cancel inbox items; engines handling mixed inputs explicitly dispatch their
commands and process their events/cancels. `testing.py` no longer exports the
production facade; authored toy models remain in examples/tests.

`EngineContext` offers field get/set/retract, lifecycle create/remove, command
accept/reject/execute/feedback/succeed/fail, actual cancel decision/cleanup,
event emission, child command submission, explicit wakeups, and scoped RNG.
It retains actual input dispatch/dirty causes and successful field-read version
causes. Within a batch, local receipt heads use proposal positions, including
intervening nonreceipt operations; feedback preserves the head. Later receipts
use the retained actual dispatch and current permitted action head. A child
command's ID is learned from its routed submitted notification, not invented.

Fact writes require the actual acquisition stamp/validity or an **explicit
per-field application policy**. The toy deliberately chooses computation-time
canonical acquisition and open validity for its local authored states. External
observations must supply their real source coordinates. `ABSENT` remains distinct
from null and from every concrete value. Owned-key reads are implicit only for
actually resolved keys; other reads require declared dependencies, including
lag-validity restrictions. The context cannot select a producer, grant, cut,
availability, ID, frontier or hidden lifecycle/route permission.

[examples/README.md](../examples/README.md) documents the executable SDK example.

## Findings → fix/test map

The review counterexamples were first installed as failing kernel tests, before
production fixes. The twelve nonperformance/nonorganization findings produced
13 failing cases (F3 has three resource axes); F11's dense-DAG test failed, and
F12's SDK-import regression initially failed. The five named surviving behaviors
are covered directly by these original-before failures, rather than inferred
from statement coverage. Evidence is in `benchmarks/evidence/regressions-before.txt`.

| Finding | Fix | Regression / additional adversarial coverage |
| --- | --- | --- |
| F1 | Semantic cause-kind and source/recipient checks; publication/reservation/enqueue cannot substitute for dispatch; historical lifecycle/action causes require declared reads. Submitted status cannot give an undelivered target command authority. | `test_m1_baseline::test_rejects_illegal_live_waves`; `test_m1_scopes` covers every kernel command-origin kind, own emitted command publication and other partitions' lifecycle/receipt/feedback/cancel-decision causes. |
| F2 | Dirty deeply detaches/freezes under the run budget; retained intent/timer values are replaced, never mutated. | `test_dirty_cannot_mutate_published_intent`; caught nested timer/lifecycle/receipt mutation plus historical records/intents/live-replay equality in `test_m1_scopes`. |
| F3 | Bootstrap discovers advertised header limits within active interpreter limits, then validates the entire header and later records under the pinned policy. | `test_live_headers_replay`: 9-MiB config, depth-140 config, 4101-digit root seed. |
| F4 | Host and partition cancel share recipient lag then native latch. | `test_host_cancel_respects_route_lag`; four independent DES/fixed-step × host/partition cases separately check publication, eligibility and actual dispatch. |
| F5 | Preflight every Create/Remove textual ID across the merged wave before applying operations. | Original remove/create reproducer; eight order/type/cohort-batch combinations; existing later-transaction cross-type reuse stays legal. |
| F6 | Pinned microstep bound enforced in shared grants and every replay record. | Consistently changed intent/return/seal coordinates in `test_replay_rejects_consistent_corruption`; existing live storm test. |
| F7 | Validate typed partition sequence and uniqueness before indexing; apply identical binding/cycle constraints offline. | Duplicate descriptor reproducer; existing malformed header, capability and cycle tests. |
| F8 | Explicit validity/partition timer kinds and disjoint tuple namespaces; contextual `TIMER_RECORD` for malformed active records. | Legal partition named `kernel`; missing record, malformed due/kind/payload; indexed deadline cancellation/fork isolation. |
| F9 | Validate identity/active key at the issued read cut first; a derived lag prefix predating this known generation yields lifecycle absence/empty history. Explicit unknown/foreign identities still fail. | Dynamic creation reproducer and independent field/history/explicit-precreation-cut tests in `test_m1_scopes`. |
| F10 | Read-only per-engine RNG mapping contains only its declared partitions; SDK stream access uses the invoking partition. | Foreign-stream reproducer; literal seed/digest/output vectors, independent stream fixture and cross-process hash seeds. |
| F11 | Shared append-only fact columns/prefix lengths, indexed lookups, COW overlays, pending indexes/heaps, one SCC compilation, compact shared fact rows and file WAL without duplicate full journal buffers. | `kernel_bench.py`, opt-in perf checks, storage branch/prefix/interval oracle, canonical encoding equivalence, existing atomicity/replay tests. |
| F12 | Production SDK with public docstrings, explicit time policies, decorators and proposal-only helpers; example rewritten. | `test_sdk`: all outcomes, interleaved heads, actual cancellation/cleanup, child IDs, policies, unsupported handlers, dispatch/RNG/route/writer authority. |
| F13 | Effective-value lookup on the actual open left side of the validity boundary, including prior microsteps. | Original reactive expiry reproducer; value-only future-valid start and expiry in `test_m1_storage`; independent interval oracle. |

The review's five named mutants correspond to F4, F5, F2, F1 and F3 respectively;
each fails its regression on the original implementation. The tests assert
modeled timing, forbidden commits, immutable state, authorized dispatch, or actual
successful replay; changing absence to a placeholder would not satisfy them.

## Invariant → test map

| Invariant | Evidence |
| --- | --- |
| I1 integer time/mapping/cuts | `test_foundations` independent tuple/Fraction checks; issued equal-Instant cuts and native/lag restrictions; header-resource axes and consistent microstep corruption. |
| I2 identity/lifecycle | `test_lifecycle` generation properties/cleanup/stale identities; both create/remove orders, concrete types and merged cohort batches; dynamic lag/history absence. |
| I3 selected single writer | `test_registry` rules/exact bindings/priorities/ties, uncovered domains; `test_m1_selectors` independently checks 15,552 rule-order/priority/type/pattern/exact-generation activation cases; nonowner SDK writes and historical cause scopes. |
| I4 immutable whole wave | Invalid last operation and pre-call WAL failure; `test_m1_strength` transaction flush loss **after one real stateful call**, with no live publication/retry and authoritative incomplete recovery; nested dirty mutation and divergent COW prefixes. |
| I5 bitemporal/prefix state | `test_m1_temporal_oracle`: six authored interval lists at every complete prefix, null/retractions/overlap/microsteps/acquisition/death/native cuts/lag; expected values use integer tuple comparisons, not Store.field or replay. `test_m1_storage` separately checks indexed intervals and reactive future start/expiry. |
| I7 native causality | Existing native-start/hold/frontier/input-coverage tests and lagged reader oracle; DES/fixed-step cancel symmetry. |
| I8 bounded settlement | Live storm/deadlock/reentry/cycle tests; replay bound checks; timer heap ordering and cohort priority. |
| I9 routing/scoped causes | Every premature command-origin spelling; restricted lifecycle/action causes; actual publication/eligibility/dispatch timings; unequal-lag fanout and repeated deferred queue work; consistent dispatch behind frontier/seal corruption. |
| I10 receipts/actions | `test_m1_action_table`: literal independent 8×8 transition table (64 pairs), feedback/stale receipt and cancel heads, multiple actual cancels, missing receipts and terminal exclusion; SDK outcomes/cleanup. |
| I12 every prefix/offline | `test_m1_strength` authored work/status model plus captured live publication snapshots at **every prefix**. The old `else True` assertion was replaced with every-prefix queue snapshots. Replay spies now prohibit live clocks as well as engine/RNG execution; resource/header/corruption/WAL checks remain strict. |
| I14 deterministic serial execution | `test_m1_determinism`: literal canonical seed bytes/SHA/derived seeds/stream outputs, PYTHONHASHSEED 0/1/71 subprocesses, all 24 engine registrations and cohort permutations; unchanged exact semantic toy trace. |

The independent oracle identifies removal by the authored Remove operation's
actual transaction coordinate; a coincident validity timer can consume an earlier
microstep. Its expected lifecycle/value rules are independent. No original
semantic test was weakened. Wire-inspection helpers expand lossless compact rows
before intentionally editing closed diagnostic records; low-level test facade
imports were moved from testing to sdk.

## Allocation/indexes and performance

Each fact key holds shared append-only columns with immutable prefix lengths.
Only abandoned/divergent candidate suffixes require a prefix copy. Real values,
acquisition/mapped/available/valid coordinates, producer and item coordinates are
retained for every version. Public frozen Fact/Retraction objects are materialized
on reads. Knowledge-prefix and monotone open-validity selection use binary
search; general overlapping intervals compile an affected-interval sweep index
once per requested prefix, then query it in logarithmic time. Four such indexes
are cached; index compilation is O(H log H), and cache eviction never discards
history. Lag cuts and historical action snapshots also use binary indexes.

COW indexes flatten after 16 changed layers, bounding lookup without copying the
whole store for every candidate. Pending invocation indexes avoid history scans.
Recipient work and active timer deadlines use heaps; fired/canceled timer records
remain retained separately. SCC components and the potential dependency graph
are compiled once with iterative traversal rather than enumerating DAG paths.
Action snapshots are created only when canonical action state changes.

Fact proposals, returned operations and generated versions share one compact
row by item reference, with per-record identity/field/stamp/interval tables.
Internal validated scalar/schema bounds are cached with type-sensitive keys.
The WAL writes/flushes one record at a time; file sinks do not retain a second
journal-byte list, and the live log retains one compact encoded record, expanded
lazily for diagnostics/causes. Fast encoding skips only facts/identity metadata
already validated under the same pinned policy; dynamic metadata, nested wrapper
depth and complete encoded frame size remain checked. Canonical-byte equivalence
is tested on scalar and nested Unicode/null payloads. No samples are skipped,
versions pruned, budgets silently reduced or field values defaulted.

Measured with [benchmarks/kernel_bench.py](../benchmarks/kernel_bench.py), Python
3.11.12, Linux process peak RSS. Two real engine instances: fixed-step writer and
idle DES. All N×3 fields are written every 100 ms, including 600 steps and bootstrap;
the full retained version count, final values and 60-s seal are checked. The final
JSON and journal SHA-256 are saved in [benchmarks/results.json](../benchmarks/results.json).

| Workload | Review before | K2-M1F after | Target |
| --- | --- | --- | --- |
| 100 × 3, 60 s | 187.56 s; 1948.14 MiB; 281,186,711 B; 180,300 versions; complete | **7.63 s; 62.93 MiB; 16,715,052 B; 180,300 versions; complete** | ≤10 s; ≤300 MiB |
| 1000 × 3, 60 s | **Failed at 5.9 s simulated**: 181.74 s; 1991.20 MiB; 278,472,984 B; 180,000 versions; MemoryError | **84.10 s; 384.94 MiB; 147,345,953 B; 1,803,000 versions; complete** | ≤90 s; ≤1536 MiB |
| Dense dependency bind | 20 partitions/190 edges: 0.7047 s; 40-partition completion not measured | **40 partitions/780 edges: 0.0248 s** | <0.5 s |
| Amortized journal B/fact version | 100: 1559.61; 1000 partial: 1547.07 | **100: 92.71; 1000: 81.72** | Aim <120 |

Before numbers are the independently recorded review evidence: overlapping host
runs, default in-memory Journal, 2-GiB virtual-address limit. After runs are separate
file-WAL subprocesses with no address cap. Thus these are capacity/outcome evidence,
not an isolated identical-sink speedup estimate. No completed 1000-entity baseline
is inferred from its partial run. Final after time includes bind/start/run and final
history/value verification. The benchmark's `--check-targets` passed both full runs.

```sh
.venv/bin/python benchmarks/kernel_bench.py --entities 100 --steps 600 \
  --journal benchmarks/run-100.jsonl --check-targets
.venv/bin/python benchmarks/kernel_bench.py --entities 1000 --steps 600 \
  --journal benchmarks/run-1000.jsonl --check-targets
.venv/bin/python benchmarks/kernel_bench.py --bind-only --check-targets
.venv/bin/python -m pytest -c pyproject.toml -m perf
```

Journal paths must be new. Perf smoke tests have generous bounds, verify real
file/history completion, and are excluded by default. The kernel conftest also
skips them under the repository's pre-existing pytest.ini unless `-m perf` is
explicitly selected; pytest.ini belongs outside this task's modification scope.

## Review deviation decisions

1. **Retain/narrow:** M1 is an explicit subset, not an override of v0.1 §13.
   Unavailable active profiles reject binding. Ordinary zone.transition is distinct
   from sampled entered; relation/fulfills is not fabricated.
2. **Retain:** finite float `number` and arbitrary int `integer` remain distinct;
   records require members/required/extra, unions use `$case`, dimensions are explicit,
   unsupported/recursive active constraints reject compilation without coercion.
3. **Retain:** explicit rules/exact bindings select active capability fields without
   adding inheritance. Only actually resolved owned keys have implicit field reads.
4. **Narrow:** origin-zero, nonsplittable fixed-step is this M1 profile. Nonzero origins
   and exact splitting remain spec gaps. Holds/native-start cuts and explicit input
   hold policies do not excuse dropping route lag.
5. **Retain:** multi-partition reset is one serial engine call with separate recorded
   intents/batch authority. Evolving views are not shared between partitions.
6. **Retain/narrow:** original **actual target dispatch** may support later receipts;
   current heads and local prior operations still apply. Publication/enqueue or
   another recipient's dispatch cannot replace actual delivery.
7. **Retain/fix:** pinned host identity, original-content idempotency, separate source
   kinds and closed `$type` codec remain. Immutable decoding, header budget adoption
   and typed/unique replay declarations now enforce that contract. New journals pin
   major 1/minor 1 for compact facts, explicit timer kinds and cause/budget fields;
   this development revision explicitly rejects minor 0 instead of claiming an
   incomplete legacy decoder. Historical 1.0 migration is not delivered here.
8. **Retain semantics/revert allocation choice:** complete history remains required;
   copied whole candidates/duplicated journal retention are replaced with the
   indexed/shared structures above. Retention bounds/checkpoint/resume remain deferred.

## Spec change requests

No normative DESIGN edit was required or made. F9 uses the review's recommended
clarification: explicit unknown identity cuts fail, while a declared lag prefix
before creation of a generation already known at the invocation cut yields
lifecycle absence. This rule should be stated explicitly in a future spec edit;
it does not convert unknown values to defaults. The new lossless wire minor is an
implementation format revision, with an explicit unsupported-legacy-version boundary.

## GLM provenance

Concurrent WorkBuddy DSH sessions used workspace-local profiles, the requested
`workbuddy/glm-5.3-flash`, maxTokens=131072 and no effort parameter. Every session
received absolute DESIGN/review/project paths, disjoint file ownership and the
instruction to preserve other writers' edits. Actual session identities were
checked in DSH session storage:

| Owned subtask | Actual session | Delivered result |
| --- | --- | --- |
| regression fixtures | a392a400-ef4e-4387-86b4-dd48a9658919 | Unfinished analysis; interrupted; root completed the tests. |
| SDK facade | ebbf0e80-a08a-4588-8131-704a32f148a4 | Unfinished analysis; interrupted; root implemented/reviewed SDK. |
| adversarial checks | b310dbb9-8b1a-45af-8de5-587112be94ef | Unfinished analysis; interrupted; root completed checks. |
| independent action table | 5c6a31d7-b053-4412-a93b-13640f2d8e31 | Model error, no table delivered; root authored/tested 64-pair table. |
| literal RNG/hash/permutation fixtures | 23718cdf-56b1-4bd2-a601-34b973ff4af7 | Staged 15 tests in its writable cwd; root reviewed and integrated the tests, including all-registration permutations. |

The first three sessions ran concurrently, and the latter two ran concurrently.
DSH mounted the repository read-only within its child sandbox, so the successful
writer staged its file in owned scratch rather than modifying production. Its
output is retained in `benchmarks/evidence/glm-vectors.txt`. Failed sessions are
not counted as delivered implementation or review work. Root integration is
responsible for the verified final result.

## Validation

Use the existing editable installation and pinned interpreter. This explicit config
selects both kernel and existing audit tests without modifying pytest.ini or audit code:

```sh
PYTHONDONTWRITEBYTECODE=1 COVERAGE_FILE="$PWD/tests/.coverage" \
  TMPDIR="$PWD/benchmarks/evidence/tmp" \
  HYPOTHESIS_STORAGE_DIRECTORY="$PWD/tests/.hypothesis" \
  .venv/bin/python -m pytest -c pyproject.toml --basetemp=tests/.pytest_all \
  --cov=aerokernel --cov-report=term-missing --cov-fail-under=90
.venv/bin/python -m ruff check --cache-dir tests/.ruff_cache
.venv/bin/python -m ruff format --check --cache-dir tests/.ruff_cache
.venv/bin/python -m mypy --strict --cache-dir tests/.mypy_cache aerokernel
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python examples/two_engine_toy.py
```

Final validation: **415 tests passed** in the full suite (117.63 s), with two
opt-in perf tests deselected. The subsequently added independent selector test
also passed all 15,552 authored cases: **416 distinct tests passed** overall.
Coverage including that focused run is **95.23%** (3251/3414 statements).
The explicit `-m perf` run passed both smoke tests. Ruff lint and formatting
passed all 46 selected Python files; strict mypy passed all 24 package modules.
The installed editable package ran directly with
`.venv/bin/python examples/two_engine_toy.py`, printing the exact 12 transactions.
All 24 modules parse with Python 3.10 grammar. Evidence is retained under
`benchmarks/evidence/`.

External simulator determinism, full AeroGraph semantics and Python 3.10 runtime
execution are not claimed; the available execution interpreter is Python 3.11.12.
