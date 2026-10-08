# aerokernel milestone 1 implementation

This package implements the **K2-M1 subset** of DESIGN.md, not every feature in
its complete v0.1 scope. Runtime imports use only the standard library. Python
3.10+ is supported; validation was run with the workspace's Python 3.11 venv.
The kernel contains no ontology content, aviation vocabulary, physics defaults,
unit/frame conversions, business success inference, or upstream build imports.

## DESIGN section status

| DESIGN section | Status | Implementation |
| --- | --- | --- |
| §1 Boundary/authority | Done for M1 | Independent package; immutable normalized contracts; private candidate publication. |
| §2 Time/coordination | Partial; M2 profiles deferred | Integer `Instant`/`Cut`, rational mappings, DES/fixed-step common barriers, native holds, native input cuts, physical-before-reaction, lifecycle/cohort priority, route/dependency lag, coverage requirements, global microstep bound, deadlock faults. Lockstep, real time/external waits and numerical splitting raise explicit M2 errors. |
| §3 Identity/state | Done | Namespace/generation allocation across concrete types, separate controllers, active field resolution, typed immutable facts/retractions, bitemporal full history, prefix-resolved death, identity references, explicit cleanup readiness and write freeze. |
| §4 Relations/atomic commits | Partial; relations M2 | Whole-wave candidate schema/authority/cause/action validation; bootstrap creates across owners; no partial view on failure; WAL before visibility. Temporal edges/cardinalities/obligations are M2. |
| §5 Messages/actions | Done | Kernel IDs/sequences/publication, direct/topic routing, recipient lag/grid eligibility and actual dispatch, scoped causes, every invocation intent/return, offline requests, recorded live reservations, source-scoped ingress idempotency, receipts/feedback, cancellation reservations/decisions/cleanup and explicit request rejection. |
| §6 Engines/RPC | Partial; RPC M2 | Engine Protocol, serial calls, exact echoed grants/cuts/frontiers, reset once and idempotent close. JSONL transport state machine/timeouts/taint are M2. |
| §7 Registry/AeroGraph | Core done; compiler external | Immutable DAG/types/descriptors/digest; portable schema subset including references, arrays/vectors/matrices/records/tagged unions; opaque metadata. No AeroGraph normalization/compiler is imported. |
| §8 Notifications/sampling | Partial; sampled profiles M2 | Field/version/value-only, lifecycle and receipt dirtiness, future-validity/expiry deadlines, partition-owned never-reused one-shot timers and cancellations. SampleFrame/cone scheduling/entered are M2. Observer infrastructure remains host-owned. |
| §9 Journal/replay/RNG | Done for M1 | Versioned complete header, flushed/fsynced JSONL WAL, atomic effects, control records, semantic prefix replay without engine/evaluator/RNG execution, strict corruption rejection and explicit truncated-tail recovery; exclusive declared seeded streams. |
| §10 Errors/invariants | Done for requested M1 invariants | Stable exception codes/context; property/unit/adversarial tests listed below. I6/I11/I13 need M2 implementations. |
| §11 API | Done for M1 | `bind/start/submit/cancel/run_until/view/action/close`; no public commit; frozen public records; read-only replay. |
| §12 Worked trace | Partial, authorized M1 adaptation | Executable fixed-step mover + DES orders + ordinary reactive zone producer. Exact commit/receipt sequence tested; arrival and business acceptance remain separate. The fulfills edge and sampled entered profile are deferred. |
| §13 Deferred work | M2 / v0.2+ | M2: lockstep/real time, relation store/cardinality, sampled frames/entered, RPC. The listed v0.2+ transfer/rollback/parallel/resume/checkpoint/custom-validator features remain deferred. |
| Review log | Applied within M1 | Native-start latching, exact prefix reads, cohort/local-cause separation, empty invocation returns, atomic enqueue/status, cancellation-head validation, timer order and portable integer budgets have executable checks. |

## Public use and engine ergonomics

