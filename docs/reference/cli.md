# CLI reference

Entry point: `aeroagentsim` (defined in `pyproject.toml` as
`aeroagentsim.services.cli:main`; see
[`src/aeroagentsim/services/cli.py`](../../src/aeroagentsim/services/cli.py)).
Install with `pip install -e ./aerokernel -e '.[server]'` for `serve`; the
agents extra is `[agents]`.

## aeroagentsim run

```console
aeroagentsim run scenarios/behaviours/minimal.yaml --out runs
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `scenario` (positional) | — | Path to a scenario YAML file. |
| `--out` | `runs` | Output root; the run directory is `<out>/<scenario-stem>-<12 hex>`. |
| `--engine-profile` | — | Path to an engine-profile YAML applied after loading. |
| `--provenance` | scenario value (`lean`) | `lean` or `full`; explicit override of the pinned scenario default. |

Prints a JSON summary: `run`, `elapsed_wall_s`, `simulated_s`, `rtf`,
`peak_rss_kib`. These are the timings of your own run — do not compare them
against timings taken from other profiles or hardware.

## aeroagentsim plugins

```console
aeroagentsim plugins
```

Prints the plugin catalog as JSON: `{api_version, scenarios, engines,
algorithms, models, metrics}`. `api_version` remains `1`; `metrics` is an additive
family with the same `{name, origin, distribution, status}` row fields. Scenario
rows also contain sorted `templates` (IDs from `template.yaml`) and `apply_hooks`
(owned hook names). These arrays are populated by loading scenario plugins and
calling their data factories, as in the existing inventory contract. Hooks are
not invoked; engines and model providers are never instantiated. The other four
families are enumerated from metadata without loading their entries.

Rows are sorted by name, with installed entries replacing same-name builtins.
Every family reports a duplicated installed name once with `status: "duplicate"`
and `conflict` containing every declaring distribution, including repeated names
and unknown (`null`) distributions. Other rows have `status: "ok"`, which means
only that the name is unambiguous, not that its contract is valid or ready to run.
Malformed declarations remain visible for diagnosis. Scenario/metric runtime
admission rejects bad names and duplicates before loading providers; engine
catalogs reject duplicates before loading factories. Algorithm/model runtime
lookup retains the legacy rules in [extension admission](extension-admission.md#family-matrix).

If any scenario name is duplicated or malformed, the **whole scenario section**
falls back to declaration metadata without loading scenario plugins. Builtins
stay listed and all scenario `templates`/`apply_hooks` arrays are then empty
because contributions are uninspected. In normal, successfully loaded rows,
empty arrays instead mean that the plugin declares no contributions of that
kind (for example, a hook-only plugin has no templates).

The catalogue is an inventory, not readiness: it does not prove a worker, native
simulator, camera, model provider or detailed city is available. See
[extension admission](extension-admission.md), [plugin contracts](plugin-contracts.md)
and the matching [`GET /v1/plugins` response](http-api.md#plugin-catalog).

## aeroagentsim templates

```console
aeroagentsim templates
```

Lists the templates discovered from builtin and installed scenario plugins as
JSON. Installed plugin templates may add to the builtins; there is no fixed
count.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--templates-root` | discovered plugins | Operator template registry directory. Loads that root's manifests while retaining discovered hooks; it does not add every plugin template. |

## aeroagentsim run-template

```console
aeroagentsim run-template traffic-accident --set accident_time_s=5 --set physics_hz=2 --headless
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `template` (positional) | — | Template id from `aeroagentsim templates`. |
| `--set` | — | Repeatable `KEY=VALUE` typed knob override. |
| `--headless` | off | Run to completion and print a JSON summary instead of opening a console. |
| `--out` | `runs/templates` | Output root. |
| `--port` | `0` (pick a free port) | Interactive API port. |
| `--templates-root` | discovered plugins | Operator template registry directory. |

`run-template` applies declared defaults and repeated `--set` overrides before
validating and running the scenario. Choices use their declared names,
integers/numbers use numeric values, and booleans use `true` or `false`. Unknown
knobs, type errors and out-of-range values fail with a readable message. Headless
output includes the run id, directory, status and measured KPI values; applied
knobs are pinned in the run manifest. Without `--headless`, the command imports a
draft, opens the project in the workbench (`/w/<workspace>`) and serves it; it
does not start a run. That mode requires a built `frontend/dist`.

`--headless` still uses the actual city camera for traffic capture, which
requires Node/Playwright and Chromium; `AEROAGENTSIM_CHROMIUM` can select an
installed browser. The scripted decision mode uses the shipped declared decision
fixture; live mode requires a named operator provider profile and the agents
extra. See [create a template](../guides/templates.md) for the manifest format.

## aeroagentsim experiment

```console
aeroagentsim experiment parcel-delivery --vary allocator=nearest,auction --seeds 3 --jobs 2
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `template` (positional) | — | Template id. |
| `--vary` | — | Repeatable `KNOB=VALUE,VALUE` axis of knob values to sweep. |
| `--seeds` | `3` | Matched deterministic seeds `0..seeds-1` per cell. |
| `--jobs` | `1` | Bounded parallel worker processes. |
| `--horizon` | template duration | Simulated horizon in seconds. |
| `--out` | `runs` | Output root. |
| `--templates-root` | discovered plugins | Operator template registry directory. |

