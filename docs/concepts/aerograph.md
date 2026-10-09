# AeroGraph and scenario registries

AeroGraph describes entity types, ancestry, typed fields, directed relations and predicate/event definitions. A registry is the portable subset a particular experiment uses. The runtime reads the registry rather than importing the ontology repository.

## Standalone snapshots

The shipped scenarios reference committed snapshots:

```yaml
registry:
  snapshot: registry.snapshot.json
```

Paths resolve relative to the scenario file. A snapshot contains the selected type descriptors and ancestry, applicable fields, relation descriptors and message schemas. Scenario overlays can add local types and fields for an experiment. The traffic demo includes its [snapshot](../../scenarios/demos/traffic-accident/registry.snapshot.json) and [overlay](../../scenarios/demos/traffic-accident/registry.overlay.yaml).

No AeroGraph checkout is needed for these scenarios. Studio and the console use their snapshot for type search, ancestry, fields and relations, and show that the type catalog is limited to that snapshot. Types absent from it cannot be selected just because they exist elsewhere in the ontology.

## Use a full source checkout

If you have access to AeroGraph, set `AEROAGENTSIM_AEROGRAPH_ROOT` to its directory to retain the broader authoring catalog. Compilation remains read-only. Select the types and fields needed by your scenario; distribute the resulting snapshot with your experiment so readers can run it independently.

The compiler lives in [the AeroGraph integration](../../src/aeroagentsim/integrations/aerograph/). Inspect its available commands with:

```bash
python -m aeroagentsim.integrations.aerograph --help
```

Only declared parent edges establish ancestry. Directory organization and suggested parents do not create inheritance. Applicable fields follow that real ancestry; auxiliary reference and relation endpoint types supply identity descriptors without automatically activating all their fields.

## Schema and execution

Field schemas describe values, units and frames. Relations describe endpoint types and cardinalities. Messages describe typed events, commands and results. These declarations do not supply state, clocks, transforms or a physical model: engines and their scenario bindings do.

A scenario assigns one writer per field and declares concrete lifecycle and relation authority. Predicates evaluate committed state against the registered schemas. A new type alone cannot move an entity or grant an agent access to it.

The compiler supports a bounded portable schema subset. Unsupported active definitions report a source diagnostic rather than being silently generalized. For a local study, add explicit experiment types/fields to its overlay and document their units and meaning.

Continue with [Predicates](predicates.md), [Field ownership](plugins.md) or the [scenario reference](../reference/scenario.md).
