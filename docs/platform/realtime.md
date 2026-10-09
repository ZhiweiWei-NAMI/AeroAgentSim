# Real-time ingress

A `real_time` engine can run through `Simulation`, `RunSession`, the CLI and the
HTTP service. Declare its source clock mapping and ingress policy in the additive
`aeroagentsim.scenario/v1` engine entry:

```yaml
clock_mappings:
- {mapping_id: canonical, clock_id: canonical}
- {mapping_id: sensor-ns, clock_id: sensor-clock, p: 1, q: 1, offset_ns: 0, rounding: exact}
engines:
  sensor:
    plugin: telemetry
    config: {entity: sensor-1, field: aas.telemetry.value, command: aas.telemetry.observe}
    ingress:
      mapping_id: sensor-ns
      initial_watermark_ns: 0
      lateness: reject
      timeout_s: 10
```

The engine's `ingress` object requires all four keys. Its `mapping_id` selects a
pinned `clock_mappings` entry, including that entry's `clock_id`. Missing policy,
unknown mapping, invalid values and conflicting policies identify
`engines.<id>.ingress` in validation errors. Every engine with a `real_time`
partition needs its own declaration, even when another engine already declares
one. Input targets must belong to the selected engine.

| Key | Meaning |
| --- | --- |
| `mapping_id` | Source-clock conversion used for this engine's submissions. |
| `initial_watermark_ns` | Nonnegative closed-prefix watermark at run creation. |
| `lateness` | Kernel vocabulary: `reject` or `delay`. |
| `timeout_s` | Positive finite bound on a kernel watermark wait; expiration records `WATERMARK_TIMEOUT` and faults the run. |

A watermark asserts that no further on-time input at or before that canonical
nanosecond will arrive. It advances monotonically. A source timestamp alone
cannot close a prefix. The producer must submit its inputs **before** advancing
the watermark. `run_until(N)` waits until the watermark covers each prospective
boundary, then processes due input and seals it. This also applies to DES or
fixed-step engines sharing a run with a live policy.

The current kernel has **one run-wide policy and watermark**, so all declared
engines must share `initial_watermark_ns`, `lateness` and `timeout_s`. Engines may
use different pinned source mappings. A watermark call closes the prefix for
all of them; an upstream aggregator must establish that closure across sources.
Independent per-engine watermarks are a kernel gap, and are rejected as conflicting
scenario policies. The kernel has no automatic allowed-lateness interval in ns;
`timeout_s` bounds waiting, not source lateness. No platform timestamp or wall
clock is substituted for a producer's closure assertion.

Source stamps use the exact rational conversion
`offset_ns + (p/q) * (numerator/denominator)`. Supported rounding is `exact`,
`floor`, `ceil`, or `nearest_ties_even`; the identity `canonical` mapping remains
required alongside external mappings. Clock mismatch and nonintegral `exact`
conversion fail explicitly.

An input is late when its mapped source time is at or before the watermark or
seal. `reject` writes a rejection with code `LATE_INGRESS`, without a command ID.
`delay` reserves at the next admissible boundary after the closed prefix and
records the displacement. For example, source time `50000000` after closure at
`100000000` is delayed to `100000001`. Requested command activation can move
later as needed. The original stamp is retained; telemetry facts use that stamp
as acquisition time.

## Submit through the worker

`POST /v1/runs` creates the ordinary run. For a live run, use initial watermark 0
and remove the example's authored `bindings.commands`. Then call
`POST /v1/runs/{id}/ingress`:

```json
{
  "engine": "sensor",
  "schema": "aas.telemetry.observe",
  "target": "sensor",
  "at_ns": 50000000,
  "payload": {"value": 24.0},
  "source_stamp": {
    "clock_id": "sensor-clock",
    "numerator": 50000000,
    "denominator": 1,
    "mapping_id": "sensor-ns"
  },
  "idempotency_key": "sensor-sample-1"
}
```

