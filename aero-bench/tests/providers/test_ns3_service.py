from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path

import pytest

from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection import DeliveryEvidence


_SERVER_PATH = Path(__file__).parents[2] / "containers" / "ns3" / "server.py"
_SPEC = importlib.util.spec_from_file_location("aero_bench_ns3_server", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_SERVER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SERVER)


RUN_ID = "a" * 64
SESSION_TOKEN = "c" * 64
RUNTIME_IMAGE = "registry.test/ns3@sha256:" + "1" * 64
CONFIG_DIGEST = "b" * 64


def _network_projection() -> object:
    profile = _SERVER.WifiRadioProfile(
        radio_profile_id="radio.ax",
        provider_id="network",
        wifi_standard="802.11ax",
        frequency_ghz=5.805,
        frequency_mhz=5805,
        channel_number=161,
        channel_width_mhz=20,
        band="BAND_5GHZ",
        tx_power_dbm=20.0,
        rx_sensitivity_dbm=-92.0,
        data_mode="HeMcs11",
        control_mode="HeMcs0",
        max_data_rate_bps=143_000_000,
    )
    return _SERVER.Ns3NetworkProjection(
        provider_id="network",
        radio_profiles=(profile,),
        node_bindings=(
            _SERVER.NetworkNodeBinding(
                node_id="node.edge",
                entity_id="entity.edge",
                endpoint_id="edge.1",
                radio_profile_id="radio.ax",
                position_enu_m=(10.0, 0.0, 0.0),
            ),
            _SERVER.NetworkNodeBinding(
                node_id="node.uav",
                entity_id="entity.uav",
                endpoint_id="uav.1",
                radio_profile_id="radio.ax",
                position_enu_m=(0.0, 0.0, 0.0),
            ),
        ),
        links=(
            _SERVER.NetworkLinkBinding(
                link_id="link.1",
                source_node_id="node.uav",
                destination_node_id="node.edge",
                data_rate_bps=1_000_000,
                propagation_delay_ns=10,
            ),
        ),
        propagation_volumes=(),
        projection_digest="d" * 64,
    )


def workload_identity() -> object:
    scenario = _SERVER._provider_selfcheck_identity(
        runtime_image=RUNTIME_IMAGE, run_id=RUN_ID
    ).scenario
    return _SERVER.Ns3WorkloadIdentity(
        run_id=RUN_ID,
        provider_id="network",
        adapter="ns3.rpc",
        runtime_image=RUNTIME_IMAGE,
        config=_SERVER.Ns3ConfigIdentity(
            config_digest=CONFIG_DIGEST,
            network_model=_SERVER.NETWORK_MODEL,
            supported_wifi_standards=("802.11ax",),
        ),
        clock=_SERVER.Ns3ClockIdentity(
            step_ns=100,
            max_steps=10,
            provider_timeout_ms=1_000,
        ),
        scenario_digest=scenario.scenario_digest,
        scenario=scenario,
        network=_network_projection(),
        artifact_requirement={
            "artifact_id": "artifact.network.delivery",
            "artifact_type": "network.delivery",
            "producer_id": "network",
            "visibility": "private",
            "relative_path": "evidence/network-delivery.json",
            "max_size_bytes": 65_536,
            "source_asset_id": None,
        },
    )


def prepare_payload(
    requirement: dict[str, object], *, session_token: str = SESSION_TOKEN
) -> dict[str, object]:
    return {
        "provider_id": "network",
        "protocol_version": _SERVER.PROTOCOL_VERSION,
        "runtime_image": RUNTIME_IMAGE,
        "config_digest": CONFIG_DIGEST,
        "network_model": _SERVER.NETWORK_MODEL,
        "supported_wifi_standards": ["802.11ax"],
        "artifact_requirements": [requirement],
        "session_token": session_token,
    }


