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
accepts only a named `profile`. URLs, model settings and credential environment
variable names come from operator configuration, never from submitted scenarios.
The `default` profile uses `AAS_LLM_BASE_URL`, `AAS_LLM_MODEL` and
`AAS_LLM_API_KEY`. The base URL defaults to `http://127.0.0.1:8788/v1`; the
model must be set explicitly through `AAS_LLM_MODEL` or a named profile —
there is no implicit default model. No API key is required by default. To
configure named profiles, set
`AEROAGENTSIM_PROVIDER_PROFILES` to a JSON mapping, or point
`AEROAGENTSIM_PROVIDER_CONFIG` at a server-side JSON file with that mapping:

```json
{"research": {"base_url": "https://your-endpoint.example/v1", "model": "your-model", "api_key_env": "MY_KEY_VAR"}}
```

The environment mapping overrides file entries of the same name. Populate
`MY_KEY_VAR` on the server, then select `provider: {profile: research}` in the
scenario. Unknown profiles and scenario-supplied URLs or credential selectors
are rejected. The console's live-provider form also accepts a profile name.
Provider failures record a short safe message and HTTP status where available;
upstream error bodies and request headers are discarded. There is no
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
      profile: default
```

Trigger `points` accept absolute `timer_ns`, granted `event` schemas,
own-action `receipt` statuses, or equality `predicate` entries (false→true
only). Tools are strict JSON Schemas compiled from the registry; malformed
calls produce `TOOL_REJECTED` records fed back within the retry budget. Every
proposed command becomes a real kernel command with actual submitted,
accepted, executing and terminal receipts — a proposal is never described as
completed.

The `langgraph` engine requires explicit graph budgets (`wall_timeout_s` ≤
300, exactly one of `sim_timeout_ns` or `sim_deadline_ns`, `max_calls`, `max_tokens`, `max_retries`,
`max_prompt_bytes`, `recursion_limit`) and grants with
`fields/relations/commands/events/facts`.
`sim_timeout_ns` is a duration measured from each decision's trigger time;
`sim_deadline_ns` is an absolute simulated timestamp. The shipped live demo
uses a one-second duration, so decisions after the first simulated second
remain eligible. Each invocation records its resolved deadline for replay.

## Live-LLM demo invocation

The shipped demo's `live-llm` profile swaps the scripted decisions for the
LangGraph path. From the repository root:

```sh
pip install -e ./aerokernel -e '.[agents]'   # agents extra: langgraph + openai

export AEROAGENTSIM_LLM_BASE_URL="https://your-endpoint.example/v1"
export AEROAGENTSIM_LLM_MODEL="your-model"
export AEROAGENTSIM_LLM_API_KEY_ENV="MY_KEY_VAR"   # name of the credential var
export MY_KEY_VAR="sk-..."                          # the populated credential

aeroagentsim demo traffic-accident --profile live-llm --headless --out runs/demo
```

The base URL and model are required (checked in
[../../src/aeroagentsim/services/demo.py](../../src/aeroagentsim/services/demo.py));
`AEROAGENTSIM_LLM_API_KEY_ENV` names the credential variable when the endpoint requires authentication. These
operator settings define the built-in `live-llm` provider profile; the scenario
stores only its name. Without
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

## Cooperation failure and re-decision

The low-altitude cooperation model records an invalid or failed model decision
as `lowalt.incident.decision_failed`, with incident, vehicle, role, mode and
reason. Responder failure clears its choice and sets incident state to
`decision_failed`. A failed helper offer is not an acceptance or a refusal.
After all offers resolve, an eligible acceptance can still be selected; when
handoff cannot proceed, its state/event is `failed` / `lowalt.handoff.failed`
if any helper decision failed, otherwise `declined` / `lowalt.handoff.declined`.

After that unsuccessful handoff, prescribed responder mode chooses the next
allowed action after `request_handoff` in its configured order. Agent mode
instead emits a fresh `lowalt.coop.decision_request` using the actual current
state and queries the model again. Another handoff is blocked. The earlier
failure remains recorded: this business-triggered re-decision is different
from automatic transport retry or prescribed takeover after model failure.

低空协作中的无效回复或模型失败记录为 `lowalt.incident.decision_failed`，
响应者事件状态变为 `decision_failed`。协助者决策失败不是接受或拒绝；其他协助者
仍可提供有效接受。交接不能继续时，有协助者失败则记录 `lowalt.handoff.failed`
及 `failed` 状态，否则为 `declined`。此后预设响应者采用后续允许选项；智能体
响应者会根据当前状态重新请求模型决策，不再请求同一次交接。原失败记录保留，
不因重新决策被改写为成功，也不会由预设规则接管失败模型。

The generic `decision`/`langgraph` engines also journal typed failures
(transport, wall/budget limits, invalid calls, missing usage); those records
are not successful action receipts. Story explains missing expected steps
from recorded failures and refusals. Replay makes no fresh model calls.
通用决策引擎也记录类型化失败，不冒充动作成功回执；Story 根据实际记录解释
未完成步骤，回放不会重新调用模型。

See [cooperation contract](../product/assistant.md#2-simulation-capability-cooperative-incident-response-with-handoff)
and [manual authoring](blank-project-tutorial.md).

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
live inputs, [behaviours](behaviours.md) for consuming proposals, and
[HTTP API security](../reference/http-api.md#request-boundary) for local hosting
and bearer authentication on non-local deployments.
