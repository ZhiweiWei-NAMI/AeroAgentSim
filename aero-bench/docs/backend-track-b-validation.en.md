# Backend Track B validation

The primary agent owns this backend work in the main AERO_BENCH checkout.
Frontend implementation is assigned separately to Claude Code. This report
records module tests and native-container diagnostics separately from formal
run acceptance. The fresh R5 native inspection reference passed its declared
OCI verifier and published an audited verified replay. A separately compiled
explicit reference also passed authenticated native Control execution. General
city compilation and physical logistics remain separate, incomplete gates.

## Latest accepted reference execution

R5 Run ID is
`fd72f4efc2953a2077edc2652c0eca4072be5847a19e0a1556764880d257f564`.
Its source-matched seven OCI images pin closure
`f71a4a497b8e8522833b74ff79a747efd0183dc0dd7b8e57881d576f1e5bf98c`
(233 files), recorded in `reference-images-r5/build-inputs.json`. The final
summary records ready preflight, completed execution, 13 sealed artifacts,
independent verification `passed`, public replay publication and no failure
classes. All 15 goals passed; detection F1 is 1.0. Native execution completed
524 ticks (262 seconds), within the unchanged 600-tick horizon.

The user explicitly approved the v3 landing-rule revision before this run.
It verifies airborne descent into native permitted pad contact, continuous
grounded contact/geometry, then MAVSDK landed confirmation. The existing
0.35 m/s grounded-confirmation limit, 0.5 m AGL limit, footprint containment,
safety, stopped dwell and disarm checks remain. The limit is not relabelled as
an impact-speed bound. No PX4 flight-controller parameter was changed. The
config and policy schemas are v3 and the OCI verifier implementation is
`0.4.0-inspection-verifier.2`. The old failed R3 result was not reclassified.

The capacity model was recorded before raising the ledger budget to 512 MiB.
R5 measured 225682396 event-log bytes, within that declaration. A successful
run does not prove a universal peak bound; the model distinguishes R3's
measured peak from the full-horizon estimate.

The public replay's phase is `verified` and its public verdict is `passed`.
The read-only actual audit passed its contracts, hashes, sizes, closed inventory
and indexed history checks: 524 SceneStates, 1596 public events, 42 files,
106070604 bytes. Evidence: `reference-r5-public-replay-audit.json` and `.log`.
Trace SHA-256 is
`d81252c6a31907800f2d525014daeae56c52755fc5d8fa565cdc7cc8f35ee338`;
manifest SHA-256 is
`c3797f811373cc8cafe5705f9d2d4f0eea96632d9570500425564a61a32d6be5`.
Paths and frontend integration instructions are in coordination entry B-010.

This closes B1 for the two-target deterministic native reference. It does not
accept the model-agent release, the 414-building authored city, logistics,
physical charging, or frontend screenshot fidelity.

## Accepted compiled-reference backend handoff

The immutable compilation
`beafba9d964949cd9798278eedb673445af38e04d4d9ed70f3941d507ec286c5`
was created from an explicit operator-authored workspace v2. It is not the
browser-saved Shanghai draft. Its native registration is
`inspection.reference.explicit`, pin
`8024d5251428c97d928e775a9bbab1f955173431cac7d5808e7e6b8527e16618`.
Fresh `registered-reference-r2/` republishes current catalog metadata without
changing that pin or the public-scenario hash.

`tools/validate_compiled_city_run.py` loaded that publication through the
authenticated HTTP Control catalog/start/status path and required the recorded
executor/Run identities. Actual Run ID is
`f316faaef72168afa89aceb10df10af4211b1aa98c28a4b64f6cc89aa056fc8b`;
scenario digest is
`ac61f64a7e2eb811c5b82753f288911f017f29f9d63685dd283246cbe02673f0`.
It reused R5's frozen digest-pinned images and landing policy v3, completed
522 native ticks (261 simulated seconds), sealed 13 artifacts and passed all
15 independent OCI verifier goals. The audit records no management failure,
`passed: true`, `http_catalog_start_status_validated: true`,
`browser_validated: false`, and `credentials_persisted: false`.
Credentials remained in memory; no configuration was changed.

