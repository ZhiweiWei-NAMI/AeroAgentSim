# Urban native physics evidence contract

Status: **implementation contract, not execution evidence**. No native producer or
successful urban run is established by this document. Inspection artifacts and
sealed runs do not adopt this urban-only inventory.

## Required private artifacts

The urban flight workload and offline verifier now both declare:

- `gazebo.physics-journal` (`flight/physics-journal.jsonl`): canonical,
  newline-terminated `PhysicsJournalRecord` objects from the native engine.
- `gazebo.physics-plugin` (`flight/physics-plugin.so`): the producing plugin's
  actual binary bytes, not a digest string, metadata document, or estimated data.

These artifacts are private. Agents receive neither artifact grants nor mounts
for them. The seal binds producer, path, type, visibility, byte count and SHA-256.
The independent verifier re-reads and hashes those files; a nonzero digest in a
ledger payload alone is insufficient.

The **current PX4 service does not support these two artifact types**. Its
capability/inventory check rejects the declaration. Do not remove that check or
substitute trajectory records to start a nominally formal run. A native producer,
its real image/plugin build provenance, and publication integration are still
required.

## Native record and window semantics

`aero_bench.tasks.urban_recovery_demo.contracts.PhysicsJournalRecord` defines one
4 ms step, with the run/scenario/provider, source `gazebo.system`, producing plugin
SHA-256, engine timestamp, sequence, and sorted unique wrenches. Each wrench names
a vehicle, engine link, component, ENU force in newtons and torque in newton-metres.

Every step must cover both UAVs' applied `wind`, `aerodynamic`, `rotor` and `gravity`
components. Contact wrenches must have origin `solver_measured`; other components
must have origin `applied`. Observed classified contacts require solver-contact
coverage. Net acceleration, estimated wind, a commanded force, or telemetry alone
cannot supply these missing independent component measurements.

For each 200 ms logical barrier, `EngineWindow` describes **exactly 50** native
records. Its sequence range is `(first_sequence, last_sequence]` and engine-time
range is `(engine_start_ns, engine_end_ns]`. `journal_sha256` hashes those exact 50
JSONL lines **including each trailing newline**, not a cumulative prefix or JSON
array. Windows retain one stable warmup engine origin and have no time or sequence
gaps. Exactly 3000 windows must cover the 600-second main run; trailing journal
records are rejected.

Every native wrench must match exactly one ledger `gazebo.applied-wrench.v1`
projection. A missing, duplicate, extra, altered, off-grid or foreign projection
fails verification. Comparison indexes records by barrier and decodes at most one
window at a time; it does not scan the entire run for each 4 ms timestamp. The
existing sealed-byte reader and event ledger still materialize complete inputs,
so this is **not** a full-run memory acceptance result.

Airspace evidence must use the same independently checked engine origin and the
configured Gazebo vehicle model name. Both UAVs' boundary observations are checked
against the recorded entry/exit interval and declared world/region identities.

## Report semantics

The supported urban goals are fixed and distinct:

- `urban.recovery.exact_horizon`: whether the validated terminal time is exactly
  tick 3000 / 600 seconds.
- `urban.recovery.mission_completed`: whether all implemented mission checks pass.

Both require equality to 1.0. Unsupported metrics, altered thresholds/operators,
foreign verifiers or an incomplete goal inventory are rejected, not assigned a
common success boolean. A mission failure does not erase a proven exact horizon.
If it stops validation before later checks, the report does not claim complete
coverage. Missing/malformed authority yields invalid results with no metrics.
These aggregate goals are not a claim of comprehensive per-phase scoring or final
acceptance of every mission validator.

## Still required before delivery

Implement and validate real native component/solver instrumentation; bind the
loaded plugin to the actual pinned image and source build inputs; seal the raw
journal without overwriting old evidence; close private world/toolchain truth and
safe flight policy integration. Then perform real preflight, short integration,
full-run sealing, independent verification, production-browser recorded-only
replay and visual acceptance. Unit callback/JSONL fixtures are never substitutes.
