# Record workbench documentation media

From the repository root, select the installed Python and Chromium explicitly:

```sh
export AEROAGENTSIM_CHROMIUM=/path/to/chrome
# Optional: use an installed source catalog or city pack.
export AEROAGENTSIM_AEROGRAPH_ROOT=/path/to/AeroGraph
tools/docs/record-docs-media.sh --python .venv/bin/python \
  --work-dir /tmp/aeroagentsim-docs-media --port 18960
```

The tool builds into `WORK_DIR/dist`, starts the production service with isolated
runs and workspaces on the chosen free port (18960–18969), records six English
1280 × 800 [workbench stories](../../docs/media/README.md), then converts them to
GIFs in `docs/media`. It stops only its own server. It never writes to
`frontend/dist`. With the optional catalog and asset environment variables unset,
it uses the packaged registry and resolves the installed city pack, or the shipped
lite OSM city when no detailed pack is installed. `AEROAGENTSIM_TRAFFIC_ASSET_ROOT`
selects a separately installed city asset directory.

Each story uses the actual UI and backend. The overview demonstrates the full
lifecycle, including an initialization held at t = 0. The operation story uses
Interactive food delivery, an 80-second east-kitchen preparation setting and a
20-second restaurant delay. Why and Records reuse the real runs created by those
stories. The Photos story runs the shipped scheduled accident and its own capture configuration;
it requires real stored browser-rendered pixels. No provider calls or invented
journal records are prepared. Failures remain failures, with screenshots and
browser error logs in the scratch directory.

The recordings also produce `WORK_DIR/screenshots/workbench-{food,food-zh,city}.png`
at 1600 × 1000, from separate real scheduled food-delivery and city-day runs.
The screenshot runs are stopped after capture; 1920 × 1080 review images remain in scratch. The full conversion command copies these three files into `docs/media` after
the stories pass; review the published screenshots alongside the GIFs. `--skip-gifs`
keeps both the existing GIFs and published screenshots untouched.
All source WebMs, productive-segment timings, browser errors and the conversion
report remain in scratch. Cursor highlights and captions are added only by the
recording fixture. A black calibration prelude aligns marks to Chromium's actual
video start and is excluded from the exported footage.

By default GIFs use 960 px, 12 fps and a 48-color palette without dithering. The
existing two-pass ffmpeg conversion accepts explicit tuning, enforces 15–40 s
per story, a 6 MB per-GIF budget (8 MB for overview), and a 25 MB aggregate budget
for GIFs and PNGs in `docs/media`:

```sh
tools/docs/record-docs-media.sh --python .venv/bin/python \
  --work-dir /tmp/aeroagentsim-docs-media --port 18960 \
  --fps 8 --width 800 --speed 00-overview=1.6 \
  --speed 02-run-and-operate=3.4 --budget 02-run-and-operate=8
```

`--budget FLOW=MB` explicitly changes one story’s size allowance; the 25 MB aggregate cap still applies.
`--trim FLOW=START:END` trims the assembled productive timeline. All six current
stories are required for a successful conversion; old page recordings cannot be published.
The converter preserves available real footage and reports any missing story as a failure; it never fills a gap with substitute footage.
The ffmpeg binary is installed via `imageio-ffmpeg` into a scratch virtual
environment, or supplied with `--ffmpeg PATH` / `FFMPEG`. Playwright's encoder
uses a scratch registry; shared browser caches and dependencies are untouched.
If Chromium is unset, the tool resolves Playwright's installed executable first.

For a selector correction, reuse the same scratch build, context and storage:

```sh
tools/docs/record-docs-media.sh --python .venv/bin/python \
  --work-dir /tmp/aeroagentsim-docs-media --skip-build --skip-gifs \
  --reuse-context /tmp/aeroagentsim-docs-media/context.json --flows 03-why
```

The context records the owned server port, which must be free. `--flows` accepts
Playwright's filename filter and preserves other successful videos. Record
00-overview and 02-run-and-operate before dependent Why and Records reruns.
Use `--skip-gifs` for browser verification without updating published GIFs.
`--gates` additionally runs the frontend typecheck and Vitest suite before
recording. After replacing media or captions, run `python3 tools/docs/check_links.py`.
