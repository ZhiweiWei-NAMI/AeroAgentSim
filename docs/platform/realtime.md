# Live ingress and per-stream watermarks

Kernel v0.2 binds named input streams to engine domains. Each stream pins its
clock mapping, late disposition, initial closed prefix, wait timeout and optional
allowed lateness. Policies and mappings may differ between streams. One stream
can bind several engines, and one engine can receive several streams.

```yaml
ingress_streams:
- id: fast-source
  mapping_id: fast-ns
  engine_ids: [fast]
  initial_watermark_ns: 0
  lateness: reject
  timeout_s: 10
  allowed_lateness_ns: 20000000
```

`id`, `mapping_id`, `engine_ids`, `initial_watermark_ns`, `lateness` and
`timeout_s` are required. Engine IDs must exist; the list must be nonempty and
unique. `lateness` is `reject` or `delay`; `timeout_s` is a positive finite
wall-clock wait budget. `allowed_lateness_ns`, when present, must be a nonnegative
integer: null, booleans and negative values fail at the authored path. Omission
means the kernel subtracts no allowance. Clock mappings are declared separately
in `clock_mappings`, retaining the identity `canonical` mapping.

The Q1 shorthand remains valid:

```yaml
engines:
  sensor:
    plugin: telemetry
    config: {entity: sensor-1, field: aas.telemetry.value, command: aas.telemetry.observe}
    ingress:
      mapping_id: sensor-ns
      initial_watermark_ns: 0
      lateness: reject
      timeout_s: 10
      # Optional: stream_id: shared-source
      # Optional: allowed_lateness_ns: 20000000
```

This compiles to an `IngressStream` named `sensor`, bound to that engine.
An explicit `stream_id` selects another name. Declarations sharing that name
must agree on mapping and policy; their bound engines are combined. A conflicting
shorthand reports its path and the first declaration's path. Duplicate IDs in the
top-level list fail instead of overwriting an authored declaration. Every
`real_time` engine requires at least one binding. The normalized stream tuple is
passed to `Kernel(ingress_streams=...)`.

## Admission, progress and closure

`Simulation.submit_live(engine_id, command, stamp, stream_id=...)` and the same
`RunSession` method call kernel `admit_live` and return the kernel's immutable
`IngressReceipt`. `engine_id` may be `None` when a stream is supplied; an engine
argument also validates the target against that engine's partitions. The kernel
checks the stream's recipient domain, schema, payload and pinned mapping before
admission. Invalid requests fail without a decision record.

Platform HTTP receipts use `aeroagentsim.ingress-receipt/v2`, retaining the Q1
fields and adding `stream_id`: `disposition`, `journal_index`, `mapped_ns`,
`command_id`, `boundary_ns`, `activation_ns`, `delay_ns` and `code`. They cite an
acknowledged WAL decision. Admission does not establish engine acceptance or
execution success; query the recorded action receipts for those outcomes.

`reject` records `rejected` with `code: LATE_INGRESS` and null command/delivery
coordinates. `delay` records a reservation at a legal future publication boundary
and preserves the original stamp and request. Accepted arrivals after a partial
commit can also have a later publication boundary; this preserves causality
without changing the source occurrence time. Idempotency is scoped to
`(stream_id, key)` and returns the original receipt/index for accepted, delayed
and rejected decisions. Changed keyed content fails `IDEMPOTENCY_CONFLICT`.
The platform no longer intercepts journal appends to construct receipts.

Two explicit source assertions are available on `Simulation` and `RunSession`:

- `advance_source_progress(stamp, stream_id=...)` maps asserted progress `P` and
  closes exactly `P - allowed_lateness_ns`. For `P = 70ms` and a `20ms` allowance,
  the closed prefix is `50ms`; inputs in `(50ms, 70ms]` remain admissible.
- `advance_watermark(ns, stream_id=...)` asserts an inclusive **canonical closed
  prefix** directly. It subtracts nothing. Adapters calculating `P-L` themselves
  use this API to avoid subtracting twice.

At or below a closed prefix, inputs are late. At or below the common sealed
prefix, inputs are also late. Input timestamps never advance either assertion.
Progress and closure cannot regress, including below bootstrap closure; the
kernel rejects such assertions without clamping. Equal assertions are no-ops.
`timeout_s` limits waiting in wall-clock seconds and is independent of the
lateness bound in nanoseconds.

For omitted stream IDs, submissions select the unique stream bound to the named
engine, preferring a declared `default` stream. Watermark/progress calls select a
declared `default` or the sole stream. Ambiguous or missing bindings require an
explicit ID. Explicit IDs are passed unchanged: an engine-named stream has no
implicit `default` alias. Existing single-engine Q1 calls and HTTP bodies that
omit stream IDs continue to work.

## Two-source example and CLI

[realtime-streams.yaml](../../scenarios/realtime-streams.yaml) uses two generic
`ingress-consumer` engines. They echo actual command payloads into typed execution
results and have no shared state, lifecycle authority or routes. The streams use
different mappings, dispositions and allowances. The slow clock maps ticks as
`canonical_ns = 1000 + 2 * ticks`.

This independence matters: the kernel conservatively propagates stream influence
through declared reads, routes, lifecycle authority, engine groups and cohorts.
Two `telemetry` engines with lifecycle authority and state writes share influence
even when their entity types differ. Separate IDs alone cannot make them safe to
advance independently.

