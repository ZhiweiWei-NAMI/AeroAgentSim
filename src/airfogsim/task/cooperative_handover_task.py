from __future__ import annotations
from typing import Dict, Optional

from airfogsim.core.cooperative_task import CooperativeTask, CooperativeTaskState
from airfogsim.core.transfer_item import TransferItem
from airfogsim.core.rendezvous_area import RendezvousArea
from airfogsim.manager.transfer_manager import TransferManager


class CooperativeHandoverTask(CooperativeTask):
    """协同包裹交接任务（最小实现）。"""

    PRODUCED_STATES = [s.name for s in CooperativeTaskState]
    NECESSARY_METRICS = ["progress_rate"]

    def __init__(self, env, agent, *, participants: Dict[str, str],
                 rendezvous_area: RendezvousArea,
                 transfer_manager: TransferManager,
                 item: TransferItem,
                 properties: Optional[Dict] = None):
        props = dict(properties or {})
        props.update({
            "participants": participants,
            "rendezvous_area": rendezvous_area,
            "transfer_manager": transfer_manager,
            "item": item
        })
        super().__init__(env, agent,
                         component_name="cooperative",
                         task_name="handover",
                         properties=props)
        self._transfer_started = False
        self._handover_start_time = None  # type: Optional[float]
        self._last_phase = None

    def send_invitation(self) -> bool:
        return True

    def check_confirmation(self):
        return True

    def _check_preconditions(self) -> bool:
        if not self.item:
            self.fail("no_item")
            return False
        if not self.transfer_manager:
            self.fail("no_transfer_manager")
            return False
        if not self.rendezvous_area:
            self.fail("no_rendezvous_area")
            return False
        if not getattr(self.rendezvous_area, "allow_buffering", False):
            self.fail("area_not_buffering")
            return False
        if "initiator" not in self.participants or "recipient" not in self.participants:
            self.fail("participants_incomplete")
            return False
        return True

    def on_rendezvous_ready(self) -> None:
        pass

    def on_prepare(self) -> bool:
        if not self._transfer_started:
            self.transfer_manager.start_transfer(
                self.item,
                self.participants["initiator"],
                self.participants["recipient"],
                self.rendezvous_area
            )
            self._transfer_started = True
            self._handover_start_time = getattr(self.env, "now", 0.0)
        return True

    def execute_transfer(self) -> float:
        if not self._transfer_started:
            return 0.0
        self.transfer_manager.update_transfer(self.item.item_id)
        ctx = self.transfer_manager.get_context(self.item.item_id)
        if ctx and ctx.phase != self._last_phase:
            self._last_phase = ctx.phase
        return 1.0 if self.transfer_manager.is_finished(self.item.item_id) else 0.5

    def _is_transfer_done(self) -> bool:
        return self.transfer_manager.is_finished(self.item.item_id)

    def on_success(self) -> None:
        pass

    def on_failure(self, reason: str) -> None:
        if self._transfer_started and not self.transfer_manager.is_finished(self.item.item_id):
            self.transfer_manager.abort_transfer(self.item.item_id, reason)