Runs a full-factorial headless experiment. Normal run directories remain visible
in Runs/Compare. Reports live under `<out>/.experiments/<id>/experiment.md` and
`experiment.csv` and contain journal-based mean, sample standard deviation,
sample count and best-value flags. See [experiments](../guides/experiments.md).

## aeroagentsim replay

```console
aeroagentsim replay runs/scenario-0f3a21b4c5d6
```

Reconstructs a read-only kernel from the run's `journal.jsonl` with no
engines, clocks or RNG calls — replay never contacts model providers or
engines. Prints `{run, cut, ns, incomplete}`; `incomplete: true` flags a
truncated or faulted journal (a sealed, complete run replays deterministically).
Recovered records are links at the recorded cut, not causal labels or proof of
mission success.

## aeroagentsim metrics

```console
aeroagentsim metrics runs/scenario-0f3a21b4c5d6
```

Prints the metrics pack's JSON for a completed run (from
[`src/aeroagentsim/packs/metrics.py`](../../src/aeroagentsim/packs/metrics.py)).
Missing measurements stay missing; they are not filled with zero.

## aeroagentsim serve

```console
aeroagentsim serve --out runs --frontend frontend/dist
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--out` | `runs` | Run storage root. |
| `--scenario-root` | current directory | Root that `scenario_path` submissions are resolved against. |
| `--host` / `--port` | `127.0.0.1` / `8002` | Bind address; non-loopback serving requires API authentication unless explicitly overridden below. |
| `--unsafe-local-lab` | off | **Unsafe:** allow a non-loopback listener without authentication in an isolated trusted lab; prints a bilingual stderr warning. Never use on shared/public networks. |
| `--startup-timeout SECONDS` | `60` | Real worker initialization and t=0 ACK deadline. |
| `--input-timeout SECONDS` | `10` | End-to-end worker input/progress budget, including executor queue, serialization, send and full reply; also applies to capture-artifact admission. |
| `--shutdown-grace SECONDS` | `10` | Cooperative stop budget, shared across all owned run workers. |
| `--terminate-grace SECONDS` | `2` | Terminate-to-exit budget, shared across workers. |
| `--kill-grace SECONDS` | `2` | Kill-to-exit/reaping budget, shared across workers. |
| `--cleanup-timeout SECONDS` | `1` | Final IPC/cache-task drain (shared at shutdown, per completed run otherwise); also bounds the worker's final input-thread join. |
| `--frontend` | — | Built console directory (`frontend/dist`); enables SPA pages. |
| `--studio-root` | `<out>/.workspaces` (or `AEROAGENTSIM_STUDIO_ROOT`) | Studio workspace storage. `--studio-root` wins over the environment variable, which wins over the `<out>/.workspaces` default. |

The console URL used for city assets and the in-simulation camera defaults to
`http://<host>:<port>` (IPv6 literals are bracketed; wildcard binds use
`127.0.0.1` instead). Set `AEROAGENTSIM_CONSOLE_URL` when serving behind a proxy
or accessing a wildcard listener remotely.
`serve` mounts the Studio and assistant routes; the workspace root above is also
the default when no `--studio-root` is passed.

**Non-loopback serving requires a nonblank `AEROAGENTSIM_API_TOKEN`.** This
includes wildcard binds (`0.0.0.0`, `::`), remote IPs and DNS names other than
`localhost`; DNS is not resolved to infer loopback. Missing/blank credentials fail
before app/storage creation. Literal loopback addresses and `localhost` retain
optional authentication. `--unsafe-local-lab` is the explicit isolated-lab
exception, not an authentication feature: Host/Origin, JSON and any configured
token checks still apply. With a wildcard listener, set
`AEROAGENTSIM_CONSOLE_URL` to the actual trusted remote origin; binding remotely
does not broaden trusted Hosts. Programmatic ASGI listeners must enforce the same
bind policy themselves.

