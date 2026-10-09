# Authored kernel examples

Run the editable package after installing the editable package:

```sh
python examples/two_engine_toy.py
```

`two_engine_toy.py` preserves the DESIGN §12 trace: fixed-step movement,
DES order processing, and an ordinary reactive zone transition. Arrival and
business acceptance have separate receipts. This authored model retains its
ordinary transition schema for compatibility.

Engines subclass `aerokernel.sdk.ContextEngine`, implement `bootstrap`, `step`
and `on_inputs`, and use `@handles("declared.command")` for typed command
handlers. `EngineContext` produces ordinary proposals; kernel bindings still
control every field, lifecycle operation, route, dispatch and action transition.
`SimpleEngine` remains available in the SDK for low-level proposal authors.

The example explicitly selects canonical acquisition at computation time and
open validity for its local model fields. An external adapter must supply its
actual source stamp/validity instead. `ctx.get` preserves tagged absence;
`ctx.set` never invents a value or an implicit hold policy. Retain dispatched
`Command` handles on the engine for later receipts; child command IDs arrive
through routed submitted notifications. `ctx.rng(name)` exposes the invoking
partition's declared stream only.

Run the additional contracts with:

```sh
.venv/bin/python -m examples.lockstep_adapter_skeleton
.venv/bin/python -m examples.sampled_relations
```

The lockstep skeleton translates actual native results and applies commands after
the completed boundary. Its FakeLockstepSimulator is explicitly authored test data;
replace that object with the platform's PX4/SUMO/ns-3 transport. Their container
protocols remain separate from `aerokernel.rpc`. Unexpected early stops fault;
explicit buffering must finish the grant before releasing retained outputs.

The sampled record/collection example binds a directed relation and activated
minimum, then emits one optional entered event over the observed interval `(0,3]`.
Its evaluator reads the settled declared state; unresolved observations remain
unresolved. A first true would emit nothing. Neither viewer cadence nor repeated
run limits sample the model.

For out-of-process SDK engines, the host creates
`RemoteEngine(process.stdout, process.stdin, timeouts=...)`; the child calls
`serve_engine(engine, sys.stdin.buffer, sys.stdout.buffer)` and writes diagnostics
only to stderr. Configure the same ResourceBudget at both ends. A transport fault
ends the run; do not retry that stateful request.
