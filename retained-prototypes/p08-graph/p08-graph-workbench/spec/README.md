# P08 semantic contract

Status: implemented private authoring/import contract, version 1.0.0. This package formalizes the accepted P02 stage-one scope; it does not establish native Atlas execution or real physical, legal, custody, medical or backend outcomes.

## Reading order

1. `CONTRACT_DRAFT.md`: stable graph exchange envelope and invariants. The filename records its early handoff role; the schema and registries below are the implemented v1 contract.
2. `p08-graph.schema.json`: JSON Schema Draft 2020-12 for preservation-valid graph exchange.
3. `node-type-registry.json`: canonical families, levels, source profiles and semantic obligations.
4. `relation-registry.json`: directed, typed, role-preserving endpoint variants.
5. `migration-mappings.json`: exhaustive 35 frozen collection and 42 runtime-kind mappings, all 92 frozen/69 runtime relation-name variants, plus source-preservation extensions.
6. `ambiguity-ledger.json`: concrete differences, resolutions and still-unverified source/evidence questions.
7. `canonical-identity-policy.json`: lifecycle, revision, occurrence and deduplication rules.
8. `ast-preservation.schema.json`: source-AST envelope, explicitly not a claimed native grammar.
9. `SEMANTIC_DECISIONS.md`: readable decisions and complete mapping tables.
10. `semantic-facets.schema.json`: strict optional projections for 25 central concept families, with known/missing/unverified slots. These are authoring facets, not a second source catalog.
11. `source-manifest.json`: pinned input hashes and the review scope.

`contract.py` is the shared pure mapping API used by the importer. `build_contract.py` reproducibly emits schemas and registries from the pinned accepted inventory and proposal ledger. No source catalog is rewritten by this package. `validate_contract.py` checks shape, levels, identity, endpoint/role variants and multigraph invariants without simulating or evaluating rules.

## Scope and counts

Source scope is 35 original activities, 226 ordered original steps and 20 machine-information examples, with cross-domain and preserved emergency examples retained separately. The runtime ledger has 42 raw kind names and 69 relation names, covering 4,446 record and 12,157 relation occurrences. Mapping 42 raw kinds to 39 canonical families removes only three compatible naming aliases. It does not merge any record identities. Registry kind/relation counts describe the exchange vocabulary, not layers, business completion, legal compliance or backend coverage.

The following alias decisions are affirmative semantic compatibility decisions:

- Runtime `command`, `command_instance` and `command_attempt` describe a concrete request/attempt. They become `command_attempt` with all source profile fields retained. Frozen `commands` remain `command_definition`.
- Runtime `rule_evaluation_binding` and `evaluation_binding` bind fixed rules to role, field, parameter and time context. They become `evaluation_binding`. A result reference remains only a reference; it is not proof an evaluation ran.

All other runtime families stay distinct. In particular, a lifecycle-state aggregate is not a single receipt; generic check, constraint check and effect evaluation results have different proof scopes; expected predicate truth is not a native evaluation; general review, professional review and signature evidence remain separate.

## Four validation boundaries

1. Preservation validity: lossless source payloads, exact source pointers, unique storage IDs, resolvable endpoint references and declared vocabulary.
2. Semantic authoring validity/readiness: typed roles, exact identities, required signatures/units/scopes, AST structure, authority/enforcement/evidence bindings and explicit unknown gaps.
3. Native-rule verification: pinned real Atlas catalog/operator/evaluator versions plus native round-trip and outcome parity. Currently unverified.
4. Real-backend readiness: authenticated actual producers, legal/authority evidence, measured effects and appropriate device/backend connections. Currently unverified.

A successful graph import establishes only the checks actually reported. Missing production evidence is not a reason to destroy useful authored records; it is a reason to keep execution readiness false/unverified. An unsupported operator may be preservation-valid and still non-executable.

## No universal cardinality

The graph is a directed multigraph. An entity can have several scoped agents, a command several ordered lifecycle receipts, an execution multiple resource dimensions, a relation several role endpoints, and a source definition several original occurrences. Validation scopes any declared cardinality to a precise relation definition, binding or attempt, endpoint role, version and validity interval. The package never assumes one agent per entity or one edge per endpoint pair.

## Version and migration discipline

- `schema_version` identifies the exchange shape; `contract_version` versions this mapping policy.
- Source artifact SHA-256 and original revision values remain independent. A file digest is source-byte identity, not invented native rule revision, lifecycle epoch or authority.
- Compatible spelling migration changes only the canonical envelope. The exact original object, raw kind/relation, ordered fields/arrays and pointer remain available.
- Structural unsupported cases are retained with precise diagnostics or quarantined; never repaired by speculative source values.
- A future incompatible semantic change requires a new contract version, reviewed migration map and before/after regression evidence. Do not silently rewrite old exports or source records.

## Reproduction

Run `python spec/build_all.py` from this workbench (the script resolves its own path). Validate a graph with `python spec/validate_contract.py data/graph.json --report spec/validation-report.json`. This requires only local Python and the installed JSON Schema library. No evaluator, simulator, real device, network publication or external transaction is invoked.
