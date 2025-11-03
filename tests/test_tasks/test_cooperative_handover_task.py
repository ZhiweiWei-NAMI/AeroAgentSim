import pytest

from airfogsim.task.cooperative_handover_task import CooperativeHandoverTask
from airfogsim.core.cooperative_task import CooperativeTaskState
from airfogsim.core.transfer_item import MockTransferItem
from airfogsim.core.rendezvous_area import MockRendezvousArea
from airfogsim.manager.transfer_manager import TransferManager

"""
前置条件校验：缺 item / 缺 transfer_manager / 区域不支持缓冲 / participants 不完整 → 任务 FAIL。
状态机主线：INIT → INVITATION → CONFIRMATION → RENDEZVOUS → EXECUTING → COMPLETED。
TransferManager 驱动：phase 逐步推进至 COMMIT。
会合判定：模拟位置满足与不满足（延迟到位）。
失败处理中止：执行中人为调用 abort → 任务 FAIL。
超时（可选）：模拟到位延迟超过自定义超时 → FAIL（如果你还没实现超时可先跳过）。
重试/拒绝（可选）：覆盖 check_confirmation 返回 False 场景（若你实现了相关逻辑）。
"""


# ---------- 基础 Mock ----------

class DummyEventRegistry:
    def publish(self, *args, **kwargs):
        pass
    def subscribe(self, *args, **kwargs):
        pass

class DummyAgent:
    """Minimal agent stub providing required interfaces for Task base class."""
    def __init__(self, agent_id, pos=(0, 0, 0)):
        self.agent_id = agent_id
        self.id = agent_id  # Task base expects .id
        self._state = {"position": pos}
        # components dict needed by Task.__init__ -> agent.get_component(component_name)
        self._components = {"cooperative": object()}  # placeholder component

    # --- Interfaces used by Task base ---
    @classmethod
    def get_state_templates(cls):  # Return mapping with keys matching PRODUCED_STATES
        # CooperativeHandoverTask.PRODUCED_STATES are CooperativeTaskState names
        return {name: None for name in [s.name for s in CooperativeTaskState]}

    def get_component(self, name):
        return self._components.get(name)

    def get_state(self, key):
        return self._state.get(key)

    def update_states(self, new_states: dict):  # Called by Task execution loop (not heavily used here)
        self._state.update(new_states)

class DummyEnv:
    def __init__(self):
        self.now = 0.0
        self.event_bus = None
        self.event_registry = DummyEventRegistry()  # 新增：Task 基类需要
        self.agent_registry = {}
        self.visual_interval = 1.0
        self.update_interval = 1.0
        self.logger = None  # 若后续代码尝试写日志可替换为简单对象
        self.task_manager = None  # Agent may reference

    # SimPy-like placeholder process API expected by Agent (not used here)
    def process(self, generator):
        return generator
    def step(self, dt=1.0):
        self.now += dt


# ---------- 辅助函数 ----------

def drive(task, env, max_steps=50):
    for _ in range(max_steps):
        task.update()
        env.step(1.0)
        if task.state in (CooperativeTaskState.COMPLETED, CooperativeTaskState.FAILED):
            break
    return task.state


# ---------- 用例 ----------

def test_handover_success_fast_path():
    env = DummyEnv()
    a_src = DummyAgent("src")
    a_dst = DummyAgent("dst")
    env.agent_registry[a_src.agent_id] = a_src
    env.agent_registry[a_dst.agent_id] = a_dst

    area = MockRendezvousArea(area_id="zoneA", capacity=2)
    area.allow_buffering = True  # 确保缓冲开启
    item = MockTransferItem(item_id="p1", item_type="package", quantity=1)
    tm = TransferManager(env)

    task = CooperativeHandoverTask(
        env=env,
        agent=a_src,
        participants={"initiator": "src", "recipient": "dst"},
        rendezvous_area=area,
        transfer_manager=tm,
        item=item
    )

    final_state = drive(task, env, max_steps=20)
    assert final_state == CooperativeTaskState.COMPLETED
    assert tm.is_finished(item.item_id)


