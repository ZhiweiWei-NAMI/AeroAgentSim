from __future__ import annotations

import asyncio
import base64
import hashlib

import pytest

from aero_bench.config.models import (
    ArtifactRequirement,
    FileRef,
    ImplementationIdentity,
    NamedValue,
)
from aero_bench.providers.contracts import ProviderManifest
from aero_bench.providers.ns3 import (
    BUILDING_ATTENUATION_DB,
    LOG_DISTANCE_EXPONENT,
    NETWORK_MODEL,
    PROPAGATION_MODEL,
    PROTOCOL_VERSION,
    RX_NOISE_FIGURE_DB,
    STATE_SCHEMA,
    THERMAL_NOISE_DENSITY_DBM_HZ,
    NetworkMessage,
    NetworkQos,
    NetworkTraceRecord,
    Ns3Config,
    Ns3Provider,
    Ns3ProviderError,
    SoftwareIdentity,
    ns3_capabilities,
)
from aero_bench.providers.ns3.protocol import MAX_NETWORK_PAYLOAD_BYTES
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.runtime.contracts import (
    CommandRequest,
    ProviderEvent,
    ProviderFinalizationRequest,
    SimulationTime,
    StepReceipt,
)
from tests.providers.ns3_support import AGENT_ID, network_request, network_scenario


RUN_ID = "a" * 64
SESSION_TOKEN = "c" * 64


def artifact_requirement() -> ArtifactRequirement:
    return ArtifactRequirement(
        artifact_id="artifact.network.delivery",
        artifact_type="network.delivery",
        producer_id="network",
        visibility="private",
        relative_path="evidence/network-delivery.json",
        max_size_bytes=65_536,
        source_asset_id=None,
    )


def manifest() -> ProviderManifest:
    return ProviderManifest(
        provider_id="network",
        adapter="ns3.rpc",
        implementation=ImplementationIdentity(
            component_id="ns3.rpc",
            kind="mechanical_fixture",
            source_uri="https://github.com/moby/moby",
            source_revision="4aeab8310c1c8bf6c2c7b255ae1abc2c767bb556",
            version="test-fixture-1",
        ),
        runtime_image="registry.test/ns3@sha256:" + "1" * 64,
        config_digest="f" * 64,
        capabilities=ns3_capabilities(("802.11ax",)),
        protocol_schema=FileRef(path="ns3.json", sha256="f" * 64),
        artifact_requirements=(artifact_requirement(),),
    )


def config() -> Ns3Config:
    return Ns3Config(
        schema_version="aero-bench.ns3/v3",
        provider_id="network",
        ns3=SoftwareIdentity(version="3.42", commit="b" * 40),
        network_model=NETWORK_MODEL,
        supported_wifi_standards=("802.11ax",),
        required_commands=("ns3",),
        command_timeout_ms=1000,
    )


def readiness() -> dict[str, object]:
    return {
        "status": "ready",
        "provider_id": "network",
        "protocol_version": PROTOCOL_VERSION,
        "runtime_image": manifest().runtime_image,
        "config_digest": manifest().config_digest,
        "scenario_digest": network_scenario().scenario_digest,
        "network_projection_digest": "d" * 64,
        "network_model": NETWORK_MODEL,
        "propagation_model": PROPAGATION_MODEL,
        "log_distance_exponent": LOG_DISTANCE_EXPONENT,
        "building_attenuation_db": BUILDING_ATTENUATION_DB,
        "rx_noise_figure_db": RX_NOISE_FIGURE_DB,
        "thermal_noise_density_dbm_hz": THERMAL_NOISE_DENSITY_DBM_HZ,
        "propagation_volume_count": 1,
        "clock_step_ns": 100,
        "clock_max_steps": 10,
        "supported_wifi_standards": ["802.11ax"],
        "radio_profile_count": 1,
        "node_count": 2,
        "link_count": 1,
        "ns3_version": config().ns3.version,
        "ns3_commit": config().ns3.commit,
    }


