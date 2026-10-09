# K2-M1R: independent milestone 1 review

Reviewed on 2026-10-08 against normative `docs/DESIGN.md`. **M1 needs changes before acceptance.** The ordinary toy trace and current tests pass, but there are reproducible violations of dispatch causality, immutable publication, replay, generation allocation and routing. No production code, tests or example were changed. The review deliverable is this file; executable probes and logs are under `.kernel-agents/review-m1/`.

## Evidence and scope

Read `aerokernel/*.py`, `tests/kernel/*`, `examples/two_engine_toy.py`, `docs/IMPLEMENTATION.md`, DESIGN and REPORT's Round 2. This review treats DES/fixed-step state, messages/actions, journaling and the requested invariants as M1. Explicitly rejected relation/cardinality, sampled, RPC, lockstep and real-time profiles are tracked as scope gaps rather than implemented behavior. This does **not** certify full DESIGN §13 v0.1 conformance or external simulator behavior.

Executed with `/mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python`:

```sh
env PYTHONDONTWRITEBYTECODE=1 \
  TMPDIR="$PWD/.kernel-agents/review-m1/tmp" \
  HYPOTHESIS_STORAGE_DIRECTORY="$PWD/.kernel-agents/review-m1/hypothesis" \
  .venv/bin/python -m pytest tests/kernel -q -p no:cacheprovider \
  --basetemp=.kernel-agents/review-m1/pytest-tmp
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  .venv/bin/python .kernel-agents/review-m1/reproduce.py
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  .venv/bin/python .kernel-agents/review-m1/bitemporal_oracle.py
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  .venv/bin/python .kernel-agents/review-m1/sdk_smoke.py
```

- Existing kernel suite: **142 passed in 29.71 s**, recorded in `pytest.txt`. IMPLEMENTATION's 262-test claim covers a broader validation invocation; 142 is the count for the requested kernel directory, not evidence that the earlier count was false.
- An independent authored interval-list oracle checked **10,362 live/replay queries** across six traces, including both time axes, overlapping intervals, null, finite/open retractions, microstep validity boundaries, historical acquisition and every complete prefix after creation. It passed. Its expected values come from authored versions and integer tuple comparisons, not `Store.field` or `typed_equal`.
- `reproduce.jsonl` contains the failures below. The SDK sketch passed Python 3.10 grammar parsing and a real kernel trace covering accept, interleaved fact write, execute, feedback, success, cancel decision, cleanup completion and replay. Execution used Python 3.11; no Python 3.10 runtime claim is made.
- No upstream builds, upstream Git operations, commits, branches or resets were run. All scratch/cache/temp paths used by these probes are inside this workspace.

## Ranked findings

Severity follows DESIGN's review convention: critical threatens causality, publication or replay; high materially breaks correct execution/interoperability; medium identifies a bounded defect or contract ambiguity; low concerns API organization.

| ID | Severity | Finding | Ran probe |
|---|---|---|---|
| F1 | Critical | A command publication item substitutes for actual recipient dispatch as a cause | `undispatched_command_cause` |
| F2 | Critical | Dispatched dirty payloads mutate already published invocation records; live/replay work state differs | `mutable_dirty` |
| F3 | Critical | Legal live headers with enlarged pinned budgets cannot replay | `big_header`, `deep_header`, `integer_header` |
| F4 | High | Host cancellation skips the recipient's route lag | `cancel_lag` |
| F5 | High | Remove then recreate the same ID passes in one wave | `remove_recreate` |
| F6 | High | Replay accepts work beyond the pinned microstep bound as complete | `replay_microstep_bound` |
| F7 | High | Replay silently collapses duplicate partition declarations | `replay_duplicate_partition` |
| F11 | High | Modest sustained write loads exceed practical memory/time; binding enumerates DAG paths | Both workload benchmarks and binding benchmark |
| F13 | High | Expiry of a preceding reactive write loses value-only dirtiness | `expiry_microstep` |
| F8 | Medium | Partition ID `kernel` collides with the internal timer namespace | `timer_namespace` |
| F9 | Medium | A known dynamic generation can fault a lagged read before its creation | `lag_dynamic_creation` |
| F10 | Medium | The delivered RNG context exposes every other partition's stream | `rng_scope` |
| F12 | Low | The documented reusable engine facade lives in the production testing module | Documented import and executed example/test path |

### F1 — command publication bypasses dispatch causality

**Locations:** `aerokernel/transactions.py:129`, `:132`, `:155`; `aerokernel/control.py:130`. DESIGN §5, particularly `docs/DESIGN.md:104` and `:106`, requires actual recipient dispatch for a reaction, with recipient-specific timing and private authorization.

