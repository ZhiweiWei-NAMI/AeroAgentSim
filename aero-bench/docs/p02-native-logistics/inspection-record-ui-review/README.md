# Inspection record UI review

These three browser screenshots use the unchanged accepted inspection run `9e07a000…` through authoritative post-run replay. They test corrected record presentation and English labels. They are not evidence of a new logistics run or live Control reconnect.

- `semantics-en-tick292.png` and `semantics-en-tick300.png`: 1600 × 1000.
- `semantics-en-narrow-tick300.png`: 1280 × 900.

The source declares zero logistics orders. Its two network-delivered inspection reports appear under **Mission records**. Order, parcel, carrier, custody and destination remain UNKNOWN. The original GLB city, roads and aircraft remain visible. Header links and source disclosure use the existing language mechanism. The focused mission-record and viewer-chrome suites passed 33 checks.

`manifest.json` binds the exact screenshot bytes and seven frontend source hashes. `asset-failure-classification.json` separates navigation/request aborts from inventory pack warnings; an aborted request is not proof that a city object is missing. The source-version receipt records the capture-time checkout plus uncommitted source hashes; this publication commits those exact seven files. Live parcel tracking and a new native verdict remain pending.
