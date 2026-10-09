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

Both record every phase in the kernel WAL — observation, prompt, response,
validation, command, failure, finished and receipt — so the full decision
history is ordinary journaled records, never a private log.

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

`OpenAIProvider.from_config` accepts `base_url`, `model`, `api_key_env`
(`src/aeroagentsim/agents/provider.py`). A total wall deadline covers DNS,
connection, headers and body; expiry records `WALL_TIMEOUT` and a late reply
cannot publish. Transport failures are never auto-retried into success, and
missing usage is an explicit failure.

Two configuration layers exist:

1. Scenario engines (`decision`, `langgraph`) read `AAS_LLM_BASE_URL`,
   `AAS_LLM_MODEL` and `AAS_LLM_API_KEY` for the credential by default; an explicit scenario
   setting takes precedence. The model must be provided explicitly — there is
   no implicit default model; the base URL defaults to
   `http://127.0.0.1:8788/v1`.
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
automatic fallback when a live model is unavailable. Responses and prompts
are recorded without credentials; the resolved model name is recorded.

## Budgets, ordering and replay

Each decision has wall, round, call, token and prompt budgets; concurrent
calls use stable identities and results are ordered by identity, not network
completion. Two latency profiles are explicit:

- **`offline_blocking`** (implemented): the coordinator stays at the
  scheduled simulation instant while the call runs within the wall deadline.
- **Online delayed-reply integration** (named streams returning results at
  recorded availability) is not implemented in the current decision engine.

Replay executes no decision engines or providers: model calls are
reconstructed from recorded responses. `replay_graph` can additionally
reexecute a recorded graph against its recorded replies for verification —
it performs no kernel writes and no model I/O.

## Where to go next

- [Agents guide](../guides/agents.md) — configuring a decision engine.
- [Behaviours](behaviours.md) — rule-driven orchestration alongside agents.
- [Runs](runs.md) — inspecting agent records in the console.
