# P08 lossless import pipeline

This offline pipeline imports the accepted P02 inventory into a linked graph. It does not run the simulator, dispatch commands, grant permissions, determine legal applicability, or evaluate native rule truth.

## Rebuild and test

From the workbench root:

```sh
python pipeline/import_catalogs.py
python -m unittest discover -s pipeline -p 'test_*.py' -v
```

The default inputs are `/workspace/shared/p02_stage1`. Use `--source-root` and `--output` for a different source directory or independent deterministic build. A delivered package can rebuild from its bundled snapshots with `python pipeline/import_catalogs.py --source-root data/inputs`. The primary-source closure evidence also has a bundled fallback. An existing immutable snapshot with different bytes fails closed; use a new output/version rather than replacing it.

## Scope and counts

- Four accepted catalogs: 20,004 node occurrences and 63,870 edge occurrences, including preserved source wrappers and declarations.
- Additional delivery source-preservation artifact: 197 nodes and 1,108 references/edges.
- Complete graph: 20,201 nodes and 64,978 directed edge occurrences.
- 64 authored cases, 10 source-preservation cases, one shared contract case.
- Exact source denominator: 35 workflows and 226 steps. The 20 machine cases are additional examples, not part of the 35-workflow denominator.
- All four accepted authored-count reports and SHA-256 hashes reconcile.
- Zero edge deduplication, zero unresolved endpoints, zero fabricated endpoint nodes.

These are preservation/coverage counts, not counts of domain-validated scenarios or runtime successes. Generic branch text is retained as source text and does not establish scenario specificity.

## Identity and aliases

Every occurrence has an ID derived from its immutable artifact identity and JSON pointer. The original ID remains independently available. Runtime records, actors in different scenes, repeated checks, temporal facts and parallel directed relations are never collapsed.

A definition may be reused only when artifact namespace, declared ID, exact definition payload, explicit revision and explicit scope agree. No repeated definition pair in the accepted input presently meets all those conditions. Therefore this build makes zero semantic merges. The 108 shared-definition identity groups remain distinct and are listed in `data/conflicts.json`. Similar labels alone provide no identity proof.

`data/aliases.json` maps every one of the 85,179 node/edge occurrences to its canonical ID, immutable artifact, exact pointer, source hash, and raw-object digest. Deduplication decisions are reversible because the complete source package is retained.

## Lossless means the package

`data/graph.json` preserves full original node/edge payloads. `data/inputs/` also preserves byte-exact complete source documents, including document-level metadata that is not itself a graph node or edge. The package is lossless; `graph.json` by itself is not advertised as a replacement for every source document.

Recover a byte-exact artifact to a new path:

```sh
python pipeline/reconstruct.py agriculture/agriculture-instances.json --output recovered-agriculture.json
```

`recover_artifact()` verifies the source snapshot hash. `recover_occurrence()` additionally verifies the occurrence's claimed source-version hash and raw-object digest before returning its original JSON object. Existing output files are not overwritten.

## Metatype endpoint closure

Seven original network extraction cases omitted four local metatype records, leaving 63 references without a local target. The pipeline does not create substitute nodes. It resolves these references to existing records in the delivery source-preservation artifact only after checking:

1. Both catalogs name the same primary source SHA-256.
2. The primary source bytes really have that SHA-256.
3. The source JSON pointer resolves to the exact preserved record payload.

The byte snapshot, pointer, record digest and canonical target are recorded in `primary-source-evidence.json` and `endpoint-closures.json`.

## Types, relations and time

The specification worker's `spec/contract.py` supplies canonical kinds and directed relation variants. Container collection wins over a record's internal collection member. This matters for city metatype wrappers whose internal collection describes the represented family.

Original endpoints and roles are retained. In particular, delivery ARGUMENT edges run parent→child and normalize to `has_argument`; the other verified source profiles run child→parent and normalize to `argument_of`. No endpoint is silently reversed. Legacy check-to-field references mislabeled INSTANCE_OF retain evidence-only `legacy_field_reference` semantics.

Fact projections preserve JSON types, quantities, units, frames, exact time strings, nulls, declared identity/generation and original source data. The `semantics` projection is only a reader aid. Missing metadata is not supplied from a guessed clock, unit, actor, generation or successful outcome.

`semantic_queries.py` provides conservative offline fact selection and three-valued truth utilities for tests and inspection. It requires explicit compatible clocks, availability cutoffs and validity intervals. An exact-identity query requires all five lifecycle identity members. It rejects stale, late, mismatched, null and incomplete data and returns UNKNOWN for conflicting values without last-arrival-wins. It is not an implementation of the unavailable native rule evaluator.

## Files used by the UI

- `manifest.json`: complete case list, counts, relative chunk paths and warnings
- `scenarios/*.json`: bounded per-case graph chunks
- `search-index.json`: global identity/kind/label search without loading the complete graph
- `coverage.json`: exact source-step → concrete canonical-node traceability
- `relation-signatures.json`: observed typed relation coverage
- `counts.json`, `aliases.json`, `conflicts.json`: auditable before/after, reversal and unresolved-identity reports

Large graphs remain chunked for display. A UI's visible subset must not be confused with accepted, loaded or globally covered counts.
