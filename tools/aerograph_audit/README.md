# Read-only AeroGraph audit

Run from the aerokernel workspace with Python 3.10 or newer (stdlib only):

```bash
.venv/bin/python -m tools.aerograph_audit /mnt/data2/weizhiwei/AeroGraph --out docs/audit
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check tools tests
```

The CLI produces deterministic `aerograph-audit.json` and `aerograph-audit.md`.
`--examples N` controls examples per check. Reports default to exit status 0 when
successfully written, even when findings exist; `--fail-on-blocker` returns 1
for blocker findings. Invalid invocation or a failure to finish returns 2.
Output must stay inside this workspace and outside the source tree. Input is
read as JSON/text; no source Python, JavaScript, build scripts or tests run.
Only `git rev-parse HEAD` and `git status --porcelain` are used for source Git
metadata, with optional locks disabled. No commits, branches or resets occur.

Each finding has a stable content-derived ID, check, severity, artifact,
object_id, message, evidence path/pointer and count. Paths are source-relative;
pointers identify actual JSON containers, or `line:N` in text. Missing keys
and audit-local generated wrappers point to the nearest existing container;
`details.requested_pointer` retains the requested member location. Grouped
schema findings include individual key failures. `count` is the number of
occurrences/violations for a finding, not necessarily distinct objects.

The JSON includes source HEAD/dirty status, SHA-256 inventory, all findings,
recomputed metadata claims, inherited field status, AST dependency closures,
capability reconstruction, seven-directory readiness and three vertical-slice
fix lists. It checks input hashes again once at the end to detect concurrent
source edits. Report generation does not repair or normalize source files.

The tool checks shipped schema keywords (required, type, enum, minimum,
anyOf, const, properties, items, additionalProperties); it is not a complete
JSON Schema implementation. Value-schema references are resolved locally.
Explicit member units are propagated through typed structures. Dynamic
quantity units remain requirements, not fabricated dimensionless values.
Native `r` dependencies execute canonical targets; `b` validates the source
label and executes inline AST. Parameters/defaults remain local to their
native target/leaf or object definition. A guarded unknown branch is preserved.
Specialized native geometry/graph operators lack a complete static proof and
are excluded from static readiness; no expression is evaluated on live data.

Current source profile mapping is reconstructed from adopted inheritance,
current fields and the generator's literal candidate mapping policies, including
813 source mappings, 20 explicit candidates and 22 unadopted mappings in the
reviewed tree. These counts are not hard-coded as correctness assertions.
A new mapping policy is reported for inspection rather than silently guessed.
Auto-generated leaf-state contracts/browser coverage metadata are not persisted
source data and are excluded, with that limit stated in the report. Cross-artifact
identity checks use a separate materialized semantic identity set when available;
otherwise the shared-input arrangement is reported explicitly.

`units.py` is an audit-owned, stdlib-only copy of AeroGraph's
`semantic-directory/src/expanded_units.py`, read from source HEAD
`20da07f1599940eb2ea3d6997f61eb132ac6879c`; its original SHA-256 is recorded below.
It preserves strict unit inference, scale, affine/log and counting-unit semantics.
Only formatting/lint changes and an attribution header were applied to that copy;
no upstream module is imported. The remaining audit implementation is independent.

Original unit helper SHA-256:
`63afae4ff49647041b98e58e2951d38b0f22ac0c60cb3ed1c1fbd5eafd0a4855`.

Implementation delegation attempts are retained in the ignored workspace directory
`.audit-agents/`: two distinct DSH session stores and two logs confirm concurrent
`workbuddy/glm-5.3-flash` attempts with 131072 maxTokens and no effort argument.
Both exited with TRANSPORT errors; they produced no source changes.