`aerokernel.testing.SimpleEngine` supplies one partition's bookkeeping. A model
normally overrides `initialize(view)`, `integrate(view)` and/or
`on_react(view, inbox, dirty)`, each returning ordered proposals. The helper
acknowledges holds without fabricating measurements. `view.batch(...)` echoes
all grant metadata for custom engines. A typical field producer is 20–40 lines;
[examples/two_engine_toy.py](../examples/two_engine_toy.py) exercises the longer
action workflow.

Engines pin a `version` string and declare selected field reads, message schemas,
message target/topic domains, native timing and named RNG streams. A partition's
`message_lag_ns` applies exactly once before its native-grid latch. Fields use
their declared dependency lag. Dirty payloads contain actual timer payloads,
lifecycle identities, or canonical receipt items, so origin engines learn child
command IDs through their dispatched notifications without accessing internals.

An active M2 profile is explicitly bound through `Partition.features` (for
example `("relations",)`, `("sampled",)`, or `("rpc",)`), or a timing mode.
Binding raises `NotImplementedError` with an `M2_*` code. No M2 state is synthesized.

## Invariant → test mapping

| Invariant | Tests / oracle |
| --- | --- |
| I1 integer time/mapping/cuts | `test_foundations`: Hypothesis integer ordering/rational rounding/typed wire round trips; `test_core`: forged/equal-time prefix cuts. |
| I2 identity/lifecycle | `test_adversarial`: duplicate/nonowner bootstrap; `test_lifecycle`: Hypothesis cross-type generation reuse with new writer, inherited cleanup scopes, removal, namespace/stale write rejection. |
| I3 selected single writer | `test_registry`: Hypothesis exact overrides/priority/ties, uncovered lifecycle domains, production mismatch; `test_adversarial`: nonowner writes. |
| I4 immutable whole wave | `test_core`: invalid final operation and injected WAL flush failure; `test_adversarial`: Hypothesis registration permutations/identical journal bytes; immutable nested values in `test_foundations`. |
| I5 bitemporal/prefix state | `test_core`: Hypothesis publication/validity read oracle and every LF prefix; null/retraction/history; `test_lifecycle`: old death views; `test_messages`: old receipt heads. |
| I7 native causality (additional) | `test_core`: 3→20 latch, native-start cuts and holds; `test_scheduling`: bound violation before publication and exact frontier echo; `test_lifecycle`: input coverage fails before integration. |
| I8 bounded settlement (additional) | `test_core`: microstep storm and deadlock; `test_scheduling`: reentry cycle bind failure, controller-cohort priority and earliest timer order. |
| I9 routing/scoped causes | `test_adversarial`: future/local cause rejection, idempotent reservations and fan-out order; `test_scheduling`: route lag then grid latch, empty fan-out, request identity after later seals. |
| I10 action receipts | `test_messages`: authorized target/dispatch/current head, ordered transitions, feedback, failure before execution, cancel cleanup, terminal exclusion, internal-completion/cancel tie and ingress/partition name collision. |
| I12 offline every prefix | `test_core`/`test_trace`: all complete prefixes and atomic state/work/action effects; `test_replay`: corruption/truncation, unresolved and malformed invocation returns, custom integer budgets, no engine/validator/RNG recomputation. |
| I14 serial determinism | `test_foundations`: Hypothesis RNG derivation/stream independence and typed equality; `test_adversarial`: registration permutation bytes; `test_trace`: same-seed identical full journal. |

Tests use explicitly authored synthetic registries/engines. They do not claim
external simulator repeatability, production observations, or AeroGraph semantic
conformance. Ruff/mypy checks cover this package and its kernel tests/example;
the concurrently owned audit tree has not been changed.

## Spec deviations/clarifications

1. **Task scope overrides DESIGN §13.** K2-M1 explicitly defers lockstep, real time,
   relation cardinality/store, sampled frames/entered and RPC to M2. These reject
   active binding. The example records `zone.transition`, an ordinary reactive
   transition with its authored interval, and does not emit the sampled profile's
   `entered` schema. There is no placeholder fulfills edge pretending to be real.
