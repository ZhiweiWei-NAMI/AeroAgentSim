# Example scenario plugin

This distribution adds the `example-patrol` template without changing the
platform. It copies the built-in `security-patrol` scenario and its registry
snapshot, changes the scenario id, and exposes two knobs:

| Knob | Hook | Effect |
| --- | --- | --- |
| `run_mode` | `lowalt.operator_mode` | Reuses the built-in hook provider for scheduled or interactive ingress. |
| `patrol_interval_s` | `example-patrol.patrol_interval` | Sets patrol arrivals to 0, one interval and two intervals (30–120 seconds per interval). |

## Install and inspect

Use an environment with a compatible AeroAgentSim installation that exports
`aeroagentsim.plugin_api` with `API_VERSION = 1`. From the repository root:

```sh
python -m pip install ./examples/plugins/aeroagentsim-example-scenario
aeroagentsim plugins
aeroagentsim templates
aeroagentsim run-template example-patrol --headless --out runs/example-patrol
aeroagentsim run-template example-patrol --set patrol_interval_s=60 --headless --out runs/example-patrol-60
```

`plugins` should show the installed `example-patrol` scenario plugin, its one
template and its own hook. `lowalt` remains the provider of
`lowalt.operator_mode`. `templates` should include `example-patrol` beside the
built-ins. Do not pass `--templates-root`; that selects a directory instead of
merging plugin templates.

Without `--headless`, `run-template` opens the configuration in Studio.
Interactive mode (`--set run_mode=interactive`) waits for operator time
advances and events; use the console and Stop rather than an unattended run.
The copied investigation at 55 simulated seconds remains scheduled in both
modes; do not inject a second investigation for the same one-shot task.

## Packaged inputs

The package-data patterns include `template.yaml`, `scenario.yaml` and
`registry.snapshot.json`. All three snapshot references (the scenario and its
two inline behaviour packages) resolve to that local snapshot. Behaviour
rules, coordinates and schedules are inline. No separate ontology checkout,
model provider or native simulator is needed for this example.

The factory locates these files with `importlib.resources.files`. It requires
an unpacked filesystem installation, as produced by a normal wheel install;
zip-imported packages are not supported. It does not return a path from a
closed temporary `as_file()` context.

The repository test imports this package from `src` without installing it,
then applies both default and changed knobs through `TemplateRegistry`.
That exercises scenario loading, not wheel contents or a complete run.

See the [scenario plugin guide](../../../docs/guides/scenario-plugins.md) and
[plugin contracts](../../../docs/reference/plugin-contracts.md). The copied
scenario and registry retain the repository's [Apache-2.0 licence](../../../LICENSE).
