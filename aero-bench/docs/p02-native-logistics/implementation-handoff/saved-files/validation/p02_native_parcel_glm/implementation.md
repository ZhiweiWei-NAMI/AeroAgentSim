# P02 Native Parcel Slice — Implementation Record (GLM)

Status: **module-complete, integration-deferred.** The one-order native parcel
runtime admission slice is implemented, strictly typed, and exercised by a
focused module test suite. No native run was launched, no capability was
enabled, and no sealed provenance was minted.

## Delivered files (the only files written for this slice)

| File | Content |
| --- | --- |
| `aero_bench/tasks/logistics/native_parcel_contract.py` | Strict typed contract surface: identities, carrier binding + signed calibration, required body-local attachment transform, authored dwell policy, admitted action/RX evidence, custody holders, exactly-once transfer records (`.mint()`), state/transition records, dwell admission + admission outcome types, canonical notes. |
| `aero_bench/tasks/logistics/native_parcel_runtime.py` | Smallest executable admission state machine: canonical carrier profile guard, contiguous closed-stage presence windows, pure dwell verdict over the accepted presence kernel, typed action/RX admission, exactly-once custody transfers, runtime-detected in-transit flip from real in-air evidence, carrier-local carriage pose via shared frame math, seek-consistent append-only snapshot/restore. |
| `tests/tasks/test_native_parcel_runtime.py` | 37 focused module tests (see coverage below). |
| `validation/p02_native_parcel_glm/implementation.md` | This record. |

## Reuse — nothing reimplemented

* Motion/presence validation re-runs the accepted pure kernel
  `assess_facility_presence` per sample (frame, binding, landed/not-in-air, 3D
  speed, rotated footprint clearance, contact height) with the authored policy
  tolerances. No geometry was duplicated.
* Evidence records are the existing `FacilityPadPhysicalObservation` journal
  surface (the exact records `LogisticsRuntimeHook` derives via
  `derive_physical_observations` and journals through
  `logistics.observation.ingest`); no new observation schema.
* Carriage math uses shared `frame_math` (`UnitQuaternion.compose/rotate`,
  `RotationMatrix`, `rpy_to_quaternion`). The recorded ENU-zyx attitude triple
  is reconstructed in ENU axes and re-expressed in scene axes by conjugating
  with the ENU→scene axis map (`x=east, y=up, z=-north`, proper rotation).
* Identities reuse the existing typed aliases (`LogisticsIdentifier`,
  `FacilityIdentifier`-pattern fleet ids, `Identifier`, `Sha256`); the
  proposal identities (`order.p02.single`, `parcel.p02.single`,
  `uav.p02.carrier`, `facility.p02.pickup`, `facility.p02.dropoff`) compile as
  declared.

## Contract decisions (exact, no defaults)

* **Attachment transform is required.** A contract cannot compile without the
  declared body-local offset + orientation; `relative_pose` is never defaulted
  to a carrier origin and never derived from body height
  (`test_missing_attachment_transform_cannot_compile`).
* **Calibration is signed.** `pose_reference_above_contact_m` mirrors the
  accepted kernel's `reference_height_finite` (signed below-pad-surface roots
  are representable; NaN/inf/bool/None rejected). Never clamped, never a
  half-height default.
* **Admission gates.** Pickup requires contiguous grounded closed dwell on the
  declared pickup pad reaching `minimum_pickup_dwell_s` **and** the admitted
  action's RX barrier digest equal to the dwell window's closing stage barrier.
  Delivery requires destination dwell reaching `minimum_dropoff_dwell_s` **and**
  the admitted dropoff. An inspection report, arrival label, or network receipt
  never delivers.
* **In-transit is runtime-detected, not admitted.** `loaded -> in_transit`
  fires only when a closed observation of the carrier-in-custody reports the
  explicit measured in-air attributes; it carries no transfer, changes no
  holder, and is bound to the exact observation digest that evidenced it.
* **Exactly-once custody.** `ParcelCustodyTransferRecord.mint` derives
  `transfer_id` as SHA-256 over canonical content excluding the id, so a
  duplicate admitted action returns the original decision and the identical
  record — never a second transfer. A divergent payload under the same action
  id raises (`payload conflict`). Unconfirmed/rejected decisions are retriable
  and never replay as admitted.
