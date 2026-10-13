# Low-altitude algorithms

A scenario selects `allocation`, `path_planning`, `conflict_resolution`, and
`charging` in its top-level `algorithms` mapping. The runtime gives each algorithm
an immutable snapshot of committed facts. It validates proposals and submits
commands to the physical owner; algorithms cannot publish state.

| Slot / protocol | Method | Proposal |
| --- | --- | --- |
| `allocation` / `Allocation` | `allocate(snapshot)` | tuple of `Assignment(task, vehicle)` |
| `path_planning` / `PathPlanning` | `plan(snapshot, start, goal)` | tuple of ENU points, excluding start and including goal |
| `conflict_resolution` / `ConflictResolution` | `resolve(snapshot, conflicts)` | tuple of `Resolution(vehicle, action, altitude_m, hold_s)` |
| `charging` / `Charging` | `charge(snapshot)` | tuple of `Charge(vehicle, pad)` |

Types live in `aeroagentsim.algorithms.protocols`. Positions and bounds are ENU
metres, wind is ENU metres per second, and `at_ns` is canonical simulated time.
Vehicles include SOC, payload, payload capacity and availability; tasks include
kind, priority, endpoints and payload. Zones carry kind, bounds and active state.
Missing committed inputs remain errors naming the source field.

Builtins are `nearest`, `auction`, `hungarian` (allocation, see below),
`straight`, `corridor_astar`, `altitude_layers`, `hold_and_go`, and
`threshold_return`. `corridor_astar` uses A* on a visibility graph of expanded
obstacle corners and corridor midpoints, checking complete 3D segments against
active no-fly boxes. `straight` proposes a direct route; the task engine still
checks no-fly clearance before executing each segment. Restricted zones and
altitude bands are monitored membership volumes, not automatic flight bans.

## Comparing allocators

The task engine does not allocate each order the instant it appears. With
`batch_window_s` (task engine config, default `0` = immediately; the parcel and
medical templates use `10`), orders accumulate and the allocator is called once
per window with every pending task and every free drone in the snapshot, so a
global method can see trade-offs a one-at-a-time rule cannot. `vehicle_model`
(`speed_m_s`, `energy_per_m_j`) tells the engine what cruise speed and energy
rate to hand to allocators. A burst arrival mode (`arrivals.burst_size`, orders
released together at the same average `rate_per_s`) creates the simultaneous
demand where the choice matters.

All three built-ins share one cost model (`algorithms/builtins.py`):

1. Estimated completion time = `busy_s` (remaining time on the current leg) +
   (vehicle to pickup + pickup to drop-off distance) / `speed_m_s`.
2. A pair is feasible only if the state of charge after the trip *and the flight
   back to the nearest pad* stays at or above `reserve_soc` (default 0.2);
   infeasible pairs are never proposed. Unknown battery capacity skips this check.
3. Higher priority always wins over lower cost.

- `nearest` is the greedy baseline: highest priority first, each order takes the
  closest feasible drone by pickup distance.
- `auction`: every drone bids its completion time, inflated by up to
  `1 + energy_weight` (default 1) as the trip leaves it with less charge; each
  round awards the lowest bid among the top-priority tasks.
- `hungarian` minimises the total completion time of the whole batch.

Drones are eligible from where they are (they stay where their last delivery
ended, not only at a pad), but a drone still flying a task is not offered: the
engine has no per-drone task queue, so `busy_s` is `0` for built-in dispatch and
is available to custom allocators and future queued dispatch.

The 2.1 worked comparison uses unchanged parcel demand, 300 simulated seconds,
`hold_and_go`, three matched seeds (0–2) and both fleet sizes. All 18 runs
completed without a runtime failure; that does **not** mean every order finished.
Each cell has three observations. Values are mean ± sample standard deviation
of per-run KPIs, not confidence intervals:

```sh
aeroagentsim experiment parcel-delivery \
  --vary allocator=nearest,auction,hungarian --vary fleet_size=4,8 \
  --seeds 3 --jobs 3 --horizon 300
```

