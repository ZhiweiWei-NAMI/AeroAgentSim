# P08 typed graph contract v1

This package formalizes the accepted stage-one catalog for a private authoring/review graph. It does not implement a native Atlas evaluator, a simulator, real authority, legal compliance, or command dispatch.

## Exchange envelope (stable for pipeline and UI)

Graph: `schema_version: p08.typed-graph/v1`, `nodes`, `edges`, optional `activities`, `scenarios`, `diagnostics`, `readiness`, `source_manifest` and `counts`.

Node: `id`, `kind`, `semantic_level`, `label`, `provenance`, `source_records`, `payload`. Preserve `raw_kind`, `source_collection`, and exact original JSON under `payload`. `identity` and `semantics` can project inspected values; no recovered metadata may be invented. `source_records` contains `artifact`, `pointer`, `source_id`, optional `case_id`, and source artifact `sha256`.

Edge: `id`, `relation`, `source`, `target`, `role`, `semantic_level`, `provenance`, `source_records`, `payload`. Endpoints are canonical node ID strings. Preserve `raw_relation`, `contract_variant`, source endpoint references, ordered role metadata and all conditions in raw payload. An absent source role is recorded as `unspecified` with a diagnostic; that does not authorize a role assumption.

Semantic levels are `definition`, `configured_instance`, `runtime_record`, `source_record`. Edge level is `definition`, `configuration`, `runtime`, `cross_level`, or `source_record`. It reflects endpoint semantics; source labels such as authored_instance or instance are retained verbatim in payload.

## Invariants

- IDs are opaque. Graph storage identity is not lifecycle identity. Never parse labels or ID prefixes into entity types, time, source, legal scope, or permission.
- Exact runtime identity is `{run_id, epoch, id, generation, ref_type}` with no type coercion. Missing members stay missing. Integer generation 1 differs from string "1".
- Definitions, configured instances, attempts/executions and results stay distinct. A record describing an unexecuted attempt can belong to the runtime-record semantic family without claiming execution.
- Stable source occurrence pointers and unchanged payloads are the lossless export authority. A deduplicated definition may have several source occurrences only after identity, explicit revision, semantics and scope agree. Facts, attempts, receipts, effects and parallel edges are not merged by matching labels or endpoints.
- Directed multigraph: edge IDs, not endpoint pairs, identify edges. Keep separate roles, temporal contexts, conditions, versions, source evidence and multiple relationships.
- Role cardinality applies within a declared relation definition and exact binding/attempt/validity scope. There is no one-agent-per-entity, one-receipt-per-command, one-resource-per-behavior or universal one-outgoing-edge constraint.
- State specifications define fields; facts carry values. Desired target, accepted command target and actual pose are three fields. Computing authority, command authority, physical contact, material containment and legal custody are different relations.
- Validity time, occurrence time, availability time and evidence cutoff are independent and retain their clock domains and precision. Nanoseconds remain decimal strings. Do not silently convert seconds, coerce epochs, bridge clocks, interpolate, carry values indefinitely or choose latest arrival as authority.
- Main truth is only True/False/Unknown. Applicability and diagnostics remain separate. A fixture expectation is not an executed result. A true predicate is not an emitted occurrence or permission.
- Preserve complete native source AST recursively, including non-dict literals, object properties, argument order, target references and separate applicability root. `window_s`, `duration_seconds`, `max_gap_s`, `scope`, `enter_s`, `clear_s` stay exactly present or absent. Unknown operators are preserved with readiness diagnostics, never silently evaluated.
- Capability is not authority; capacity is not reservation, occupancy or availability; command acceptance is not physical success; event feedback does not dispatch a command.
- Missing real regulatory sources/clauses, qualifications, grants, calibration, backend/evaluator versions and evidence remain explicit gaps. No new legal fact or operational policy is supplied here.
