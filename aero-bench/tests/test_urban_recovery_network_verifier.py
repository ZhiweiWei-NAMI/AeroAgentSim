from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aero_bench.artifacts.contracts import ArtifactRecord
from aero_bench.config.loader import BundleReader
from aero_bench.config.models import AssetAudience, AssetRef, FileRef, NamedValue
from aero_bench.providers.ns3.provider import (
    NetworkMailboxMessage,
    NetworkMailboxObservationPayload,
)
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.urban_recovery_demo import verifier as urban
from aero_bench.tasks.urban_recovery_demo.contracts import DemoTaskPackage
from aero_bench.tasks.urban_recovery_demo.participant import (
    RECOVERY_MESSAGE_SCHEMA,
    RecoveryMessage,
)

RUN_ID = "a" * 64
ARTIFACT_ID = "artifact.network"
WORK_ORDER_ID = f"urban.recovery.{RUN_ID[:16]}"
STEP_NS = 200_000_000
TEST_FINAL_TICK = 220
GOAL = {"x": 300.0, "y": 300.0, "z": 45.0}
POLYGONS = (
    ((-50.0, -50.0), (50.0, -50.0), (50.0, 50.0), (-50.0, 50.0)),
)


@pytest.fixture(autouse=True)
def _bounded_network_horizon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urban, "FINAL_TICK", TEST_FINAL_TICK)


def _package() -> DemoTaskPackage:
    return DemoTaskPackage.model_validate(
        {
            "schema_version": "aero-bench.urban-recovery-demo/v1",
            "package_id": "urban.uav-recovery-demo.v1",
            "replay_mode": "indexed",
            "task_id": "task.urban.recovery",
            "verifier_id": "verifier.urban.recovery",
            "scene_source_sha256": "d" * 64,
            "duration_ns": 600_000_000_000,
            "step_ns": STEP_NS,
            "physics_step_ns": 4_000_000,
            "final_tick": 3000,
            "roles": [
                {
                    "agent_id": "groundstation.rule",
                    "role": "groundstation",
                    "endpoint_id": "endpoint.groundstation",
                    "vehicle_id": None,
                    "telemetry_observation_id": None,
                    "safety_observation_id": None,
                    "mailbox_observation_id": "network.mailbox.endpoint.groundstation",
                },
                {
                    "agent_id": "uav.policy.01",
                    "role": "uav",
                    "endpoint_id": "endpoint.uav.01",
                    "vehicle_id": "uav.01",
                    "telemetry_observation_id": "observation.uav.01.telemetry",
                    "safety_observation_id": "observation.uav.01.safety",
                    "mailbox_observation_id": "network.mailbox.endpoint.uav.01",
                },
                {
                    "agent_id": "uav.policy.02",
                    "role": "uav",
                    "endpoint_id": "endpoint.uav.02",
                    "vehicle_id": "uav.02",
                    "telemetry_observation_id": "observation.uav.02.telemetry",
                    "safety_observation_id": "observation.uav.02.safety",
                    "mailbox_observation_id": "network.mailbox.endpoint.uav.02",
                },
            ],
            "recovery": {
                "incident_vehicle_id": "uav.01",
                "incident_region_id": "region.no-fly.recovery",
                "injection_start_ns": 60_000_000_000,
                "heartbeat_interval_ns": 1_000_000_000,
                "response_timeout_ns": 10_000_000_000,
                "maximum_retries": 3,
                "vehicle_radius_m": 1.0,
                "obstacle_margin_m": 5.0,
                "cruise_agl_m": 45.0,
                "landing_deadline_ns": 540_000_000_000,
            },
            "flight_provider_id": "flight",
            "network_provider_id": "network",
            "traffic_provider_id": "traffic",
            "required_channels": [
                "agent",
                "gazebo.airspace",
                "gazebo.contact",
                "gazebo.force",
                "gazebo.wind",
                "mavlink",
                "ns3",
                "px4",
                "sumo",
                "sumo.signals",
            ],
        }
    )


