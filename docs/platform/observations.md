# Exact-cut capture and artifact integration

Job D provides the domain-neutral factory `aeroagentsim.observations.capture:build`. Capture owns only the metadata fields mapped in its config, creates observation records, and issues kernel command receipts. It never writes pose, task phase, dwell or domain award state. The accident pack must validate its own request/actor/dwell evidence before issuing the command and before consuming upload verification as edge acceptance.

## Plugin contract

The config requires `request_schema`, `storage_result_schema`, `accepted_schema`, `accepted_topic`, `record_type`, `record_id_prefix`, `fields`, `run_directory`, and `renderer`. `fields` maps **every** key in `observations.capture.METADATA` to distinct applicable fields owned by this engine through binding rules. The lifecycle domain must cover `record_id_prefix + sha256(request_id)`. Generic schemas are exported in `observations.schemas`: `REQUEST`, `CAPTURE_RECEIPT`, `STORAGE_RESULT`, `UPLOAD_RECEIPT`, `UPLOAD_VERIFIED`, and `METADATA_SCHEMAS`. Commands require registered result schemas. Packs choose descriptor IDs and can further constrain the camera schema.

`CaptureRequest.to_command_data()` uses a native tagged actor reference; `to_data()` is the persisted/browser form with complete untagged EntityRef data. Both carry request ID, kernel run ID, actor including epoch/type/generation, source cut `{index, instant:[ns,microstep]}`, explicit camera manifest, SHA-256 asset digest, rational acquisition stamp, width/height and finite wall timeout. The browser bridge encodes integers outside the JS exact range as `{"$integer":"..."}`, escaping literal reserved-tag records with `$record`, matching the feed convention. E must compare these with BigInt/lossless cut handling; it must never round them. The kernel checks that the cut is an issued committed prefix and the actor exists at that cut and remains live at availability.

Renderer selection is explicit:

```yaml
renderer:
  mode: browser
  viewer_url: http://127.0.0.1:8002/runs?capture=1
  node_modules: /absolute/path/to/frontend/node_modules
  timeout_s: 20.0
  # Optional, explicitly select a Chromium binary or hardware GL:
  # browser_executable: /absolute/path/to/chrome-headless-shell
  # software_gl: false
```

`mode: stub` instead requires `fixtures: {request-id: /absolute/path/to/fixture.png}`. A missing fixture is a failure. Browser startup, scene/camera application, decode, and timeout failures never switch modes. This initial engine profile blocks simulation time while rendering; it does not claim online motion during camera latency. BrowserRenderer maintains one Playwright browser and page across captures; a failed request closes that process group, including Chromium. Acquisition and later metadata publication retain separate timestamps.

For direct RunSession use, explicitly set `run_directory` to the session directory. The REST run creation hook pins it to the service-assigned run directory **before** engine validation, scenario snapshot and WAL creation. Validation alone opens no browser and writes no artifact. The service output path changes the pinned scenario digest, not its registry/bindings or physical assumptions.

Success follows actual PNG validation/storage, then committed record creation, metadata publication and the terminal receipt containing its typed record reference. PNG acceptance supports bounded, non-interlaced 8-bit images and validates dimensions, chunks/CRCs, decompression extent and filters. Storage failures remain failures. Renderer timeout produces a failed kernel receipt with the typed result status `timeout`; the kernel has no separate terminal timeout status. Failed/timed-out acquisition closes the request to late uploads.

## Frontend API required from job E

The **built run viewer**, in an explicit capture mode, must install:

```ts
window.aeroCapture = {
  async render({ request, label, service_run_id }): Promise<{
    request: CaptureRequestBrowserForm;
    png_data_url: string; // data:image/png;base64,... from the actual WebGL canvas
  }>
}
```

`render` must load the specified immutable run/epoch, seek the temporal store to the exact `source_cut.index` **and** ns/microstep, verify actor generation, load assets matching `asset_digest`, apply the complete camera preset/revision, wait for assets and the render, and return metadata measured from the applied store/camera. It must reject mismatched/missing facts, assets, presets and cuts. Capture geometry uses exact recorded poses, with interpolation and live following disabled. Nonspatial camera roles require an authored anchor and do not create a pose for a coordinator. The canvas must support actual PNG readback (`preserveDrawingBuffer` or a render-target readback). No screenshot of a loading screen or substitute geometry is acceptable.

When `label` is supplied by the recorder, include it visibly in the **returned canvas PNG**. It contains exact source ns/microstep/index and playback speed; DOM labels outside the canvas are insufficient. Return the full applied request, including camera manifest/dimensions, rather than echoing an unverified input. The bridge compares every returned metadata field before storing the bytes. Preserve the bridge’s `$integer` tags in returned metadata, including actual large cut ns; the Python comparison decodes them exactly.

Job D does not edit frontend. The browser acceptance fixture builds a small actual Three.js viewer from recorded kernel facts and independently hashed HTML/Three assets. It proves the bridge, source-cut and storage paths; it does not establish full console/city visual parity. E/F must mount the contract on the final built console and run its integration gate.

## Storage, REST and upload verification

Artifacts live under the run directory:

- `artifacts/requests/<sha256(request-id)>.json`: owner-registered outstanding request.
- `artifacts/blobs/<content-sha256>.png`: validated bytes.
- `artifacts/records/<sha256(request-id)>.json`: immutable request/camera/asset metadata, SHA-256, byte count, media type and explicit renderer mode.
- `artifacts/closed/` and `artifacts/deliveries/`: failed acquisition closure and actual ingress transport/admission result.

