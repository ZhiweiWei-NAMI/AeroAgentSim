# Install

Use Python 3.10 or newer, Node.js 22 and npm. Python 3.11 is a convenient development choice. The released repository includes the kernel under `aerokernel/`.

```bash
git clone https://github.com/ZhiweiWei-NAMI/AeroAgentSim.git
cd AeroAgentSim
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ./aerokernel -e '.[server]'
cd frontend
npm ci
npm run build
cd ..
```

In a current development checkout, replace `./aerokernel` with `../aerokernel`. Install both editable packages into the same environment. On Windows, activate with `.venv/Scripts/activate`.

## Camera dependencies

The traffic demo captures a real PNG through headless Chromium. It needs frontend dependencies even with `--headless`. The demo reuses installed Playwright Chromium or installs it on first use. On fresh Linux systems, install Chromium and its system libraries explicitly:

```bash
cd frontend
npx playwright install --with-deps chromium
cd ..
```

To use an existing installation, set `AEROAGENTSIM_CHROMIUM` to its executable path. A missing executable is an error. Building the console creates `frontend/dist`; the default demo command requires that directory.

## Optional capabilities

| Extra | Purpose |
| --- | --- |
| `server` | HTTP service and console hosting |
| `agents` | LangGraph and OpenAI-compatible live model client |
| `dev` | Python tests, typing and lint tools |
| `docs` | Documentation build dependencies |

For live agents, install `python -m pip install -e '.[server,agents]'` and follow [Deploy an agent](../guides/agents.md). PX4/Gazebo, SUMO and ns-3 run as separate services; see [Containers](../guides/containers.md). Docker is optional for the default kinematic experiment.

## Included data

The committed registry snapshot provides the demo's types, ancestry, fields and relations. No AeroGraph checkout is needed. With access to a full checkout, set `AEROAGENTSIM_AEROGRAPH_ROOT` to enable its broader catalog; see [AeroGraph](../concepts/aerograph.md).

The demo includes OSM-derived roads and building footprints rendered as procedural geometry, with [attribution](../../scenarios/demos/traffic-accident/inputs/lite-city/ATTRIBUTION.txt). `AEROAGENTSIM_TRAFFIC_ASSET_ROOT` optionally selects a separately supplied high-detail pack. It is not distributed here. See [Asset notes](../../ASSETS.md).

Continue with the [quickstart](quickstart.md).