def _policy_document() -> dict[str, object]:
    return {
        "role": "groundstation",
        "endpoint_id": "endpoint.groundstation",
        "groundstation_endpoint_id": "endpoint.groundstation",
        "vehicle_id": None,
        "telemetry_observation_id": None,
        "safety_observation_id": None,
        "mailbox_observation_id": "network.mailbox.endpoint.groundstation",
        "heartbeat_interval_ns": 1_000_000_000,
        "injection_start_ns": 60_000_000_000,
        "response_timeout_ns": 10_000_000_000,
        "maximum_retries": 3,
        "vehicle_radius_m": 1.0,
        "obstacle_margin_m": 5.0,
        "cruise_agl_m": 45.0,
        "origin_latitude_deg": 31.2304,
        "origin_longitude_deg": 121.4737,
        "origin_ellipsoid_height_m": 50.0,
        "origin_amsl_m": 20.0,
        "recovery_goal_enu_m": GOAL,
        "forbidden_polygons": POLYGONS,
        "vehicle_endpoint_ids": {
            "uav.01": "endpoint.uav.01",
            "uav.02": "endpoint.uav.02",
        },
        "vehicle_agent_ids": {
            "uav.01": "uav.policy.01",
            "uav.02": "uav.policy.02",
        },
    }


def _alert(message_id: str) -> RecoveryMessage:
    return RecoveryMessage(
        schema_version=RECOVERY_MESSAGE_SCHEMA,
        kind="alert",
        message_id=message_id,
        sender_agent_id="uav.policy.01",
        vehicle_id="uav.01",
        incident_region_id="region.no-fly.recovery",
        incident_sequence=1,
    )


def _reply(
    message_id: str,
    cause_message_id: str,
    *,
    goal: dict[str, float] | None = None,
) -> RecoveryMessage:
    return RecoveryMessage(
        schema_version=RECOVERY_MESSAGE_SCHEMA,
        kind="recovery_reply",
        message_id=message_id,
        sender_agent_id="groundstation.rule",
        vehicle_id="uav.01",
        incident_region_id="region.no-fly.recovery",
        incident_sequence=1,
        planner_version="aero-bench.recovery-a-star/v1",
        goal_enu_m=GOAL if goal is None else goal,
        forbidden_polygons=POLYGONS,
        cause_message_id=cause_message_id,
    )


@dataclass(frozen=True)
class _Submission:
    message: RecoveryMessage
    sent_tick: int
    delivered_tick: int | None
    work_order_id: str = WORK_ORDER_ID
    issue_sequence: int | None = None


@dataclass(frozen=True)
class _NetworkFixture:
    raw: bytes
    artifact: ArtifactRecord
    run: SimpleNamespace
    package: DemoTaskPackage
    reader: BundleReader
    ledger: SimpleNamespace
    observations: dict[tuple[str, str, int], tuple[SimpleNamespace, dict[str, object]]]
    issues: dict[str, tuple[SimpleNamespace, dict[str, object]]]
    policy_path: Path


def _record(
    *,
    sequence: int,
    tick: int,
    event_type: str,
    source: str,
    payload_schema_id: str,
    payload: dict[str, object],
    agent_id: str | None = None,
    provider_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        sequence=sequence,
        event=SimpleNamespace(
            event_type=event_type,
            payload_schema_id=payload_schema_id,
            source=source,
            time=SimulationTime(tick=tick, sim_time_ns=tick * STEP_NS),
            payload=tuple(
                NamedValue(name=name, value=value)
                for name, value in sorted(payload.items())
            ),
            agent_id=agent_id,
            provider_id=provider_id,
        ),
    )


def _route(message: RecoveryMessage) -> tuple[str, str, str]:
    if message.kind == "alert":
        return "uav.policy.01", "endpoint.uav.01", "endpoint.groundstation"
    if message.kind == "recovery_reply":
        return "groundstation.rule", "endpoint.groundstation", "endpoint.uav.01"
    raise AssertionError("focused fixture supports only alert/reply traffic")


def _policy_run(tmp_path: Path) -> tuple[BundleReader, SimpleNamespace, Path]:
    policy_path = tmp_path / "participants/groundstation.json"
    policy_path.parent.mkdir(parents=True)
    policy_bytes = canonical_json_bytes(_policy_document())
    policy_path.write_bytes(policy_bytes)
    reference = FileRef(
        path="participants/groundstation.json",
        sha256=hashlib.sha256(policy_bytes).hexdigest(),
    )
    asset = AssetRef(
        asset_id="asset.participant.groundstation.rule",
        file=reference,
        classification="public",
        audiences=(
            AssetAudience(role="agent", workload_ids=("groundstation.rule",)),
            AssetAudience(
                role="verifier", workload_ids=("verifier.urban.recovery",)
            ),
        ),
    )
    run = SimpleNamespace(run_id=RUN_ID, task=SimpleNamespace(assets=(asset,)))
    return BundleReader(tmp_path), run, policy_path


