# P08 star-graph UI: source-only backup

Snapshot date: 2026-10-05. This folder preserves the authored read-only UI source and display vocabulary. It is a restoration component, not a complete runnable graph application or an integrated simulator.

## Contents

- `ui/app.js`, `index.html`, and `styles.css`: navigation, inspection, filtering, display, and current-view export.
- `ui/canonical-model.js`, `model.js`, `coherent.js`: read-only graph indexing, bounded scene projection, deterministic force layout, and semantic formatting.
- `ui/renderer.js`: Canvas 2D rendering of a three-dimensional graph layout and pointer/keyboard navigation.
- `ui/display.js` and `display-vocabulary.js`: display-only readable labels; source record identities and relations remain unchanged.
- `ui/server.mjs`: read-only, loopback-only source server.
- `ui/build-offline.py`: original offline build source. Its required data inputs are deliberately absent.
- `ui/package.json`: original development metadata.
- `tests/source-smoke.test.mjs`: newly authored, dataset-free smoke tests using tiny synthetic records.
- `SHA256SUMS` and `manifest.json`: file-integrity records for this backup.

All 12 files under `ui/` are byte-for-byte copies of the authored source snapshot. The README, ignore rules, integrity records, and synthetic tests were added for this source-only backup.

## Deliberately excluded

No original source ZIP, native catalog/index/detail data, case graphs, instance datasets, supplement datasets, generated offline HTML, binary archives, screenshots, private download links, or data-bearing build reports are included. The broad archive-packaging script and original dataset-dependent regression tests are also omitted. The display vocabulary contains authored translation labels and generic case titles, not graph payloads or source catalog definitions.

Do not infer that a full dataset can be reconstructed from this branch. Do not publish restored data merely because the UI source is public.

## Checks that work without external data

Use Node.js 22 or newer; the backup was checked with Node.js 24.19.0. No npm install is needed for these dataset-free checks:

```sh
node --test --test-concurrency=1 tests/source-smoke.test.mjs
for f in ui/*.js ui/*.mjs; do node --check "$f"; done
python3 -c "import ast,pathlib; ast.parse(pathlib.Path('ui/build-offline.py').read_text())"
sha256sum --check SHA256SUMS
```

The eight smoke tests cover module import, record identity/direction, bounded projection, deterministic finite layout, display labels and immutability, the canonical same-origin URL helper, semantic formatting, and the source server's loopback/read-only behavior. These checks do not replace the original graph-data regression suite, actual browser interaction testing, or integration acceptance.

## Restoration dependencies

Preserve this folder's `ui/` layout. The original app resolves data relative to it, under a sibling `data/` directory. Obtain the matching datasets independently from an authorized source before expecting graph views to work:

- Default coherent-case view: `data/coherent/manifest.json`, the branch graph paths it names, `data/supplements/manifest.json`, and supplement branch/explanation paths referenced by those manifests.
- Catalog view: `data/typed-index.json`, its lazy `detail_path` records under `data/`, and any records followed through `payload_from`.
- Legacy instance view: `data/instances/manifest.json`, `data/instances/search-index.json`, and the scenario chunks referenced by the manifest.
- Offline build additionally expects `data/native-index.json`, native and typed detail chunks, coherent branch graph files, supplement JSON files, and instance files enumerated in `ui/build-offline.py`.

After restoring the exact compatible inputs locally:

```sh
cd ui
npm start
```

Open `http://127.0.0.1:4333/ui/`. Without those inputs, the server can serve the UI shell, but graph loading returns a missing-data error. The offline build can only run after its separately held inputs have been restored; no offline output was generated or committed for this backup.

## Review and acceptance limits

The preceding readability review covered an offscreen rendering of a C01 view. That is not full browser acceptance. This source-only backup was syntax-checked and passed its dataset-free smoke tests; it makes no claim that the complete application has passed browser acceptance or that PX4, Gazebo, live providers, physical execution, or real authority have been integrated or validated.

The source UI retains its own explanatory wording about authored cases and fixture evidence. Those messages describe the compatible full prototype when its separately held data is supplied; they do not mean this backup contains those datasets.
