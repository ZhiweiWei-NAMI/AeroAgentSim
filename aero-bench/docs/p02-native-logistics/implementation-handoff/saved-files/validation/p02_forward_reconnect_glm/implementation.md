# P02 Forward Catalog Recovery — Implementation Record

Leaf: GLM (glm-5.3-flash), isolated worktree
`/mnt/data1/weizhiwei/AERO_WORLD_runtime/p02_aeroagentsim_native_integration_20261005/aero-bench`.
Scope: forward, non-secret Start identity discovery through the existing
authenticated catalog and the existing idempotent Start. No new endpoint, no
auth-policy change, no run started, no service touched, no delegation. The
native same-seal inspection run `run9e07a000` was not accessed in any way; its
historical start_id is treated as unrecoverable and out of scope.

## Backend patch (smallest actual change)

### `aero_bench/control/contracts.py`
`CatalogRun` gains one nullable non-secret field:

```python
# Non-secret Start idempotency identity, populated only while this
# process still manages this catalog's exact run; an uncreated run is
# null. Never carries credentials.
start_id: Identifier | None = None
```

Static catalog/run resolution is unchanged; the field is additive and
defaults to null.

### `aero_bench/control/manager.py`
`ControlRunManager.catalog` is now rebuilt per request under the existing
manager lock (`self._lock`), copying the static resolved entries and filling
`start_id` from `self._managed[run.run_id].start_id` for the exact run IDs of
this compilation/catalog that this process still manages. Uncreated runs —
including everything a previous process created — stay null. The rebuild is
necessary because `ControlCatalog`/`CatalogRun` are frozen models; caching a
mutated instance is impossible without mutation. `__init__` additionally
retains `self._suite_id` / `self._suite_sha256` for the rebuild. No other
manager behavior changed: `start()` already deduplicates through
`self._starts` (same start_id + run_id replays the cached `_start_response`
without a new thread or execution) and refuses a start_id reuse for another
run with `start.id_conflict`.

### Generated contracts (existing generator, no hand edits)
`/home/weizhiwei/data/iiot_predict/iiot_py311/airfogsim/bin/python
tools/generate_contracts.py --write` regenerated:
`schemas/generated/aero-bench-contracts.schema.json`,
`schemas/generated/control-catalog.schema.json`,
`schemas/generated/control-api.openapi.json`,
`schemas/generated/manifest.json`,
`frontend/src/generated/schemas/aero-bench-contracts.schema.json`,
`frontend/src/generated/schemas/control-catalog.schema.json`,
`frontend/src/generated/aero-bench-contracts.ts`.
`CatalogRun.start_id` is optional `string | null` (`StartId`); the pre-existing
request-side `StartId` alias was renamed `StartId1` by the generator; no
source references the old name (grep-verified). `tools/generate_contracts.py
--check` passes: "checked 90 generated contract files".

## Frontend wiring

### `frontend/src/run-start-identity.ts`
- Added `importServedStartId(runId, servedStartId)`: validated 64-hex run id;
  **null served identity is a no-op** (`"unchanged"`) — a null catalog entry
  proves nothing because the Start HTTP response carrying the identity may
  have been lost after the run was created, so a saved pre-request identity is
  preserved and stays reusable idempotently (no eviction); a malformed served
  identity throws `"Served start identity is malformed"` explicitly; a non-null
  served identity is the authoritative current service identity: equal →
  `"unchanged"`, no saved ID → `"imported"`, different saved ID → `"superseded"`
  (persisted), so a stale saved ID never silently survives the service's
  authoritative statement. Credentials never pass through the store.
- Added `get(runId)` (validated saved identity or null; throws the existing
  scope-mismatch error for foreign-scope data). The removed-identity method
  from an earlier draft was dropped together with the null-eviction semantics.
- Storage type stays `Pick<Storage, "getItem" | "setItem">`.

### `frontend/src/run-store.ts`
- `loadCatalog` success path imports the served start_id of **every** catalog
  row into the scoped `RunStartIdentityStore` (only when a store is provided;
  there is no skip: a fresh browser with empty localStorage must discover a
  non-null served identity). An import refusal (malformed/out-of-scope served
  identity) throws inside the try block and surfaces as `catalogError`; the
  catalog is **not** adopted into state, so nothing can be selected or started
  from it.
- New state field `discoveredStartId`: the non-secret start_id the
  authenticated catalog acknowledged for the selected run under the same
  service/compilation, or null when it did not (yet). Populated by
  `selectRun`; never a credential, never invented.
- `start()` keeps `getOrCreate` (persist-before-request preserved): the saved
  scoped identity is reused so `POST /v1/runs` stays idempotent across
  reloads; a new one is generated only when none is saved. After
  `loadCatalog`, the saved identity is the authoritative catalog identity for
  the served non-null case; for the null case the local pre-request identity
  is preserved by design (null is a no-op in the store, never an eviction).

## Tests

### Backend — `tests/test_control_start_discovery.py` (new, 8 tests)
Mechanical fixture via `tests.support.build_bundle` + `resolve_bundle`
(executor profile `docker_reference`), `ControlRunManager` built with a
stub executor whose plans are rejected (no workload ever runs; the stub is an
interface test, not mission evidence):
1. `test_catalog_start_id_is_null_until_the_run_is_created` — all null before,
   exact `start.forward-reconnect` after `manager.start`.
