# Scenario Format (`aeroagentsim.scenario/v1`)

A scenario file uses the `aeroagentsim.scenario/v1` format and declares initial field facts that are validated against the kernel registry. Facts are addressed by authored entity id and field id. Values for the `number` schema are strict Python floats: in YAML, `-5.0` is valid, while `-5` (integer), `true` (boolean), and `"-5.0"` (string) are invalid. The same strictness applies to every element of a number vector. Integer-valued fields use the `integer` schema. There is no implicit coercion and no defaulting.

Registry failures are wrapped in a `ScenarioError` whose message identifies the authored path `entities.<authored id>.facts.<field id>`. The `error.code` attribute preserves the original `VALUE_SCHEMA`, `VALUE_ENUM`, or `VALUE_BOUND` code, and the original `KernelError` is available via `__cause__`.

Engine factory failures use the path `engines.<id>.config`.

A threshold sampled context must have exactly one `bindings.samples` entry assigned to its engine. When the entry is missing, a `ScenarioError` is raised carrying the engine and context; the failure is never surfaced as `StopIteration`.

See [plugin discovery and configuration](plugins.md) for Studio forms, and [viewer message subjects](frontend.md) for explicit entity bindings in payloads.
