from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import Dict, Optional

from airfogsim.core.task import Task


class CooperativeTaskState(Enum):
    """协同任务对外可见的主要状态阶段."""

    INIT = auto()
    INVITATION = auto()
    CONFIRMATION = auto()
    RENDEZVOUS = auto()
    EXECUTING = auto()
    COMPLETED = auto()
    FAILED = auto()


class CooperativeTask(Task, ABC):
    """
    协同任务统一骨架，提供“初始化→邀约→确认→会合→执行→结束”的状态机。
    对外呈现 7 个主状态，更细的子阶段（如 PREPARE / TRANSFER）可在子类里用内部标志实现。
    """

    def __init__(self, env, agent, component_name, task_name,
                 workflow_id=None, target_state=None, properties=None):
        super().__init__(env, agent, component_name, task_name, workflow_id, target_state, properties)
        props = properties or {}

        self.state = CooperativeTaskState.INIT
        self.error = None  # type: Optional[str]
        self.progress = 0.0
        self.participants = props.get("participants", {})  # type: Dict[str, str]
        self.rendezvous_area = props.get("rendezvous_area")
        self.transfer_manager = props.get("transfer_manager")
        self.item = props.get("item")

        # 协商/邀约阶段配置
        self.invitation_timeout = float(props.get("invitation_timeout", 0.0))
        self.confirmation_timeout = float(props.get("confirmation_timeout", 0.0))
        self.invitation_retry_delay = float(props.get("invitation_retry_delay", 0.0))
        self.max_invitation_attempts = max(1, int(props.get("invitation_attempts", 1)))

        self._prepared = False
        self._invitation_attempts = 0
        self._next_invitation_time = 0.0
        self._confirmation_deadline = None
        self._awaiting_confirmation = False

    # ------- 生命周期 API -------
    def init(self) -> None:
        if not self._check_preconditions():
            self.fail("precondition_failed")

    def start(self) -> None:
        if self.state != CooperativeTaskState.INIT:
            return
        self.init()
        if self.state != CooperativeTaskState.INIT:
            return
        self._next_invitation_time = self._now()
        self.state = CooperativeTaskState.INVITATION

    def update(self) -> None:
        progressed = True
        while progressed:
            progressed = False

            if self.state in (CooperativeTaskState.COMPLETED, CooperativeTaskState.FAILED):
                return

            if self.state == CooperativeTaskState.INIT:
                previous = self.state
                self.start()
                progressed = self.state != previous
                continue

            if self.state == CooperativeTaskState.INVITATION:
                previous = self.state
                self._step_invitation_phase()
                progressed = self.state != previous
                continue

            if self.state == CooperativeTaskState.CONFIRMATION:
                previous = self.state
                self._step_confirmation_phase()
                progressed = self.state != previous
                continue

            if self.state == CooperativeTaskState.RENDEZVOUS:
                if self._check_rendezvous_ready():
                    # 调用会合回调，允许子类在其中直接 fail/complete
                    self.on_rendezvous_ready()
                    # 若回调中已失败或完成，终止后续流转
                    if self.state in (CooperativeTaskState.FAILED, CooperativeTaskState.COMPLETED):
                        return
                    # 否则进入执行阶段
                    self.state = CooperativeTaskState.EXECUTING
                    progressed = True
                else:
                    return
                continue

            if self.state == CooperativeTaskState.EXECUTING:
                self._execute()
                if self._is_transfer_done():
                    self.complete()
                return

            return

    def terminate(self, reason: Optional[str] = None) -> None:
        self.fail(reason or "terminated")

    def get_status(self) -> Dict[str, str]:
        return {
            "state": self.state.name,
            "error": self.error,
            "progress": f"{self.progress:.2f}",
        }

    # ------- Task 接口适配 -------
    def _update_task_state(self, performance_metrics: Dict) -> None:
        self.update()

    # ------- 状态流转 -------
    def complete(self) -> None:
        if self.state != CooperativeTaskState.COMPLETED:
            self.state = CooperativeTaskState.COMPLETED
            self.on_success()

    def fail(self, reason: str) -> None:
        if self.state in (CooperativeTaskState.COMPLETED, CooperativeTaskState.FAILED):
            return
        self.state = CooperativeTaskState.FAILED
        self.error = reason
        self.on_failure(reason)

    def _execute(self) -> None:
        if not self._prepare_if_needed():
            return
        self.progress = self.execute_transfer()
        if self.progress is None:
            self.progress = 0.0

    def _prepare_if_needed(self) -> bool:
        if self._prepared:
            return True
        ready = self.on_prepare()
        self._prepared = ready
        return ready

    def _step_invitation_phase(self) -> None:
        if self.state != CooperativeTaskState.INVITATION:
            return
        if self._now() < self._next_invitation_time:
            return

        if self._invitation_attempts >= self.max_invitation_attempts:
            self.fail("invitation_attempts_exhausted")
            return

        self._invitation_attempts += 1
        sent = self.send_invitation()
        if sent:
            self._awaiting_confirmation = True
            self.on_invitation_sent()
            self.state = CooperativeTaskState.CONFIRMATION
        else:
            if self._invitation_attempts >= self.max_invitation_attempts:
                self.fail("invitation_failed")
            else:
                self._schedule_invitation_retry("invitation_send_failed")

    def _step_confirmation_phase(self) -> None:
        if self.state != CooperativeTaskState.CONFIRMATION:
            return

        decision = self.check_confirmation()
        if decision is True:
            self._awaiting_confirmation = False
            self._confirmation_deadline = None
            self.on_invitation_accepted()
            self.state = CooperativeTaskState.RENDEZVOUS
            return

        if decision is False:
            self._awaiting_confirmation = False
            self.on_invitation_rejected("rejected")
            if self._invitation_attempts >= self.max_invitation_attempts:
                self.fail("invitation_rejected")
            else:
                self._schedule_invitation_retry("invitation_rejected")
            return

        # decision is None -> still waiting
        deadline = self._confirmation_deadline or self._deadline(self.confirmation_timeout)
        self._confirmation_deadline = deadline
        if deadline and self._now() > deadline:
            self._awaiting_confirmation = False
            self.on_invitation_timeout()
            if self._invitation_attempts >= self.max_invitation_attempts:
                self.fail("confirmation_timeout")
            else:
                self._schedule_invitation_retry("confirmation_timeout")

    def _check_rendezvous_ready(self) -> bool:
        if not self.rendezvous_area:
            return True
        checker = getattr(self.env, "agent_registry", None)
        for role, agent_id in self.participants.items():
            if checker: #有rendezvous区域则逐个参与者查询 position（需环境能通过 agent_registry 找到 agent）
                agent = checker.get(agent_id)
                position = agent.get_state("position") if agent else None
            else:
                position = self.agent.get_state("position")
            if position is None:
                return False
            if not self.rendezvous_area.is_inside(agent_id, tuple(position)):
                return False
        return True

    def _schedule_invitation_retry(self, reason: str) -> None:
        self.on_invitation_retry(reason)
        self._next_invitation_time = self._now() + self.invitation_retry_delay
        self.state = CooperativeTaskState.INVITATION
        self._confirmation_deadline = None

    # ------- 子类 Hooks -------
    @abstractmethod
    def _check_preconditions(self) -> bool:
        """启动前检查是否具备执行条件。"""

    def on_prepare(self) -> bool:
        return True

    def on_rendezvous_ready(self) -> None:
        """会合完成后回调。"""

    def send_invitation(self) -> bool:
        """发起邀约，返回是否成功发送。默认立即成功。"""

        return True

    def on_invitation_sent(self) -> None:
        """邀约发送成功的回调。"""

    def check_confirmation(self) -> Optional[bool]:
        """轮询确认结果。

        返回值:
            True: 接收方确认接受
            False: 接收方拒绝
            None: 尚未得到结果
        默认立即接受，子类可以重写实现真实协商。
        """

        return True

    def on_invitation_accepted(self) -> None:
        """邀约被接受时触发。"""

    def on_invitation_rejected(self, reason: str) -> None:
        """邀约被拒绝时触发。"""

    def on_invitation_timeout(self) -> None:
        """确认阶段超时时触发。"""

    def on_invitation_retry(self, reason: str) -> None:
        """准备重试邀约时触发，可用于更换目标。"""

    @abstractmethod
    def execute_transfer(self) -> float:
        """执行转移，返回进度（0~1）。"""

    @abstractmethod
    def _is_transfer_done(self) -> bool:
        """判断任务是否完成。"""

    def on_success(self) -> None:
        """成功回调。"""

    def on_failure(self, reason: str) -> None:
        """失败回调。"""

    # ------- 工具方法 -------
    def _now(self) -> float:
        return float(getattr(self.env, "now", 0.0))

    def _deadline(self, timeout: float) -> Optional[float]:
        if timeout <= 0:
            return None
        return self._now() + timeout