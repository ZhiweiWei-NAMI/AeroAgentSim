# P08 independent technical acceptance

## Decision and boundary

The accepted-source migration, canonical graph indexing, and read-only UI model/DOM path are independently testable. Final evidence is recorded in `artifact-audit.json`, `schema-validation.json`, `rebuild-comparison.json`, and test logs in this directory. A passing static check is not native execution, a simulator result, legal applicability, actual command delivery, or observed physical success.

Actual browser/pixel rendering and end-user interaction latency have **not** been verified. Prior browser access restrictions were not bypassed. DOM tests use jsdom and a renderer stub. Renderer tests check geometric coordinates with an instrumented canvas context, not actual pixels.

## What is covered

- The four accepted stage-one inventories independently enumerate 20,004 node and 63,870 edge occurrences. All are present in the alias ledger with the exact source JSON pointer and raw digest. The supplementary extracted emergency source adds 197 nodes and 1,108 edges, for 85,179 reversible occurrences.
- All seven accepted source snapshots are byte-exact. Reverse export is package-backed: immutable snapshots preserve original document-level metadata and wrappers; `graph.json` alone is not claimed to reconstruct an entire original document.
- Every canonical payload, source pointer, source version SHA, endpoint, direction, role, time and generation value is checked against its source. Raw recursive AST structure and literal/argument ordering survive exact payload equality, including explicit null, false, missing fields and nanosecond strings.
- All 64,978 edge occurrences remain separate. Shared endpoints do not collapse parallel relationships. All 878 native ARGUMENT occurrences are mapped according to their verified source direction: delivery parent→child; agriculture/city/network child→parent.
- All 35 source activities and 226 ordered steps match their exact catalog names and mappings. Twenty machine cases are retained.
- All 49 relation-instance role bindings and two n-ary material transformations retain declared endpoint membership and multiplicity. This is source incidence validation, not inference of a new global cardinality policy.
- Sixty-three previously missing local source-case endpoints close to existing records only after exact primary-source SHA, pointer and raw-object validation. No inferred live entity is created.
- No definition occurrence is merged in the actual data because the repeated records do not have sufficient explicit revision and scope proof. Positive and negative compatibility fixtures separately verify proven equal definitions can merge, while null identity, conflicting revision/scope, lifecycle records and type-coerced identities cannot.
- Model indexing covers the union of all 75 scene files, with all 76 node kinds and 152 relations, zero duplicate-ID conflicts and zero missing endpoints. A node filter does not invent shortcut edges.
- DOM integration checks actual manifest counts, selected/current/canvas scope, global cross-scene lookup, step filtering, raw AST/source inspection, exact edge endpoints, cancellation/retry, stale-request suppression and failed-load recovery.

## Fixed counterexamples

The independent review found and owners fixed:

1. Edges from another scenario leaked through a node-only case filter when canonical nodes were shared.
2. Equal ID/kind/payload masked incompatible semantic level or typed lifecycle identity in the UI index.
3. Null/empty definition identity fields were incorrectly accepted as merge proof.
4. Malformed decimal time strings could raise instead of becoming unknown.
5. Incomplete or malformed exact lifecycle references could be accepted by the fact-selection helper.
6. Occurrence recovery verified raw-object digest but initially ignored the alias's claimed source-version SHA.
7. Graph/schema integration initially disagreed on legacy relation variants, source declarations and authored authority-declaration levels.
8. Structural JSON null was incorrectly labeled with an is_unknown projection flag. It is now is_null, independent of semantic truth.
9. Opposite-direction parallel edges used the same visual/hit-test segment; offsets now use a stable endpoint orientation while preserving arrow direction.

## Reproduce

Run from the workbench root:

```sh
python review/technical/source_oracle.py
python -m unittest discover -s review/technical -p 'test_*acceptance.py' -v
python review/technical/audit_artifacts.py
python review/technical/validate_schema.py
node --test review/technical/ui_acceptance.test.mjs review/technical/ui_dom_acceptance.test.mjs review/technical/renderer_geometry.test.mjs
node review/technical/benchmark_graph.mjs
python review/technical/audit_offline.py
python pipeline/import_catalogs.py --output review/technical/rebuild
```

The DOM suite uses the already available jsdom installation at `AeroAgentSim-workbench/frontend/console-prototype/node_modules/jsdom`. This is a test dependency, not a runtime UI dependency. The prototype itself does not need an external CDN.

`rebuild-comparison.json` records byte-for-byte equality of all 94 generated JSON artifacts against an independently rebuilt output. The graph and schema hashes tie the acceptance evidence to the exact versions reviewed.

## Performance evidence and remaining gaps

`model-benchmark.json` measures real 75-scene JSON loading, canonical indexing, filtering and bounded layout in Node.js. The measured run used 20,201 nodes and 64,978 edges; only 480 nodes and 669 edges were selected for the preview. It reported 19,721 omitted candidate nodes and 6,567 boundary edges explicitly. Browser paint, GPU work, frame rate, pointer hit usability, screen-reader behavior and visual legibility are unmeasured.

Raw AST preservation does not mean every scalar/property is an independent graph node. The inspector recursively shows native AST data; traversable expression edges are the records explicitly present in the accepted sources.

No repository commit, push, deployment, legal rule, native executable catalog, live grant or physical effect is established by this review.

## Offline HTML boundary

The standalone HTML embeds canonical graph, all 75 scenes, search/alias metadata and review files for inspection and export without network fetches. `offline-audit.json` independently verifies embedded bytes against the final source artifacts and confirms no external script/style asset and a no-network CSP. Original document reconstruction still requires the full ZIP's immutable `data/inputs` snapshots. This artifact inspection does not establish a real-browser visual pass.
