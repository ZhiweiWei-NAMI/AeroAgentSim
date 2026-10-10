# Shared entity/state binding adapter: native fixture prototype

This dependency-free Python prototype sits before the existing Atlas evaluator. It does not change Atlas's scalar/temporal rules, introduce another ontology layer, write the parcel frontend, or connect to a server/controller.

## Verified result

- With external native Atlas supplied, 64 executable tests pass: 30 binding/native integration tests and 34 fixture-ledger tests.
- Without Atlas, 42 standalone tests pass and 22 native-dependent tests are explicitly skipped.
- Seven JSON cases use one preparation path for parcel, UGV, UAV, facility, human, network resource and compute resource.
- Real Atlas evaluation is invoked on prepared flat values. The explicit four predicate catalogs load 1,420 native targets without compile errors. This is not a full-catalog coverage claim.
- JSON ledger demo: competing transfers produce `committed` / `stale_precondition`; overlapping resource bundles produce `admitted` / `stale_precondition` / `capacity_exceeded` after re-reading revisions.
- All data is hypothetical. No physical action, live provider integration, distributed authority, server access, deployment or physical control was performed. This repository addition publishes only the standalone source prototype.

## Run

Python 3.10+; Python standard library only. Supply the actual existing Atlas `observable_v2` root.

```sh
cd validation/predicate-binding-prototype
ATLAS_ROOT=/path/to/observable_v2 \
  PYTHONDONTWRITEBYTECODE=1 python run_checks.py
python demo.py --atlas-root /path/to/observable_v2
```

`run_checks.py` writes `test_results.json` with actual test names and outcomes. `demo.py` reads the checked-in JSON inputs and writes local `demo_results.json`. Native Atlas is a caller-supplied external dependency and is not bundled. Without `ATLAS_ROOT`, tests needing its evaluator are explicitly skipped; standalone contract and ledger checks still run. With a supplied but invalid path, loading fails instead of silently skipping.

## Compact API

1. `Ref(run_id, epoch, id, generation, ref_type='object')`: opaque exact identity; dots are literal. Relation refs use `ref_type='relation'`.
2. `ObjectRecord(ref, kind, lifetime, capabilities)`: immutable lifecycle and issuer/scoped/valid capability claims. Capabilities determine applicability, not action permission.
3. `StateCell(...)`: typed scalar or structured ref, unit, coordinate frame, sample and valid times, named evidence domain, observation/availability commit, availability time, authority, source, revision and provenance. Optional explicit supersession, never last arrival wins.
4. `RelationRecord(...)` and `ScopeCoverage(...)`: ordered role-to-Ref endpoints plus valid/available boundaries. Positive existence can be proven from one valid row. Absence requires matching, complete, current, exact-scope coverage.
5. `Snapshot(objects, cells, relations, coverage)`: ingest once, reject malformed values/duplicate identities, index exact keys. No repository discovery. Input collections and policies are copied into immutable structures.
6. `CompiledBinding(Binding(...))`: validate explicit signature and assign independent `slot_0`, `slot_1`, … fields once. The binding key includes target revision, all generations/endpoints, parameters, scenario and observation policy, binding epoch and evaluation revision.
7. `prepared = compiled.prepare(snapshot, query_point)`: input validation and projection; missing/stale/conflicting leaves become native `None` with small reason/evidence sidecars.
8. `compiled.evaluate(engine, prepared, history=())`: calls the unchanged native `engine.evaluate` with an exact `field_map`, explicit same-binding history and no owner aliases. No action dispatch.

```python
from json_io import read_document, load_case
from adapter import load_atlas

engine = load_atlas('/explicit/path/to/observable_v2')  # once at bootstrap
fixtures = read_document('fixtures.json')
snapshot, binding, query = load_case(fixtures, fixtures['cases'][1])
prepared = binding.prepare(snapshot, query)
result = binding.evaluate(engine, prepared)
assert result['result'] is True
```

`load_atlas` reads only caller-specified catalog paths, defaulting to four explicit predicate files. It imports native operator dependencies normally; it does not recursively discover repository files. Reuse the engine and compiled bindings across frames. A binding fingerprint is computed once; listed-file fingerprints in demo output are a one-time handoff manifest, not per-row audit gates.

### Time and unknown rules

All input time values are integer nanoseconds. Event time is separate from `available_ns` in the named evidence domain. A cell is usable only when both its availability time and its availability commit are within the query cutoffs; neither is compared numerically with event time.

