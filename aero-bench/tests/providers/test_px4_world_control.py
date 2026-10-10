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
    "aero_bench_px4_gazebo_world_control_service", _SERVICE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_SERVICE = importlib.util.module_from_spec(_SPEC)
sys.modules["rpc"] = _RPC
sys.modules[_SPEC.name] = _SERVICE
_SPEC.loader.exec_module(_SERVICE)


def test_world_control_uses_typed_gz_service_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = _SERVICE.RealPx4Stack(tmp_dir=tmp_path)
    config = SimpleNamespace()
    stack._config = config
    calls: list[tuple[tuple[str, ...], object, float | None]] = []

    async def gz_cli(
        arguments: tuple[str, ...], observed_config: object, *, timeout_s: float | None
    ) -> str:
        calls.append((arguments, observed_config, timeout_s))
        return "data: true\n\n"

    monkeypatch.setattr(stack, "_gz_cli", gz_cli)

    asyncio.run(
        stack._gazebo_world_control(
            "/world/default/control", multi_step=32, timeout_s=20.0
        )
    )

    assert calls == [
        (
            (
                "service",
                "--service",
                "/world/default/control",
                "--reqtype",
                "gz.msgs.WorldControl",
                "--reptype",
                "gz.msgs.Boolean",
                "--timeout",
                "20000",
                "--req",
                "pause: true\nmulti_step: 32",
            ),
            config,
            20.0,
        )
    ]


@pytest.mark.parametrize(
    "response",
    ("data: false\n", "", "data: true\ntrailing", "data: true\ndata: true\n"),
)
def test_world_control_rejects_false_or_invalid_cli_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: str
) -> None:
    stack = _SERVICE.RealPx4Stack(tmp_dir=tmp_path)
    stack._config = SimpleNamespace()

    async def gz_cli(
        _arguments: tuple[str, ...], _config: object, *, timeout_s: float | None
    ) -> str:
        assert timeout_s == 1.0
        return response

    monkeypatch.setattr(stack, "_gz_cli", gz_cli)

    with pytest.raises(_SERVICE._GazeboWorldControlAckError) as caught:
        asyncio.run(
            stack._gazebo_world_control(
                "/world/default/control", multi_step=1, timeout_s=1.0
            )
        )

    assert caught.value.lost_reply is False
    if response != "data: false\n":
        assert f"raw stdout={response!r}" in str(caught.value)


def test_world_control_chunks_bursts_without_changing_exact_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = _SERVICE.RealPx4Stack(tmp_dir=tmp_path)
    stack._config = SimpleNamespace(world_name="default")
    controls: list[tuple[str, int, float]] = []
    barriers: list[tuple[int, int | None]] = []

    async def world_control(
        service: str, *, multi_step: int, timeout_s: float
    ) -> None:
        controls.append((service, multi_step, timeout_s))

    async def wait_until(
        target_ns: int, *, minimum_version: int | None = None
    ) -> None:
        barriers.append((target_ns, minimum_version))

    monkeypatch.setattr(stack, "_gazebo_world_control", world_control)
    monkeypatch.setattr(stack, "_wait_until_sim_time", wait_until)
    monkeypatch.setattr(
        stack, "_stats_topic_version_snapshot", lambda: len(controls)
    )

    asyncio.run(
        stack._advance_world_control(
            start_ns=0,
            iterations=257,
            physics_ns=4_000_000,
        )
    )

    assert controls == [
        ("/world/default/control", 128, _SERVICE.WORLD_CONTROL_ACK_TIMEOUT_S),
        ("/world/default/control", 128, _SERVICE.WORLD_CONTROL_ACK_TIMEOUT_S),
        ("/world/default/control", 1, _SERVICE.WORLD_CONTROL_ACK_TIMEOUT_S),
    ]
    assert barriers == [
        (512_000_000, 0),
        (1_024_000_000, 1),
        (1_028_000_000, 2),
    ]


def test_invalid_world_control_ack_is_not_recovered_by_time_barrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = _SERVICE.RealPx4Stack(tmp_dir=tmp_path)
    stack._config = SimpleNamespace(world_name="default")
    calls = 0

    async def gz_cli(
        _arguments: tuple[str, ...], _config: object, *, timeout_s: float | None
    ) -> str:
        nonlocal calls
        calls += 1
        return "data: false\n"

    async def unexpected_barrier(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid ACK must not reach the time barrier")

    monkeypatch.setattr(stack, "_gz_cli", gz_cli)
    monkeypatch.setattr(stack, "_wait_until_sim_time", unexpected_barrier)

    with pytest.raises(_SERVICE._GazeboWorldControlAckError):
        asyncio.run(
            stack._advance_world_control(
                start_ns=0, iterations=1, physics_ns=1
            )
        )

    assert calls == 1


def test_time_barrier_waits_for_first_stats_update(
    tmp_path: Path,
) -> None:
    stack = _SERVICE.RealPx4Stack(tmp_dir=tmp_path)
    stack._config = SimpleNamespace(command_timeout_ms=1_000)
    stack._time_origin_ns = 10_000
    stack._stats_topic_subscribed = True
    stack._stats_topic_active = True

    async def publish_first_stats() -> None:
        await asyncio.sleep(0.01)
        with stack._stats_topic_lock:
            stack._stats_topic_version = 1
            stack._stats_topic_latest = (10_000 + 500_000_000, True)

    async def exercise() -> None:
        publisher = asyncio.create_task(publish_first_stats())
        await stack._wait_until_sim_time(500_000_000)
        await publisher

    asyncio.run(exercise())


def test_lost_world_control_ack_is_reconciled_from_stats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = _SERVICE.RealPx4Stack(tmp_dir=tmp_path)
    stack._config = SimpleNamespace(world_name="default", command_timeout_ms=1_000)
    stack._time_origin_ns = 10_000
    stack._stats_topic_subscribed = True
    stack._stats_topic_active = True
    calls = 0

    async def failed_gz_cli(
        _arguments: tuple[str, ...], _config: object, *, timeout_s: float | None
    ) -> str:
        nonlocal calls
        calls += 1
        raise _SERVICE.Px4ServiceError("native gz service reply was lost")

    async def publish_target() -> None:
        await asyncio.sleep(0.01)
        with stack._stats_topic_lock:
            stack._stats_topic_version = 1
            stack._stats_topic_latest = (10_000 + 4_000_000, True)

    async def exercise() -> None:
        publisher = asyncio.create_task(publish_target())
        await stack._advance_world_control(
            start_ns=0, iterations=1, physics_ns=4_000_000
        )
        await publisher

    monkeypatch.setattr(stack, "_gz_cli", failed_gz_cli)
    asyncio.run(exercise())
    assert calls == 1


def test_world_control_reconciliation_uses_relative_run_time(
    tmp_path: Path,
) -> None:
    stack = _SERVICE.RealPx4Stack(tmp_dir=tmp_path)
    stack._time_origin_ns = 10_000
    stack._stats_topic_subscribed = True
    stack._stats_topic_active = True

    async def publish_target() -> None:
        await asyncio.sleep(0.01)
        with stack._stats_topic_lock:
            stack._stats_topic_version = 1
            stack._stats_topic_latest = (10_000 + 500_000_000, True)

    async def exercise() -> bool:
        publisher = asyncio.create_task(publish_target())
        reached = await stack._world_control_target_reached(
            expected_ns=500_000_000,
            minimum_version=0,
            timeout_s=1.0,
        )
        await publisher
        return reached

    assert asyncio.run(exercise()) is True
