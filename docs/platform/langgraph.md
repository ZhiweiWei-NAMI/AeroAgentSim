# LangGraph journaled decision plugin

`langgraph` is an optional, domain-neutral kernel decision partition. It consumes
actual typed message deliveries, runs a configured multi-role graph at that
simulation instant, and submits authority-checked commands, events or owned-field
facts. It works directly as an engine or behind a rule/event-chain engine that
emits a trigger. It implements the offline profile in D1's proposed runtime
design: inference holds simulated time; model wall latency is recorded separately.
Online delayed-result integration and checkpoint resume are outside this plugin.

Install with `pip install '.[langgraph]'`. R4 installed **LangGraph 1.2.14** in
`/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv` using
`python -m ensurepip` (pip was initially absent), then
`python -m pip install 'langgraph>=1.0,<2'`. The extra declares
`langgraph>=1.2.14,<2`. Importing the ordinary engine catalog does not import
LangGraph; the distribution also exposes the `aeroagentsim.engines` entry point.

## Factory and message contract

See [accident-graph.json](../../scenarios/agents/accident-graph.json) for a complete,
small, packaged-snapshot scenario. Its positions/tasks and scripted replies are
explicit **authored test fixtures**, not traffic telemetry or substituted live
responses. It needs no AeroGraph source checkout.

```python
from typing import TypedDict
from langgraph.graph import StateGraph, START, END

class State(TypedDict):
    observation: dict
    trigger: dict
    outputs: list

def make_graph(client, options):
    async def choose(state):
        def validate(reply):
            if set(reply) != {"text"} or type(reply["text"]) is not str:
                raise ValueError("text required")
        reply = await client.ask_json("choose", "Return JSON text", state["trigger"], validate)
        return {"outputs": [{"kind": "event", "schema": options["schema"],
                             "topic": options["topic"], "payload": reply}]}
    graph = StateGraph(State)
    graph.add_node("choose", choose)
    graph.add_edge(START, "choose")
    graph.add_edge("choose", END)
    return graph
```

Configure `factory: my_package.graphs:make_graph` and an explicit `options` object.
The factory receives a `JournalClient` and a JSON copy of options, and returns an
**uncompiled StateGraph**. Nodes receive `observation` and `trigger`; the latter
contains the triggering message's actual `id`, `schema`, `payload` and `at_ns`.
The observation uses the existing grant-limited field/relation/event representation,
including absence, fact versions, acquisition, validity and knowledge cuts.

