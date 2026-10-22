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

Set `AEROAGENTSIM_API_TOKEN` for a non-local deployment. When set, every `/v1/`
API request requires `Authorization: Bearer <token>`; missing or incorrect
tokens return 401. CORS preflight remains accessible. Local use with the token
unset works directly from the console. For example:

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

Run status values: `created`, `running`, `paused`, `waiting_for_input`,
`completed`, `stopped`, `faulted`, `interrupted`, `input_timeout`.
Terminal statuses are `completed`, `stopped`, `faulted`, `interrupted`,
`input_timeout` and carry a `final_cursor`.

## Plugin catalog

| Endpoint | Method | Semantics |
| --- | --- | --- |
| `/v1/plugins` | GET | Plugin catalog: `{api_version, scenarios: [{name, origin, distribution, templates, apply_hooks}], engines, algorithms, models}`. `api_version` is `1`. |

Scenario entries describe discovered builtins and installed plugins in
`aeroagentsim.scenarios`. Installed names take precedence over matching builtins;
duplicate installed names are errors. The CLI counterpart is
[`aeroagentsim plugins`](cli.md#aeroagentsim-plugins). See
[plugin contracts](plugin-contracts.md) for the plugin APIs.

## Run execution API

| Endpoint | Method | Semantics |
| --- | --- | --- |
| `/v1/runs` | POST (201) | Start a run. Body is either `{"scenario_path": "..."}` (resolved under the configured scenario root) or `{"scenario": {...}}` / inline scenario mapping, optionally with `studio_workspace` to base a console draft. The loader runs and engine declarations/bindings are validated by constructing a throwaway `Simulation` before 201 is returned; invalid input yields 422. Returns the run metadata. |
| `/v1/runs` | GET | List all runs with manifests (id, scenario, status, `until_ns`, optional `error` and `waiting`), additive `kpis` summaries, `knobs`, `algorithms`, `title`, and pinned `template` settings (`template_id`/`template_title` aliases) when launched from a template. |
| `/v1/runs/{id}/header` | GET | Pinned viewer-feed header (see [viewer feed](viewer-feed.md)). Adds `catalogNotice` when no AeroGraph root is configured, plus Studio scene links when applicable. |
| `/v1/runs/{id}/commits?from=N&limit=M` | GET | Page projected commits (`limit` 1–4096). Response: `{commits, next, status, waiting?, finalCursor?}`. Empty runs page from index 1. |
| `/v1/runs/{id}/stream?from=N` | GET (SSE) | Live tail. Events: `commit` (id = journal index; supports `Last-Event-ID` reconnect), `status` (on change, includes `waiting`), `end` (terminal, includes `finalCursor`). Keep-alive comments while idle. |
| `/v1/runs/{id}/{pause\|resume\|stop}` | POST | Control a live worker. 409 when there is no active worker or the run is terminal. Pause freezes simulation advancement; resume continues it; stop requests a graceful halt at a sealed boundary. Returns `{id, requested, status}`. |

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

The run manifest pins applied knobs. Comparison reads those settings and the
run's saved scenario algorithms, not current template defaults. Missing
measurements remain `null`; count metrics record zero when the committed
prefix has no corresponding events. KPI details and business-event narratives
are described in [the viewer feed](viewer-feed.md).

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
| `/v1/runs/{id}/ingress` | POST | Submit typed live input after worker-side validation against the pinned injection manifest (see below). 409 if the worker is gone; 422 with a kernel error code for rejected payloads. Returns an admission receipt. |
| `/v1/runs/{id}/watermark` | POST | Advance stream progress: exactly one of `watermark_ns` (integer) or `source_stamp` (`clock_id, mapping_id, numerator, denominator`), optional `stream_id`. Returns a `aeroagentsim.watermark-receipt/v2` with the effective watermark. |

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
| `/v1/runs/{id}/capture-artifacts` | POST (202) | Upload a rendered capture (base64 PNG + request + timing stamps, ≤ 90 MB). Requires an active run and exactly one configured capture owner; the resulting storage event is admitted through the worker ingress path. 409/422 on mismatch. |

Agent records: LangGraph decision messages are recorded as
`aas.langgraph.record` events; the projector additionally derives
console-compatible `aas.agent.record` views (`observation`, `prompt`,
`response`, `validation` phases) from them — these are projection views of
real WAL records, not separate commands.

## Studio (authoring) API — `/v1/studio`

Mounted when `create_app(..., studio_root=...)` or `AEROAGENTSIM_STUDIO_ROOT` is configured. The `demo` command configures it automatically; `serve` has no studio-root flag. All execution still goes through `/v1/runs`.

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/catalog` | GET | Extract sources, engine catalog with capability descriptors, demo profiles. |
| `/types?q=&workspace=` · `/types/{id}` | GET | Type catalog search/detail (snapshot-backed when no AeroGraph root exists). |
| `/explorer?workspace_id=` | GET | Workspace explorer payload. |
| `/workspaces` | GET/POST | List drafts; create with `{"name"}` (201). |
| `/workspaces/{id}` | GET/POST | Fetch / save a draft. |
| `/workspaces/{id}/templates/{name}` | POST | Import a demo template (`console`, `capture_mode: city\|primitive-test`). |
| `/workspaces/{id}/decision-profile` | POST | Switch demo decisions between `scripted` fixture and a named live provider. |
| `/workspaces/{id}/behaviours` · `.../validate` · `.../export` | POST/GET | Edit, validate (draft must be saved first) and export a behaviour package. |
| `/workspaces/{id}/region` · `/place` · `/validate` · `/import` · `/export` | POST/GET | Region selection from configured OSM extracts, placement, scenario validation, YAML import/export. |
| `/workspaces/{id}/network` · `/network.net.xml` | POST/GET | Compile/retrieve the SUMO network for the region. |
| `/workspaces/{id}/buildings.geojson` · `/runs/{run_id}/buildings.geojson` | GET | Draft/run building geometry. |
| `/demo-assets/{name}` · `/demo-capture-assets` | GET | Demo city assets and capture manifest. |
| `/runs/{run_id}/configuration` | GET | Pinned authoring inputs plus actual WAL identity (`kernel_run_id`, `epoch`, `resolved_bindings`) — read-only. |

Error mapping: 404 for missing resources, 422 for invalid input, 409 for
state conflicts.

## Frontend pages

When `--frontend` points at a built console, the server serves the workbench at
`/` and `/w/<workspace>`, the agent console at `/agents/<run>`, and redirects the
earlier addresses `/runs`, `/inspect`, `/aerograph` and `/studio` into the workbench. The console sends
JSON for submissions and controls; configure its origin using
`AEROAGENTSIM_CONSOLE_URL` when it is hosted outside localhost.

See [experiments](../guides/experiments.md) for matched seeds, sample statistics, report files and restart behavior.
