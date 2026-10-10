from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import struct
import zlib
from types import SimpleNamespace

import pytest

from aero_bench.agent.inspection_reference import (
    InspectionReferenceParticipant,
    panel_has_dark_patch,
)
from aero_bench.agent.runtime import AgentContext, AgentFrameworkError
from aero_bench.config.models import NamedValue
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.executor.contracts import AgentWorkloadContract
from aero_bench.gateway.contracts import ObservationEnvelope, ToolResult
from aero_bench.runtime.contracts import CommandReceipt, SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.contracts import rgb_observation_public_payload
from aero_bench.tasks.inspection.formal_v2_contracts import FORMAL_V2_COMPONENT_IDS
from aero_bench.tasks.inspection.image_evidence import (
    decode_rgb_png_pixels,
    decode_strict_rgb_png,
)
from aero_bench.world.resolved import scenario_assets_for_workload
from tools import build_inspection_reference as builder
from tools.build_agent_inspection_images import (
    REFERENCE_COMPONENTS,
    _reference_image_lock_payload,
)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _png(width: int, height: int, pixels: bytes, filter_kind: int = 0) -> bytes:
    stride = width * 3
    encoded = bytearray()
    for row in range(height):
        encoded.append(filter_kind)
        for column in range(stride):
            offset = row * stride + column
            left = pixels[offset - 3] if column >= 3 else 0
            above = pixels[offset - stride] if row else 0
            upper_left = pixels[offset - stride - 3] if row and column >= 3 else 0
            if filter_kind == 0:
                prediction = 0
            elif filter_kind == 1:
                prediction = left
            elif filter_kind == 2:
                prediction = above
            elif filter_kind == 3:
                prediction = (left + above) // 2
            else:
                estimate = left + above - upper_left
                distances = (
                    abs(estimate - left),
                    abs(estimate - above),
                    abs(estimate - upper_left),
                )
                prediction = (left, above, upper_left)[distances.index(min(distances))]
            encoded.append((pixels[offset] - prediction) & 255)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(encoded))
        + _chunk(b"IEND", b"")
    )


def _panel_image(*, damaged: bool) -> bytes:
    width, height = 80, 60
    pixels = bytearray(bytes((170, 190, 210)) * width * height)
    for y in range(22, 38):
        for x in range(30, 50):
            pixels[(y * width + x) * 3 : (y * width + x) * 3 + 3] = bytes((200, 40, 25))
    if damaged:
        for y in range(26, 30):
            for x in range(37, 41):
                pixels[(y * width + x) * 3 : (y * width + x) * 3 + 3] = bytes(
                    (10, 10, 10)
                )
    return _png(width, height, bytes(pixels))


@pytest.mark.parametrize("filter_kind", range(5))
def test_png_pixel_decode_reverses_each_filter(filter_kind: int) -> None:
    pixels = bytes((index * 73 + 11) % 256 for index in range(9 * 7 * 3))
    png = _png(9, 7, pixels, filter_kind)
    assert decode_rgb_png_pixels(png) == (9, 7, pixels)
    assert decode_strict_rgb_png(png) == (9, 7)


def test_reference_classifier_uses_pixels_and_distinguishes_clean_panel() -> None:
    assert panel_has_dark_patch(_panel_image(damaged=True)) is True
    assert panel_has_dark_patch(_panel_image(damaged=False)) is False
    with pytest.raises(AgentFrameworkError, match="no centered red panel"):
        panel_has_dark_patch(_png(80, 60, bytes((10, 10, 10)) * 80 * 60))
    with pytest.raises(ValueError, match="CRC"):
        image = bytearray(_panel_image(damaged=True))
        image[-5] ^= 1
        panel_has_dark_patch(bytes(image))


@pytest.fixture
def reference_bundle(tmp_path: Path) -> Path:
    # Unit-only digests exercise strict compilation; these images are never run.
    revision, _ = builder.urban._managed_source_closure()
    images = {
        component.key: f"localhost:5000/unit-only/{component.key}@sha256:{index:064x}"
        for index, component in enumerate(REFERENCE_COMPONENTS, start=1)
    }
    lock = _reference_image_lock_payload(revision=revision, images=images)
    lock_path = tmp_path / "unit-images-lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    return builder.build(tmp_path / "reference", lock_path)


