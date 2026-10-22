# Compare two allocation algorithms in one command

An experiment repeats the same template across a grid of knob values and
matched random seeds. Each cell is an ordinary headless run with its own
journal, manifest and viewer data; open the output root in the console to
inspect or compare those runs.

```sh
aeroagentsim experiment parcel-delivery \
  --vary allocator=nearest,auction --seeds 3 --jobs 2 --out runs
```

This launches six runs: two allocators, each with seeds 0, 1 and 2. Seeds are
matched across configurations so allocation strategies see the same seeded
arrival process. Repeating the command uses new run directories and the same
seed values. Headless experiments use fast pacing; this changes wall time,
not the simulated event times.

Add another axis to run the full factorial grid:

```sh
aeroagentsim experiment parcel-delivery \
  --vary allocator=nearest,auction,hungarian \
  --vary fleet_size=4,8 --seeds 3 --jobs 3 --horizon 300
```

That is 18 independent runs. `--horizon` sets the simulated duration in
seconds. `--jobs` bounds the number of worker processes. Override values pass
the same typed knob validation as `run-template`; an unknown knob, empty
axis, invalid choice or out-of-range value fails before work is launched.
Use `--templates-root DIR` for an operator-owned registry.

The command reports its experiment id and artifact directory. Under
`runs/.experiments/<id>/`, `experiment.md` contains the configuration table
and links to runs; `experiment.csv` contains one row per configuration and
KPI with mean, sample standard deviation, observed sample count, unit and a
best-value flag. Numeric KPIs come from the committed journals. A metric
without measurements stays unmeasured; a single observed sample has no
sample standard deviation. Failed runs retain their status and error and do
not become successful observations. Inspect the sample counts before making
a claim about a strategy.

Best values are highlighted separately for each KPI, using its declared
direction: more completed tasks or a higher completion rate, and less
latency, energy use, conflict or violation count. There is no combined score.
For example, an allocator that completes more jobs may use more energy;
compare both outcomes and the run horizons.

## Launch from the API

```sh
curl -H 'Content-Type: application/json' \
  -d '{"vary":{"allocator":["nearest","auction"]},"seeds":3,"jobs":2,"horizon_s":300}' \
  http://127.0.0.1:8002/v1/templates/parcel-delivery/experiments
curl http://127.0.0.1:8002/v1/experiments/EXPERIMENT_ID
```

The POST returns after validating the plan. Poll the GET for status, total,
completed and failed run counts, individual run outcomes and the aggregate
table. Both routes use the existing optional token and origin boundary.
The runs are visible through `/v1/runs` and
`/v1/runs/compare?ids=RUN_A,RUN_B`, with their applied knobs, algorithms and
experiment metadata pinned. After a service restart, completed experiments
remain available and unfinished coordinators are marked interrupted rather
than silently resumed.

## From the browser

Open **Records** (the `R` shortcut in the Workbench) and choose **Experiments**.
Select a template, then pick axes from its declared knobs: `choice` options are
checkbox lists, `boolean` knobs offer both typed values, and `integer`/`number`
knobs accept comma-separated values that are checked against their declared
type and bounds before submission. Matched seeds are a separate field; a knob
that points at `/run/seed` is shown disabled and cannot be selected as an axis.
`jobs` bounds worker processes, and the horizon override is optional — leave it
blank to use the template's own duration, shown next to the field. A knob that
points at
`/run/until_ns` cannot be varied while the horizon override is set.

The preview multiplies the axis value counts and seeds. The browser refuses
more than 24 runs per experiment and says so before submitting; that cap is a
UI guard, not a server limit. Larger grids remain available through the CLI.
Review the plan once, then confirm once. A rejected request (for example an
invalid knob value) is reported with the server's error and no experiment is
claimed. If the submission response is lost after the request was sent, the
panel says `submission status unknown; check Records` and does not retry the
POST automatically, because the server may already have accepted it.

A successful POST returns after validation; the panel stores the returned id in
the page URL as `?experiment=<id>` (with `records=1`), so the page can be
bookmarked and reloaded. It follows that id with the existing
`GET /v1/experiments/<id>` and polls while the server reports `created` or
`running`. After a service restart, an unfinished experiment is reported as
`interrupted` by the server; it is not resumed. There is no experiment-list,
resume or cancel API, so the browser can only reopen an id it already knows.

Each configuration shows every expected cell, including runs reported as failed
or interrupted. Aggregate rows show exactly the server's `mean`, `std` and `n`;
`null` appears as unavailable, and a single observed sample has no sample
standard deviation (its `std` is unavailable). Failed runs are not counted as
observations. A run cell links to replay and can be selected for the existing
2–4 run comparison. The panel does not combine metrics into a score, does not
rank configurations, and makes no general performance claim from a few seeds:
read `n`, units and every reported metric before drawing a conclusion.

## Read the low-altitude results

The parcel template reports released, assigned, completed and failed tasks;
mean and 95th-percentile completion latency; gross battery discharge;
distance flown; and committed conflict, geofence and weather-hold events.
Completion rate uses released tasks as its denominator. An on-time rate
requires an explicitly committed deadline and completion time; the parcel
template does not invent delivery deadlines.

Viewer paging uses a rebuildable SQLite index beside each run. It retains
projected cuts and the lifecycle, KPI and narrative state at its last
committed cursor. Seeking or reconnecting uses indexed cuts, and a live run
adds only its new committed tail. The journal and pinned declarations remain
the source of truth.

## Generate template thumbnails

From a source checkout with the camera's Node runtime and Chromium installed:

```sh
PYTHONPATH=aerokernel:src python tools/templates/thumbnails.py \
  parcel-delivery traffic-accident
```

The tool runs each template, reads a representative committed cut at 45 s,
resolves inherited spatial presentations, and uses the existing capture
renderer with the shipped lite-city geometry. `--run PATH` may be repeated
once per template to reuse pinned journals; `--at-seconds S` selects another
cut. The overview may crop distant entities, and marker size is a viewing aid.
Poses and source timestamps come from the journal. The script writes
`thumbnail.png` at 640 × 360, at most 120 KB, and declares it in `template.yaml`.
The API exposes its URL. Oversized frames require Pillow for palette
quantization; the shipped images did not need quantization.

Matched seeds override a template's source seed. A knob that points directly
at `/run/seed` cannot be varied; use `--seeds`. A duration knob cannot be varied
at the same time as an explicit `--horizon` override. Run manifests record the
actual applied controls.