Nanoseconds and stamp coordinates require actual JSON integers; a schema of
`number` requires a floating-point payload such as `24.0`. The optional key
identifies the original request **and stamp**. An identical keyed duplicate
returns the original receipt and does not create another reservation; different
content produces `IDEMPOTENCY_CONFLICT`. Rejected inputs are recorded on each
attempt because the kernel does not retain rejection keys.

HTTP 200 returns `aeroagentsim.ingress-receipt/v1`, with `disposition`
(`accepted`, `delayed`, `rejected`), `journal_index`, `mapped_ns`, `command_id`,
`boundary_ns`, `activation_ns`, `delay_ns` and `code`. Rejected receipts have no
command ID or reservation coordinates (`null`) and carry `LATE_INGRESS`.
These are admission receipts; the target's execution receipts appear separately
in committed records. Each admission receipt is derived from an acknowledged
kernel WAL append, and duplicate receipts cite the original index.

After submitting the prefix, call `POST /v1/runs/{id}/watermark` with
`{"watermark_ns": 100000000}`. It returns
`aeroagentsim.watermark-receipt/v1` only after the worker advances the kernel
watermark. Advance to `300000000` after the remaining inputs to finish this
example. A concurrent worker input thread can admit commands and close prefixes
while the coordinator waits. Viewer reads never choose watermarks or seals.

Invalid input returns HTTP 422 with the actual error code/message. An exited or
terminal worker returns HTTP 409. If the connection closes before acknowledgment,
inspect the journal to determine whether the input committed. Existing
pause/resume/stop controls remain boundary controls: pause or stop requested
during a watermark wait takes effect after that boundary settles, or the actual
watermark timeout faults the run. Ingress remains available while paused.

The SDK exposes the same path:

```python
from aerokernel import CommandRequest, Instant, Stamp
from aeroagentsim.platform import RunSession

# scenario has initial watermark 0 and no authored bootstrap commands
with RunSession(scenario, directory) as session:
    session.start()
    receipt = session.submit_live(
        "sensor",
        CommandRequest("aas.telemetry.observe", "sensor", Instant(50_000_000),
                       {"value": 24.0}, "sensor-sample-1"),
        Stamp("sensor-clock", 50_000_000, 1, "sensor-ns"),
    )
    session.advance_watermark(100_000_000)
    session.run_until(100_000_000)
    session.advance_watermark(300_000_000)
    session.run()
```

Use `Simulation.submit_live` and `Simulation.advance_watermark` for a run without
artifact ownership. Direct `kernel.submit` is the existing offline command path;
stamped live observations go through `submit_live` to enforce admission policy.

## Example, pacing and replay

[realtime-ingress.yaml](../../scenarios/realtime-ingress.yaml) uses the generic
`telemetry` plugin and a small pinned registry snapshot compiled from the real
AeroGraph observation descriptors. It models one stream value without domain
vehicle assumptions. Its CLI form has an explicitly authored offline command
(value `21.5` at `100000000 ns`) and an initial watermark covering its entire
`300000000 ns` interval. Those are declared example inputs, not live measurements.

```sh
aeroagentsim run scenarios/realtime-ingress.yaml --out /tmp/aas-q/q1/cli-runs
aeroagentsim replay /tmp/aas-q/q1/cli-runs/<run-directory>
```

`run.pacing: realtime` delays the host's advance loop against elapsed wall time;
`fast` runs it without those delays. Pacing supplies neither source timestamps
nor watermark closure and cannot satisfy a `real_time` engine's ingress contract.
The kernel also supports optional `IngressPolicy.speed_ratio`; this schema does
not expose that separate kernel pacing option.

The journal header pins the full scenario (including each engine binding),
resolved clock mappings and kernel policy. Accepted/delayed reservations,
rejections, original stamps, watermark advances and seals are journaled. Offline
`aeroagentsim replay` reconstructs this committed prefix without plugin factories,
engine calls, source reads, RNG or live clock calls. Missing closure produces a
real timeout/fault; replay preserves that prefix rather than synthesizing success.

`tests/platform/test_realtime.py` covers both late dispositions, concurrent
watermark waiting, idempotency, source/target validation, engine-path validation,
HTTP worker admission and journal/state equivalence with live calls disabled.