The YAML labels its bootstrap commands as authored offline inputs and initially
closes both streams through the run interval, so ordinary CLI execution needs no
live source:

```sh
aeroagentsim run scenarios/realtime-streams.yaml --out /tmp/aas-q/q9/cli-runs
aeroagentsim replay /tmp/aas-q/q9/cli-runs/<run-directory>
```

The executable live experiment resets initial closures to zero and removes those
bootstrap commands, then submits explicit inputs through `RunSession`:

```sh
PYTHONPATH=src python scenarios/realtime-streams-demo.py --out /tmp/aas-q/q9/live-demo
aeroagentsim replay /tmp/aas-q/q9/live-demo
```

It observes fast execution at 50ms while slow remains pending with closure 0,
admits an input at 50ms + 1ns inside the fast lateness tail, rejects an input at
the closed 50ms endpoint, then closes both streams through 300ms. Offline replay
reconstructs exactly the committed records with factory, engine and live-clock
calls disabled. The script requires a new output directory.

Early service occurs at a selected common boundary. `run_until` returns only
after the common global seal, when all relevant streams and due work are settled.
Committed views, action receipts and the HTTP commit feed expose partial progress
while it waits. A fast island cannot skip arbitrarily beyond a blocked boundary.
This is local coordination; distributed synchronization remains out of scope.

The existing [realtime-ingress.yaml](../../scenarios/realtime-ingress.yaml)
continues to exercise the telemetry shorthand and authored offline commands.
`run.pacing: realtime` only delays host execution against wall time. It supplies
neither source timestamps nor closure and cannot satisfy an ingress contract.

## HTTP worker interface

Start `aeroagentsim serve --out /tmp/aas-q/q9/http-runs --scenario-root .`.
Create a live variant using the actual pinned scenario:

```python
import copy
import json
from pathlib import Path
from aeroagentsim.scenario import load_scenario

scenario = load_scenario(Path("scenarios/realtime-streams.yaml"))
body = copy.deepcopy(scenario.document)
body["registry"]["snapshot"] = str(scenario.base / body["registry"]["snapshot"])
body["bindings"]["commands"] = []
for stream in body["ingress_streams"]:
    stream["initial_watermark_ns"] = 0
Path("/tmp/aas-q/q9/live-streams.json").write_text(json.dumps(body))
```

POST this JSON to `/v1/runs`; use the returned run ID below. The `/ingress`
body accepts `engine`, `stream_id`, or both (at least one is required):

```http
POST /v1/runs/<id>/ingress
Content-Type: application/json

{"stream_id":"fast-source","schema":"aas.stream.observe","target":"fast","at_ns":50000000,"payload":{"value":21.5},"idempotency_key":"fast-first","source_stamp":{"clock_id":"fast-clock","mapping_id":"fast-ns","numerator":50000000,"denominator":1}}
```

Assert fast source progress separately:

```http
POST /v1/runs/<id>/watermark
Content-Type: application/json

{"stream_id":"fast-source","source_stamp":{"clock_id":"fast-clock","mapping_id":"fast-ns","numerator":70000000,"denominator":1}}
```

The `aeroagentsim.watermark-receipt/v2` response includes
`stream_id: fast-source` and `watermark_ns: 50000000`. GET
`/v1/runs/<id>/commits` to observe the fast execution receipt while slow still
blocks sealing. Submit a fresh keyed input at 50000001 to see `accepted`, and
another at 50000000 to see `rejected`/`LATE_INGRESS`. A valid late rejection is HTTP
200 with a typed receipt; invalid payloads, domains, mappings or assertions are
HTTP 422. An exited/completed worker returns HTTP 409.

Finish by POSTing `{"stream_id":"slow-source","watermark_ns":300000000}` and
`{"stream_id":"fast-source","watermark_ns":300000000}` to `/watermark`.
Alternatively, slow progress at 174999500 ticks maps to 350000000ns, retaining
its 50ms tail and closing through 300ms. `/watermark` accepts exactly one of
`watermark_ns` or `source_stamp`; supplying both or neither fails validation.

`tests/platform/test_realtime_streams_example.py` exercises this shipped scenario
through CLI and HTTP, including partial service, mapped progress, tail admission,
rejected idempotency and exact offline reconstruction. `test_realtime.py` retains
single-source reject/delay coverage and tests declaration/admission edge cases.

Runs default to lean provenance: each engine output cites its invocation, which
records delivered inputs and its read cut. Choose scenario `provenance: full`,
`Simulation(..., provenance="full")`, or CLI `run`/`demo --provenance full` for
explicit read-vector auditing. A CLI/run override is pinned in the saved scenario
copy and run manifest. Ownership, typed receipts, watermarks and time seals apply
in both modes.

Default compressed lean journals use 2.1 (`semantic_version: 4`,
`provenance: lean`); lean JSON journals use 1.4. Full keeps historical 1.2/1.3 or
compressed 2.0 encodings, including their offline/named-stream policy version.
Live headers pin complete streams, mappings and policies, followed by per-stream
decisions and progress/closure records. Historical journals replay under their
original semantics. Replay never constructs
plugins or calls engines, sources, RNG or live clocks. Missing closure produces a
recorded timeout/fault and an incomplete prefix, never synthetic success.