The historical-cause filter rejects enqueue/intent/reservation and another partition's `operation.message`, but accepts the kernel's **`ingress_publish`** item without a dispatch check. This is a second spelling of an undelivered command publication.

The probe uses one DES target with `message_lag_ns=10`, one offline command at `(0,0)`, and an unrelated reset activation:

```python
# reset proposal
(Activate("target"),)
# on the activation, before the command reaches this target:
(Emit("event", "response", "topic", view.instant, None,
      causes=(ItemRef(2, 3),)),)
```

In this fixture record 2/item 3 is exactly `ingress_publish`. The response commits at **`(0,1)`**, the target has received no command, and its actual command dispatch is at **10 ns**. Replay accepts the same illegal trace. Receipts themselves still enforce dispatch; the bypass affects ordinary fact/event effects and cannot be dismissed as permitted retained receipt causes.

**Repair:** validate causes by semantic kind and source/recipient scope, including kernel ingress publications. Command publication/enqueue/reservation must not stand in for delivery. Preserve the explicitly allowed original command dispatch in later target receipt calls. Also audit lifecycle/action cause kinds, rather than protecting only fact versions and partition emissions.

### F2 — dirty payload aliases the flushed intent

**Locations:** `aerokernel/codec.py:54`, `:55`; `aerokernel/messages.py:180`; `aerokernel/coordinator.py:348`; `aerokernel/scheduling.py:159`, `:164`. DESIGN §3 requires immutable exposed nested values; §9 includes invocation work in every-prefix reconstruction.

`decode_record` passes `payload` through by reference. `Message` freezes it in `__post_init__`, but **`Dirty` does not**. The coordinator decodes the stored intent's dirty list directly before calling the engine. Consequently this ordinary callback mutation succeeds:

```python
# initialize
ScheduleTimer("t", Instant(1), {"input": [7]})
# callback
dirty[0].payload["input"][0] = 99
```

After settlement, the live canonical invocation record contains `[99]`; replay of the acknowledged WAL contains `[7]`. Both `k.records == replay(...).records` and the invocation-index equality are **false**. Fact values in this particular probe still agree; the demonstrated divergence is recorded invocation/work state, which §9 explicitly promises to reconstruct. Frozen dataclass attributes do not protect the nested payload.

**Repair:** freeze/detach every dispatched payload using the run's pinned budget and prevent record decoding from lending mutable journal containers to engines. Test nested mutation of timer, lifecycle and receipt dirty payloads, including retained historical views/intents.

### F3 — header bootstrap uses defaults instead of the recorded budget

**Locations:** `aerokernel/journal.py:78`, `:90`, `:94`, `:113`. DESIGN §9 pins resource budgets for in-process values and replay alike.

`read_records` parses the whole first header under the default budget, then reads its advertised budget. Valid live configurations therefore fail before replay can adopt their policy:

| Authored live input | Live result | Replay result |
|---|---|---|
| `frame_bytes=16 MiB`, configuration string of 9 MiB | Starts; header is **9,438,633 bytes** | `RESOURCE_LIMIT: frame bytes exceeded` |
| `nesting_depth=200`, configuration containing 140 nested arrays | Starts | `RESOURCE_LIMIT: nesting depth exceeded` |
| `integer_digits=4200`, root seed `10**4100` | Starts | `RESOURCE_LIMIT: integer token digits exceeded` |

Each journal is produced by the live public API and contains complete LF-terminated records. This directly defeats I12's valid-prefix replay promise. `test_replay.py:130` puts the large integer in a later payload, so its small header misses the bootstrap problem.

**Repair:** make header bootstrap consume the advertised policy, or expose an explicit replay bootstrap budget agreeing with that header. Apply the same supported policy at live binding and replay. Enlarged budgets must remain usable, not merely be recorded and rejected later.

### F4 — cancellation arrives earlier than its declared lag

**Locations:** `aerokernel/control.py:163` versus `aerokernel/transactions.py:717`. DESIGN §5 and `docs/IMPLEMENTATION.md:40` apply exactly one recipient lag before latching.

With a DES target lag of 10 ns, a host command published at 1 dispatches correctly at 11 and is accepted. Host `cancel(id)` then reserves publication at 12. `boundary_control` calls `eligibility` without the target's `message_lag_ns`, so the cancel dispatches at **12**, rather than **22**. Engine-originated cancellation supplies the lag correctly.

