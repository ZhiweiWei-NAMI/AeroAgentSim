from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class TransferItem(ABC):
    """
    统一的传输对象抽象。

    Attributes:
        item_id: 物品唯一标识。
        item_type: 物品类型（package / energy / data 等）。
        quantity: 物品数量或度量值。
        metadata: 物品附加属性（重量、优先级等）。
    """
    item_id: str
    item_type: str
    quantity: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @abstractmethod
    def describe(self) -> Dict[str, Any]:
        """返回当前物品的描述信息，便于日志与调试。"""


@dataclass
class MockTransferItem(TransferItem):
    """
    测试 / 本地开发用的占位实现。
    """

    def describe(self) -> Dict[str, Any]:
        return {
            "id": self.item_id,
            "type": self.item_type,
            "quantity": self.quantity,
            "metadata": self.metadata,
        }