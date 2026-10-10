# P02 branch integration review

Reviewed on 2026-10-10 against `low_altitude_sim` at
`da790c2ece3b71e962cfa2152acc4ca6210e308f`.
Source: `feature/p02-bench-native-integration-20261005` at
`fa0c822`. The common ancestor is `a4f49e8b`.

## Destination and retained scope

This integration belongs to the existing AERO-BENCH workbench under
`low_altitude_sim`. It does not replace the separate AeroAgentSim 2.0 platform
on `main`. A three-way merge preserves the target branch's other work.

The 11 reviewed source commits are `e8390a3`, `ec16cf3`, `6528071`,
`22b7dfd`, `5ff8eff`, `dea2691`, `4c783b7`, `17aa46a`, `ad5f7c4`,
`8e65431`, and `fa0c822`.

Retained live changes include suite-relative compilation inputs, shared
rate-limited asset requests, persisted non-secret Start identities, separate
inspection mission records, reachable live controls, and localized navigation.
The old unbound-order presentation is replaced by the dedicated mission-record
section: records without declared order identities do not become parcel orders.
The native presentation test double now supplies the diagnostic fields already
present on the real RunSession.

All source and evidence checkpoint packets remain in their documented locations.
They preserve the chronology, including earlier defects and later scoped repairs.
They are not installed into the active runtime. Native parcel activation, service
wire tests, declared launch pins/pads, independent verifier acceptance, and live
flight acceptance remain outside this branch merge. Historical pass counts and
screenshots are not fresh acceptance evidence.

## Isolated erroneous delta

The incoming `native-city-presentation.ts` change unconditionally fetched the
viewer terrain texture library while loading a native scenario whose asset
contract does not declare that library. It bypassed the supplied run asset
transport and caused all three existing native loader/disposal/tamper tests to
fail with an invalid `/textures/terrain-v1/manifest.json` URL before reaching
their intended checks.

Only that terrain-texture wiring delta was excluded. The complete existing
native city loader, declared geometry, flat measured material colors, disposal,
and digest checks are retained. All six native presentation tests pass.
The original proposed texturing code remains recoverable in the source history.
No other live implementation was removed.

## Fresh validation

Environment: Python 3.12, Pydantic 2.11.3, pytest 8.4.2, locally installed SUMO
1.24.0; Node 24.19.0 and the committed frontend npm lockfile. SUMO is used only
by unit fixtures; no native flight or formal provider run was performed.

From `aero-bench/frontend`:

- `npm ci --ignore-scripts`: passed.
- `npm test -- src/control-asset-queue.test.ts src/run-start-identity.test.ts src/run-store.test.ts src/control-replay-source.test.ts src/p02-mission-records.test.ts src/viewer-chrome.test.ts src/native-city-presentation.test.ts src/city-preview-scenes.test.ts src/app.native-presentation.test.ts --maxWorkers=2`: 85 passed across nine files.
- `npm test -- --reporter=dot --maxWorkers=4`: 1,297 passed, 39 failed, 48 skipped; one unhandled Blob-related error. An independent untouched target checkout produced 1,280 passed, the same 39 failures and 48 skips, and the same unhandled error. The exact failure headings match. The broad suite is not green: missing private assets and existing UI/test failures remain.
- `npm run typecheck`: blocked by the same four errors as the untouched target: missing `CityCompiledSelection.draftSha256` and three missing private OSM JSON imports. No new typecheck errors were introduced. The aggregate `npm run build` therefore remains blocked at its typecheck stage.
- `node_modules/.bin/vite build`: bundling passed, with the existing large-chunk warning. This separate bundling check is not an aggregate build pass.

From `aero-bench`:

- `python -m pytest tests/authoring/test_draft_compiler.py tests/authoring/test_city_inspection_registration.py -q --tb=short`: 45 passed; nine city-registration setup errors because `validation/codex-takeover-20261001/B/huangpu-native-public-scenario-v6.json` is absent. The same nine setup errors reproduce on the untouched target.
- All preserved native-parcel Python snapshot files parse successfully.
- All 75 entries in the stage-projection, provider-retry, clock-integration,
  complete-source, and first-three checkpoint manifests match their recorded
  SHA-256 values.
- Whitespace checking passes for changed live source and tests. Historical
  logs and patch packets retain their original whitespace for evidence integrity.

These checks support the bounded source integration. They do not establish a
fully passing repository, private-asset visual acceptance, or a live parcel run.