def prepare_backend_response() -> list[str]:
    return [
        " ".join(
            (
                "OK",
                "PREPARE",
                _SERVER.b64(_SERVER.NETWORK_MODEL),
                "1",
                "2",
                "1",
            )
        )
    ]


def network_stage_request(tick: int) -> dict[str, object]:
    """Mechanical protocol fixture with canonical motion and SceneState hashes."""
    identity = workload_identity()
    target = {"tick": tick, "sim_time_ns": tick * identity.clock.step_ns}
    samples = []
    for entity in identity.scenario.scenario["entities"]:
        sample = {
            "schema_version": "aero-bench.state-sample/v1",
            "run_id": RUN_ID, "scenario_digest": identity.scenario_digest,
            "at": target, "stage": "motion", "entity_id": entity["entity_id"],
            "provider_id": entity["owner_id"], "sample_kind": entity["state"],
            "pose": entity["initial_pose"],
            "linear_velocity_enu": {"frame_id": "ENU", "east_mps": 0.0, "north_mps": 0.0, "up_mps": 0.0},
            "linear_velocity_ned": {"frame_id": "NED", "north_mps": 0.0, "east_mps": 0.0, "down_mps": 0.0},
            "angular_velocity_body": None, "mode": None, "armed": None,
            "battery": None, "health": None, "contacts": [], "attributes": [],
        }
        sample["sample_digest"] = _SERVER.digest(sample)
        samples.append(sample)
    receipt = {
        "schema_version": "aero-bench.stage-receipt/v1", "run_id": RUN_ID,
        "scenario_digest": identity.scenario_digest, "at": target,
        "stage": "motion", "provider_id": "flight",
        **{name: _SERVER.digest({"fixture": name, "tick": tick})
           for name in ("state_digest", "step_receipt_digest", "contribution_digest", "payload_digest")},
        "input_scene_state_digest": None, "predecessor_barriers": [],
    }
    receipt["receipt_digest"] = _SERVER.digest(receipt)
    barrier = {
        "schema_version": "aero-bench.stage-barrier/v1", "run_id": RUN_ID,
        "scenario_digest": identity.scenario_digest, "at": target, "stage": "motion",
        "input_scene_state_digest": None, "predecessor_barriers": [],
        "provider_ids": ["flight"], "receipts": [receipt],
        "receipt_digests": [receipt["receipt_digest"]],
    }
    barrier["barrier_digest"] = _SERVER.digest(barrier)
    scene = {
        "schema_version": "aero-bench.scene-state/v1", "run_id": RUN_ID,
        "scenario_digest": identity.scenario_digest, "at": target,
        "declared_entity_ids": [e["entity_id"] for e in identity.scenario.scenario["entities"]],
        "samples": samples, "stage_barrier": barrier,
        "contribution_digests": [receipt["contribution_digest"]],
        "previous_scene_state_digest": "0" * 64 if tick == 1 else network_stage_request(tick - 1)["scene_state_digest"],
    }
    scene["scene_state_digest"] = _SERVER.digest(scene)
    return {
        "schema_version": "aero-bench.provider-stage-request/v1", "run_id": RUN_ID,
        "scenario_digest": identity.scenario_digest, "provider_id": "network",
        "target": target, "stage": "network", "scene_state": scene,
        "scene_state_digest": scene["scene_state_digest"],
        "predecessor_barriers": [{"stage": "motion", "barrier_digest": barrier["barrier_digest"]}],
    }


def backend_link_record() -> str:
    """Return deterministic mechanical link facts for the fixture's 10 m link."""
    distance = 10.0
    path_loss = (20.0 * math.log10(4.0 * math.pi * 5805_000_000.0 / 299_792_458.0)
                 + 10.0 * _SERVER.LOG_DISTANCE_EXPONENT * math.log10(distance))
    rssi = 20.0 - path_loss
    noise = (_SERVER.THERMAL_NOISE_DENSITY_DBM_HZ + 10.0 * math.log10(20_000_000.0)
             + _SERVER.RX_NOISE_FIGURE_DB)
    return " ".join([
        "LINK", _SERVER.b64("link.1"), _SERVER.b64("node.uav"), _SERVER.b64("node.edge"),
        *(format(v, ".17g") for v in (distance, path_loss, 0.0, rssi, rssi, rssi - noise, rssi - noise)),
        "0", "0", "0", "0",
    ])


