from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aero_bench.providers import rpc as _RPC


_SERVICE_PATH = Path(__file__).parents[2] / "containers" / "px4-gazebo" / "service.py"
_SPEC = importlib.util.spec_from_file_location(
    "aero_bench_px4_gazebo_contact_stream_service", _SERVICE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_SERVICE = importlib.util.module_from_spec(_SPEC)
sys.modules["rpc"] = _RPC
sys.modules[_SPEC.name] = _SERVICE
_SPEC.loader.exec_module(_SERVICE)


class _Collision:
    def __init__(self, name: str, identity: int = 1):
        self.id = identity
        self.name = name


class _Contact:
    def __init__(self, first: str, second: str):
        self.collision1 = _Collision(first)
        self.collision2 = _Collision(second)


class _Contacts:
    def __init__(self, pairs: tuple[tuple[str, str], ...]):
        self.contact = tuple(_Contact(first, second) for first, second in pairs)


def _stack(tmp_path: Path) -> object:
    stack = _SERVICE.RealPx4Stack(tmp_dir=tmp_path)
    stack._config = SimpleNamespace(
        world_name="urban_inspection",
        vehicles=(
            {
                "vehicle_id": "uav.inspector",
                "gazebo_model_name": "x500_mono_cam_0",
            },
        ),
        contact_model_tokens=(
            ("x500_mono_cam_0", "vehicle.uav.inspector"),
            ("ground_plane", "ground.world"),
        ),
    )
    stack._contact_active = True
    stack._contact_subscribed_topics = (
        "/world/urban_inspection/model/x500_mono_cam_0/link/base_link"
        "/sensor/aero_contact_base_link_collision_0/contact",
    )
    stack._contact_window = {"uav.inspector": set()}
    return stack


def test_contacts_message_payload_extracts_collision_names() -> None:
    payload = _SERVICE.RealPx4Stack._contacts_message_payload(
        _Contacts(
            (
                (
                    "x500_mono_cam_0::base_link::base_link_collision_0",
                    "ground_plane::link::collision",
                ),
            )
        )
    )
    assert payload == {
        "contact": [
            {
                "collision1": {
                    "id": 1,
                    "name": "x500_mono_cam_0::base_link::base_link_collision_0",
                },
                "collision2": {
                    "id": 1,
                    "name": "ground_plane::link::collision",
                },
            }
        ]
    }


def test_contact_callback_unions_window_and_clears_on_drain(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    topic = stack._contact_subscribed_topics[0]
    stack._on_contact_topic_message(
        topic,
        _Contacts(
            (
                (
                    "x500_mono_cam_0::base_link::base_link_collision_0",
                    "ground_plane::link::collision",
                ),
            )
        ),
    )
    stack._on_contact_topic_message(topic, _Contacts(()))
    snapshot = asyncio.run(stack._drain_contact_events())
    assert snapshot == {"uav.inspector": ("ground.world",)}
    empty = asyncio.run(stack._drain_contact_events())
    assert empty == {"uav.inspector": ()}


def test_contact_callback_failure_is_fail_closed(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    stack._on_contact_topic_message(
        stack._contact_subscribed_topics[0],
        _Contacts((("not-a-scoped-name", "also-bad"),)),
    )
    with pytest.raises(_SERVICE.Px4ServiceError, match="contact subscriber failed"):
        asyncio.run(stack._drain_contact_events())


def test_contact_topic_bindings_cover_exact_collision_inventory(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    bindings = stack._contact_topic_bindings(stack._config)
    topics = tuple(topic for topic, _, _ in bindings)
    assert len(topics) == 9
    assert len(set(topics)) == 9
    assert all("/sensor/aero_contact_" in topic for topic in topics)
    assert "stdbuf" not in Path(
        _SERVICE_PATH
    ).read_text(encoding="utf-8")
