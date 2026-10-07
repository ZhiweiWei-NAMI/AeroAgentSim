# AERO-BENCH native parcel integration

Platform 0.1 setup, UI execution and evidence replay: [native parcel guide](docs/p02-native-logistics/platform-0.1.md).

This directory contains the AERO-BENCH runtime used by AeroAgentSim's native
parcel workflow. It keeps the integration under one project root so Python,
container, schema, and browser builds use the same contracts.

## Layout

- `aero_bench/`: resolved-run contracts, authoring, executors, providers,
  runtime hooks, public trace projection, agents, and the native parcel task.
- `containers/`: logistics business, parcel participant, and parcel verifier
  build contexts.
- `schemas/`: generated JSON Schema and OpenAPI contracts.
- `frontend/`: the public run viewer and native parcel presentation.
- `tests/`: the provider, task, control, and integration tests required by this
  delivery.
- `docs/p02-native-logistics/calibration-review/`: the reviewed pose
  calibration inputs and manifest.

## Python checks

Run commands from this directory:

```bash
python -m pip install -e '.[test]'
python tools/generate_contracts.py --check
pytest -q \
  tests/providers/test_native_parcel_business_wiring.py \
  tests/providers/test_native_parcel_provider_retry.py \
  tests/tasks/test_logistics_signed_pose_reference.py \
  tests/tasks/test_native_parcel_clock_integration.py \
  tests/tasks/test_native_parcel_hook_projection.py \
  tests/tasks/test_native_parcel_integration.py \
  tests/tasks/test_native_parcel_review_regressions.py \
  tests/tasks/test_native_parcel_rpc.py \
  tests/tasks/test_native_parcel_runtime.py \
  tests/tasks/test_native_parcel_stage.py \
  tests/tasks/test_native_parcel_stage_integration.py \
  tests/tasks/test_native_parcel_verifier.py \
  tests/test_control_start_discovery.py
```

## Frontend checks

```bash
cd frontend
npm ci
npm run typecheck
npm test -- \
  src/app.native-presentation.test.ts \
  src/city-spatial-road-clearance.test.ts \
  src/city-studio.test.ts \
  src/control-reconnect-ui.test.ts \
  src/map.native-presentation.test.ts \
  src/native-city-presentation.test.ts \
  src/native-parcel-view.test.ts \
  src/p02-mission-records.test.ts \
  src/run-start-identity.test.ts \
  src/run-store.test.ts
npm run build
```

Container builds use this directory as their build context. Formal runs must
use the resolved bundle and digest-pinned images selected by the executor.