def test_ns3_rejects_payload_digest_mismatch() -> None:
    with pytest.raises(ValueError, match="payload_sha256"):
        NetworkMessage(
            message_id="message.1",
            source="uav.1",
            destination="edge.1",
            payload_base64=base64.b64encode(b"hello").decode(),
            payload_sha256="e" * 64,
            qos=NetworkQos(
                traffic_class="best_effort", priority=0, reliability="best_effort"
            ),
            send_time=SimulationTime(tick=1, sim_time_ns=100),
        )


def test_ns3_accepts_the_declared_maximum_payload_and_rejects_overflow() -> None:
    payload = b"p" * MAX_NETWORK_PAYLOAD_BYTES
    message = NetworkMessage(
        message_id="message.max-payload",
        source="uav.1",
        destination="edge.1",
        payload_base64=base64.b64encode(payload).decode(),
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        qos=NetworkQos(
            traffic_class="best_effort", priority=0, reliability="best_effort"
        ),
        send_time=SimulationTime(tick=1, sim_time_ns=100),
    )
    assert base64.b64decode(message.payload_base64, validate=True) == payload
    oversized = payload + b"x"
    with pytest.raises(ValueError, match="exceeds"):
        NetworkMessage(
            message_id="message.oversized-payload",
            source="uav.1",
            destination="edge.1",
            payload_base64=base64.b64encode(oversized).decode(),
            payload_sha256=hashlib.sha256(oversized).hexdigest(),
            qos=NetworkQos(
                traffic_class="best_effort", priority=0, reliability="best_effort"
            ),
            send_time=SimulationTime(tick=1, sim_time_ns=100),
        )


def test_ns3_rejects_arrival_tick_before_send_tick() -> None:
    with pytest.raises(ValueError, match="arrival precedes send time"):
        NetworkTraceRecord(
            run_id=RUN_ID,
            message_id="message.1",
            source="uav.1",
            destination="edge.1",
            payload_sha256=hashlib.sha256(b"hello").hexdigest(),
            send_time=SimulationTime(tick=2, sim_time_ns=0),
            outcome="delivered",
            arrival_time=SimulationTime(tick=1, sim_time_ns=75),
        )


def test_ns3_rejects_unimplemented_qos_semantics() -> None:
    with pytest.raises(ValueError, match="best_effort"):
        NetworkQos(traffic_class="telemetry", priority=0, reliability="best_effort")


def test_ns3_provider_accepts_public_delivery_requirement() -> None:
    public_requirement = artifact_requirement().model_copy(
        update={"visibility": "public"}
    )
    provider = Ns3Provider(
        config=config(),
        manifest=manifest().model_copy(
            update={"artifact_requirements": (public_requirement,)}
        ),
        runtime_endpoint=RuntimeEndpoint(host="runtime-ns3", port=17433),
        run_id=RUN_ID,
        session_token=SESSION_TOKEN,
        scenario=network_scenario(),
    )
    assert provider._artifact_requirements()[0]["visibility"] == "public"


