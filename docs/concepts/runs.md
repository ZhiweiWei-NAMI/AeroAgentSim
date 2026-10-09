# Runs, replay and provenance

A run executes one resolved scenario on one kernel. The CLI runs it directly; the HTTP service owns a worker process. Saved configuration and committed records keep later authoring edits separate from the run being inspected.

## Lifecycle

| Status | Meaning |
| --- | --- |
| `created` | Run accepted and worker initialization beginning |
| `running` | Advancing normally |
| `waiting_for_input` | A declared stream has not closed the next required boundary; `waiting.stream_ids` and `waiting.at_ns` identify it |
| `paused` | Simulation advancement paused at the current committed cut |
| `completed` | Declared horizon reached and run closed |
| `stopped` | Operator requested a clean stop at the actual committed cut |
| `input_timeout` | An authored stream wait budget expired |
| `faulted` | Engine or runtime error; recorded prefix retained |
| `interrupted` | Host restarted or worker failed to close within its shutdown deadline |

Without a declared `timeout_s`, an input wait can continue indefinitely. Waiting never supplies input or advances a watermark. Pause remains responsive during a wait; resume continues its boundary with a fresh wait budget. Stop closes the run promptly and preserves its recorded prefix.

## Input and controls

Simulation pause freezes advancement. Viewer playback pause freezes only the displayed timeline. Profile, model or ownership changes create a new run; there is no writer hot swap during execution.

Live input admission and command execution are separate. `/ingress` validates and admits an input with a typed receipt. Owner receipts and committed state then describe whether its action was accepted, executed and completed. Resubmitting an idempotency key returns its original admission; conflicting content is rejected.

A stream watermark explicitly closes a prefix. Source progress closes `P - allowed_lateness_ns`; a direct canonical watermark already denotes the closed prefix. Timestamps on input alone do not close it. See [External events](../guides/external-events.md) and [HTTP API](../reference/http-api.md).

## Saved files

```text
runs/<run-id>/
  manifest.json          run identity, status, waiting/error metadata
  scenario.json          resolved scenario configuration
  registry.snapshot.json source registry snapshot
  runtime.registry.json  registry including scenario overlays
  journal.jsonl          committed runtime records
  index.json             paging index
  behaviour.ir.json      compiled packages, when configured
  artifacts/             camera outputs, when configured
```

The kernel seals time instants after their due work and relevant stream progress have settled. This is time coordination: a blocked source cannot be bypassed by inventing closure. Committed safe progress remains inspectable while a boundary is pending.

## Replay

```bash
aeroagentsim replay <run-directory>
```

Replay reconstructs the recorded prefix without running engines, cameras, model providers or native simulators. It preserves recorded decisions rather than asking a model to decide again. Replaying an interrupted prefix does not resume execution. The CLI reports its cut and whether the journal is incomplete.

Serve the run directory's parent with `aeroagentsim serve --out runs --frontend frontend/dist`, then open the run in Inspect to seek its graph, poses, events and receipts on one timeline.

## Provenance levels

`lean` is the default. It retains committed outcomes, semantic events and the information required for replay while reducing detailed per-output read records. `full` adds read details for diagnosing engine interactions. Neither mode changes ownership, time coordination or validation.

Choose `provenance: full` in the scenario, or pass `--provenance full` to `run` or `demo`. A caller override is saved with the effective run configuration. Use lean for ordinary studies and full when the extra detail answers a specific diagnostic question.

See [CLI](../reference/cli.md) and [Views](views.md).
