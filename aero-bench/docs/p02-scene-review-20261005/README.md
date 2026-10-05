# P02 scene review

This is the existing AERO_BENCH city/GLB renderer, captured before the current business-UI integration edits. It is an engineering/authoring preview, not a formal live run. The screenshot shows actual server model assets loaded by the existing renderer; no model or map assets are included in this delivery.

![Original existing BENCH city and GLB scene](original-bench-glb-complete-desktop.png)

Source HEAD: `ea295073cdd310a31fa2e29317a3571bc716bb87`. Entry: `frontend/index.html` → `frontend/src/main.ts` → `PublicTraceApp` → `PublicTraceMap`. Model loaders: `frontend/src/entity-visuals.ts` and `frontend/src/city-asset-cache.ts`. Scene configuration: `frontend/public/city-presentation/jingan-engineering-preview-v3.json`. Exact dirty-file hashes at capture were not retained; the HEAD identifies source provenance and does not assert a clean checkout.

The integrated business view is **IN_PROGRESS**. Its source patch has not yet produced a completed capture, so this gallery contains no image claiming that integration is finished. Parent pixel review remains required.

`screenshot-provenance.json` records the PNG checksum, original file timestamps, scene source and publication scope. This delivery includes only the PNG and these two explanation files. It uploads no account information, credentials, private host URLs, raw traces, models or maps.
