# Guide: Add a scenario as a plugin

A scenario plugin supplies template directories and namespaced knob hooks.
It does not need to supply an engine. The ten built-in scenarios each use a
one-template plugin named by template id; `lowalt`, `traffic` and `rescue`
are separate hook-provider plugins with no templates.

Use the public contracts in
[`aeroagentsim.plugin_api`](../../src/aeroagentsim/plugin_api.py).
See [plugin contracts](../reference/plugin-contracts.md) for ownership,
determinism and compatibility rules. Installed plugins are trusted Python
code, not sandboxed scenario documents.

## 1. Package a working template

Start from the [external example](../../examples/plugins/aeroagentsim-example-scenario/).
It copies `security-patrol`, the smallest self-contained patrol/delivery
scenario, under a new id. Keep the scenario's registry snapshot and every
relative input inside its template directory.

```text
aeroagentsim-example-scenario/
├── pyproject.toml
├── README.md
└── src/aeroagentsim_example_scenario/
    ├── __init__.py
    └── templates/example-patrol/
        ├── template.yaml
        ├── scenario.yaml
        └── registry.snapshot.json
```

Set both manifest and scenario ids to `example-patrol`; do not reuse a
built-in template id unless you are replacing its plugin. The directory name
should match. `template.yaml` uses `aeroagentsim.template/v1`; see the
[template guide](templates.md) for required metadata and JSON-pointer knobs.
The example keeps its behaviour packages inline, and their snapshot paths
also point to the local `registry.snapshot.json`.

## 2. Register the distribution

Use an environment with a compatible AeroAgentSim installation exporting
`API_VERSION = 1`. This is the example's `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=61", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "aeroagentsim-example-scenario"
version = "0.1.0"
readme = "README.md"
requires-python = ">=3.10"
dependencies = ["aeroagentsim"]

[project.entry-points."aeroagentsim.scenarios"]
example-patrol = "aeroagentsim_example_scenario:plugin"

[tool.setuptools]
package-dir = {"" = "src"}
include-package-data = true

[tool.setuptools.packages.find]
where = ["src"]
include = ["aeroagentsim_example_scenario"]
namespaces = false

[tool.setuptools.package-data]
aeroagentsim_example_scenario = ["templates/*/*.yaml", "templates/*/*.json"]
```

Add package-data patterns for any nested behaviours, fixtures or thumbnails
you introduce. Merely keeping files in the source tree does not package them.
The distribution name, import name and plugin name may differ. The entry-point
name must equal the returned `ScenarioPlugin.name`.

## 3. Return a plugin and define its hook

Put this in `src/aeroagentsim_example_scenario/__init__.py`:

```python
from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

from aeroagentsim.plugin_api import ScenarioPlugin, TemplateSource


def _patrol_interval(document: dict[str, Any], base: Path, value: Any) -> None:
    if type(value) is not int or not 30 <= value <= 120:
        raise ValueError("knob patrol_interval_s: expected an integer from 30 to 120")
    patrol = next(
        generator
        for generator in document["engines"]["tasks"]["config"]["generators"]
        if generator["kind"] == "patrol"
    )
    patrol["arrivals"]["times_ns"] = [
        index * value * 1_000_000_000 for index in range(len(patrol["tasks"]))
    ]


def plugin() -> ScenarioPlugin:
    directory = files(__name__).joinpath("templates", "example-patrol")
    if not isinstance(directory, Path):
        raise RuntimeError("example-patrol: install as an unpacked filesystem package")
    return ScenarioPlugin(
        name="example-patrol",
        api_version=1,
        templates=(TemplateSource(directory=directory.resolve()),),
        apply_hooks={"example-patrol.patrol_interval": _patrol_interval},
    )
```

The factory takes no arguments. `templates` is a tuple of `TemplateSource`
objects; each `directory` must be a `Path`. `importlib.resources.files`
locates installed package data without assuming a repository checkout.
This example requires filesystem resources, as in a normal unpacked wheel
installation. It does not support zip imports. Do not return a temporary path
from a closed `as_file()` context: resources must remain available while the
registry, runs and asset readers use them.

`TemplateSource` also accepts `scenario: Path | None` (an absolute override)
and `extra_knobs: KnobProvider | None`. A knob provider takes
`document: dict[str, Any]` and returns `list[dict[str, Any]]` knob declarations.
Neither is needed here.

Each hook key must start with its owner's `<plugin>.` prefix and have a
nonempty suffix. A hook has the `ApplyHook` signature
`(document: dict[str, Any], base: Path, value: Any) -> None`.
It may edit only the copied document. Keep it deterministic; no network,
clock, random or write side effects. The only permitted I/O is reading files
under `base`, the scenario source directory. Reject invalid values with
`ValueError("knob <id>: ...")`. Knob providers should also derive declarations
deterministically without mutating their input.

## 4. Reuse another plugin's hooks

The example manifest declares these knobs:

```yaml
knobs:
- id: run_mode
  label: Run mode
  type: choice
  default: scheduled
  options: [scheduled, interactive]
  apply: lowalt.operator_mode
