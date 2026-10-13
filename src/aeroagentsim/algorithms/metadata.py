"""Chinese presentation copy shared by shipped factories and catalog fallbacks.

Algorithm ids and configuration remain language-independent. Third-party
factories may supply their own ``label_zh`` and ``description_zh`` attributes.
"""

from __future__ import annotations

BUILTIN_COPY_ZH: dict[str, tuple[str, str]] = {
    "nearest": (
        "就近分配",
        "优先处理高优先级任务，选择最近且能保留安全电量完成任务的空闲无人机。",
    ),
    "auction": (
        "竞价分配",
        "无人机按预计完成时间和剩余电量报价，同一优先级内由最低报价者执行任务。",
    ),
    "hungarian": (
        "批量最优匹配",
        "批量匹配待处理任务与空闲无人机，降低总预计完成时间。",
    ),
    "llm": (
        "语言模型智能体",
        "通过可追溯的 LangGraph 决策插件分配任务；默认使用预设回复，可配置实时模型。",
    ),
    "straight": ("直线航线", "在起点和终点之间直接飞行，不主动绕开禁飞区。"),
    "corridor_astar": ("安全走廊航线", "用 A* 搜索障碍物拐点和走廊节点，绕开生效的禁飞区。"),
    "coverage": ("区域覆盖", "在设定区域内按固定间距逐行往返，覆盖作业范围。"),
    "survey": ("巡检航点", "依次访问设定的巡检航点，再飞往终点。"),
    "patrol": ("巡逻环线", "沿设定的巡逻环线完整飞行一圈。"),
    "altitude_layers": ("高度分层", "将存在间隔冲突的无人机调整到不同高度层。"),
    "hold_and_go": ("等待放行", "冲突双方按固定顺序，一架等待，另一架先行。"),
    "threshold_return": ("低电量返航", "电量达到阈值时，返回最近有空位的充电坪；充电坪已满时排队等候。"),
}
