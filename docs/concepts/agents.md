# Agents

Agent decisions are plugin engines that turn authorized observations into
typed, authority-checked proposals. They are DES partitions like any other
engine: inference blocks simulated time, outputs are journaled, and replay
makes zero model calls.

## Decision plugins

Two model-driven plugins are built in:

- **`decision`** (`src/aeroagentsim/agents/`): a synchronous decision
  partition driven by trigger points, with strict JSON-schema function tools.
- **`langgraph`** (`src/aeroagentsim/agents/langgraph.py`): an optional
  multi-role LangGraph decision plugin (`pip install -e '.[agents]'`). The
  scenario declares a trusted Python factory returning an uncompiled
  `StateGraph`; nodes receive the observation and trigger, and must make
  model calls only through the journaled client (`complete` / `ask_json`).

Both record decision evidence in the kernel WAL: observations, model calls,
validation, output proposals and terminal outcomes (with command receipts
recorded separately). Committed history is journaled evidence, not a private log.
The read-only [agent execution view](../reference/agent-execution.md) normalises
function-tool, structured-JSON and explicitly scripted evidence, including
partial outcomes and missing historical metadata, without rewriting WALs.

## Grants: what an agent can see and do

Agent access is explicit and minimal:

- `grants.fields` — exact `{entity, field}` selections; nothing else enters
  observations or read tools.
- `grants.relations` — explicit relation descriptor IDs.
- `grants.events` — explicit `{schema, topic}` pairs allowed in or out.
- `grants.commands` — `{schema, target}` pairs that become the only function
  tools and emission declarations, with optional per-member payload
  allow-lists.

Observations carry `valid_at` and the issued `known_at` cut, per-field
producer, acquisition clock/mapping, validity, availability and fact version.
An absent fact appears as `status: absent` with no invented value.

Tools are strict JSON Schema functions compiled from the pinned registry.
Malformed calls produce typed `TOOL_REJECTED` records fed back to the model
within the retry budget; they never become zero estimates or defaults.
Commands are proposals: the kernel assigns command IDs and publishes real
submitted/accepted/executing/terminal receipts.

## Model providers and the live-llm profile

`OpenAIProvider.from_config` accepts a named operator-resolved `profile`
(`src/aeroagentsim/agents/provider.py`); scenarios cannot supply provider URLs,
model settings or credential-variable selectors. A total wall deadline covers DNS,
connection, headers and body; expiry records `WALL_TIMEOUT` and a late reply
cannot publish. The transport provider does not retry; graph `ask_json` can
retry `HTTP_STATUS`/`TRANSPORT` errors and JSON rejections within its existing
retry budget, retaining each attempt. Missing usage is an explicit failure.

Two configuration layers exist:

1. `decision` selects `provider: {profile: default}` or another operator profile;
   the LangGraph wrapper uses `provider: {mode: live, profile: default}`, or an
   explicit `{mode: stub, model: ..., responses: ...}` script. The default profile reads
   `AAS_LLM_BASE_URL`, `AAS_LLM_MODEL` and `AAS_LLM_API_KEY`. Other profiles
   come from `AEROAGENTSIM_PROVIDER_PROFILES` or `AEROAGENTSIM_PROVIDER_CONFIG`;
   see the [provider guide](../guides/agents.md#the-provider). The model must be
   provided explicitly; the default base URL is `http://127.0.0.1:8788/v1`.
2. The shipped demo's `live-llm` profile (`src/aeroagentsim/services/demo.py`)
   instead requires the environment variables `AEROAGENTSIM_LLM_BASE_URL`,
   `AEROAGENTSIM_LLM_MODEL` and `AEROAGENTSIM_LLM_API_KEY_ENV`, where the
   last one names the environment variable that actually carries the
   credential:

```bash
export AEROAGENTSIM_LLM_BASE_URL=http://127.0.0.1:8788/v1
export AEROAGENTSIM_LLM_MODEL=your-model
export AEROAGENTSIM_LLM_API_KEY_ENV=MY_PROVIDER_KEY   # variable holding the key
export MY_PROVIDER_KEY="replace-with-your-key"
aeroagentsim demo traffic-accident --profile live-llm
```

Recorded/stub decision mode is an explicit, selected alternative — never an
automatic fallback when a live model is unavailable. The resolved model and
credential-free provider metadata are recorded; raw prompts, responses and
plugin diagnostics can still contain sensitive supplied text.

For low-altitude cooperation, invalid/failed agent replies record
`lowalt.incident.decision_failed`; responder incident state is `decision_failed`.
An unsuccessful handoff is `failed` when a helper decision failed, otherwise
`declined`. Prescribed mode then selects its next allowed ordered action;
agent mode asks the model anew against current state. These are separate
recorded outcomes, not a prescribed-success fallback.
[Failure and re-decision guide](../guides/agents.md#cooperation-failure-and-re-decision).

低空协作的模型失败记录为决策失败，不自动转成预设成功。没有有效交接接受者时，
协助者决策失败对应 `failed`，否则为 `declined`；之后预设模式选择后续允许动作，
智能体模式根据当前状态重新询问模型，保留原失败记录。

## Budgets, ordering and replay

Each decision has wall, round, call, token and prompt budgets; concurrent
calls use stable identities and results are ordered by identity, not network
completion. Two latency profiles are explicit:

- **`offline_blocking`** (implemented): the coordinator stays at the
  scheduled simulation instant while the call runs within the wall deadline.
- **Online delayed-reply integration** (named streams returning results at
  recorded availability) is not implemented in the current decision engine.

State replay executes no decision engines or providers: committed effects
are reconstructed from the journal. Separately, `replay_graph` re-imports and
reexecutes the installed graph factory against recorded replies; its client
makes no provider calls, but trusted factory Python is not sandboxed or
verified against all recorded pins. Live inference is not byte-identical replay.
See the [execution reference](../reference/agent-execution.md#replay-and-audit-limits)
for audit durability and replay limitations.

## Where to go next

- [Agents guide](../guides/agents.md) — configuring a decision engine.
- [Behaviours](behaviours.md) — rule-driven orchestration alongside agents.
- [Runs](runs.md) — inspecting agent records in the console.
