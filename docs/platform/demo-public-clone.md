# Run the traffic accident demo

The public demo uses committed registry snapshots and a small OSM-derived city.
It needs neither an AeroGraph checkout nor the optional city mesh pack.

```bash
git clone https://github.com/ZhiweiWei-NAMI/AeroAgentSim.git && cd AeroAgentSim
pip install -e ./aerokernel -e '.[server]'
(cd frontend && npm ci && npm run build) && aeroagentsim demo traffic-accident
```

During development the kernel lives next door: replace `./aerokernel` with
`../aerokernel`. The console imports a traffic accident workspace and prints its
URL. Start a run from that workspace, then inspect the synchronized graph and 3D
view. `--port N` selects a port; omission chooses a free local port. Playwright
Chromium is installed on first camera use if it is absent; the host also needs
Chromium's OS libraries (`cd frontend && npx playwright install --with-deps chromium`
on supported Linux distributions). `AEROAGENTSIM_CHROMIUM` selects an existing
browser explicitly.

```bash
aeroagentsim demo traffic-accident --headless
```

Headless mode needs `cd frontend && npm ci`, but no frontend build. It runs the
file scenario, writes the journal and actual PNG, and prints the committed
accident → award → capture → upload event times. The scripted kinematic profile
uploads at about 49 simulated seconds. Replay uses the journal without calling
motion, decision or camera engines.

The default console runs the file scenario's authored accident timer and closed
operator stream. Interactive operator injection needs a live stream and explicit
source progress, as described in [live ingress](realtime.md).

The default `--profile kinematic` requires no native simulator or model provider.
`--profile live-llm` uses the live LangGraph decision path: install `.[agents]`
and set `AEROAGENTSIM_LLM_BASE_URL`, `AEROAGENTSIM_LLM_MODEL`, and
`AEROAGENTSIM_LLM_API_KEY_ENV` to the name of your populated credential variable.
Actual model decisions may produce a different outcome. SUMO and PX4 profile
contracts currently require native coordinate, command and telemetry bindings;
`--profile sumo|px4` reports those missing bindings rather than running kinematics
under a native label.

Without `AEROAGENTSIM_AEROGRAPH_ROOT`, the type catalog is limited to the selected
scenario snapshot and its overlay. Setting that variable enables the full
source-based catalog. `AEROAGENTSIM_TRAFFIC_ASSET_ROOT` optionally selects the
locally held high-detail pack; those meshes/textures are excluded from this repo.
The lite city contains OSM footprints, supplied building heights and road
polylines rendered procedurally. Its attribution and regeneration instructions
are next to [scene.json](../../scenarios/demos/traffic-accident/inputs/lite-city/scene.json).
