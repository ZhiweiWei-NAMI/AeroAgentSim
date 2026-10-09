# Record console product flows

From the repository root:

```sh
scripts/record-docs-media.sh --gates
```

The script uses the installed Python and Playwright only: `--python PATH` (or
`AEROAGENTSIM_DOCS_PYTHON`) selects the interpreter that provides `uvicorn` and
the backend dependencies, defaulting to `python` resolved through `command -v`.
No private virtual environment or asset path is selected implicitly. With both
`AEROAGENTSIM_AEROGRAPH_ROOT` and `AEROAGENTSIM_TRAFFIC_ASSET_ROOT` unset, this
records the public clone's packaged registry snapshot and lite OSM city. Set
these variables explicitly only when recording a separately installed source
catalog or asset pack.

Chromium is selected through `AEROAGENTSIM_CHROMIUM`. When it is unset, the
script resolves Playwright's own Chromium via
`require('playwright').chromium.executablePath()` before selecting a scratch
video-encoder registry; if no executable can be verified, run
`npx playwright install chromium` (in `frontend/`) or set `AEROAGENTSIM_CHROMIUM`.

The command builds the UI, starts its own backend on a free port, creates a real
completed city-capture run, records eight 1280×800 Playwright stories, and writes
960px/12fps GIFs to `docs/media`. Pass `--skip-gifs` to stop after the
recordings, so flow verification leaves the existing `docs/media` GIFs
untouched. It stops only its own server. Browser videos, logs,
productive-segment timings and the size/duration report stay under the script's
scratch work directory (a fresh temporary directory by default; pass `--work-dir`
or set `AEROAGENTSIM_DOCS_WORK_DIR` to keep it stable). Cursor highlights and
captions are injected by tests only. A
black calibration prelude, excluded from the GIF, aligns segment marks with the
actual screencast start rather than assuming Playwright starts recording at page
creation. The hero and explorer omit their first 0.8 seconds of compositor
settling; explicit `--trim` options override those defaults.

The agent replay runs the shipped recorded model responses through the real
LangGraph executor. The live-provider story selects a decision provider profile
(`AEROAGENTSIM_DOCS_PROVIDER_PROFILE`, default `default`, a builtin operator
profile) for a future run; it makes no paid model call. Scripted-provider token
usage is explicitly zero, because no model inference occurs. Recording
preparation retains the endpoint's relative per-decision timeout, so later
triggers receive their own deadline. The live story uses the shipped 1 Hz, real-time demo
and the console's operator controls. It records normal-speed live motion and
omits long waits between productive actions. Native plugin stories configure
drafts and show capability checks; they do not claim to launch PX4 or SUMO.

An existing genuinely completed run can be imported with `--replay-source DIR`.
Use a fresh `--work-dir` when re-recording an imported run. The script installs
`imageio-ffmpeg` in that scratch directory; alternatively supply `--ffmpeg PATH`
or `FFMPEG`. The selected ffmpeg is also linked into Playwright’s scratch
video-encoder registry; no browser cache or npm dependency in the shared
environment is modified.

Slow backend waits remain in the original videos but are omitted using each
flow's marked productive segments. For explicit post-processing adjustments:

```sh
scripts/record-docs-media.sh --speed 07-visualize=2 --trim 00-overview=0:26
```

`make_gifs.py` also accepts these options directly. It uses separate palettegen
and paletteuse passes with a 48-color palette and no dithering (to keep city
textures compact), enforcing 6 MB per GIF (8 MB for the overview) without
silently reducing resolution or frame rate. All eight recordings are required.
The simulation story keeps display interpolation separate from exact committed
state.

After selector-only changes, resume against the same owned backend storage with
`--skip-build --skip-gifs --reuse-context WORK_DIR/context.json`, where `WORK_DIR`
is the scratch work directory of the recording being resumed. Its
recorded local port must be free. For targeted reruns, preserve
`videos/recordings.json` and use Playwright’s filename filter; successful videos
are copied out of its disposable output directory.