* **UNKNOWN vs error.** Missing/gapped/short/moving/in-air evidence → explicit
  `unconfirmed` outcome with the exact reason (`UNKNOWN_SOURCE_NOTE`). Foreign
  run/aircraft/facility/order/parcel/carrier or malformed identity →
  `NativeParcelRuntimeError` (subclass of `NativeParcelContractError` →
  `ValueError`).
* **Sealed-verifier replay requirement.** Carried canonically
  (`SEALED_VERIFIER_REPLAY_REQUIREMENT` is validated on every contract
  instance); the live slice never mints `VerifiedDwellProvenance` and
  `provenance_verified` stays `Literal[False]` on all evidence.

## Evidence classification

**Module evidence (this record).** The 37 tests run with
`/home/weizhiwei/data/iiot_predict/iiot_py311/airfogsim/bin/python -m pytest
tests/tasks/test_native_parcel_runtime.py` → **37 passed**. They use an honest
synthetic fixture built from the accepted contracts: the real package lowering
(`lower_logistics_task_package`, with the routing hub the package rules
require), canonical pad geometry from `facility_landing_pads`, real
single-sample presence assessments recomputed by the kernel, and
self-consistent canonical observation digests. No PX4/Gazebo, no executor, no
hook wiring, no run launch.

**Native execution: none.** No sealed artifact, no provider receipt, no
verifier replay exists for this slice. Per the formal execution path, only an
independent sealed verifier replaying the same command/observation/dwell/
attachment/transfer records can issue the final verdict.

**Test coverage:** wrong aircraft/run/facility binding raises; decision time
before the evidence frontier raises; missing/single-sample/gapped/short dwell
unconfirmed with exact reasons; moving (3D speed) and in-air carriers break the
presence dwell; direct non-contiguous windows never confirm; full dwell admits
pickup once with facility→carrier custody; duplicate admitted action returns
the original decision without a second transfer; payload conflict under the
same action id raises; foreign order action raises; dropoff without
destination dwell unconfirmed; short destination dwell never delivers; full
delivery requires dwell + admitted dropoff with carrier→facility custody;
dropoff before pickup rejected; second pickup after admission rejected; in-air
observation after pickup flips in-transit bound to the evidence digest;
destination dwell accrues through in-transit; carriage offset rotates with
measured yaw (vertical + horizontal cases, orientation composition);
non-typed observation rejected; snapshot replay reproduces the identical
snapshot; restore ignores at/below-frontier stages and deterministic
seek-forward replay reproduces the original outcome; foreign-run snapshot
restore raises; signed calibration representable, NaN rejected; duplicate
identities and negative policy rejected.

**Adjacent suites:** `test_logistics_dwell_eligibility.py` +
`test_logistics_physical_observations.py` + `test_logistics_facility_presence.py`
→ 90 passed (no regression from this slice).

## Remaining integration interface (narrow, deferred to review)

1. **Factory/hook wiring** — construct `NativeParcelStateMachine` per run
   inside the runtime hook (contract compiled from the resolved package +
   proposal identities; `now` from the authoritative `SimulationTime`; pad
   indexes from the declared pads). Feed it journaled
   `FacilityPadPhysicalObservation`s via `observe()` and admitted
   `AdmittedParcelAction`s via `admit_action()`; publish `snapshot()`/
   `ParcelAdmissionOutcome`s to the public trace. No hook file was edited.
2. **Derived carriage publication** — call `parcel_carriage_pose` with the
   latest closed observation and the contract attachment when the viewer needs
   the carried parcel pose (backend-derived only).
3. **Action/RX provenance** — the integration layer must supply the typed
   `AdmittedParcelAction` (including `rx_stage_barrier_digest` bound to the
   closing stage) from the real command/RX surface; this slice defines the
   shape and the binding check but not the transport.
4. **Sealed verifier config** — the verifier must replay the same records;
   `LOGISTICS_RUNTIME_IMPLEMENTED` stays `False` until that path exists.
5. **Run launch** — explicitly not performed here; native execution evidence
   remains absent by design.
