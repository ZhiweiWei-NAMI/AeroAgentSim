# AeroGraph registry compiler

The compiler turns an explicit AeroGraph type slice into an immutable
`aerokernel.registry.MemoryRegistry` and a pinned JSON snapshot. It reads persisted
JSON and local Git metadata. It never imports AeroGraph, runs its builders, creates
observations, binds writers, or imports AeroAgentSim's legacy SimPy modules. The
implementation uses only the Python standard library and aerokernel, on Python
3.10 or newer.

## Usage

From the AeroAgentSim-platform workspace:

```bash
PYTHONPATH=src .venv/bin/python -m aeroagentsim.integrations.aerograph compile \
  --root /mnt/data2/weizhiwei/AeroGraph \
  --select oo:UAV,oo:Order,oo:ObservationRecord \
  --out registry.snapshot.json --report report.md

PYTHONPATH=src .venv/bin/python -m aeroagentsim.integrations.aerograph inspect \
  registry.snapshot.json --type oo:UAV
```

Default policy excludes `proposal:` IDs, proposed/conflict reviews,
quarantined/candidate-not-accepted dispositions, and definitions explicitly marked
`acceptedAsCompleteContract: false`. Selection does not approve these definitions.
The current AeroGraph fields in this three-type slice and its incident relations
are proposed, so the default command produces an identity/ancestry registry with
exclusion records. To inspect the complete slice for an explicitly chosen research
scenario, add `--admit-proposed`. This admits gated definitions, including their
required reference/endpoint types, while preserving review status and marking
admission; it never makes them production-approved or assigns write authority.

Use `--admit ID1,ID2` for individual admissions, `--exclude ID1,ID2` for intentional
exclusions, and `--fields ID1,ID2` / `--relations ID1,ID2` for allow-lists. An empty
allow-list selects no definitions of that kind. A missing selected type/field or
relation fails with a diagnostic unless it is explicitly excluded. Selecting an
excluded parent or reference target through an active consumer still fails: exclude
that consumer or supply/admit its required definition. No constraint is weakened
to make an unresolved reference compile.

The inspector reads only the snapshot. It enumerates effective fields, schemas,
units, frames, semantic roles, temporal declarations and producer hints, plus
inherited incident relations and their normalized cardinalities. It also accepts
`inspect --snapshot registry.snapshot.json --type oo:Order`.

```python
from aeroagentsim.integrations.aerograph import (
    Policy, Selection, compile_registry, read_snapshot, write_snapshot,
)

compiled = compile_registry(
    "/mnt/data2/weizhiwei/AeroGraph",
    Selection(("oo:UAV", "oo:Order", "oo:ObservationRecord")),
    Policy(admit_proposed=True),  # explicit scenario research disposition
)
write_snapshot(compiled, "registry.snapshot.json")
pinned = read_snapshot("registry.snapshot.json")
assert pinned.digest == compiled.digest
assert pinned.registry.digest == compiled.registry.digest
fields = pinned.effective_fields("oo:UAV")
relations = pinned.effective_relations("oo:UAV")
```

## Selection and inheritance

`Selection.type_ids` is explicit and nonempty. Optional `field_ids` and
`relation_ids` are global allow-lists; `None` means all definitions applicable to
the selected scope. IDs and enumeration are sorted and deduplicated.

The source catalog is `entity-directory/data/concepts.json`. Field records come
from `semantic-directory/data/definitions.json`, including its listed shards, and
persisted `fields` arrays in pilot/capability/expanded documents. Relations come
from `semantic-directory/data/relations.json` and persisted extension relation
arrays. `schema-definitions.json` supplies shared local schema definitions.
No generated browser coverage or default/sample observation is materialized.

Only actual `parent`/`parents` edges establish inheritance. A root requires an
explicit null parent or empty parents array. `abstract` must be boolean. Suggested
parents, navigation directories/cross-indexes, `originalParent`, and unattached
profiles never create an `is_a` edge. A detached selected profile remains detached.
The catalog's nested `sourceReviewStatus` on retained baseline identities is
preserved as history; it is not an instruction to discard the catalog's adopted
identity hierarchy. Top-level review/disposition and proposal IDs are gated.

