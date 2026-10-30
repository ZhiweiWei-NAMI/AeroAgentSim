"""Traffic-accident scenario plugin hooks."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from aeroagentsim.plugin_api import ScenarioPlugin
from aeroagentsim.scenario.loader import UniqueLoader


def _accident_time(document: dict[str, Any], base: Path, value: Any) -> None:
    packages = document["behaviours"]
    package = packages[0]
    if "path" in package:
        package = yaml.load((base / package["path"]).read_bytes(), Loader=UniqueLoader)
    elif "document" in package:
        package = package["document"]
    package = copy.deepcopy(package)
    transitions = package["chains"]["traffic.incident_report"]["transitions"]
    action = next(
        action
        for transition in transitions
        if transition["id"] == "schedule"
        for action in transition["actions"]
        if action["id"] == "accident-time"
    )
    duration_ns = round(value * 1e9)
    if duration_ns >= document["run"]["until_ns"]:
        raise ValueError(
            "knob accident_time_s: accident must occur before the run horizon"
        )
    action["duration_ns"] = duration_ns
    document["behaviours"] = [package, *packages[1:]]


def _decision_mode(document: dict[str, Any], base: Path, value: Any) -> None:
    if value == "live":
        from aeroagentsim.authoring.demo import live_decisions

        live_decisions(document, {"profile": "live-llm"})
    else:
        document["engines"]["decisions"]["config"]["fixture_path"] = str(
            base / "fixtures/decisions.json"
        )


def _physics_hz(document: dict[str, Any], base: Path, value: Any) -> None:
    step_ns = round(1e9 / value)
    engines = document.get("engines", {})
    # The air-motion owner is selectable; never index a renamed/removed engine.
    for name in ("road_motion", "air_motion", "native_air"):
        engine = engines.get(name)
        if isinstance(engine, dict):
            config = engine.get("config")
            if isinstance(config, dict):
                config["step_ns"] = step_ns
    document["run"]["advance_ns"] = step_ns


def _motion_backend(document: dict[str, Any], base: Path, value: Any) -> None:
    """Select the air-motion owner; the scheduler kernel stays authoritative."""
    if value not in {"kinematic", "px4_gazebo"}:
        raise ValueError(f"knob motion_backend: unsupported backend {value!r}")
    engines = document["engines"]
    if value == "kinematic":
        # The built-in kinematic scheduler engine computes flight motion
        # directly and therefore replaces Gazebo for high-speed simulation.
        air = engines.get("air_motion")
        if not isinstance(air, dict):
            raise ValueError(
                "knob motion_backend: kinematic requires an air_motion engine"
            )
        air["plugin"] = "kinematic"
        engines.pop("native_air", None)
        return
    from aeroagentsim.packs.traffic_accident.profiles import validate_profile

    profile = yaml.load((base / "profiles/px4.yaml").read_bytes(), Loader=UniqueLoader)
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


def _wind_east(document: dict[str, Any], base: Path, value: Any) -> None:
    from aeroagentsim.packs.traffic_accident.knobs import set_wind

    set_wind(document, 0, value)


def _wind_north(document: dict[str, Any], base: Path, value: Any) -> None:
    from aeroagentsim.packs.traffic_accident.knobs import set_wind

    set_wind(document, 1, value)


def plugin() -> ScenarioPlugin:
    return ScenarioPlugin(
        name="traffic",
        templates=(),
        apply_hooks={
            "traffic.accident_time": _accident_time,
            "traffic.decision_mode": _decision_mode,
            "traffic.physics_hz": _physics_hz,
            "traffic.motion_backend": _motion_backend,
            "traffic.wind_east": _wind_east,
            "traffic.wind_north": _wind_north,
        },
    )


__all__ = ["plugin"]
