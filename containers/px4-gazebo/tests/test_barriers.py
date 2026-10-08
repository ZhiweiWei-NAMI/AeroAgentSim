"""An accepted control request cannot replace exact native boundary observations."""

import asyncio
from types import SimpleNamespace

import pytest
from service.commands import Commands
from service.transport import Gazebo

STEP = 4_000_000


def stats(ns, paused):
    return SimpleNamespace(
        sim_time=SimpleNamespace(sec=ns // 10**9, nsec=ns % 10**9),
        paused=paused,
        HasField=lambda name: name == "sim_time",
    )


def pose(ns):
    return SimpleNamespace(
        header=SimpleNamespace(
            stamp=SimpleNamespace(sec=ns // 10**9, nsec=ns % 10**9),
            HasField=lambda name: name == "stamp",
        ),
        HasField=lambda name: name == "header",
        pose=[
            SimpleNamespace(
                name="v0",
                position=SimpleNamespace(x=1, y=2, z=3),
                orientation=SimpleNamespace(x=0, y=0, z=0, w=1),
                HasField=lambda name: name in ("position", "orientation"),
            )
        ],
    )


def setup_gazebo(ack=b'{"result":true,"data":true}\n'):
    gazebo = Gazebo("isolated", "world", ["v0"])
    gazebo.loop = asyncio.get_running_loop()
    written = []

    async def drain():
        pass

    async def readline():
        return ack

    gazebo.control_process = SimpleNamespace(
        returncode=None,
        stdin=SimpleNamespace(write=written.append, drain=drain),
        stdout=SimpleNamespace(readline=readline),
    )
    phases = {"stats": asyncio.Event(), "pose": asyncio.Event()}
    native_wait = gazebo.wait

    async def entered(predicate, timeout, label):
        phases["stats" if label.startswith("WorldStatistics") else "pose"].set()
        await native_wait(predicate, timeout, label)

    gazebo.wait = entered
    gazebo.on_stats(stats(0, True))
    gazebo.on_pose(pose(0))
    return gazebo, phases, written


def test_ack_unpaused_stats_and_stale_pose_each_keep_barrier_closed():
    async def scenario():
        gazebo, phases, written = setup_gazebo()
        task = asyncio.create_task(gazebo.step(1, STEP))
        await asyncio.wait_for(phases["stats"].wait(), 1)
        assert not task.done()  # acknowledgment is already accepted
        gazebo.on_stats(stats(STEP, False))
        await asyncio.sleep(0)
        assert not task.done()
        assert not phases["pose"].is_set()
        gazebo.on_stats(stats(STEP, True))
        await asyncio.wait_for(phases["pose"].wait(), 1)
        assert not task.done()  # source pose remains at 0
        gazebo.on_pose(pose(STEP))
        await asyncio.wait_for(task, 1)
        assert gazebo.sim_ns == STEP
        assert (await gazebo.sample("v0"))["sim_ns"] == STEP
        assert written == [b"1\n"]

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["overshoot", "subscription", "missing_timestamp"])
def test_fault_wakes_barrier_without_resending_world_control(failure):
    async def scenario():
        gazebo, phases, written = setup_gazebo()
        task = asyncio.create_task(gazebo.step(1, STEP))
        await asyncio.wait_for(phases["stats"].wait(), 1)
        if failure == "overshoot":
            gazebo.on_stats(stats(2 * STEP, True))
        elif failure == "subscription":
            await asyncio.to_thread(gazebo.fail, ValueError("broken source"))
        else:
            gazebo.on_stats(SimpleNamespace(HasField=lambda field: False))
        with pytest.raises(
            RuntimeError, match="overshot|broken source|omitted sim_time"
        ):
            await asyncio.wait_for(task, 1)
        assert written == [b"1\n"]
        assert gazebo.sim_ns == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("ack", [b'{"result":false,"data":false}\n', b""])
def test_control_rejection_and_eof_are_not_retried(ack):
    async def scenario():
        gazebo, _, written = setup_gazebo(ack)
        with pytest.raises(RuntimeError, match="no retry"):
            await gazebo.step(1, STEP)
        assert written == [b"1\n"]
        assert gazebo.sim_ns == 0

    asyncio.run(scenario())


def test_event_wait_and_empty_dispatch_do_not_poll_or_sleep(monkeypatch):
    async def scenario():
        gazebo = Gazebo("isolated", "world", [])
        gazebo.loop = asyncio.get_running_loop()
        entered = asyncio.Event()

        async def no_sleep(*args):
            raise AssertionError("unexpected polling or empty dispatch sleep")

        monkeypatch.setattr(asyncio, "sleep", no_sleep)

        def predicate():
            entered.set()
            return gazebo.stats == (STEP, True)

        task = asyncio.create_task(gazebo.wait(predicate, 1, "source"))
        await entered.wait()
        gazebo.on_stats(stats(STEP, True))
        await asyncio.wait_for(task, 1)
        await Commands(SimpleNamespace(), SimpleNamespace(), {}).dispatch(0)

    asyncio.run(scenario())