The runtime seal digest is
`90bbe03490f84366c4c9be81406893edc3d62898777bff80a96a68ed73db7893`;
event-chain root is
`36801e50697b483627e38a14bd8e67b788e9f83eec004e2f334f140bd6c3db9a`.
The event ledger measured 224832447 bytes. The public replay audit passed:
phase `verified`, verdict `passed`, 522 SceneStates, 1590 public events,
42 files and 105664013 bytes. Its trace/manifest hashes are
`910df30577c2bbaa973f5647475b7687fdc3d979feeada93b1f9d7c25dc9e8eb`
and `a694c700aa28c682654ba57d7b6d258238c2ce323dce91b1d74aa1b2bab29c13`.
An actual authenticated public-asset download matched its 466-byte declaration
and SHA-256 `2f8a7c199cbd15aa312055f0abc561b2015ab9f03cd9a0a6a33fde60ed13b560`.
Evidence is under `compiled-native-control-r2/`, plus
`compiled-native-public-replay-audit.json` and `.log` in the backend directory.

The first HTTP-only attempt stopped before workload creation because urllib
used an ambient proxy. The loopback Control client now uses a direct opener
for both JSON and SSE requests; the focused regression passed. That change is
limited to loopback client routing, not proxy credentials or global settings.

This closes the limited compiler-to-Control backend execution check.
Frontend save/compile/run/replay integration and general city lowering remain
open. Compilation alone still reports `executed: false` and `verified: false`;
the later runner/public verdict supplies execution authority.

## Logistics source reconstruction and current module validation

New `runtime/sealed_motion.py` independently validates the exact declared
artifact inventory, seal hashes and canonical manifest, authoritative runtime
ledger, indexed SceneState history, stage closures and source-event placement.
Its read-only audit of the actual R5 seal passed 524 closed frames and 524 PX4
source events. Evidence: `reference-r5-sealed-motion-audit.json` and `.log`.
This is an integrity audit, not another benchmark verdict.

New `providers/px4_gazebo/sealed_motion.py` checks the non-inspection native
JSONL snapshot profile. Each closed source event must match the exact vehicle
facts, paused-step snapshot, receipt digest and historical trajectory-prefix
hash. The reader pins run/config/image, PX4/Gazebo/MAVSDK identities, complete
vehicle inventory and consecutive declared times. It rejects inspection
trajectory arrays rather than reinterpreting them. R5's inspection trajectory
uses its existing independent Inspection verifier; it is not evidence for this
new non-inspection reader's native execution gate.

New `tasks/logistics/sealed_observations.py` loads the real declared Business
config and Task pins, validates native fleet bindings, re-derives all pad
observations, and replays the append-only Business journal from the closed
motion stream. It compares journal acknowledgements and private/verifier-only
airspace segments in the actual post-stage/pre-commit window. The standalone
`tools/audit_logistics_observations.py` reports source integrity only:
no task verdict, dwell provenance, order-transition acceptance or battery
transfer check. The hook's segment producer prepares before RPC and commits
only after Business acknowledgement. Reference-point linear interpolation
and the absence of body-clearance/fence-enforcement claims are explicit.

The source/replay selection passed 38 tests, including the actual Business
service over JSON-line RPC with synthetic motion. Synthetic snapshots, staged
frames and journal fixtures are not formal Provider evidence. Existing test
fixtures now name current registered adapters and required runtime stages;
production parsing did not gain defaults or legacy aliases. Physical
pickup/handoff/delivery, native charger/battery coupling and the complete
independent logistics verifier remain unimplemented. The runtime availability
flag stays false and no physical capability was registered.

The backend pack publisher now validates the current OSM2World
`coordinate_contract` and actual producer/projection-helper byte pins.
Fresh publication inventory remains exact. Tests copy only the current
declared files into temporary directories; retained user cache files and
historical manifests were not removed.

The final expanded regression passed 925 tests in 109.89 seconds with no skips,
including the opt-in digest-pinned SUMO Docker integration check. Its command
was:

```bash
AERO_TEST_SELECTED_NETWORK_DOCKER=1 pytest -q \
  tests/tasks/test_logistics_*.py tests/providers/test_logistics_business_*.py \
  tests/providers/test_px4_sealed_motion.py tests/authoring \
  tests/runtime/test_sealed_motion.py tests/test_city_workspace_backend.py \
  tests/test_control_client_proxy.py tests/test_control_public_assets.py \
  tests/test_inspection_landing_v3.py
```