2. **Portable schema spelling is pinned.** `number` means finite binary64 **float**,
   whereas `integer` means arbitrary-precision int. No numeric coercion occurs.
   Records require `members`, `required`, and boolean `extra`; union discriminator
   is exactly `$case`; vectors/matrices require both dimensions and element schema.
   Unsupported constraints and recursive schema references reject compilation.
3. **Active field selection is explicit.** Matching field rules/exact bindings
   select the generation's active fields. A field rule explicitly applies a
   capability field to its chosen type domain; it never adds inheritance. There
   is no automatic activation of all registry fields. Resolved ownership alone
   grants implicit reads of that exact owned key; other reads require dependencies.
4. **M1 fixed-step profile is nonsplittable, origin zero.** Off-grid deliveries
   latch, intermediate physical grants hold, and integration uses the previous
   native settlement cut. Nonzero native origins and nonlatching/exact-split
   wrappers are explicitly rejected. `Dependency.require_coverage` checks the
   pinned known input versions throughout the native interval before the call;
   otherwise a declared model may hold its own input rather than asking the store
   to extrapolate. Zero-time internal work belongs in reset proposals or explicit
   reactive activation; an unchanged zero horizon with no work deadlocks.
5. **Multi-partition reset is one serial engine call.** Returned batches follow
   the engine's declared partition tuple. Initial cuts/frontiers are identical;
   each batch is checked against its own recorded private intent. Reset receives
   the first partition's restricted empty bootstrap view and sees no partial state.
6. **Retained receipt causes.** A target may cite its original recorded command
   dispatch in later receipt/feedback calls, as required by §5. Recipient ownership
   and cut visibility still apply; another partition's emission/enqueue/dispatch
   cannot substitute for delivery. Local causes always address earlier operations
   in the same originating batch, irrespective of generated item positions.
7. **Ingress identity and serialization.** The host has one pinned ingress source;
   idempotency is scoped by that identity and compares the original request before
   any seal-dependent boundary is assigned. Control reservations do not reopen a
   seal. Internal dataclass records use a closed `$type`/`fields` codec, distinct
   from schema-directed `$ref`/`$case` portable payloads. Format major 1/minor 0 is
   pinned; schema/manifest/header integrity is revalidated on offline replay.
8. **Full history is retained.** Candidate indexes and action snapshots are copied
   between committed prefixes. This favors a reviewable serial implementation;
   no bounded retention/checkpoint or crash-resume promise is made. Large runs
   need later indexing/retention work, not silent truncation or missing-value defaults.

## Known limitations

Only in-process DES and the M1 fixed-step profile are executable. The package
cannot enforce undeclared Python globals or stop an engine using wall time/global
randomness. Conforming engines own actual model state, final cleanup and declared
input-hold policies. Run-limit subdivisions are recorded and are part of byte
reexecution inputs. Replay reconstructs a prefix; it neither resumes external
execution nor retries stateful calls. A complete WAL line whose flush acknowledgment
was lost may be authoritative during recovery even though no new live state was
exposed. Faulted, pending-invocation, pending-ingress and unfinished run-control
prefixes are marked incomplete, never successful.

The WorkBuddy GLM sessions were attempted concurrently with file ownership and
workspace-local profiles. Implementation sessions produced no delivered files
(model errors/unfinished analysis); their work was completed by the root agent.
The separate review also ended with a model error. Its log findings about
idempotency and timer ordering were independently verified and fixed; no successful
GLM implementation/review delivery is claimed.

## Validation

Run from the workspace with the pinned interpreter:

```sh
.venv/bin/python -m pytest --cov=aerokernel --cov-report=term-missing
.venv/bin/python -m ruff check
.venv/bin/python -m ruff format --check
.venv/bin/python -m mypy --strict aerokernel
.venv/bin/python -m examples.two_engine_toy
```

Final validation on the delivered revision: **262 tests passed** (92.38s),
**94.12% statement coverage** for `aerokernel`
(2465/2619 statements). Ruff lint and format checks passed;
strict mypy passed all 20 package modules. The example ran successfully, and all
20 modules parse with Python 3.10 grammar. Python 3.10 runtime execution was not
available in the pinned environment; the executed interpreter was Python 3.11.12.
