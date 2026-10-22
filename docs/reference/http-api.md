# HTTP API reference

The optional REST/SSE host is defined in
[`src/aeroagentsim/services/app.py`](../../src/aeroagentsim/services/app.py),
with artifacts in
[`services/artifacts.py`](../../src/aeroagentsim/services/artifacts.py) and the
optional Studio hook in
[`src/aeroagentsim/authoring/api.py`](../../src/aeroagentsim/authoring/api.py).
Start it with `aeroagentsim serve` (requires `aeroagentsim[server]`). Run
execution uses the same worker service for scenario and template launches;
the Studio endpoints only author drafts.

## Request boundary

The server binds `127.0.0.1` by default. State-changing requests must have a
Host naming `localhost`, `127.0.0.1`, `::1`, or the host configured by
`AEROAGENTSIM_CONSOLE_URL`. When an Origin header is present, it must be a
localhost HTTP(S) origin or match that configured console origin exactly.
Missing Origin is permitted for CLI clients; an untrusted Host or Origin
returns 403. These checks apply to Studio and run controls as well as submissions.
Every POST requires `Content-Type: application/json` (415 otherwise); send
`{}` for controls without parameters. CORS permits those console origins,
GET/POST, and Content-Type, Last-Event-ID and Authorization headers.

**Non-loopback `aeroagentsim serve --host HOST` requires a nonblank
`AEROAGENTSIM_API_TOKEN`, or the explicit `--unsafe-local-lab` override.** This
includes wildcard listeners (`0.0.0.0`, `::`), remote IPs and non-localhost DNS
names; hostnames are not resolved to guess whether they are local. Literal
loopback IPs and `localhost` retain their existing optional-auth behavior.
Missing/blank remote credentials fail before app/storage creation. The unsafe
override prints a conspicuous bilingual warning to stderr and is only for an
isolated trusted lab, not a shared/public network. It does not bypass Host,
Origin, JSON or configured-token checks. Programmatic ASGI deployments must
apply the same policy at their listener; `create_app` cannot discover its bind
address. Configure `AEROAGENTSIM_CONSOLE_URL` for the actual trusted remote
origin when using a wildcard bind; the bind policy does not broaden trusted Hosts.

When the token is set, every `/v1/` API request requires
`Authorization: Bearer <token>`; missing or incorrect tokens return 401. CORS
preflight remains accessible. **The token-protected browser flow is not
supported:** the console's `fetch` and SSE clients send no `Authorization` header
and have no token setting, so a protected server cannot serve the interactive
console even though the API itself accepts bearer tokens. Use an API client for
a protected deployment, or the unauthenticated console on loopback. The unsafe
lab override is not a browser authentication feature. For example:

```sh
curl -H "Authorization: Bearer $AEROAGENTSIM_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"scenario_path":"scenarios/realtime-streams.yaml"}' \
  http://127.0.0.1:8000/v1/runs
```

