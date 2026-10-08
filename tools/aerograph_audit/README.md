# Read-only AeroGraph audit (v2)

Run with Python >=3.10, pure stdlib at runtime:

```bash
.venv/bin/python -m tools.aerograph_audit /mnt/data2/weizhiwei/AeroGraph --out docs/audit --select oo:UAV,oo:Order,oo:ObservationRecord
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_aerograph_audit.py --basetemp tools/aerograph_audit/.pytest-tmp -q
.venv/bin/python -m ruff check tools/aerograph_audit tests/test_aerograph_audit.py
```

Reports are deterministic JSON and Markdown. `--examples N` controls examples.
Successful report generation exits 0; `--fail-on-blocker` exits 1 for corpus
blockers without `--select`, or selected compilation blockers with it. Unknown
selected IDs and incomplete audits exit 2. Output must remain inside this
workspace and outside the audited source. Source files are read as JSON/text;
no upstream modules, builders, tests, Git commands or simulation run. HEAD is
read directly from Git metadata; dirty status is unmeasured. Input hashes are
checked once at completion to detect concurrent source changes.

Corpus findings respect review gates: proposal IDs, proposed/conflict reviews,
quarantined/unaccepted dispositions are reported as info. Preserved original
AST blockers are capped at major and marked `preserved_source`. Each finding
retains `contract_severity`, independently of corpus severity. Neither gating
nor a severity cap makes an invalid expression statically executable.

`--select` collects each type's own and actually inherited fields, relations
incident on that ancestry, contracts in its declared type/role scope, and their
transitive semantic, field and relation dependencies. Suggested parents and
unadopted mappings never become inheritance. The selected report restores
contract severity for these definitions, including gated and archived ones;
selection does not approve candidates or bind real producers. Shared blockers
are counted once in the selected union. Unbound field writers are listed
separately as integration requirements. Corpus findings remain unchanged by
selection. Contracts without type scopes are selected by field/relation use;
shared support fields do not pull in unrelated typed contracts. Full dependency
and finding IDs are in JSON; Markdown groups the
blockers and gives example IDs.

Findings have stable content-derived IDs, source-relative evidence paths and
JSON pointers. Missing members point to the nearest existing container and
retain the requested pointer in details. Finding records and affected
occurrences are distinct: native parameter defaults are grouped by
(rule, parameter name), but `count` and pointer lists retain every occurrence.
Context-derived unit suggestions never alter source defaults or declarations.
Writer counts also split by integration disposition and requiredWhen kind.

The audit recognizes explicit directional null-as-unbounded cardinality,
unit.members/memberUnits, declared reference identity integers, dynamic sibling
unit contexts, and alternate timing dialects. Identity/config/spec metadata
needs no outer sampling time. Actual numeric observations still need quantity
units and timing; actual spatial vectors still need frame declarations. Count
annotations can match dimensionless units at equal scale and physical
dimensions; different semantic counts, opaque dimensions and logarithmic
flavors remain distinct. Quaternion norm is dimensionless; ordinary vector
norm retains its component unit. Structured membership does not inherit an
unrelated scalar collection unit.

The bundled schema checker covers shipped keywords, not all JSON Schema.
Native `r` executes canonical targets; `b` audits inline execution and retains
its provenance label. Guarded unknown branches remain unknown. Specialized
native geometry/graph operators without an audit-owned proof are excluded from
static readiness. Browser-generated leaf-state contracts and metadata are not
materialized source inputs and are not fabricated by running upstream builders.
Static readiness does not establish producer integration or live execution.

`units.py` began as an audit-owned copy of AeroGraph's pure-stdlib
`semantic-directory/src/expanded_units.py` at HEAD
`20da07f1599940eb2ea3d6997f61eb132ac6879c`, original SHA-256
`63afae4ff49647041b98e58e2951d38b0f22ac0c60cb3ed1c1fbd5eafd0a4855`.
V2 changes the audit copy's counting compatibility, norm typing and structured
item inheritance; no upstream module is imported or modified.

The generated Markdown includes **Audit corrections (v2)** with accepted and
rejected verifier proposals and **Upstream fix list for AeroGraph maintainers**
with priorities, recomputed counts, examples and concrete source-owner actions.
The v1 baseline is 431 blocker, 9,832 major, 0 minor and 1,034 info records.