This changes modeled cancellation races and can also choose the wrong fixed-step latch boundary. The DES probe alone proves the defect; no unrun fixed-step claim is needed.

**Repair:** use the common route-lag/latch path for host and partition cancellation, and assert dispatch times separately from message publication and queue eligibility.

### F5 — same-wave generation reuse is order-dependent

**Locations:** `aerokernel/transactions.py:205`, `:207`, `:382`, `:386`; `aerokernel/scheduling.py:219`. DESIGN §3 (`docs/DESIGN.md:70`) explicitly forbids remove/recreate of an ID in one transaction.

Start generation zero, then return `(Remove(old), Create(replace(old, generation=1)))` from one controller reaction. Both are accepted with **the same `Cut(4, Instant(0,1))`**. Replay also accepts the replacement. The reverse order is rejected because `Remove` checks `self.creating`; `create` checks live generations after the removal and never consults the wave's removal set.

**Repair:** preflight lifecycle mutations over the whole merged wave by textual ID, rejecting every create/remove combination regardless of order, generation, concrete type or partition. Keep legal reuse in a later transaction. The existing generation property uses separate odd/even physical boundaries and misses this ordering case.

### F6 — replay does not enforce the microstep budget

**Locations:** `aerokernel/replay.py:152`; `aerokernel/scheduling.py:73`; compare the live check at `aerokernel/coordinator.py:602`.

Generate a valid one-activation journal with `max_microsteps=1`. Keep the header/reset transaction unchanged, but change the reaction intent, its return and seal coordinates consistently from `(0,1)` to `(0,1025)`. Replay accepts **`Cut(5, Instant(0,1025))` with `incomplete=False`**, while retaining the pinned maximum of one.

These are complete, structurally consistent records but an illegal same-time work schedule. The live budget check is outside the validation path reused by replay.

**Repair:** enforce the pinned bound in the shared grant/record rules, including reactive calls and timer-boundary work. Add a corruption fixture that changes all dependent coordinates consistently; changing only one generated item tests consistency, not this invariant.

### F7 — duplicate replay partitions disappear silently

**Location:** `aerokernel/replay.py:81`; compare live rejection at `aerokernel/coordinator.py:93`. DESIGN §§6/9 require unique partition IDs and exact header integrity.

Duplicate the sole partition descriptor in an otherwise valid header and retain all subsequent records. Replay completes with **two declared header partitions, one actual stored partition, and `incomplete=False`**. The dict comprehension silently deduplicates before validation. Live binding rejects duplicate IDs.

**Repair:** validate the typed descriptor sequence and uniqueness before indexing it. Apply the same binding/cycle constraints to live and replay headers. Do not silently collapse duplicate declarations, even when their contents agree.

### F8 — a legal partition name breaks timer dispatch

**Locations:** `aerokernel/transactions.py:575`; `aerokernel/control.py:188`. Partition IDs are arbitrary nonempty UTF-8 identifiers; the internal kernel timer namespace is meant to be separate.

Bind `Partition("kernel", "e")`, return `ScheduleTimer("my_timer", Instant(1), {"input": 7})`, and run to 1. Binding/start succeed. Timer firing treats the partition timer as a validity timer and accesses its nonexistent `timer["key"]`, producing **`KeyError('key')`**, fault code `ENGINE_EXCEPTION`, and no intended notification.

**Repair:** tag timer namespaces/kinds explicitly instead of inferring kernel authority from a partition ID. Give malformed timer records a stable, contextual kernel error. Avoid imposing an otherwise undocumented reserved user ID merely to preserve this collision.

### F9 — lagged reads and dynamic creation need a defined absence rule

**Locations:** `aerokernel/state.py:273`, `:278`, `:281`, `:175`. DESIGN §2.2 defines lagged knowledge/validity and genuine pre-origin absence; §3 resolves lifecycle before field validity.

A controller creates a generation at 10 ns. A reader declaring lifecycle access and `Dependency("x", lag_ns=5)` receives the valid creation notification at 10. `view.field((new_ref, "x"), Instant(5))` passes the initial identity/active-key checks at its invocation cut, then faults **`ENTITY_UNKNOWN`** when the internally derived lag cut precedes creation. The equivalent current host view returns lifecycle `ABSENT` at valid time 5.

This is a real fault, but the distinction between **an explicitly supplied cut before identity exists** and **an internally imposed lag cut for an already known generation** needs clarification. Retain rejection for truly unknown/foreign identities. For the latter case, returning lifecycle absence would make dynamic generations usable through the same declared lag contract; alternatively document that engines must guard creation time explicitly. Do not convert unknown field values into defaults.