Effective fields follow adopted `ownFieldIds` along actual ancestry. An ancestry
node must supply this inventory explicitly; a missing inventory does not become
zero fields. An explicit field allow-list can also activate an additional
persisted field declared by a selected ancestor. It cannot attach an unrelated
capability/profile. Two IDs occupying the same `propertyPath` conflict, including
identical-looking definitions. A child can declare `overrides: <parent-field-id>`
or an array of IDs; each overridden field must occupy that property slot and be
declared by a strict actual ancestor. Override choices are logged per effective
type. Parent descriptors remain available for independently selected parents.

Relations are selected when either endpoint type belongs to the actual ancestry
of an explicitly selected type. This includes inherited endpoint contracts. The
scope is computed before adding auxiliary types, so incident relation selection
does not cascade through support types. Duplicate selected IDs, missing endpoints,
missing validity declarations and invalid cardinalities block compilation.

The registry includes ancestry, field reference targets and relation endpoints,
plus their actual parents, as auxiliary **type descriptors**. Their own fields and
incident relations are not automatically activated. `statistics.selected_types`,
`ancestry_types` and `types` distinguish these counts. Auxiliary types require
valid identities/ancestry and the same explicit research disposition.

## Normalization policy

Every applied interpretation records a rule ID, field/type/relation ID, source
file/pointer, and before/after values. Raw definitions remain in field metadata,
relation records and `details.raw_types`. Schema expansion retains the referring
field pointer and the referenced definition in its log; the shared definition
file is also content-hashed. Unsupported active constraints fail rather than
silently disappearing into metadata.

| Source dialect | Pinned interpretation |
| --- | --- |
| `configuration`, `specification` | `config`, `spec`; unknown roles require a reviewed source fix. |
| `#/$defs/...` | Resolve escaped JSON Pointer tokens, including nested pointers and field-local definitions. Missing, external and cyclic refs fail; active ref siblings requiring intersection fail. |
| Native `enum.values` / JSON `enum` / scalar `const` | Preserve typed values and unknown-valued members. Empty/duplicate vocabularies fail. Heterogeneous enums become tagged branches by strict scalar kind. |
| `oneOf` / `anyOf` / type arrays / native branches | Preserve branch order as explicit `$case` tags (`"0"`, `"1"`, ...). A single non-null branch plus null becomes `nullable: true`, including null in enums. No first-match branch selection. Producers must supply tagged values. |
| JSON `object` / native `record` | Kernel record with explicit members, required list and extra-member policy. JSON objects retain optional/open JSON Schema defaults when omitted. Native records are structural products: omitted required/extra means all members required and closed, explicitly logged. |
| `shape`, `minItems`, `maxItems`, `minLength`, `maxLength` | Explicit vector/matrix dimensions or kernel length bounds. Conflicting/inapplicable constraints fail. |
| Native `ref.target` / `targetClass` | Kernel `target_type` and `$ref` identity wire format. Original identity component/encoding descriptions remain raw; authored examples are not silently converted or used. |
| String units / `status: explicit` | Declared symbol with exact status; explicit status requires a usable symbol. Absence/unresolved status never becomes dimensionless. |
| `byte`, `Cel`, `count`, `count/s` | `By`, `degC`, `1`, `1/s`, respectively. Counting identities such as packet/person and scaled units such as cm remain distinct; no numerical conversion. |
| `unit.members` / `unit.memberUnits` / inline schema units | Preserve and normalize explicit leaf/component units, merge nonconflicting member maps, retain numeric leaf paths. Container count units do not type ordinary record quantities. |
| Declared identity integer / sibling quantity-unit member | Identity integers may use `1` only within declared identity/InstanceRef context. Numeric quantities can retain a declared sibling dynamic unit requirement; unrelated leaves still need units. |
| Audit v2 incident-time quotation | Only the audited occurrenceTimeEvidence contract with the exact source quotation `所有秒值使用明确来源时钟` supplies seconds to its enumerated time members. No generic suffix-based unit inference. |
| String frame | Preserve the declaration and require an instance frame/transform revision binding; invent no world frame or origin. Vector/matrix contracts require a frame declaration. |
| `clockRef`, temporal bindingPolicy, clock-bearing writer prose, configuration lifetime, schema clock/acquisition/availability/interval/age members | Retain separate temporal declarations and their source evidence. No timestamp substitution, clock instance, conversion or sampling is manufactured. Arbitrary per-observation lifetime is insufficient. |
| Legacy cardinality `min/max` plus optional `inverse` | Explicit targets-per-source and, when declared, sources-per-target bounds. `"*"` is unbounded. |
| Snake directional `minimum/maximum` or camel directional `min/max` | Normalize each declared direction independently. An explicitly present null maximum means unbounded; a missing maximum is invalid. Preserve scope and omit an undeclared inverse rather than inventing bounds. |

