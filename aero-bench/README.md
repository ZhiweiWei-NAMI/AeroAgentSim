# AeroBench 0.1

This directory is the simulation platform inside
[ZhiweiWei-NAMI/AeroAgentSim](https://github.com/ZhiweiWei-NAMI/AeroAgentSim).
Platform source is maintained in this repository's `main` branch. Python,
container and browser components share its resolved-run contracts.

Start the retained local delivery from the repository root:

```bash
./run-aero-bench
```

Open <http://127.0.0.1:5416/?view=live>, log in under `正式运行控制`, load the
catalog and choose `打开已封存回放`. City Studio is at
<http://127.0.0.1:5416/city-studio.html>. Forward only port 5416 for a remote
browser; `/v1` uses the frontend origin. Private bootstrap values stay in
`credentials/platform-0.1.json` with mode 0600. The safe pointer is
`validation/platform-0.1/connection.json`.

The [platform guide](docs/p02-native-logistics/platform-0.1.md) covers
prerequisites, configuration, physical reproduction and the preserved
failure/recovery records. The [evidence manifest](docs/p02-native-logistics/platform-0.1-evidence.json)
identifies the measured results. Local run data is under
`validation/platform-0.1/`; the recorded video is
`final-watchable/native-parcel-0.1.mp4`, also served at
<http://127.0.0.1:5416/platform-0.1/native-parcel-0.1.mp4>.

## Source layout

- `aero_bench/`: authoring, compilation, executors, providers, tasks,
  authoritative state, sealing and public trace projection.
- `containers/`: explicit OCI workload build inputs.
- `frontend/`: City Studio, authenticated run control and public replay.
- `schemas/`: generated contracts.
- `tests/`: module and integration checks.
- `docs/p02-native-logistics/`: usage, evidence identities and pose calibration.

Install Python dependencies with `python -m pip install -e '.[test]'` and
frontend dependencies with `npm ci --prefix frontend`. Native physical runs
also require a working Docker daemon and the declared digest-pinned images.
Test fixtures and command receipts are not formal execution evidence.