def test_handover_missing_item_fail():
    env = DummyEnv()
    a_src = DummyAgent("src")
    a_dst = DummyAgent("dst")
    env.agent_registry.update({"src": a_src, "dst": a_dst})

    area = MockRendezvousArea(area_id="zoneA", capacity=2)
    area.allow_buffering = True
    tm = TransferManager(env)

    # item 传 None 触发 precondition fail
    task = CooperativeHandoverTask(
        env=env,
        agent=a_src,
        participants={"initiator": "src", "recipient": "dst"},
        rendezvous_area=area,
        transfer_manager=tm,
        item=None  # type: ignore
    )
    drive(task, env, max_steps=5)
    assert task.state == CooperativeTaskState.FAILED
    assert task.error == "no_item"


def test_handover_area_not_buffering_fail():
    env = DummyEnv()
    a_src = DummyAgent("src")
    a_dst = DummyAgent("dst")
    env.agent_registry.update({"src": a_src, "dst": a_dst})

    area = MockRendezvousArea(area_id="zoneA", capacity=2)
    area.allow_buffering = False  # 强制不缓冲
    item = MockTransferItem("p2", "package")
    tm = TransferManager(env)

    task = CooperativeHandoverTask(
        env=env,
        agent=a_src,
        participants={"initiator": "src", "recipient": "dst"},
        rendezvous_area=area,
        transfer_manager=tm,
        item=item
    )
    drive(task, env, max_steps=5)
    assert task.state == CooperativeTaskState.FAILED
    assert task.error == "area_not_buffering"


def test_handover_delayed_rendezvous():
    env = DummyEnv()
    a_src = DummyAgent("src")
    a_dst = DummyAgent("dst")
    env.agent_registry.update({"src": a_src, "dst": a_dst})

    # 延迟 3 tick 才允许 is_inside
    area = BlockingRendezvousArea("zoneA", delay_ticks=3)
    area.allow_buffering = True
    item = MockTransferItem("p3", "package")
    tm = TransferManager(env)

    task = CooperativeHandoverTask(
        env=env,
        agent=a_src,
        participants={"initiator": "src", "recipient": "dst"},
        rendezvous_area=area,
        transfer_manager=tm,
        item=item
    )
    final = drive(task, env, max_steps=30)
    assert final == CooperativeTaskState.COMPLETED
    assert tm.is_finished(item.item_id)
    # 验证至少经历过延迟（env.now >= 3）
    assert env.now >= 3.0


def test_handover_abort_midway():
    env = DummyEnv()
    a_src = DummyAgent("src")
    a_dst = DummyAgent("dst")
    env.agent_registry.update({"src": a_src, "dst": a_dst})

    area = MockRendezvousArea(area_id="zoneA", capacity=2)
    area.allow_buffering = True
    item = MockTransferItem("p4", "package")
    tm = TransferManager(env)

    task = CooperativeHandoverTask(
        env=env,
        agent=a_src,
        participants={"initiator": "src", "recipient": "dst"},
        rendezvous_area=area,
        transfer_manager=tm,
        item=item
    )

    # 手动推进几步后中止
    for _ in range(5):
        task.update()
        env.step()
        if task.state == CooperativeTaskState.EXECUTING:
            task.fail("manual_abort")
            break

    assert task.state == CooperativeTaskState.FAILED
    assert task.error == "manual_abort"


class BlockingRendezvousArea(MockRendezvousArea):
    """Rendezvous area that delays inside detection for given ticks."""
    def __init__(self, area_id, delay_ticks=0):
        super().__init__(area_id=area_id, capacity=2)
        self.allow_buffering = True
        self._delay = delay_ticks
        self._tick = 0
    def is_inside(self, agent_id, position):
        inside = self._tick >= self._delay
        self._tick += 1
        return inside