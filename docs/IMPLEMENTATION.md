# aerokernel v0.1 implementation (K2-M2)

K2-M2 completes the remaining v0.1 contracts in [DESIGN.md](DESIGN.md), on top
of K2-M1F. DESIGN remains normative and was not edited. Runtime dependencies
are exclusively Python's standard library; the package targets Python >=3.10.
The kernel contains no ontology taxonomy, vehicle assumptions, upstream imports,
simulated success, missing-value defaults, interpolation or measurement fallback.

## DESIGN section status

| Section | Status | Delivered behavior |
| --- | --- | --- |
| §1 Boundary/authority | Done | Independent general kernel, normalized registry, bindings and private atomic publication. |
| §2 Time/coordination | Done | DES, fixed-step, lockstep and declared real-time ingress; exact splitting, certified native holds, nonzero grid origins, pinned interval-start cuts, post-boundary latching, watermarks, timeout faults and optional pacing. |
| §3 Identity/state | Done | Generations, scoped lifecycle/writers, bitemporal facts/retractions, historical lifecycle and authorized cleanup. |
| §4 Atomic commits/relations | Done | Whole merged waves; directed edge versions, source-scoped authority, directional interval sweeps, activated minimum obligations, cohort swaps and explicit incident cleanup. |
| §5 Messages/actions | Done | Typed routing, recipient lag/latch/dispatch, scoped causes, offline/live reservations, original-stamp lateness records, idempotency, receipts, feedback and actual cancel cleanup. |
| §6 Engines/RPC | Done | Serial intent/return execution plus versioned strict JSON-lines transport, declared projections, pinned digests/deadlines, acknowledgment checks, taint and bounded cleanup. |
| §7 Registry | Done | Immutable normalized descriptors, inheritance, directional relation contracts, digest and portable schemas; source normalization stays external. |
| §8 Notifications/sampling | Done | Dirty/value-only/lifecycle/action notifications and deadlines; declared sample triggers, settled cones, frame levels, one frame/time, and optional sampled entered profile. |
| §9 Journal/replay/RNG | Done | Journal 1.2 with additive controls and metadata, actual 1.1 replay, complete atomic effects, budgets and scoped deterministic RNG. |
| §10 Invariants/errors | Done | I1–I14 evidence below, including independent relation oracle, native boundary cases, RPC faults and sampled resolution. |
| §11 API | Done | Existing exports, SDK and testing APIs preserved; additions listed below. |
| §12 Worked trace | Done | Original M1 compatibility trace remains 12 transactions; an additional record/collection example exercises relations, minimum obligations and sampled entered events. |
| §13 v0.1 scope | Done | All requested timing, relation, sampled, RPC and ordinary SCC contracts implemented. v0.2 crash-resume, distributed execution and rollback remain outside this scope. |

## M2 contracts and API additions

`Timing` appends `exact_stop` and `certified_hold`. A lockstep adapter declares an
exact-stop contract, or a positive communication `step_ns`/`origin_ns` with certified
holds. `latch=False` requires exact stops for lockstep and enables fixed-step
splitting. Logical holds acknowledge the grant without integration or output;
physical integration reads the pinned native interval-start cut. Inputs arriving
at 3 ms are applied after completing the 20-ms interval for a nonsplittable
20-ms adapter. An exact-stop adapter integrates to 3 ms first and applies them
there. An unexpected early native return faults the wave. Explicit buffering must
actually finish the grant; retained events keep their native occurrence/stamp and
are published at the later completed boundary. No native rollback is asserted.

`aerokernel.testing.FakeLockstepSimulator` provides configurable stop granularity,
one-shot early return, authored output times and a log of actual advances/applied
inputs. [lockstep_adapter_skeleton.py](../examples/lockstep_adapter_skeleton.py)
shows the host adapter boundary. PX4/Gazebo and SUMO containers keep their
`aeroagentsim.px4/v1` and `aeroagentsim.sumo/v1` protocols. Platform host adapters
implement kernel engines and translate their actual simulator results; those
containers do not need to implement kernel RPC. The same contract applies to
ns-3 and other external state engines.

