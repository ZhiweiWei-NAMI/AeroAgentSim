from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from airfogsim.core.rendezvous_area import RendezvousArea
from airfogsim.core.transfer_item import TransferItem


@dataclass
class TransferContext:
    item: TransferItem
    source_id: str
    target_id: str
    area: RendezvousArea
    phase: str = "LOCK_SOURCE" #内部阶段机当前阶段
    progress: float = 0.0
    started_at: Optional[float] = None #记录开始时间（用于指标）
    metadata: Optional[Dict] = None #扩展字段（可塞 task_id、优先级等）


class TransferManager:
    """
    通用传输调度器，负责将源代理的资源安全转移给目标代理。
    """

    def __init__(self, env):
        self.env = env
        self._contexts: Dict[str, TransferContext] = {}

    # ------- 公共 API -------
    def start_transfer(self, item: TransferItem, source_id: str, target_id: str, area: RendezvousArea) -> None:
        ctx = TransferContext(
            item=item,
            source_id=source_id,
            target_id=target_id,
            area=area,
            started_at=getattr(self.env, "now", 0.0),
        )
        self._contexts[item.item_id] = ctx
        self._emit("transfer_started", ctx)

    def update_transfer(self, item_id: str) -> None:
        ctx = self._contexts.get(item_id)
        if not ctx:
            return
        phase = ctx.phase

        if phase == "LOCK_SOURCE":
            ctx.phase = "RELEASE_SOURCE"
            self._emit("source_locked", ctx)

        elif phase == "RELEASE_SOURCE":
            placed = ctx.area.place_item(ctx.item, ctx.source_id)
            if not placed:
                return
            ctx.phase = "BUFFERED"
            self._emit("item_buffered", ctx)

        elif phase == "BUFFERED":
            ctx.phase = "LOCK_TARGET"
            self._emit("target_locking", ctx)

        elif phase == "LOCK_TARGET":
            retrieved = ctx.area.retrieve_item(ctx.item.item_id, ctx.target_id)
            if retrieved is None and ctx.area.allow_buffering:
                return
            ctx.phase = "COMMIT"

        elif phase == "COMMIT":
            ctx.progress = 1.0
            self._emit("transfer_committed", ctx)

    def is_finished(self, item_id: str) -> bool:
        ctx = self._contexts.get(item_id)
        return bool(ctx and ctx.phase == "COMMIT")

    def abort_transfer(self, item_id: str, reason: str) -> None:
        ctx = self._contexts.pop(item_id, None)
        if ctx:
            ctx.phase = "ABORTED"
            self._emit("transfer_failed", ctx, {"reason": reason})

    def get_context(self, item_id: str) -> Optional[TransferContext]:
        return self._contexts.get(item_id)

    # ------- 内部 -------
    def _emit(self, event_type: str, ctx: TransferContext, extra: Optional[Dict] = None) -> None:
        event = {
            "time": getattr(self.env, "now", 0.0),
            "task_id": ctx.metadata.get("task_id") if ctx.metadata else None,
            "item_id": ctx.item.item_id,
            "phase": ctx.phase,
            "type": event_type,
            "extra": extra or {},
        }
        event_bus = getattr(self.env, "event_bus", None)
        if event_bus and hasattr(event_bus, "publish"):
            event_bus.publish(event)