- id: patrol_interval_s
  label: Patrol arrival interval (s)
  type: integer
  default: 100
  min: 30
  max: 120
  apply: example-patrol.patrol_interval
```

Do not copy or register `lowalt.operator_mode` in your own plugin. Discovery
loads `lowalt` alongside your plugin. Its hook expects an `operator` entry in
`ingress_streams` and `run.until_ns`: scheduled mode sets that stream's initial
watermark to the run horizon; interactive mode sets it to zero and removes
the timeout. Reusing a hook requires the matching document structure, not just
its name. Hook dependencies are not inferred or installed by the registry;
package any external hook-provider dependency explicitly.

To inspect without entry-point installation, supply plugin instances:

```python
import aeroagentsim_example_scenario as example
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.plugin_api import discover_scenario_plugins

registry = TemplateRegistry(
    plugins=(*discover_scenario_plugins(), example.plugin())
)
assert registry.plugin_of("example-patrol") == "example-patrol"
prepared = registry.apply("example-patrol", {"patrol_interval_s": 60})
```

`plugin_of(id)` returns the owner name; `directory_of(id)` locates its assets.
Do not append the example a second time if it is already installed and
discovered. Passing `plugins=()` disables discovery. Passing a `root` selects
only that root's manifests, while retaining supplied/discovered hooks; it
does not merge external templates. Configured-root templates have no plugin
owner. For local authoring only, `register_apply_hook(name, hook)` remains
available; installed plugins should declare their hooks in `ScenarioPlugin`.

## 5. Replace a built-in by name

Installed entry points take precedence over built-ins of the same name.
Discovery replaces the whole plugin, not individual templates or hook keys.
Duplicate installed entry-point names fail; duplicate template ids and hook
keys also fail rather than silently overriding one another.

For example, an AeroRescue distribution could register **two** factories:

```toml
[project.entry-points."aeroagentsim.scenarios"]
flood-rescue = "aerorescue.scenarios:flood_plugin"
rescue = "aerorescue.scenarios:hooks_plugin"
```

Here `flood_plugin()` must return `ScenarioPlugin(name="flood-rescue", ...)`
with its replacement template, and `hooks_plugin()` must return
`ScenarioPlugin(name="rescue", ...)` with every `rescue.*` hook needed by the
selected templates. Replacing only `flood-rescue` leaves the built-in `rescue`
hook provider available. Replacing only `rescue` also affects any other
template using its hooks. This is a replacement pattern, not a claim that an
AeroRescue distribution already ships these factories. It does not make
private rescue engine state or mission-control proposals public APIs.

## 6. Install and verify

From the repository root, in the environment used by the CLI/server:

```sh
python -m pip install ./examples/plugins/aeroagentsim-example-scenario
aeroagentsim plugins
aeroagentsim templates
aeroagentsim run-template example-patrol --headless --out runs/example-patrol
aeroagentsim run-template example-patrol --set patrol_interval_s=60 --headless --out runs/example-patrol-60
```

`plugins` reports API version, scenario plugin origins/distributions, template
ids and owned hook names, plus engine/algorithm/model catalogs. Look for the
installed `example-patrol` and the separate `lowalt` provider. `templates`
should include the new id. The server exposes the same catalog through
[`GET /v1/plugins`](../reference/http-api.md).

In the console, open the scenario gallery and choose **Plugins / 插件**. The
panel lists every discovered scenario plugin, including hook-only providers,
with its origin (`built-in` or `entry-point`), distribution, template ids and
hook names; a template card also carries a small badge naming the built-in or
distribution that declares its template id. Matching is by id only, so a
configured template root can register the same id — read the badge as declared
provenance, not proof of which directory the running server loaded.

Without `--headless`, open the template in Studio. Interactive mode waits for
operator time advances/events; use the console and Stop, not an unattended
headless check. The example retains its scheduled investigation at 55 seconds
in both modes; do not inject that one-shot task a second time.
`apply()` validates knobs, runs hooks and calls
`load_scenario(document, base=...)`; successful application is not evidence
that a complete simulation or packaged wheel has been verified.

## Checklist

- [ ] Entry-point name matches the plugin name; `api_version=1` is supported.
- [ ] Template id is unique, or its built-in plugin is explicitly replaced.
- [ ] All scenario inputs are packaged and resolve within the template directory.
- [ ] Foreign hooks come from their providers; own hooks use `<plugin>.*`.
- [ ] Hooks are deterministic, validate values and never write external state.
- [ ] Default and changed knobs load successfully; source files stay unchanged.
- [ ] Inspect the installed catalog and wheel resources, then run the scenario.
- [ ] Record seeds/revisions and keep secrets out of scenarios and logs.