### F10 — RNG ownership is a convention at the public context boundary

**Locations:** `aerokernel/coordinator.py:107`, `:113`, `:328`; `aerokernel/engine.py:155`; `aerokernel/rng.py:60`.

Every engine receives the same `RunContext.rng` dictionary of **all** partition streams. An engine owning only `thief` can call `context.rng["victim"].stream("s").random()` during reset. With seed 7, the victim's first observed number changes from **0.8559490015532898** to **0.2601434820698473**, without any ownership error.

This does **not** establish nondeterminism for conforming engines: the thief violates the declared exclusive-stream contract, and Python globals/private internals cannot generally be policed. It does establish unnecessary public capability exposure, contrary to the implementation's unqualified “exclusive” wording. Pass each engine only its declared partition streams through a read-only mapping; the SDK should expose `ctx.rng(name)` scoped to the invoking partition. No `hash()`, wall-clock ordering or unordered ready-set ordering defect was found in the tested conforming paths.

### F11 — measured performance and allocation costs

`benchmark.py` uses two genuine in-process engine instances: a fixed-step writer and an idle DES engine. Every entity has three integer fields; **all three fields are written every 100 ms step**, with bootstrap versions retained. Default kernel/journal policies are used. Wall time includes bind/start/run; `ru_maxrss` is Linux peak process RSS. Journal bytes are the sum of acknowledged lines, avoiding a measurement-time whole-journal join.