- `after_barrier`: interval membership is `start <= t < end`; sample time must be `<= t`.
- `strict_left`: membership is `start < t <= end`; sample time must be `< t`. This applies consistently to lifecycles, capabilities, cells, relations and coverage.
- No default hold/interpolation; each StateCell declares validity. No lifecycle termination is interpreted as event recovery.
- Native Atlas uses float seconds. The bridge rejects unsupported large/precision-losing nanosecond times instead of allowing distinct history samples to collapse. Use an explicitly declared relative simulation clock; the bridge never silently rebases time.
- Engine truth remains true / false / unknown. Unbound/unsupported/invalid are separate binding statuses. Ordinary missing leaves preserve native strong-Kleene decisive conjunction/disjunction behavior.
- A structural relation direction/schema/integrity error invalidates the binding; it cannot be hidden by another decisive Boolean leaf.
- Histories must be explicit, strictly earlier, unique, same-binding samples with compatible evidence cutoffs. No automatic history truncation is performed. Episode rules may require full-epoch history.
- Preserve old `Prepared` rows for as-observed decisions. Use a new `evaluation_revision` and separately prepared history for retrospective corrections. This read-only prototype does not retain decisions or execute replayed actions.

Conversions are explicit and small: km/h→m/s, decimal MB/s→bit/s and Mbit/s→bit/s. Other unit/frame mismatches are unavailable. Boolean-as-number, non-finite input values, overflowing conversions and ambiguous supersession are rejected.

### Fixture authority API

`FixtureLedger(authority_id, authority_epoch)` provides one in-memory lock and immutable records:

- `initialize_custody`, `prepare_transfer`, `observe_transfer_evidence`, `commit_transfer`.
- `mark_executing`, `mark_execution_indeterminate`, `transfer_status`, `reconcile_transfer`.
- `observe_contacts` is independent of exclusive responsible custody; multiple contacts are valid.
- `register_resource`, `publish_occupancy_snapshot`, `reserve_bundle`, `capacity_conflicts`, `update_capacity`.
- Writes require the exact `AuthorityToken` and expected custody/resource revision where applicable. Rotation fences stale writers. This token is a fixture fencing model, not authentication.

Transfers bind parcel/source/target/task generations and transaction ID to release/acquire evidence. Replaying the same committed transaction returns its existing result; altered payload/evidence collides. Missing execution acknowledgement remains `execution_indeterminate` until the same transaction is reconciled. There is no physical release, retry, attachment change or rollback API. Custody may be known unassigned (`Custody(holder=None)`) or unknown (no record); these differ.

Resource reservations commit all requested resources or none. Half-open interval sweeps use exact `Fraction` arithmetic and every declared capacity dimension. Explicit complete occupancy coverage must span the request. Matched claim/occupancy consumes the greater amount once; unmatched occupancy remains additive. Expired reservations do not erase observed occupancy. Priority never makes an occupied resource vacant. Existing observations and claims survive capacity shrink; conflicts include witnesses. `CapacityViolation.total` is a Fraction and is serialized as text in demo output.

Atomicity is per ledger method/resource bundle. This is not an atomic transaction over physical actions, or a completed end-to-end custody-plus-resource workflow. Upstream fixture policy supplies valid lifecycle/capability/authorization decisions before ledger calls; the ledger rejects self-custody and relation-typed object endpoints but is not a general permission service.

## Artifacts

- `contracts.py`, `adapter.py`, `json_io.py`: input contract and native evaluation bridge.
- `ledger.py`: isolated fixture-only authority.
- `fixtures.json`, `ledger_fixtures.json`: executed, portable JSON inputs.
- `test_adapter.py`, `test_ledger.py`, `run_checks.py`, `verification_native.json`, `verification_standalone.json`: regression suite and actual results.
- `demo.py`: JSON-to-external-Atlas and authority demos; results are generated locally.
- `parcel_mapping.json`: verified shape mapping for the existing frontend, with live-provider gaps explicit. Mapping only; frontend unchanged.
- `HANDOFF.md`: smallest cloud/server integration sequence and remaining limits.

## Audit-derived acceptance scope

The tests reuse the review counterexamples: literal dotted IDs; stale human state; UGV unit conversion; future-visible UAV evidence; renderer rejection; conflicting sources/revisions; reused compute generation; wrong pair and directional links; generation history leakage; capability applicability; incomplete occupancy; competing custody; lost acknowledgement; stale authority; cross-resource write skew; reservation expiry; strong Kleene with a missing custody leaf. Independent review added mutability, numeric overflow, positive-existence, native time precision and strict-left regressions.

This does not implement every normative scenario in the reviews. Real stage readiness, source scene-digest joins, ns-3 flow/cohort semantics, task-phase action gates, physical return/landing, effect verification, command deduplication, provider authentication and controller policies remain outside this prototype.

## Explicit trust boundaries

The caller supplies the genuine schema/quantity semantics, authority allowlists, lifecycles, complete-coverage certificates and comparable clocks. This fixture code cannot prove those claims are true. `target_revision` is caller-pinned metadata: the binding key includes it, but the bridge does not authenticate that label against every transitive native definition. Pin the engine/catalog deployment once at bootstrap and keep it immutable for the binding's lifetime; changing definitions requires a new revision and recompiled bindings. The manifest fingerprints only listed native files, not every dependency.
