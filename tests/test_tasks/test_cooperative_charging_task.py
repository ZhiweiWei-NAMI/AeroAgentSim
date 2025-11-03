import pytest

from airfogsim.task.cooperative_charging_task import CooperativeChargingTask
from airfogsim.core.cooperative_task import CooperativeTaskState
from airfogsim.core.rendezvous_area import MockRendezvousArea, DockingRendezvousArea

# ---- Mocks ----
class DummyEventRegistry:
    def publish(self, *a, **k):
        pass
    def subscribe(self, *a, **k):
        pass
    def get_event(self, *a, **k):
        class E: pass
        return E()
    def trigger_event(self, *a, **k):
        pass

class DummyAgent:
    def __init__(self, agent_id, pos=(0,0,0)):
        self.id = agent_id
        self._state = {"position": pos}
        self._components = {"cooperative": object()}
    @classmethod
    def get_state_templates(cls):
        return {name: None for name in [s.name for s in CooperativeTaskState]}
    def get_component(self, name):
        return self._components.get(name)
    def get_state(self, key):
        return self._state.get(key)
    def update_states(self, d):
        self._state.update(d)

class DummyEnv:
    def __init__(self):
        self.now = 0.0
        self.event_registry = DummyEventRegistry()
        self.event_bus = None
        self.agent_registry = {}
        self.visual_interval = 1.0
        self.update_interval = 1.0
        self.task_manager = None
    def process(self, gen):
        return gen
    def step(self, dt=1.0):
        self.now += dt

# ---- Helper ----

def drive(task, env, max_steps=50):
    for _ in range(max_steps):
        task.update()
        env.step(1.0)
        if task.state in (CooperativeTaskState.COMPLETED, CooperativeTaskState.FAILED):
            break
    return task.state

# ---- Tests ----

def test_charging_success():
    env = DummyEnv()
    drone = DummyAgent("drone")
    charger = DummyAgent("charger")
    env.agent_registry = {"drone": drone, "charger": charger}
    area = MockRendezvousArea(area_id="dockA", capacity=2)
    area.allow_buffering = False
    task = CooperativeChargingTask(
        env=env,
        agent=drone,
        participants={"initiator": "drone", "recipient": "charger"},
        rendezvous_area=area,
        required_energy=50,
        max_rate=10
    )
    final_state = drive(task, env, max_steps=20)
    assert final_state == CooperativeTaskState.COMPLETED
    assert pytest.approx(task._charged, rel=1e-6) == 50


def test_charging_invalid_required_energy():
    env = DummyEnv()
    drone = DummyAgent("drone")
    charger = DummyAgent("charger")
    env.agent_registry = {"drone": drone, "charger": charger}
    area = MockRendezvousArea(area_id="dockA", capacity=2)
    task = CooperativeChargingTask(
        env=env,
        agent=drone,
        participants={"initiator": "drone", "recipient": "charger"},
        rendezvous_area=area,
        required_energy=0,
        max_rate=10
    )
    # update once to trigger preconditions
    drive(task, env, max_steps=2)
    assert task.state == CooperativeTaskState.FAILED
    assert task.error == "invalid_required_energy"


def test_charging_docking_failure():
    env = DummyEnv()
    drone = DummyAgent("drone")
    charger = DummyAgent("charger")
    env.agent_registry = {"drone": drone, "charger": charger}

    class FailingDock(DockingRendezvousArea):
        def __init__(self):
            super().__init__(area_id="dockFail", capacity=1)
        def verify_docking(self, drone_state):
            return False
        def is_inside(self, agent_id, position):
            return True

    area = FailingDock()
    task = CooperativeChargingTask(
        env=env,
        agent=drone,
        participants={"initiator": "drone", "recipient": "charger"},
        rendezvous_area=area,
        required_energy=20,
        max_rate=10
    )
    drive(task, env, max_steps=5)
    assert task.state == CooperativeTaskState.FAILED
    assert task.error == "dock_verify_failed"


def test_charging_progress_partial():
    env = DummyEnv()
    drone = DummyAgent("drone")
    charger = DummyAgent("charger")
    env.agent_registry = {"drone": drone, "charger": charger}
    area = MockRendezvousArea(area_id="dockA", capacity=2)
    task = CooperativeChargingTask(
        env=env,
        agent=drone,
        participants={"initiator": "drone", "recipient": "charger"},
        rendezvous_area=area,
        required_energy=30,
        max_rate=10
    )
    # 手动推进几步但不让其完成
    for _ in range(2):
        task.update()
        env.step()
    assert task.state in (CooperativeTaskState.EXECUTING, CooperativeTaskState.COMPLETED)
    assert task._charged >= 10


def test_charging_manual_fail_midway():
    env = DummyEnv()
    drone = DummyAgent("drone")
    charger = DummyAgent("charger")
    env.agent_registry = {"drone": drone, "charger": charger}
    area = MockRendezvousArea(area_id="dockA", capacity=2)
    task = CooperativeChargingTask(
        env=env,
        agent=drone,
        participants={"initiator": "drone", "recipient": "charger"},
        rendezvous_area=area,
        required_energy=40,
        max_rate=10
    )
    for _ in range(3):
        task.update()
        env.step()
    # 强制失败
    task.fail("manual_abort")
    assert task.state == CooperativeTaskState.FAILED
    assert task.error == "manual_abort"