def test_ns3_provider_finalizes_against_exact_terminal_root() -> None:
    class ScriptedTransport:
        def __init__(self) -> None:
            self.requests: list[tuple[str, dict[str, object]]] = []
            self.artifact_id = artifact_requirement().artifact_id
            self.closed = False

        async def request(self, operation, payload):
            document = dict(payload)
            self.requests.append((operation, document))
            if operation == "prepare":
                return readiness()
            if operation == "reset":
                reached = SimulationTime(tick=0, sim_time_ns=0)
                return {
                    "receipt": StepReceipt(
                        run_id=RUN_ID,
                        provider_id="network",
                        reached=reached,
                        state_digest="e" * 64,
                        events=(
                            ProviderEvent(
                                provider_id="network",
                                event_id="state.0",
                                time=reached,
                                payload_schema_id=STATE_SCHEMA,
                            ),
                        ),
                    ).model_dump(mode="json")
                }
            if operation == "finalize":
                finalization = document["request"]
                return {
                    "receipt": {
                        "schema_version": (
                            "aero-bench.provider-finalization-receipt/v1"
                        ),
                        "run_id": RUN_ID,
                        "provider_id": "network",
                        "event_chain_root": finalization["event_chain_root"],
                        "artifacts": [
                            {
                                "artifact_id": self.artifact_id,
                                "sha256": "f" * 64,
                                "size_bytes": 128,
                            }
                        ],
                    }
                }
            if operation == "shutdown":
                return {"status": "stopped"}
            raise AssertionError(f"unexpected operation: {operation}")

        async def close(self) -> None:
            self.closed = True

    async def scenario() -> None:
        provider = Ns3Provider(
            config=config(),
            manifest=manifest(),
            runtime_endpoint=RuntimeEndpoint(host="runtime-ns3", port=17433),
            run_id=RUN_ID,
            session_token=SESSION_TOKEN,
            scenario=network_scenario(),
        )
        transport = ScriptedTransport()
        provider._transport = transport
        await provider.prepare()
        await provider.reset(seed=7)
        request = ProviderFinalizationRequest(
            schema_version="aero-bench.provider-finalization-request/v1",
            run_id=RUN_ID,
            terminal_event="run.completed",
            terminal_time=SimulationTime(tick=0, sim_time_ns=0),
            event_chain_root="d" * 64,
        )
        receipt = await provider.finalize(request)
        assert receipt.event_chain_root == request.event_chain_root
        operation, frame = transport.requests[-1]
        assert operation == "finalize"
        assert frame["request"] == request.model_dump(mode="json")
        assert frame["session_token"] == SESSION_TOKEN

        transport.artifact_id = "artifact.other"
        with pytest.raises(Ns3ProviderError, match="receipt identity"):
            await provider.finalize(request)
        await provider.shutdown()
        assert transport.closed

    asyncio.run(scenario())


def _network_send_request(
    *,
    command_id: str = "command.send",
    message_id: str = "message.1",
    work_order_id: str = "wo.1",
) -> CommandRequest:
    payload = b"inspection-report"
    return CommandRequest(
        run_id=RUN_ID,
        command_id=command_id,
        agent_id=AGENT_ID,
        tool_id="network.send",
        issued_at=SimulationTime(tick=0, sim_time_ns=0),
        arguments=(
            NamedValue(name="work_order_id", value=work_order_id),
            NamedValue(name="message_id", value=message_id),
            NamedValue(name="source", value="uav.1"),
            NamedValue(name="destination", value="edge.1"),
            NamedValue(name="payload_base64", value=base64.b64encode(payload).decode()),
            NamedValue(
                name="payload_sha256", value=hashlib.sha256(payload).hexdigest()
            ),
            NamedValue(name="traffic_class", value="best_effort"),
            NamedValue(name="priority", value=0),
            NamedValue(name="reliability", value="best_effort"),
        ),
    )