@pytest.mark.parametrize(
    "frame",
    (
        b'{"value":NaN}\n',
        b'{"value":Infinity}\n',
        b'{"value":-Infinity}\n',
        b'{"value":1}',
        b'{"value":1,"value":2}\n',
    ),
)
def test_ns3_service_rejects_non_strict_json_lines(frame: bytes) -> None:
    with pytest.raises(
        _SERVER.ProviderError, match="strict JSON|newline|duplicate|finite"
    ):
        _SERVER.parse_json(frame)


@pytest.mark.parametrize("visibility", ("public", "private"))
def test_ns3_service_preserves_public_or_private_artifact_contract(
    visibility: str,
) -> None:
    requirement = {
        "artifact_id": "artifact.network.delivery",
        "artifact_type": "network.delivery",
        "producer_id": "network",
        "visibility": visibility,
        "relative_path": "evidence/non-default-delivery.json",
        "max_size_bytes": 4096,
        "source_asset_id": None,
    }
    assert (
        _SERVER.require_artifact_requirement(requirement, provider_id="network")
        == requirement
    )


def test_ns3_service_rejects_asset_backed_delivery() -> None:
    requirement = {
        "artifact_id": "artifact.network.delivery",
        "artifact_type": "network.delivery",
        "producer_id": "network",
        "visibility": "private",
        "relative_path": "evidence/non-default-delivery.json",
        "max_size_bytes": 4096,
        "source_asset_id": "private.truth",
    }
    with pytest.raises(_SERVER.ProviderError, match="source_asset_id"):
        _SERVER.require_artifact_requirement(requirement, provider_id="network")


@pytest.mark.parametrize(
    "relative_path", ("/delivery.json", ".", "a/../delivery.json", "a\\delivery.json")
)
def test_ns3_service_rejects_unsafe_artifact_paths(relative_path: str) -> None:
    requirement = {
        "artifact_id": "artifact.network.delivery",
        "artifact_type": "network.delivery",
        "producer_id": "network",
        "visibility": "private",
        "relative_path": relative_path,
        "max_size_bytes": 4096,
        "source_asset_id": None,
    }
    with pytest.raises(_SERVER.ProviderError, match="normalized"):
        _SERVER.require_artifact_requirement(requirement, provider_id="network")


def test_ns3_prepare_rejects_artifact_contract_not_in_workload(
    tmp_path: Path, monkeypatch
) -> None:
    async def run() -> None:
        monkeypatch.setenv("AERO_BENCH_RUN_ID", RUN_ID)
        monkeypatch.setenv("AERO_BENCH_SEED", "7")
        monkeypatch.setenv("AERO_BENCH_ARTIFACT_DIR", str(tmp_path))
        identity = workload_identity()
        service = _SERVER.Ns3Service(identity, session_token=SESSION_TOKEN)
        forged_requirement = {
            **identity.artifact_requirement,
            "max_size_bytes": 4096,
        }
        with pytest.raises(_SERVER.ProviderError) as error:
            await service.handle("prepare", prepare_payload(forged_requirement))
        assert error.value.code == "identity.mismatch"
        assert service._prepared is False

    asyncio.run(run())


@pytest.mark.parametrize(
    "clock",
    (
        {
            "authority": "free_running",
            "step_ns": 100,
            "max_steps": 10,
            "provider_timeout_ms": 1_000,
        },
        {
            "authority": "provider_barrier",
            "step_ns": 2**63 - 1,
            "max_steps": 2,
            "provider_timeout_ms": 1_000,
        },
        {
            "authority": "provider_barrier",
            "step_ns": 100,
            "max_steps": 10,
            "provider_timeout_ms": 600_001,
        },
    ),
)
def test_ns3_rejects_unsupported_or_unbounded_contract_clock(
    clock: dict[str, object],
) -> None:
    with pytest.raises(_SERVER.ProviderError):
        _SERVER._parse_clock(clock)


