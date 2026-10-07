# Native parcel platform 0.1

The parcel participant flies from the destination pad to the pickup hub,
lands and disarms, takes custody, flies back, then lands and disarms on the
destination pad before delivery. PX4/Gazebo supplies motion and contact
measurements. The business provider owns parcel custody. The independent
verifier checks their sealed records, including actual airborne transport.

## Open the platform

For the local delivery, open `http://127.0.0.1:5416/city-studio.html` to inspect
the configuration. The read-only Control service on port 8769 exposes the
authenticated catalog for the recovered sealed run. Open
`http://127.0.0.1:5416/?view=live`, enter the private bootstrap values under
`正式运行控制`, load the catalog, select the parcel run and click
`打开已封存回放`. This service uses `SealedReplayManager` and needs no runtime
reconnect. Close the controls panel to see the business dock, select
`uav.p02.carrier`, choose `外部跟随` and play. The service is bound to the host
loopback address; remote browsers need forwarding to that host.
The local `final-connection.json` records the private credentials file path.
Use its bootstrap values in the two password fields; the file is not in Git.
The recorded first replay load fetched 418 embedded assets in about four
minutes; loading progress remains visible. The viewer animates its carrier
marker from sealed PX4/Gazebo telemetry.

The local delivery directory is `validation/platform-0.1/`. The final-run
paths are `final-scene/native-bundle/`, `final-registry/`,
`final-configuration/`, `final-compilations/`, `execution/`, and
`final-watchable/`. Recovered projection output is retained under
`publication/<run_id>/`. The image lock is `verifier-image/images.json`; use its
actual component digests. Raw runs, original city assets, recordings and
credentials are retained in local storage rather than published in Git.

The second physical run is
`4e39a29217c0e15ad52b8d7fe5c5d5d7b256e83d0b644b00569bb4ee2ef2ceeb`,
compiled through the UI as
`b76081905ec64aa146f74197d5e1daa605ba25a078b13a898f6c0f99edb91c44`.
Its formal verifier report is `passed`, with complete coverage and all five
goals passing. Actual pickup completed at tick 60, delivery at tick 128,
and the declared observation horizon remained 300 ticks. Its original run
summary nevertheless has status `error` with
`public_trace_projection_failed`; public trace publication did not finish.
Recovery completed that projection from the same immutable runtime and
verification seals into `publication/<run_id>/`, whose summary is `passed`.
The original failed summary stays unchanged. The sealed replay was actually
played from tick 1 through tick 300, with the carrier in external follow view
and visible pickup, transport and delivery states. See
[the evidence manifest](platform-0.1-evidence.json) for the recorded source
identities and outcomes. The watchable video is
`validation/platform-0.1/final-watchable/native-parcel-0.1.mp4`; its
`video-evidence.json` records the source recording, measured trim offsets and
file hashes. The adjacent `pickup.png`, `transport.png`, `delivered.png` and
`result.png` are decoded frames from that actual recording.

An earlier run completed physical pickup and delivery but failed formal
verification because the loader selected the wrong task package resolver.
Its original failed result and raw evidence are retained separately.

## Inputs and preparation

Run commands from `aero-bench/`, using Python 3.10 or later, Node with the
frontend lockfile dependencies, a working Docker daemon, and the declared
digest-pinned OCI images. Install the project with `pip install -e '.[test]'`
and frontend packages with `npm ci --prefix frontend`. Browser capture also
requires Chromium; install it from `frontend/` with
`npx playwright install chromium` if it is not already installed. Restore the
viewer model and terrain texture files under `frontend/public/` from their
retained source assets; these visual assets are not bundled into the source
checkout.

Preparation requires an existing native city source bundle containing its
`suite.yaml`, world assets and provider configs, a six-component image lock,
and the measured pose calibration. These are required inputs, not generated
demo replacements. The local run uses:

- City source: `/mnt/data2/weizhiwei/AERO_BENCH/validation/platform-plan-20261001/W1-I5/v8-registration-attempt-1/native-v8`.
- Image lock: `validation/platform-0.1/verifier-image/images.json`.
- Calibration: `docs/p02-native-logistics/calibration-review/calibration.json`.
- Runner: `validation/platform-0.1/runner.yaml`.
- Authoring source manifest: `validation/p02_platform_native_parcel/authoring-sources.json`.

The paths above identify retained input data; historical directory names are
not platform version numbers. On another machine, supply the same declared
assets and pinned identities at explicit local paths. The source manifest
must reference actual source files accessible to the authoring service.