def _network_fixture(
    tmp_path: Path,
    submissions: tuple[_Submission, ...],
) -> _NetworkFixture:
    package = _package()
    reader, run, policy_path = _policy_run(tmp_path)
    roles_by_endpoint = {role.endpoint_id: role for role in package.roles}
    delivery_order = sorted(
        (item for item in submissions if item.delivered_tick is not None),
        key=lambda item: (int(item.delivered_tick or 0), item.message.message_id),
    )
    delivery_rank = {
        item.message.message_id: index
        for index, item in enumerate(delivery_order)
    }
    issue_rank = {
        item.message.message_id: index
        for index, item in enumerate(
            sorted(submissions, key=lambda value: (value.sent_tick, value.message.message_id))
        )
    }

    artifact_records: list[dict[str, object]] = []
    delivery_records: list[SimpleNamespace] = []
    mailbox_by_key: dict[tuple[str, int], list[dict[str, Any]]] = {}
    issues: dict[str, tuple[SimpleNamespace, dict[str, object]]] = {}
    for submission in submissions:
        message = submission.message
        agent_id, source, destination = _route(message)
        sent_at = SimulationTime(
            tick=submission.sent_tick,
            sim_time_ns=submission.sent_tick * STEP_NS,
        )
        payload = canonical_json_bytes(message.model_dump(mode="json"))
        payload_base64 = base64.b64encode(payload).decode("ascii")
        payload_digest = hashlib.sha256(payload).hexdigest()
        command_id = f"command.{message.message_id}"
        arguments: dict[str, object] = {
            "work_order_id": submission.work_order_id,
            "message_id": message.message_id,
            "source": source,
            "destination": destination,
            "payload_base64": payload_base64,
            "payload_sha256": payload_digest,
            "traffic_class": "best_effort",
            "priority": 0,
            "reliability": "best_effort",
        }
        default_offset = 150 if message.kind == "recovery_reply" else 250
        issue_sequence = (
            submission.issue_sequence
            if submission.issue_sequence is not None
            else submission.sent_tick * 1000
            + default_offset
            + issue_rank[message.message_id]
        )
        issue = _record(
            sequence=issue_sequence,
            tick=submission.sent_tick,
            event_type="command.issued",
            source=agent_id,
            payload_schema_id="command.issued.v1",
            payload={"tool_id": "network.send"},
            agent_id=agent_id,
            provider_id="network",
        )
        issues[command_id] = (issue, arguments)
        delivered_at = (
            None
            if submission.delivered_tick is None
            else SimulationTime(
                tick=submission.delivered_tick,
                sim_time_ns=submission.delivered_tick * STEP_NS,
            )
        )
        artifact_records.append(
            {
                "source_artifact_id": ARTIFACT_ID,
                "run_id": RUN_ID,
                "work_order_id": submission.work_order_id,
                "message_id": message.message_id,
                "payload_digest": payload_digest,
                "sent_at": sent_at.model_dump(mode="json"),
                "delivered_at": (
                    None if delivered_at is None else delivered_at.model_dump(mode="json")
                ),
            }
        )
        if delivered_at is None:
            continue
        delivery_payload = {
            "schema_id": urban._NETWORK_DELIVERY_SCHEMA,
            "run_id": RUN_ID,
            "provider_id": "network",
            "command_id": command_id,
            "agent_id": agent_id,
            "source_artifact_id": ARTIFACT_ID,
            "work_order_id": submission.work_order_id,
            "message_id": message.message_id,
            "payload_digest": payload_digest,
            "sent_tick": sent_at.tick,
            "sent_time_ns": sent_at.sim_time_ns,
            "delivered_tick": delivered_at.tick,
            "delivered_time_ns": delivered_at.sim_time_ns,
        }
        delivery_records.append(
            _record(
                sequence=delivered_at.tick * 1000
                + delivery_rank[message.message_id],
                tick=delivered_at.tick,
                event_type="network.delivery",
                source="network",
                payload_schema_id=urban._NETWORK_DELIVERY_SCHEMA,
                payload=delivery_payload,
                agent_id=agent_id,
                provider_id="network",
            )
        )
        mailbox_message = NetworkMailboxMessage(
            work_order_id=submission.work_order_id,
            message_id=message.message_id,
            command_id=command_id,
            sender_agent_id=agent_id,
            source=source,
            destination=destination,
            payload_base64=payload_base64,
            payload_sha256=payload_digest,
            send_time=sent_at,
            arrival_time=delivered_at,
            path=(source, destination),
        )
        recipient = roles_by_endpoint[destination]
        mailbox_by_key.setdefault((recipient.agent_id, delivered_at.tick), []).append(
            mailbox_message.model_dump(mode="json")
        )

    observations: dict[
        tuple[str, str, int], tuple[SimpleNamespace, dict[str, object]]
    ] = {}
    observation_offsets = {
        "groundstation.rule": 100,
        "uav.policy.01": 200,
        "uav.policy.02": 300,
    }
    for role in package.roles:
        for tick in range(TEST_FINAL_TICK + 1):
            messages = sorted(
                mailbox_by_key.get((role.agent_id, tick), []),
                key=lambda item: str(item["message_id"]),
            )
            messages_json = canonical_json_bytes(messages).decode("utf-8")
            mailbox = NetworkMailboxObservationPayload(
                schema_version="aero-bench.network-mailbox-observation/v1",
                run_id=RUN_ID,
                provider_id="network",
                agent_id=role.agent_id,
                observation_id=role.mailbox_observation_id,
                mailbox_endpoint_id=role.endpoint_id,
                time_tick=tick,
                sim_time_ns=tick * STEP_NS,
                stage="reset" if tick == 0 else "network",
                input_scene_state_digest=None if tick == 0 else "1" * 64,
                motion_barrier_digest=None if tick == 0 else "2" * 64,
                network_step_receipt_digest="3" * 64,
                message_count=len(messages),
                messages_digest=hashlib.sha256(
                    canonical_json_bytes(messages)
                ).hexdigest(),
                messages_json=messages_json,
            )
            observation_record = _record(
                sequence=tick * 1000 + observation_offsets[role.agent_id],
                tick=tick,
                event_type="observation.validated",
                source="network",
                payload_schema_id="observation.validated.v1",
                payload={},
                agent_id=role.agent_id,
                provider_id="network",
            )
            observations[(role.agent_id, role.mailbox_observation_id, tick)] = (
                observation_record,
                mailbox.model_dump(mode="python"),
            )

    artifact_records.sort(key=lambda item: str(item["message_id"]))
    raw = canonical_json_bytes(artifact_records)
    artifact = ArtifactRecord(
        artifact_id=ARTIFACT_ID,
        artifact_type="network.delivery",
        producer_id="network",
        visibility="public",
        relative_path="network/delivery.json",
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
    )
    return _NetworkFixture(
        raw=raw,
        artifact=artifact,
        run=run,
        package=package,
        reader=reader,
        ledger=SimpleNamespace(
            records=tuple(sorted(delivery_records, key=lambda item: item.sequence))
        ),
        observations=observations,
        issues=issues,
        policy_path=policy_path,
    )