`Kernel(ingress_policy=IngressPolicy(initial_watermark_ns, lateness, timeout_s,
speed_ratio))`, `advance_watermark(ns)` and `submit_live(request, stamp)` add a
closed-prefix stream. The stamp uses an existing pinned rational clock mapping.
At/before the watermark or seal, `reject` records a rejection without reserving
an action/sequence; `delay` reserves the next legal publication boundary and
records the original request, stamp, mapped time and displacement. Normal recipient
lag/latching still records its separate eligibility and actual dispatch time.
Watermark waits use a condition and a finite deadline before physical calls.
Ingress during a wait changes the cut and forces boundary selection to be
recomputed. A speed ratio only paces wall-clock progress; it never changes order.
Offline replay uses the recorded watermarks/decisions without waits or pacing.

`MemoryRegistry(..., relations=...)` adds `RelationDescriptor` with explicit
`Cardinality(minimum, maximum)` for both directions. `RelationRule` binds the
writer by relation and source type/ID scope; `ObligationRule` binds each directional
minimum controller. `Partition` appends `relation_produces`, `relation_consumes`
(`RelationDependency`) and `obligation_produces`; manifests append `relation_rules`
and `obligation_rules`. `AssertEdge`, `CloseEdge`, `CancelEdge`,
`ActivateObligation`, `EndObligation` and `CancelObligation` produce immutable
versions. Replacement closes/cancels an old ID and asserts a new one atomically.
`StateView.relations`, `relation_history` and `obligation_history` retain knowledge
prefixes and source-specific lag. SDK contexts add `relate`, `unrelate`,
`relations` and `replace_relation`, retaining actual version causes.

The final merged graph is validated once per affected relation. The sweep removes
ends before adding starts at the same Instant, counts distinct counterpart
identities, checks overlapping pairs/scopes, maxima, activated minima, future gaps
and open tails. An unactivated minimum does not create a universal existence rule.
Removal never cascades: incident edges/obligations need their own authorized
closure/cancellation in the controller cohort or an earlier transaction.

`SampleSpec` pins context/partition, the complete upstream cone, roles, declared
instance sources, native clock/mapping IDs, trigger kinds, frame inputs and opaque
parameters. It is supplied through `BindingManifest(samples=...)`.
`SampleFrame`, `RecordedFrame` and `StateView.sample_frames` expose atomic recorded
results. Ordinary work settles before sampling; contexts run in topological levels,
with ordinary downstream settlement between levels. Later same-time input into
an already sampled cone faults. A run limit alone creates no frame. Undeclared
triggers and changed role/source/clock metadata are rejected explicitly.

`aerokernel.profiles.sampled` is optional. `Evaluation`, `EnteredResult`, `entered`,
`SampledEvaluator` and `EnteredEvaluator` leave predicate calculation in the
application's `evaluate(ctx)` hook. Every evaluation records known/unresolved/invalid
status, diagnostics and applicability. The latest strictly earlier physical frame
is selected; an unresolved latest frame cannot be skipped. Role identities include
run/epoch/ID/generation/type and must match sources and native mappings. A first
true emits nothing. Only known false→known true emits, recording the observed
`(previous_ns,current_ns]` interval and citing the actual frames/input versions.
[The sampled example](../examples/sampled_relations.py) exercises this API.

`aerokernel.rpc.RemoteEngine(stream, output=None, budget=..., timeouts=...)` and
`serve_engine(engine, stream, output=None, ...)` support sockets and binary stdio.
`RPCConnection`/framing helpers are in `rpc_transport`. Protocol 1.0 negotiates the
common minor/features and confirms the resource budget; every operation has a
finite deadline for the complete write/read or server callback. Frames reject
non-UTF-8, BOM, duplicate keys, nonfinite numbers, missing LF and oversize including
LF, preserving big integers. Only one request/reset is permitted. Transport and
metadata faults taint, publish no partial wave and authorize no retry; bounded
cleanup is permitted. A timed-out callback may have changed native state, so cleanup
shares its execution lock and cannot concurrently enter that engine.

