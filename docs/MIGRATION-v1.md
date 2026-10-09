# Migrating from v1

The v1 SimPy runtime, its environment aliases, namespace shim, console script,
and `legacy` extra have been removed. New code imports `Simulation` or
`RunSession` from `aeroagentsim`. SimPy generator processes need to be rewritten
as kernel engines or behaviour chains; their scheduling semantics do not
translate automatically.

| v1 concept | Shared-runtime replacement |
|---|---|
| Environment | `Simulation`; `RunSession` also manages run files and replay |
| Agent | Typed entity state plus a decision engine, optionally LLM/LangGraph |
| Component | An engine capability bound to typed commands and owned fields |
| Task | A typed command with lifecycle receipts |
| Workflow | A behaviour chain with states, transitions and child commands |
| Trigger | Typed events and predicate evaluations |
| Data provider | An engine publishing observations through declared clocks |

Define entities, relations, fields, messages and bindings in a scenario. Bind
each state field to one writer. Engines advance through the shared runtime;
external observations enter through configured ingress streams. See
[runtime](platform/RUNTIME.md), [behaviours](platform/behaviours.md),
[adapters](platform/adapters.md), and [agents](platform/langgraph.md).

From the repository root, this example uses a checked-in registry snapshot:

```python
from pathlib import Path
from aeroagentsim import Simulation
from aeroagentsim.scenario import load_scenario

simulation = Simulation(load_scenario(Path("scenarios/packs/logistics-small.yaml")))
try:
    simulation.start()
    view = simulation.run_until(1_000_000_000)
    print(view.instant.ns)
finally:
    simulation.close()
```

For a saved run, use the current CLI:

```bash
aeroagentsim run scenarios/packs/logistics-small.yaml --out runs
aeroagentsim replay runs/<directory-from-run-output>
aeroagentsim serve --help
```

Replay reads the committed journal without invoking domain engines or model
providers. Scenario and behaviour validation report authoring errors before
execution. The kernel workflow/threshold compatibility engines remain for
existing kernel scenarios; they do not restore v1 SimPy APIs.