Units, frames and time remain descriptive contracts. Registry compilation does not
establish real clocks, frame identities, conversions, native operator semantics,
valid relation instances or observed numeric results. Numeric observation/derived
fields require source-backed temporal declarations. Required concrete bindings
belong to the scenario compiler and binding manifest.

Producer hints copy the selected field's `writer`, `ownerSubject`, role aliases,
producer/source roles, aliases and legacy references. They are exposed in
`compiled.producer_hints` with `authority: false` and source pointers. A description
such as Own/Oi or "bound_source_producer" is not a partition identity or permission.
Raw examples stay provenance, never runtime state.

## Provenance and snapshot identity

The snapshot contains compiler format/version, requested selection/policy, kernel
descriptors, effective field lists, normalized relations, raw definitions,
normalizations and their digest, exclusions/admissions, producer hints, and source
provenance. Exports are detached portable trees; in-memory collections are deeply
frozen. No wall-clock compilation time or absolute checkout root is hashed.

`provenance.descriptors` records a source file and JSON Pointer for each included
type, field and relation. `input_sha256` hashes the exact bytes of every compiler
input read, including manifests, field shards, shared definitions and extension
files. This pins the inventory scan too: changing an unselected record in a read
file can change the artifact digest, even if the kernel descriptor digest stays
the same. Review disposition and exclusions also contribute to artifact identity.

Git HEAD and dirty provenance are measured by reading `.git` metadata directly,
with no Git subprocesses or writes. Dirty scope is **tracked worktree/index files
and compiler input files**; unrelated untracked/ignored files are not surveyed.
The reader handles detached/loose/packed refs and worktree indirection, index v2/v3,
worktree blob/mode differences, and staged differences when necessary HEAD trees
are available as loose objects. A detected change gives `dirty: true`. Absent Git
metadata, unsupported index/submodules or unavailable packed tree objects give
`dirty: null` plus an explicit reason, never false. Source hashes remain the
binding evidence even when Git metadata is unavailable.

`compiled.registry.digest` is the kernel's descriptor digest.
`compiled.digest` is SHA-256 over canonical sorted UTF-8 JSON of the complete
artifact payload, omitting the digest member and terminal LF. Snapshots include
that digest and one LF. `read_snapshot()` recompiles only the portable kernel
subset, checks format/version and recomputes the artifact digest. It never reads
AeroGraph. Nonfinite values, duplicate JSON keys and digest mismatches fail.
The kernel's canonical serializer and resource budgets apply (8 MiB snapshot by
default); cross-Python/runtime numerical byte identity is not promised.

## Verified three-type slice

With explicit `Policy(admit_proposed=True)` against the persisted source at
HEAD `20da07f1599940eb2ea3d6997f61eb132ac6879c`:

| Explicit type | Effective fields | Inherited incident relations |
| --- | ---: | ---: |
| oo:UAV | 20 | 122 |
| oo:Order | 7 | 91 |
| oo:ObservationRecord | 11 | 92 |

