"""Low-altitude scenario plugin hooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from aeroagentsim.plugin_api import ScenarioPlugin
from aeroagentsim.scenario.loader import UniqueLoader


def _operator_mode(document: dict[str, Any], base: Path, value: Any) -> None:
    operator = next(
        stream for stream in document["ingress_streams"] if stream["id"] == "operator"
    )
    operator["initial_watermark_ns"] = {
        "scheduled": document["run"]["until_ns"], "interactive": 0,
    }[value]
    if value == "interactive":
        # Omission is the ingress schema's explicit unlimited-wait policy;
        # null is not a valid numeric timeout. Stop remains available.
        operator.pop("timeout_s", None)


def _motion_backend(document: dict[str, Any], base: Path, value: Any) -> None:
    """Select the low-altitude motion owner; the scheduler kernel stays authoritative."""
    if value not in {"kinematic", "px4_gazebo"}:
        raise ValueError(f"knob motion_backend: unsupported backend {value!r}")
    motion = document.get("engines", {}).get("motion")
    if not isinstance(motion, dict):
        raise ValueError("knob motion_backend: lowalt requires a motion engine")
    if value == "kinematic":
        # The built-in kinematic scheduler engine computes flight motion
        # directly and therefore replaces Gazebo for high-speed simulation.
        motion["plugin"] = "kinematic"
        return
    from aeroagentsim.packs.traffic_accident.profiles import validate_profile

    profile_path = base / "profiles/px4.yaml"
    if not profile_path.is_file():
        raise ValueError(
            "knob motion_backend: px4_gazebo requires an external PX4/Gazebo "
            "service contract at profiles/px4.yaml; the low-altitude templates ship "
            "only the in-process kinematic scheduler engine / 飞行物理引擎后端 "
            "px4_gazebo 需要外部 PX4/Gazebo 服务契约（profiles/px4.yaml）；"
            "低空场景模板默认使用内置 kinematic 调度核心引擎"
        )
    profile = yaml.load(profile_path.read_bytes(), Loader=UniqueLoader)
    requirements = validate_profile(profile)
    zh_map = {
        "PX4 service URL/protocol capabilities": "PX4 服务地址与协议能力",
        "vehicle ID mapping and supported Gazebo model": "飞行器 ID 映射与支持的 Gazebo 模型",
        "explicit local/geodetic origin and vertical datum transform": "明确的本地/大地坐标原点与高程基准转换",
        "pose source clock/mapping + warmup offset": "位姿源时钟/映射与预热偏移",
        "boundary-observed velocity/battery canonical clock mapping": "边界观测速度/电量的标准时钟映射",
        "native command/result schemas and goto altitude datum": "原生命令/结果 Schema 与 goto 高度基准",
        "native planning-energy model or calibrated estimator with actual telemetry; never convert % blindly": "原生规划能耗模型或基于实测遥测的校准估算器（禁止盲目按百分比换算）",
    }
    # Fail closed: a configuration-contract-only profile must never look runnable.
    joined = "; ".join(requirements)
    joined_zh = "；".join(zh_map.get(item, item) for item in requirements)
    raise ValueError(
        "knob motion_backend: px4_gazebo requires external PX4/Gazebo service "
        f"configuration: {joined} / 飞行物理引擎后端 px4_gazebo 需要配置外部 "
        f"PX4/Gazebo 飞控仿真服务：{joined_zh}"
    )


def plugin() -> ScenarioPlugin:
    return ScenarioPlugin(
        name="lowalt",
        templates=(),
        apply_hooks={
            "lowalt.operator_mode": _operator_mode,
            "lowalt.motion_backend": _motion_backend,
        },
    )


__all__ = ["plugin"]
