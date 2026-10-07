# AeroBench native parcel platform 0.1

AeroBench lives in `aero-bench/` inside the official
[ZhiweiWei-NAMI/AeroAgentSim repository](https://github.com/ZhiweiWei-NAMI/AeroAgentSim).
The parcel participant flies from the destination pad to the pickup hub,
lands and takes custody, flies back, then lands and disarms before delivery.
PX4/Gazebo supplies actual motion and contact; the business provider owns
custody and the independent verifier evaluates their sealed evidence.

## Open the retained delivery

From the AeroAgentSim repository root:

```bash
./run-aero-bench
```

After readiness, open <http://127.0.0.1:5416/?view=live>. Under
`正式运行控制`, enter the bootstrap values from the local mode-0600 file
`aero-bench/credentials/platform-0.1.json`, load the catalog, select the
parcel run and click `打开已封存回放`. Close the controls panel, select
`uav.p02.carrier`, choose `外部跟随` and play. This is a read-only
`SealedReplayManager` session; it starts no simulation and needs no runtime
reconnect. City Studio is at <http://127.0.0.1:5416/city-studio.html>.

For SSH or VS Code Remote, forward only remote port **5416** to local 5416.
Both `http://127.0.0.1:5416` and `http://localhost:5416` are launcher origins.
Browser Control calls go through the frontend's `/v1` proxy; authoring uses
`/authoring/v1`. Backend ports need no separate browser forwarding. If VS Code chooses a
different local port, set its forwarded local port to 5416.

The safe local pointer is `validation/platform-0.1/connection.json` inside
`aero-bench/`. The canonical serving configuration is
`validation/platform-0.1/replay-runner.json`. The original `runner.yaml`
remains byte-identical for provenance and contains its historical output
path; use the canonical configuration for the relocated delivery.

Watch the recorded video at
<http://127.0.0.1:5416/platform-0.1/native-parcel-0.1.mp4>, or open
`validation/platform-0.1/final-watchable/native-parcel-0.1.mp4` locally.
`video-evidence.json` records the source recording, trim offsets and hashes.
The adjacent pickup, transport, delivered and result images are decoded
frames from that recording.

## Recorded result and retained files

Run `4e39a29217c0e15ad52b8d7fe5c5d5d7b256e83d0b644b00569bb4ee2ef2ceeb`
was UI-compiled as
`b76081905ec64aa146f74197d5e1daa605ba25a078b13a898f6c0f99edb91c44`.
Pickup completed at tick 60 and delivery at tick 128; the horizon stayed
300 ticks. The sealed verifier passed all five goals. Disconnect, login and
reconnect continued the same run with 11 issued commands and two custody
transfers, without repeated actions.

The original second-run summary is still `error` with
`public_trace_projection_failed`. Recovery completed only the public trace
projection from the same immutable runtime and verification seals; the
publication summary is `passed`. The original failed summary was not
rewritten. Actual sealed replay was recorded through tick 300. See
[the evidence manifest](platform-0.1-evidence.json)
for exact identities and measured outcomes.

Paths below are relative to `aero-bench/validation/platform-0.1/`:

- `inputs/native-city/`, `inputs/authoring-sources.json`, `inputs/osm/`:
  active retained native source and authoring inputs.
- `verifier-image/images.json`: six-component digest-pinned image lock.
- `final-scene/native-bundle/`, `final-registry/`: prepared scene and registration.
- `final-configuration/`, `final-compilations/`: saved UI response and immutable compilation.
- `execution/`: original episode, runtime seal, verification seal and original summary.
- `publication/`: recovered public trace and same-run recovery receipt.
- `final-watchable/`: actual replay video, decoded frames and capture evidence.

Relocation uses separate active path configuration while retaining the
original artifact hashes. Historical paths inside source/evidence records
remain provenance; they were not globally replaced. Large run data, native
assets and private credentials stay local rather than in Git.

## Configure and reproduce a physical run

Run the following commands from `aero-bench/`. Python 3.10+, installed
project dependencies, Node/frontend lockfile dependencies and retained
viewer assets are required. Physical execution additionally needs the
user-accessible Docker daemon and declared digest-pinned OCI images. Browser
capture needs Playwright Chromium; install it from `frontend/` with
`npx playwright install chromium` when absent.

City Studio can load `logistics.native-parcel.p02`, save/export the workspace
and compile it. Record its actual compilation ID and output directory.
Retained native source is under `inputs/native-city/`; fresh scene preparation
uses `tools/prepare_native_parcel_scene.py` and registration uses
`tools/register_native_parcel_scene.py`, with the pinned image lock and
reviewed pose calibration.

Set `PARCEL_NEW_OUTPUT` to a new absolute output location. The root launcher
opens the retained sealed delivery; new physical execution uses explicit
compiled mode. Stop the root launcher before reusing its ports. Set
`PARCEL_COMPILATION_ID` and `PARCEL_COMPILATION_ROOT` from the actual saved
compilation. New root-launcher UI compilations use `authoring/compilations/`;
the retained example is under `final-compilations/`.

```bash
PARCEL_DELIVERY=validation/platform-0.1
mkdir -p "$PARCEL_NEW_OUTPUT"
python tools/run_stack.py \
  --control-compilation-id "$PARCEL_COMPILATION_ID" \
  --control-compilation-root "$PARCEL_COMPILATION_ROOT" \
  --control-execution-output "$PARCEL_NEW_OUTPUT/execution" \
  --control-runner-config "$PARCEL_DELIVERY/replay-runner.json" \
  --authoring-output "$PARCEL_NEW_OUTPUT/authoring" \
  --sources-manifest "$PARCEL_DELIVERY/inputs/authoring-sources.json" \
  --native-scenes-manifest "$PARCEL_DELIVERY/final-registry/native-scenes.json" \
  --authoring-port 8771 --control-port 8769 --frontend-port 5416 \
  --credentials-file "$PARCEL_NEW_OUTPUT/credentials.json"
```

Compiled mode overrides the runner configuration's `output_root` with the
explicit `--control-execution-output`; that directory must not exist. Its
parent must exist. The new credentials file must also be absent. Omitting
`--compilation-output` gives the authoring service a fresh compiler directory.
Log in, select the compiled run and start it. Disconnect/login/reconnect
uses the existing start identity. After sealing, `加载封存回放` opens the
same run's history in this `ControlRunManager` workflow.

The scene keeps its 300-second horizon, 0.5 m flight landing condition,
0.15 m parcel vertical tolerance and 0.2 m/s stationary limit. Its two legs
span 60 m horizontally. The authored 15 m/s cruise profile implies a
4-second ideal horizontal lower bound under that assumption; it neither
measures nor sets the PX4 command speed limit. Actual duration comes from
sealed flight measurements. Route-derived metadata is 60 motor vehicles,
36 pedestrians and 12 bicycles, with the same pinned SUMO demand and world.

To record an already published replay, use a fresh capture directory:

```bash
node tools/capture_native_parcel.mjs --origin http://127.0.0.1:5416 \
  --control http://127.0.0.1:8769 \
  --credentials-file credentials/platform-0.1.json \
  --compilation-result validation/platform-0.1/final-configuration/compilation-result.json \
  --output "$PARCEL_CAPTURE_OUTPUT" --renderer hardware --sealed-only --replay \
  --timeout-seconds 9000 --request-timeout-seconds 300
```

`--sealed-only` requires `--replay` and rejects `--attach`. It opens the
actual authenticated replay action without Start or live-control requests.
For a new live run, use its new credentials and actual compilation response,
with `--replay` and without `--sealed-only`; `--attach` requires an existing
start identity. Capture stores sanitized append-only stream evidence apart
from the authoritative sealed trace.