Set `PARCEL_CITY_SOURCE`, `PARCEL_IMAGE_LOCK`, `PARCEL_SOURCES_MANIFEST`,
and `PARCEL_RUNNER_CONFIG` to the retained inputs or their verified local
copies. For a new reproduction, set `PARCEL_OUTPUT` to a fresh directory;
the existing final-run directories are retained evidence.

Prepare and register the scene:

```bash
python tools/prepare_native_parcel_scene.py \
  --source "$PARCEL_CITY_SOURCE" --image-lock "$PARCEL_IMAGE_LOCK" \
  --calibration docs/p02-native-logistics/calibration-review/calibration.json \
  --output "$PARCEL_OUTPUT/final-scene"
python tools/register_native_parcel_scene.py \
  --bundle "$PARCEL_OUTPUT/final-scene/native-bundle" \
  --output "$PARCEL_OUTPUT/final-registry"
```

To rebuild changed components, use `tools/build_native_parcel_images.py
--base-image-lock ... --output ... --component agent --component verifier`.
It builds from a recorded source inventory, pushes to the local registry and
writes an updated digest lock. Other component identities remain explicit.
The native flight and traffic Dockerfiles require their pinned parent images;
building and pushing also requires the local registry at `localhost:5000`.

The scene keeps the 300-second horizon, 0.5 m flight landing height condition,
0.15 m parcel vertical tolerance and 0.2 m/s stationary limit. Its two legs
span 60 m horizontally. Dividing by the authored 15 m/s cruise profile gives
a 4-second ideal horizontal lower bound under that assumption. The profile
does not measure or set the PX4 command speed limit. Climbing, descent,
acceleration and settling are additional; actual duration comes from the
sealed flight measurements.

Registration derives traffic metadata from the same pinned SUMO route XML
and exact object bindings: 60 motor vehicles, 36 pedestrians and 12 bicycles.
It identifies bicycles by the declared `vType.vClass`; the native object
kind remains `vehicle`. This corrects the displayed quantities without
changing the world or its traffic demand.

## Save and compile through the UI

Start the authoring API with the prepared registration and real source manifest:

```bash
python -m aero_bench.authoring.api --host 127.0.0.1 --port 8771 \
  --output "$PARCEL_OUTPUT/authoring" \
  --compilation-output "$PARCEL_OUTPUT/final-compilations" \
  --sources-manifest "$PARCEL_SOURCES_MANIFEST" \
  --native-scenes-manifest "$PARCEL_OUTPUT/final-registry/native-scenes.json"
```

In another terminal, start Vite from `frontend/`:

```bash
AERO_AUTHORING_API_TARGET=http://127.0.0.1:8771 \
AERO_CONTROL_API_TARGET=http://127.0.0.1:8769 \
npm run dev -- --host 127.0.0.1 --port 5416 --strictPort
```

Open City Studio, select `logistics.native-parcel.p02`, load its reference
draft, save/export the workspace and compile it. Record the returned
compilation ID and run ID. Save the actual compilation response JSON if you
will use the runtime capture. The optional configuration capture performs
these same visible actions and saves that response as
`final-configuration/compilation-result.json`:

```bash
node tools/capture_city_configuration.mjs --origin http://127.0.0.1:5416 \
  --registration logistics.native-parcel.p02 \
  --output "$PARCEL_OUTPUT/final-configuration"
```

## Reproduce a live run, reconnect and observe

After compilation, stop the two preparation services and launch the complete
stack against that immutable compilation. The `--control-execution-output`
directory must be fresh. Set `PARCEL_COMPILATION_ID` from the actual UI
response; the launcher does not compile or replace that response. Its authoring
service receives a fresh compilation output directory, while Control reads
the existing immutable compilation directory. Omitting `--compilation-output`
from the launcher gives the authoring service a fresh directory in its private
run directory. The saved authoring output can be reused after its preparation
service releases the publisher lock.

```bash
python tools/run_stack.py \
  --control-compilation-id "$PARCEL_COMPILATION_ID" \
  --control-compilation-root "$PARCEL_OUTPUT/final-compilations" \
  --control-execution-output "$PARCEL_OUTPUT/execution" \
  --control-runner-config "$PARCEL_RUNNER_CONFIG" \
  --authoring-output "$PARCEL_OUTPUT/authoring" \
  --sources-manifest "$PARCEL_SOURCES_MANIFEST" \
  --native-scenes-manifest "$PARCEL_OUTPUT/final-registry/native-scenes.json" \
  --authoring-port 8771 --control-port 8769 --frontend-port 5416 \
  --credentials-file "$PARCEL_PRIVATE_CREDENTIALS"
```

