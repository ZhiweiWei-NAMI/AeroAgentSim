# Logistics and inspection packs (P5)

The packs are scenario data and small `aerokernel.sdk.ContextEngine` plugins.
They use platform motion, workflow, scenario compilation and journaling. They
do not import AeroBench, SimPy or any legacy domain manager. The kernel stays
independent of aviation and of these workflows.

`scenarios/packs/aerograph.snapshot.json` pins a read-only compilation of
`oo:UAV`, `oo:Order`, `oo:Facility`, `oo:ObservationRecord` and
`agp:type:ActivityRestriction`, selecting the real
`he.aircraft.position_enu_m` field. Its provenance includes source hashes and
review dispositions. The remaining `aas:*` types and `packs.*` fields are
explicitly authored model extensions in the YAML; they do not claim to be
adopted AeroGraph definitions or live external measurements. The configured
type, field, relation and message IDs can be rebound to compatible descriptors.
No AeroGraph files or build scripts were changed or executed.

## Run and measure

The required Python 3.11 interpreter has the test tools. This checkout's
existing platform environment supplies PyYAML through `PYTHONPATH`, without
installing packages or changing either environment:

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src:.venv/lib/python3.11/site-packages
P5_PYTHON=/mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python

"$P5_PYTHON" -m aeroagentsim.services.cli run \
  scenarios/packs/logistics-small.yaml --out tests/packs/runs
"$P5_PYTHON" -m aeroagentsim.services.cli run \
  scenarios/packs/inspection-small.yaml --out tests/packs/runs