Remote views contain declared field/edge/frame histories and permitted actions,
not the ontology or journal. The selected registry includes reachable schema/type
metadata; the full manifest digest pins the authority contract. The header records
negotiated profile, manifest/registry digest and per-operation timeout policy. Views
append public `invocation_ref`, `phase`, `logical_before`, `native_before` metadata;
requests echo an invocation/query token and cuts/frontiers. RPC faults record
operation/request/policy and a stable code; elapsed time is not semantic state.

## Specification clarifications and compatibility

* A watermark closes `<= watermark_ns`; equal stamps are late. A delayed command's
  original stamp and activation request remain in its reservation record. Publication
  displacement and ordinary recipient latch delay are distinct recorded quantities.
* Relation validity is half-open over the full Instant, including microsteps.
  Canceled heads have explicit `valid=None`; historical asserted intervals remain
  readable at their older cuts. `endpoint_pair` identity prevents reuse of a pair
  by another edge ID, including disjoint/canceled heads; `edge_id` permits disjoint
  IDs for the same pair. Unknown or omitted bounds are never interpreted as zero.
* Whole-relation validity queries obey all declared slice lags; each source's
  knowledge prefix is capped separately. Historical edge/obligation reads likewise
  obey their declared lag. Native physical calls use the original native input cut.
* Sample triggers are dirty-kind declarations (`field` also covers validity
  boundaries), message inputs, or explicitly scheduled timers/activations. Frames
  record source-role metadata in addition to the §8 sketch's fields. Opaque
  parameters and result/payload maps are never interpreted as record tags.
* The transport's invocation token is separate from the kernel authority token;
  proposals still pass ordinary kernel schema/authority/causality validation.
  Cleanup after a taint is resource cleanup and never an action-success receipt.

There are no removals or changed signatures in the existing `aerokernel.__init__`,
`aerokernel.sdk` or `aerokernel.testing` public APIs, and no deprecation shim is
needed. Journal 1.2 adds live ingress/rejection, watermark, sampled/relation effects
and RPC profile/fault metadata. The decoder recognizes **complete** historical
1.1 declaration shapes and supplies only the newly added declaration defaults;
arbitrarily missing fields still fail. The untouched 1.1 toy fixture retains SHA-256
`f814b5a4c0324fbc5e741da445d238972bb5dabc60dab7d29ccbdb51137a462c` and replays its
original state/actions. The additive 1.2 toy fixture is pinned to
`1b75a0cc904cd3833d7357a4eddb44ad1a18c34bcee4bf851fcb5e0b57f7413d`; all
registration/hash-seed permutations still match it. The former bind-time rejection
tests were replaced by declaration-validation and implemented-contract tests.

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
| I6 temporal relations | `test_m2_relations`: merged swaps/cohorts, future overlaps/gaps, both directional bounds/activated minima, closure/cancel histories, authority, incident removal, deadlines and independent Hypothesis brute oracle including canceled/open intervals; explicit microstep touches. |
| I7 native causality | Existing native-start/hold/frontier/input-coverage tests and lagged reader oracle; DES/fixed-step cancel symmetry; `test_m2_lockstep_adapter` proves hold, exact 3-ms stop, 20-ms latch, early fault and completed buffering; `test_m2_grid` checks origins/splits. |
| I8 bounded settlement | Live storm/deadlock/reentry/cycle tests; replay bound checks; timer heap ordering and cohort priority; `test_m2_scc` checks Jacobi convergence with immutable wave inputs, unchanged physical integration count, and bounded event storms. |
| I9 routing/scoped causes | Every premature command-origin spelling; restricted lifecycle/action causes; actual publication/eligibility/dispatch timings; unequal-lag fanout and repeated deferred queue work; consistent dispatch behind frontier/seal corruption. |
| I10 receipts/actions | `test_m1_action_table`: literal independent 8×8 transition table (64 pairs), feedback/stale receipt and cancel heads, multiple actual cancels, missing receipts and terminal exclusion; SDK outcomes/cleanup. |
| I11 RPC atomicity | `test_m2_rpc`: fragmented frames, duplicate/nonfinite/UTF-8/LF/size checks, lossless big ints, wrong IDs/versions/tokens, EOF/timeout/oversize after actual native work, taint/cleanup, real subprocess stdio, full remote/local toy equality, projections and immutable frame history. |
| I12 every prefix/offline | `test_m1_strength` authored work/status model plus captured live publication snapshots at **every prefix**. The old `else True` assertion was replaced with every-prefix queue snapshots. Replay spies now prohibit live clocks as well as engine/RNG execution; resource/header/corruption/WAL checks remain strict. |
| I13 sampled protocol | `test_m2_sampling`: whole-cone coalescing, topological levels, later-input fault and transitive feedback; `test_m2_entered`: first true, latest unresolved, applicability, identity/source/clock/history rejection, journaled SDK results and one causal crossing event. |
| I14 deterministic serial execution | `test_m1_determinism`: literal canonical seed bytes/SHA/derived seeds/stream outputs, PYTHONHASHSEED 0/1/71 subprocesses, all 24 engine registrations and cohort permutations; unchanged exact semantic toy trace. |