The credentials file must not exist, and its parent directory must already
exist. The launcher creates that file with mode 0600. Use its bootstrap
values only in the password fields under `正式运行控制`. Load the catalog,
select the compiled run and start it. Use the visible play control and select
the carrier's external follow view. Disconnecting the browser does not stop
the runtime. After login, reconnect using the catalog's existing start
identity; do not create a new start identity for the same operation. This
reproduction uses `ControlRunManager`. After sealing, click `加载封存回放`
to load its history; this differs from the read-only publication catalog
described above.

The runtime capture automates this same UI flow, including disconnect,
login and reconnect. Use `--renderer hardware` on the delivery host and
`--replay` to include the same run's full sealed playback after live capture.
The renderer option selects Chromium rendering and does not change physics.
Use a fresh capture output directory:

```bash
node tools/capture_native_parcel.mjs --origin http://127.0.0.1:5416 \
  --control http://127.0.0.1:8769 \
  --credentials-file "$PARCEL_PRIVATE_CREDENTIALS" \
  --compilation-result "$PARCEL_OUTPUT/final-configuration/compilation-result.json" \
  --output "$PARCEL_OUTPUT/final-watchable" --timeout-seconds 9000 \
  --request-timeout-seconds 300 --renderer hardware --replay
```

Add `--attach` to require an already started run, including a completed run
for sealed playback. The append-only
`runtime-stream-evidence.jsonl` contains sanitized connection and public
event records; authoritative sealed trace bytes remain separate. The video,
screenshots and replay evidence must show actual motion, pickup and delivery.
A command receipt or a moving cursor alone is not a successful task. Wait for sealing and the
independent verifier, then inspect the actual motion and parcel transitions
in the public replay. The verifier requires loaded airborne progress toward
the destination, exact destination contact, landing, disarm and consistent
custody evidence; replaying business events alone cannot produce a verified
success.

## Recover publication and capture the read-only replay

Publication recovery completed for the recorded run. The recovery CLI
accepts only the recorded publication failure with passed sealed
verification. It copies the seals independently, projects the same run, and
records the original summary and recovery receipt. Use a fresh `publication/`
output; preserve any previous attempt.

```bash
PARCEL_DELIVERY=validation/platform-0.1
PARCEL_RECORDED_RUN_ID=4e39a29217c0e15ad52b8d7fe5c5d5d7b256e83d0b644b00569bb4ee2ef2ceeb
PARCEL_RECORDED_COMPILATION_ID=b76081905ec64aa146f74197d5e1daa605ba25a078b13a898f6c0f99edb91c44
python tools/finalize_verified_run.py \
  --compilation-root "$PARCEL_DELIVERY/final-compilations" \
  --compilation-id "$PARCEL_RECORDED_COMPILATION_ID" \
  --runner-config "$PARCEL_DELIVERY/runner.yaml" \
  --source-run-root "$PARCEL_DELIVERY/execution/$PARCEL_RECORDED_RUN_ID" \
  --output-root "$PARCEL_DELIVERY/publication"
```

After successful publication, its `replay-runner.json` supplies the read-only
service's output root. The service uses the same immutable suite with
`--sealed-run-id` under `SealedReplayManager`. Use the authenticated catalog
and `打开已封存回放` as described above. Set `PARCEL_SEALED_CAPTURE_OUTPUT`
to a fresh directory, then capture through that actual action:

```bash
node tools/capture_native_parcel.mjs --origin http://127.0.0.1:5416 \
  --control http://127.0.0.1:8769 \
  --credentials-file "$PARCEL_PRIVATE_CREDENTIALS" \
  --compilation-result "$PARCEL_DELIVERY/final-configuration/compilation-result.json" \
  --output "$PARCEL_SEALED_CAPTURE_OUTPUT" \
  --renderer hardware --sealed-only --replay --timeout-seconds 9000 \
  --request-timeout-seconds 300 --poll-seconds 5
```

`--sealed-only` requires `--replay` and rejects `--attach`. It authenticates
and opens registered replay access without Start, runtime-status, SSE or
live reconnect requests. Capture checks the same run's passed public report,
all five goals and the complete history through tick 300, then records actual
UI playback. The completed capture recorded continuous playback from tick 1
through tick 300, all three parcel stages, and zero Start or live-control
requests. The separate live capture retains the actual disconnect/login/
reconnect evidence for this same run.