"$P5_PYTHON" -m aeroagentsim.services.cli metrics <run-directory>
```

The installed console entry point is `aeroagentsim metrics <run>`. A run
directory or an individual `journal.jsonl` is accepted. Metrics perform
engine-free kernel replay and derive results solely from committed journal
messages and the journal's pinned configuration. Replay integrity checking
can take longer than a live simulation; it is done once per CLI invocation.
`compute(kernel)` also accepts an already replayed kernel, avoiding another
replay. A corrupt/truncated/faulted/incomplete journal is never treated as a
successful run. No engine, LLM, backend, imagery or evidence sealing is used
by this path.

## Logistics contract

The authored small scenario has five carriers, two facilities and thirty
orders. Arrival times use the kernel partition's named `arrivals` RNG stream
with seed 42, a Poisson rate of 0.4 orders/s and integer nanoseconds. Scheduled
arrivals use an explicit nondecreasing time list, including simultaneous
releases. Orders are predeclared but cannot be assigned before release.
Release occurs at the first configured polling boundary at or after its time.

The workflow is `unreleased → queued → pickup → in_transit → handoff →
delivered → accepted/rejected`. An assignment reserves one pad at each
endpoint and a destination locker. The baseline deliberately holds both pad
reservations through delivery; it is conservative capacity admission rather
than a detailed taxi/pad scheduling model. Initial parcels consume source
locker capacity. Delivery moves storage occupancy to the destination; accepted
and rejected parcels continue to occupy their real locker.

Before assignment the pack reads actual selected position, velocity and
energy facts. It checks current carrier availability, pad and locker capacity,
and the acceleration-limited route budget. The energy budget includes approach,
transport, a return reserve, both dwell windows, polling/grid allowance and the
declared joule reserve. The kinematic example shares speed, acceleration and
energy coefficients with the motion engine. These are authored model
parameters, not inferred vehicle specifications.

Activity restrictions are entities of the configured AeroGraph restriction
type. Their explicit fields bind active status, canonical half-open interval,
ENU box and affected activities. Assignment conservatively checks the whole
approach/transport route against any interval overlapping the budgeted mission.
Segment/box boundary contact blocks admission. The small example's restriction
is outside its route; the test moves that same real restriction input onto the
route and verifies rejection. This model covers box restrictions and declared
activities; it is not a general evaluator of arbitrary regulatory ASTs.

Both pickup and handoff require a succeeded native motion receipt, current
position within the configured facility tolerance and speed below the stopped
threshold continuously for the facility's dwell time. Leaving or moving resets
the window. Dwell is sampled at the polling period; it makes no claim about
unobserved subperiod motion. Physical arrival and transportation completion
cannot directly produce business acceptance. A separate configured platform
workflow reviews a delivered order and emits its explicit decision after one
second; the command path also permits an external accept/reject decision.

Custody is a kernel relation from parcel to custodian with
`targets_per_source.maximum = 1`. Source facility, carrier and destination
facility assertions have explicit acquisition and half-open validity. Each
transfer closes the old edge and asserts a never-reused edge ID in one wave.
The kernel rejects overlapping current **or future** custodians. Acceptance
does not rewrite custody. Cancellation retains the actual holder and waits for
native cancellation cleanup before releasing operation reservations. A carrier
still holding a canceled/failed parcel cannot be assigned another order;
recovering that stranded parcel requires a future explicit recovery workflow.

Assignments support three policy choices:

- `deterministic`: stable identity order over feasible carriers, FIFO authored
  order iteration.
- `module:function`: an `AssignmentPolicy(order_id, eligible_ids)` callable
  returning an ordered subset of the feasible immutable carrier list.
- `external`: automatic assignment disabled. P6 can issue
  `packs.logistics.assign {order, carrier}` and receive actual accepted/rejected
  receipts from the same feasibility path. `packs.logistics.decide
  {order, accepted}` and `packs.logistics.cancel {order}` are also declared.

Failed native motion records a failed order rather than a delivery. No absence
or invalid numeric observation becomes zero, and no missing restriction status
becomes false. Empty-population means/rates are null with population counts,
because they have no mathematical denominator.

## Inspection contract

The small scenario flies an actual kinematic carrier out and back across two
waypoint targets and a rectangular area. It reads the selected committed
position and explicitly declared stabilized camera angles and FOV fields on
its sensor entity. Ground footprint corners are pinhole rays intersecting a
flat ground plane. FOVs are full angles in degrees. Camera orientation is
`Rz(yaw) Ry(tilt) Rx(roll)` in ENU; zero angles point the optical axis along
`-Z`, image X/Y along E/N, and positive yaw is mathematical rotation about U.
It is not PX4's compass-yaw or NED convention.

Observation records retain acquisition time, delayed availability, source
pose acquisition clock/mapping/rational timestamp, source pose availability,
position, angles, FOVs and the computed footprint. Dynamic records are created
in a committed lifecycle wave and populated in the following writer wave.
The observation-subject relation binds the concrete record to its carrier.
Acquisition is geometry computation time; the separate pose stamp exposes any
held/older source sample. The example uses a 1s sample period and 100ms release
delay.

Coverage for a waypoint means it lies inside at least one valid footprint.
Area coverage is the continuous union of clipped convex footprints divided by
target area; overlaps are not counted twice, and no sampling grid is used.
The summary is the unweighted mean of target percentages. Revisit times are
differences between distinct acquisition times with nonzero target coverage;
continuous viewing consequently yields the sample period. Times with no
covered sample are not invented. Fewer than two observations yield null
revisit statistics. Horizon-crossing, invalid FOV or below-ground geometry
produces an explicitly unsuccessful sensor sample with its reason and no
footprint; metrics expose invalid observation counts.

These are geometric observations. There is no terrain, occlusion, lens
distortion or image-based detection model. The current PX4 adapter exposes
pose, telemetry and contacts, **no camera image field**. A real camera path is
future work: bind actual backend images and their acquisition stamps when the
adapter exposes them. No generated bytes, progress fields or geometry record
is presented as imagery.

## KPI definitions and observed results

Logistics on-time rate is deliveries by their order deadlines divided by all
released orders. Mean delivery time is release to delivery over delivered
parcels. Energy per parcel is the difference between actual selected energy
samples at assignment and delivery; the journal preserves the samples' time
semantics. Utilization is assignment-to-delivery/failure/cancellation busy time
over fleet size times actual run duration; active jobs are clipped to the
journal's final boundary. The PX4 path converts real battery fraction using an
explicit declared capacity, so its joule KPI is a modeled estimate, not a
native joule meter.

Measured non-Docker results, recomputed from the journals:

| Scenario | Duration | Result |
| --- | ---: | --- |
| logistics-small | 180 s | 30 released, 30 delivered, 30 accepted; on-time rate 1.0; mean delivery 27.0642473286 s; energy 291.6666666667 J/parcel; utilization 0.4833333333 |
| inspection-small | 20 s | 19 observations, 0 invalid; mean target coverage 100%; corridor coverage 99.99999999999997% (floating-point arithmetic); origin maximum revisit 11 s, mean 2.25 s; destination/corridor revisits 1 s |

`tests/packs/logistics-kpis.json` and `tests/packs/inspection-kpis.json` contain
the actual offline reports. The single-order characterization independently
checks delivery at 10.5s, later acceptance at 11.5s, 174J selected-sample
consumption and 0.475 utilization over a 20s run.

## PX4 scenario and verification

`logistics-px4.yaml` is a 300s, one-carrier PX4/Gazebo scenario using
`px4_gazebo`. Arm and takeoff are authored commands. Release at 56s, pickup
dwell and destination dwell target the AeroBench native-parcel schedule near
60s pickup and 128s delivery. Those are reference expectations, **not observed
results**. Receipt success and real stopped telemetry gate the transfers;
timers cannot manufacture them. The Docker test admits explicit reference
windows (56–75s pickup, 110–150s delivery), later acceptance and offline replay
equality. It will fail if the native run does not satisfy them. The orchestrator
must run it against the real backend; Docker was not invoked in this task.

```bash
"$P5_PYTHON" -m pytest --confcutdir=tests/packs \
  -o cache_dir=tests/packs/.pytest_cache --basetemp=tests/packs/.pytest_tmp \
  tests/packs -m docker -q