The independent oracle identifies removal by the authored Remove operation's
actual transaction coordinate; a coincident validity timer can consume an earlier
microstep. Its expected lifecycle/value rules are independent. No original
semantic test was weakened. Wire-inspection helpers expand lossless compact rows
before intentionally editing closed diagnostic records; low-level test facade
imports were moved from testing to sdk.

## M1 allocation/indexes and historical performance

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

## M2 measured performance

These are separate complete runs with the same 600-step workload and file-WAL
sink as the final M1 measurements. Times include bind, start, run and verification.
Both `--entities 100 --check-targets` and `--entities 1000 --check-targets` passed.

| Workload | M2 wall time | Peak RSS | Retained versions | Journal | Target |
| --- | --- | --- | --- | --- | --- |
| 100 × 3 fields, 60 s | **7.647 s** | **65.54 MiB** | 180,300 | 16,715,321 B; 92.71 B/version | ≤10 s; ≤300 MiB |
| 1000 × 3 fields, 60 s | **84.840 s** | **387.17 MiB** | 1,803,000 | 147,346,222 B; 81.72 B/version | ≤90 s; ≤1536 MiB |
| 40 partitions / 780 dependency edges | **0.0224–0.0285 s** | — | — | — | <0.5 s |

Exact measurements and journal hashes are retained in
[m2-100.json](../benchmarks/evidence/m2-100.json) and
[m2-1000.json](../benchmarks/evidence/m2-1000.json).
The initial M2 1000-entity run completed but missed the wall-time target at
94.252 s. Profiling identified repeated recursive normalization of generated
one-key fact references. The final implementation checks each reference's exact
shape, sign/type, integer budget and wrapper depth, normalizes all other payloads,
then restores only those validated references. Complete encoded frame-size
validation remains in place. A regression compares mixed fact/nonfact canonical
bytes, signed-zero normalization and depth/digit budget failures. Initial and final
runs have identical journal bytes and SHA-256 at each workload; no model steps,
checks, versions or history were dropped. Initial measurements are retained as
[m2-100-initial.json](../benchmarks/evidence/m2-100-initial.json) and
[m2-1000-initial.json](../benchmarks/evidence/m2-1000-initial.json).
The default benchmark WAL now uses a workspace-local temporary directory and
streams the final hash; `--journal` still permits an explicitly retained WAL.

## M1 review decisions, updated for M2

1. **Completed in M2:** the earlier M1 subset now includes all requested v0.1
   timing, relation, sampled and RPC profiles. Malformed declarations still reject
   binding. The original ordinary zone.transition is preserved; sampled entered
   and relations have their own executable example and declared application logic.
