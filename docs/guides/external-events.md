# Guide: External events and live ingress

External inputs enter a running simulation through **named ingress streams**.
Each stream pins its clock mapping, late disposition, initial closed prefix
(watermark), optional allowed lateness and optional wall-clock wait timeout.
Source code:
[../../src/aeroagentsim/platform/simulation.py](../../src/aeroagentsim/platform/simulation.py)
(`RunSession.submit_live` / `advance_watermark` /
`advance_source_progress`) and the HTTP surface in
[../../src/aeroagentsim/services/app.py](../../src/aeroagentsim/services/app.py).

## Declaring streams

```yaml
ingress_streams:
- id: operator
  engine_ids: [behaviour]
  mapping_id: canonical
  initial_watermark_ns: 0     # live: start open; offline demos may pre-close
  lateness: reject            # or delay
  timeout_s: 10               # optional wall-clock wait budget
  allowed_lateness_ns: 20000000   # optional
```

`id`, `mapping_id`, `engine_ids`, `initial_watermark_ns` and `lateness` are
required. Clock mappings are declared separately under `clock_mappings`. A
shorthand `ingress:` block on a single engine compiles to one named stream.

## Progress, closure and lateness

Two explicit source assertions exist on `Simulation` and `RunSession`:

- `advance_source_progress(stamp, stream_id=...)` — asserts source progress
  `P` and closes exactly `P - allowed_lateness_ns`. Inputs in the tail
  `(P-L, P]` remain admissible.
- `advance_watermark(ns, stream_id=...)` — asserts an inclusive **closed
  prefix** directly and subtracts nothing. Use this when your adapter
  computes `P-L` itself, to avoid subtracting twice.

At or below a closed prefix an input is late: `reject` returns a typed
`rejected` receipt with `code: LATE_INGRESS`; `delay` records a reservation at
a legal future publication boundary while preserving the original source
occurrence time. Progress and closure never regress. Input timestamps never
advance closure by themselves.

## HTTP API

```http
POST /v1/runs/{id}/ingress
Content-Type: application/json

{"stream_id":"fast-source","schema":"aas.stream.observe","target":"fast",
 "at_ns":50000000,"payload":{"value":21.5},
 "idempotency_key":"fast-first",
 "source_stamp":{"clock_id":"fast-clock","mapping_id":"fast-ns",
                 "numerator":50000000,"denominator":1}}
```

- `engine`, `stream_id`, or both are accepted (at least one required).
- The response is an `aeroagentsim.ingress-receipt/v2` object with
  `stream_id`, `disposition` (`accepted|delayed|rejected`), `journal_index`,
  `mapped_ns`, `command_id`, `boundary_ns`, `activation_ns`, `delay_ns` and
  `code`. Admission is **not** engine acceptance or execution success; check
  recorded action receipts for those.
- Idempotency is scoped to `(stream_id, key)`; resubmitting the same key
  returns the original receipt, changed content fails
  `IDEMPOTENCY_CONFLICT`.
- Valid late rejections are HTTP 200 with a typed receipt; invalid payloads,
  domains, mappings or assertions are HTTP 422; an exited worker is 409.

Assert progress separately:

```http
POST /v1/runs/{id}/watermark
{"stream_id":"fast-source","source_stamp":{"clock_id":"fast-clock",
 "mapping_id":"fast-ns","numerator":70000000,"denominator":1}}
```

`/watermark` accepts exactly one of `watermark_ns` or `source_stamp`. The
watermark receipt is `aeroagentsim.watermark-receipt/v2` and includes the
effective closed `watermark_ns`.

## Waiting for input

While a stream's prefix is open at the boundary the run needs closed, run
metadata, commit pages and SSE status events report
**`waiting_for_input`** with `waiting.stream_ids` and `waiting.at_ns`. The
wait never implies closure — do not infer a closed stream or a finished run
from it. To unblock:

1. Inject the event (if any) via `/ingress`.
2. Close the prefix via `/watermark` or `advance_source_progress`.

An explicitly authored `timeout_s` ends the run as `input_timeout` with a
closed, replayable journal — not a fault. Stop ends as `stopped` (a
`run_stop` record at the actual committed cut, no manufactured input); pause
yields at the same cut and resume continues the outstanding boundary with a
fresh wait budget. Injecting an event and advancing its stream's watermark
remain separate actions; the console's Inspect/Runs page offers an injection
shortcut that does not close the stream for you.

## The accident operator stream

The traffic-accident demo declares an `operator` stream for a behaviour
injection point (accident injection). The offline scenario pre-closes it:

```yaml
ingress_streams:
- id: operator
  engine_ids: [behaviour]
  mapping_id: canonical
  initial_watermark_ns: 90000000000   # covers the whole authored run
  lateness: reject
  timeout_s: 10.0
```

For an operator-controlled run, disable the authored accident timer and set `initial_watermark_ns: 0`. The next boundary beyond closure reports `waiting_for_input`; you then submit a typed
`aas.runtime.inject_event` at an unclosed future boundary with injection point
`accident` and payload `{incident: {...$ref...}, reason: ...}`, and
afterwards explicitly close the queued prefix through the watermark endpoint.
The run does not close the stream for you, and completion of the injection
does not mean the stream closed.

## Python API

The following API fragment assumes actual command, stamp, engine ID and output directory objects; it is not a standalone script.

```python
from aeroagentsim.platform import RunSession
from aeroagentsim.scenario import load_scenario

with RunSession(load_scenario("scenarios/realtime-streams.yaml"), out) as s:
    s.start()
    receipt = s.submit_live(engine_id, command, stamp, stream_id="fast-source")
    s.advance_source_progress(stamp, stream_id="fast-source")
    s.run_until(50_000_000)
```

A complete live experiment is
[../../scenarios/realtime-streams-demo.py](../../scenarios/realtime-streams-demo.py)
(two streams with different mappings, lateness tail admission, a rejected
boundary input, and engine-free replay).

## Notes

- `run.pacing: realtime` only delays host execution against wall time; it
  supplies neither source timestamps nor closure.
- Replay reads the journal and performs no engine, source or model calls;
  missing closure stays a recorded timeout/fault, never synthetic success.

See also: [behaviours guide](behaviours.md) (injection points),
[runs concepts](../concepts/runs.md).
