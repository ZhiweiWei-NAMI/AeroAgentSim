# Authored kernel examples

Run the editable package with the workspace interpreter:

```sh
.venv/bin/python examples/two_engine_toy.py
```

`two_engine_toy.py` preserves the DESIGN §12 M1 trace: fixed-step movement,
DES order processing, and an ordinary reactive zone transition. Arrival and
business acceptance have separate receipts. This is authored model content,
not telemetry or the deferred sampled `entered` profile.

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