2. **Retain:** finite float `number` and arbitrary int `integer` remain distinct;
   records require members/required/extra, unions use `$case`, dimensions are explicit,
   unsupported/recursive active constraints reject compilation without coercion.
3. **Retain:** explicit rules/exact bindings select active capability fields without
   adding inheritance. Only actually resolved owned keys have implicit field reads.
4. **Completed in M2:** nonzero origins and declared exact splitting are implemented
   for fixed-step and lockstep, with certified holds for nonsplittable native steps.
   Native-start cuts and explicit input hold policies retain recipient route lag.
5. **Retain:** multi-partition reset is one serial engine call with separate recorded
   intents/batch authority. Evolving views are not shared between partitions.
6. **Retain/narrow:** original **actual target dispatch** may support later receipts;
   current heads and local prior operations still apply. Publication/enqueue or
   another recipient's dispatch cannot replace actual delivery.
7. **Retain/fix:** pinned host identity, original-content idempotency, separate source
   kinds and closed `$type` codec remain. Immutable decoding, header budget adoption
   and typed/unique replay declarations enforce that contract. New journals pin
   major 1/minor 2 for the additive M2 records/declarations, retaining complete
   1.1 decoding and replay. Minor 0 remains explicitly unsupported; historical
   1.0 migration is not delivered here.
8. **Retain semantics/revert allocation choice:** complete history remains required;
   copied whole candidates/duplicated journal retention are replaced with the
   indexed/shared structures above. Retention bounds/checkpoint/resume remain deferred.

## Spec change requests

No normative DESIGN edit was required or made. F9 uses the review's recommended
clarification: explicit unknown identity cuts fail, while a declared lag prefix
before creation of a generation already known at the invocation cut yields
lifecycle absence. This rule should be stated explicitly in a future spec edit;
it does not convert unknown values to defaults. Journal 1.2 is an additive format
revision with actual 1.1 replay support; only minor 0 remains unsupported. The M2
clarifications above document implemented choices without modifying DESIGN.

## M1 GLM provenance

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

## M2 GLM provenance

Actual concurrent DSH agents used `workbuddy/glm-5.3-flash`, `maxTokens: 131072`
and no effort option. DSH profiles, output, reasoning, sessions and owned scratch
are all inside `.m2-work/`; external paths were read-only. Each task received the
corresponding absolute project contracts, strict file ownership and instructions
not to revert other writers. Actual session identities and output were inspected:

| Owned task | Actual session | Result reviewed by root |
| --- | --- | --- |
| timing | 752047c6-c68b-4af9-a28b-048094d3c5b8 | No usable implementation; root implemented. |
| relations | fa05e54e-149d-4079-b2c2-4777c5b528ce | No usable implementation; root implemented. |
| sampled profile | c0092216-89c7-4ad1-93d9-8c1357a3e5b2 | No usable implementation; root implemented. |
| RPC | 9520013c-6090-4873-bbc4-bd41ec16170b | No usable implementation; root implemented. |
| narrowed ingress module | f0062445-e61e-445e-a42b-da0fc149d4d3 | No usable implementation; root implemented. |
| narrowed sweep module | a2cac91c-8196-4951-849c-e0f7ba686e7e | No usable implementation; root implemented. |
| narrowed entered module | 3ec6d2a8-7f09-460e-b372-0eff671ca861 | No usable implementation; root implemented. |
| narrowed transport module | bd047f4e-72ca-439e-a2b4-e848f1d5fcc6 | No usable implementation; root implemented. |
| relation second opinion | 8f41f21c-97f8-4063-84bc-4e573bcba366 | Delivered report/probes; independently rerun and adjudicated. |
| RPC second opinion | 8ae6e5fd-6cf4-4f9c-a6df-9a2b3bceb2be | Model error; no usable report. |