```

For an explicit container-backed run, use the existing adapter runner:

```bash
"$P5_PYTHON" -m aeroagentsim.adapters.runner \
  scenarios/packs/logistics-px4.yaml --containers \
  --journal tests/packs/px4-journal.jsonl
```

The full gate completed with **19 passed, 1 Docker test skipped**; two subsequent
focused tests for competing native-command rejection and dwell reset also
passed, for 21 passing tests in total. Tests cover
seeded/scheduled arrivals, deterministic journal equality, replay state/metrics
equality, exact known KPIs, current/future custody cardinality, restriction and
energy admission rejection, storage contention, dwell, independent acceptance
and rejection, native cancellation cleanup, changing real pose samples,
delayed records, continuous area union, horizon failures and CLI corruption
handling. `ruff check` passed and `mypy --strict` passed for all 14 pack, test and
scenario-generator source files:

```bash
HYPOTHESIS_STORAGE_DIRECTORY=tests/packs/.hypothesis \
"$P5_PYTHON" -m pytest --confcutdir=tests/packs \
  -o cache_dir=tests/packs/.pytest_cache --basetemp=tests/packs/.pytest_tmp \
  tests/packs -q
"$P5_PYTHON" -m ruff check --no-cache \
  src/aeroagentsim/packs tests/packs scenarios/packs/generate.py
MYPYPATH=../aerokernel:src "$P5_PYTHON" -m mypy --strict \
  --cache-dir=tests/packs/.mypy_cache \
  src/aeroagentsim/packs tests/packs scenarios/packs/generate.py
```

The isolated GLM profile selected `workbuddy/glm-5.3-flash`, maxTokens 131072,
with no effort setting. Two independent sessions actually launched concurrently
under `tests/packs/dsh-home`: geometry review
`098fc131-702e-4bb5-af82-1b928707a321` and custody/API review
`b295293e-c585-4c06-aace-fcd4ff10d08f`. Geometry output was reviewed for angle,
horizon and coverage cases, with the implemented convention tested explicitly.
The custody/API session produced intermediate reasoning but failed to return
its concise final review and was stopped (exit 130). Its closure/edge-reuse
observations were checked against the SDK and the real cardinality tests;
speculative kernel defect claims were not adopted. No completed independent
custody review is claimed. Logs remain in the two `tests/packs/glm-*` folders.

## Platform changes

Two additive hooks were necessary to make the owned pack files reachable
through the existing platform. `platform/plugins.py` adds two lazy builtin
factory paths (`logistics`, `inspection`); `services/cli.py` adds the `metrics`
subcommand and lazily calls the pack metrics module. No scheduling, schema,
engine, adapter or kernel behavior was changed. There were no other changes
outside the P5-owned paths except these two hooks, and no Git commits, branches
or resets. Initial test invocations inadvertently allowed Hypothesis to write
pack constant-cache entries into the preexisting root `.hypothesis` directory.
It was not cleaned or otherwise edited because that directory is outside P5
ownership; later runs explicitly used `tests/packs/.hypothesis`, and the pack
conftest now sets that location before importing pack modules.

## Known failure (orchestrator run, 2026-10-08)

`pytest tests/packs -m docker` (real PX4/Gazebo via `aeroagentsim/px4-gazebo:dev-p2b`)
fails: `test_logistics_px4_native_parcel_and_replay` never records an `in_transit`
custody state (`KeyError: 'in_transit'`), i.e. pickup did not complete in the real
flight. Kinematic logistics scenarios pass. Under investigation.