Log: `backend-final-regression.log`. The final 62-test source/publication
selection passed, including rehashed incorrect producer/helper pins.
The 78-file generated-contract
check, scoped Ruff and read-only frontend type checking passed. Earlier
578/884-test selections cover different or overlapping modules; counts must
not be added. The failed intermediate fixture runs are retained as diagnostics.

## Current city candidate evidence

Codex independently reran Claude's v6 strict road audit against actual network
SHA-256 `38bd3d8a2609cecef7f0c98608e0d58eb9f5123448fd675a6e1d8dff6ccfeffc`
and source OSM SHA-256
`0f985e5963b04b4096b4f93d0fd5523279ba7d850b8d64348ff99aa7b946e057`.
Result: strict geometry PASS, zero building contacts/area, collapsed native or
turn lanes, undrawable walking areas, crossings and abutments. The measured
passenger/bicycle/pedestrian boundary-connected shares are
0.7767/0.7819/0.7521; interior dead ends are 50/50/18. This does not add a
connectivity acceptance threshold. Evidence: `claude-v6-ground-audit.json`
and `.log`. Earlier blocked geometry reports remain unchanged.

The candidate is not the current default publication or an accepted native
WorldPackage/Task. The default remains building-only. The saved current
Shanghai draft is strictly parsed, then blocked for its unregistered scene,
preview-only second airframe and missing Agent image. New-city geometry,
formal registration and Task execution are separate checks.

## Explicit stages and network test contracts

The prepared explicit-stage patch is integrated. The initial module run passed
227 tests and failed six ns-3 tests. Five failures used obsolete client fixtures
without the required resolved scenario and with the removed `step_to` operation.
The other negative case incorrectly equated physical network node endpoints
with task logical grants. The declared candidate08 inspection bundle has no
task logical endpoints and declares `endpoint.operations` and `endpoint.uav`
as physical network endpoints. These are different contracts.

Tests now compile a strict mechanical world/scenario, use
authorized source mailbox grants, and consume delivery evidence within
`step_stage`. The corrected backend regression selection passed 295 tests in
295.67 seconds. The generated-contract consistency check passed all 68 files.
Fixtures are not formal evidence.

## Native ns-3 selfcheck diagnosis

The original digest-pinned ns-3 image's native selfcheck aborted. A diagnostic
image adds failure measurements and fixes the Python selfcheck's out-of-scope
pose helper. It is labelled `executor_validation`, not a formal runtime release.
Evidence is under `validation/backend-track-b-20260930/ns3-native-diagnostic/`.

Measured native result before changing parameters: zero of three UDP packets
delivered in 50 ms. Received-power calculations were -42.7547 dBm at 2 m and
-51.7856 dBm at 4 m. Payload integrity did not fail; no payload arrived.
The configured one-way propagation delay was 2 ms and rate was 1 Mbit/s.

### Model and hypothesis before parameter experiments

At 4 m, light-speed propagation takes 4 / 299792458 s, or 13.343 ns; its integer
upper bound is 14 ns. A 2 ms one-way delay corresponds to about 600 km of
light-speed path, despite the selfcheck's 4 m node separation. The current
ns-3 3.48 source computes the normal-ACK timeout from transmission duration,
SIFS, slot time and ACK PHY preamble/header duration, without adding this 2 ms
round trip. The hypothesis is that this selfcheck's declared delay prevents
normal Wi-Fi frame exchange. Packet loss remains a valid outcome for declared
best-effort links; the production Provider must not manufacture delivery.

With the configured 20 MHz channel and 7 dB noise figure, thermal noise is
-174 + 10 log10(20000000) + 7 = -93.990 dBm. At the measured 4 m received power,
SNR is about 42.204 dB. The ideal Shannon bound is about 280.4 Mbit/s, not an
achievable PHY measurement. The declared 1 Mbit/s queue rate is the tighter
capacity bound. Three 1200-byte payloads require 28.8 ms at that rate before
burst credit; the 1500-byte initial token credit permits a burst. For three
1228-byte IPv4/UDP packets, the remaining 2184 bytes require at least 17.472 ms
of token replenishment, excluding MAC/ARP overhead. The existing 9.6 ms
arrival-span assertion is a conservative shaping check.