Factories are trusted Python plugins. They must perform model calls **only** via
`await client.complete(key, messages, tools)` or
`await client.ask_json(key, system_prompt, observation, validator)`. The client
journals every attempt and validates usage; `ask_json` additionally validates
strict JSON and feeds public typed rejections into bounded retries. A factory has
no kernel context or publication capability. Python imports are not sandboxed:
this contract does not prevent deliberately written code from creating its own
network client. Factory code must also avoid other unrecorded I/O, wall clocks,
unseeded randomness, background state and completion-order reducers. Validators
and graph reducers must be deterministic. LangGraph itself uses supersteps and
requires deterministic reducers ([official reference](https://reference.langchain.com/python/langgraph/graph/state)).

All config members are explicit:

| Member | Contract |
| --- | --- |
| `triggers` | Nonempty list of event `{schema, topic}` or command `{schema, target}`; command target must equal this engine ID. Topics form the subscription set; schemas filter the resulting inbox. Kernel Delivery does not expose a topic label, so these are union subscriptions/schema filters, not pair-specific dispatch rules. |
| `grants.fields` | Exact `{entity, field}` observation selections. |
| `grants.relations` | Relation descriptor IDs included in observations. |
| `grants.events` | Permitted output `{schema, topic}` pairs. |
| `grants.commands` | Permitted output `{schema, target}` pairs. |
| `grants.facts` | Exact `{entity, field}` pairs owned by this partition in the scenario binding manifest. |
| `budget` | `wall_timeout_s` (finite, positive, at most 300), absolute `sim_deadline_ns`, positive `max_calls`, aggregate completion `max_tokens`, `max_prompt_bytes`, `recursion_limit`, and nonnegative `max_retries`. |
| `provider` | Explicit `mode: live` with existing `base_url/model/api_key_env` settings, or `mode: stub`, `model`, and named `responses` lists. |

The graph returns a JSON state with an `outputs` list. Each output is exactly one
of these closed forms:

```json
{"kind":"event","schema":"package.event","topic":"results","payload":{}}
{"kind":"command","schema":"package.command","target":"owner","payload":{}}
{"kind":"fact","entity":"entity-id","field":"package.field","value":42}
```

All outputs are checked together before publication: closed shape, grant, schema,
live entity scope, field ownership, and duplicate fact writes. There is no string,
number, Boolean or missing-value coercion. Rejections emit the typed
`aas.langgraph.record` event with `code: TOOL_REJECTED`; no output from that
invocation is published. `record_descriptor()` returns the portable descriptor
for the scenario registry. The record shape matches the existing agent record:
`decision_id`, `phase`, nullable `code`, `data_json`. Its topic is
`langgraph-records`; failure/recovery chains can subscribe there.

The plugin creates/initializes only entities/fields already assigned to it by the
manifest. Facts use explicit canonical model acquisition/validity policies.
Commands remain proposals: their destination engine issues actual action receipts.
A command trigger is accepted/executed, then succeeded after publication or failed
on graph failure; its failure receipt cites the typed failure record. When the
trigger command declares a result schema, the graph must additionally return a
`receipt` payload conforming to that schema; it is validated before publishing
outputs and used as the actual success result. An event
trigger emits a failure record and has no invented command receipt.

## Budgets, ordering and replay

Each call has a stable identity `(superstep, node, key, attempt)`. Keys must be
nonempty and unique within a node/step. Parallel model I/O overlaps, while records
are emitted in identity order after graph execution. A list-edge fan-in waits for
both bid branches in the example. Runtime requests/responses, exact model and
parameters, remaining wall timeout, wall latency, usage and failures are retained
in the **kernel WAL**, without a second mutable decision log. Stub latency is
explicitly zero and its configured timeout is fixed, allowing byte-identical
scripted journals. Live latency is measured and naturally differs across runs.

Observations record the factory module source hash, LangGraph version, options,
budgets and initial state. Model records precede result publication and become
local causes. Output commands/events/facts cite the actual trigger dispatch,
observed fact/relation evidence and model-call records. All graph outputs occur
at the same simulated nanosecond; kernel microsteps retain reactive order.

The graph has a total wall deadline, and every provider request receives its
remaining time. Daemon model workers have no journal or kernel access; canceled
or late replies cannot publish. `WALL_TIMEOUT`, HTTP/transport/protocol errors,
missing usage, exhausted scripts, recursion and call/token/prompt budgets remain
explicit failures. `ask_json` retries HTTP/transport failures and invalid JSON
within the common wall deadline and `max_retries`; low-level `complete` makes one
attempt. Requests, including retries and rejected calls, count against `max_calls`.
The token allowance bounds each request and is also checked in aggregate before
publishing any output. It is not divided between parallel branches by network
completion order. The simulated deadline is checked before invoking the graph;
this offline profile cannot advance simulated time while inference runs.

Two replay paths serve different purposes:

```python
from aerokernel.journal import replay
from aeroagentsim.agents.langgraph import replay_graph

recovered_kernel = replay(journal_bytes)       # No graph/factory/provider calls.
result = replay_graph(journal_bytes, "graph/1")  # Reexecutes graph, no model I/O.
```

`replay_graph` matches every request against its recorded response/error, checks
all records were consumed, and compares the complete result. Changed source,
version, request, unused/missing response or result produces `REPLAY_MISMATCH`.
It preserves completed model-failure and rejected-output outcomes as
`{"failure": ...}`. A wall interruption before the first model boundary has no
complete graph transcript and returns `REPLAY_INCOMPLETE`; ordinary kernel replay
still reconstructs that failure and its receipt with zero calls. This is
verification reexecution, not checkpoint recovery or retrying a failed live graph.
Reexecution performs no kernel writes. Use kernel replay to verify/recover the
canonical state and WAL integrity.

Stub responses are keyed by the caller key and consumed in that key's attempt
order. Each item is the existing provider envelope:
`{message: {role: assistant, content: JSON-string}, raw: actual-or-scripted-reply,
usage: {completion_tokens: integer}}`, or an explicit
`{error: {code: string, message: string}}`. Missing/exhausted scripts fail with
`SCRIPT_EXHAUSTED`; live mode never switches to stub automatically.

## Accident compatibility example and prompt provenance

[accident_graph.py](../../scenarios/agents/accident_graph.py) implements
`vehicle_report → edge_broadcast → (uav_alpha ∥ uav_bravo) → edge_award`.
The existing generic workflow engine emits the typed incident fixture at 1 ns.
The graph computes real distance/ETA from the delivered snapshot positions and
configured speed, passes each candidate's actual current task and Boolean
interruptibility, and rejects accepting a noninterruptible task. The award must
name an eligible bid. It preserves the original discretionary model award, with
stable ID sorting for equal ETAs. It publishes an award recommendation event;
physical rerouting, task ownership, flight, capture and upload are external owners.

Prompts are verbatim from the READ-ONLY source
`/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim/aero-bench/demos/traffic_accident/runtime.py`:

- Reporter: **1044–1049**; report observation/processing: **1028–1082**.
- UAV: **1133–1136**; current task/position/ETA observations: **1108–1129**.
- Award: **1207–1209**; eligible ETA ranking/selection: **1185–1230**.
- User message prefix `实时观测：`: **1262–1275**; topology: **429–441**.

The original exception-to-false bid fallback at 1139–1146 is deliberately removed.
Provider failure is a recorded failed invocation, never a fabricated valid refusal.
The model's recommendation cannot mutate current tasks or positions directly.

Run from the worktree root (so the example factory is importable):

```bash
PYTHONPATH=src /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python \
  -m aeroagentsim.services.cli run scenarios/agents/accident-graph.json --out /tmp/aas-q/r4/stub-runs
```

The live test selects `http://127.0.0.1:8788/v1`, model `glm-5.3-flash`, uses the
same authored kernel inputs and persists actual responses/WAL outside Git. It
skips only when the socket is unreachable; HTTP errors or bad recommendations
fail the test. This is a real model run over fixture data, not a traffic experiment.

## R4 verification

Commands and measured results are listed here after the final gates. All large
journals, execution logs and two actual concurrent GLM sessions are under
`/tmp/aas-q/r4/`. No frontend or Docker changes were made.

```bash
export PYTHONPATH=src
export MYPYPATH=../aerokernel
export AEROAGENTSIM_AEROGRAPH_ROOT=/mnt/data2/weizhiwei/AeroGraph  # read-only
export AEROAGENTSIM_LLM_ARTIFACTS=/tmp/aas-q/r4/legacy-llm
PY=/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python

$PY -m pytest -q -p no:cacheprovider -m "not docker" \
  tests/platform tests/adapters tests/agents tests/packs tests/authoring
# 398 passed, 4 Docker tests deselected, 751.73s; includes both live LLM tests.
# Subsequent typed-receipt and factory-deadline additions were checked below.
$PY -m pytest -q -p no:cacheprovider -m "not llm" tests/agents/test_langgraph*.py
# Final focused suite: 21 passed, 1 live test deselected.
AAS_LANGGRAPH_LIVE_OUT=/tmp/aas-q/r4/live-final \
  $PY -m pytest -q -p no:cacheprovider -m llm tests/agents/test_langgraph_example.py
# 1 passed, 5 offline tests deselected, 11.55s.

$PY -m ruff check src/aeroagentsim/agents/langgraph*.py \
  scenarios/agents/accident_graph.py tests/agents/test_langgraph*.py \
  src/aeroagentsim/platform/plugins.py
$PY -m mypy --strict --follow-imports=silent \
  src/aeroagentsim/agents/langgraph*.py scenarios/agents/accident_graph.py \
  tests/agents/test_langgraph*.py src/aeroagentsim/platform/plugins.py
# Both clean; mypy checks all six touched Python files.
```

The final saved live run made **4 actual calls**, selected `uav.bravo`, and both
replays made **0 model calls** (provider entry points replaced with failing
sentinels). Call wall latencies: **2.743, 5.453, 3.819, 2.589 s**; the two bid calls
overlap. Evidence: `/tmp/aas-q/r4/live-final/{journal.jsonl,langgraph-metrics.json}`.
The standalone CLI fixture run and CLI WAL replay passed at cut 18 / 2 ns,
`incomplete: false`, under `/tmp/aas-q/r4/stub-runs/`.

Final focused tests also prove schema-checked command success results, failure
receipts, no publication from delayed model replies, and a wall deadline covering
factory construction/compilation as well as nodes. The full-suite and focused
logs are `/tmp/aas-q/r4/{final-regression.log,langgraph-final-test.log}`. Prompt
AST equality hashes/ranges are `/tmp/aas-q/r4/prompt-provenance.json`.

Two GLM workers were actually launched concurrently with
`workbuddy/glm-5.3-flash`, **131072 maxTokens**, no effort parameter, separate
owned scratch directories and persisted sessions. The graph draft was reviewed;
its exception-to-refusal fallback and initial missing fan-out were corrected in
the delivered implementation. The overlong fixture draft was stopped and its
scope replaced with a bounded independent review of the completed fixture/graph.
Session IDs, artifacts and review disposition are in `/tmp/aas-q/r4/`; draft
claims were not used as test evidence. No frontend checks or Docker runs were
needed. `pip check` found no broken requirements.

## Flagship LIVE decisions (R5)

The traffic-accident scenario retains scripted decisions by default. Its explicit
`profiles/live-llm.yaml` engine profile replaces `decisions` with the `langgraph`
plugin and adds a failure bridge. Run from the repository root:

```bash
export PYTHONPATH=/tmp/aas-q/kernel-head:src
export AEROAGENTSIM_AEROGRAPH_ROOT=/mnt/data2/weizhiwei/AeroGraph
export AEROAGENTSIM_CHROMIUM=$HOME/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell
/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python \
  -m aeroagentsim.services.cli run scenarios/demos/traffic-accident/scenario.yaml \
  --engine-profile scenarios/demos/traffic-accident/profiles/live-llm.yaml \
  --out /tmp/aas-q/r5/live-runs
```

The profile is applied before pinning `scenario.json`. The recorded model is
`glm-5.3-flashx`, at `http://127.0.0.1:8788/v1`; each invocation has a 120-second
wall budget, a 90-second absolute simulated deadline, and at most three bounded retries.
These calls block wall time at a fixed simulated instant, not simulated motion.

The **same** `scenarios.agents.accident_graph:build_graph` factory uses its
`proposals` mode: detection calls the reporter; the rule-authored broadcast calls
Alpha and Bravo concurrently and joins them in stable node order. The report,
bid and award prompt constants remain shared with the original example and the
demo prompt files (legacy runtime lines 1044–1049, 1133–1136,
1207–1209). Edge award explanation is optional and omitted in this profile;
committed eligibility and minimum-ETA selection author the award.

Granted observations supply actual ENU positions, stored energy in joules,
current-task references, task phase/kind/interruptibility and capture altitude.
ENU is converted to the legacy prompt's x/east, y/up, z/north coordinates.
Energy is sent as joules; no invented battery percentage is supplied. Missing
committed fields fail the decision. Reporter route selection names the authored
bypass; road ownership and the committed safe-gap rule authorize actual travel.
A syntactically valid Alpha `accept=true` remains an acceptance **proposal**. The
medical lock and eligibility predicate prevent it from interrupting delivery or
winning. The stub live-path test forces this case without changing prompts.

`traffic_decision_failures` turns journaled failures into causal
`traffic.decision.failed` events carrying the code and request id. Reporter
failures end in `failed`; collecting bid failures end in `no_candidate`. No
scripted response is substituted. All-or-nothing graph publication means a
failed parallel bid session publishes no bid proposals.

The feed retains original `aas.langgraph.record` messages and supplies explicit
`aas.agent.record` views for the existing decision panel. Views carry their
source schema/message id: observation is unwrapped, model calls become prompt
and response views, and typed event outputs become validation views. They do
not invent command receipts. The WAL remains the source of truth.

Kernel replay needs no engine/model/renderer. `replay_graph(journal_bytes, id)`
also re-executes each graph using its recorded requests/responses, checks recorded model requests and the graph result, and makes zero provider calls. See
`tests/demos/traffic_accident/test_live_decisions.py`; its `llm` test runs the
complete CLI chain and forbids both live and scripted providers during replay.
`AAS_R5_LIVE_RUN` can point that test at an explicitly supplied completed live
CLI artifact, avoiding a second full physical run during verification.

The kernel's `number` schema is strict: `alt_target_m` must be the JSON
floating-point value `70.0`. The original bid prompt remains unchanged; the
observation states this output contract. Integer `70` is a journaled validation
rejection, followed by the configured explicit retry, never silently converted.
