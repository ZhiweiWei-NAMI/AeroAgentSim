# Migrating the v1 SimPy runtime

New code imports `from aeroagentsim import Simulation`. `Simulation` hosts the
independent `aerokernel`; it is not a subclass of SimPy. This release retains
the historical code, rather than pretending arbitrary SimPy generators can be
translated into kernel engines. The old `Environment`, `AirFogSimEnv`,
`AeroAgentSimEnv`, and `airfogsim` namespace require `aeroagentsim[legacy]` and
are deprecated. Root legacy imports emit `DeprecationWarning` pointing here.
Enable notices with `python -W default::DeprecationWarning`.

```bash
pip install -e ../aerokernel -e '.[legacy,dev]'
pytest tests/test_core tests/test_workflow tests/test_visualization tests/test_examples -m legacy
```

Without the extra, `Environment` raises an actionable `ImportError`. Python
package metadata does not record which extras pip selected: the compatibility
check verifies that every legacy dependency distribution is installed, without
loading them. Installing that same complete dependency set independently is
equivalent. A default/server installation stays free of SimPy and legacy imports.
The old suites receive the `legacy` marker and are excluded from collection
when those dependencies are absent. Installing the extra does not activate the
old runtime for new scenarios. Historical failures remain failures.

## Concept and API mapping

| v1 | Kernel platform | Migration responsibility |
| --- | --- | --- |
| `Environment`, float seconds, implicit managers | `Simulation(load_scenario(...))`; `RunSession` for artifacts; integer nanoseconds | Explicitly author clock, seed, bindings and plugins. `run_until(ns)` replaces `run(until=seconds)`. |
| `Agent` / subclass, mutable `agent.state` | Entity identity/type and committed typed facts; optional decision partition | Declare ontology descriptors and initial facts. Submit commands to the declared field writer; never mutate a view. |
| `Component`, attachment, metric callbacks | Capability bindings and engine partition declarations | Bind fields/relations/messages to one writer; applicability is separate from implementation. |
| `Task`, local progress, `execute_task` | Typed command plus acceptance/execution/result/cancellation receipts | Completion must come from the accepting engine. Progress or a transport ACK cannot imply success. |
| `Workflow` / `WorkflowStatusMachine` | `engines/workflow.py` configured state machine or pack engine | Declare state, timer/event/receipt transitions, cleanup and independent business acceptance. |
| `Trigger`, wildcard string event bus | Typed message schemas, partition-owned timers and bound predicate contexts | Use explicit dependencies. The threshold plugin implements a limited comparison AST, not arbitrary predicate languages or cron. |
| State/template classes / custom Python proxies | AeroGraph compiler and authored registry extensions | Pin source/digest and declare executable writer/lifecycle authority separately from conceptual ownership. |
| Visualization `visual_update` integration | Kernel grants; viewer consumes committed feed | Viewer refresh cannot change physical state or simulation time. |
| Legacy run files / trajectories | Kernel WAL + pinned scenario/registry + feed index | Old run files are historical artifacts, not compatible kernel journals. There is no implicit log conversion. |

The public API is `Simulation.start()`, `run_until(ns)`, `close()` and
`simulation.kernel` for typed submission, cancellation and reads. `RunSession`
adds artifact ownership and `run()`. Native adapter cancellation is currently
unsupported; the kinematic command contract declares its supported operations.
See [capability checklist](CAPABILITIES.md) for gaps; retained v1 classes are
not proof that their new equivalent exists.

## Runnable before/after examples

Run these from the repository root after the installation above. The new examples
use the authored P1 slice, which currently requires the matching read-only
AeroGraph checkout at `/mnt/data2/weizhiwei/AeroGraph`, including its pinned
predicate source hash. See [installation](../../INSTALL.md) for portable source
or registry-snapshot setup. Every code block below is independently executable.
Seconds-to-nanoseconds conversion here uses exact integer literals.

### 1. Advance the simulation

Before:

```python
from aeroagentsim import Environment

env = Environment(visual_interval=0)
env.run(until=1)
assert env.now == 1
```

After:

```python
from aeroagentsim import Simulation
from aeroagentsim.scenario import load_scenario

sim = Simulation(load_scenario("scenarios/p1-slice.yaml"))
try:
    sim.start()
    view = sim.run_until(1_000_000_000)
    assert view.instant.ns == 1_000_000_000
finally:
    sim.close()
```

### 2. Read entity state

Before:

```python
from aeroagentsim import Environment
from aeroagentsim.agent.drone import DroneAgent

env = Environment(visual_interval=0)
agent = env.create_agent(DroneAgent, "sample", properties={
    "position": (0, 0, 0), "battery_level": 100,
})
assert agent.get_state("position") == (0, 0, 0)
```

After (the scenario creates identities and supplies their authored initial facts):

```python
from aeroagentsim import Simulation
from aeroagentsim.scenario import load_scenario

scenario = load_scenario("scenarios/p1-slice.yaml")
ref = next(ref for ref in scenario.manifest.entities if ref.id == "uav-1")
sim = Simulation(scenario)
try:
    view = sim.start()
    fact = view.field((ref, "he.aircraft.position_enu_m"), view.instant)
    from aerokernel.state import Fact
    assert isinstance(fact, Fact)
    print(fact.value)
finally:
    sim.close()
```

Pose, energy and business state can have different writers on the same entity.
The new read returns a fact carrying timestamps and validity, not a writable
state dictionary. A missing fact remains missing.

### 3. Replace callback timers with workflow transitions

Before:

```python
from aeroagentsim import Environment
from aeroagentsim.core.trigger import TimeTrigger

env = Environment(visual_interval=0)
fired = []
trigger = TimeTrigger(env, interval=1, name="once")
trigger.set_max_triggers(1)
trigger.add_callback(lambda context: fired.append(context["time"]))
trigger.activate()
env.run(until=2)
assert fired == [1]
```

After (P1 explicitly schedules its first dispatch at 10 ms):

```python
from aeroagentsim import Simulation
from aeroagentsim.scenario import load_scenario

scenario = load_scenario("scenarios/p1-slice.yaml")
order = next(ref for ref in scenario.manifest.entities if ref.id == "order-1")
sim = Simulation(scenario)
try:
    sim.start()
    view = sim.run_until(20_000_000)
    assert view.field((order, "aas.p1.order_state"), view.instant).value == "executing"
finally:
    sim.close()
```

This is a model rewrite: an explicit workflow transition emits a typed motion
command. It does not execute a legacy callback. The physical action later reports
arrival; a separate business workflow acknowledges it.

## Runtime cutover

Use `aeroagentsim run <scenario.yaml> --out <run-root>` and
`aeroagentsim replay <printed-run-directory>`. Replay reads the recorded WAL
without simulator/model calls. `aeroagentsim serve` exposes `/v1/runs` and
committed feeds; it does not serve the old `/api` workbench contract. Build the
optional frontend separately and pass `--frontend frontend/dist`. The deprecated
`main_for_visualization.py` now forwards to this server and never auto-installs
Python/Node dependencies. Its old `--reload`/frontend-process options are retired.

Legacy `agent/`, `component/`, `task/`, `workflow/`, `manager/`, `dataprovider/`,
`statistics/`, `visualization/`, `resource/`, `event/`, `helper/`, `core/` and the
old `cli/` are retained for historical execution only. Their weather, sensing,
energy and cron approximations are not validated by the kernel cutover. Custom
SimPy generators, mutable state, custom subclasses and old database records
need explicit migration; there is no general conversion command in this release.
Legacy root aliases are planned for removal in the next major release, after
pending checklist items and conversion support have been decided.

## Verification in P8

See [CAPABILITIES.md](CAPABILITIES.md#p8-verification) for clean-install output,
executed snippet results and the full legacy failure record. Source availability,
component-job measurements and this round's fresh checks are distinguished there.
