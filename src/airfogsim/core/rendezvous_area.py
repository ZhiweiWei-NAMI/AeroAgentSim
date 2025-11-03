from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from airfogsim.core.transfer_item import TransferItem


@dataclass
class RendezvousArea(ABC):
    """
    会合区域抽象，负责到位检测、容量管理与中间缓冲。
    """

    area_id: str #区域唯一标识
    capacity: int = 1 #最大槽位容量（既用于占位，也用于缓冲容量的上限）
    geometry: Optional[Dict[str, float]] = None #可存几何描述
    allow_buffering: bool = True #是否允许把物品暂存到区域

    buffer: List[TransferItem] = field(default_factory=list, init=False) #物品临时存放列表（包裹型交接用）
    occupants: Set[str] = field(default_factory=set, init=False) #当前占位的参与者 agent_id 集

    # ------- 几何 / 到位判定 -------
    @abstractmethod #子类决定“到位”判定逻辑
    def is_inside(self, agent_id: str, position: Tuple[float, float, float]) -> bool:
        """判断代理是否位于会合区域内。"""

    def acquire_slot(self, agent_id: str) -> bool:
        """申请占位。"""
        if agent_id in self.occupants:
            return True
        if len(self.occupants) >= self.capacity:
            return False
        self.occupants.add(agent_id)
        return True

    def release_slot(self, agent_id: str) -> None:
        """释放占位。"""
        self.occupants.discard(agent_id)

    # ------- 物品缓冲 -------
    def can_place(self) -> bool:
        return not self.allow_buffering or len(self.buffer) < self.capacity

    def place_item(self, item: TransferItem, source_id: str) -> bool:
        if not self.allow_buffering:
            return True  # 能量类可直接透传
        if not self.can_place():
            return False
        self.buffer.append(item)
        return True

    def retrieve_item(self, item_id: str, target_id: str) -> Optional[TransferItem]:
        if not self.allow_buffering:
            return None  # 透传任务不需取出
        for idx, it in enumerate(self.buffer):
            if it.item_id == item_id:
                return self.buffer.pop(idx)
        return None

    # ------- 扩展钩子 -------
    def get_handover_position(self, agent_id: str) -> Optional[Tuple[float, float, float]]:
        """可选：提供推荐的交接位姿。"""
        return None

    def timeout_handler(self, now: float) -> None:
        """可选：处理缓冲区超时逻辑。"""


class DockingRendezvousArea(RendezvousArea):
    """
    支持停靠判定的会合区域，常用于充电。用于“需要稳定停靠”的场景
    """

    max_speed: float = 0.5  # m/s
    max_altitude_offset: float = 0.5  # m

    def is_inside(self, agent_id: str, position: Tuple[float, float, float]) -> bool:
        # 默认实现：永真，需要具体环境提供速度/高度信息时覆写
        return True

    def verify_docking(self, agent_state: Dict[str, float]) -> bool:
        speed = agent_state.get("speed", 0.0)
        alt = agent_state.get("position_z", 0.0)
        return speed <= self.max_speed and abs(alt - 0.0) <= self.max_altitude_offset


class MockRendezvousArea(RendezvousArea):
    """
    单元测试 / 开发用的简化实现，不做几何校验。
    """

    def is_inside(self, agent_id: str, position: Tuple[float, float, float]) -> bool:
        return True