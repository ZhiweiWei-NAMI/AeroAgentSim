# Install AeroAgentSim

Use Python **3.10 or newer**; P8 used Python 3.11.12 from the kernel development
interpreter. The kernel is an independent, pure-stdlib runtime distribution.
The platform's mandatory dependencies are only `aerokernel` and `PyYAML`.
Node 22/npm is required for building the optional frontend. Docker and the
native engine images are required only for external simulator runs.

## Local editable development

Keep compatible repositories as siblings:

```text
workspace/
  aerokernel/               independent kernel source
  AeroAgentSim-platform/    this repository
```

From `AeroAgentSim-platform/`:

```bash
python3.11 -m venv --symlinks .venv
source .venv/bin/activate
pip install -e ../aerokernel -e '.[server]'
pip check
python -c 'from aeroagentsim import Simulation; print(Simulation.__name__)'
aeroagentsim serve --help
```

`pyproject.toml` declares the compatible kernel version range in portable package
metadata and a relative editable path under `[tool.uv.sources]`. `uv sync --extra
server` uses that path. Pip ignores uv source configuration, so supply both
editable paths as above; the sibling kernel satisfies the version requirement.
This avoids an absolute host path in published wheel metadata. A release requires
a published compatible kernel, or a release-owner-pinned immutable VCS reference
(`aerokernel @ git+https://<actual-repository>@<commit>`). No public kernel
repository URL is invented here; the orchestrator owns publication and pins.

| Extra | Installs / purpose |
| --- | --- |
| `server` | FastAPI and uvicorn for run control, SSE feed and optional frontend hosting |
| `legacy` | SimPy, NumPy, Pint, pandas, model/API and old workbench dependencies; deprecated v1 only |
| `dev` | pytest, Hypothesis, coverage, HTTP test client, Ruff, mypy and YAML typing |
| `docs` | Sphinx, RTD theme, MyST and documentation extensions |

For example, `pip install -e ../aerokernel -e '.[server,dev]'` enables development
without installing SimPy.

### Compatible versions and reproducible CI installs

The `server` extra is bounded to the CI-validated FastAPI/Starlette range
(`fastapi>=0.115,<0.144`, `starlette>=0.46,<1.8`): newer Starlette releases
require the `httpx2` package for `fastapi.testclient.TestClient`, and Starlette
>=1.8 removes the `httpx` fallback entirely. The `dev` extra therefore declares
`httpx2>=2,<3` alongside `httpx>=0.27,<0.29` so `TestClient` works across the
whole bounded range. For reproducible CI and local installs, pass the exact-pin
constraints file: `pip install -e ../aerokernel -e '.[server,dev]' -c
constraints/dev.txt` pins fastapi 0.143.0, starlette 1.7.0, httpx 0.28.1,
uvicorn 0.54.0, pydantic 2.14.0, anyio 4.15.1, pytest 9.1.1 and hypothesis
6.168.5 (`.github/workflows/tests.yml` installs with this file). `pip check`
inside such an environment must stay clean. When the server extra is installed
without the constraints file, the upper bounds alone still resolve a compatible
set; re-pin `constraints/dev.txt` deliberately after revalidating the suite.

`from aeroagentsim import Environment` requires the
legacy dependency set and emits `DeprecationWarning`; see
[MIGRATION-v1.md](docs/platform/MIGRATION-v1.md).

## Run the kinematic slice

```bash
aeroagentsim run scenarios/p1-slice.yaml --out runs
aeroagentsim replay runs/<directory-printed-by-run>
```

The P1 slice currently selects actual AeroGraph source at
`/mnt/data2/weizhiwei/AeroGraph`, plus a hash-pinned predicate reference. That
matching checkout is an input, not a pip dependency. The compiler reads persisted
files only; do not run AeroGraph build scripts. With that input and dependencies
ready, the 22 s workload took 8.02 s in P8, and replay returned
`{"cut":1645,"ns":"22000000000","incomplete":false}`.

For another checkout location, create your own scenario copy and update its real
source references, preserving its hash pin:

```bash
export AEROGRAPH_ROOT=/absolute/path/to/AeroGraph
python - <<'PYCODE'
import os
from pathlib import Path
import yaml
root = Path(os.environ["AEROGRAPH_ROOT"]).resolve()
source = Path("scenarios/p1-slice.yaml")
document = yaml.safe_load(source.read_text())
document["registry"]["compile"]["root"] = str(root)
document["engines"]["threshold"]["config"]["native_reference"]["path"] = str(
    root / "semantic-directory/src/expanded_runtime.js"
)
Path("/tmp/p1-local.yaml").write_text(yaml.safe_dump(document, sort_keys=False))
PYCODE
aeroagentsim run /tmp/p1-local.yaml --out runs
```