When `AEROAGENTSIM_API_TOKEN` is set, API clients must send
`Authorization: Bearer <token>` for `/v1/` requests (except CORS preflight). The
browser console sends no bearer token, so a token-protected server is not a
supported browser flow. See the [HTTP request boundary](http-api.md#request-boundary).

Lifecycle limits must be positive finite wall-clock seconds, no greater than
Python's `threading.TIMEOUT_MAX`. They do not alter simulated time or the
scenario's watermark `timeout_s`. Shutdown process-control phases share
10 + 2 + 2 seconds by default, followed by one shared cleanup second, rather
than multiplying by the number of workers. Uvicorn separately allows five seconds
for open requests/SSE before worker shutdown. These are scheduling budgets, not
hard real-time guarantees against stalled filesystems or unkillable OS tasks.
Startup/input timeouts retain the run id and prohibit automatic resubmission;
inspect the run, admissions and acknowledged journal instead. Capture upload
validation and byte storage precede its admission budget. See
[host lifecycle limits](http-api.md#host-lifecycle-limits) for failure semantics
and process-control limitations.

Requires the `server` extra. On restart, non-terminal runs are marked
`interrupted`: the journal remains replayable, but execution is not resumed.
See [HTTP API](http-api.md).

## aeroagentsim demo

```console
aeroagentsim demo traffic-accident --profile kinematic --headless --out runs/demo
aeroagentsim demo traffic-accident --profile live-llm
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `name` (positional) | — | Only `traffic-accident` is shipped. |
| `--profile` | `kinematic` | `kinematic`, `sumo`, `px4`, or `live-llm`. |
| `--headless` | off | Run to completion without the console; prints the observed event chain as JSON. |
| `--port` | 0 (auto) | Console port when not headless. |
| `--out` | `runs/demo` | Output root. Console mode uses `runs/` and `workspaces/` subdirectories; headless mode writes each run directly here. |
| `--provenance` | `lean` | `lean` or `full`. |

Profile behaviour, exactly as implemented:

- `kinematic` — the runnable default; procedural OSM city, scripted decisions.
- `sumo` / `px4` — **configuration contracts, not runnable replacements**.
  The CLI raises an error listing each profile's `configuration_required`
  items (see
  [`scenarios/demos/traffic-accident/profiles/`](../../scenarios/demos/traffic-accident/profiles/sumo.yaml)).
  Generic native adapters do work with correctly configured separate
  scenarios; the shipped demo just does not carry that configuration.
- `live-llm` — requires `AEROAGENTSIM_LLM_BASE_URL`,
  `AEROAGENTSIM_LLM_MODEL`, `AEROAGENTSIM_LLM_API_KEY_ENV` and the named
  API-key environment variable to be populated, plus the `[agents]` extra.
  Without them the command fails; nothing is silently stubbed as live data.
  The default `kinematic` profile's `traffic_decisions` `mode: stub` is an
  authored fixture and is not model inference.

The console mode needs a frontend build (`cd frontend && npm ci && npm run
build`) and a Chromium for capture rendering (`AEROAGENTSIM_CHROMIUM` or
Playwright's Chromium).

## aeroagentsim assets

```console
aeroagentsim assets list
aeroagentsim assets fetch --url https://example.invalid/pack.zip
aeroagentsim assets install ./huangpu-city-v1.1.zip
aeroagentsim assets remove huangpu-city --version 1.1.0
```

| Subcommand | Flags | Meaning |
| --- | --- | --- |
| `list` | — | List installed asset packs as JSON. |
| `fetch` | `--url URL`, `--insecure-skip-digest` | Download, verify and install a pack. Without `--url`, uses `AEROAGENTSIM_ASSET_PACK_URL` or the packaged default. `--insecure-skip-digest` explicitly skips archive SHA-256 verification. |
| `install` | positional `ZIP-OR-DIR` | Install from a local archive or directory, verifying its manifest and file sizes. |
| `remove` | positional `ID` (default `huangpu-city`), `--version` | Remove an installed pack, optionally one version. |

See [asset verification and licences](../../ASSETS.md) for locations, digests
and NOTICE terms.

## Measure a run

`run` prints wall time, simulated time and their ratio for your invocation. Keep units, publication cadence, provenance level and hardware explicit when comparing experiments. The default traffic demo uses 1 Hz motion publication; see its [walkthrough](../examples/traffic-accident.md).
