from __future__ import annotations

import math

from aero_bench.config.models import Sha256
from aero_bench.runtime.contracts import (
    EnuLinearVelocity,
    NedLinearVelocity,
    SCENE_STATE_ROOT_DIGEST,
    SceneContribution,
    SceneState,
    SimulationTime,
    StageBarrier,
    StateSample,
    scene_state_digest_value,
    state_sample_digest_value,
)
from aero_bench.world import frame_math
from aero_bench.world.resolved import (
    ResolvedCoordinate,
    ResolvedEntity,
    ResolvedFrameAuthority,
    ResolvedPose,
    ResolvedQuaternion,
    ResolvedScenario,
)


_POSITION_TOLERANCE_M = 1e-5
_WGS84_ECEF_TOLERANCE_M = 1e-3
_SCALAR_TOLERANCE_M = 1e-6
_ROTATION_TOLERANCE = 2e-9


def _close(
    left: float,
    right: float,
    *,
    absolute: float = 1e-9,
    relative: float = 1e-12,
) -> bool:
    return math.isclose(left, right, abs_tol=absolute, rel_tol=relative)


def _rotation_close(
    left: frame_math.RotationMatrix,
    right: frame_math.RotationMatrix,
    *,
    tolerance: float,
) -> bool:
    return all(
        abs(left.rows[row][column] - right.rows[row][column]) <= tolerance
        for row in range(3)
        for column in range(3)
    )


def _math_quaternion(value: ResolvedQuaternion) -> frame_math.UnitQuaternion:
    return frame_math.UnitQuaternion(
        w=value.qw,
        x=value.qx,
        y=value.qy,
        z=value.qz,
    )


