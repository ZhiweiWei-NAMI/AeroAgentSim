# Native parcel platform 0.1

The parcel participant flies from the destination pad to the pickup hub,
lands and disarms, takes custody, flies back, then lands and disarms on the
destination pad before delivery. PX4/Gazebo supplies motion and contact
measurements. The business provider owns parcel custody. The independent
verifier checks their sealed records, including actual airborne transport.

## Open the platform

For the local delivery, open `http://127.0.0.1:5415/city-studio.html` to inspect
the configuration, or `http://127.0.0.1:5415/?view=replay` to select the public
trace for the completed run. The service is bound to the host loopback address;
remote browsers need forwarding to that host.

The local delivery directory is `validation/platform-0.1/`. It contains the
saved UI workspace, immutable compilation, pinned image lock, sealed run,
and browser recordings. The final evidence manifest identifies their exact
paths and measured outcomes. Raw runs, original city assets, recordings and
credentials are retained in local storage rather than published in Git.

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
- Image lock: `validation/platform-0.1/images/images.json`.
- Calibration: `docs/p02-native-logistics/calibration-review/calibration.json`.
- Runner: `validation/platform-0.1/runner.yaml`.
- Authoring source manifest: `validation/p02_platform_native_parcel/authoring-sources.json`.

The paths above identify retained input data; historical directory names are
not platform version numbers. On another machine, supply the same declared
assets and pinned identities at explicit local paths. The source manifest
must reference actual source files accessible to the authoring service.

Use a fresh output directory for preparation:

```bash
python tools/prepare_native_parcel_scene.py \
  --source "$PARCEL_CITY_SOURCE" --image-lock "$PARCEL_IMAGE_LOCK" \
  --calibration docs/p02-native-logistics/calibration-review/calibration.json \
  --output "$PARCEL_OUTPUT/scene"
python tools/register_native_parcel_scene.py \
  --bundle "$PARCEL_OUTPUT/scene/native-bundle" \
  --output "$PARCEL_OUTPUT/registered-scene"
```

To rebuild changed components, use `tools/build_native_parcel_images.py
--base-image-lock ... --output ... --component agent --component verifier`.
It builds from a recorded source inventory, pushes to the local registry and
writes an updated digest lock. Other component identities remain explicit.
The native flight and traffic Dockerfiles require their pinned parent images.

The scene keeps the 300-second horizon, 0.5 m flight landing height condition,
0.15 m parcel vertical tolerance and 0.2 m/s stationary limit. Its two legs
span 60 m horizontally. The declared 15 m/s cruise profile gives only a
4-second ideal horizontal lower bound; climbing, descent, acceleration and
settling are additional. This bound is not a measured flight duration or an
energy-performance claim.

## Save and compile through the UI

Start the authoring API with the prepared registration and real source manifest:

```bash
python -m aero_bench.authoring.api --host 127.0.0.1 --port 8770 \
  --output "$PARCEL_OUTPUT/authoring" \
  --compilation-output "$PARCEL_OUTPUT/compilations" \
  --sources-manifest "$PARCEL_SOURCES_MANIFEST" \
  --native-scenes-manifest "$PARCEL_OUTPUT/registered-scene/native-scenes.json"
```

In another terminal, start Vite from `frontend/`:

```bash
AERO_AUTHORING_API_TARGET=http://127.0.0.1:8770 \
AERO_CONTROL_API_TARGET=http://127.0.0.1:8768 \
npm run dev -- --host 127.0.0.1 --port 5415 --strictPort
```

Open City Studio, select `logistics.native-parcel.p02`, load its reference
draft, save/export the workspace and compile it. Record the returned
compilation ID and run ID. Save the actual compilation response JSON if you
will use the runtime capture. The optional configuration capture performs
these same visible actions and saves that response as
`configuration/compilation-result.json`:

```bash
node tools/capture_city_configuration.mjs --origin http://127.0.0.1:5415 \
  --registration logistics.native-parcel.p02 \
  --output "$PARCEL_OUTPUT/configuration"
```

## Start, reconnect and observe

After compilation, stop the two preparation services and launch the complete
stack against that immutable compilation. The `--control-execution-output`
directory must be fresh. Set `PARCEL_COMPILATION_ID` from the actual UI
response; the launcher does not compile or replace that response. Its authoring
service receives a fresh compilation output directory, while Control reads
the existing immutable compilation directory.

```bash
python tools/run_stack.py \
  --control-compilation-id "$PARCEL_COMPILATION_ID" \
  --control-compilation-root "$PARCEL_OUTPUT/compilations" \
  --control-execution-output "$PARCEL_OUTPUT/run" \
  --control-runner-config "$PARCEL_RUNNER_CONFIG" \
  --authoring-output "$PARCEL_OUTPUT/authoring" \
  --sources-manifest "$PARCEL_SOURCES_MANIFEST" \
  --native-scenes-manifest "$PARCEL_OUTPUT/registered-scene/native-scenes.json" \
  --authoring-port 8770 --control-port 8768 --frontend-port 5415 \
  --credentials-file "$PARCEL_PRIVATE_CREDENTIALS"
```

The credentials file must not exist, and its parent directory must already
exist. The launcher creates that file with mode 0600. Use its bootstrap
values only in the password fields under `正式运行控制`. Load the catalog,
select the compiled run and start it. Use the visible play control and select
the carrier's external follow view. Disconnecting the browser does not stop
the runtime. After login, reconnect using the catalog's existing start
identity; do not create a new start identity for the same operation.

The runtime capture automates this same UI flow, including disconnect,
login and reconnect. It verifies actual playback and carrier position changes:

```bash
node tools/capture_native_parcel.mjs --origin http://127.0.0.1:5415 \
  --control http://127.0.0.1:8768 \
  --credentials-file "$PARCEL_PRIVATE_CREDENTIALS" \
  --compilation-result "$PARCEL_OUTPUT/configuration/compilation-result.json" \
  --output "$PARCEL_OUTPUT/watchable" --timeout-seconds 9000 \
  --request-timeout-seconds 300
```

Add `--attach` to capture an already started run. Recordings and JSON contain
public state and sanitized connection evidence. A command receipt or a
moving cursor alone is not a successful task. Wait for sealing and the
independent verifier, then inspect the actual motion and parcel transitions
in the public replay. The verifier requires loaded airborne progress toward
the destination, exact destination contact, landing, disarm and consistent
custody evidence; replaying business events alone cannot produce a verified
success.
