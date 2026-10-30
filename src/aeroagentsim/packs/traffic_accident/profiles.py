"""Replacement contract validation; native readiness requires actual services."""

from __future__ import annotations

from typing import Any

from aeroagentsim.adapters.px4_gazebo import ACTIONS as PX4_ACTIONS
from aeroagentsim.adapters.sumo import ACTIONS as SUMO_ACTIONS

REQUIREMENT_LABELS_ZH = {
    "PX4 service URL/protocol capabilities": "PX4 服务地址与协议能力",
    "vehicle ID mapping and supported Gazebo model": "飞行器 ID 映射与支持的 Gazebo 模型",
    "explicit local/geodetic origin and vertical datum transform": "明确的本地/大地坐标原点与高程基准转换",
    "pose source clock/mapping + warmup offset": "位姿源时钟/映射与预热偏移",
    "boundary-observed velocity/battery canonical clock mapping": "边界观测速度/电量的标准时钟映射",
    "native command/result schemas and goto altitude datum": "原生命令/结果 Schema 与 goto 高度基准",
    "native planning-energy model or calibrated estimator with actual telemetry; never convert % blindly": "原生规划能耗模型或基于实测遥测的校准估算器（禁止盲目按百分比换算）",
}


def format_px4_requirements_error(requirements: tuple[str, ...]) -> str:
    joined = "; ".join(requirements)
    joined_zh = "；".join(REQUIREMENT_LABELS_ZH.get(item, item) for item in requirements)
    return (
        "knob motion_backend: px4_gazebo requires external PX4/Gazebo service "
        f"configuration: {joined} / 飞行物理引擎后端 px4_gazebo 需要配置外部 "
        f"PX4/Gazebo 飞控仿真服务：{joined_zh}"
    )


def validate_profile(profile: dict[str, Any]) -> tuple[str, ...]:
    """Return precise unresolved requirements, without certifying native execution."""
    if profile["format"] != "traffic-accident-replacement-profile/v1":
        raise ValueError("unsupported traffic replacement profile")
    if profile["switch_policy"] != "new_run_and_epoch":
        raise ValueError("kernel does not support hot writer transfer")
    if (
        type(profile["sampled_return_lag_ns"]) is not int
        or profile["sampled_return_lag_ns"] <= 0
    ):
        raise ValueError("sampled physical feedback needs an explicit positive lag")
    owned: set[str] = set()
    for owner, entry in profile["owners"].items():
        fields = entry["fields"]
        if len(set(fields)) != len(fields) or owned & set(fields):
            raise ValueError(f"profile ownership overlaps at {owner}")
        owned.update(fields)
        capabilities = profile["capabilities"].get(owner, [])
        native = {"sumo": SUMO_ACTIONS, "px4_gazebo": PX4_ACTIONS}
        if entry["plugin"] in native and not set(capabilities) <= set(
            native[entry["plugin"]]
        ):
            raise ValueError(f"profile advertises unsupported native commands: {owner}")
    if profile["id"] != "kinematic" and not profile["remove_owners"]:
        raise ValueError("native replacement must remove the previous physical owner")
    return tuple(profile["configuration_required"])