Each child was limited to **2 GiB virtual address space** and 600 s wall time. These are review resource limits, not a kernel capacity guarantee. The workload runs overlapped on the shared host, so timings are sanity measurements, not isolated comparative throughput claims.

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  .venv/bin/python .kernel-agents/review-m1/benchmark.py --entities 1000 --steps 600
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  .venv/bin/python .kernel-agents/review-m1/benchmark.py --entities 100 --steps 600
```

| Entities × fields | Requested / settled simulated time | Wall time | Bootstrap | Peak RSS | Acknowledged journal | Fact versions | Outcome |
|---|---|---|---|---|---|---|---|
| 1000 × 3 | 60 / **5.9 s** | **181.74 s** | 4.44 s | **1991.20 MiB** | **278,472,984 B**, 186 lines | 180,000 | `MemoryError`; target not completed |
| 100 × 3 | 60 / **60 s** | **187.56 s** | 0.44 s | **1948.14 MiB** | **281,186,711 B**, 1807 lines | 180,300 | Completed |

At 1000 entities the native method attempted step 60, but the committed/sealed prefix contains only 59 steps plus bootstrap. The report deliberately reports that prefix instead of calling the partial run successful. Full 1000-entity/60-second memory or time was **not measured or extrapolated as a completed result**.

Hot spots with concrete code paths:

- `aerokernel/transactions.py:470`: appending a version copies that key's full history tuple. H writes to a key cost O(H²) aggregate pointer copying, even though immutable fact values are shared.
- `aerokernel/state.py:122`, `:139`, `:142`: every candidate/control/intent copies whole key indexes, all accumulated intent dictionaries, and record/cut lists. This is not a deep copy of every fact value, but costs grow with total history. `coordinator.py:257` additionally retains full action-index snapshots at every prefix; action-heavy runs add O(records × actions) references.
- `aerokernel/scheduling.py:99`, `:141`: scan work per selected partition, then remove each ready item from a list. Large fan-out/dirty waves can incur quadratic queue work. `transactions.py:207` also scans lifecycle history for every creation.
- `aerokernel/scheduling.py:278` and `transactions.py:363`, `:471`: journal records retain returned batches, proposals and generated versions, duplicating much of each fact's identity/time/value encoding. `journal.py:36`, `:58` keeps both the default `BytesIO` sink and every encoded line, while the store retains decoded record trees too. A file sink still retains `_lines`, so it does not remove all journal-byte memory.
- `aerokernel/values.py:59`: each normalization recomputes a large decimal bound; schema validation, freezing and encoding repeatedly normalize values. This is avoidable CPU overhead, not a correctness fallback.

There is also an independent **exponential binding** hot spot: `coordinator.py:233` recursively visits every dependency path with no completed-node/SCC memoization. `bind_benchmark.py` constructs a valid dense acyclic potential dependency graph, without entities or model computation:

| Partitions | DAG edges | Measured bind wall time |
|---|---|---|
| 12 | 66 | 0.0123 s |
| 16 | 120 | 0.0573 s |
| 20 | 190 | 0.7047 s |
| 22 | 231 | 2.6128 s |

**Repair direction:** retain complete bitemporal history while sharing immutable version/index structures and journal storage; avoid redundant whole-prefix copies/encodings; use indexed queues; compile SCCs once in O(partitions + dependency edges). These allocation/indexing fixes do not require pruning, checkpoints, numeric shortcuts or weaker validation. Do not make the workload “pass” by skipping writes, truncating history or synthesizing unchanged samples.

### F12 — reusable facade belongs in SDK

`docs/IMPLEMENTATION.md:30` directs platform engines to the shipped `aerokernel.testing.SimpleEngine` (`aerokernel/testing.py:11`), whereas DESIGN §11 (`docs/DESIGN.md:201`) places test helpers outside production. The actual facade is useful and does not manufacture measurements; the problem is its public location and insufficient receipt/time-policy ergonomics. Put the reusable driver in `aerokernel.sdk`, retain authored toy models in examples/tests, and keep low-level proposals available.

### F13 — expiry compares the wrong side of a physical boundary

**Locations:** `aerokernel/transactions.py:617`, `:622`, `:637`. DESIGN §8 requires effective-value comparisons before/after validity boundaries, including suppression only when the value really remains equal.

Bootstrap creates an active, unset field. Its owner writes `x=1` during reaction `(0,1)`, valid on `[(0,1),(1,0))`. A value-only observer receives that write at time 0. At time 1 the fact expires and the current field becomes `ABSENT`, but **no expiry notification reaches the observer**.

For a physical-time timer, `notify_field` uses `Instant(ns-1,0)` as the old coordinate. In this probe that is `(0,0)`, before the reactive write became valid. It therefore compares absence with absence and suppresses a real `1→ABSENT` transition. The observer's only notification remains the time-zero field change; an evaluator retaining its last result would stay stale.

**Repair:** compare the effective value on the actual left side of the nominal validity boundary, accounting for preceding microsteps/lifecycle/version intervals, rather than assuming the prior physical instant's microstep zero is its predecessor. Add expiry and future-valid start cases after reactive writes, including value-only consumers. `test_adversarial.py:226` initializes the expiring fact at `(0,0)` and expires it at 4, so its previous-time probe happens to work.

## IMPLEMENTATION deviations: retain, narrow or revert

All eight numbered clarifications at `docs/IMPLEMENTATION.md:72` onward were assessed independently of its “Done” labels.

| # | Decision | Reason / necessary qualification |
|---|---|---|
| 1 — M1 scope overrides §13 | **Retain as an explicit M1 subset; not a v0.1 spec override.** | Requested invariant coverage excludes relation/RPC/sample invariants I6/I11/I13. Rejecting active unavailable features is appropriate. The claimed original K2-M1 authorization is reported by IMPLEMENTATION, not independently established here. DESIGN §13 remains normative for full v0.1. Keep the distinct `zone.transition` name and absent fulfills edge; never label the ordinary reactor as sampled `entered`. |
| 2 — portable schema spelling | **Accept.** | Strict finite float versus integer, explicit record/union/dimension spelling and rejection of unsupported active constraints are compatible with the portable subset. No coercion or invented data is introduced. |
| 3 — explicit active field selection / owned-key reads | **Accept as a clarification.** | Rules/exact bindings explicitly select capability application; production declarations alone grant nothing. Implicit reads are limited to actually resolved owned keys. Other field reads still need dependencies. Document this owned-key read convention at the API boundary. It does not authorize F1. |
| 4 — origin-zero, nonsplittable fixed-step | **Accept for this M1 profile; keep the broader spec gap open.** | Holds, interval-start cuts, coverage and next-grid application preserve the intended causality. Positive/nonzero native origins and exact splitting are not implemented. The explicit rejection is preferable to pretending support, but prevents an unqualified v0.1 timing conformance claim. F4 is a routing bug, not an acceptable latch-policy choice. |
| 5 — one reset call per multi-partition engine | **Accept.** | Initial views are equally empty, returns are checked per recorded partition intent and the combined candidate preserves separate authority. Subsequent calls remain canonical/serial. This must not become permission to share evolving views across partitions. |
| 6 — retain original receipt dispatch causes | **Accept.** | Later receipts need the original actual target dispatch plus the current head; earlier ordered local receipt references are legitimate. The clause cannot authorize command publication/enqueue or another recipient's dispatch. |
| 7 — ingress identity / closed record codec | **Accept the contract; fix its implementation.** | A pinned host source, request-content idempotency before boundary assignment, separate source kinds and `$type` versus schema-directed payload tags fit DESIGN. The decoder and header validation currently violate that contract in F2/F3/F7; the clarification does not excuse them. |
| 8 — complete history with copied candidates | **Accept retention semantics; revise the allocation choice/limitation.** | Full history is required. It does not require full index/intent/action snapshots or duplicate encoded journal buffers at every commit. The measured M1 workload fails at only 5.9 s for 1000 entities under the review limit. Repair allocation without changing history semantics; bounded retention remains deferred. |

Other gaps: diagnostics are less contextual than DESIGN §10 promises (F8 records only a generic engine-exception code); ordinary observers are host-owned; there is no executable lockstep/wait/watermark, relation/obligation, sampled frame/cone or RPC implementation. These are explicit gaps, not evidence of fake external success. The toy is authored content and separates arrival from business acceptance correctly on its intended single-order trace.

## Test strength and the missing adversarial cases

The suite has useful hand-written checks: immutable fact values, writer/target rejection, ordered receipts, generation reuse, native-start cuts, exact toy transaction sequence and WAL failure. Passing them is useful evidence. Statement coverage and shared live/replay validation are insufficient for the defects above.

**An actually vacuous prefix assertion:** `tests/kernel/test_adversarial.py:303` compares queues only when the replay cut equals the final live cut; every earlier prefix selects `else True`. Thus it exercises parsing/replay, but its queue assertion tests only the last prefix.

**Shared or incomplete oracles:** `test_core.py:249` has a genuinely separate final-validity oracle, but the older-prefix loop at `:261` compares replay with the same live `StateView.field` algorithm. `test_trace.py:93` does likewise. Both live and replay invoke `build_wave`/`Candidate`; F1/F5 demonstrate that agreement can preserve an illegal trace. `test_foundations.py:113` uses the implementation's `typed_equal` to judge value round trips; explicit signed-zero/type examples partly compensate. The RNG derivation expectation at `:199` reuses `canonical_json`, so independently pinned byte/hash fixtures would strengthen it. `test_registry.py:215` varies two priorities, not the full selector/activation space. These are limitations, not a claim that every property is tautological.

| Invariant | Missing adversarial coverage that matters here |
|---|---|
| I1 | Issued same-Instant cuts combined with lag/native restrictions and consistently corrupted replay microsteps; custom **header** budgets on each resource axis. Scalar ordering/rational rounding already have useful independent tuple/Fraction checks. |
| I2 | Remove/create of one textual ID in both operation orders, cross-type generations and separate cohort batches in the same wave; dynamic creation interacting with older lag cuts. Existing reuse traces separate removal and creation. |
| I3 | Non-fact lifecycle/action/message publication cause scopes; dynamic ownership activation with lagged consumers; source namespace collisions in kernel-owned timer work. Ordinary nonowner writes are covered and were not bypassed by these probes. |
| I4 | Mutate dispatched nested dirty payloads; WAL failure **on the transaction flush after a stateful call**, not only before it. `test_core.py:155` flips its sink before `run_until` writes `run_limit`, so that injected failure never reaches engine advancement/publication. Retain its useful pre-call case and add the later case. |
| I5 | A separate two-axis oracle at every prefix including retractions, null, lifecycle death and lag/native cuts; value-only expiry/start notifications after reactive publication (F13). The new scratch oracle strengthens ordinary fact reads, but does not settle F9's dynamic identity policy or cover relation history/notification semantics. |
| I9 | Every command-origin item kind tested as a premature cause; host and partition cancel lag/latch symmetry; malformed but otherwise consistent dispatch behind frontier/seal; multiple recipients with unequal lag and repeated deferred work. Existing event route-lag tests miss host cancellation. |
| I10 | A complete independent state-transition table over all statuses, stale heads after intervening feedback/decisions, multiple queued cancels, missing receipts and failures on later publication. The current property exhausts submitted's next states, not every pair. Transport/cancel acceptance must continue to leave success/cleanup unproven. |
| I12 | Compare every prefix's pending/authorized/returned work against captured independent expectations, not `else True`; enlarged header limits; duplicate descriptors; consistent illegal microstep schedules; mutable dirty/intent aliases. The “no live clock” test at `test_replay.py:35` patches engines and RNG, but no clock function. |
| I14 | Cross-process `PYTHONHASHSEED` fixtures, larger registration/cohort/selector permutations and independently pinned canonical bytes/RNG vectors. Scope public RNG access per engine. No numerical determinism claim should be inferred for future external adapters. |

Five plausible mutations/omissions **the current suite demonstrably does not catch** (it passed while all five behaviors were present):

1. Drop the recipient lag from host cancel eligibility.
2. Permit remove-then-create by consulting only the candidate's remaining live IDs.
3. Decode `Dirty.payload` as the original mutable journal dictionary/list.
4. Accept a historical kernel ingress publication as a command-reaction cause.
5. Parse the header with default limits before adopting its advertised budget.

Each has a runnable counterexample above, stronger evidence than a hypothetical surviving mutant. For test improvements, use a small authored transition/interval/queue model rather than duplicating the production implementation; capture snapshots at each actual publication where the public API cannot query historical work.

## Proposed `aerokernel.sdk` API

The facade should build ordinary ordered proposals inside **one real invocation**, then return `view.batch`. It must not publish directly or choose a producer, grant, availability, message ID, native frontier or read cut. Platform bindings remain explicit. Decorators register typed handlers only for command schemas already declared on the partition; they confer no lifecycle or field authority.

The following **135-line sketch** was saved separately and smoke-tested; it is a proposal, not installed code. `commands` is engine-owned retained state keyed by actual dispatch IDs. `policies` must be supplied explicitly per field. For a local derived state, an application may deliberately choose canonical acquisition at computation and open validity; external observations must supply their actual source stamp/validity instead. There is no implicit universal hold, interpolation or default value.

```python
"""Proposed aerokernel.sdk facade; not installed production code."""
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar
from aerokernel import (ABSENT, CancelDecision, Delivery, Fact, FactWrite, Feedback,
                       Instant, Interval, LocalCause, Receipt, ScheduleTimer, Stamp,
                       Emit)