The first experiment changed only the explicitly supplied selfcheck delay from
2 ms to 14 ns. Packet count, payload, seed, power, loss model, shaping rate,
burst credit and 50 ms horizon remained unchanged. All three packets arrived:
first at 11,461,098 ns and last at 28,304,154 ns, satisfying the unchanged 9.6 ms
shaping-span assertion. This supports the timing hypothesis. The native
selfcheck default now uses 14 ns. The Python service selfcheck uses 34 ns for
its maximum 10 m separation. It delivered the exact 28-byte payload at
12,786,238 ns, within the second 10 ms barrier. It applied changed staged
positions, measured obstruction loss from 9 dB to 0 dB, and finalized its
354-byte delivery artifact with SHA-256
`79043561c2f03e64f1426c9c4fe0cf233a072f21f79dc61a2efba78809e3f87a`.
Production link delays, rates and loss models were not changed.

Both checks used diagnostic image
`localhost:5000/aero-bench/ns3-native-diagnostic@sha256:2a118cdd761f45c9d0c11aa75f48cd13f013befa03c28f01f1eaf8cc18a0ceaa`.
The first service invocation failed because its supplied artifact directory did
not exist; the successful invocation used the existing `/tmp` tmpfs. Both logs
are preserved. The runtime was non-root, read-only, capability-free and isolated
from host network and paths. Diagnostic fixture identities are not formal run
identities, and neither selfcheck is independent verifier acceptance.