class _NetworkTransport:
    def __init__(self, *, arrival_time_ns: int = 75) -> None:
        self.requests: list[tuple[str, dict[str, object]]] = []
        self.arrival_time_ns = arrival_time_ns
        self.message: dict[str, object] | None = None

    async def request(self, operation, payload):
        document = dict(payload)
        self.requests.append((operation, document))
        if operation == "prepare":
            return readiness()
        if operation == "reset":
            reached = SimulationTime(tick=0, sim_time_ns=0)
            return {
                "receipt": StepReceipt(
                    run_id=RUN_ID,
                    provider_id="network",
                    reached=reached,
                    state_digest="e" * 64,
                    events=(
                        ProviderEvent(
                            provider_id="network",
                            event_id="state.0",
                            time=reached,
                            payload_schema_id=STATE_SCHEMA,
                        ),
                    ),
                ).model_dump(mode="json")
            }
        if operation == "submit_message":
            self.message = document["message"]
            assert isinstance(self.message, dict)
            return {
                "submission": {
                    "run_id": RUN_ID,
                    "work_order_id": document["work_order_id"],
                    "message_id": self.message["message_id"],
                    "source": self.message["source"],
                    "destination": self.message["destination"],
                    "payload_sha256": self.message["payload_sha256"],
                    "send_time": self.message["send_time"],
                    "status": "queued",
                }
            }
        if operation == "step_stage":
            request = document["request"]
            target = request["target"]
            evidence = {
                "accepted_scene_state_digest": request["scene_state_digest"],
                "motion_predecessor_barrier_digest": request["predecessor_barriers"][0]["barrier_digest"],
                "staged_positions_applied": True,
                "staged_position_binding": "scene-state-constant-position-mobility/v1",
                "network_model": NETWORK_MODEL,
                "network_projection_digest": "d" * 64,
                "link_state_count": 1,
                **{key: "f" * 64 for key in (
                    "staged_node_positions_digest", "staged_link_attenuations_digest",
                    "staged_link_obstructions_digest", "link_state_digest",
                )},
            }
            return {
                "input_scene_state_digest": request["scene_state_digest"],
                "predecessor_barriers": request["predecessor_barriers"],
                "deliveries": self.deliveries(),
                "receipt": StepReceipt(
                    run_id=RUN_ID,
                    provider_id="network",
                    reached=SimulationTime.model_validate(target),
                    state_digest="d" * 64,
                    events=(
                        ProviderEvent(
                            provider_id="network",
                            event_id="state.1",
                            time=SimulationTime.model_validate(target),
                            payload_schema_id=STATE_SCHEMA,
                            payload=tuple(NamedValue(name=name, value=value)
                                          for name, value in sorted(evidence.items())),
                        ),
                    ),
                ).model_dump(mode="json")
            }
        raise AssertionError(f"unexpected operation: {operation}")

    def deliveries(self):
        assert self.message is not None
        return [
                    {
                        "work_order_id": "wo.1",
                        "trace": {
                            "run_id": RUN_ID,
                            "message_id": self.message["message_id"],
                            "source": self.message["source"],
                            "destination": self.message["destination"],
                            "payload_sha256": self.message["payload_sha256"],
                            "send_time": self.message["send_time"],
                            "outcome": "delivered",
                            "arrival_time": {
                                "tick": 1,
                                "sim_time_ns": self.arrival_time_ns,
                            },
                            "path": [],
                        },
                        "payload_base64": self.message["payload_base64"],
                    }
        ]

    async def close(self) -> None:
        pass


class _UnsortedNetworkTransport(_NetworkTransport):
    def __init__(self) -> None:
        super().__init__()
        self.submissions: list[tuple[dict[str, object], str]] = []

    async def request(self, operation, payload):
        document = dict(payload)
        if operation == "submit_message":
            message = dict(document["message"])
            work_order_id = document["work_order_id"]
            assert isinstance(work_order_id, str)
            response = await super().request(operation, payload)
            self.submissions.append((message, work_order_id))
            return response
        return await super().request(operation, payload)

    def deliveries(self):
        return [
                    {
                        "work_order_id": work_order_id,
                        "trace": {
                            "run_id": RUN_ID,
                            "message_id": message["message_id"],
                            "source": message["source"],
                            "destination": message["destination"],
                            "payload_sha256": message["payload_sha256"],
                            "send_time": message["send_time"],
                            "outcome": "delivered",
                            "arrival_time": {"tick": 1, "sim_time_ns": 75},
                            "path": [],
                        },
                        "payload_base64": message["payload_base64"],
                    }
                    for message, work_order_id in reversed(self.submissions)
        ]


