# Agent execution evidence (v1)

`aeroagentsim.agents.execution` provides a read-side
`aeroagentsim.agent-execution/v1` view with `AGENT_EXECUTION_VERSION = 1`.
It adapts existing `aas.agent.record` and `aas.langgraph.record` events; it
neither replaces their schemas nor rewrites historical WALs. Importing the
adapter does not import engines, LangGraph factories, providers or services.
The functions only inspect supplied records: callers remain responsible for
selecting an admitted committed prefix and grouping it by decision.

## Modes: protocol is not proof of live inference

| `mode` | Meaning |
|---|---|
| `function_tools` | The decision engine's recorded function-tool prompt, or a graph request using function tools. A reply can contain multiple tool calls. |
| `structured_json` | A graph's structured-response API, including `ask_json` and `allocation: llm` JSON rank selection. JSON ranks are not function-tool calls. |
| `scripted` | Explicitly recorded scripted/stub provider metadata, not a live-model fallback. Scripts can return either tool calls or JSON. |
| `None` | The supplied evidence does not establish a mode, or recorded mode values are unsupported/conflicting. |

Classification uses recorded `execution_mode`, provider mode (`stub`,
`scripted`, `live`) and model identifiers, never reply wording or apparent
quality. A `scripted` label requires supporting provider mode/model evidence;
a contradictory or unsupported label alone is not sufficient.
For legacy records without an explicit provider mode, the exact
historical model IDs `stub`, `scripted` and
`lowalt-scripted-allocation/v1` identify authored scripts. An explicit `live`
provider mode takes precedence over these model-name defaults. Arbitrary
models containing words like "stub" are **not** classified as scripted.
Otherwise legacy decision prompts establish `function_tools`; graph requests
with a recorded tools list establish their API mode. Missing fields remain
unknown, not evidence of a live provider.

Graph call-level modes take precedence over the invocation's default API
mode. If actual calls have incompatible explicit modes, the execution's mode
is `None`; individual `calls[].mode` values remain available. A graph with no
model calls can still record its configured mode. A mode label alone proves
neither network access nor decision quality; a test monkeypatch does not
retroactively change recorded provider metadata.

The default `allocation: llm` configuration explicitly selects a finite
`mode: stub` script, model `lowalt-scripted-allocation/v1`. Its records use
the LangGraph adapter; it is not a third execution engine or live inference.

## Adapting records

```python
from aeroagentsim.agents.execution import (
    from_agent_record,
    from_langgraph_records,
)

# Ordered original message envelopes for ONE decision, collected at a known cut.
execution = from_agent_record(decision_messages)
graph_execution = from_langgraph_records(graph_messages)
```

Both functions accept raw phase payloads (`decision_id`, `phase`, `code`,
`data_json`), decoded phase rows (`phase`, `data`), or full message envelopes
(`id`, `source`, `schemaId`, `payload`). `from_agent_record` also accepts a
single phase payload; that produces a partial *view*, not a claim of a
terminal execution outcome. An optional envelope
`{decision_id, engine_id, invocation_id, records: [...]}` supplies retained
identity for phase-only rows. It is accepted by both functions.

Supply phases in recorded order, not network-completion order. The graph
producer already orders calls by `(superstep, node, caller key, attempt)`.
Mixed decision IDs and wrong source schemas raise `ValueError`. Non-mapping
records raise `TypeError`. Identical message IDs are counted once; conflicting
payloads for the same ID reject. Equal payloads with **different** IDs are
not deduplicated. Identity-less rows retain their supplied multiplicity.

Use original `aas.langgraph.record` messages, not the projector's
`aas.agent.record` console aliases. Aliases carry `source_schema` and
`source_message_id`; these adapters reject them rather than double-counting
calls, inventing commands, or silently merging two evidence classes.

## View fields

`AgentExecution` and its nested record dataclasses are frozen. Raw JSON
mappings and lists are detached copies, not recursively immutable values.
Unknown/missing scalar metadata is `None`; collections contain only retained
records. No provider object, environment lookup or credential resolution is
needed to build the view.

