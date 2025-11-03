from __future__ import annotations
from typing import Dict, Optional

from airfogsim.core.cooperative_task import CooperativeTask, CooperativeTaskState
from airfogsim.core.rendezvous_area import RendezvousArea, DockingRendezvousArea


class CooperativeChargingTask(CooperativeTask):
    """
    充电协同任务（流式能量传输，不使用缓冲）:
      - participants: {'initiator': <drone_id>, 'recipient': <charger_id>}
      - rendezvous_area.allow_buffering 可为 False
    properties 需包含:
      required_energy: float  需要补充的能量 (Wh)
      max_rate: float         每次 update 最大传输量 (Wh)
      可选: charging_timeout, rendezvous_timeout
    """

    PRODUCED_STATES = [s.name for s in CooperativeTaskState]
    NECESSARY_METRICS = ["progress_rate"]

    def __init__(self, env, agent, *, participants: Dict[str, str],
                 rendezvous_area: RendezvousArea,
                 required_energy: float,
                 max_rate: float = 10.0,
                 properties: Optional[Dict] = None):
        props = dict(properties or {})
        props.update({
            "participants": participants,
            "rendezvous_area": rendezvous_area,
            "transfer_manager": None,
            "item": None,
            "required_energy": required_energy,
            "max_rate": max_rate
        })
        super().__init__(env, agent,
                         component_name="cooperative",
                         task_name="charging",
                         properties=props)
        self.required_energy = required_energy
        self.max_rate = max_rate
        self._charged = 0.0
        self._charging_started = False

    # ---- 协商阶段 ----
    def send_invitation(self) -> bool:
        return True

    def check_confirmation(self):
        return True

    # ---- 前置条件 ----
    def _check_preconditions(self) -> bool:
        if self.required_energy <= 0:
            self.fail("invalid_required_energy")
            return False
        if "initiator" not in self.participants or "recipient" not in self.participants:
            self.fail("participants_incomplete")
            return False
        if not self.rendezvous_area:
            self.fail("no_rendezvous_area")
            return False
        return True

    def on_rendezvous_ready(self) -> None:
        # 若需要严格停靠
        if isinstance(self.rendezvous_area, DockingRendezvousArea):
            # 这里应获取真实状态；简化为直接通过
            dummy_state = {"speed": 0.0, "position_z": 0.0}
            if not self.rendezvous_area.verify_docking(dummy_state):
                self.fail("dock_verify_failed")

    def on_prepare(self) -> bool:
        self._charging_started = True
        return True

    def execute_transfer(self) -> float:
        if not self._charging_started:
            return 0.0
        remain = self.required_energy - self._charged
        if remain <= 0:
            return 1.0
        step = min(self.max_rate, remain)
        # 真实情况下：更新参与设备能量组件
        self._charged += step
        return min(1.0, self._charged / self.required_energy)

    def _is_transfer_done(self) -> bool:
        return self._charged >= self.required_energy

    def on_success(self) -> None:
        pass

    def on_failure(self, reason: str) -> None:
        # 可回滚或记录失败原因
        pass