async def _ready_network_provider(
    transport: _NetworkTransport,
) -> Ns3Provider:
    provider = Ns3Provider(
        config=config(),
        manifest=manifest(),
        runtime_endpoint=RuntimeEndpoint(host="runtime-ns3", port=17433),
        run_id=RUN_ID,
        session_token=SESSION_TOKEN,
        scenario=network_scenario(),
    )
    provider._transport = transport
    await provider.prepare()
    await provider.reset(seed=7)
    return provider


def test_network_send_receipt_is_distinct_from_real_delivery() -> None:
    async def scenario() -> None:
        transport = _NetworkTransport()
        provider = await _ready_network_provider(transport)
        request = _network_send_request()

        result = await provider.handle_command(request)

        assert [receipt.phase for receipt in result.receipts] == [
            "received",
            "accepted",
            "applied",
            "completed",
        ]
        assert result.events == ()
        assert [operation for operation, _ in transport.requests[-1:]] == [
            "submit_message"
        ]
        submission = transport.requests[-1][1]
        assert submission["work_order_id"] == "wo.1"
        assert submission["message"]["send_time"] == {
            "tick": 0,
            "sim_time_ns": 0,
        }

        with pytest.raises(Ns3ProviderError, match="command_id was already applied"):
            await provider.handle_command(request)

        staged = await provider.step_stage(network_request())
        receipt = staged.step_receipt
        assert transport.requests[-1][0] == "step_stage"
        assert all(operation != "release_deliveries" for operation, _ in transport.requests)
        assert len(receipt.events) == 2
        delivery = receipt.events[1]
        assert delivery.event_id == "network.delivery"
        assert delivery.time == SimulationTime(tick=1, sim_time_ns=100)
        assert {item.name: item.value for item in delivery.payload} == {
            "schema_id": "inspection.network-delivery.v1",
            "run_id": RUN_ID,
            "provider_id": "network",
            "command_id": "command.send",
            "agent_id": AGENT_ID,
            "source_artifact_id": "artifact.network.delivery",
            "work_order_id": "wo.1",
            "message_id": "message.1",
            "payload_digest": hashlib.sha256(b"inspection-report").hexdigest(),
            "sent_tick": 0,
            "sent_time_ns": 0,
            "delivered_tick": 1,
            "delivered_time_ns": 75,
        }

    asyncio.run(scenario())


def test_ns3_provider_rejects_delivery_released_before_arrival() -> None:
    async def scenario() -> None:
        provider = await _ready_network_provider(_NetworkTransport(arrival_time_ns=101))
        await provider.handle_command(_network_send_request())
        with pytest.raises(Ns3ProviderError, match="before its arrival"):
            await provider.step_stage(network_request())

    asyncio.run(scenario())


def test_ns3_provider_rejects_unsorted_delivery_response() -> None:
    async def scenario() -> None:
        provider = await _ready_network_provider(_UnsortedNetworkTransport())
        await provider.handle_command(
            _network_send_request(command_id="command.1", message_id="message.1")
        )
        await provider.handle_command(
            _network_send_request(command_id="command.2", message_id="message.2")
        )
        with pytest.raises(Ns3ProviderError, match="ordered by message_id"):
            await provider.step_stage(network_request())

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field",
    (
        "runtime_image",
        "endpoint",
        "protocol_version",
        "port",
        "protocol",
        "deployment",
    ),
)
def test_ns3_config_rejects_deployment_fields(field: str) -> None:
    raw = config().model_dump(mode="json")
    raw[field] = {
        "runtime_image": {"image": "registry.test/ns3@sha256:" + "1" * 64},
        "endpoint": {"host": "ns3-provider", "port": 17433},
        "protocol_version": PROTOCOL_VERSION,
        "port": 17433,
        "protocol": "tcp",
        "deployment": {"kind": "docker"},
    }[field]
    with pytest.raises(ValueError):
        Ns3Config.model_validate(raw)