def _validate(
    fixture: _NetworkFixture,
) -> tuple[tuple[RecoveryMessage, SimulationTime], tuple[RecoveryMessage, SimulationTime]]:
    return urban._validate_network(
        fixture.raw,
        fixture.artifact,
        fixture.run,
        fixture.package,
        fixture.reader,
        fixture.ledger,
        fixture.observations,
        fixture.issues,
    )


def test_multiple_delayed_replies_use_actual_mailbox_processing_order(
    tmp_path: Path,
) -> None:
    fixture = _network_fixture(
        tmp_path,
        (
            _Submission(_alert("alert.00"), sent_tick=10, delivered_tick=20),
            _Submission(
                _reply("reply.a", "alert.00"), sent_tick=20, delivered_tick=90
            ),
            _Submission(_alert("alert.01"), sent_tick=60, delivered_tick=70),
            _Submission(
                _reply("reply.z", "alert.01"), sent_tick=70, delivered_tick=90
            ),
        ),
    )

    alert, reply = _validate(fixture)

    assert alert[0].message_id == "alert.00"
    assert alert[1].tick == 20
    assert reply[0].message_id == "reply.a"
    assert reply[1].tick == 90


def test_retry_deadline_uses_prior_submission_time(tmp_path: Path) -> None:
    fixture = _network_fixture(
        tmp_path,
        (
            _Submission(_alert("alert.00"), sent_tick=10, delivered_tick=20),
            _Submission(
                _reply("reply.a", "alert.00"), sent_tick=20, delivered_tick=90
            ),
            _Submission(_alert("alert.01"), sent_tick=61, delivered_tick=None),
        ),
    )

    with pytest.raises(ValueError, match="submission-time deadlines"):
        _validate(fixture)


