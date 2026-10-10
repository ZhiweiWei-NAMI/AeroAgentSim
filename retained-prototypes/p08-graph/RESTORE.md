# P08 graph recovery snapshot, 2026-10-05

This branch preserves the current P08 workbench: maintainable Python/JavaScript source, semantic contracts, immutable input snapshots, generated graph data, all 75 scene chunks, and review/test evidence. It is an independent recovery branch in `ZhiweiWei-NAMI/AeroAgentSim`.

## Important scope boundary

The source catalog contains **35 activities, 226 ordered steps, and 265 predicate records** in the current P08 graph. The historical **1,686-entry catalog has not been recovered**. This snapshot must not be presented as the complete historical activity/predicate catalog. Graph node counts and scene counts are separate from source activity counts.

Current preserved graph: **20,201 nodes, 64,978 directed edge occurrences, and 75 scenes** (64 authored, 10 source-preservation, and 1 contract scene). There are also 20 machine examples. The data is authored inspection/validation material; passing tests does not establish native rule execution, legal applicability, live backend operation, or physical success. Actual browser/pixel verification remains unperformed in the original acceptance record.

Expected uncompressed `data/graph.json` SHA-256:

`900f9d725b220727b66609bc31bb9e448f92177959c4b07b0ecb4a5f9d0026de`

## Clone and restore

```sh
git clone --depth 1 --single-branch --branch backup/p08-graph-20261005 https://github.com/ZhiweiWei-NAMI/AeroAgentSim.git p08-recovery
cd p08-recovery
sha256sum -c SHA256SUMS
cd p08-graph-workbench
python3 restore.py --check-only
python3 restore.py
```

Python 3.9+ standard library is sufficient for restoration. Each stored file is checked before decoding; each decoded file is checked against its original size and SHA-256. Existing identical files are reused. Different existing files are never overwritten. Use `--output /path/to/new-workbench` for a separate recovered directory.

Large JSON files are checked in as individual deterministic `.json.gz` files. Restoration recreates the exact original `.json` files. This avoids GitHub's large-file limit and retains scene-level files. `RECOVERY-MANIFEST.json` maps every original path to its stored path and records both hashes. It is the recovery authority, including the extra primary-source immutable input snapshot.

## View the graph

With Node.js 22+ installed, from the recovered workbench root:

```sh
node ui/server.mjs
# Open http://127.0.0.1:4318/ui/
```

For a self-contained, double-click HTML:

```sh
python3 ui/build-offline.py
# Open ui/P08-graph-explorer-offline.html
```

The HTML and source ZIP are intentionally not duplicated in Git. The HTML is rebuilt from the preserved source and data. `ui/offline-build-report.json` records the original expected HTML hash. The page uses native gzip DecompressionStream support and no external runtime assets. The original source-package manifest/report are omitted because the recovery layout has its own independently checked manifest.

## Independently rebuild the canonical data

From the recovered workbench root:

```sh
python3 pipeline/import_catalogs.py --source-root data/inputs --output rebuilt-data
python3 -c "import hashlib,pathlib; p=pathlib.Path('rebuilt-data/graph.json'); assert hashlib.sha256(p.read_bytes()).hexdigest()=='900f9d725b220727b66609bc31bb9e448f92177959c4b07b0ecb4a5f9d0026de'; print('Graph hash verified')"
```

This path uses all eight bundled immutable input files (seven accepted catalog artifacts plus primary closure evidence). It does not need the original cloud filesystem.

## Tests and provenance limits

Portable checks from the recovered root:

```sh
python3 -m unittest discover -s review/technical -p 'test_*acceptance.py' -v
node --test ui/tests/model.test.mjs review/technical/ui_acceptance.test.mjs review/technical/renderer_geometry.test.mjs
cd ui
npm run check
npm install
npm test
```

`npm install` is needed only for the jsdom development tests, not the viewer or recovery. Python schema checks require the `jsonschema` package. Review scripts and original test evidence are retained unchanged. Some original full-audit/spec-regeneration scripts still refer to the historical `/workspace/shared/p02_stage1` inventory or original source paths; those are provenance, not required dependencies for recovery, viewing, or the explicit bundled-input graph rebuild above. Do not treat historic logs as a newly run full audit. Snapshot-specific verification is recorded separately in `RECOVERY-VERIFICATION.json`.

Only P08 project work is included. Caches, hidden files, duplicate technical rebuilds, third-party asset packs, credentials, account/session files, and unrelated materials are excluded. This public branch is a code/data recovery snapshot, not a deployment.