The ns-3 source inside the pinned image is the version-specific authority for
the ACK calculation. The [official model library](https://www.nsnam.org/docs/models/ns-3-model-library.pdf)
also documents ARP pending queues and packet trace diagnostics; a queue or
address-resolution failure remains an alternative hypothesis until measured.

## Current-contract deterministic reference

The new `inspection.reference.v1` profile runs an explicit deterministic
participant against two targets in the existing 18-building native world.
It reuses actual PX4/Gazebo, SUMO, ns-3 and Inspection Business Providers and
the independent Formal Inspection v2 Verifier. All 15 verifier criteria and
their thresholds remain unchanged. It declares no model driver and does not
claim acceptance of the model-agent release, the 414-building city or logistics.
The [pre-run model](inspection-reference-performance-model.en.md) separates
geometric/radio bounds and flight assumptions from measurements.

The participant uses only granted public geometry, Gateway commands/queries
and actual RGB observations. A versioned pixel classifier distinguishes a
dark patch inside the red panel from a clean panel. PNG filter, clean/damaged
image and exact report-upload tests use synthetic unit inputs; those inputs
are never passed to formal workloads. Private model assets and truth are not
granted to the participant.

The first real attempt, Run ID
`05b757f84fcd338f8a2711515cbfca0fd63f059f43831963cb7f81a11b35c801`,
failed before tick one. Gateway audit enforcement requires a decision summary
before every formal command. The participant now records that summary with
the command's identity before dispatch. Enforcement was not relaxed. An
audited stop closed the failed attempt at tick zero; diagnostics and partial
Provider artifacts remain under `reference-run-r1/execution/`. They are failure
evidence, not an accepted seal or replay.

The failed participant also exposed that the Docker wait path only observed
model-driver failures early. Fail-fast observation now covers every formal
participant and the existing real engineering startup path. Diagnostic tests
were brought to the explicit current attempt/Agent contracts. The combined
audit, Gateway and Docker selection passed 95 tests in 86.25 seconds; the
reference/profile selection then passed 18 tests in 4.97 seconds. Contract
generation passed its 68-file check. Ruff passed the six new/changed reference,
PNG and image-builder modules/tests. Logs are in the backend evidence directory.

R2's seven images were digest-pinned and passed runtime identity checks.
The source closure is
`6cf86bddfecd3c9839f69d8836acb9523a35b90065eeca53be747faffdbfa237`;
the exact image digests, labels, frozen build inputs and logs are in
`reference-images-r2/`. Run ID
`c574bf87db067f8a8c501aa8303175adaacedb06d41d7ed1d72d0f021ae0fff9`
completed both inspection legs with four actual RGB frames and two detections.
It returned to launch but neither report arrived while it hovered about 20 m
above the operations radio. The participant exhausted its declared delivery
wait at tick 526 (263 seconds); the runner failed fast and produced no accepted
seal, verification or public replay. Partial artifacts and the failure index
remain under `reference-run-r2/execution/`. Abrupt failure teardown did not retain
the in-memory event ledger, so that attempt cannot be reconstructed as a run.

The native radio comparison reused the exact 1311-byte aggregate report, seed,
propagation delay and complete radio profile. At R2's measured airborne
position it delivered zero of two messages; at the proposed landed launch
position it delivered both. The Provider selects constant `HeMcs11` data mode;
the 6 Mbit/s queue limit does not lower that PHY mode. The performance model
records the received-power, noise and capacity calculation made before the
experiment. This diagnostic supports landing before upload; it does not prove
formal Business completion. Evidence is `reference-native-radio-comparison.log`.

R3 updates the explicit reference mission and participant together to return,
land, upload, then disarm/finish. Radio parameters, propagation delay, loss,
queue rate, horizon and verifier thresholds remain unchanged. Failure collection
now requests an audited abort before stopping a live Harness, waits for its
normal shutdown, and records unavailable control/flush attempts explicitly.
It cannot promote partial artifacts into an authoritative seal.

The R3 source closure is
`3f96771055bd76b264c4d82453bde8be994db051c9ded3beb2b9de71753b05a9`;
the seven image identities and frozen inputs are in `reference-images-r3/`.
Run ID `d9bf10b264858ff8a53cfb60b0c345fc77b96eb630d80b9286348697d412ffcb`
closed with runner status `error`. The native participant completed at tick 522
(261 seconds); all runtime workloads exited zero and 13 declared artifacts
sealed. The exact aggregate report arrived through ns-3 twice at
256510360090 ns and 256510386355 ns. The declared OCI verifier exited one;
the original summary has `verification: null`. Its failure collection did not
retain verifier stderr, and a subsequent public projection error obscured the
latest diagnostic. The original summary and sealed bytes are preserved.

### R3 evidence-boundary repairs

The following repairs use the actual immutable seal and focused tests. They do
not change the 15 verifier criteria, thresholds or Provider physics:

- Delivery ordering now validates delivery records through `delivered_at`,
  rather than passing bare times to a record-based check. Missing clocks,
  backwards ticks and backwards simulation times remain errors.
- Frame authority selects `public.sensor-frame` events. A private audit record
  and its public capture are not two captures; repeated public frame IDs still
  fail.
- Production inspection projection validates its exact 18-field frame metadata,
  sealed public image/index artifacts, digest identities and the one-barrier
  capture-to-publication delay. It does not publish private pose coordinates.
  Cold-import tests cover the lazy domain import required to avoid a runtime
  bootstrap cycle.
- Public verification summaries omit only known sealed private references.
  They preserve metrics and outcomes and require public support for each
  metric. Unknown artifacts, duplicate public evidence and absent selectors
  fail. Other public references still reject private artifacts.
- Docker verifier failures now collect bounded, redacted workload logs before
  cleanup. The runner retains per-stage diagnostic files as well as the latest
  summary, so a projection error cannot erase the verifier cause.

The repaired host diagnostic evaluates all 15 criteria: 14 pass, landing fails,
and semantic F1 is 1.0. Launch-pad contact starts at tick 504; the landed flag
first appears at tick 507 with +0.0183996 m/s vertical velocity. The current
criterion requires a new landed transition with negative velocity of magnitude
at most 0.35 m/s. The preceding descent is about -0.70 m/s. Velocity is MAVSDK
telemetry; pose/contact are native Gazebo evidence. The
[performance model](inspection-reference-performance-model.en.md) records the
transition and capacity measurements. This diagnostic is not the declared OCI
verdict and does not make R3 accepted.

### Actual sealed public replay

The repaired projector published a sealed-only replay from R3's native data:
522 SceneStates, 1590 public events, 42 files and 105640184 published bytes.
Its phase is `sealed` and its `verifier_public` is null. The original runner
summary still reports the original errors; publication does not rewrite history.

Public artifacts are under
`reference-run-r3/execution/d9bf10b264858ff8a53cfb60b0c345fc77b96eb630d80b9286348697d412ffcb/public/`:

- `public-trace.json`, SHA-256
  `e1ded10d9a33fc9c65b480ad3f02658ab0dc55ceef1d251b0041c6e8a4150997`.
- `replay/replay-manifest.json`, SHA-256
  `436752e766ae6cf5e045c31de38c5f518126ac91279c280ac3eb69785f2b2fb1`.

The read-only auditor passed the actual replay's contract, hash, size and closed
inventory checks. It does not run the benchmark Verifier. The frontend may use
these public bytes for strict loading and sealed/nonverified display checks,
but must not consume private seal inputs or show a pass. The diagnostic public
report projects all 15 goals with public support and no private artifact IDs;
it is not attached to this replay as an authoritative verdict.

The expanded current-contract regression passed 480 tests in 410.19 seconds
before the landing/failure-collection changes. The focused selection for those
changes passed 79 tests in 72.64 seconds. The read-only public replay auditor,
projector, strict trace and indexed-publication selection passed 44 tests in
5.71 seconds. The auditor checks published contracts, hashes, sizes, closed
inventory and indexed history; it reports the existing public verdict without
running a benchmark Verifier. Its synthetic unit publications are not formal
evidence. The latest generated-contract check passed all 68 files. The combined
post-change regression passed 510 tests in 408.12 seconds; its log is
`current-contract-regression-r3-verified.log`. Counts from the overlapping
focused selections are not additional tests.

The first indexed auditor fixture incorrectly changed its task package while
retaining an Inspection RGB payload. That caused seven fixture setup errors,
including in a regression process that had already imported the earlier file.
The corrected fixture varies only the public replay-mode hint, preserving the
original task and payload contracts. The 44-test rerun and final 510-test run
both passed. Earlier failure logs remain in the evidence directory.

The later verifier/projector/failure-record selection passed 208 tests in
73.10 seconds. Cold-import and boundary tests passed 30 tests in 2.62 seconds;
frame authority, projection and delivery ordering passed 19 in 2.61 seconds.
The latest privacy and failure-boundary selection passed 52 in 3.06 seconds.
These selections overlap; their counts must not be added. The broad final
regression passed 578 tests in 425.15 seconds; its log is
`current-contract-regression-r5.log`. The final generated-contract check passed all 68 files,
and scoped Ruff passed the modified reference, verifier, executor, projector,
runner, tools and focused tests. These are module checks, not a formal pass.

The first final replay-audit invocation used the parent `public/` directory,
which lacks `replay-manifest.json`; the correct root is `public/replay/`.
That read-only command error did not modify the publication. The corrected
actual audit passed and is logged in
`reference-r3-sealed-replay-audit-final-corrected.log`. Use:

```bash
python3 tools/audit_public_replay.py --replay-root \
  validation/backend-track-b-20260930/reference-run-r3/execution/d9bf10b264858ff8a53cfb60b0c345fc77b96eb630d80b9286348697d412ffcb/public/replay
```

### Remaining backend gates

B1 reference acceptance is now established by the fresh R5 result above.
The R4 images were built before the final frame-authority/privacy fixes and
were never run; do not reuse their source lock as current source. No new formal
attempt is active. All failed-run evidence remains available.

B2 now has strict workspace v2 input, explicit native registration, immutable
compiler publication, generated request/result/error contracts, and positive
and negative module tests. B3 has a compiled-publication handoff into the
existing authenticated Control service, now validated by the actual compiled
native run above. Browser integration and general lowerers remain open; these
paths do not accept the
unregistered Shanghai scene. The browser-saved sample is available under
`validation/frontend-opus-20260930/editor-review/`.
B4 needs accepted new-city native inputs and still has backend implementation
gaps in physical logistics, native charging and its complete verifier.
The project coordination record (session record, omitted from this export)
records
frontend ownership, current public inputs and these gates.

To create a fresh attempt from the current backend source, use unused output
directories; neither builder overwrites an existing attempt:

```bash
python3 tools/build_agent_inspection_images.py \
  --participant-profile reference_inspection \
  --output validation/inspection-reference/images-new
python3 tools/build_inspection_reference.py \
  --images-lock validation/inspection-reference/images-new/images-lock.json \
  --output-root validation/inspection-reference/run-new
python3 -m aero_bench.runner.cli \
  validation/inspection-reference/run-new/suite.yaml \
  validation/inspection-reference/run-new/runner.local.yaml
```

Image and bundle source closures must match. `model_inspection` remains a
separate profile requiring its declared model CLI; the reference profile never
substitutes for it implicitly. B1 acceptance requires the final summary to show
completed execution, sealed evidence and an independent verifier pass. Command
receipts and diagnostic reports cannot substitute for that result. Preserve
failed attempts when diagnosing subsequent changes.
