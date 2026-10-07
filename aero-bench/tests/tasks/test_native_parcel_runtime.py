"""Focused module-level tests for the native one-order parcel slice.

The slice (:mod:`aero_bench.tasks.logistics.native_parcel_contract` and
:mod:`aero_bench.tasks.logistics.native_parcel_runtime`) is a pure runtime
admission state machine: it consumes already-journaled
``FacilityPadPhysicalObservation`` records and typed admitted action/RX
evidence and drives one parcel through ``awaiting_pickup -> loaded ->
in_transit -> delivered``.  These tests are **module evidence**, not native
execution: they use an honest synthetic closed-stage observation fixture built
from the accepted package, presence-kernel and physical-observation contracts
(no PX4/Gazebo, no executor, no resolver wiring, no run launch).

Adversarial coverage: wrong owner/run/time binding, missing/gapped/short
dwell, moving/in-air pickup, missing dropoff handoff evidence, duplicate
action (no second transfer), payload conflict, carriage transform math, and
the seek-consistent append-only snapshot.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from aero_bench.runtime.contracts import SimulationTime
from aero_bench.tasks.logistics import physical_observations as po_mod
from aero_bench.tasks.logistics.contracts import (
    LOGISTICS_PACKAGE_ID,
    LOGISTICS_SCHEMA_VERSION,
    lower_logistics_task_package,
)
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.facility_presence import (
    AircraftPresenceProfile,
    MeasuredAircraftSample,
    PresenceEventBinding,
    PresenceTolerances,
    assess_facility_presence,
)
from aero_bench.tasks.logistics.native_parcel_contract import (
    ATTACHMENT_FRAME_NOTE,
    NATIVE_PARCEL_ADMISSION_SCHEMA_VERSION,
    NATIVE_PARCEL_ACTION_SCHEMA_VERSION,
    NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
    NATIVE_PARCEL_STATE_SCHEMA_VERSION,
    NATIVE_PARCEL_TRANSFER_SCHEMA_VERSION,
    SEALED_VERIFIER_REPLAY_REQUIREMENT,
    UNKNOWN_SOURCE_NOTE,
    AdmittedParcelAction,
    DeclaredParcelAttachment,
    DeclaredParcelCarrier,
    DeclaredParcelPolicy,
    NativeParcelContract,
    NativeParcelContractError,
    NativeParcelIdentities,
    ParcelActionIdentityBinding,
    ParcelCustodyHolder,
    ParcelCustodyTransferRecord,
    ParcelDwellAdmission,
)
from aero_bench.tasks.logistics.native_parcel_runtime import (
    NATIVE_PARCEL_SNAPSHOT_SCHEMA_VERSION,
    NativeParcelRuntimeError,
    NativeParcelSnapshot,
    NativeParcelStateMachine,
    SeekConsistencyError,
    assess_parcel_dwell,
    parcel_carriage_pose,
)
from aero_bench.tasks.logistics.physical_observations import (
    CLEARANCE_SCOPE_NOTE,
    FacilityPadPhysicalObservation,
    SourceFrameOrigin,
    observation_digest_value,
)
from aero_bench.world.resolved import ResolvedQuaternion

_RUN_ID = "a" * 64
_SCENARIO_DIGEST = "b" * 64
_OTHER_RUN_ID = "c" * 64
_PROVENANCE_NOTE = po_mod._OBSERVATION_PROVENANCE_NOTE
_PROVIDER_ID = "flight"
_VEHICLE_ID = "uav.p02.carrier"
_AIRCRAFT_ID = "uav.p02.carrier"
_FLEET_ENTRY_ID = "uav.p02.carrier"
_AUTHORIZED_PRINCIPAL_ID = "uav.p02.carrier"
_ASSET_ID = "model:logistics-drone-v1"
_PICKUP = "facility.p02.pickup"
_DROPOFF = "facility.p02.dropoff"
_ORDER_ID = "order.p02.single"
_PARCEL_ID = "parcel.p02.single"
_POSE_REFERENCE_M = 0.1
_TOL = PresenceTolerances(
    vertical_tolerance_m=0.15,
    horizontal_uncertainty_m=0.1,
    max_stationary_speed_m_s=0.2,
)


# ------------------------------------------------------------------ fixtures


def _vertiport(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": _PICKUP,
        "name": "pickup vertiport",
        "kind": "vertiport",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 10, "z": -20},
        "rotationDeg": 0,
        "widthM": 18,
        "depthM": 8,
        "heightM": 4,
        "landing": {"parkingSlots": 3, "movementsPerHour": 30},
        "cargo": None,
        "charging": None,
    }
    value.update(overrides)
    return value


def _hub(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "hub.p02.single",
        "name": "cargo hub",
        "kind": "hub",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 30, "z": -40},
        "rotationDeg": 0,
        "widthM": 16,
        "depthM": 12,
        "heightM": 6,
        "landing": {"parkingSlots": 2, "movementsPerHour": 20},
        "cargo": {"storageCapacityKg": 100, "throughputPerHourKg": 50},
        "charging": None,
    }
    value.update(overrides)
    return value


def _scene(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "scene_id": "world.city-demo",
        "coordinate_frame": "scene_east_south_m",
        "origin_latitude_deg": 39.916,
        "origin_longitude_deg": 116.397,
        "origin_altitude_m": 43.0,
        "scene_source_sha256": "ab" * 32,
    }
    value.update(overrides)
    return value


def _package_document() -> dict[str, object]:
    return {
        "schema_version": LOGISTICS_SCHEMA_VERSION,
        "package_id": LOGISTICS_PACKAGE_ID,
        "task_id": "logistics.native-parcel.v1",
        "verifier_id": "logistics.verifier",
        "scene": _scene(),
        "facilities": [
            _vertiport(),
            _vertiport(id=_DROPOFF, position={"x": 40, "z": -60}),
            _hub(),
        ],
        "fleet": [
            {
                "id": _FLEET_ENTRY_ID,
                "assetId": _ASSET_ID,
                "count": 1,
                "homeFacilityId": _PICKUP,
                "batteryWh": 20000,
                "reserveRatio": 0.2,
                "maxPayloadKg": 5,
            }
        ],
        "performanceProfiles": [
            {
                "fleetEntryId": _FLEET_ENTRY_ID,
                "sourceLabel": "operator estimate",
                "provenance": "selected-city v3 planning data",
                "aircraftBody": {"xM": 0.6, "yM": 0.6, "zM": 0.3},
                "cruiseSpeedMps": 15,
                "cruisePowerW": 900,
                "hoverPowerW": 700,
                "chargeEfficiency": 0.85,
            }
        ],
        "orders": [
            {
                "id": _ORDER_ID,
                "sourceFacilityId": _PICKUP,
                "destinationFacilityId": _DROPOFF,
                "hubHandoffFacilityId": "hub.p02.single",
                "cargoKg": 1,
                "releaseAtS": 0,
                "deliverByS": 600,
            }
        ],
        "noFlyZones": [],
        "actors": [{"actor_id": f"{_FLEET_ENTRY_ID}:1", "role": "aircraft_agent"}],
    }


@pytest.fixture(scope="module")
def package():
    return lower_logistics_task_package(_package_document())


def _observation(
    package,
    *,
    at: SimulationTime,
    yaw_deg: float = 0.0,
    facility_id: str = _PICKUP,
    pad_index: int = 0,
    landed: bool = True,
    in_air: bool = False,
    speed_east: float = 0.0,
    speed_up: float = 0.0,
    speed_south: float = 0.0,
    offset_x_m: float | None = None,
    run_id: str = _RUN_ID,
) -> FacilityPadPhysicalObservation:
    """One honest synthetic closed-stage physical observation on a declared pad.

    Assembled from the exact accepted contracts: the canonical pad geometry, a
    scene-frame grounded sample, the declared canonical presence profile, the
    expected event binding and the real single-sample presence assessment
    (recomputed by the presence kernel), then a self-consistent canonical
    observation digest.  ``offset_x_m`` displaces the measured centre when a
    footprint-boundary test needs it.
    """
    pad = facility_landing_pads(package.facilities.require(facility_id))[pad_index]
    profile = AircraftPresenceProfile(
        aircraft_id=_VEHICLE_ID,
        body_width_m=0.6,
        body_depth_m=0.3,
        pose_reference_above_contact_m=_POSE_REFERENCE_M,
    )
    source_event_id = f"state.{_VEHICLE_ID}.{at.tick}"
    x = pad.x if offset_x_m is None else pad.x + offset_x_m
    sample = MeasuredAircraftSample(
        sample_ref=source_event_id,
        run_id=run_id,
        provider_id=_PROVIDER_ID,
        aircraft_id=_VEHICLE_ID,
        at=at,
        frame="scene_east_south_m",
        x=x,
        y=pad.y + _POSE_REFERENCE_M,
        z=pad.z,
        body_yaw_deg=yaw_deg,
        velocity_east_m_s=speed_east,
        velocity_up_m_s=speed_up,
        velocity_south_m_s=speed_south,
        landed=landed,
        in_air=in_air,
        provenance="caller_supplied",
        evidence_ref=source_event_id,
    )
    event_binding = PresenceEventBinding(
        run_id=run_id,
        provider_id=_PROVIDER_ID,
        aircraft_id=_VEHICLE_ID,
        at=at,
        evidence_ref=source_event_id,
    )
    assessment = assess_facility_presence(
        pad=pad,
        sample=sample,
        profile=profile,
        event=event_binding,
        tolerances=_TOL,
    )
    fields = {
        "schema_version": po_mod.LOGISTICS_PHYSICAL_OBSERVATION_SCHEMA_VERSION,
        "facility_id": facility_id,
        "pad_index": pad_index,
        "aircraft_id": _AIRCRAFT_ID,
        "fleet_entry_id": _FLEET_ENTRY_ID,
        "visual_asset_id": _ASSET_ID,
        "provider_id": _PROVIDER_ID,
        "native_vehicle_id": _VEHICLE_ID,
        "run_id": run_id,
        "scenario_digest": _SCENARIO_DIGEST,
        "at": at,
        "frame": "scene_east_south_m",
        "pad": pad,
        "sample": sample,
        "profile": profile,
        "event_binding": event_binding,
        "assessment": assessment,
        "source_event_id": source_event_id,
        "source_state_sample_digest": "ab" * 32,
        "source_scene_state_digest": f"{at.tick:02d}" * 32,
        "source_stage_barrier_digest": f"{at.tick:04d}" * 16,
        "source_evidence_path": "trajectory/evidences.jsonl",
        "source_evidence_sha256": "1" * 64,
        "source_frame_origin": SourceFrameOrigin(
            latitude_deg=39.916,
            longitude_deg=116.397,
            ellipsoid_height_m=43.0,
            scene_frame="scene_east_south_m",
        ),
        "single_point_in_time": True,
        "provenance_declared": "caller_supplied",
        "provenance_verified": False,
        "provenance_note": _PROVENANCE_NOTE,
        "measured_roll_deg": 0.0,
        "measured_pitch_deg": 0.0,
        "measured_yaw_deg": yaw_deg,
        "collision_contact": False,
        "footprint_basis": "yaw_only_declared_body_footprint",
        "clearance_note": CLEARANCE_SCOPE_NOTE,
        "observation_digest": "0" * 64,
    }
    candidate = FacilityPadPhysicalObservation.model_construct(**fields)
    fields["observation_digest"] = observation_digest_value(candidate)
    return FacilityPadPhysicalObservation(**fields)


def _dwell_tick(tick: int) -> SimulationTime:
    return SimulationTime(tick=tick, sim_time_ns=tick * 1_000_000_000)


def _attachment() -> DeclaredParcelAttachment:
    return DeclaredParcelAttachment(
        schema_version=NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
        parcel_entity_id=_PARCEL_ID,
        carrier_entity_id=_AIRCRAFT_ID,
        offset_x_m=0.0,
        offset_y_m=-0.2,
        offset_z_m=0.0,
        orientation=ResolvedQuaternion(qw=1.0, qx=0.0, qy=0.0, qz=0.0),
        frame_note=ATTACHMENT_FRAME_NOTE,
    )


def _carrier() -> DeclaredParcelCarrier:
    return DeclaredParcelCarrier(
        schema_version=NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
        carrier_entity_id=_AIRCRAFT_ID,
        fleet_entry_id=_FLEET_ENTRY_ID,
        visual_asset_id=_ASSET_ID,
        provider_id=_PROVIDER_ID,
        native_vehicle_id=_VEHICLE_ID,
        pose_reference_above_contact_m=_POSE_REFERENCE_M,
    )


def _policy() -> DeclaredParcelPolicy:
    return DeclaredParcelPolicy(
        schema_version=NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
        minimum_pickup_dwell_s=2.0,
        minimum_dropoff_dwell_s=2.0,
        vertical_tolerance_m=0.15,
        horizontal_uncertainty_m=0.1,
        max_stationary_speed_m_s=0.2,
    )


def _contract() -> NativeParcelContract:
    return NativeParcelContract(
        schema_version=NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
        contract_id="native.parcel.p02",
        identities=NativeParcelIdentities(
            schema_version=NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
            task_id="logistics.native-parcel.v1",
            order_id=_ORDER_ID,
            parcel_entity_id=_PARCEL_ID,
            carrier_entity_id=_AIRCRAFT_ID,
            authorized_principal_id=_AUTHORIZED_PRINCIPAL_ID,
            pickup_facility_id=_PICKUP,
            dropoff_facility_id=_DROPOFF,
        ),
        carrier=_carrier(),
        attachment=_attachment(),
        policy=_policy(),
        sealed_verifier_replay_requirement=SEALED_VERIFIER_REPLAY_REQUIREMENT,
    )


def _machine(package, **kwargs) -> NativeParcelStateMachine:
    return NativeParcelStateMachine(
        contract=_contract(), run_id=_RUN_ID,
        now=_dwell_tick(0), **kwargs,
    )


def _action(**overrides: object) -> AdmittedParcelAction:
    barrier = overrides.get(
        "rx_stage_barrier_digest", f"{3:04d}" * 16
    )
    # The typed RX evidence is received at the exact closing stage tick the
    # barrier digest binds; an explicit received_at override wins.
    if "received_at" not in overrides:
        overrides["received_at"] = _dwell_tick(int(str(barrier)[:4]))
    value: dict[str, object] = {
        "schema_version": NATIVE_PARCEL_ACTION_SCHEMA_VERSION,
        "run_id": _RUN_ID,
        "action_id": "action.pickup.1",
        "kind": "pickup",
        "evidence_basis": "closed_stage_action_rx",
        "order_id": _ORDER_ID,
        "parcel_entity_id": _PARCEL_ID,
        "carrier_entity_id": _AIRCRAFT_ID,
        "principal_id": _AUTHORIZED_PRINCIPAL_ID,
        "rx_stage_barrier_digest": barrier,
    }
    value.update(overrides)
    return AdmittedParcelAction(**value)


# ----------------------------------------------------------------- contracts


class TestContractConstruction:
    def test_contract_builds_and_digest_is_deterministic(self):
        contract = _contract()
        assert contract.contract_digest() == contract.contract_digest()
        assert contract.identities.pickup_facility_id == _PICKUP
        assert contract.identities.dropoff_facility_id == _DROPOFF

    def test_missing_attachment_transform_cannot_compile(self):
        # relative_pose is never defaulted: the attachment is a required
        # explicit field, so constructing a contract without one raises.
        with pytest.raises(Exception):
            NativeParcelContract(
                schema_version=NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
                contract_id="native.parcel.p02",
                identities=NativeParcelIdentities(
                    schema_version=NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
                    task_id="logistics.native-parcel.v1",
                    order_id=_ORDER_ID,
                    parcel_entity_id=_PARCEL_ID,
                    carrier_entity_id=_AIRCRAFT_ID,
                    authorized_principal_id=_AUTHORIZED_PRINCIPAL_ID,
                    pickup_facility_id=_PICKUP,
                    dropoff_facility_id=_DROPOFF,
                ),
                carrier=_carrier(),
                policy=_policy(),
                sealed_verifier_replay_requirement=(
                    SEALED_VERIFIER_REPLAY_REQUIREMENT
                ),
            )

    def test_duplicate_identities_rejected(self):
        with pytest.raises(ValueError):
            NativeParcelIdentities(
                schema_version=NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
                task_id="logistics.native-parcel.v1",
                order_id=_ORDER_ID,
                parcel_entity_id=_AIRCRAFT_ID,
                carrier_entity_id=_AIRCRAFT_ID,
                authorized_principal_id=_AUTHORIZED_PRINCIPAL_ID,
                pickup_facility_id=_PICKUP,
                dropoff_facility_id=_DROPOFF,
            )

    def test_substituted_calibration_rejected(self):
        # NaN is never accepted as a declared calibration (the value is
        # signed, mirroring the accepted kernel, but must be a real finite
        # number — never a missing/undefined substitution).
        with pytest.raises(ValueError):
            DeclaredParcelCarrier(
                schema_version=NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
                carrier_entity_id=_AIRCRAFT_ID,
                fleet_entry_id=_FLEET_ENTRY_ID,
                visual_asset_id=_ASSET_ID,
                provider_id=_PROVIDER_ID,
                native_vehicle_id=_VEHICLE_ID,
                pose_reference_above_contact_m=float("nan"),
            )

    def test_signed_calibration_below_pad_surface_is_representable(self):
        # A nested Gazebo model root below the pad surface has a negative
        # calibration; the declared number stays signed, never clamped.
        carrier = DeclaredParcelCarrier(
            schema_version=NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
            carrier_entity_id=_AIRCRAFT_ID,
            fleet_entry_id=_FLEET_ENTRY_ID,
            visual_asset_id=_ASSET_ID,
            provider_id=_PROVIDER_ID,
            native_vehicle_id=_VEHICLE_ID,
            pose_reference_above_contact_m=-0.013,
        )
        assert carrier.pose_reference_above_contact_m == -0.013

    def test_negative_policy_value_rejected(self):
        with pytest.raises(ValueError):
            DeclaredParcelPolicy(
                schema_version=NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
                minimum_pickup_dwell_s=-1.0,
                minimum_dropoff_dwell_s=2.0,
                vertical_tolerance_m=0.15,
                horizontal_uncertainty_m=0.1,
                max_stationary_speed_m_s=0.2,
            )


# ------------------------------------------------------------------- runtime


class TestWrongOwnerRunTime:
    def test_wrong_aircraft_observation_raises(self, package):
        machine = _machine(package)
        foreign = _observation(
            package,
            at=_dwell_tick(1),
            facility_id=_PICKUP,
        )
        # Rebuild the record naming another aircraft: the machine must reject
        # the foreign evidence rather than reassess it.
        fields = dict(foreign.__dict__)
        fields["aircraft_id"] = "uav.other"
        fields["observation_digest"] = "0" * 64
        from aero_bench.tasks.logistics.physical_observations import (
            observation_digest_value as _odv,
        )
        candidate = FacilityPadPhysicalObservation.model_construct(**fields)
        fields["observation_digest"] = _odv(candidate)
        forged = FacilityPadPhysicalObservation(**fields)
        with pytest.raises(NativeParcelRuntimeError):
            machine.observe(forged)

    def test_wrong_run_observation_raises(self, package):
        machine = _machine(package)
        with pytest.raises(NativeParcelRuntimeError):
            machine.observe(
                _observation(package, at=_dwell_tick(1), run_id=_OTHER_RUN_ID)
            )

    def test_foreign_facility_observation_raises(self, package):
        machine = _machine(package)
        with pytest.raises(NativeParcelRuntimeError):
            machine.observe(
                _observation(
                    package, at=_dwell_tick(1), facility_id="hub.p02.single"
                )
            )

    def test_decision_time_before_frontier_raises(self, package):
        machine = _machine(package)
        for tick in (1, 2, 3):
            machine.observe(
                _observation(package, at=_dwell_tick(tick), facility_id=_PICKUP)
            )
        with pytest.raises(NativeParcelRuntimeError):
            machine.admit_action(
                _action(rx_stage_barrier_digest=f"{3:04d}" * 16),
                now=_dwell_tick(0),
            )


class TestDwellAdmission:
    def test_missing_dwell_is_unconfirmed(self, package):
        machine = _machine(package)
        outcome = machine.admit_action(_action(), now=_dwell_tick(3))
        assert outcome.status == "unconfirmed"
        assert outcome.state_after == outcome.state_before == "awaiting_pickup"
        assert UNKNOWN_SOURCE_NOTE in outcome.reason
        assert outcome.transfer_id is None

    def test_single_sample_dwell_is_unconfirmed(self, package):
        machine = _machine(package)
        machine.observe(
            _observation(package, at=_dwell_tick(1), facility_id=_PICKUP)
        )
        outcome = machine.admit_action(
            _action(rx_stage_barrier_digest=f"{1:04d}" * 16),
            now=_dwell_tick(1),
        )
        assert outcome.status == "unconfirmed"
        assert "at least two" in outcome.reason

    def test_gapped_dwell_never_pads(self, package):
        machine = _machine(package)
        machine.observe(
            _observation(package, at=_dwell_tick(1), facility_id=_PICKUP)
        )
        machine.observe(
            _observation(package, at=_dwell_tick(3), facility_id=_PICKUP)
        )
        outcome = machine.admit_action(
            _action(rx_stage_barrier_digest=f"{3:04d}" * 16),
            now=_dwell_tick(3),
        )
        assert outcome.status == "unconfirmed"
        # The rolling window reset on the gap, so it holds a single stage.
        assert "at least two distinct closed stages" in outcome.reason

    def test_direct_noncontiguous_window_is_never_confirmed(self, package):
        # The machine always resets on a gap; the pure verdict function keeps
        # an explicit contiguity guard for directly assembled windows.
        window = (
            _observation(package, at=_dwell_tick(1), facility_id=_PICKUP),
            _observation(package, at=_dwell_tick(3), facility_id=_PICKUP),
        )
        verdict = assess_parcel_dwell(
            contract=_contract(),
            run_id=_RUN_ID,
            kind="pickup",
            window=window,
            facility_id=_PICKUP,
            pad_index=0,
        )
        assert verdict.confirmed is False
        assert "not contiguous" in verdict.unconfirmed_reason

    def test_short_dwell_below_minimum_is_unconfirmed(self, package):
        machine = _machine(package)
        machine.observe(
            _observation(package, at=_dwell_tick(1), facility_id=_PICKUP)
        )
        machine.observe(
            _observation(package, at=_dwell_tick(2), facility_id=_PICKUP)
        )
        # 1.0 s elapsed < declared 2.0 s minimum.
        outcome = machine.admit_action(
            _action(rx_stage_barrier_digest=f"{2:04d}" * 16),
            now=_dwell_tick(2),
        )
        assert outcome.status == "unconfirmed"
        assert "does not reach the declared minimum" in outcome.reason

    def test_full_dwell_admits_pickup_once(self, package):
        machine = _machine(package)
        for tick in (1, 2, 3):
            machine.observe(
                _observation(package, at=_dwell_tick(tick), facility_id=_PICKUP)
            )
        outcome = machine.admit_action(
            _action(rx_stage_barrier_digest=f"{3:04d}" * 16),
            now=_dwell_tick(3),
        )
        assert outcome.status == "admitted"
        assert outcome.state_after == "loaded"
        assert machine.state == "loaded"
        assert machine.holder.holder_kind == "carrier"
        assert len(machine.parcel_state.transfers) == 1
        transfer = machine.parcel_state.transfers[0]
        assert transfer.kind == "pickup"
        assert transfer.from_holder.holder_id == _PICKUP
        assert transfer.to_holder.holder_id == _AIRCRAFT_ID

    def test_stale_dwell_window_ends_before_admission_tick_is_unconfirmed(
        self, package
    ):
        # The qualifying window closed at tick 3, but the admission is
        # decided at tick 4 (the evidence stream paused): the dwell no longer
        # ends at the current admission tick, so the evidence is stale and
        # never confirmed.
        machine = _machine(package)
        for tick in (1, 2, 3):
            machine.observe(
                _observation(package, at=_dwell_tick(tick), facility_id=_PICKUP)
            )
        outcome = machine.admit_action(
            _action(rx_stage_barrier_digest=f"{3:04d}" * 16),
            now=_dwell_tick(4),
        )
        assert outcome.status == "unconfirmed"
        assert "closed at tick 3" in outcome.reason
        assert "before the current admission tick 4" in outcome.reason
        assert machine.state == "awaiting_pickup"
        assert outcome.transfer_id is None
        # The stale decision is exactly once per key: the identical replay
        # returns the same unconfirmed outcome without a second decision.
        replay = machine.admit_action(
            _action(rx_stage_barrier_digest=f"{3:04d}" * 16),
            now=_dwell_tick(4),
        )
        assert replay.status == "unconfirmed"
        assert replay.action_digest == outcome.action_digest

    def test_window_ending_at_admission_tick_after_in_air_gap_is_unconfirmed(
        self, package
    ):
        # The window ends exactly at the admission tick, but an in-air stage
        # at tick 4 broke the grounded run: only one grounded sample remains
        # after the reset, so the dwell cannot qualify.
        machine = _machine(package)
        for tick in (1, 2, 3):
            machine.observe(
                _observation(package, at=_dwell_tick(tick), facility_id=_PICKUP)
            )
        machine.observe(
            _observation(
                package, at=_dwell_tick(4), facility_id=_PICKUP,
                landed=False, in_air=True, speed_up=0.5,
            )
        )
        machine.observe(
            _observation(package, at=_dwell_tick(5), facility_id=_PICKUP)
        )
        outcome = machine.admit_action(
            _action(
                received_at=_dwell_tick(5),
                rx_stage_barrier_digest=f"{5:04d}" * 16,
            ),
            now=_dwell_tick(5),
        )
        assert outcome.status == "unconfirmed"
        # The in-air sample cleared the prefix; the one subsequent grounded
        # sample cannot establish a contiguous dwell or a custody transfer.
        assert "a contiguous dwell requires at least two distinct closed stages" in outcome.reason
        assert machine.parcel_state.state == "awaiting_pickup"
        assert not machine.parcel_state.transfers

    def test_motion_at_admission_tick_invalidates_window(self, package):
        # A qualifying grounded prefix (ticks 1-2) followed by a moving stage
        # at the admission tick itself: the window ends at the admission tick
        # but the closed run is invalidated by motion and never confirms.
        machine = _machine(package)
        machine.observe(
            _observation(package, at=_dwell_tick(1), facility_id=_PICKUP)
        )
        machine.observe(
            _observation(package, at=_dwell_tick(2), facility_id=_PICKUP)
        )
        machine.observe(
            _observation(
                package, at=_dwell_tick(3), facility_id=_PICKUP, speed_east=0.5
            )
        )
        outcome = machine.admit_action(
            _action(rx_stage_barrier_digest=f"{3:04d}" * 16),
            now=_dwell_tick(3),
        )
        assert outcome.status == "unconfirmed"
        assert "the closed presence window is empty" in outcome.reason
        assert machine.parcel_state.state == "awaiting_pickup"
        assert not machine.parcel_state.transfers

    def test_contact_loss_at_admission_tick_invalidates_window(self, package):
        # Contact loss: the final stage at the admission tick no longer sits
        # on the pad surface, so the grounded run is broken.
        machine = _machine(package)
        machine.observe(
            _observation(package, at=_dwell_tick(1), facility_id=_PICKUP)
        )
        machine.observe(
            _observation(package, at=_dwell_tick(2), facility_id=_PICKUP)
        )
        machine.observe(
            _observation(
                package, at=_dwell_tick(3), facility_id=_PICKUP,
                landed=False, in_air=False, speed_up=0.05,
            )
        )
        outcome = machine.admit_action(
            _action(rx_stage_barrier_digest=f"{3:04d}" * 16),
            now=_dwell_tick(3),
        )
        assert outcome.status == "unconfirmed"
        assert "the closed presence window is empty" in outcome.reason
        assert machine.parcel_state.state == "awaiting_pickup"
        assert not machine.parcel_state.transfers

    def test_moving_carrier_breaks_presence_dwell(self, package):
        machine = _machine(package)
        machine.observe(
            _observation(
                package, at=_dwell_tick(1), facility_id=_PICKUP, speed_east=0.3
            )
        )
        machine.observe(
            _observation(package, at=_dwell_tick(2), facility_id=_PICKUP)
        )
        outcome = machine.admit_action(
            _action(rx_stage_barrier_digest=f"{2:04d}" * 16),
            now=_dwell_tick(2),
        )
        assert outcome.status == "unconfirmed"
        assert "a contiguous dwell requires at least two distinct closed stages" in outcome.reason
        assert machine.parcel_state.state == "awaiting_pickup"
        assert not machine.parcel_state.transfers

    def test_in_air_carrier_breaks_presence_dwell(self, package):
        machine = _machine(package)
        machine.observe(
            _observation(
                package, at=_dwell_tick(1), facility_id=_PICKUP, in_air=True,
                landed=False,
            )
        )
        machine.observe(
            _observation(package, at=_dwell_tick(2), facility_id=_PICKUP)
        )
        outcome = machine.admit_action(
            _action(rx_stage_barrier_digest=f"{2:04d}" * 16),
            now=_dwell_tick(2),
        )
        assert outcome.status == "unconfirmed"
        assert "a contiguous dwell requires at least two distinct closed stages" in outcome.reason
        assert machine.parcel_state.state == "awaiting_pickup"
        assert not machine.parcel_state.transfers

    def test_dwell_window_reset_by_gap_between_pads(self, package):
        # A pickup-pad stage, an off-pad stage (dropoff pad, resets nothing on
        # the pickup window but is a different tick), then a pickup-pad stage:
        # the pickup window sees a tick gap and cannot confirm dwell.
        machine = _machine(package)
        machine.observe(
            _observation(package, at=_dwell_tick(1), facility_id=_PICKUP)
        )
        machine.observe(
            _observation(package, at=_dwell_tick(2), facility_id=_DROPOFF)
        )
        machine.observe(
            _observation(package, at=_dwell_tick(3), facility_id=_PICKUP)
        )
        outcome = machine.admit_action(
            _action(rx_stage_barrier_digest=f"{3:04d}" * 16),
            now=_dwell_tick(3),
        )
        assert outcome.status == "unconfirmed"


class TestCustodyAndTransfers:
    def _pickup(self, package, machine) -> None:
        for tick in (1, 2, 3):
            machine.observe(
                _observation(package, at=_dwell_tick(tick), facility_id=_PICKUP)
            )
        machine.admit_action(
            _action(rx_stage_barrier_digest=f"{3:04d}" * 16),
            now=_dwell_tick(3),
        )

    def test_duplicate_admitted_action_returns_original_no_second_transfer(
        self, package
    ):
        machine = _machine(package)
        self._pickup(package, machine)
        transfers_before = len(machine.parcel_state.transfers)
        replay = machine.admit_action(
            _action(rx_stage_barrier_digest=f"{3:04d}" * 16),
            now=_dwell_tick(3),
        )
        assert replay.status == "admitted"
        assert replay.duplicate_replay is False
        assert (
            len(machine.parcel_state.transfers) == transfers_before
        ), "a duplicate admitted action must never mint a second transfer"

    def test_payload_conflict_under_same_action_id_raises(self, package):
        machine = _machine(package)
        self._pickup(package, machine)
        original = _action(rx_stage_barrier_digest=f"{3:04d}" * 16)
        binding = ParcelActionIdentityBinding(
            schema_version=NATIVE_PARCEL_ACTION_SCHEMA_VERSION,
            run_id=_RUN_ID,
            action_id=original.action_id,
            kind="dropoff",  # diverges from the admitted pickup payload
            order_id=_ORDER_ID,
            parcel_entity_id=_PARCEL_ID,
            carrier_entity_id=_AIRCRAFT_ID,
            principal_id=original.principal_id,
        )
        with pytest.raises(NativeParcelRuntimeError):
            machine.admit_action(original, payload_binding=binding, now=_dwell_tick(3))
        # The same-key ledger rejects the conflict even without any
        # payload_binding from the transport layer.
        divergent = _action(rx_stage_barrier_digest=f"{2:04d}" * 16)
        with pytest.raises(NativeParcelRuntimeError):
            machine.admit_action(divergent, now=_dwell_tick(3))

    def test_foreign_principal_cannot_authorize_carrier_action(
        self, package
    ):
        # The declared contract binds exactly one authorized principal to the
        # declared carrier.  Any other principal is foreign: it is rejected
        # with an explicit error, never inferred, aliased or accepted, and it
        # never reaches a state decision.
        machine = _machine(package)
        self._pickup(package, machine)
        with pytest.raises(NativeParcelRuntimeError):
            machine.admit_action(
                _action(
                    principal_id="uav.p02.other",
                    rx_stage_barrier_digest=f"{3:04d}" * 16,
                ),
                now=_dwell_tick(3),
            )
        assert machine.state == "loaded"
        assert len(machine.parcel_state.transfers) == 1

    def test_wrong_run_action_raises(self, package):
        machine = _machine(package)
        for tick in (1, 2, 3):
            machine.observe(
                _observation(package, at=_dwell_tick(tick), facility_id=_PICKUP)
            )
        with pytest.raises(NativeParcelRuntimeError):
            machine.admit_action(
                _action(
                    run_id=_OTHER_RUN_ID,
                    rx_stage_barrier_digest=f"{3:04d}" * 16,
                ),
                now=_dwell_tick(3),
            )

    def test_foreign_order_action_raises(self, package):
        machine = _machine(package)
        with pytest.raises(NativeParcelRuntimeError):
            machine.admit_action(
                _action(order_id="order.other", rx_stage_barrier_digest=f"{3:04d}" * 16),
                now=_dwell_tick(3),
            )

    def test_dropoff_without_destination_dwell_is_unconfirmed(self, package):
        machine = _machine(package)
        self._pickup(package, machine)
        outcome = machine.admit_action(
            _action(
                kind="dropoff",
                action_id="action.dropoff.1",
                received_at=_dwell_tick(4),
                rx_stage_barrier_digest=f"{4:04d}" * 16,
            ),
            now=_dwell_tick(4),
        )
        assert outcome.status == "unconfirmed"
        assert machine.state == "loaded"
        assert outcome.transfer_id is None

    def test_missing_handoff_evidence_never_delivers(self, package):
        machine = _machine(package)
        self._pickup(package, machine)
        # Only two destination stages: 1.0 s < 2.0 s minimum dropoff dwell.
        machine.observe(
            _observation(package, at=_dwell_tick(4), facility_id=_DROPOFF)
        )
        machine.observe(
            _observation(package, at=_dwell_tick(5), facility_id=_DROPOFF)
        )
        outcome = machine.admit_action(
            _action(
                kind="dropoff",
                action_id="action.dropoff.1",
                received_at=_dwell_tick(5),
                rx_stage_barrier_digest=f"{5:04d}" * 16,
            ),
            now=_dwell_tick(5),
        )
        assert outcome.status == "unconfirmed"
        assert machine.state == "loaded"

    def test_full_delivery_requires_dwell_and_admitted_dropoff(self, package):
        machine = _machine(package)
        self._pickup(package, machine)
        for tick in (4, 5, 6):
            machine.observe(
                _observation(package, at=_dwell_tick(tick), facility_id=_DROPOFF)
            )
        outcome = machine.admit_action(
            _action(
                kind="dropoff",
                action_id="action.dropoff.1",
                received_at=_dwell_tick(6),
                rx_stage_barrier_digest=f"{6:04d}" * 16,
            ),
            now=_dwell_tick(6),
        )
        assert outcome.status == "admitted"
        assert machine.state == "delivered"
        assert machine.holder.holder_kind == "dropoff_facility"
        assert machine.holder.holder_id == _DROPOFF
        kinds = [transfer.kind for transfer in machine.parcel_state.transfers]
        assert kinds == ["pickup", "dropoff"]

    def test_dropoff_before_pickup_is_rejected(self, package):
        machine = _machine(package)
        outcome = machine.admit_action(
            _action(
                kind="dropoff",
                action_id="action.dropoff.early",
                rx_stage_barrier_digest=f"{3:04d}" * 16,
            ),
            now=_dwell_tick(3),
        )
        assert outcome.status == "rejected"
        assert machine.state == "awaiting_pickup"

    def test_second_pickup_after_admission_is_rejected(self, package):
        machine = _machine(package)
        self._pickup(package, machine)
        outcome = machine.admit_action(
            _action(action_id="action.pickup.2", rx_stage_barrier_digest=f"{3:04d}" * 16),
            now=_dwell_tick(3),
        )
        assert outcome.status == "rejected"
        assert machine.state == "loaded"


class TestInTransitDetection:
    def _pickup(self, package, machine) -> None:
        for tick in (1, 2, 3):
            machine.observe(
                _observation(package, at=_dwell_tick(tick), facility_id=_PICKUP)
            )
        machine.admit_action(
            _action(rx_stage_barrier_digest=f"{3:04d}" * 16),
            now=_dwell_tick(3),
        )

    def test_in_air_observation_after_pickup_flips_in_transit(self, package):
        machine = _machine(package)
        self._pickup(package, machine)
        departure = _observation(
            package, at=_dwell_tick(4), facility_id=_PICKUP,
            landed=False, in_air=True, speed_up=0.5,
        )
        machine.observe(departure)
        assert machine.state == "in_transit"
        assert machine.holder.holder_kind == "carrier"
        transition = machine.parcel_state.transitions[-1]
        assert transition.to_state == "in_transit"
        assert transition.action_digest == departure.observation_digest

    def test_grounded_dropoff_stages_keep_dwell_through_in_transit(self, package):
        machine = _machine(package)
        self._pickup(package, machine)
        machine.observe(
            _observation(
                package, at=_dwell_tick(4), facility_id=_PICKUP,
                landed=False, in_air=True, speed_up=0.5,
            )
        )
        assert machine.state == "in_transit"
        for tick in (5, 6, 7):
            machine.observe(
                _observation(package, at=_dwell_tick(tick), facility_id=_DROPOFF)
            )
        outcome = machine.admit_action(
            _action(
                kind="dropoff",
                action_id="action.dropoff.1",
                received_at=_dwell_tick(7),
                rx_stage_barrier_digest=f"{7:04d}" * 16,
            ),
            now=_dwell_tick(7),
        )
        assert outcome.status == "admitted"
        assert machine.state == "delivered"


class TestCarriageTransform:
    def test_offset_rotates_with_measured_yaw(self, package):
        # yaw 90 deg about scene +y (east-positive) maps body +x (forward) to
        # scene -z (north): the offset (0, -0.2, 0) body -> scene (0, 0, 0.2).
        obs = _observation(package, at=_dwell_tick(1), yaw_deg=90.0)
        pose = parcel_carriage_pose(attachment=_attachment(), observation=obs)
        assert pose.position_x_m == pytest.approx(obs.sample.x)
        assert pose.position_y_m == pytest.approx(obs.sample.y - 0.2)
        assert pose.position_z_m == pytest.approx(obs.sample.z + 0.0, abs=1e-9)
        # orientation: yaw 90 about scene +y
        half = math.radians(45.0)
        assert pose.orientation_w == pytest.approx(math.cos(half), abs=1e-9)
        assert pose.orientation_y == pytest.approx(math.sin(half), abs=1e-9)
        assert pose.orientation_x == pytest.approx(0.0, abs=1e-12)
        assert pose.orientation_z == pytest.approx(0.0, abs=1e-12)
        assert pose.at == obs.at
        assert pose.frame_note == ATTACHMENT_FRAME_NOTE

    def test_zero_yaw_identity_attitude_keeps_offset_axes(self, package):
        obs = _observation(package, at=_dwell_tick(1), yaw_deg=0.0)
        pose = parcel_carriage_pose(attachment=_attachment(), observation=obs)
        assert pose.position_x_m == pytest.approx(obs.sample.x)
        assert pose.position_y_m == pytest.approx(obs.sample.y - 0.2)
        assert pose.position_z_m == pytest.approx(obs.sample.z)
        assert pose.orientation_w == pytest.approx(1.0)

    def test_horizontal_offset_follows_measured_yaw(self, package):
        # A body-forward offset at yaw 90 deg (east-positive about scene +y)
        # points scene -z (north): body +x maps to scene -z, never flattened.
        attachment = DeclaredParcelAttachment(
            schema_version=NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
            parcel_entity_id=_PARCEL_ID,
            carrier_entity_id=_AIRCRAFT_ID,
            offset_x_m=1.0,
            offset_y_m=0.0,
            offset_z_m=0.0,
            orientation=ResolvedQuaternion(qw=1.0, qx=0.0, qy=0.0, qz=0.0),
            frame_note=ATTACHMENT_FRAME_NOTE,
        )
        obs = _observation(package, at=_dwell_tick(1), yaw_deg=90.0)
        pose = parcel_carriage_pose(attachment=attachment, observation=obs)
        assert pose.position_x_m == pytest.approx(obs.sample.x, abs=1e-9)
        assert pose.position_y_m == pytest.approx(obs.sample.y, abs=1e-9)
        assert pose.position_z_m == pytest.approx(obs.sample.z - 1.0, abs=1e-9)

    def test_carriage_requires_typed_observation(self):
        with pytest.raises(NativeParcelRuntimeError):
            parcel_carriage_pose(
                attachment=_attachment(),
                observation=SimpleNamespace(sample=SimpleNamespace(x=0, y=0, z=0)),
            )


class TestSnapshotSeekConsistency:
    def _run_to_loaded(self, package) -> NativeParcelStateMachine:
        machine = _machine(package)
        for tick in (1, 2, 3):
            machine.observe(
                _observation(package, at=_dwell_tick(tick), facility_id=_PICKUP)
            )
        machine.admit_action(
            _action(rx_stage_barrier_digest=f"{3:04d}" * 16),
            now=_dwell_tick(3),
        )
        return machine

    def test_snapshot_replay_reproduces_identical_state(self, package):
        machine = self._run_to_loaded(package)
        snap = machine.snapshot()
        fresh = _machine(package)
        for tick in (1, 2, 3):
            fresh.observe(
                _observation(package, at=_dwell_tick(tick), facility_id=_PICKUP)
            )
        fresh.admit_action(
            _action(rx_stage_barrier_digest=f"{3:04d}" * 16),
            now=_dwell_tick(3),
        )
        fresh_snap = fresh.snapshot()
        assert fresh_snap == snap

    def test_restore_ignores_replayed_older_stages(self, package):
        machine = self._run_to_loaded(package)
        snap = machine.snapshot()
        machine.observe(
            _observation(package, at=_dwell_tick(4), facility_id=_PICKUP,
                         landed=False, in_air=True, speed_up=0.5)
        )
        assert machine.state == "in_transit"
        machine.restore(snap)
        assert machine.state == "loaded"
        # A stage at or before the restored frontier is ignored (not re-bound).
        assert (
            machine.observe(
                _observation(package, at=_dwell_tick(3), facility_id=_PICKUP)
            )
            is False
        )
        assert machine.state == "loaded"
        # Deterministic sealed replay: re-delivering the exact stage the
        # original stream delivered next reproduces the original outcome.
        assert (
            machine.observe(
                _observation(package, at=_dwell_tick(4), facility_id=_PICKUP,
                             landed=False, in_air=True, speed_up=0.5)
            )
            is True
        )
        assert machine.state == "in_transit"

    def test_check_seek_forward_rejects_skipped_stage(self, package):
        machine = self._run_to_loaded(package)
        snap = machine.snapshot()
        machine.restore(snap)
        with pytest.raises(SeekConsistencyError):
            machine.check_seek_forward(
                _observation(package, at=_dwell_tick(5), facility_id=_PICKUP,
                             landed=False, in_air=True, speed_up=0.5)
            )

    def test_foreign_snapshot_restore_raises(self, package):
        machine = self._run_to_loaded(package)
        snap = machine.snapshot()
        other = _machine(package)
        other.observe(
            _observation(package, at=_dwell_tick(1), facility_id=_PICKUP)
        )
        # A snapshot from a different machine of the same contract/run is the
        # same declared state; a different run is foreign and must raise.
        foreign = NativeParcelSnapshot(
            schema_version=NATIVE_PARCEL_SNAPSHOT_SCHEMA_VERSION,
            contract_id=snap.contract_id,
            contract_digest=snap.contract_digest,
            run_id=_OTHER_RUN_ID,
            sequence=snap.sequence,
            parcel_state=snap.parcel_state,
            past_keys=snap.past_keys,
            stream_digest=snap.stream_digest,
            current_time=snap.current_time,
            last_observation_at=snap.last_observation_at,
            facility_frontiers=snap.facility_frontiers,
            pickup_window_samples=snap.pickup_window_samples,
            pickup_window_last_at=snap.pickup_window_last_at,
            dropoff_window_samples=snap.dropoff_window_samples,
            dropoff_window_last_at=snap.dropoff_window_last_at,
            decisions=snap.decisions,
            decision_outcomes=snap.decision_outcomes,
            last_decision_at=snap.last_decision_at,
        )
        with pytest.raises(NativeParcelRuntimeError):
            other.restore(foreign)

    def test_restore_preserves_unconfirmed_tombstone_original_retry(
        self, package
    ):
        # A keyed unconfirmed decision survives the JSON roundtrip with its
        # original outcome: the exact same content replays the recorded
        # unconfirmed outcome (never re-decided, never physically
        # re-evaluated), and a different payload under the same key still
        # raises the explicit conflict.
        machine = _machine(package)
        machine.observe(
            _observation(package, at=_dwell_tick(1), facility_id=_PICKUP)
        )
        action = _action(
            received_at=_dwell_tick(1), rx_stage_barrier_digest=f"{1:04d}" * 16
        )
        original = machine.admit_action(action, now=_dwell_tick(1))
        assert original.status == "unconfirmed"
        restored = _machine(package)
        restored.restore(
            NativeParcelSnapshot.model_validate_json(
                machine.snapshot().model_dump_json()
            )
        )
        replayed = restored.admit_action(action, now=_dwell_tick(1))
        assert replayed == original
        assert replayed.status == "unconfirmed"
        with pytest.raises(NativeParcelRuntimeError):
            restored.admit_action(
                _action(
                    kind="dropoff",
                    received_at=_dwell_tick(1),
                    rx_stage_barrier_digest=f"{1:04d}" * 16,
                ),
                now=_dwell_tick(1),
            )

    def test_restore_preserves_two_pad_frontiers(self, package):
        # The per-facility frontiers survive the roundtrip: after restore,
        # the first sample from the second declared pad at a brand-new tick
        # is absorbed, and a duplicate from that same pad at the *same* tick
        # is still refused — the two-pad same-tick semantics are exact.
        machine = _machine(package)
        machine.observe(
            _observation(package, at=_dwell_tick(1), facility_id=_PICKUP)
        )
        machine.observe(
            _observation(package, at=_dwell_tick(1), facility_id=_DROPOFF)
        )
        restored = _machine(package)
        restored.restore(
            NativeParcelSnapshot.model_validate_json(
                machine.snapshot().model_dump_json()
            )
        )
        assert (
            restored.observe(
                _observation(package, at=_dwell_tick(2), facility_id=_DROPOFF)
            )
            is True
        )
        assert (
            restored.observe(
                _observation(package, at=_dwell_tick(2), facility_id=_DROPOFF)
            )
            is False
        )


class TestIntegrationClockBoundaries:
    """The declared clock is ONE monotone relation, never a tuple ordering.

    Simulation time must increase with tick; a same-tick re-stamp (changed
    sim_time_ns) is never a new stage; cross-facility sharing is the exact
    SimulationTime only.  Regression basis: the native boundary review's
    lexicographic-tuple defects.
    """

    def test_received_at_same_tick_later_ns_is_rejected(self, package):
        # Same tick as the decision frontier with a LATER sim_time_ns: the
        # reception clock is still future evidence and is rejected, with the
        # machine state exactly unchanged.
        machine = _machine(package)
        for tick in (1, 2, 3):
            machine.observe(
                _observation(package, at=_dwell_tick(tick), facility_id=_PICKUP)
            )
        before = machine.snapshot()
        with pytest.raises(NativeParcelRuntimeError):
            machine.admit_action(
                _action(
                    received_at=SimulationTime(
                        tick=3, sim_time_ns=4_000_000_000
                    )
                ),
                now=_dwell_tick(3),
            )
        assert machine.snapshot() == before

    def test_received_at_earlier_tick_later_ns_is_rejected(self, package):
        # The exact lexicographic defect: (tick=2, 100 s) sorted AFTER
        # (tick=3, 3 s) by tuple comparison, though its clock is earlier.
        machine = _machine(package)
        for tick in (1, 2, 3):
            machine.observe(
                _observation(package, at=_dwell_tick(tick), facility_id=_PICKUP)
            )
        with pytest.raises(NativeParcelRuntimeError):
            machine.admit_action(
                _action(received_at=SimulationTime(tick=2, sim_time_ns=100_000_000_000)),
                now=SimulationTime(tick=3, sim_time_ns=3_000_000_000),
            )

    def test_now_clock_inconsistent_with_frontier_is_rejected(self, package):
        machine = _machine(package)
        for tick in (1, 2, 3):
            machine.observe(
                _observation(package, at=_dwell_tick(tick), facility_id=_PICKUP)
            )
        with pytest.raises(NativeParcelRuntimeError):
            machine.admit_action(
                _action(),
                now=SimulationTime(tick=4, sim_time_ns=2_000_000_000),
            )

    def test_same_tick_changed_ns_even_for_second_facility_raises(self, package):
        machine = _machine(package)
        machine.observe(
            _observation(package, at=_dwell_tick(1), facility_id=_PICKUP)
        )
        with pytest.raises(NativeParcelRuntimeError):
            machine.observe(
                _observation(
                    package,
                    at=SimulationTime(tick=1, sim_time_ns=100_000_000_000),
                    facility_id=_DROPOFF,
                )
            )

    def test_same_facility_same_tick_cannot_increment(self, package):
        machine = _machine(package)
        assert (
            machine.observe(
                _observation(package, at=_dwell_tick(1), facility_id=_PICKUP)
            )
            is True
        )
        assert machine.sequence == 1
        # Same facility, same tick, exact time: duplicate, never re-bound.
        assert (
            machine.observe(
                _observation(package, at=_dwell_tick(1), facility_id=_PICKUP)
            )
            is False
        )
        # Same facility, same tick, changed ns: re-stamped stage, raises.
        with pytest.raises(NativeParcelRuntimeError):
            machine.observe(
                _observation(
                    package,
                    at=SimulationTime(tick=1, sim_time_ns=500_000_000),
                    facility_id=_PICKUP,
                )
            )
        assert machine.sequence == 1

    def test_second_facility_shares_exact_time_only(self, package):
        machine = _machine(package)
        machine.observe(
            _observation(package, at=_dwell_tick(1), facility_id=_PICKUP)
        )
        assert (
            machine.observe(
                _observation(package, at=_dwell_tick(1), facility_id=_DROPOFF)
            )
            is True
        )
        # The second facility at the same tick with a different ns: raised.
        with pytest.raises(NativeParcelRuntimeError):
            machine.observe(
                _observation(
                    package,
                    at=SimulationTime(tick=1, sim_time_ns=2_000_000_000),
                    facility_id=_DROPOFF,
                )
            )

    def test_increasing_ticks_require_increasing_simtime(self, package):
        machine = _machine(package)
        machine.observe(
            _observation(package, at=_dwell_tick(1), facility_id=_PICKUP)
        )
        # A greater tick with a smaller sim_time_ns: inconsistent clock.
        with pytest.raises(NativeParcelRuntimeError):
            machine.observe(
                _observation(
                    package,
                    at=SimulationTime(tick=2, sim_time_ns=500_000_000),
                    facility_id=_PICKUP,
                )
            )
        # No tick duration is assumed: a greater tick with a GREATER ns is
        # a valid next stage even if the ns jump is not a whole second.
        assert (
            machine.observe(
                _observation(
                    package,
                    at=SimulationTime(tick=2, sim_time_ns=2_500_000_000),
                    facility_id=_PICKUP,
                )
            )
            is True
        )


class TestSeekForwardExactShapes:
    def _restored_at_tick3(self, package) -> NativeParcelStateMachine:
        machine = _machine(package)
        for tick in (1, 2, 3):
            machine.observe(
                _observation(package, at=_dwell_tick(tick), facility_id=_PICKUP)
            )
        machine.admit_action(
            _action(rx_stage_barrier_digest=f"{3:04d}" * 16),
            now=_dwell_tick(3),
        )
        machine.restore(
            NativeParcelSnapshot.model_validate_json(
                machine.snapshot().model_dump_json()
            )
        )
        return machine

    def test_exact_frontier_time_permitted_for_unseen_facility(self, package):
        machine = self._restored_at_tick3(package)
        machine.check_seek_forward(
            _observation(package, at=_dwell_tick(3), facility_id=_DROPOFF)
        )

    def test_exact_frontier_time_rejected_for_same_pad(self, package):
        machine = self._restored_at_tick3(package)
        with pytest.raises(SeekConsistencyError):
            machine.check_seek_forward(
                _observation(package, at=_dwell_tick(3), facility_id=_PICKUP)
            )

    def test_mismatched_same_tick_ns_is_rejected(self, package):
        machine = self._restored_at_tick3(package)
        with pytest.raises(SeekConsistencyError):
            machine.check_seek_forward(
                _observation(
                    package,
                    at=SimulationTime(tick=3, sim_time_ns=100_000_000_000),
                    facility_id=_DROPOFF,
                )
            )

    def test_next_tick_increasing_ns_is_permitted(self, package):
        machine = self._restored_at_tick3(package)
        machine.check_seek_forward(
            _observation(package, at=_dwell_tick(4), facility_id=_PICKUP)
        )

    def test_jump_and_backward_are_rejected(self, package):
        machine = self._restored_at_tick3(package)
        with pytest.raises(SeekConsistencyError):
            machine.check_seek_forward(
                _observation(package, at=_dwell_tick(7), facility_id=_PICKUP)
            )
        with pytest.raises(SeekConsistencyError):
            machine.check_seek_forward(
                _observation(package, at=_dwell_tick(2), facility_id=_PICKUP)
            )

    def test_restore_after_only_one_pad_then_second_pad_same_time(self, package):
        # Seek backward to a frontier absorbed by ONLY the pickup pad: the
        # second pad's record of that exact frontier time is still permitted
        # by the seek check, and observing it binds the second facility at
        # the shared closed stage without advancing the global frontier.
        machine = _machine(package)
        machine.observe(
            _observation(package, at=_dwell_tick(1), facility_id=_PICKUP)
        )
        machine.restore(
            NativeParcelSnapshot.model_validate_json(
                machine.snapshot().model_dump_json()
            )
        )
        machine.check_seek_forward(
            _observation(package, at=_dwell_tick(1), facility_id=_DROPOFF)
        )
        assert (
            machine.observe(
                _observation(package, at=_dwell_tick(1), facility_id=_DROPOFF)
            )
            is True
        )
        assert machine._last_observation_at == _dwell_tick(1)