| Field | Evidence semantics |
|---|---|
| `version`, `engine` | View version 1; source engine family `decision` or `langgraph`. Not a WAL or scenario-plugin API version. |
| `engine_id`, `decision_id`, `invocation_id` | Retained identities. Engine ID can also be recovered from the engines' recorded `<partition>/<sequence>` decision ID. Kernel invocation ID stays `None` unless supplied explicitly. |
| `observation`, `observation_cut` | Recorded observation and its `known_at` cut. Integer nanosecond strings and microsteps are preserved. This is the observed state cut, not an ACK/final-results cursor. |
| `mode`, `provider_mode`, `provider_profile`, `model` | Execution API classification, recorded stub/live mode, operator profile **alias**, and recorded model. Missing historical aliases are not resolved to today's settings. |
| `transport_transformations` | Recorded credential-free policy identifiers, e.g. `opencode.ai:strip-function.strict:v1`. `[]` is an explicitly empty descriptor; `None` means unavailable/unsupported. |
| `graph_pin` | Recorded factory and LangGraph version where available. Retaining a pin is not checking that installed code matches it. |
| `calls` | Model-client attempts, including requests that fail preflight. Not the number of functions in a reply, commands submitted, or proof of provider billing. |
| `outputs` | Actual recorded `command`/graph `output` phases, preserving kind, raw data and evidence `message_id`. Rejected graph result candidates remain in `failure`, not successful outputs. |
| `receipts` | Separately retained decision receipt phases with their evidence IDs. Graph output records do not manufacture command receipts. |
| `outcome`, `outcome_code`, `failure` | Derived terminal decision outcome, recorded typed failure code (an extensible string), and retained failure diagnostics. See below. |
| `message_ids` | Original evidence message IDs supplied with the records. Bare payloads cannot provide IDs that were not retained. |

Each `AgentCall` retains `identity` (graph), `round` (decision), `attempt`
(zero-based where recorded, including legacy graph identity element 4),
`request`, `response`, `usage`, `latency_s`, `valid`, `failure_code`, `error`,
`validations` and `message_ids`. It also exposes the available per-call mode
and provider metadata. A decision round is **not** invented as a graph
attempt number. Missing decision latency remains `None`; simulation timestamps
are not converted into wall latency.

A call's validations preserve tool IDs, validity, typed rejection codes,
diagnostics and source record IDs. Any rejected tool makes that call's
`valid` false; true requires validation of **all** tool calls in its reply.
An incomplete prefix remains unknown. Graph `ask_json` success now records
explicit validation; historical successful calls without it retain
`valid=None`. Transport failure is not a JSON validation verdict.

An output's `message_id` is the ID of its **evidence event**, not an inferred
ID of the resulting command/event. Its raw data retains any recorded
`call_id`, schema, target/topic, payload or fact identity. Actual command IDs
and status belong to separately recorded receipts. Do not correlate by payload
equality or promote a proposal to a completion receipt.

### Terminal outcomes

- `succeeded`: a `finished` phase was recorded. Earlier rejected attempts may
  have been corrected within budget. This means the agent invocation finished,
  **not** that a flight, allocation, rescue mission or physical action finished.
- `partial`: a terminal `failure` followed retained command/output proposal
  records. In particular, `decision` submits valid calls one by one; an invalid
  later call does not undo an earlier submitted proposal.
- `failed`: a terminal `failure` with no retained output proposals. LangGraph
  validates the entire output set before publication, so rejected graph results
  do not become partial published output.
- `None`: no terminal phase is available in the supplied prefix. An unfinished
  view is not automatically `failed` or `partial`.

`outcome_code` preserves the terminal failure code, e.g. `RETRY_BUDGET`.
Per-call `failure_code` separately preserves a transport/validation failure,
e.g. `TOOL_REJECTED`. Decision retry/round exhaustion or a later round's
preflight failure is not relabelled as an error on an earlier completed call.
An admission ACK is not completion, and later command receipts do not rewrite
the inference outcome.

## Additive recording and retries

New decision prompts and graph observations/model-call rows record
`execution_mode`, `provider_mode`, `provider_profile` and
`transport_transformations` alongside existing model evidence. Only the
actually selected resolver contributes a profile alias; injected providers do
not inherit an overridden configuration's stub/live classification or alias.
Optional metadata is not an endpoint, request header, API key, key-file path,
credential environment selector or dump of provider internals.

Existing request members retain their meanings: the view's decision request
is the prompt record (including its metadata), while a graph request is the
recorded `request` subobject. The transformation descriptor
from `OpenAIProvider.transport_transformations` records policy, not a literal
per-call wire transcript; a listed transformation need not affect every
request. For example, stripping `function.strict` has no effect when no tools
are sent. Do not claim wire-exact evidence from this descriptor.

