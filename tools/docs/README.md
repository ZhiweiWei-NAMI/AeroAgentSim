# Record console product flows

From the repository root:

```sh
AEROAGENTSIM_AEROGRAPH_ROOT=/path/to/AeroGraph \
AEROAGENTSIM_TRAFFIC_ASSET_ROOT=/path/to/city/assets \
AEROAGENTSIM_DOCS_PYTHON=/path/to/installed/venv/bin/python \
scripts/record-docs-media.sh --gates
```

The command builds the UI, starts its own backend on a free port, creates a real
completed city-capture run, records eight 1280×800 Playwright stories, and writes
960px/12fps GIFs to `docs/media`. It stops only its own server. Browser videos,
logs, productive-segment timings and the size/duration report stay under
`/tmp/aas-q/e3e`. Cursor highlights and captions are injected by tests only. A black calibration
prelude, excluded from the GIF, aligns segment marks with the actual screencast
start rather than assuming Playwright starts recording at page creation.

The agent replay runs the shipped recorded model responses through the real
LangGraph executor. The live-provider story configures a future run; it makes no
paid model call. Scripted-provider token usage is explicitly zero, because no
model inference occurs. Recording preparation sets an explicit simulated deadline
through the demo end (the current endpoint default is an absolute 1 second). The operator story sets a 300-second wall-clock wait in its temporary draft,
so paced software-rendered recording does not expire the current 10-second operator
window. Simulated times and shipped scenario files are unchanged. Native plugin stories configure drafts and show capability
checks; they do not claim to launch PX4 or SUMO.

An existing genuinely completed run can be imported with `--replay-source DIR`.
Use a fresh `--work-dir` when re-recording an imported run. The script installs
`imageio-ffmpeg` in that scratch directory; alternatively supply `--ffmpeg PATH`
or `FFMPEG`. The selected ffmpeg is also linked into Playwright’s scratch video-encoder registry;
no browser cache or npm dependency in the shared environment is modified.

Slow backend waits remain in the original videos but are omitted using each
flow's marked productive segments. For explicit post-processing adjustments:

```sh
scripts/record-docs-media.sh --speed 06-simulate=3.5 --trim 00-overview=0:26
```

`make_gifs.py` also accepts these options directly. It uses separate palettegen
and paletteuse passes with a 64-color palette and no dithering (to keep city textures compact), enforcing 6 MB per GIF (8 MB for the overview) without
silently reducing resolution or frame rate. All eight recordings are required.
Re-record after the operator-wait state and procedural-city jobs merge.

After selector-only changes, resume against the same owned backend storage with
`--skip-build --reuse-context /tmp/aas-q/e3e/context.json`. Its recorded local
port must be free. For targeted reruns, preserve `videos/recordings.json` and use
Playwright’s filename filter; successful videos are copied out of its disposable
output directory.
