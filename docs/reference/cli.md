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

## aeroagentsim replay

```console
aeroagentsim replay runs/scenario-0f3a21b4c5d6
```

Reconstructs a read-only kernel from the run's `journal.jsonl` with no
engines, clocks or RNG calls — replay never contacts model providers or
engines. Prints `{run, cut, ns, incomplete}`; `incomplete: true` flags a
truncated or faulted journal (a sealed, complete run replays deterministically).

## aeroagentsim metrics

```console
aeroagentsim metrics runs/scenario-0f3a21b4c5d6
```

Prints the metrics pack's JSON for a completed run (from
[`src/aeroagentsim/packs/metrics.py`](../../src/aeroagentsim/packs/metrics.py)).

## aeroagentsim serve

```console
aeroagentsim serve --out runs --frontend frontend/dist
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--out` | `runs` | Run storage root. |
| `--scenario-root` | current directory | Root that `scenario_path` submissions are resolved against. |
| `--host` / `--port` | `127.0.0.1` / `8002` | Bind address. |
| `--frontend` | — | Built console directory (`frontend/dist`); enables SPA pages. |

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

The console mode needs a frontend build (`cd frontend && npm ci && npm run
build`) and a Chromium for capture rendering (`AEROAGENTSIM_CHROMIUM` or
Playwright's Chromium).

## Measure a run

`run` prints wall time, simulated time and their ratio for your invocation. Keep units, publication cadence, provenance level and hardware explicit when comparing experiments. The default traffic demo uses 1 Hz motion publication; see its [walkthrough](../examples/traffic-accident.md).
