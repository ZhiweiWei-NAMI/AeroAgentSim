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

Install both editable packages into the same environment. On Windows, activate with `.venv/Scripts/activate`.

## Camera dependencies

The traffic demo captures a real PNG through headless Chromium. Building the
console requires Node.js and npm on the host; `frontend/node_modules` is not
vendored in the repository. The capture renderer reuses the Playwright Chromium
installed via `npx playwright install chromium`; on first use a minimal set of
npm Playwright and Three.js packages is fetched into the user cache. On fresh
Linux systems, install Chromium and its system libraries explicitly:

```bash
cd frontend
npx playwright install --with-deps chromium
cd ..
```

Current Playwright downloads require a supported operating system (Ubuntu 22.04 or newer on Ubuntu). On older distributions, supply a compatible Chromium installation instead.

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

The committed registry snapshot provides the demo's types, ancestry, fields and relations. No AeroGraph checkout is needed. With access to a full checkout, set `AEROAGENTSIM_AEROGRAPH_ROOT=/path/to/AeroGraph` to enable its broader catalog; see [AeroGraph](../concepts/aerograph.md).

The demo includes OSM-derived roads and building footprints rendered as procedural geometry, with [attribution](../../scenarios/demos/traffic-accident/inputs/lite-city/ATTRIBUTION.txt). `AEROAGENTSIM_TRAFFIC_ASSET_ROOT` optionally selects a separately supplied high-detail pack. It is available as a separate download; see the optional detailed city instructions below and [Asset notes](../../ASSETS.md).

Continue with the [quickstart](quickstart.md).

## Optional detailed Huangpu city

The lightweight city works offline from a public clone. For the detailed buildings,
textures, car and X500 model, download the separate pack (about 103 MB):

```bash
aeroagentsim assets fetch
aeroagentsim assets list
```

The console and simulation camera automatically use the installed city on their
next load. The ZIP is kept outside the repository. Offline installation is supported:

```bash
aeroagentsim assets install /path/to/huangpu-city-v1.1.zip
aeroagentsim assets remove huangpu-city
```

Packs live in `$XDG_DATA_HOME/aeroagentsim/assets` (by default
`~/.local/share/aeroagentsim/assets`). Set `AEROAGENTSIM_ASSETS_DIR` to select another
location. `assets fetch --url URL` or `AEROAGENTSIM_ASSET_PACK_URL` selects a mirror;
interrupted downloads resume when the server supports HTTP Range. If the release
asset has not been uploaded, install a local ZIP instead.

`AEROAGENTSIM_TRAFFIC_ASSET_ROOT` still overrides discovery with an unpacked assets
folder containing `scene.json`. Clear it to return to automatic pack selection.
The pack includes an owner-supplied NOTICE with OpenStreetMap attribution; the
building-mesh and texture licence must be stated by the project owner.
