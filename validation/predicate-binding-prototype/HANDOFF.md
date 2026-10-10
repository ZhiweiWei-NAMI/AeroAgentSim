# Cloud/server handoff

## Outcome

A standalone native prototype is ready for fixture replay. With external Atlas supplied, 64/64 tests pass; without it, 42 pass and 22 are explicitly skipped. There are also seven checked-in JSON cases evaluated by the actual unchanged Atlas runtime. Files are confined to `predicate_binding_adapter/`; Atlas, P02 proposal documents and parcel/UI files were read-only.

No remote server execution, paid model use, deployment or real control was performed. This directory is a source-only Git handoff. Do not describe this as full entity coverage, live multi-entity consistency, safe physical admission or verified closed-loop simulation.

## Smallest next integration

1. Copy this directory to an explicitly authorized environment. Supply its local Atlas `observable_v2` path through `ATLAS_ROOT` / `--atlas-root`; paths are executor-local. Python standard library only.
2. Run `run_checks.py` and `demo.py` there. Confirm the exact locally pinned Atlas definition revision. The current manifest covers six listed files; it is not a full transitive supply-chain attestation.
3. At provider ingress, write one explicit mapper from the provider's already-known schema to the immutable `Snapshot` contracts. Build the current-generation registry once. Require exact object/relationship endpoints, units, coordinate frame, event validity and evidence availability.
4. Create explicit target signatures and compiled bindings once. Do not infer ownership from dotted IDs, parse display names, discover schemas by scanning the repo on every frame, or synthesize new predicate semantics. Flat values and `field_map` are the only Atlas input bridge.
5. Begin with read-only provider replay. Persist original as-observed prepared rows and read-only result revisions. A missing state stays unknown; lack of a relation row proves absence only with complete exact-scope coverage.
6. Keep the fixture ledger opt-in and isolated. A live authority requires durable atomic writes, real authentication/fencing, lifecycle/capability authorization, conflict recovery, verified release/acquire receipts and source commit frontiers. Never connect these fixture mutators directly to physical execution.
7. Feed the existing parcel view an immutable prepared view frame through its read-only `setFrame` boundary. `parcel_mapping.json` records the inspected shape and needed joins. The view must not commit custody, infer receipt success, fabricate detachment or own the world clock.

## Boundaries implemented once

- JSON/input ingress: `json_io.py`, exact Ref and typed cell construction.
- Binding setup: `CompiledBinding`, fixed signature/policy key and independent flat slots.
- Per-query projection: indexed cells, explicit lifecycle/time/authority/unit validation and compact unavailable reasons.
- Native call: `CompiledBinding.evaluate`; unchanged Atlas engine, explicit history, no owner alias fallback.
- Fixture state writes: `FixtureLedger`; one authority/fencing epoch and lock, versioned custody and atomic resource bundle admission.

No per-row source hashing, repository discovery, heavyweight provenance gate or extra rule interpreter is introduced.

## Observable API decisions

- Positive relation evidence can decide existence despite other incomplete rows; any gaps remain diagnostics. Negative queries need exact complete coverage.
- Wrong relation direction/schema is binding-invalid, distinct from a legal binding with unavailable evidence.
- Strict-left validity is a real left-limit policy for all projected records; after-barrier is the regular half-open policy.
- No native time rebasing or silent float timestamp collision. A declared relative clock is required for unsupported large timestamps.
- Engine/catalog revision integrity is the bootstrap caller's responsibility. The prototype checks required field completeness and supplied binding metadata; it does not prove semantic equivalence of arbitrary selectors to target definitions.
- Historical corrections need a new evaluation revision. This prototype has no online command dispatcher, so replay cannot execute anything.

## Remaining work before live claims

Provider-specific ingestion and schema signatures; authoritative stage/scene consistency; packet-cohort and directed-flow identity; real authority durability/security; task/mission phase gates; action idempotency and rearm semantics; real physical execution/effect confirmation; attachment changes with physical authority evidence; complete return/landing trajectories; and end-to-end replay on real provider observations.

Fixture custody commits do not prove physical motion or detachment. Indeterminate execution requires same-transaction reconciliation. A database correction cannot roll back a real handover. Priority can select an admissible request; it cannot erase occupancy.

## Reproducible verification

From this directory:

```sh
ATLAS_ROOT=/actual/observable_v2 PYTHONDONTWRITEBYTECODE=1 python run_checks.py
python demo.py --atlas-root /actual/observable_v2 --out demo_results.json
```

Expected: 64 passing tests, zero skipped/errors/failures; seven matching native JSON cases; `committed/stale_precondition` custody; `admitted/stale_precondition/capacity_exceeded` resource outcomes; zero physical actions executed. If definitions differ, report that mismatch and inspect it rather than weakening the fixtures until they pass.