def test_ns3_step_requires_next_contract_clock_barrier(
    tmp_path: Path, monkeypatch
) -> None:
    async def run() -> None:
        monkeypatch.setenv("AERO_BENCH_RUN_ID", RUN_ID)
        monkeypatch.setenv("AERO_BENCH_SEED", "7")
        monkeypatch.setenv("AERO_BENCH_ARTIFACT_DIR", str(tmp_path))
        identity = workload_identity()
        service = _SERVER.Ns3Service(identity, session_token=SESSION_TOKEN)

        class Backend:
            async def request(self, command: list[str]) -> list[str]:
                if command[0] == "PREPARE":
                    return prepare_backend_response()
                if command[0] == "RESET":
                    return ["OK RESET 0 0 0 0"]
                raise AssertionError(f"unexpected backend command: {command}")

        service._backend = Backend()
        await service.handle(
            "prepare", prepare_payload(identity.artifact_requirement)
        )
        await service.handle(
            "reset",
            {
                "provider_id": "network",
                "seed": 7,
                "session_token": SESSION_TOKEN,
            },
        )
        with pytest.raises(_SERVER.ProviderError, match="contract clock barrier"):
            await service.handle(
                "step_stage",
                {
                    "request": network_stage_request(2),
                    "session_token": SESSION_TOKEN,
                },
            )

    asyncio.run(run())


def test_ns3_public_link_uses_bound_scenario_entities(
    tmp_path: Path, monkeypatch
) -> None:
    async def run() -> None:
        monkeypatch.setenv("AERO_BENCH_RUN_ID", RUN_ID)
        monkeypatch.setenv("AERO_BENCH_SEED", "7")
        monkeypatch.setenv("AERO_BENCH_ARTIFACT_DIR", str(tmp_path))
        base_identity = workload_identity()
        requirement = {**base_identity.artifact_requirement, "visibility": "public"}
        identity = base_identity._replace(artifact_requirement=requirement)
        service = _SERVER.Ns3Service(identity, session_token=SESSION_TOKEN)

        class Backend:
            async def request(self, command: list[str]) -> list[str]:
                if command[0] == "MOBILITY":
                    self.scene_digest = command[1]
                    return [f"OK MOBILITY {command[1]} 2 1"]
                if command[0] == "STEP":
                    return [f"OK STEP 100 1 0 1 {self.scene_digest}", backend_link_record(), "END"]
                responses = {
                    "PREPARE": prepare_backend_response(),
                    "RESET": ["OK RESET 0 0 0 0"],
                    "MESSAGE": ["OK MESSAGE"],
                }
                return responses[command[0]]

        service._backend = Backend()
        await service.handle("prepare", prepare_payload(requirement))
        await service.handle(
            "reset",
            {
                "provider_id": "network",
                "seed": 7,
                "session_token": SESSION_TOKEN,
            },
        )
        payload_bytes = b"entity-binding"
        payload_base64 = base64.b64encode(payload_bytes).decode()
        await service.handle(
            "submit_message",
            {
                "message": {
                    "message_id": "message.entity-binding",
                    "source": "uav.1",
                    "destination": "edge.1",
                    "payload_base64": payload_base64,
                    "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
                    "qos": dict(_SERVER.SUPPORTED_QOS),
                    "send_time": {"tick": 0, "sim_time_ns": 0},
                },
                "session_token": SESSION_TOKEN,
                "work_order_id": "work-order.entity-binding",
            },
        )
        response = await service.handle(
            "step_stage",
            {
                "request": network_stage_request(1),
                "session_token": SESSION_TOKEN,
            },
        )
        event = next(
            item
            for item in response["receipt"]["events"]
            if item["payload_schema_id"] == "public.network.link.v3"
        )
        public_link = json.loads(event["payload"][0]["value"])
        assert public_link["source_entity_id"] == "entity.uav"
        assert public_link["target_entity_id"] == "entity.edge"

    asyncio.run(run())