The union has 3 selected types, 9 ancestry types, 104 total type descriptors,
28 distinct fields and 133 distinct relations, with 209 logged normalizations and
172 research admissions; there are no compile blockers or policy exclusions in
the admitted slice. Auxiliary proposed identities retain
research admission. Counts reflect this source tree and policy, not simulation
readiness. The CLI reports current normalization/admission/exclusion totals and
digests; integration tests assert nonzero content, no blockers, and matching
digests across two compiles and snapshot reload.

## Known gaps

- Predicate/event AST evaluation, capability attachment and runtime writer binding
  are separate integrations. This API selects types, fields and relations; it does
  not activate native predicates/events or create MessageDescriptors for them.
- Runtime cardinality enforcement is not supplied by this compiler. Consumers must
  use the pinned relation records until the kernel provides relation descriptors.
- General JSON Schema intersections, conditionals, patterns, formats, typed extra
  properties and recursive schemas are outside the portable kernel subset. Active
  selected uses fail with a source diagnostic; fix the selected source contract or
  explicitly exclude that definition. Nothing is replaced by a generic record.
- Tagged branches and strict binary64/integer distinctions require schema-aware
  adapter transport. The compiler never chooses a branch from sample/default data.
- Unit symbols are opaque source declarations beyond the listed equivalent
  aliases. There is no dimension conversion engine or spatial transform engine.
  Dynamic units, frame conventions and temporal prose require explicit bindings.
- Git dirty measurement has the stated scope and object/index limitations. Exact
  input SHA-256s and pinned snapshot content remain available independently.

## Kernel API requests

1. Add a portable `RelationDescriptor` and registry lookup/export/import API for
   endpoint types/roles, independently directed cardinalities, validity contracts,
   metadata and provenance. MemoryRegistry currently accepts only TypeDescriptor,
   FieldDescriptor, MessageDescriptor and schemas; relations are retained in the
   compiler's immutable snapshot details and included in its digest, not encoded
   as fake fields/messages.
2. Add source-aware descriptor compilation diagnostics identifying the offending
   field/schema path. Compiler-side source diagnostics already cover unsupported
   dialects; errors raised by the final kernel schema compiler can lack a field ID.
3. Consider optional non-executable descriptor annotations/provenance and explicit
   adapter codec requirements for tagged unions and native reference encodings.
   Keep native AST execution and ontology-specific authority outside the kernel.

No kernel files were modified for these requests.

## Verification

The legacy repository-wide `tests/conftest.py` imports SimPy. Limit conftest
loading to the new suite; no legacy environment is needed:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  HYPOTHESIS_STORAGE_DIRECTORY=tests/integrations/aerograph/.hypothesis \
  .venv/bin/python -m pytest --confcutdir=tests/integrations/aerograph \
  -o cache_dir=tests/integrations/aerograph/.pytest_cache \
  --basetemp=tests/integrations/aerograph/.pytest_tmp \
  tests/integrations/aerograph -q

.venv/bin/python -m ruff check --no-cache \
  src/aeroagentsim/integrations tests/integrations/aerograph

MYPYPATH=src:/mnt/data2/weizhiwei/aeroagentsim/aerokernel \
  .venv/bin/python -m mypy --strict \
  --cache-dir=tests/integrations/aerograph/.mypy_cache \
  src/aeroagentsim/integrations
```

MYPYPATH supplies the editable kernel source to mypy, without editing its package
or hiding missing-import diagnostics. Synthetic tests cover inheritance/overrides,
source dialects, quarantine, selection boundaries, diagnostics, immutability,
provenance, CLI and snapshot integrity. Hypothesis checks selection-order digest
invariance and directional cardinality round-trips; filesystem tests have no
wall-clock deadline. Real-source tests skip only when the checkout is absent.

Two independent DSH GLM review tasks were attempted. Both launchers failed before
creating a session because the existing profile required rewriting read-only
`cordis.yml`. No GLM review result is claimed; implementation and verification were
completed directly within the task's permitted paths.
