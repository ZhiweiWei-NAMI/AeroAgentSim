# Scenario resource packaging

## One authoritative source tree

Maintain built-in resources under `scenarios/`, not under `src/aeroagentsim/`.
The installed resource directories are **generated build output**, never a
second maintained copy.

| Authoritative input | Installed wheel location |
| --- | --- |
| `scenarios/templates/<id>/**` | `aeroagentsim/template_data/<id>/**` |
| `scenarios/templates/huangpu-placements.json` | `aeroagentsim/template_data/huangpu-placements.json` |
| `scenarios/demos/traffic-accident/**` | `aeroagentsim/demo_data/traffic-accident/**` |

Three packaging layers cooperate:

1. [`MANIFEST.in`](../../MANIFEST.in) grafts the complete authoritative trees
   into source distributions, so a wheel built from an sdist has its inputs.
2. [`setup.py`](../../setup.py)'s `build_py` copies both complete trees into
   the package build directory, preserving relative paths and file contents.
   It includes root-level shared files as well as nested directories, and
   fails if an authoritative tree is absent. Python bytecode/cache files are
   excluded.
3. [`[tool.setuptools.package-data]`](../../pyproject.toml) declares recursive
   `template_data/**/*` and `demo_data/**/*` resource patterns, alongside
   existing package-local assets. These patterns do not themselves copy
   resources from outside `src/`; the build command performs that staging.

Do not narrow inclusion to selected extensions or shallow globs. Resources
include manifests, scenario YAML, registry snapshots, inline or separate
behaviours, fixtures, thumbnails, README files, profiles, prompts, attributions,
OSM source inputs and nested city assets. Shared `huangpu-placements.json` is
used by the assistant's place catalogue; copying only template directories
silently loses it.

Built-in plugin factories prefer installed resources, then fall back to the
checkout for source/editable use. `traffic-accident` intentionally keeps its
manifest/thumbnail/README under `template_data` but resolves its scenario and
relative inputs through `demo_source()` under `demo_data`. Do not duplicate
that scenario into the template directory to compensate for missing packaging.
Normal unpacked filesystem wheel installations are supported; this gate does
not establish zip-import resource lifetimes.

## Adding or extending a template

1. Add `scenarios/templates/<id>/template.yaml`, a complete `scenario.yaml`,
   registry snapshot, thumbnail and README. Keep relative dependencies and
   any nested assets in the authoritative tree. For a deliberately shared demo
   source, use the existing `TemplateSource.scenario` override pattern.
2. Register the built-in factory in `aeroagentsim.scenarios` and declare knobs
   through the existing scenario API. Reusable pure hooks belong to their
   domain provider; discovery/apply must not launch engines, download assets
   or call providers. See the [scenario-plugin guide](../guides/scenario-plugins.md).
3. Keep resource paths relative to the scenario base, or resolve them from
   installed resources. Do not introduce checkout-only absolute paths. Check
   both nested files and shared files at the template-tree root.
4. Update the explicit built-in ID baseline in
   [`test_installed_scenario_plugins.py`](../../tests/authoring/test_installed_scenario_plugins.py)
   when intentionally adding/removing a built-in. Its resource inventory is
   generated recursively from source, so new file types/depths automatically
   become required wheel contents. No duplicate resource manifest is needed.
5. For an external distribution, package resources in its own namespace,
   declare package-data patterns covering its complete resource closure, and
   register an `aeroagentsim.scenarios` entry point. The
   [example plugin](../../examples/plugins/aeroagentsim-example-scenario/README.md)
   owns its own manifest, scenario and snapshot; it does not modify the host.

Keep optional native services, licensed detailed city packs, operator-supplied
models and rescue participation filters outside the shipped resource closure.
A template listing is not evidence that these capabilities are installed.

## Installed-wheel gate

When execution is authorized, run from an environment with pytest, pip, build
tooling and compatible runtime dependencies already available (currently
`aerokernel>=0.2.0,<0.3` and `pyyaml>=6`):

```sh
python -m pytest -m slow tests/authoring/test_installed_scenario_plugins.py
```

The `slow` marker is already registered in `pytest.ini`. This test:

- Stages clean project/example build inputs in temporary directories, avoiding
  stale `build/` and egg-info files and leaving the checkout untouched.
- Builds real platform and example wheels with `python -m pip wheel --no-deps`.
  If build-dependency acquisition fails, retries with `--no-build-isolation`
  to use local build tools offline; isolated backend-hook failures do not get
  this fallback. Only recognized missing pip/setuptools/wheel failures,
  independently confirmed by a tooling probe after the retry, skip with build
  logs and a clear reason. Other build errors,
  installation errors and authoring failures fail the gate. A skip is not a
  packaging pass.
- Compares every authoritative template/demo/example resource's SHA-256 with
  its wheel member, including README/thumbnail and deeply nested assets that
  `apply()` need not open.
- Installs both wheels with `pip install --no-index --no-deps --target <tmp>/site`,
  without a venv or dependency downloads during installation.
- Runs a child process outside the checkout with `PYTHONPATH` set only to that
  target. Python `-I` ignores `PYTHONPATH`, so the child explicitly inserts the
  target after removing checkout root/platform `src`/example paths. It asserts
  the source paths are absent, package imports and distribution metadata come
  from the target, and `example-patrol` is a real installed entry point.
- Requires the ten built-ins plus `example-patrol` in `TemplateRegistry().list()`,
  checks each `plugin_of`, installed asset directory and scenario base, and
  calls `apply()` with default knobs for all eleven. Unrelated installed
  templates may coexist; they cannot satisfy the required provenance checks.
- Applies a nondefault installed example patrol interval and interactive mode,
  checking actual schedule/ingress changes from the external and shared hooks.
- Checks resource bytes before discovery and after all applies to catch input
  mutation, and verifies loaded platform modules came from the target.

This is **platform-source isolation**, not a hermetic dependency environment or
an untrusted-plugin sandbox. The interpreter's installed dependency locations
remain available (including an independently installed/editable kernel).
Target package provenance assertions prevent an ambient editable platform from
standing in for the wheel. An ambient duplicate `example-patrol` entry point
is an error, not silently filtered metadata.

## Evidence and remaining limits

This change was authored and reviewed statically under a generate-only rule:
**no wheel build, package installation or test was executed**. The gate's
actual pass/fail and offline fallback remain unverified until an authorized run.
The older demo packaging test invokes only `build_py`; it is not installed-wheel
acceptance.

The new gate covers directly built wheels. `MANIFEST.in` supports source
resource inclusion, but an **sdist → wheel → install** round trip still needs a
separate release check. CI wiring and release version assignment remain with
the maintainer; scenario API v1 and the existing package version are unchanged.
Pin the actual release/artifact revision, not an assumption that every package
bearing the existing version has the same plugin implementation.

`apply()` loads and validates authoring documents, snapshots and behaviours;
it does **not** execute simulations or open every runtime-only input. In
particular, some raw decision-fixture references retain checkout-prefixed
paths; traffic hooks/workspace import rewrite paths in their respective
flows, but direct installed raw-scenario execution is not proven here. Shipping
the fixture bytes does not establish runtime path portability.

No native simulator, browser rendering, live model call, complete mission,
replay fidelity or scientific validity is established by this gate. Host-side
configuration acceptance is not native acceptance, geometric browser depth is
not physical ranging, and an admission ACK is not mission completion.