def test_ns3_service_finalizes_exact_artifact_and_freezes_state(
    tmp_path: Path, monkeypatch
) -> None:
    async def run() -> None:
        run_id = "a" * 64
        session_token = "c" * 64
        monkeypatch.setenv("AERO_BENCH_RUN_ID", run_id)
        monkeypatch.setenv("AERO_BENCH_SEED", "7")
        monkeypatch.setenv("AERO_BENCH_ARTIFACT_DIR", str(tmp_path))
        service = _SERVER.Ns3Service(
            workload_identity(), session_token=session_token
        )

        class Backend:
            async def request(self, command: list[str]) -> list[str]:
                if command[0] == "PREPARE":
                    return prepare_backend_response()
                if command[0] == "RESET":
                    return ["OK RESET 0 0 0 0"]
                if command[0] == "SHUTDOWN":
                    return ["OK SHUTDOWN"]
                raise AssertionError(f"unexpected backend command: {command}")

        backend = Backend()
        service._backend = backend
        requirement = {
            "artifact_id": "artifact.network.delivery",
            "artifact_type": "network.delivery",
            "producer_id": "network",
            "visibility": "private",
            "relative_path": "evidence/network-delivery.json",
            "max_size_bytes": 65_536,
            "source_asset_id": None,
        }
        await service.handle("prepare", prepare_payload(requirement))
        await service.handle(
            "reset",
            {
                "provider_id": "network",
                "seed": 7,
                "session_token": session_token,
            },
        )
        artifact = tmp_path / "evidence/network-delivery.json"
        assert not artifact.exists()
        request = {
            "provider_id": "network",
            "request": {
                "schema_version": "aero-bench.provider-finalization-request/v1",
                "run_id": run_id,
                "terminal_event": "run.completed",
                "terminal_time": {"tick": 0, "sim_time_ns": 0},
                "event_chain_root": "d" * 64,
            },
            "session_token": session_token,
        }
        first = await service.handle("finalize", request)
        assert await service.handle("finalize", request) == first
        content = artifact.read_bytes()
        document = json.loads(content)
        assert document == []
        assert first["receipt"]["artifacts"] == [
            {
                "artifact_id": requirement["artifact_id"],
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        ]
        changed_root = {
            **request,
            "request": {**request["request"], "event_chain_root": "e" * 64},
        }
        with pytest.raises(_SERVER.ProviderError, match="root cannot be changed"):
            await service.handle("finalize", changed_root)
        with pytest.raises(_SERVER.ProviderError, match="already finalized"):
            await service.handle(
                "reset",
                {
                    "provider_id": "network",
                    "seed": 7,
                    "session_token": session_token,
                },
            )
        assert await service.handle(
            "shutdown",
            {"provider_id": "network", "session_token": session_token},
        ) == {"status": "stopped"}
        assert artifact.read_bytes() == content

    asyncio.run(run())


def test_ns3_service_releases_real_arrivals_and_writes_direct_delivery_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    async def run() -> None:
        run_id = "a" * 64
        session_token = "c" * 64
        monkeypatch.setenv("AERO_BENCH_RUN_ID", run_id)
        monkeypatch.setenv("AERO_BENCH_SEED", "7")
        monkeypatch.setenv("AERO_BENCH_ARTIFACT_DIR", str(tmp_path))
        service = _SERVER.Ns3Service(
            workload_identity(), session_token=session_token
        )

        class Backend:
            def __init__(self) -> None:
                self.messages: dict[str, tuple[str, str, str, int, int]] = {}
                self.report_invalid_facts = False

            async def request(self, command: list[str]) -> list[str]:
                if command[0] == "PREPARE":
                    return prepare_backend_response()
                if command[0] == "RESET":
                    return ["OK RESET 0 0 0 0"]
                if command[0] == "SHUTDOWN":
                    return ["OK SHUTDOWN"]
                if command[0] == "MESSAGE":
                    message_id = base64.b64decode(command[1]).decode()
                    self.messages[message_id] = (
                        command[2],
                        command[3],
                        command[4],
                        int(command[5]),
                        int(command[6]),
                    )
                    return ["OK MESSAGE"]
                if command[0] == "MOBILITY":
                    self.scene_digest = command[1]
                    return [f"OK MOBILITY {command[1]} 2 1"]
                if command[0] == "STEP":
                    message_id = "message.b"
                    source, destination, payload, send_tick, send_ns = self.messages[
                        message_id
                    ]
                    submitted = 3 if self.report_invalid_facts else 2
                    return [
                        f"OK STEP {command[1]} {submitted} 1 1 {self.scene_digest}",
                        backend_link_record(),
                        " ".join(
                            (
                                "DELIVERY",
                                _SERVER.b64(message_id),
                                source,
                                destination,
                                str(send_tick),
                                str(send_ns),
                                "50",
                                payload,
                            )
                        ),
                        "END",
                    ]
                raise AssertionError(f"unexpected backend command: {command}")

        backend = Backend()
        service._backend = backend
        requirement = {
            "artifact_id": "artifact.network.delivery",
            "artifact_type": "network.delivery",
            "producer_id": "network",
            "visibility": "private",
            "relative_path": "evidence/network-delivery.json",
            "max_size_bytes": 65_536,
            "source_asset_id": None,
        }
        await service.handle("prepare", prepare_payload(requirement))
        await service.handle(
            "reset",
            {"provider_id": "network", "seed": 7, "session_token": session_token},
        )

        async def submit(message_id: str, work_order_id: str) -> None:
            payload_bytes = message_id.encode()
            payload = base64.b64encode(payload_bytes).decode()
            response = await service.handle(
                "submit_message",
                {
                    "message": {
                        "message_id": message_id,
                        "source": "uav.1",
                        "destination": "edge.1",
                        "payload_base64": payload,
                        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
                        "qos": dict(_SERVER.SUPPORTED_QOS),
                        "send_time": {"tick": 0, "sim_time_ns": 0},
                    },
                    "session_token": session_token,
                    "work_order_id": work_order_id,
                },
            )
            assert response["submission"]["work_order_id"] == work_order_id

        await submit("message.b", "wo.b")
        await submit("message.a", "wo.a")

        backend.report_invalid_facts = True
        with pytest.raises(_SERVER.ProviderError, match="facts differ"):
            await service.handle(
                "step_stage",
                {
                    "request": network_stage_request(1),
                    "session_token": session_token,
                },
            )
        backend.report_invalid_facts = False

        released = await service.handle(
            "step_stage",
            {
                "request": network_stage_request(1),
                "session_token": session_token,
            },
        )
        assert [item["trace"]["message_id"] for item in released["deliveries"]] == [
            "message.b"
        ]
        assert released["deliveries"][0]["work_order_id"] == "wo.b"

        with pytest.raises(_SERVER.ProviderError, match="more than once"):
            await service.handle(
                "step_stage",
                {
                    "request": network_stage_request(2),
                    "session_token": session_token,
                },
            )

        await service.handle(
            "finalize",
            {
                "provider_id": "network",
                "request": {
                    "schema_version": "aero-bench.provider-finalization-request/v1",
                    "run_id": run_id,
                    "terminal_event": "run.completed",
                    "terminal_time": {"tick": 1, "sim_time_ns": 100},
                    "event_chain_root": "d" * 64,
                },
                "session_token": session_token,
            },
        )
        content = (tmp_path / "evidence/network-delivery.json").read_bytes()
        document = json.loads(content)
        assert content == canonical_json_bytes(document)
        assert [record["message_id"] for record in document] == [
            "message.a",
            "message.b",
        ]
        evidence = tuple(DeliveryEvidence.model_validate(record) for record in document)
        assert evidence[0].work_order_id == "wo.a"
        assert evidence[0].delivered_at is None
        assert evidence[1].work_order_id == "wo.b"
        assert evidence[1].delivered_at is not None
        assert evidence[1].delivered_at.tick == 1
        assert evidence[1].delivered_at.sim_time_ns == 50
        assert {record.source_artifact_id for record in evidence} == {
            requirement["artifact_id"]
        }

    asyncio.run(run())


def _resolved_position(east_m: float) -> dict[str, object]:
    return {
        "enu": {"east_m": east_m, "north_m": 0.0, "up_m": 0.0},
        "ned": {"north_m": 0.0, "east_m": east_m, "down_m": 0.0},
        "ecef": {"x_m": east_m, "y_m": 0.0, "z_m": 0.0},
        "wgs84": {
            "longitude_deg": 0.0,
            "latitude_deg": 1.0,
            "ellipsoid_height_m": 0.0,
        },
        "geoid_separation_m": 0.0,
        "amsl_m": 0.0,
        "terrain_amsl_m": 0.0,
        "agl_m": 0.0,
    }


def _resolved_entity(entity_id: str, east_m: float) -> dict[str, object]:
    quaternion = {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0}
    return {
        "entity_id": entity_id,
        "kind": "uav",
        "owner_kind": "provider",
        "owner_id": "flight",
        "source_provider_id": "flight",
        "authority_kind": "gazebo_physics",
        "state": "dynamic",
        "model_asset_id": "model.uav",
        "initial_pose": {
            "position": _resolved_position(east_m),
            "orientation_enu": quaternion,
            "orientation_ned": quaternion,
        },
        "selected_launch_override": False,
    }


def _projection_scenario() -> object:
    capabilities = list(_SERVER._required_capabilities(("802.11ax",)))
    schema = {"path": "schemas/endpoint.json", "sha256": "1" * 64}
    document = {
        "providers": [
            {
                "provider_id": "flight",
                "runtime_stage": "motion",
                "roles": ["motion"],
                "capability_ids": ["flight.command"],
            },
            {
                "provider_id": "network",
                "runtime_stage": "network",
                "roles": ["wireless_network"],
                "capability_ids": capabilities,
            },
        ],
        "task": {
            "logical_endpoint_ids": ["edge.1", "uav.1"],
            "tools": [
                {
                    "binding_id": "agent.1.endpoint.edge",
                    "agent_id": "agent.1",
                    "tool_id": "endpoint.edge",
                    "endpoint_id": "edge.1",
                    "request_schema": schema,
                    "response_schema": schema,
                    "timeout_ms": 1000,
                    "idempotent": False,
                },
                {
                    "binding_id": "agent.1.endpoint.uav",
                    "agent_id": "agent.1",
                    "tool_id": "endpoint.uav",
                    "endpoint_id": "uav.1",
                    "request_schema": schema,
                    "response_schema": schema,
                    "timeout_ms": 1000,
                    "idempotent": False,
                },
            ],
            "observations": [],
        },
        "entities": [
            _resolved_entity("entity.edge", 10.0),
            _resolved_entity("entity.uav", 0.0),
        ],
        "buildings": [],
        "regions": [],
        "network": {
            "provider_id": "network",
            "radio_profiles": [
                {
                    "radio_profile_id": "radio.ax",
                    "provider_id": "network",
                    "wifi_standard": "802.11ax",
                    "frequency_ghz": 5.805,
                    "channel_width_mhz": 20.0,
                    "tx_power_dbm": 20.0,
                    "rx_sensitivity_dbm": -92.0,
                }
            ],
            "node_bindings": [
                {
                    "node_id": "node.edge",
                    "entity_id": "entity.edge",
                    "endpoint_id": "edge.1",
                    "radio_profile_id": "radio.ax",
                },
                {
                    "node_id": "node.uav",
                    "entity_id": "entity.uav",
                    "endpoint_id": "uav.1",
                    "radio_profile_id": "radio.ax",
                },
            ],
            "links": [
                {
                    "link_id": "link.1",
                    "source_node_id": "node.uav",
                    "destination_node_id": "node.edge",
                    "data_rate_bps": 1_000_000,
                    "propagation_delay_ns": 2_000_000,
                }
            ],
        },
    }
    return _SERVER.ValidatedWorkloadScenario(
        scenario_digest="e" * 64,
        scenario=document,
        assets=(),
    )


def _validate_projection(scenario: object | None = None) -> object:
    return _SERVER._validate_network_projection(
        _projection_scenario() if scenario is None else scenario,
        provider_id="network",
        supported_wifi_standards=("802.11ax",),
        capabilities=_SERVER._required_capabilities(("802.11ax",)),
    )


def test_ns3_projection_closes_entities_endpoints_radios_and_links() -> None:
    projection = _validate_projection()
    assert [node.endpoint_id for node in projection.node_bindings] == [
        "edge.1",
        "uav.1",
    ]
    assert projection.radio_profiles[0].channel_number == 161
    assert projection.radio_profiles[0].band == "BAND_5GHZ"
    assert projection.links[0].data_rate_bps == 1_000_000
    assert projection.projection_digest != "0" * 64


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            lambda raw: raw["network"]["node_bindings"][0].__setitem__(
                "entity_id", "entity.missing"
            ),
            "undeclared scenario entity",
        ),
        (
            lambda raw: raw["network"]["node_bindings"][0].__setitem__(
                "endpoint_id", "uav.1"
            ),
            "bijective",
        ),
        (
            lambda raw: raw["network"]["radio_profiles"][0].__setitem__(
                "provider_id", "flight"
            ),
            "not owned",
        ),
        (
            lambda raw: raw["entities"][0].__setitem__(
                "source_provider_id", "network"
            ),
            "ownership",
        ),
        (
            lambda raw: raw["network"]["radio_profiles"][0].__setitem__(
                "frequency_ghz", 5.8
            ),
            "operating channel",
        ),
        (
            lambda raw: raw["network"]["radio_profiles"][0].__setitem__(
                "tx_power_dbm", float("nan")
            ),
            "finite numeric range",
        ),
        (
            lambda raw: raw["network"]["links"][0].__setitem__(
                "propagation_delay_ns", 2**63
            ),
            "integer",
        ),
        (
            lambda raw: raw["network"]["links"][0].__setitem__(
                "data_rate_bps", True
            ),
            "integer",
        ),
    ),
)
def test_ns3_projection_rejects_unclosed_or_out_of_range_authority(
    mutation, message: str
) -> None:
    scenario = _projection_scenario()
    raw = copy.deepcopy(scenario.scenario)
    mutation(raw)
    forged = _SERVER.ValidatedWorkloadScenario(
        scenario_digest=scenario.scenario_digest,
        scenario=raw,
        assets=(),
    )
    with pytest.raises(_SERVER.ProviderError, match=message):
        _validate_projection(forged)


def test_ns3_physical_endpoints_do_not_require_logical_task_grants() -> None:
    scenario = _projection_scenario()
    raw = copy.deepcopy(scenario.scenario)
    raw["task"]["logical_endpoint_ids"] = []
    raw["task"]["tools"] = []
    declared = _SERVER.ValidatedWorkloadScenario(
        scenario_digest=scenario.scenario_digest, scenario=raw, assets=(),
    )
    projection = _validate_projection(declared)
    assert {node.endpoint_id for node in projection.node_bindings} == {
        "edge.1", "uav.1",
    }
