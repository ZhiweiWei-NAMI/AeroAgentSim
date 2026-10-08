# Decision engine and model gateway (P6)

The `decision` plugin is an offline kernel DES partition. Its synchronous decision
invocation keeps the coordinator at the assigned simulation instant while a
stdlib OpenAI-compatible provider waits for the model. Other state engines do not
advance during that call. The provider enforces a total wall deadline across DNS,
connection, headers and body using an isolated daemon I/O worker; expiry shuts down
the socket and records `WALL_TIMEOUT`. A late reply cannot publish commands.
Transport failures are never retried automatically or turned into success.

## Scenario contract

See [llm-dispatch.yaml](../../scenarios/agents/llm-dispatch.yaml). Each decision
engine config declares:

- `instruction`: public system instruction.
- `grants.fields`: exact `{entity, field}` selections from scenario entities.
  Dependencies are narrowed to those entity IDs/types. No other field is included
  in an observation or exposed by a read tool.
- `grants.relations`: explicit relation descriptor IDs. Effective edges include
  their endpoint identities, validity and actual availability.
- `grants.events`: explicit `{schema, topic}` pairs. Only those schemas from the
  actual dispatched inbox enter observations; topic subscription is separate from
  event schema identity.
- `grants.commands`: `{schema, target, allowed?}`. Optional `allowed` constrains
  named payload members to explicitly listed values. Only granted schemas and
  destinations become function tools and partition emission declarations.
- `points`: one trigger per entry: absolute positive `timer_ns`, granted `event`
  schema, own-action `receipt` status, or equality `predicate` with
  `{entity, field, value}`. Predicate entry requires observed false→true; first
  true and absence do not count. Points in one reaction coalesce into one decision.
- `budget`: positive `wall_timeout_s` (maximum 300), `max_rounds`, `max_calls`,
  `max_tokens` (completion tokens), `max_prompt_bytes`, and nonnegative
  `max_retries`. Budgets apply per decision. All returned calls, including invalid
  ones, count; retries share the wall and token budget.

Observations identify both `valid_at` and the issued `known_at` cut. Every known
field includes value, producer, acquisition clock/mapping, validity, availability
and fact version. An absent fact has `status: absent` and no invented value.
Actions/receipts are limited to this partition's issued commands. Available typed
references are restricted to declared scenario entities and checked against their
committed lifecycle before command proposal. Sessions currently use fixed entity
and grant scopes; dynamic session rebinding is not implemented.

Tools are strict JSON Schema functions compiled from the registry's portable
record, scalar, array/vector/matrix, typed-ref, tagged-union and schema-ref forms.
All properties are required in the function schema, with null placeholders for
native optional fields; only those placeholders are removed during translation.
JSON numbers are translated to the kernel's strict float slots. Extra properties,
duplicate JSON keys, nonfinite numbers, ungranted functions/arguments, and missing
or blank `decision_summary` are rejected. Every tool includes the required public
summary. `noop` ends a decision; `wait` adds a positive partition timer without
advancing time during inference.

Malformed calls produce `TOOL_REJECTED` records and corresponding tool results
fed back to the model within the retry budget. Valid calls in a mixed response
remain real proposals and count toward budgets. A proposal is never described as
completed: the kernel assigns its command ID and publishes actual submitted,
accepted, executing and terminal receipts. Commands cite observed fact versions,
dispatch/timer causes and decision records through `EngineContext` local causes.

## Provider and recording

`OpenAIProvider.from_config` accepts `base_url`, `model`, and `api_key_env`.
Omitted settings use `AAS_LLM_BASE_URL`, `AAS_LLM_MODEL`, and `AAS_LLM_API_KEY`;
defaults are `http://127.0.0.1:8788/v1` and `glm-5.3-flashx`, with no required key.
An explicitly configured scenario setting takes precedence over its environment
setting. Only the resolved model name is recorded; credentials are not included
in prompts or records. Responses are limited to 4 MiB. Missing token usage is an
explicit `USAGE_MISSING` failure. The `Provider` protocol also supports custom
providers; an Anthropic-specific implementation is not included.

The scenario registers `aas.agent.record`: required `decision_id`, an enumerated
`phase`, enumerated nullable failure/rejection `code`, and `data_json` containing
the complete recorded blob. Phases are observation, prompt, response, validation,
command, failure, finished and receipt. Prompts include exact messages, strict
tools, model and output budget; responses retain actual assistant message, raw
reply and provider usage. Validations retain original tool calls and rejection
reasons. Command records preserve the resulting typed payload and target;
subsequent receipt records correlate kernel command IDs to decision/call IDs.
These events are ordinary authority-checked kernel outputs in the WAL, alongside
the actual commands and receipts, without an independent mutable log.