Atomic writes and a process-shared lock preserve request/content idempotency. A changed actor/cut/camera/assets/content conflicts; a corrupt stored blob cannot be reused. Replay calls `replay_capture(store, request_id)` to verify/read the stored observation; it never constructs a renderer. WAL replay determines whether the original request or upload actually succeeded.

REST routes:

| Method/path | Result |
| --- | --- |
| `GET /v1/runs/{id}/artifacts` | Records with content/camera hashes and byte counts. |
| `GET /v1/runs/{id}/artifacts/{sha256}` | Metadata and download link for all requests referencing that content. |
| `GET /v1/runs/{id}/artifacts/{sha256}/download` | Verified PNG, `X-Content-SHA256`, ETag and byte count. |
| `POST /v1/runs/{id}/capture-artifacts` | Storage record plus the actual worker ingress admission receipt, HTTP 202. |

Upload JSON requires `request` (persisted form), `png_base64`, `digest`, `byte_count`, `at_ns`, and `source_stamp` (the actual **availability** source stamp). The server selects the one `plugin: capture` owner, its `storage_result_schema` and `ingress_stream_id` from the pinned scenario; callers cannot select a different target/schema. Bytes and metadata are checked/stored before submission through the existing Q9 worker path. Retries reuse `capture/<request-id>/<content-digest>`. The source manager must explicitly close the capture stream prefix via the existing watermark/progress API; an upload never closes it by inference. A finished run refuses uploads.

A dispatched storage-result command verifies the record/hash/current actor again and emits the separately registered `accepted_schema` event (`UPLOAD_VERIFIED` payload) plus its own terminal receipt. This is generic byte/upload verification. The demo's behaviour gateway must correlate that event with the active incident capture, recorded dwell and request before committing **edge/task** acceptance. Neither HTTP 202, ingress admission nor a video export completes a simulation task. Admission failure retains the actual stored bytes and a visible delivery failure.

## Integration metadata for job F

Only F edits shared packaging. Register:

```toml
[project.entry-points."aeroagentsim.engines"]
capture = "aeroagentsim.observations.capture:build"

[tool.setuptools.package-data]
"aeroagentsim.observations" = ["viewer_bridge.mjs"]
```

Node Playwright and Three are already in the frontend dependency tree; no npm dependency is requested. Chromium is optional runtime infrastructure, selected explicitly when its installed revision differs from Playwright's default. `browser` and `media` markers are registered locally by `tests/observations/conftest.py`. Test/run/media outputs stay under `/tmp/aas-q/d/`.

## Exact live prefix and service identity

The bridge passes `service_run_id` separately to `window.aeroCapture.render`. It is the REST/storage run ID; `request.run_id` and actor refs retain the kernel namespace. Service creation pins the bridge value into renderer config, and the recorder reads it from the actual run manifest. E must load `/v1/runs/{service_run_id}/header` and verify both identities, instead of using a scenario ID as a REST run ID.

During blocking capture the worker may not yet have refreshed `index.json`. E should fetch `GET /v1/runs/{service_run_id}/capture-requests/{request_id}/scene` for the registered request. It returns the already committed WAL/feed prefix through the exact owner-validated source cut without rebuilding indexes, advancing the worker, changing the seal or rendering. Missing/partial/gapped or mismatched cuts fail. Finished-run videos use ordinary indexed replay paging. Artifact metadata and the scene endpoint preserve large integers with `$integer`; upload decoding accepts that lossless form.

## Recorder and exporter

Run `tools/demos/record_run_views.py` with `--run`, `--output`, `--cameras`, `--viewer-url`, `--node-modules`, optional `--browser-executable`, `--fps`, `--speed`, `--ffmpeg` and `--encoder`. Camera JSON is a nonempty list of unique `{id, actor, camera, asset_digest, width, height}` presets; actors contain complete EntityRef data. Views are explicit; there is no ID-prefix role inference or universal 72-view count. Outputs must be outside the immutable run.

Encoder selection is explicit. This environment's ffmpeg lacks libx264, so media gates select its installed mpeg4 encoder. An unavailable encoder remains a failure and never triggers an automatic codec switch. The recorder uses rational source step speed/fps, selects the last committed cut at/before each sample including its final microstep, includes the final cut, and labels each canvas PNG with exact source coordinates and speed. Manifests retain pending/completed/failed views, camera hashes, cuts/intervals, encoder, actual decoded frame counts, output duration, content hashes and byte counts. The last frame holds for one output-frame interval, recorded separately from nominal speed. Decode/count validation follows encoding.

Run `tools/demos/export_traffic_report.py --run RUN --output OUT`; repeat `--decision-schema ID` for the actual graph/node record schemas. The default is aas.agent.record. Reports copy verified photos, preserve all typed messages/receipts and exact cuts, show selected decision phase data/usage, and include last published fact/retraction evidence with validity. Absence is never zero; response records are never inferred model attempts. Unselected decision contracts remain visible in the timeline. Optional `--docx` needs installed pandoc; `--pdf` also needs an installed LaTeX engine. Requested unavailable tools fail clearly while retaining Markdown. Background recorder/export job HTTP APIs remain for service integration outside this job's minimal artifact hook.