The first four, next four, and final two sessions were respectively concurrent.
Failed/incomplete sessions are not counted as delivered implementation or review.
The relation reviewer initially used an interpreter without Hypothesis and
misdeclared some bounds. Root reran the corrected thirteen directional-minimum
probes using the required workspace interpreter; all passed. Its endpoint-pair
claim conflicts with the explicitly declared identity policy; edge-ID identity
supports the requested same-pair replacement. Its alleged maximum escape was
not demonstrated by its saved script: closed intervals remain in effective heads,
old knowledge versions are not simultaneous edges, and reasserting an existing
edge ID is rejected. Root's property oracle and actual kernel regressions cover
these cases. The report and adjudication are retained in
[m2-glm-review.txt](../benchmarks/evidence/m2-glm-review.txt).

## M2 final validation

The final current-source run passed **397 kernel tests** (137.45 s), with the two
opt-in perf tests deselected. The unchanged existing audit suite passed **120
additional tests** (1.56 s); the explicit perf selection passed **2 tests** (1.37 s).
Thus **519 tests passed** across the complete repository selections, with no failures.
Kernel coverage is **94.36%** (4787/5073 statements), exceeding the 90% gate.
Ruff lint and formatting pass all **67 selected Python files**; `mypy --strict aerokernel` passes all **32 runtime modules**. All 32 modules also parse under
Python 3.10 grammar. The execution interpreter is Python 3.11.12.

All three examples ran successfully with the installed editable package:
`examples.two_engine_toy` preserves the twelve original transactions;
`examples.lockstep_adapter_skeleton` completes the actual 20-ms native call before
applying its 3-ms input; `examples.sampled_relations` records one crossing `(0,3]`.
AST comparisons with the M1 snapshot find no removed exports, public functions or
methods, and no changed existing signatures in `__init__`, `sdk` or `testing`.
`docs/DESIGN.md` is byte-identical to that snapshot. No upstream workspace was
modified and no commit, branch or reset operation was run.

Reproduce the final gates from this workspace:

```sh
.venv/bin/python -m pytest -c pyproject.toml tests/kernel \
  --basetemp=tests/.pytest_delivery --cov=aerokernel \
  --cov-report=term --cov-fail-under=90
.venv/bin/python -m pytest -c pyproject.toml tests/test_aerograph_audit.py \
  --basetemp=tests/.pytest_audit_delivery -q
.venv/bin/python -m pytest -c pyproject.toml -m perf \
  --basetemp=tests/.pytest_perf_delivery -q
.venv/bin/python -m ruff check aerokernel tests/kernel examples benchmarks
.venv/bin/python -m ruff format --check aerokernel tests/kernel examples benchmarks
.venv/bin/python -m mypy --strict aerokernel
.venv/bin/python -m examples.two_engine_toy
.venv/bin/python -m examples.lockstep_adapter_skeleton
.venv/bin/python -m examples.sampled_relations
.venv/bin/python benchmarks/kernel_bench.py --entities 100 --check-targets
.venv/bin/python benchmarks/kernel_bench.py --entities 1000 --check-targets
```

Retained evidence: [kernel tests](../benchmarks/evidence/m2-kernel-tests.txt),
[coverage](../benchmarks/evidence/m2-coverage.json),
[audit tests](../benchmarks/evidence/m2-audit-tests.txt),
[perf tests](../benchmarks/evidence/m2-perf-tests.txt),
[ruff lint](../benchmarks/evidence/m2-ruff-check.txt),
[format](../benchmarks/evidence/m2-ruff-format.txt),
[mypy](../benchmarks/evidence/m2-mypy.txt),
[toy](../benchmarks/evidence/m2-example-toy.txt),
[lockstep](../benchmarks/evidence/m2-example-lockstep.txt) and
[sampled example](../benchmarks/evidence/m2-example-sampled.txt).
External simulator determinism and Python 3.10 runtime execution are not claimed;
the external adapter skeleton deliberately uses the declared test simulator.

## M1 historical validation

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