from aerokernel.values import thaw

T = TypeVar("T")

@dataclass(frozen=True)
class Command(Generic[T]):
    delivery: Delivery
    payload: T

    @property
    def id(self):
        return self.delivery.message.id

@dataclass(frozen=True)
class FactPolicy:
    acquired: Callable[[Instant], Stamp]
    valid: Callable[[Instant], Interval]

class EngineContext:
    def __init__(self, view, inbox, dirty, commands, policies):
        self.view, self.now = view, view.instant
        self.commands, self.policies = commands, policies
        self.ops, self.heads = [], {}
        self.inputs = [d.dispatch_ref for d in inbox] + [d.cause for d in dirty]

    def _causes(self, *extra):
        return tuple(dict.fromkeys(self.inputs + list(extra)))

    def _append(self, op):
        index = len(self.ops)
        self.ops.append(op)
        return LocalCause(index)

    def get(self, ref, field, *, valid_at=None):
        fact = self.view.field((ref, field), self.now if valid_at is None else valid_at)
        if isinstance(fact, Fact):
            self.inputs.append(fact.version)
            return fact.value
        return ABSENT

    def set(self, ref, field, value, *, acquired=None, valid=None):
        policy = self.policies[field]  # Explicit model policy; no kernel defaults.
        return self._append(FactWrite(
            (ref, field), value,
            policy.acquired(self.now) if acquired is None else acquired,
            policy.valid(self.now) if valid is None else valid, self._causes()))

    def remember(self, delivery, decode):
        if delivery.message.kind != "command" or delivery.recipient != self.view.partition:
            raise ValueError("an actual command delivery to this partition is required")
        cmd = Command(delivery, decode(thaw(delivery.message.payload)))
        self.commands[cmd.id] = cmd  # Engine-owned retention of original dispatch.
        return cmd

    def command(self, command_id):
        return self.commands[command_id]  # Never turn an undispatched ID into a handle.

    def _head(self, cmd):
        if cmd.id not in self.heads:
            self.heads[cmd.id] = self.view.action(cmd.id).head
        head = self.heads[cmd.id]
        if head is None:
            raise ValueError("no submitted receipt head")
        return head

    def _receipt(self, cmd, status, result=None):
        cause = self._append(Receipt(cmd.id, status, result,
            self._causes(cmd.delivery.dispatch_ref, self._head(cmd))))
        self.heads[cmd.id] = cause
        return cause

    def accept(self, cmd):
        return self._receipt(cmd, "accepted")

    def execute(self, cmd):
        return self._receipt(cmd, "executing")

    def succeed(self, cmd, result=None):
        return self._receipt(cmd, "succeeded", result)

    def fail(self, cmd, result=None):
        return self._receipt(cmd, "failed", result)

    def feedback(self, cmd, payload):
        return self._append(Feedback(cmd.id, payload,
            self._causes(cmd.delivery.dispatch_ref, self._head(cmd))))

    def decide_cancel(self, cmd, cancel, accepted, reason=None):
        if cancel.message.kind != "cancel" or cancel.recipient != self.view.partition:
            raise ValueError("an actual cancel delivery is required")
        ref = self._append(CancelDecision(cmd.id, cancel.message.id, accepted, reason,
            self._causes(cancel.dispatch_ref, self._head(cmd))))
        if accepted:
            self.heads[cmd.id] = ref
        return ref

    def canceled(self, cmd):
        return self._receipt(cmd, "canceled")  # Caller confirms actual cleanup first.

    def emit(self, schema, target, payload, *, at=None, stamp=None):
        return self._append(Emit("event", schema, target,
            self.now if at is None else at, payload, self._causes(), stamp))

    def schedule(self, at, payload=None):
        timer_id = f"sdk:{self.now.ns}:{self.now.microstep}:{len(self.ops)}"
        self._append(ScheduleTimer(timer_id, at, payload))
        return timer_id

    def batch(self):
        return self.view.batch(tuple(self.ops))  # Coordinator validates and publishes.