def test_reference_bundle_resolves_current_contracts_with_all_formal_criteria(
    reference_bundle: Path,
) -> None:
    run = ResolvedRunSpec.model_validate_json(
        (reference_bundle / "resolved-run.json").read_bytes()
    )
    package = json.loads(
        (reference_bundle / "task/inspection-package.json").read_bytes()
    )
    verifier = json.loads((reference_bundle / "configs/verifier.json").read_bytes())
    assert run.execution_scope == "formal_benchmark"
    assert run.task.task_id == builder.PROFILE_ID
    assert run.agents[0].driver is None
    assert {goal["metric_id"] for goal in package["goals"]} == {
        f"inspection.formal.{component}" for component in FORMAL_V2_COMPONENT_IDS
    }
    assert verifier["minimum_horizontal_ground_track_m"] == 50.1
    assert verifier["minimum_detection_f1"] == 1.0
    assert verifier["target_ids"] == ["target.01", "target.05"]
    assert len(package["work_orders"]) == 2
    assert len(run.scenario.task.observations) >= 1
    assert {item.runtime_stage for item in run.scenario.providers} == {
        "motion",
        "network",
        "business_environment",
    }
    assert len(run.scenario.entities) == 47
    assert run.environment.clock.max_steps == builder.MAX_STEPS
    assert (
        package["bounds"]["imaging"]["focal_length_m"]
        == builder.urban.CAMERA_EQUIVALENT_FOCAL_LENGTH_M
    )
    assert (
        package["bounds"]["link"]["minimum_latency_s"]
        == run.scenario.network.links[0].propagation_delay_ns / 1e9
    )
    assert run.scenario.network.links[0].propagation_delay_ns < 5000
    assert (
        next(
            binding
            for binding in run.scenario.network.node_bindings
            if binding.node_id == "node.operations"
        ).entity_id
        == "entity.launch.alpha"
    )
    assert (
        reference_bundle / "private/truth.json"
    ).read_bytes() == builder.urban.canonical_json_bytes(
        json.loads((reference_bundle / "private/truth.json").read_bytes())
    )
    granted = {
        asset.asset_id
        for asset in run.scenario.assets
        if any(
            audience.role == "agent" and audience.workload_id == run.agents[0].agent_id
            for audience in asset.audiences
        )
    }
    assert "asset.reference-inspection-policy" in granted
    assert "asset.inspection-truth" not in granted
    assert "asset.inspection-target-model" not in granted


def test_reference_lock_rejects_extra_model_role_and_placeholder() -> None:
    images = {
        component.key: f"localhost:5000/unit-only/{component.key}@sha256:{index:064x}"
        for index, component in enumerate(REFERENCE_COMPONENTS, start=1)
    }
    with pytest.raises(ValueError, match="exactly its seven"):
        _reference_image_lock_payload(
            revision="a" * 64, images={**images, "agent_driver": images["agent"]}
        )
    images["agent"] = "registry.invalid/agent@sha256:" + "a" * 64
    with pytest.raises(ValueError, match="non-placeholder"):
        _reference_image_lock_payload(revision="a" * 64, images=images)


