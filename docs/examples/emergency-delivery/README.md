# Emergency-delivery canonical 3D graph

Open `../emergency-delivery-graph.html` directly in a browser. One offline HTML file contains the validated authoring configuration, its derived typed graph, source-linked fixture records and coverage review. No server, account, CDN or network is needed.

## Interaction

- Chinese by default; switch to English
- Drag blank space to rotate the 3D typed graph; Shift-drag to pan; wheel to zoom
- Screen-facing labels stay readable; click **平面核对 / Flat audit** for flat review
- Hover a node for type and local relationships; click to pin details
- Hover/click an edge for its exact canonical relation, direction, role and source path
- Focus views and relation-layer filters reveal the same graph, not independent models
- Export writes the unchanged canonical authoring configuration, ready for reimport into the workbench

## One source of truth

Authoring source: `frontend/console-prototype/examples/emergency-delivery.config.json` in the coordinated workbench checkout. The page generator consumes `scripts/emit-emergency-graph.mjs` output. It preserves every canonical `graphNodes` and `graphEdges` identity, direction, AST operand and source record. There is no second independently authored rule or relationship schema in the renderer.

Runtime instances derive only from that source's fixture outputs and link to their definitions. Authored expected effects are stored in the source configuration's business-module parameters and are distinctly labeled **not executed**. The visual layout, labels and focus views are presentation metadata.

## Scenario and evidence boundary

Everything is fictional: UAV, parcel, scripted child, airspace, recipient, observations and policy. No actual children, identification, footage or upload are involved. Observation is not ground truth; exercise policy is not law.

The UAV agent uses computation to produce a suspected-fall observation and communicates the report/application. An independent agent on the edge server allocates corridor time. Server approval, grant delivery, allocation, planned route and actual motion are separate records. The outbound allocation is [16,22) s; the independent reverse return allocation is [44,78) s. The no-fly polygon cannot be waived by the incident. Parcel custody and attachment remain distinct. Energy expectations use unique ledger entries, once each.

Configuration/graph validation and lossless import/export pass. Seven fixture scenarios execute. A positive fixture lease check does not establish actual safety, legality or permission. Aggregate real permission and native Atlas truth remain UNKNOWN. Physical-module effects, real upload and actual arrival are not executed or proven.

## Coverage

The 92 named relation contracts are divided into business semantics, authoring references, definition/instance structure, AST structure, and evidence/runtime. They are not 92 equally mandatory business links. All are retained in the review, with represented and not-applicable cases explicit; runtime implementation gaps stay visible. Disposition coverage is not full concrete-example or native-engine coverage.

The canonical configuration has no renderer-generated missing required fields. Source validation warnings and missing runtime capabilities are separate from structural validity.

## Files

- `../emergency-delivery-graph.html`: standalone 3D/flat page
- `../emergency-delivery-graph.config.json`: unchanged canonical config
- `../emergency-delivery-graph.fixture.json`: derived display data plus canonical source/results
- `../emergency-delivery-graph.coverage.json`: exact source coverage and per-layer review
- `../emergency-delivery-graph.tests.json`: canonical identity + DOM/3D checks
- `build_canonical_page.py`, `page.template.html`, `test_canonical_graph.cjs`: current renderer source and tests
- Earlier `build_fixture.py`, `add_airspace.py`, `coverage.py` and `test_graph.cjs` are the historical preview pipeline, not the canonical source

Rebuild with `python docs/examples/emergency-delivery/build_canonical_page.py /tmp/emergency-canonical-projection.json`, then `node docs/examples/emergency-delivery/test_canonical_graph.cjs`. Builder/test paths refer to the coordinated workspace only; the delivered HTML has no local dependencies.

Local Chromium startup is blocked by sandbox socket restrictions. DOM tests and geometric overlap checks are not browser screenshots or visual acceptance. Real browser rendering remains a separate QA step, with no restriction bypass attempted.