def test_retry_budget_counts_undelivered_alert_submissions(tmp_path: Path) -> None:
    fixture = _network_fixture(
        tmp_path,
        tuple(
            _Submission(_alert(f"alert.{index:02d}"), sent_tick=tick, delivered_tick=None)
            for index, tick in enumerate((10, 60, 110, 160, 210))
        ),
    )

    with pytest.raises(urban._MissionFailure) as caught:
        _validate(fixture)

    assert caught.value.failure_class == "urban.mission.alert_retry_limit_exceeded"


def test_accepted_submissions_do_not_require_delivery_callbacks(tmp_path: Path) -> None:
    fixture = _network_fixture(
        tmp_path,
        tuple(
            _Submission(_alert(f"alert.{index:02d}"), sent_tick=tick, delivered_tick=None)
            for index, tick in enumerate((10, 60, 110, 160))
        ),
    )

    with pytest.raises(urban._MissionFailure) as caught:
        _validate(fixture)

    assert caught.value.failure_class == "urban.mission.alert_not_delivered"


def test_network_work_order_must_equal_exact_run_identity(tmp_path: Path) -> None:
    fixture = _network_fixture(
        tmp_path,
        (
            _Submission(
                _alert("alert.00"),
                sent_tick=10,
                delivered_tick=None,
                work_order_id="urban.recovery.wrong-run",
            ),
        ),
    )

    with pytest.raises(ValueError, match="work order"):
        _validate(fixture)


def test_reply_cannot_name_an_undelivered_alert_attempt(tmp_path: Path) -> None:
    fixture = _network_fixture(
        tmp_path,
        (
            _Submission(_alert("alert.00"), sent_tick=10, delivered_tick=None),
            _Submission(
                _reply("reply.a", "alert.00"), sent_tick=20, delivered_tick=None
            ),
        ),
    )

    with pytest.raises(ValueError, match="reply cause is undelivered"):
        _validate(fixture)


def test_reply_requires_prior_groundstation_mailbox_observation(
    tmp_path: Path,
) -> None:
    fixture = _network_fixture(
        tmp_path,
        (
            _Submission(_alert("alert.00"), sent_tick=10, delivered_tick=20),
            _Submission(
                _reply("reply.a", "alert.00"),
                sent_tick=20,
                delivered_tick=None,
                issue_sequence=20_050,
            ),
        ),
    )

    with pytest.raises(ValueError, match="prior groundstation mailbox"):
        _validate(fixture)


def test_reply_planning_inputs_must_match_digest_bound_policy(tmp_path: Path) -> None:
    fixture = _network_fixture(
        tmp_path,
        (
            _Submission(_alert("alert.00"), sent_tick=10, delivered_tick=20),
            _Submission(
                _reply(
                    "reply.a",
                    "alert.00",
                    goal={"x": 320.0, "y": 300.0, "z": 45.0},
                ),
                sent_tick=20,
                delivered_tick=30,
            ),
        ),
    )

    with pytest.raises(ValueError, match="planning constraints"):
        _validate(fixture)


def test_groundstation_policy_bytes_remain_digest_bound(tmp_path: Path) -> None:
    fixture = _network_fixture(
        tmp_path,
        (
            _Submission(_alert("alert.00"), sent_tick=10, delivered_tick=20),
            _Submission(
                _reply("reply.a", "alert.00"), sent_tick=20, delivered_tick=30
            ),
        ),
    )
    fixture.policy_path.write_bytes(b"{}")

    with pytest.raises(ValueError, match="sha256 mismatch"):
        _validate(fixture)
