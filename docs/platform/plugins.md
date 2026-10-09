# Engine discovery and configuration

Distributions register callable factories in the `aeroagentsim.engines` entry
point group. Studio enumerates all installed entries and all builtins, including
`records` and `threshold`. An installed entry takes precedence over a builtin
with the same name. Duplicate installed names are rejected. Factory imports and
descriptor failures appear as unavailable entries with their actual error.
Catalog discovery never constructs an engine or starts an external simulator.

A factory may expose a portable JSON-schema-like object as `config_schema`:

```python
def build(context):
    return ExampleEngine(context)

build.config_schema = {
    "type": "object",
    "properties": {
        "source_field": {"type": "string", "title": "Source field"},
        "interval_ns": {"type": "integer", "minimum": 1},
        "mode": {"type": "string", "enum": ["sample", "continuous"]},
    },
    "required": ["source_field", "interval_ns"],
}
```

This optional attribute is data, not a schema callback. It must be a finite,
portable mapping with root `type: object`. The catalog returns it unchanged as
`engines[].config_schema`. It supplies authoring controls; engine factories
remain responsible for validating real configuration, bindings and capabilities.
Descriptors never create default research inputs or normalize registry values.

Studio uses forms for its supported schema vocabulary and retains a raw JSON
configuration editor when a descriptor is absent or requires unsupported
features. The full scenario YAML editor remains available. Required properties
are authored explicitly; numeric and boolean omissions do not become zero or
false. Third-party engines need no platform changes to become discoverable.

See [scenario validation](scenario.md) for strict registry number semantics and
[the plugin interface](p1.md#scenario-and-plugin-interface) for `EngineBuild`.
