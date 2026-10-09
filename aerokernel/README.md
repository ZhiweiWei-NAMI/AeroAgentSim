# aerokernel

`aerokernel` is an independent, pure-stdlib Python (>= 3.10) discrete-event
simulation kernel: time coordination, single-writer state authority, typed
commands and receipts, an append-only journal, and deterministic replay. It
ships no physics, domain ontology or process management; adapters bring their
own models and declare what they read and write.

Install from the repository root:

```sh
pip install -e './aerokernel[test]'
```

Run the test suite:

```sh
cd aerokernel
python -m pytest -q
```

Worked, runnable examples live in [`examples/`](examples/):
[`two_engine_toy.py`](examples/two_engine_toy.py) (bootstrap, movement and a
sampled zone evaluation), [`per_stream_ingress.py`](examples/per_stream_ingress.py)
(named live ingress streams and watermarks),
[`sampled_relations.py`](examples/sampled_relations.py),
[`lockstep_adapter_skeleton.py`](examples/lockstep_adapter_skeleton.py)
(native-grid adapter with certified holds) and
[`remote_decision_pause.py`](examples/remote_decision_pause.py)
(wall-clock hold over RPC).

The normative design specification is [`docs/DESIGN.md`](docs/DESIGN.md).

## What it provides

- **Time coordination.** `Instant(ns, microstep)` orders physical time and
  same-time reactive waves; conservative coordination advances partitions to a
  common boundary from declared horizons, settles physical work before
  boundary inputs, reacts in ordered microstep waves, samples after upstream
  cones settle, and seals a physical time only when nothing remains due there.
  Fixed-step, lockstep, DES and real-time timing policies are explicit per
  partition.
- **Ingress and watermarks.** External commands enter through recorded
  reservations with explicit publication boundaries; live streams carry
  monotonic closed-prefix watermarks (optionally derived from source progress
  with an allowed-lateness tail). Late input is rejected or displaced to a
  legal later boundary, never retroactively applied. Named streams and a
  compatibility default stream can coexist.
- **Single writers.** Every active field, relation scope and lifecycle domain
  resolves to exactly one bound writer per entity generation. Identity is
  caller-pinned (`EntityRef` with run/epoch/id/generation/type), generations
  are exact, and removal freezes writes after acknowledged native cleanup.
- **Typed commands and receipts.** Engines propose ordered operations; the
  kernel stamps publication, availability and authority under private
  invocation tokens. Commands move through a closed status table
  (`submitted → accepted → executing → …`) driven only by kernel receipts and
  target-issued cancel decisions; nothing infers success.
- **Journal and replay.** Every transaction — operations, generated
  facts/messages/receipts, enqueues, dirty/timer changes, invocation intents
  and returns — is one atomic journal record. Replay validates and reapplies
  complete records without running engines, evaluators, RNG or clocks; any
  valid prefix reconstructs full state, including pending work.
- **Lean/full provenance.** `provenance="lean"` (the default) records, per
  committed item, the invocation that produced it, with its read/native cuts,
  grants and dispatch identities. `provenance="full"` retains the complete
  ordered per-reference cause vectors. The choice changes audit detail only —
  never engine-visible state, seals or WAL-before-publication semantics.

## Scope

Out of scope by design: physics and unit/frame conversion, ontology
content, process or container deployment, distributed
watermark protocols, optimistic rollback and numerical solvers. Deferred
capabilities are listed explicitly in `docs/DESIGN.md` section 13; none is a
hidden conformance requirement.