def handles(schema, decode):
    def decorate(fn):
        fn.kernel_command = (schema, decode)
        return fn
    return decorate

def dispatch_commands(engine, ctx, deliveries):
    table = {}
    for name in sorted(dir(type(engine))):
        fn = getattr(type(engine), name)
        if hasattr(fn, "kernel_command"):
            schema, decode = fn.kernel_command
            if schema in table:
                raise ValueError("duplicate command handler")
            table[schema] = (getattr(engine, name), decode)
    for delivery in deliveries:  # Preserve the kernel's canonical inbox order.
        fn, decode = table[delivery.message.schema_id]
        fn(ctx, ctx.remember(delivery, decode))
```

The driver constructs the context from the actual restricted view, sorted inbox and dispatched dirty notifications. It supplies only this partition's RNG streams and handles physical native integration separately from reactions. The prototype conservatively attaches all authorized current inputs plus read fact versions; a fuller API can offer dependency scopes for more precise causes. Absence reads remain `ABSENT`, with their knowledge cut recorded by the invocation.

Example application handler: `@handles("job.start", decode_start)` followed by `start(self, ctx, cmd): ctx.accept(cmd); ctx.execute(cmd)`. The model later calls `ctx.succeed(ctx.command(id), actual_result)` only after actual completion. Within a batch, local head positions include intervening fact writes; across calls, the head comes from the current permitted action view. Cancellation needs an actual cancel delivery and explicit decision/cleanup. Events and cancellation must have explicit routes/handlers; the driver must not silently discard unsupported inbox items. Add `ctx.reject`, typed `ctx.command` submission, retract/history and lifecycle helpers in the same proposal-only pattern when building the production SDK.

Before accepting M1, fix F1–F8, F13 and the measured allocation/binding problems, resolve F9's contract, and add the focused adversarial tests. Keep the existing strict schemas, single-writer checks, native-start latching, WAL-before-publication and explicit incomplete/fault outcomes; none of these findings calls for a data fallback or synthetic success.

## GLM second-opinion provenance

Three headless WorkBuddy DSH sessions were launched concurrently with inherited project context supplied as absolute normative/background paths, explicit read-only production scope and disjoint scratch ownership. The workspace-local `glm_batch` profile pins `workbuddy/glm-5.3-flash`, `maxTokens=131072`, and no effort parameter. Session identities were verified from actual DSH storage, not inferred from launch requests:

| Scope / owned scratch directory | Actual session | Result |
|---|---|---|
| `temporal/` — state, scheduling, coordinator | `a455c841-0707-4467-a7e8-919d69eb483d` | Exit 1: `dsh: ERROR: model stopped: error`; no `findings.md` or reproducer delivered |
| `actions/` — messages, transactions, control | `23a9ea6e-b2ba-4b84-b38d-0b6098d9d95b` | Streamed analysis without a deliverable; interrupted with exit 130 |
| `replay-tests/` — replay, registry, values, tests | `3d89ff6a-24ff-4c79-b7ce-569c57f149f7` | Streamed analysis without a deliverable; interrupted with exit 130 |

Per-session `out.txt` and `reasoning.txt` remain in the owned directories. No successful GLM review delivery is claimed. Their streamed suggestions were treated as leads, not findings: the duplicate replay partition and validity-boundary concerns were checked with the root's executable probes; an apparent double-append claim based on misread replay indentation was rejected. The complete findings, benchmarks, SDK validation and final integration were independently performed by the reviewer. The failed/unfinished sessions did not prevent completion of the review.