Scenarios select named provider profiles; provider endpoints and credential
environment-variable names are configured only by the server operator. See
[provider configuration](../guides/agents.md#the-provider). Provider failures
retain a status code and short safe message, with no upstream error body or
request headers in the journal or commits API.

Run status values: `created`, `initialized`, `running`, `paused`,
`waiting_for_input`, `completed`, `stopped`, `faulted`, `interrupted`,
`input_timeout`. `created` and `initialized` are non-terminal; only an
`initialized` run can be started. Terminal statuses are `completed`, `stopped`,
`faulted`, `interrupted`, `input_timeout` and carry a `final_cursor`. An
`initialized` status means the worker has acknowledged the world at t = 0; it
is not a receipt for commands, a guarantee that later steps will succeed, or
evidence that a model provider, camera or native engine is ready.

## Plugin catalog

| Endpoint | Method | Semantics |
| --- | --- | --- |
| `/v1/plugins` | GET | Plugin catalog: `{api_version, scenarios, engines, algorithms, models, metrics}`. `api_version` remains `1`; `metrics` is additive. Every row has `{name, origin, distribution, status}`; scenario rows add populated `templates` and `apply_hooks`, and duplicate rows add `conflict`. |

Inventory is discovery, not readiness: a row, template or passing manifest
validation does not prove that a worker, native simulator, camera, model
provider or detailed city pack is available. `GET /v1/plugins` and
[`aeroagentsim plugins`](cli.md#aeroagentsim-plugins) report the same catalogue.

Scenario entries describe discovered builtins and installed plugins in
`aeroagentsim.scenarios`. Scenario plugins and their zero-argument data factories
are loaded to return sorted template IDs (read from `template.yaml`) and owned
hook names, preserving the existing contribution arrays. Hooks are not invoked.
Engine, algorithm, model and metric entries use metadata only; inventory never
instantiates engines or model providers. Metric rows have the same common fields
and status/conflict semantics as the other families, not reducer or metric-ID
inventories.

Rows are sorted by name; an installed name replaces the whole matching builtin.
Across all five families, duplicate installed names are listed once with
`status: "duplicate"` and `conflict` holding every declaring distribution name,
including repeated names and unknown (`null`) distributions. Other rows have
`status: "ok"`, meaning unambiguous, not validated or ready. Metadata enumeration
retains malformed names and duplicate evidence. Scenario/metric runtime admission
rejects bad names and duplicates before loading providers; engine catalogs reject
duplicates before loading factories. Algorithm/model runtime lookup retains its
[legacy family rules](extension-admission.md#family-matrix).

A duplicate or malformed scenario name triggers a section-wide metadata fallback:
all scenario `templates`/`apply_hooks` arrays are empty and builtins stay listed,
without loading any scenario plugin. These diagnostic fallback arrays mean
**uninspected**, not that the providers contribute nothing. Otherwise the arrays
contain the loaded contributions, and empty means none declared. Other invalid
scenario contracts still fail discovery rather than silently using a builtin.
See [extension admission](extension-admission.md) and
[plugin contracts](plugin-contracts.md) for the plugin APIs.

## Run execution API

| Endpoint | Method | Semantics |
| --- | --- | --- |
| `/v1/runs` | POST (201) | Start a run. Scenario mode accepts `{"scenario_path": "..."}` (resolved under the configured scenario root), `{"scenario": {...}}` or an inline scenario mapping, optionally with `studio_workspace` to base a console draft. Version mode accepts only `{"studio_workspace": "...", "version": 1, "autostart": false}`; `autostart` defaults to **false** there, so omitting it initializes without starting. With `autostart: false`, the response waits until the worker reports `initialized` or a terminal status, and the returned `status` is authoritative: **HTTP 201 does not mean initialization succeeded**. The loader validates declarations, then the single real worker initializes the engines and publishes its t=0 ACK; there is no throwaway engine initialization. Invalid declarations or a failed worker initialization yield 422 (worker failures include the run id). Startup has an operator deadline; a timeout yields 504 with an uncertain outcome, not a successful 201. Returns the run metadata. |
| `/v1/runs` | GET | List all runs with manifests (id, scenario, status, `until_ns`, optional `error` and `waiting`), additive `kpis` summaries, `knobs`, `algorithms`, `title`, and pinned `template` settings (`template_id`/`template_title` aliases) when launched from a template. |
| `/v1/runs/{id}/header` | GET | Pinned viewer-feed header (see [viewer feed](viewer-feed.md)). Adds `catalogNotice` when no AeroGraph root is configured, plus Studio scene links when applicable. |
| `/v1/runs/{id}/commits?from=N&limit=M` | GET | Page projected commits (`limit` 1–4096). Response: `{commits, next, status, waiting?, finalCursor?}`. Empty runs page from index 1. |
| `/v1/runs/{id}/stream?from=N` | GET (SSE) | Live tail. Events: `commit` (id = journal index; supports `Last-Event-ID` reconnect), `status` (on change, includes `waiting`), `end` (terminal, includes `finalCursor`). Keep-alive comments while idle. |
| `/v1/runs/{id}/{operation}` | POST | `operation` is `start`, `pause`, `resume` or `stop`. `start` is accepted only while status is `initialized` (409 otherwise, including a second start); pause/resume are rejected before Start. Stop requests a graceful halt at a sealed boundary. 409 when there is no active worker or the run is terminal. Returns `{id, requested, status}`. |
| `/v1/runs/{id}/speed` | GET | Host pacing factor: `{id, factor}`; `null` means as fast as possible. 409 when the run has no active worker. |
| `/v1/runs/{id}/speed` | POST | Body `{"factor": 0.05..1000}` or `{"factor": null}` for as fast as possible. Host pacing only; it does not change simulated outcomes. 409 without an active worker, 422 for invalid values. |

Initialize an immutable saved version and start it separately:

```sh
# Create and initialize; autostart:false waits for initialized (or terminal).
curl -H 'Content-Type: application/json' \
  -d '{"studio_workspace":"WORKSPACE_ID","version":1,"autostart":false}' \
  http://127.0.0.1:8002/v1/runs
# Start only after the returned status is "initialized".
curl -X POST -H 'Content-Type: application/json' -d '{}' \
  http://127.0.0.1:8002/v1/runs/RUN_ID/start
```

The control route takes no JSON fields, but `POST` still requires
`Content-Type: application/json` at the request boundary.

### Host lifecycle limits

The worker registry owns each run process, input channel and cache task once.
An exclusive `worker-owner.json` claim prevents re-launching the same run identity
even after its handles have been released; it is not an initialization ACK.
Completed children are joined non-blockingly and their process/pipe/task handles
are released during normal service operation, not just at shutdown. A dead worker
without a terminal publication is `interrupted`; an observed fault/timeout and
existing terminal outcomes are retained. `final_cursor` is bounded to the last
published ACK (1 for an empty unacknowledged run); visible tail bytes are never
promoted to an ACK or successful completion. Additive `host_lifecycle` metadata
records the actual exit code, `forced`, `reason` and `termination_confirmed` only
after exit is observed.

Cross-process lifecycle signals use lock-free shared bytes: only the host writes
pause/stop/start, and only the worker writes its initialization ACK flag. The
host alone writes the unlocked shared pacing value. Flag and pacing accesses
never acquire a child-owned semaphore or wait for a writer, so a child killed
mid-access cannot strand the host event loop or prevent escalation.

Operators can pass `create_app(..., worker_limits=WorkerLimits(...))`, importing
`WorkerLimits` from `aeroagentsim.services.lifecycle`, or use these `serve` flags:

| Flag | `WorkerLimits` field | Default seconds | Scope |
| --- | --- | --- | --- |
| `--startup-timeout` | `startup_timeout_s` | 60 | Real worker initialization and t=0 ACK. |
| `--input-timeout` | `input_timeout_s` | 10 | One input/progress RPC: executor-dispatch delay, serialization wait, send and complete reply together, for `/inputs`, `/ingress`, `/watermark` and capture-artifact admission. |
| `--shutdown-grace` | `shutdown_grace_s` | 10 | Cooperative stop, shared across all owned runs. |
| `--terminate-grace` | `terminate_grace_s` | 2 | Terminate-to-exit wait, shared across runs. |
| `--kill-grace` | `kill_grace_s` | 2 | Kill-to-exit/reaping wait, shared across runs. |
| `--cleanup-timeout` | `cleanup_timeout_s` | 1 | Final IPC/cache-task drain; shared at shutdown, per completed run otherwise. Also bounds the worker's final input-thread join. |

All limits must be positive finite seconds, at most Python's
`threading.TIMEOUT_MAX`; booleans/coercions are not accepted by `WorkerLimits`.
They are host wall-clock limits, **not** simulated-time limits
or the scenario's watermark `timeout_s`. A host RPC timeout does not change a run
to the kernel's `input_timeout` status.

Startup timeout returns 504 (`WORKER_STARTUP_TIMEOUT`) with the existing run id,
`outcome: "uncertain"` and `retryable: false`, and requests termination followed
by bounded kill escalation. An unexplained pre-initialization process exit
returns 500 (`WORKER_EXITED`); an observed initialization fault returns 422
(`WORKER_STARTUP_FAILED`). A process-start failure returns 503. Timeout or
termination requests are not confirmation that a process, external model call or
mission has finished. Inspect the retained run rather than automatically launching
another one.

Shutdown signals every worker before waiting. Its process-control phases share
10 + 2 + 2 seconds by default, rather than multiplying a join timeout by the number
of runs or executor batches; the final drain shares one additional second.
Uvicorn separately allows five seconds to cancel open requests/SSE before lifespan
cleanup. Experiment shutdown runs concurrently under its own
[documented policy](comparison-evidence.md#cancellation-and-actual-outcomes).
These are scheduling/process-control budgets, not hard real-time guarantees
against stalled filesystems, OS calls or unkillable tasks. An already executing
cache batch may finish after its awaiting task is cancelled; derived results
remain available through normal projection reads. Local run-worker termination
does not verify cleanup of detached plugin descendants or undo external effects.
Unconfirmed termination is logged, never labelled a confirmed terminal outcome.

### Templates and comparisons

| Endpoint | Method | Semantics |
| --- | --- | --- |
| `/v1/templates` | GET | List template metadata: id, title, summary, category, difficulty, duration, scenario, optional thumbnail, observe/try bullets, knobs and selected KPIs. |
| `/v1/templates/{id}` | GET | Fetch one template; 404 for an unknown id. |
| `/v1/templates/{id}/runs` | POST (201) | Body `{"knobs": {...}, "workspace": true?}`. Apply declared defaults and overrides, validate, and launch. Returns `{run_id, workspace_id?}`. Omit `workspace` to launch directly. |
| `/v1/templates/{id}/workspace` | POST (201) | Body `{"knobs": {...}}`. Create an editable Studio draft with copied template inputs. Returns `{workspace_id}`; fetch it through `/v1/studio/workspaces/{workspace_id}`. |
| `/v1/templates/{id}/thumbnail` | GET | Declared packaged PNG. List/detail also expose additive `thumbnail_url` when a thumbnail is declared. Missing assets return 404; paths are scoped to their template directory. |
| `/v1/templates/{id}/experiments` | POST (201) | Launch a validated full-factorial experiment: `{vary: {knob: [values]}, seeds?: 3, jobs?: 1, horizon_s?: seconds, knobs?: {base overrides}}`. Returns experiment identity, progress, run outcomes and table. |
| `/v1/experiments/{id}` | GET | Persistent experiment progress and aggregate table (`aeroagentsim.experiment/v1`). Each table row has `config_id`, applied `knobs`, and `metrics` with `{mean, std, n, unit, label, best}`. |
| `/v1/runs/{id}/kpis` | GET | `{contract: "aeroagentsim.kpi/v1", final, series}` computed from the committed journal prefix, including partial live runs. Each metric is `{value, unit, label}`. The response also carries `run_id`. |
| `/v1/runs/compare?ids=a,b,c` | GET | Align 2–20 distinct runs. Returns `{contract: "aeroagentsim.run-comparison/v1", runs, kpis, differences}`. `kpis` rows carry `{name, unit, label, values: {run_id: value}}`; `differences.knobs` and `.algorithms` carry `{name, values, present}`. |

These POSTs use the same JSON, Host/Origin and optional bearer-token boundary
as scenario submissions. Unknown knobs, wrong types (including boolean for an
integer), bounds violations and invalid choices return readable 422 errors.
Template roots and named hooks are configured by the operator, never by an
HTTP request. Workspace creation also makes the Studio routes available when
the service was started without a Studio root.

```sh
curl -H 'Content-Type: application/json' \
  -d '{"knobs":{"accident_time_s":5,"physics_hz":2},"workspace":true}' \
  http://127.0.0.1:8002/v1/templates/traffic-accident/runs
curl http://127.0.0.1:8002/v1/runs/RUN_ID/kpis
```

The run manifest pins applied knobs. Existing comparison titles, knob aliases,
status and summary fields remain available. Comparison uses
`compare_runs_at_cuts` with **one acknowledged reader snapshot per row**, captured
before projecting any row. KPIs, recorded end time/index, algorithms, embedded
`configuration` and exact `config_diff` derive from that same per-run cut; a later
append cannot extend them. This is not a globally simultaneous snapshot or a
common simulated-time horizon: `alignment` remains `recorded_run_end`.

Additive comparison fields:

| Field | Meaning |
| --- | --- |
| `basis: "fixed_cuts"` | Each run's measurement evidence uses its one selected snapshot. |
| `runs[].cut: {bytes, index}` | Acknowledged source byte length and **inclusive** record index. |
| `runs[].cursor` | Next source record index: `cut.index + 1`; not a feed-cache checkpoint. |
| `runs[].service_run_id`, `kernel_run_id`, `epoch`, `journal_identity` | Recorded source identities, unknown fields remain `null`. |
| `runs[].configuration_digest`, `registry_digest`, `engine_versions`, `provenance` | Available source pins; not a claim that every model/resource revision is known. |
| `runs[].configuration` | Exact scenario embedded in the cut's header, or `null`. |
| `runs[].projection` | KPI contract and `mode: "current_code_recomputation"`; unknown historical metric definition/revision pins remain `null`. |
| `runs[].status_basis: "host_observation"` | Existing `status` is separately observed host state, **not** state inferred from a WAL cut. The same qualifier accompanies `recorded_ends` and KPI evidence statuses. |
| `kpis[].compatibility`, `kpis[].evidence[id]` | Declared metric compatibility and source cut/cursor, run identities, definition/revision, run-level `n`, and sample exclusions. Known identity conflicts return 422; incomplete identity is `unknown`. |

`recorded_ends[id].commit_index` matches that row's `cut.index`; its `at_ns` comes
from the same last record. Existing `results_cursor` / `results_final` are
independently observed projection/host state, not the source cursor/finality of
these KPI values. An ACK, `run_stop`, terminal status or accepted input is not
proof of mission completion. `include_kpis=false` still selects fixed cuts and
retains `config_diff` and recorded ends, but omits the KPI table (including its
compatibility evidence), each row's `kpis` and the knob/algorithm `differences`
table. Missing/unreadable ACK evidence or a header-only run returns 409 rather
than comparing an inferred latest tail.

Missing measurements remain `null`; count metrics record zero when the selected
prefix has no corresponding events. Current template defaults do not replace
recorded measurements/configuration. For Python comparison and compatibility
details see [comparison evidence](comparison-evidence.md#fixed-cut-python-comparison).
KPI details and business-event narratives are described in
[the viewer feed](viewer-feed.md).

### Status vs. receipts

The manifest `status` is worker state; it is *not* a per-command receipt.
Command outcomes appear as `receipts` entries in projected commits
(`commandId`, `status`, optional `result`), and the client is expected to
correlate those against the command messages in the same feed. A `waiting`
object `{stream_ids, at_ns}` on `waiting_for_input`/`input_timeout` names the
streams and the simulated boundary the kernel was waiting for.

### Live ingress and watermarks

| Endpoint | Method | Semantics |
| --- | --- | --- |
| `/v1/runs/{id}/inputs` | GET | Actual source watermarks and host state (`aeroagentsim.input-progress/v1`); terminal runs read retained progress or replay older journals without submitting input. Active-worker reads use the input RPC deadline. |
| `/v1/runs/{id}/ingress` | POST | Submit typed live input after worker-side validation against the pinned injection manifest (see below). 409 if the worker is gone; 422 with a kernel error code for rejected payloads. An acknowledgement timeout is 504/uncertain, not a rejection. Returns an admission receipt. |
| `/v1/runs/{id}/watermark` | POST | Advance stream progress: exactly one of `watermark_ns` (integer) or `source_stamp` (`clock_id, mapping_id, numerator, denominator`), optional `stream_id`. Returns a `aeroagentsim.watermark-receipt/v2` with the effective watermark. |

Input RPC failures add `{id, code, error, outcome, retryable: false}` under the
HTTP error's `detail`. A deadline after a send attempt returns 504
(`WORKER_INPUT_TIMEOUT`, `outcome: "uncertain"`); channel loss after an attempt
returns 409 (`WORKER_INPUT_CLOSED`, also uncertain). Neither is an admission
rejection, an execution receipt or permission to automatically retry. A blocked
prior exchange can yield 504 (`WORKER_INPUT_BUSY`, `outcome: "not_submitted"`) for
a queued request that was never sent. A known timeout before any send is also
explicitly `not_submitted`. If the outer host deadline expires before the RPC
result is observed, the response conservatively stays `uncertain`: it does not
claim to know whether sending began. Expired executor-queued calls cannot submit
later. The service never resubmits an ambiguous operation, including
watermarks or artifact-admission calls. It keeps the channel serialized until the
old reply drains, so a late receipt cannot be returned for another request.
Reconcile `/admissions`, `/inputs` and the acknowledged journal/feed before any
operator decision; an idempotency key does not turn an unknown result into a
known rejection. No new admission/execution status is invented for these host
failures. A newly owned terminal worker without retained progress can be replayed
only when the whole journal matches its published ACK; otherwise `/inputs`
returns 409/unknown progress instead of reading an unacknowledged tail. Older
terminal runs retain their existing engine-free replay fallback.

Concurrent producers can send `stream_id`, `watermark_ns`, and
`expected_watermark_ns` together. If the source has advanced beyond that observed
frontier, the receipt returns its actual watermark with `advanced: false`, without
writing another journal record. An applied conditional closure returns
`advanced: true`. Unconditional backwards closures remain invalid.

Ingress bodies name `engine`, `schema`, `target`, `payload` and optionally
`stream_id`, `at_ns`, `source_stamp`, `idempotency_key`. If the schema is a
declared behaviour injection command, the payload must carry an
`injection_point` id that resolves uniquely, the target engine and named
stream must match the declaration, and the payload validates against the
point's event schema.

## Artifacts and agents

| Endpoint | Method | Semantics |
| --- | --- | --- |
| `/v1/runs/{id}/capture-requests/{request_id}/scene` | GET | Committed scene snapshot for an outstanding capture request (`aeroagentsim.capture-scene/v1`), read exactly up to the requested cut. 404 unknown; 409 not readable/inconsistent. |
| `/v1/runs/{id}/artifacts` | GET | List stored capture artifacts. |
| `/v1/runs/{id}/artifacts/{content}` | GET | Metadata for one artifact by content id, plus a download link. |
| `/v1/runs/{id}/artifacts/{content}/download` | GET | PNG bytes (`ETag` = content id). |
| `/v1/runs/{id}/capture-artifacts` | POST (202) | Upload a rendered capture (base64 PNG + request + timing stamps, ≤ 90 MB). Requires an active run and exactly one configured capture owner; the resulting storage event uses the same bounded worker ingress adapter. 409/422 on mismatch, 504 on admission timeout. |

Capture validation and PNG persistence precede admission; `--input-timeout` does
not cap the entire upload or filesystem work. Admission captures an absolute
deadline **before executor dispatch**, includes queue delay and never refreshes
its budget when dispatched. Expired unsent work cannot submit later. A 504 is not
an execution rejection or permission to retry: reconcile admissions and the
acknowledged journal as for `/ingress`. Stored bytes remain available even when
admission times out, and do not imply acceptance or capture success. The delivery
sidecar is diagnostic only: it shares the remaining admission budget and may be
absent after timeout rather than delaying the HTTP error behind a saturated
executor. An already-running diagnostic write may finish later.

Agent records: LangGraph decision messages are recorded as
`aas.langgraph.record` events; the projector additionally derives
console-compatible `aas.agent.record` views (`observation`, `prompt`,
`response`, `validation` phases) from them — these are projection views of
real WAL records, not separate commands.

## Studio (authoring) API — `/v1/studio`

Mounted by `aeroagentsim serve` (including `--studio-root DIR`, default
`<out>/.workspaces` or `AEROAGENTSIM_STUDIO_ROOT`), by
`create_app(..., studio_root=...)`, by `AEROAGENTSIM_STUDIO_ROOT`, and by the
`demo` command. Creating a template workspace or deriving one from a run also
mounts the router lazily. All execution still goes through `/v1/runs`.

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/catalog` | GET | Extract sources, engine catalog with capability descriptors, demo profiles. |
| `/types` | GET | Type catalog search (`q`, optional `workspace`). |
| `/types/{id}` | GET | Type catalog detail (snapshot-backed when no AeroGraph root exists). |
| `/explorer` | GET | Workspace explorer payload (`workspace_id` optional). |
| `/workspaces` | GET/POST | List drafts; create with `{"name"}` (201). |
| `/workspaces/{id}` | GET/POST | Fetch a draft; save `{scenario?, name?, revision?}`. |
| `/workspaces/{id}/knobs` | POST | Apply template controls to the draft: `{knobs, revision?}`; keeps other edits and rejects unknown knobs (422). |
| `/workspaces/{id}/validate` | POST | Validate `{scenario?, revision?}` and return `{valid, errors, revision}`. This checks configuration/declarations, not run readiness. |
| `/workspaces/{id}/versions` | GET/POST | List immutable versions; freeze a validated revision with `{revision, note?}` (201). The same revision returns the same version. |
| `/workspaces/{id}/versions/{version}` | GET | Fetch one immutable version, including its frozen scenario, digest and validation. |
| `/workspaces/{id}/operations` | POST | Compile and apply typed draft operations `{operations, revision?, dry_run?}` with the assistant compiler; no LLM call. |
| `/operations` | GET | Operation catalogue: names, topics, labels, parameter schemas and examples. |
| `/workspaces/{id}/templates/{name}` | POST | Import a demo template (`console`, `capture_mode: city\|primitive-test`). |
| `/workspaces/{id}/decision-profile` | POST | Switch demo decisions between the `scripted` fixture and a named live provider. |
| `/workspaces/{id}/behaviours` | POST | Edit/replace behaviour packages. |
| `/workspaces/{id}/behaviours/{index}/export` | GET | Export one behaviour package. |
| `/workspaces/{id}/behaviours/{index}/validate` | POST | Validate one behaviour package (draft must be saved first). |
| `/workspaces/{id}/region` | POST | Select/apply a region from a configured OSM extract. |
| `/workspaces/{id}/place` | POST | Place an object in the draft. |
| `/workspaces/{id}/remove` | POST | Remove a draft object. |
| `/workspaces/{id}/relations` | POST | Add a relation to the draft. |
| `/workspaces/{id}/import` | POST | Import YAML (`{yaml, revision?}`). |
| `/workspaces/{id}/export` | GET | Export the draft as YAML. |
| `/workspaces/{id}/network` | POST | Compile the SUMO network for the applied region. |
| `/workspaces/{id}/network.net.xml` | GET | Retrieve the compiled SUMO network. |
| `/workspaces/{id}/buildings.geojson` | GET | Draft building geometry. |
| `/runs/{run_id}/buildings.geojson` | GET | Run building geometry. |
| `/demo-assets/{name}` | GET | Demo city assets. |
| `/demo-capture-assets` | GET | Demo capture manifest. |
| `/runs/{run_id}/configuration` | GET | Pinned authoring inputs plus actual WAL identity (`kernel_run_id`, `epoch`, `resolved_bindings`) — read-only. |

Error mapping: 404 for missing resources, 422 for invalid input, 409 for
state conflicts.

## Assistant (authoring) API

The assistant router is mounted with Studio under `/v1/assistant` and shares the
same Host/Origin and optional bearer-token boundary. Routes, long-poll events and
plan/apply semantics are documented in the
[assistant guide](../guides/assistant.md#http-api). The assistant authors and
saves project versions; it does not start a mission, and a confirmed plan or
model reply is not a completed run.

## Frontend pages

When `--frontend` points at a built console, the server serves the workbench at
`/` and `/w/<workspace>`, the agent console at `/agents/<run>`, and redirects the
earlier addresses `/runs`, `/inspect`, `/aerograph` and `/studio` into the workbench. The console sends
JSON for submissions and controls; configure its origin using
`AEROAGENTSIM_CONSOLE_URL` when it is hosted outside localhost.

See [experiments](../guides/experiments.md) for matched seeds, sample statistics, report files and restart behavior.