| Allocator | Aircraft | Completed tasks | Completion (%) | Mean latency (s) | Energy (kJ) | Separation alerts |
| --- | --- | --- | --- | --- | --- | --- |
| nearest | 4 | 13.3 ± 4.6 | 74.6 ± 22.5 | 86.3 ± 28.4 | 211.2 ± 69.0 | 17.7 ± 4.5 |
| nearest | 8 | 9.7 ± 5.0 | 52.4 ± 17.5 | 77.8 ± 30.9 | 229.5 ± 72.3 | 34.3 ± 22.8 |
| auction | 4 | 14.0 ± 5.2 | 77.2 ± 20.4 | 79.6 ± 24.5 | 203.3 ± 62.2 | 22.7 ± 10.4 |
| auction | 8 | 10.0 ± 5.0 | 53.6 ± 15.5 | 71.1 ± 22.2 | 224.1 ± 66.7 | 35.7 ± 23.1 |
| hungarian | 4 | 14.0 ± 5.2 | 77.2 ± 20.4 | 80.1 ± 24.9 | 203.7 ± 62.5 | 20.7 ± 8.5 |
| hungarian | 8 | 10.0 ± 4.6 | 53.4 ± 12.4 | 71.4 ± 18.5 | 225.2 ± 68.2 | 38.0 ± 26.9 |

Created orders average 20 ± 10.6 in every cell; arrivals are matched within
seeds. There are no invented delivery deadlines (`on_time_rate` is unmeasured).
Greedy pickup distance, energy-weighted whole-trip bids and batch-wide trip-time
matching make different proposals, but these three seeds do not establish a
universal winner. A larger fleet finishes **fewer** tasks in this grid while
producing more separation alerts: shared-site congestion and interruptions are
important, not just allocation cost. Lower completion-only latency can also
reflect which orders finish; it is not proof of better service.

The fixed default seed 42 is a separate demo, completing 18/20 parcel orders
with eight aircraft. Do not generalize that example to arbitrary seeds or
assume adding aircraft monotonically improves throughput. Run more seeds and
inspect unfinished orders before drawing conclusions. See the
[experiment guide](experiments.md) for raw CSV/Markdown outputs and viewer links.

## Writing your own allocator

Allocators receive a read-only `Snapshot` (`vehicles`, `tasks`, `pads`, ...) and
return `Assignment(task, vehicle)` proposals; the engine re-checks every
proposal and rejects busy, ineligible or low-battery pairs. `Vehicle` carries
`position`, `soc`, `payload_capacity_kg`, `available`, `task_kinds`, and the
cost-model inputs `speed_m_s`, `energy_per_m_j`, `capacity_j`, `busy_s` (all
defaulted, so older code and hand-made snapshots keep working). Reuse
`completion_s`, `soc_after` and `feasible` from `aeroagentsim.algorithms.builtins`.
Select a class anywhere on the Python path with `allocation: my_module:MyClass`
(or set the Task allocation knob to it); keyword options go under
`engine.config.algorithm_config.allocation`. The class must be pure and
deterministic: no clocks, no global state, a seeded `random.Random` if it needs
randomness.

Factories receive keyword configuration from `engine.config.algorithm_config`
per slot. Available options: `corridor_astar` accepts `grid_m` (district scale)
and `clearance_m`; `altitude_layers` accepts `layer_spacing_m`; `hold_and_go`
accepts `hold_s`; `threshold_return` accepts `threshold`. A custom factory must
implement the slot method and return typed proposals. Set
`allocation: my_allocator:Allocator`, or register an entry point:

```toml
[project.entry-points."aeroagentsim.algorithms"]
"allocation.my_allocator" = "my_allocator:Allocator"
```

Installed entry points take precedence over builtins; duplicate names are
rejected. The loader also accepts unqualified installed names. Keep algorithms
stateless and deterministic; journal nondeterministic choices through an engine
rather than calling an unrecorded service from a synchronous slot.

The following allocator is 30 lines; save it as `my_allocator.py`:

```python
from aeroagentsim.algorithms.protocols import Assignment, Snapshot


class Allocator:
    """Assign one eligible drone to each task in priority order."""

    def __init__(self, minimum_soc: float = 0.25) -> None:
        if not 0 <= minimum_soc <= 1:
            raise ValueError("minimum_soc must be between zero and one")
        self.minimum_soc = minimum_soc

    def allocate(self, snapshot: Snapshot) -> tuple[Assignment, ...]:
        free = {v.id: v for v in snapshot.vehicles if v.available}
        result = []
        tasks = sorted(snapshot.tasks, key=lambda t: (-t.priority, t.id))
        for task in tasks:
            eligible = [
                v for v in free.values()
                if v.soc >= self.minimum_soc
                and v.payload_capacity_kg >= task.payload_kg
            ]
            if not eligible:
                continue
            def cost(vehicle):
                return sum((a - b) ** 2 for a, b in
                           zip(vehicle.position, task.origin)), vehicle.id
            vehicle = min(eligible, key=cost)
            result.append(Assignment(task.id, vehicle.id))
            del free[vehicle.id]
        return tuple(result)
```

`allocation: llm` installs a `langgraph` engine using the existing
`LangGraphDecision` and `JournalClient`. Requests and results are typed events;
provider calls, replies, validated proposals and failures use its existing WAL
records. The default provider is explicitly scripted (1000 named replies), with
no network calls. It chooses one task and feasible vehicle per invocation by
rank. Exhausted or invalid replies produce a real decision failure; they never
fall back to another allocator. Install `.[agents]` to use this slot. For a
live named provider profile, set the task engine's allocation configuration:

```yaml
algorithm_config:
  allocation:
    provider: {mode: live, profile: my-provider-profile}
```

Try the complete backend template with the existing scenario runner:

```bash
PYTHONPATH=aerokernel:src python -m aeroagentsim.services.cli run \
  scenarios/templates/parcel-delivery/scenario.yaml --out runs
```

Its five knobs have JSON pointers in `template.yaml`. The fleet generator
expands explicit prototypes; task generators release the declared finite task
pool using scheduled or seeded Poisson arrival times and sample distinct site
endpoints. All eight task kinds share the transport lifecycle. The everyday templates add
measured inspection observations, field treatment, patrol visits, medical
preemption/resumption and passenger pad reservations through the task owner.
Energy uses observed displacement, speed, payload and directional headwind;
charging starts only at a selected pad with observed zero speed. Battery energy
has one writer. Airspace detects separation with a spatial hash and executes
resolution proposals through task dispatch and the kinematic command owner.
Dynamic zones support half-open schedules and `lowalt.airspace.activate`
commands; `activation_events` can bind registered event schemas and topics to a
zone's activation. Wind limits are per vehicle class, with hold/release events.

Business event metadata is declared in
`aeroagentsim.packs.lowalt.EVENT_NARRATIVES` (`text`, `category`, `severity`, with
`category_by_kind` for tasks). The API/KPI/feed work is supplied separately.

The `coverage`, `survey` and `patrol` path built-ins accept explicit field bounds
or waypoint lists. Select them under the task engine's `work_routes` per kind;
travel to pickup and emergency return to a pad use corridor routing. Work
waypoints survive weather holds and swaps; a return trip first rejoins the
interrupted route without emitting a treatment observation.

`Vehicle.task_kinds` restricts eligibility in all allocation built-ins, including
the LangGraph adapter. An empty tuple is the explicit unrestricted protocol
default. `Snapshot.monitored_positions` supplies positions from aircraft whose
motion belongs to another pack; resolvers leave those protected aircraft under
their own command owner and propose separation for the controlled fleet.

An airspace config may declare `event_only_zones` and `dynamic_zone_centers`
(entity, field, dimensions_m (width, depth, height above the incident)). The airspace owner then owns that zone's bounds
as well as activation; use an instance binding override for these two fields.
`city-day` derives its incident zone from the road incident's committed position.