`ask_json` keeps its existing retry policy: invalid JSON/validator rejection
can regenerate within `max_retries`; only `HTTP_STATUS` and `TRANSPORT`
provider errors are eligible for automatic retry within that budget. Other
provider errors propagate. Each attempted client call already has a distinct
identity; new rows additionally expose `attempt` and nullable `failure_code`,
plus JSON `validation`. Responses, errors and rejections remain with their
own attempt. This may repeat generation/billing; it is not automatic command
resubmission. The transport provider itself does not retry.

## Request budgets and optional recent history

Decision `budget.history_window` is optional. When present it must be a positive
integer: only the most recently submitted N commands' action states and latest
receipts enter the model observation. For example, `history_window: 16` retains
up to 16 actions and their receipts, in their original order. It is not a time
window, a receipt-status filter, or a claim that omitted commands completed.
Fields, relations and current inbox events are unchanged. Each recorded `prompt`
includes the effective `history_window` when configured; absent means the
historical unbounded prompt, with no added metadata. The recorded `observation`,
all command/receipt phases and the engine's retained history remain complete;
this is prompt projection, not WAL pruning or an in-memory retention limit.
This key applies to the decision engine, not graph budgets.

Decision prompt-byte admission measures the JSON request envelope, including
tools, with the provider's serialization settings. The same detached request
contents are passed to the provider. The OpenCode strict-stripping policy is
applied before accounting/sending; logical recorded tool definitions remain
unchanged. Graph prompt-byte admission continues to measure the full request.

Graph calls reserve the remaining aggregate completion-token allowance before
provider dispatch and cap `max_tokens` at that allowance. The current per-call
cap is also the aggregate cap, so parallel nodes queue model admission until the
in-flight call settles instead of overbooking the same tokens. Waiting does not
extend the original wall deadline; all preflight checks run before each dispatch.
Exhausted allowance fails with `TOKEN_BUDGET` without calling the provider.
Valid reported usage settles a reservation, even if the response subsequently
fails validation. An `HTTP_STATUS` rejection with no completion releases its
reservation so the configured retries can proceed: the request was rejected rather
than returning an inference result. A scripted fault before any response likewise
performs no inference and releases its reservation, without inventing usage evidence.
In contrast, a timeout after sending, an ambiguous `TRANSPORT` failure, cancellation,
or a completion with missing usage may have spent tokens. Unknown usage in those
cases retains the reservation rather than assuming zero spend, so another attempt
may fail token admission. Post-call checks remain in place; these limits are not
measured cost savings or a guarantee that a provider obeys its cap.

New graph call rows record `token_allowance`, the pre-dispatch admission input.
Conformance replay uses that recorded allowance, not replay completion timing,
and still compares the entire request including `max_tokens`. Legacy rows
without it retain the historical full per-call cap, so old request equality and
post-call aggregate failures remain reproducible without provider access. Kernel
state replay remains engine-free and does not reapply new prompt limits.

## Replay and audit limits

- **State replay** reconstructs committed kernel state from admitted WAL
  evidence, without running decision engines, graph factories or providers.
  These view adapters likewise make no provider calls and perform no writes.
- **Graph conformance replay** (`replay_graph`) re-imports the installed graph
  factory and executes it against recorded client responses. It is not
  engine-free state replay or a Python sandbox. The current helper does not
  establish that factory code/dependencies match recorded pins before import;
  callers must pin and trust that environment. Client replay forbids provider
  calls, but arbitrary factory Python can have unrelated side effects.
- **Live inference is not byte-identical replay.** A shared seed or model name
  does not make repeated provider generations, wall latency or full live-run
  bytes identical. Retained transcripts support replay, not deterministic new
  generation or authenticated historical provenance.
- **Commit durability is callback-level.** Model-call rows are buffered until
  the engine returns and the kernel commits the batch. A crash after a provider
  response but before acknowledgement can lose that response. This change is
  not a crash-surviving per-attempt audit ledger or exactly-once external I/O.
- Credential-free metadata does not redact successful prompts/responses or
  plugin diagnostics. Raw evidence can contain sensitive supplied text. Apply
  access controls; identify redacted exports as derived artifacts rather than
  silently changing source evidence.

### Verification status

The accompanying tests are designed to generate host-side WAL records using
scripted or monkeypatched providers and adapt them into the view. They are
not live-model, provider-wire or native-simulator acceptance evidence. In the generate-only
implementation pass, tests, type/lint checks, builds, live inference, graph
replay and native execution were **not run**; those gates remain unverified.
No console/export consumer migration or native capability is claimed here.

See also [Agents](../concepts/agents.md) and the
[provider guide](../guides/agents.md#the-provider).