def test_reference_participant_uses_gateway_frames_and_delivers_exact_artifacts(
    reference_bundle: Path,
    tmp_path: Path,
) -> None:
    run = ResolvedRunSpec.model_validate_json(
        (reference_bundle / "resolved-run.json").read_bytes()
    )
    agent = run.agents[0]
    contract = AgentWorkloadContract(
        schema_version="aero-bench.workload-contract/v5",
        role="agent",
        run_id=run.run_id,
        seed=run.seed,
        clock=run.environment.clock,
        workload_id=agent.agent_id,
        task_id=run.task.task_id,
        instruction=run.task.instruction,
        gateway=run.environment.gateway,
        agent=agent,
        scenario_digest=run.scenario.scenario_digest,
        scenario=run.scenario,
        scenario_assets=scenario_assets_for_workload(
            run.scenario, role="agent", workload_id=agent.agent_id
        ),
    )
    output = tmp_path / "agent-output"
    output.mkdir()
    context = AgentContext(
        contract=contract,
        token="f" * 64,
        gateway_host="unit-only",
        gateway_port=17432,
        bundle_root=reference_bundle,
        artifact_root=output,
    )
    with pytest.raises(AgentFrameworkError, match="not granted"):
        context.asset_bytes("asset.inspection-truth")

    class UnitGateway:
        """Unit fixture, never used as formal Provider evidence."""

        def __init__(self):
            self.current = SimulationTime(tick=0, sim_time_ns=0)
            self.commands = []
            self.capture_count = 0
            self.completed_orders = set()
            self.summaries = {}

        def probe(self):
            return {"current": self.current.model_dump(mode="json")}

        def command(self, *, command_id, tool_id, at, arguments):
            assert at == self.current
            assert command_id in self.summaries
            self.commands.append((command_id, tool_id, arguments, at.tick))
            provider = next(
                item.provider_id for item in agent.tools if item.tool_id == tool_id
            )

            return ToolResult(
                command_id=command_id,
                response=(),
                receipts=(
                    CommandReceipt(
                        run_id=run.run_id,
                        command_id=command_id,
                        provider_id=provider,
                        phase="accepted",
                        time=at,
                    ),
                ),
            )

        def decision_summary(self, *, summary_id, at, summary, command_id):
            assert at == self.current
            assert command_id not in self.summaries
            self.summaries[command_id] = summary

        def command_status(self, *, command_id, at):
            command = next(item for item in self.commands if item[0] == command_id)
            provider = next(
                item.provider_id for item in agent.tools if item.tool_id == command[1]
            )
            return CommandReceipt(
                run_id=run.run_id,
                command_id=command_id,
                provider_id=provider,
                time=at,
                phase="completed" if at.tick > command[3] else "accepted",
            )

        def complete_turn(
            self, *, completion_id, at, disposition, command_ids, observation_ids
        ):
            assert at == self.current
            assert len(command_ids) == len(set(command_ids))
            assert len(observation_ids) == len(set(observation_ids))
            if disposition == "finished":
                return SimpleNamespace(status="terminated", at=at)
            self.current = SimulationTime(
                tick=at.tick + 1, sim_time_ns=at.sim_time_ns + contract.clock.step_ns
            )
            for _identity, tool, arguments, sent_tick in self.commands:
                if tool == "network.send" and self.current.tick > sent_tick:
                    self.completed_orders.add(arguments["work_order_id"])
            return SimpleNamespace(status="advanced", at=self.current)

        def observe(self, *, observation_id, at):
            self.capture_count += 1
            image = _panel_image(damaged=observation_id == "observation.01")
            payload = rgb_observation_public_payload(
                target_id="target." + observation_id.rsplit(".", 1)[1],
                camera_id=builder.urban.SENSOR_ID,
                distance_m=10.0,
                view_angle_deg=0.0,
                frame_id=f"frame.unit.{self.capture_count}",
                vehicle_id=builder.urban.UAV_ID,
                engine_sim_time_ns=at.sim_time_ns + 1_000_000_000,
                logical_origin_engine_ns=1_000_000_000,
                image_sha256=hashlib.sha256(image).hexdigest(),
                size_bytes=len(image),
                selector=f"frames/frame.unit.{self.capture_count}",
                width=80,
                height=60,
                capture_pose_source="gazebo.pose.private_digest",
                camera_pose_sha256="c" * 64,
                target_pose_sha256="d" * 64,
                image_base64=base64.b64encode(image).decode("ascii"),
            )
            return ObservationEnvelope(
                run_id=run.run_id,
                agent_id=agent.agent_id,
                observation_id=observation_id,
                time=at,
                payload_schema=next(
                    item.schema_file
                    for item in agent.observations
                    if item.observation_id == observation_id
                ),
                payload=tuple(
                    NamedValue(name=name, value=value)
                    for name, value in sorted(payload.items())
                ),
                payload_digest=hashlib.sha256(
                    canonical_json_bytes(payload)
                ).hexdigest(),
            )

        def query(self, *, query_id, query_type, at, arguments):
            assert query_type == "business.work-order"
            status = (
                "completed"
                if arguments["work_order_id"] in self.completed_orders
                else "submitted"
            )
            return SimpleNamespace(payload=(NamedValue(name="status", value=status),))

    gateway = UnitGateway()
    participant = InspectionReferenceParticipant(context, gateway)
    participant.run()
    assert gateway.capture_count == 4
    assert len(gateway.summaries) == len(gateway.commands)
    detections = json.loads((output / "agent/detections.json").read_bytes())
    report_bytes = (output / "agent/report.json").read_bytes()
    reports = json.loads(report_bytes)
    assert [item["target_id"] for item in detections] == ["target.01"]
    assert [len(item["detections"]) for item in reports] == [1, 0]
    assert all(
        item["detection_payload_digest"]
        == hashlib.sha256(canonical_json_bytes(detections)).hexdigest()
        for item in reports
    )
    uploads = [
        arguments
        for _identity, tool, arguments, _tick in gateway.commands
        if tool == "network.send"
    ]
    assert len(uploads) == 2
    assert all(
        base64.b64decode(upload["payload_base64"], validate=True) == report_bytes
        for upload in uploads
    )
    assert all(
        upload["payload_sha256"] == hashlib.sha256(report_bytes).hexdigest()
        for upload in uploads
    )
    landing_index = next(
        index
        for index, command in enumerate(gateway.commands)
        if command[1] == "flight.land"
    )
    first_upload_index = next(
        index
        for index, command in enumerate(gateway.commands)
        if command[1] == "network.send"
    )
    assert landing_index < first_upload_index
    assert gateway.commands[-1][1] == "flight.disarm"