class SceneStateAssembler:
    def __init__(self, scenario: ResolvedScenario):
        self._scenario = scenario
        self._enu_transform = self._validate_frame_authority(
            scenario.frame_authority
        )
        self._validate_coordinate_against_frame_authority(
            scenario.frame_authority.origin,
            label="resolved frame origin",
        )
        self._entity_by_id = {
            entity.entity_id: entity for entity in scenario.entities
        }
        for entity in scenario.entities:
            self._validate_pose_against_frame_authority(
                entity.initial_pose,
                label=f"resolved entity {entity.entity_id!r} initial pose",
            )
        self._declared_entity_ids = tuple(entity.entity_id for entity in scenario.entities)
        self._dynamic_entity_ids = frozenset(
            entity.entity_id for entity in scenario.entities if entity.state == "dynamic"
        )
        self._motion_provider_ids = tuple(
            sorted(
                {
                    entity.owner_id
                    for entity in scenario.entities
                    if entity.state == "dynamic"
                }
            )
        )
        # SceneState and every nested runtime contract inherit StrictModel, whose
        # frozen configuration makes an assembled state immutable.  Keep only the
        # last state produced by this assembler so the normal harness path can
        # validate its predecessor by identity instead of serializing it again.
        # Independently supplied state objects still take the strict round-trip
        # validation in _validate_previous below.
        self._last_scene_state: SceneState | None = None

    @staticmethod
    def _validate_frame_authority(
        authority: ResolvedFrameAuthority,
    ) -> frame_math.EnuTransform:
        origin = authority.origin
        if not all(
            _close(component, 0.0)
            for component in (
                origin.enu.east_m,
                origin.enu.north_m,
                origin.enu.up_m,
                origin.ned.north_m,
                origin.ned.east_m,
                origin.ned.down_m,
            )
        ):
            raise ValueError("resolved frame origin must be local ENU/NED zero")
        try:
            transform = frame_math.EnuTransform.from_origin(
                longitude_deg=origin.wgs84.longitude_deg,
                latitude_deg=origin.wgs84.latitude_deg,
                altitude_m=origin.wgs84.ellipsoid_height_m,
            )
            declared_ecef_to_enu = frame_math.RotationMatrix(
                authority.ecef_to_enu_rotation.rows
            )
            declared_enu_to_ecef = frame_math.RotationMatrix(
                authority.enu_to_ecef_rotation.rows
            )
            declared_enu_to_ned = frame_math.RotationMatrix(
                authority.enu_to_ned_rotation.rows
            )
            declared_ned_to_enu = frame_math.RotationMatrix(
                authority.ned_to_enu_rotation.rows
            )
        except frame_math.FrameMathError as error:
            raise ValueError("resolved frame authority is invalid") from error
        declared_origin_ecef = (
            authority.origin_ecef.x_m,
            authority.origin_ecef.y_m,
            authority.origin_ecef.z_m,
        )
        coordinate_origin_ecef = (
            origin.ecef.x_m,
            origin.ecef.y_m,
            origin.ecef.z_m,
        )
        canonical_origin_ecef = (
            transform.origin_ecef.x,
            transform.origin_ecef.y,
            transform.origin_ecef.z,
        )
        if any(
            not _close(actual, expected, absolute=_POSITION_TOLERANCE_M)
            for actual, expected in zip(
                declared_origin_ecef,
                canonical_origin_ecef,
                strict=True,
            )
        ) or any(
            not _close(actual, expected, absolute=_POSITION_TOLERANCE_M)
            for actual, expected in zip(
                coordinate_origin_ecef,
                canonical_origin_ecef,
                strict=True,
            )
        ):
            raise ValueError("resolved frame origin ECEF authority is inconsistent")
        rotations = (
            (declared_ecef_to_enu, transform.ecef_to_enu),
            (declared_enu_to_ecef, transform.enu_to_ecef),
            (declared_enu_to_ned, frame_math.ENU_TO_NED_ROTATION),
            (declared_ned_to_enu, frame_math.NED_TO_ENU_ROTATION),
        )
        if any(
            not _rotation_close(left, right, tolerance=_ROTATION_TOLERANCE)
            for left, right in rotations
        ):
            raise ValueError("resolved frame rotation authority is inconsistent")
        return transform

    def _validate_coordinate_against_frame_authority(
        self,
        coordinate: ResolvedCoordinate,
        *,
        label: str,
    ) -> None:
        enu = coordinate.enu
        ned = coordinate.ned
        if not (
            _close(ned.north_m, enu.north_m)
            and _close(ned.east_m, enu.east_m)
            and _close(ned.down_m, -enu.up_m)
        ):
            raise ValueError(f"{label} ENU and NED coordinates disagree")
        bounds = self._scenario.frame_authority.spatial_extent
        if not (
            bounds.min_east_m <= enu.east_m <= bounds.max_east_m
            and bounds.min_north_m <= enu.north_m <= bounds.max_north_m
            and bounds.min_up_m <= enu.up_m <= bounds.max_up_m
        ):
            raise ValueError(f"{label} lies outside the resolved spatial extent")
        canonical_enu = frame_math.Vector3(
            x=enu.east_m,
            y=enu.north_m,
            z=enu.up_m,
        )
        expected_ecef = self._enu_transform.ecef_from_enu(canonical_enu)
        actual_ecef = coordinate.ecef
        if any(
            not _close(actual, expected, absolute=_POSITION_TOLERANCE_M)
            for actual, expected in (
                (actual_ecef.x_m, expected_ecef.x),
                (actual_ecef.y_m, expected_ecef.y),
                (actual_ecef.z_m, expected_ecef.z),
            )
        ):
            raise ValueError(f"{label} does not use the resolved ENU frame")
        wgs84 = coordinate.wgs84
        try:
            expected_from_wgs84 = frame_math.geodetic_to_ecef(
                longitude_deg=wgs84.longitude_deg,
                latitude_deg=wgs84.latitude_deg,
                altitude_m=wgs84.ellipsoid_height_m,
            )
        except frame_math.FrameMathError as error:
            raise ValueError(f"{label} WGS84 coordinate is invalid") from error
        if any(
            not _close(actual, expected, absolute=_WGS84_ECEF_TOLERANCE_M)
            for actual, expected in (
                (actual_ecef.x_m, expected_from_wgs84.x),
                (actual_ecef.y_m, expected_from_wgs84.y),
                (actual_ecef.z_m, expected_from_wgs84.z),
            )
        ):
            raise ValueError(f"{label} WGS84 and ECEF coordinates disagree")
        if not _close(
            coordinate.amsl_m,
            wgs84.ellipsoid_height_m - coordinate.geoid_separation_m,
            absolute=_SCALAR_TOLERANCE_M,
        ):
            raise ValueError(f"{label} AMSL relationship is inconsistent")
        if not _close(
            coordinate.agl_m,
            coordinate.amsl_m - coordinate.terrain_amsl_m,
            absolute=_SCALAR_TOLERANCE_M,
        ):
            raise ValueError(f"{label} AGL relationship is inconsistent")

    def _validate_pose_against_frame_authority(
        self,
        pose: ResolvedPose,
        *,
        label: str,
    ) -> None:
        self._validate_coordinate_against_frame_authority(
            pose.position,
            label=f"{label} position",
        )
        try:
            orientation_enu = _math_quaternion(pose.orientation_enu)
            orientation_ned = _math_quaternion(pose.orientation_ned)
            expected_ned = frame_math.ENU_TO_NED_ROTATION.compose(
                orientation_enu.to_rotation_matrix()
            )
        except frame_math.FrameMathError as error:
            raise ValueError(f"{label} orientation is invalid") from error
        if not _rotation_close(
            expected_ned,
            orientation_ned.to_rotation_matrix(),
            tolerance=_ROTATION_TOLERANCE,
        ):
            raise ValueError(f"{label} ENU and NED orientations disagree")

    def assemble(
        self,
        *,
        run_id: Sha256,
        at: SimulationTime,
        barrier: StageBarrier,
        contributions: tuple[SceneContribution, ...],
        previous_scene_state: SceneState | None = None,
    ) -> SceneState:
        previous_scene_state = self._validate_previous(
            run_id=run_id,
            at=at,
            previous_scene_state=previous_scene_state,
        )
        self._validate_barrier(run_id=run_id, at=at, barrier=barrier)
        dynamic_samples = self._validate_contributions(
            run_id=run_id,
            at=at,
            barrier=barrier,
            contributions=contributions,
        )
        samples = tuple(
            self._static_sample(run_id=run_id, at=at, entity=entity)
            if entity.state == "static"
            else dynamic_samples[entity.entity_id]
            for entity in self._scenario.entities
        )
        fields = {
            "schema_version": "aero-bench.scene-state/v1",
            "run_id": run_id,
            "scenario_digest": self._scenario.scenario_digest,
            "at": at,
            "declared_entity_ids": self._declared_entity_ids,
            "samples": samples,
            "stage_barrier": barrier,
            "contribution_digests": tuple(
                sorted(
                    contribution.contribution_digest
                    for contribution in contributions
                )
            ),
            "previous_scene_state_digest": (
                SCENE_STATE_ROOT_DIGEST
                if previous_scene_state is None
                else previous_scene_state.scene_state_digest
            ),
        }
        candidate = SceneState.model_construct(
            **fields,
            scene_state_digest="0" * 64,
        )
        scene_state = SceneState(
            **fields,
            scene_state_digest=scene_state_digest_value(candidate),
        )
        self._last_scene_state = scene_state
        return scene_state

    def _validate_previous(
        self,
        *,
        run_id: Sha256,
        at: SimulationTime,
        previous_scene_state: SceneState | None,
    ) -> SceneState | None:
        if at.tick < 1:
            raise ValueError("scene state tick must start at 1")
        if at.tick == 1:
            if previous_scene_state is not None:
                raise ValueError("first scene state must not have a predecessor")
            return None
        if previous_scene_state is None:
            raise ValueError("later scene states require a predecessor")
        if not isinstance(previous_scene_state, SceneState):
            raise ValueError("previous scene state has an invalid type")
        if previous_scene_state is not self._last_scene_state:
            try:
                previous_scene_state = SceneState.model_validate(
                    previous_scene_state.model_dump(mode="json")
                )
            except ValueError as error:
                raise ValueError("previous scene state is invalid") from error
        if (
            previous_scene_state.run_id != run_id
            or previous_scene_state.scenario_digest != self._scenario.scenario_digest
        ):
            raise ValueError("previous scene state belongs to another run or scenario")
        if previous_scene_state.at.tick != at.tick - 1:
            raise ValueError("scene state ticks must be contiguous")
        if at.sim_time_ns <= previous_scene_state.at.sim_time_ns:
            raise ValueError("scene state time must advance beyond the previous state")
        return previous_scene_state

    def _validate_barrier(
        self,
        *,
        run_id: Sha256,
        at: SimulationTime,
        barrier: StageBarrier,
    ) -> None:
        if barrier.stage != "motion":
            raise ValueError("scene state assembly requires a motion stage barrier")
        if barrier.run_id != run_id:
            raise ValueError("motion barrier belongs to another run")
        if barrier.scenario_digest != self._scenario.scenario_digest:
            raise ValueError("motion barrier belongs to another scenario")
        if barrier.at != at:
            raise ValueError("motion barrier time is stale or does not match assembly time")
        if barrier.provider_ids != self._motion_provider_ids:
            raise ValueError(
                "motion barrier providers must close over dynamic entity owners"
            )

    def _validate_contributions(
        self,
        *,
        run_id: Sha256,
        at: SimulationTime,
        barrier: StageBarrier,
        contributions: tuple[SceneContribution, ...],
    ) -> dict[str, StateSample]:
        provider_ids = tuple(contribution.provider_id for contribution in contributions)
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("motion contributions must have unique providers")
        if provider_ids != tuple(sorted(provider_ids)):
            raise ValueError("motion contributions must be sorted by provider")
        if provider_ids != self._motion_provider_ids:
            raise ValueError("motion contributions must close over dynamic entity owners")
        receipt_by_provider = {
            receipt.provider_id: receipt for receipt in barrier.receipts
        }
        dynamic_samples: dict[str, StateSample] = {}
        for contribution in contributions:
            if contribution.stage != "motion":
                raise ValueError("scene state assembly accepts only motion contributions")
            if contribution.run_id != run_id:
                raise ValueError("motion contribution belongs to another run")
            if contribution.scenario_digest != self._scenario.scenario_digest:
                raise ValueError("motion contribution belongs to another scenario")
            if contribution.at != at:
                raise ValueError(
                    "motion contribution time is stale or does not match assembly time"
                )
            receipt = receipt_by_provider[contribution.provider_id]
            if (
                receipt.contribution_digest != contribution.contribution_digest
                or receipt.payload_digest != contribution.payload_digest
            ):
                raise ValueError("motion receipt does not bind its contribution")
            for sample in contribution.samples:
                entity = self._entity_by_id.get(sample.entity_id)
                if entity is None:
                    raise ValueError("motion contribution names an unknown entity")
                if entity.state == "static":
                    expected = self._static_sample(
                        run_id=run_id,
                        at=at,
                        entity=entity,
                    )
                    if sample != expected:
                        raise ValueError("motion contribution attempts static entity drift")
                    raise ValueError("motion contribution cannot include static entities")
                self._validate_dynamic_sample(
                    contribution=contribution,
                    entity=entity,
                    sample=sample,
                )
                if sample.entity_id in dynamic_samples:
                    raise ValueError("motion contributions contain a duplicate entity")
                dynamic_samples[sample.entity_id] = sample
        received_entity_ids = frozenset(dynamic_samples)
        if received_entity_ids != self._dynamic_entity_ids:
            missing = tuple(sorted(self._dynamic_entity_ids - received_entity_ids))
            raise ValueError(f"motion contributions miss dynamic entities: {list(missing)}")
        return dynamic_samples

    def _validate_dynamic_sample(
        self,
        *,
        contribution: SceneContribution,
        entity: ResolvedEntity,
        sample: StateSample,
    ) -> None:
        if sample.sample_kind != "dynamic":
            raise ValueError("dynamic entities require dynamic state samples")
        if (
            entity.owner_kind != "provider"
            or entity.owner_id != contribution.provider_id
            or sample.provider_id != entity.owner_id
        ):
            raise ValueError("motion contribution violates declared entity ownership")
        self._validate_pose_against_frame_authority(
            sample.pose,
            label=f"dynamic entity {entity.entity_id!r} pose",
        )

    def _static_sample(
        self,
        *,
        run_id: Sha256,
        at: SimulationTime,
        entity: ResolvedEntity,
    ) -> StateSample:
        fields = {
            "schema_version": "aero-bench.state-sample/v1",
            "run_id": run_id,
            "scenario_digest": self._scenario.scenario_digest,
            "at": at,
            "stage": "motion",
            "entity_id": entity.entity_id,
            "provider_id": entity.owner_id,
            "sample_kind": "static",
            "pose": entity.initial_pose,
            "linear_velocity_enu": EnuLinearVelocity(
                east_mps=0.0,
                north_mps=0.0,
                up_mps=0.0,
            ),
            "linear_velocity_ned": NedLinearVelocity(
                north_mps=0.0,
                east_mps=0.0,
                down_mps=0.0,
            ),
            "angular_velocity_body": None,
            "mode": None,
            "armed": None,
            "battery": None,
            "health": None,
            "contacts": (),
            "attributes": (),
        }
        candidate = StateSample.model_construct(
            **fields,
            sample_digest="0" * 64,
        )
        return StateSample(
            **fields,
            sample_digest=state_sample_digest_value(candidate),
        )


__all__ = ["SceneStateAssembler"]
