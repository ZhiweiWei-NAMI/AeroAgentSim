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
`px4_gazebo`. The `launch` workflow submits arm at 1s, submits takeoff only
after that child's succeeded receipt, and records readiness only after the
takeoff child's succeeded receipt. Failures/rejections enter its failed phase.
After the separate business workflow accepts delivery, launch submits land
and awaits actual `ON_GROUND`/disarmed native completion. No flight state is
written by the workflow: Gazebo/PX4 remains the sole telemetry writer.

Release is at 56s and pickup dwell is 1s. The destination dwell is explicitly
55s (previously 45s): measured stopped arrival near 72s plus 55s service and
one 0.5s delivery polling interval aligns delivery with the reference near
tick 128. This changes the authored facility service duration, not native
flight accuracy or custody evidence. Both facility points are **aerial handoff
positions** at ENU z=5m, not ground pads; landing follows acceptance. PX4 goto
uses the backend's reset-measured vertical datum and world ENU coordinates.

The position radius remains **1.0m in 3D**, with speed **at most 0.5m/s**.
A native goto receipt alone permits up to 2m/1.5m/s and cannot authorize a
transfer. Logistics additionally requires its tighter measured position and
speed gates throughout the facility dwell; leaving either gate resets dwell.
The communication boundary remains **200ms**, with an explicit
`control_step_ns: 20000000`: the PX4 adapter reaches each granted boundary
through ten **20ms native barriers**, five native 4ms physics steps each.
The energy admission model uses the 200ms publication/command-latching step.
Intermediate contacts and command updates are accumulated with their original
native timestamps; final telemetry and receipt/event availability use the
granted communication boundary. Delayed contacts retain their acquisition
time. A missed native frontier or a future/invalid native event time faults
the run before any partial observations are published.
The generic lockstep adapter gains only an overridable native advance hook;
SUMO/ns-3 behavior and PX4 configurations without this option retain their
existing advancement behavior. Exact
Gazebo timestamps alone did not ensure stable PX4 control in faster 200ms
batch runs: two full native reruns picked up but never accumulated a complete
destination dwell, with metre-scale vertical excursions (one touched ground
while its cached landed telemetry still read `IN_AIR`). Finer barriers keep
the external controller and physics more closely coupled; tolerances were
not widened to accept those failed trajectories.
The Docker regression checks every actual 0.5s polling sample across both
dwell windows, armed/`IN_AIR` telemetry, five succeeded commands in sequence,
three explicit custodians, later acceptance, completed landing, and offline
receipt/telemetry/custody/KPI equality. Reference windows remain 56–75s pickup
and 110–150s delivery to permit native numerical variation.

```bash
export MYPYPATH=../aerokernel
export PYTHONPATH=src:../aerokernel:.venv/lib/python3.11/site-packages
for attempt in 1 2; do
  "$P5_PYTHON" -m pytest --confcutdir=tests/packs \
    -o cache_dir=/tmp/aas-p5f/pytest-cache \
    --basetemp=/tmp/aas-p5f/verify-$attempt tests/packs -m docker -q
done
```

The test scopes the existing lifecycle helper to `aeroagentsim.job=p5f`;
containers use the allowlisted `aeroagentsim/px4-gazebo:dev-p2b` image, eight
CPUs, dynamic loopback ports and ownership-checked removal. The helper's
production defaults are unchanged. Journals and `measurements.json` are
preserved under each attempt's test directory.

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

## Original P5 platform integration

In the original P5 delivery, two additive hooks were necessary to make the
owned pack files reachable
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

## P5-F resolution (real PX4/Gazebo, 2026-10-08)

The original failure was reproduced against the real image in a full 300s
run. Arm succeeded at 2.2s, but the fixed takeoff schedule waited until 20s.
Telemetry showed PX4 auto-disarmed on the ground at 13.2s. The later takeoff
never reached its completion predicate; pickup goto was rejected at 56.6s
with `vehicle already has active command`, the order failed at 57s, and
takeoff finally failed at 200.2s with its native 180s completion timeout.
The resulting missing `in_transit` state caused the reported KeyError.

