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

Builtins are `nearest` (priority-first nearest pickup), `auction` (lowest
energy-adjusted fleet bid), `hungarian` (global feasible pickup-distance
assignment), `straight`, `corridor_astar`, `altitude_layers`, `hold_and_go`, and
`threshold_return`. `corridor_astar` uses A* on a visibility graph of expanded
obstacle corners and corridor midpoints, checking complete 3D segments against
active no-fly boxes. `straight` proposes a direct route; the task engine still
checks no-fly clearance before executing each segment. Restricted zones and
altitude bands are monitored membership volumes, not automatic flight bans.

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
fall back to another allocator. Install `.[langgraph]` to use this slot. For a
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
endpoints. All eight task kinds share the transport lifecycle; specialised
inspection/spraying/passenger actions can be added by scenario behaviours.
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
