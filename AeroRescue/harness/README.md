# AeroRescue Harness

Cordis composes the application; the official DSH agent loop owns both rule and
model tool calling. Python owns requested execution and factual state, not a
second autonomous decision loop. This directory is separate from platform code.

## Install and start

Install Node.js and the AeroRescue Python package/dependencies, then `npm ci`.

```
node launch.mjs --run-id rescue-001 --uav-id u1 --role uav \
  --data-dir /data --socket /run/aas-uav/agent.sock --mode rule
```

Optional: `--config PATH`, `--python PATH`. Each UAV and the centre run their own
instance and private data directory. A real existing companion endpoint is
required for native flight/radio execution. The launcher does not start a fake
simulator, download weights or read a credential file.

`--mode model` uses the existing DSH pi-ai OpenAI-compatible adapter and explicit
`AAS_LLM_BASE_URL` and `AAS_LLM_MODEL` environment configuration, plus
`AAS_LLM_API_KEY` when the endpoint/provider requires it. The adapter transport
may itself require authentication material; keyless live compatibility is not
claimed without a real endpoint check.
Missing model configuration fails before startup; it never falls back to rule.
No live API call has been performed as part of this integration work.

The launcher and execution bridge set `ORT_DISABLE_TELEMETRY=1` before Python
starts. ONNX Runtime's existing API opt-out remains in place, but its official
Privacy.md explains that the API alone may follow a native initialization event.
This pre-initialization setting disables the 1DS uploader, events and device ID.
The installed ONNX Runtime 1.31.0 wheel includes this documented setting.

Each private data directory retains DSH sessions under `dsh/` and actual host
notifications in `logs/harness-events.jsonl`, with stderr alongside. The mode
record distinguishes the local rule adapter from a configured network provider;
configuration alone does not establish a successful model request, and Python's
`model_calls` does not count DSH calls. Existing sessions use official DSH resume;
persisted tool results are delivered with their original task revision and local
notification receipts, without resubmitting physical operations.

## Decision ownership and installed tools

- The launcher runs the supported `dsh --profile sdk-minimal` entrypoint.
- `rescue.patch.yml` adds the rescue plugin and persona; local persistent Bash
  remains available for onboard scripts. No new permission framework is added.
- `skills.mjs` discovers `../skills/*/skill.json` and `SKILL.md`. There is no copied
  second roster. All 21 business tools have required `request_json`, with exact
  documented input fields in their schema descriptions and manuals in context.
- `rescue_control` exposes explicit local task/centre actions. Python checks role,
  current revision and received evidence through existing execution logic.
- Rule mode's adapter reads the pure proposal produced from actual local state
  and emits a standard DSH tool-call. It does not execute the proposal itself.
- Model mode uses DSH's existing provider/agent-loop, with the same tool registry.
- `ctx.systemPrompt.context` supplies the local factual snapshot as durable model
  context. No shared fleet state or world-truth mount is introduced.

## Frozen execution protocol P1

`bridge.mjs` starts `python -m aerorescue.companion.runtime --execution-only` with
ordinary config/run/UAV/socket arguments. There is no new JSON-RPC execution server.

The existing directory `DATA_DIR/tasks/tool-requests/` is the command queue:

```
<request_id>.queued.json
{request_id, tool OR control, request, result_key?, continue_on_unavailable?,
 task_id?, task_revision?}
```

Queue writes are atomic. The execution worker retains the same
`<request_id>.result.json` results and emits newline-delimited facts on stdout:
`ready`, `context.changed`, `tool.completed`, `tool.failed`. Completion carries
`request_id`, `operation_id`, `result`, and simulation time. Before emitting a
fact it refreshes the local snapshots:

- `harness-context.json`: `run_id`, `uav_id`, `role`, `sim_ns`,
  `decision_event_version`, `task` (or null), own state/results/inbox, and optional
  centre-local state.
- `rule-proposal.json`: a pure `{tool OR control, request, result_key,
  continue_on_unavailable?}` proposal, or no action. No Python dispatch follows
  merely from writing this snapshot.

DSH returns `pending` with the queue request ID, becomes idle for empty
continuation steps, and wakes through `agent.followup` on actual completion or
local/radio event. New input remains admissible while an operation is pending,
including cancellation. It does not use `inject` as a fake wake-up and does not
poll the model while waiting. Existing Python operation ownership handles
conflicting concurrent commands.

Controls: accept-task, apply-radio-control, withdraw-resources, centre-solve,
centre-publish, centre-notice, centre-cancel, centre-finish, wait-until. A wait is
for actual `sim_ns`, not wall-clock guessing. Publishing refers to a solver's
actual plan ID.

## Verification status

Production commands are `npm start -- [arguments]` and `npm run help`.
Development probe sources and traces are retained separately as local execution
evidence, not shipped entrypoints. The probe is never loaded by the production
profile and does not prove flight, perception or rescue completion.

The production roster was loaded from all 21 real manifests and Node syntax and
profile composition were checked. A local integration run used the existing BGE
cache and a registered copy of this README to exercise the real `read-evidence`
handler successfully. Starting DSH after the result existed delivered its actual
completion; a second official session resume neither repeated that notification
nor re-executed the tool (one tool-trace record before and after). This was an
explicit local queue request, not autonomous task selection or sensor evidence.
With no native endpoint or received task, the production rule proposal correctly
remains empty; autonomous rescue-task execution is not yet verified.
Native end-to-end flight/radio validation
still requires the actual execution environment and service. Do not substitute
the development probe for that acceptance result.

Node 24.19.0 and `@deepseek-ai/dsh@0.2.0-rc.2` were used during development. The
normal package lock records dependencies for reproduction, not a new software
hash/version admission requirement.

## Official references

- https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md
- https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent/README.md
- https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/bundle/sdk-minimal/README.md
- https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cookbook/adding-a-tool.md