A changed source hash should be reviewed and recompiled; replacing it blindly
would change the experiment. [Compiler instructions](docs/platform/aerograph-compiler.md)
explain compiling and pinning snapshots. A scenario must select either `compile`
or `snapshot` with an explicit matching `digest`. Snapshot mode removes live
registry compilation, but any explicitly pinned evaluator source remains a
required input. `scenarios/packs/logistics-small.yaml` includes a checked-in real
registry snapshot and needs no local AeroGraph checkout:

```bash
aeroagentsim run scenarios/packs/logistics-small.yaml --out runs
aeroagentsim metrics runs/<printed-logistics-directory>
```

Examples are repository assets, not bundled package resources. Run them from the
checkout or provide absolute scenario paths. Installing the wheel alone does not
install the frontend, scenarios, Docker contexts or AeroGraph data.

## Viewer and authoring

```bash
cd frontend
npm ci
npm run build
cd ..
aeroagentsim serve --out runs --scenario-root . --frontend frontend/dist
```

Open `http://127.0.0.1:8002/runs`. `/agents/<run-id>?mode=replay` displays recorded
decisions. `npm run dev` in `frontend/` serves the development UI at port 3000,
including the explicitly authored `/viewer-demo`; real run pages connect to port
8002. P7b Studio integration is concurrent work; the capability checklist records
which authoring operations are still pending. The [Studio guide](docs/platform/studio.md)
documents the opt-in `AEROAGENTSIM_STUDIO_ROOT` and `AEROAGENTSIM_AEROGRAPH_ROOT`
server settings; P7b owns that integration. The kernel server exposes `/v1`
contracts, not the historical `/api` workbench.

The old `main_for_visualization.py` forwards to `aeroagentsim serve`. It neither
installs dependencies nor spawns npm; pass `--frontend frontend/dist` explicitly.
Optional mesh packs/GLBs need verified bytes, supported frames and source attribution.
See [frontend guide](docs/platform/frontend.md).

## Native engines

Follow the [PX4/Gazebo](containers/px4-gazebo/README.md),
[SUMO](containers/sumo/README.md) and [ns-3](containers/ns3/README.md) build guides.
The scenario image tags are local development tags; their measured digests are
recorded in [adapter docs](docs/platform/adapters.md). This round did not rebuild
images or rerun Docker workloads. The host adapters are registered as distribution
entry points and can be instantiated through `Simulation`. The explicit
`python -m aeroagentsim.adapters.runner ... --containers` examples in the README
also manage startup/cleanup and scripted flight commands. Use a new journal file.

For LLM experiments, configure an actual OpenAI-compatible endpoint and model in
a scenario copy. The provided endpoint is local port 8788, not an included model
server. Runtime HTTP uses stdlib; optional keys use the configured environment
variable. No model calls are needed for replay. See [agents](docs/platform/agents.md).

## Tests and CI

```bash
pip install -e ../aerokernel -e '.[server,dev]'
pytest tests/platform tests/integrations/aerograph tests/adapters tests/agents tests/packs \
  -m 'not docker and not llm' -p no:cacheprovider
```

Full platform/decision fixtures need the matching AeroGraph source layout above.
Independent adapter/provider/compiler fixture and snapshot-pack tests run without
it. Native tests are opt-in `docker`; live model tests are opt-in `llm`.
To run historical tests install `.[legacy,dev]` and select `-m legacy`; missing
examples remain reported failures, not skips or synthesized examples.

`.github/workflows/tests.yml` checks out the independent kernel as a sibling.
Set repository variables `AEROKERNEL_REPOSITORY` (actual `owner/repo`) and
`AEROKERNEL_REF` (compatible commit). For private repositories, provide `KERNEL_READ_TOKEN` and, when needed,
`AEROGRAPH_READ_TOKEN` secrets with read access. A retained kernel submodule may replace this
second checkout; no submodule was created by P8. The default CI job runs only
independent non-Docker/non-model tests on Python 3.10 and 3.11. Set
`AEROGRAPH_REPOSITORY` and `AEROGRAPH_REF` to enable the full real-source P1/agent
job. Its runner creates the expected source-path alias; it never builds AeroGraph.
Legacy tests are separate manual checks while the historical examples are absent.

P8 used fresh `/tmp/aas-p8/venv` and `/tmp/aas-p8/legacy-venv`, derived from the
mandated Python 3.11 interpreter. Editable setuptools installs can write build
metadata into source trees, so both platform and kernel sources were copied to
`/tmp/aas-p8/{platform,aerokernel}` before installing. This kept neighboring
repositories read-only. Exact results and failure counts are in
[CAPABILITIES.md](docs/platform/CAPABILITIES.md#p8-verification).
