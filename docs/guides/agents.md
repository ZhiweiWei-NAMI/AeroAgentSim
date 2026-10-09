# Guide: Agents (decision engines and live LLMs)

AeroAgentSim runs model-backed decisions inside the shared DES kernel: the
coordinator pauses other state engines while a decision runs, and every
prompt, response, validation and command is recorded in the journal. This page
covers the shipped engines, the provider configuration and the live-LLM demo
invocation. Concepts: [concepts/agents](../concepts/agents.md).

## The shipped decision engines

| Plugin | Module | Use |
| --- | --- | --- |
| `decision` | [../../src/aeroagentsim/agents/decision.py](../../src/aeroagentsim/agents/decision.py) | Generic strict tool-calling agent with grants, trigger points and budgets |
| `langgraph` | [../../src/aeroagentsim/agents/langgraph.py](../../src/aeroagentsim/agents/langgraph.py) | Runs an authored LangGraph `StateGraph` factory |
| `agent-assignment` | [../../src/aeroagentsim/agents/dispatch.py](../../src/aeroagentsim/agents/dispatch.py) | Configurable deterministic dispatch workflow (no model) |
| `traffic_decisions` | [../../src/aeroagentsim/packs/traffic_accident/decisions.py](../../src/aeroagentsim/packs/traffic_accident/decisions.py) | Demo decisions; `mode: stub` reads an authored fixture, no model |

`decision` and `langgraph` are registered as builtins in
[../../src/aeroagentsim/platform/plugins.py](../../src/aeroagentsim/platform/plugins.py);
the demo accident graph factory is
[../../scenarios/agents/accident_graph.py](../../scenarios/agents/accident_graph.py)
(`build_graph(client, options)` returning an uncompiled `StateGraph`).

## The provider

`OpenAIProvider.from_config` ([../../src/aeroagentsim/agents/provider.py](../../src/aeroagentsim/agents/provider.py))
accepts exactly `base_url`, `model` and `api_key_env`. Omitted settings fall
back to `AAS_LLM_BASE_URL`, `AAS_LLM_MODEL` and `AAS_LLM_API_KEY`; defaults
are `http://127.0.0.1:8788/v1` and `glm-5.3-flashx` with no required key. An
explicit scenario setting wins over the environment. Only the resolved model
name is recorded — credentials never enter prompts or records. There is no
automatic retry: transport failures and `WALL_TIMEOUT` are recorded typed
failures, and a late reply cannot mutate simulation state.

## Grant-based scoping

A decision engine sees only what it is granted. Example from
[../../scenarios/agents/llm-dispatch.yaml](../../scenarios/agents/llm-dispatch.yaml)
(a complete runnable scenario):

```yaml
zz_decision:
  plugin: decision
  config:
    instruction: Dispatch authored submitted orders to available resources. ...
    grants:
      fields:
      - {entity: uav-1, field: aas.agent.available}
      - {entity: order-1, field: aas.p1.order_state}
      relations: []
      events: []
      commands:
      - schema: aas.agent.assign
        target: operations
        allowed:
          entity: [order-1, order-2]
          resource: [uav-1, uav-2]
    points:
    - {timer_ns: 1000000000}
    - {timer_ns: 8000000000}
    budget:
      wall_timeout_s: 60        # <= 300, per decision
      max_rounds: 3
      max_calls: 10
      max_tokens: 8192
      max_retries: 2
      max_prompt_bytes: 262144
    provider:
      base_url: http://127.0.0.1:8788/v1
      model: glm-5.3-flashx
```

Trigger `points` accept absolute `timer_ns`, granted `event` schemas,
own-action `receipt` statuses, or equality `predicate` entries (false→true
only). Tools are strict JSON Schemas compiled from the registry; malformed
calls produce `TOOL_REJECTED` records fed back within the retry budget. Every
proposed command becomes a real kernel command with actual submitted,
accepted, executing and terminal receipts — a proposal is never described as
completed.

The `langgraph` engine requires explicit graph budgets (`wall_timeout_s` ≤
300, `sim_deadline_ns`, `max_calls`, `max_tokens`, `max_retries`,
`max_prompt_bytes`, `recursion_limit`) and grants with
`fields/relations/commands/events/facts`.

## Live-LLM demo invocation

The shipped demo's `live-llm` profile swaps the scripted decisions for the
LangGraph path. From the worktree root:

```sh
pip install -e ./aerokernel -e '.[agents]'   # agents extra: langgraph + openai

export AEROAGENTSIM_LLM_BASE_URL="https://your-endpoint.example/v1"
export AEROAGENTSIM_LLM_MODEL="your-model"
export AEROAGENTSIM_LLM_API_KEY_ENV="MY_KEY_VAR"   # name of the credential var
export MY_KEY_VAR="sk-..."                          # the populated credential

aeroagentsim demo traffic-accident --profile live-llm --headless --out runs/demo
```

All three `AEROAGENTSIM_LLM_*` variables are required (checked in
[../../src/aeroagentsim/services/demo.py](../../src/aeroagentsim/services/demo.py));
the third names the environment variable that actually holds the key. Without
them, or with the `agents` extra missing, the command fails fast. The
default `--profile kinematic` runs fully offline with authored stub decisions
from
[../../scenarios/demos/traffic-accident/fixtures/decisions.json](../../scenarios/demos/traffic-accident/fixtures/decisions.json);
`--profile sumo`/`px4` are configuration contracts, not runnable replacements.

## Recording and replay

`aeroagentsim replay <run-directory>` uses kernel journal replay and executes
neither decision engines nor providers — the committed feed reconstructs all
records, receipts and final state with zero model calls. Records carry the
exact prompts, tools, model and output budget, raw replies, provider usage,
validation reasons and correlated command receipts. Missing token usage is an
explicit `USAGE_MISSING` failure, never a silent zero.

## Limits

- Session scopes are fixed per scenario (granted entity IDs and fields);
  dynamic session rebinding is not implemented.
- No Anthropic-specific provider is shipped; implement the `Provider`
  protocol to add one.
- Decision wall timeout applies across DNS, connection, headers and body; a
  timed-out decision is recorded as a failure, not skipped silently.
- Model/API failures stay visible in the feed; they are never turned into
  synthetic successful decisions.

See also: [external events](external-events.md) for triggering agents from
live inputs, [behaviours](behaviours.md) for consuming proposals.