2. `test_catalog_start_id_is_scoped_to_the_created_run_only` — other catalog
   runs of the same compilation stay null.
3. `test_catalog_serializes_no_credentials` — `model_dump(mode="json")` of the
   catalog contains neither token value nor `operator_token`/`csrf_token` keys.
4. `test_start_with_the_same_start_id_replays_without_a_new_execution` — same
   `_ManagedRun`, same thread object, one `_starts` entry, same credentials.
5. `test_start_with_an_unknown_run_id_fails_explicitly` —
   `catalog.run_unknown`, nothing managed, catalog stays null.
6. `test_start_id_reuse_for_another_run_fails_explicitly` —
   `start.id_conflict`; refused run's identity stays null.
7. `test_malformed_start_id_is_refused_by_the_contract` — `ValidationError`.
8. `test_shutdown_terminates_the_run_started_for_discovery` — thread joined.

### Frontend
`frontend/src/run-start-identity.test.ts` (+5): import + supersede of a stale
saved ID; import with no saved identity leaves foreign-scope storage
untouched; malformed served identities (`"start."`, `"start..double"`,
`"START.UPPER"`) throw explicitly; **null served preserves the saved
pre-request identity**; invalid run identity rejected.
`frontend/src/run-store.test.ts` (+5): reload + catalog-served `start_id` →
Start reuses it (a different generated ID is never sent); cross-console
discovery with empty localStorage and no saved identity (`discoveredStartId`
populated by `selectRun`, the served ID is what `POST /v1/runs` sends);
malformed served identity → `catalogError` matching `/malformed/`, catalog
not adopted, `start()` rejects with "no catalog run is selected", HTTP Start
never called; catalog serves null → saved pre-request ID preserved and
reused; lost Start response (request sent, identity persisted, response
rejected) followed by reload against a null-serving catalog → the persisted
pre-request identity is replayed, no second identifier is generated.

## Commands and results

```
/home/weizhiwei/data/iiot_predict/iiot_py311/airfogsim/bin/python -m pytest \
  tests/test_control_start_discovery.py -q
  → 8 passed

/home/weizhiwei/data/iiot_predict/iiot_py311/airfogsim/bin/python -m pytest \
  tests/test_control_start_discovery.py tests/test_control_manager_shutdown.py \
  tests/test_control_sealed_replay.py tests/test_gateway_service.py \
  tests/test_control_client_proxy.py tests/test_control_public_replay.py \
  tests/test_authoring_api_traffic_preview.py -q
  → 61 passed (regression sweep over manager/catalog/gateway consumers)

/home/weizhiwei/data/iiot_predict/iiot_py311/airfogsim/bin/python -m pytest \
  tests/test_run_stack.py -q
  → 28 passed

/home/weizhiwei/data/iiot_predict/iiot_py311/airfogsim/bin/python \
  tools/generate_contracts.py --check
  → checked 90 generated contract files

cd frontend && npx tsc --noEmit
  → clean

cd frontend && npx vitest run src/run-store.test.ts \
  src/run-start-identity.test.ts src/run-control.test.ts
  → 49 tests passed across the three files
    (run-store 16, run-start-identity 8, run-control 25; the run-store and
    run-start-identity files contain the new discovery tests). Files run via
    a temp vitest config outside the repo because frontend/node_modules
    symlinks to a read-only location and vitest needs a writable cacheDir;
    the repo's vitest.config.ts was not modified.
```

## Files changed (all within the declared writable list)

- `aero_bench/control/contracts.py`
- `aero_bench/control/manager.py`
- `tests/test_control_start_discovery.py` (new)
- `schemas/generated/{aero-bench-contracts,control-catalog}.schema.json`,
  `schemas/generated/control-api.openapi.json`, `schemas/generated/manifest.json`
- `frontend/src/generated/schemas/{aero-bench-contracts,control-catalog}.schema.json`,
  `frontend/src/generated/aero-bench-contracts.ts`
- `frontend/src/run-start-identity.ts`, `frontend/src/run-start-identity.test.ts`
- `frontend/src/run-store.ts`, `frontend/src/run-store.test.ts`
- this file

Not touched: `frontend/src/app.ts`, `i18n.ts`, `p02-mission-records.*` and
their tests (other writer), verifier/compiler files, `main.ts`,
`viewer-chrome.ts`, `docs/`, any auth or runtime configuration.

## Remaining gap

- The catalog-import path is wired only in `RunSession`. UI surfacing of
  `discoveredStartId` and any user-facing "reconnect" affordance belong to the
  coordinator-released UI files and are intentionally not implemented here.
- The full frontend suite currently reports failures in the
  city-presentation / osm2world / map / app domains (~39 tests, 33 files).
  These are unrelated to this slice: `frontend/public/` is untracked/absent on
  this worktree (`public/city-presentation/default-scene-v1.json` missing),
  the failing files have no runtime import edge to `run-store` /
  `run-start-identity` / generated contracts (only `import type`), and none of
  those files are in this leaf's writable list. Not investigated further, per
  ownership.
- End-to-end verification against a live gateway was not performed (no run
  may be started). The idempotent replay and catalog behavior are covered by
  the manager-level tests above.