`aeroagentsim replay <run-directory>` uses the kernel journal replay path and
executes neither decision engines nor providers. The integration test also saves
`/mnt/data2/weizhiwei/aeroagentsim/artifacts/agents/glm-60s/journal.jsonl` for the measured run (subsequent tests
create uniquely named run directories); the saved directory includes the exact
scenario/registry snapshots and feed index. Run
`aeroagentsim replay /mnt/data2/weizhiwei/aeroagentsim/artifacts/agents/glm-60s`, or serve with
`aeroagentsim serve --out /mnt/data2/weizhiwei/aeroagentsim/artifacts/agents --frontend tests/agents/frontend-build`
and open `/agents/glm-60s?mode=replay`. AgentConsole at
`/agents/<runId>?api=...&mode=live|replay` consumes the same committed feed as the
viewer, retains multiple calls and full receipt histories, and links observed
entities to the viewer inspector. Model/API failures remain visible.

## Real measured run

The local `/v1/models` endpoint listed `glm-5.3-flashx`. The first 60-second
simulated experiment completed the following measurements, preserved in
[llm-metrics.json](../../tests/agents/llm-metrics.json) and
[llm-gate.txt](../../tests/agents/llm-gate.txt):

| Dispatcher | Orders completed | Mean completion time from t=0 | Wall time |
|---|---:|---:|---:|
| Local GLM | 10 | 9.60 s | 237.58 s |
| P1 deterministic workflow | 10 | 10.66 s | 21.35 s |

The GLM run made 7 decisions/model requests and 22 valid tool calls, with 0 invalid
calls. One decision timed out and is recorded as failure; completed orders were
established by physical arrival and independent business acceptance. Replay made
0 model calls, reproduced the same final cut and was complete (42.84 s wall time).
This is one authored simulation run, not real aircraft telemetry or evidence of
policy superiority. Both scenarios start with pending orders, but the P1 policy
uses fixed dispatch timers while the model selects assignments at decision points.

Tool-schema validity is separate from workflow acceptance: the model proposed
20 assignment commands; 10 were accepted and completed, and 10 received actual
business `rejected` receipts for unavailable orders/resources. These rejections
are retained in subsequent observations and the console, and are not counted as
malformed tool calls. The two remaining valid calls were explicit wait/noop tools.

The example `agent-assignment` plugin is a configurable command-driven workflow
under `agents/dispatch.py`: it checks pending order, available resource and the
exact authored destination, submits the physical command, and waits for arrival
and business acceptance. Its types, fields, command schemas and topics are all
scenario inputs. It supports no cancellation and declares that explicitly in the
command descriptor. No drone identifiers occur in the generic decision/provider
or kernel API.

## Validation

Use `/mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python`. This interpreter
lacked the platform's already-declared PyYAML dependency, so validation installed
PyYAML into the owned `tests/agents/.deps` directory without modifying the kernel
or another workspace. Set `PYTHONPATH=tests/agents/.deps:src` when using that
interpreter; a normal platform installation supplies PyYAML itself.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests/agents/.deps:src ../aerokernel/.venv/bin/python -m pytest tests/agents -m 'not llm' -p no:cacheprovider --basetemp=tests/agents/.pytest-tmp
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests/agents/.deps:src ../aerokernel/.venv/bin/python -m pytest tests/agents -m llm -s -p no:cacheprovider --basetemp=tests/agents/.pytest-tmp-llm
MYPYPATH=../aerokernel ../aerokernel/.venv/bin/python -m mypy --strict --follow-imports=silent src/aeroagentsim/agents tests/agents/*.py
../aerokernel/.venv/bin/python -m ruff check src/aeroagentsim/agents tests/agents/*.py
npm --prefix frontend test -- --cache=false
npm --prefix frontend test -- --config ../tests/agents/vitest.config.ts
npm --prefix frontend run typecheck
npm --prefix frontend run build -- --outDir ../tests/agents/frontend-build --emptyOutDir
```

Strict mypy checks all owned Python modules/tests; imported platform modules are
followed silently because they are outside P6 ownership. Console tests live in the
owned tests tree and resolve the existing frontend dependencies via a local
symlink. Runtime records/builds/cache/dependency and GLM session artifacts are
excluded from staging by `tests/agents/.gitignore`; measured gate summaries remain
reviewable. Two concurrent DSH GLM sessions used `workbuddy/glm-5.3-flash` with
131072 output budget and no effort setting. Their provider/frontend code was
reviewed and corrected before integration; returned claims were checked against
actual tests.

Measured gates: 20 unit tests and 1 live `llm` integration passed; ruff passed;
strict mypy passed for all 10 owned Python files; 59 existing frontend tests and
2 console regressions passed; frontend typecheck and production build passed.
The real saved feed contains 129 agent records across all eight phases, and the
CLI replay reached cut 4269 at 60,000,000,000 ns with `incomplete: false`.

## Platform changes

- `platform/plugins.py`: two additive builtins, `decision` and `agent-assignment`.
- `services/app.py`: additive `/agents/{path:path}` SPA route for the optional
  frontend host. No execution/replay service logic changed.
- `frontend/src/App.js`: lazy agent console route.
- `frontend/src/pages/RunsPage.tsx`: honor entity selection links and expose the
  per-run agent console. Existing feed contracts already carry typed events,
  commands and receipts; no new feed API is required.