The fix combines the receipt-driven launch workflow with the PX4 adapter's
fine internal control barriers. Kernel code did not need changes.
`scenarios/packs/generate.py` produces the same repaired
document; the non-Docker regression compares its PX4 output against the YAML
without invoking the AeroGraph compiler or writing any generated files.
The first characterization with the original 45s
destination dwell measured pickup at 59.5s, handoff at 117s, delivery at
117.5s and acceptance at 118.5s. The final 55s dwell explicitly aligns this
service schedule with the requested reference while keeping all physical
transfer gates.

Measured final-scenario timelines (seconds, equivalent to the reference's
1s ticks); `handoff` is the actual custody transfer, and `delivered` follows
one polling interval later:

| Native run | Pickup / carrier custody | Destination custody | Delivered | Business accepted | Pickup error | Destination custody error | Delivery error |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| control1 | 59.5 | 126.5 | 127.0 | 128.0 | 0.121907m | 0.168407m | 0.174746m |
| control2 | 59.5 | 126.5 | 127.0 | 128.0 | 0.172475m | 0.121831m | 0.118322m |

Errors are distances from actual Gazebo ENU poses to the authored 3D facility
positions, using the actual last published pose at each polling instant
(for example, custody at 126.5s reads the native 126.4s pose). Across the 55s
destination dwell's 0.5s polling samples, maximum errors were
**0.565705m / 0.497896m**, and maximum observed MAVSDK speeds were
**0.443716m/s / 0.402087m/s**, respectively. These measured margins support
retaining the existing 1m and 0.5m/s gates. Native trajectories differ even
when these custody times match; offline replay reproduces each individual
recorded run, not a claim of bitwise repeatable PX4 flights.
Land completed natively at **140.02s / 141.02s**, with succeeded receipt
availability at **140.2s / 141.2s**, after independent acceptance at 128s.

Two earlier 20ms-publication characterizations also completed both real
300s flights and live assertions, but produced about 75,662 records per run
and made replay impractically expensive. Their replay processes were stopped
after the final internally substepped runs had completed their live gates;
they are not counted as completed Docker test passes. Internal substeps keep
the native control granularity without forcing every physical substep into
the kernel journal. Their journals remain under `step20`/`step20-repeat`.

Final verification: **two real Docker test passes**, each completing 300s
and an independent engine-free replay with identical telemetry, command
receipts, historical custody edges and KPIs. End-to-end pytest wall times
were **479.80s / 467.71s** with both runs launched concurrently. Non-Docker
packs: **21 passed** (49.66s). Non-Docker adapters: **136 passed** (6.93s),
including four new substep tests for native event/receipt timestamps, atomic
publication (including delayed contact acquisition), and immediate failure on
incorrect frontiers or future/invalid event times.
`ruff check --no-cache` and strict mypy passed for all 17 targeted source
files. All job containers were removed through ownership-checked cleanup.

The final artifacts are `/tmp/aas-p5f/control1.txt`,
`/tmp/aas-p5f/control2.txt`, and each
`/tmp/aas-p5f/control{1,2}/test_logistics_px4_native_parc0/` directory's
`journal.jsonl` and `measurements.json`. Non-Docker logs are
`/tmp/aas-p5f/final-packs.txt` and `/tmp/aas-p5f/final-adapters.txt`; the
subsequent delayed-contact regression suite is recorded in
`/tmp/aas-p5f/final-adapters-delayed.txt`.

Two independent GLM audit sessions were actually launched concurrently with
`workbuddy/glm-5.3-flash`, maxTokens 131072 and no effort setting:
`b5024001-9de4-4319-ae46-1700cf32b3a2` (receipt/presence review) and
`3e5b713d-2543-423c-b1e3-38d343579417` (regression cases). Their intermediate
output was inspected; both exceeded the useful review time and were stopped
without final reports. Their suggestion to align logistics with the looser
native goto tolerance was not adopted. Native journals established the actual
command timing failure and the additional coarse-barrier instability. Logs
are under `/tmp/aas-p5f/glm-contract`, `/tmp/aas-p5f/glm-tests` and
`/tmp/aas-p5f/dsh-home`; no completed GLM audit is claimed.
