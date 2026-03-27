import uuid
from pathlib import Path
from typing import Dict, Any, Callable, Optional, List, Tuple

import airfogsim.agent as agent_pkg
from airfogsim.agent import DroneAgent
from airfogsim.component import MoveToComponent, ChargingComponent
from airfogsim.workflow.inspection import create_inspection_workflow
from airfogsim.workflow.charging import create_charging_workflow
from airfogsim.workflow.image_processing import create_image_processing_workflow
from airfogsim.workflow.logistics import create_logistics_workflow

from .environment import PausableEnvironment
from .config import DEFAULT_AIRSPACE, DEFAULT_FREQUENCY, DEFAULT_LANDING_SPOT
from .registry_service import RegistryService
from .schemas import PreflightCheck, PreflightResult
from .type_utils import canonical_agent_display_type, is_drone_type, normalize_workflow_type, normalize_agent_type
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)
registry_service = RegistryService()


def _resolve_builtin_agent_class(requested_type: str):
    """Resolve a builtin agent class by name or normalized token.

    Returns the matching class or ``None`` if not found.
    """
    normalized = normalize_agent_type(requested_type)
    for cls in agent_pkg.get_all_agent_classes():
        if (
            cls.__name__ == requested_type
            or normalize_agent_type(cls.__name__) == normalized
        ):
            return cls
    # Final fallback: if the token looks like a drone, use DroneAgent
    if normalized == "drone":
        return DroneAgent
    return None


def _emit_log(log_event: Optional[Callable], source: str, message: str, level: str = "info") -> None:
    if log_event:
        log_event(source, message, level)


def _normalize_position(position: Any) -> List[float]:
    values = list(position or [0.0, 0.0, 0.0])
    while len(values) < 3:
        values.append(0.0)
    return [float(values[0]), float(values[1]), float(values[2])]


def _normalize_agent_properties(
    agent_config: Dict[str, Any],
    proxy_defaults: Optional[Dict[str, Any]] = None,
) -> Tuple[List[float], float, Dict[str, Any]]:
    position = _normalize_position(
        agent_config.get("position", agent_config.get("initial_position", [10.0, 10.0, 0.0]))
    )
    battery = float(agent_config.get("battery", agent_config.get("initial_battery", 100.0)))
    properties = {
        **(proxy_defaults or {}),
        **dict(agent_config.get("properties", {}) or {}),
    }
    properties.setdefault("position", position)
    properties.setdefault("battery_level", battery)
    properties.setdefault("altitude", position[2])
    properties.setdefault("status", "idle")
    properties.setdefault("moving_status", "idle")
    return position, battery, properties


def _validate_registry_proxies(config: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    for agent_config in config.get("agents", []) or []:
        definition_payload = agent_config.get("agent_definition")
        if not definition_payload:
            continue
        validation = registry_service.validate_definition("agents", definition_payload)
        errors.extend(validation.errors)
        warnings.extend(validation.warnings)
        if validation.errors:
            continue
        try:
            registry_service.compile_proxy(
                "agents",
                definition_payload["id"],
                version=definition_payload.get("version"),
                payload=definition_payload,
            )
        except Exception as exc:
            errors.append(f"Agent proxy {definition_payload.get('id')}: {exc}")

    for workflow_config in config.get("workflows", []) or []:
        for task_definition_payload in workflow_config.get("task_definitions", []) or []:
            validation = registry_service.validate_definition("tasks", task_definition_payload)
            errors.extend(validation.errors)
            warnings.extend(validation.warnings)
            if validation.errors:
                continue
            try:
                registry_service.compile_proxy(
                    "tasks",
                    task_definition_payload["id"],
                    version=task_definition_payload.get("version"),
                    payload=task_definition_payload,
                )
            except Exception as exc:
                errors.append(f"Task proxy {task_definition_payload.get('id')}: {exc}")

        workflow_definition_payload = workflow_config.get("workflow_definition")
        if not workflow_definition_payload:
            continue
        validation = registry_service.validate_definition("workflows", workflow_definition_payload)
        errors.extend(validation.errors)
        warnings.extend(validation.warnings)
        if validation.errors:
            continue
        try:
            registry_service.compile_proxy(
                "workflows",
                workflow_definition_payload["id"],
                version=workflow_definition_payload.get("version"),
                payload=workflow_definition_payload,
            )
        except Exception as exc:
            errors.append(f"Workflow proxy {workflow_definition_payload.get('id')}: {exc}")

    return errors, warnings


def setup_environment_resources(env: PausableEnvironment, config: Dict[str, Any], log_event: Callable = None) -> bool:
    """设置仿真环境中的资源
    
    Args:
        env: 仿真环境
        config: 环境配置
        log_event: 事件记录回调函数
    
    Returns:
        bool: 设置是否成功
    """
    if not env:
        logger.error("无法设置资源：环境未初始化")
        return False
    
    _emit_log(log_event, "ResourceManager", "开始配置仿真环境资源")
    
    # 设置空域资源
    setup_airspaces(env, config.get("airspaces", []), log_event)
    
    # 设置频率资源
    setup_frequencies(env, config.get("frequencies", []), log_event)
    
    # 设置着陆点资源
    setup_landing_spots(env, config.get("landing_spots", []), log_event)
    
    return True

def setup_airspaces(env: PausableEnvironment, airspaces: List[Dict[str, Any]], log_event: Callable = None) -> None:
    """设置空域资源"""
    create_airspace = getattr(env.airspace_manager, "create_airspace", None)
    if not callable(create_airspace):
        if airspaces:
            raise ValueError("AirspaceManager does not support create_airspace for configured airspaces.")
        _emit_log(
            log_event,
            "AirspaceManager",
            "当前运行时未暴露 create_airspace，跳过默认空域注入。",
            "warning",
        )
        return

    for idx, airspace_config in enumerate(airspaces):
        try:
            create_airspace(**airspace_config)
            _emit_log(
                log_event,
                "AirspaceManager",
                f"创建空域资源 {airspace_config.get('attributes', {}).get('name', f'空域{idx}')}",
            )
        except Exception as e:
            _emit_log(log_event, "AirspaceManager", f"创建空域资源失败: {str(e)}", "error")
    
    # 如果没有配置空域，添加默认空域
    if not airspaces:
        try:
            create_airspace(**DEFAULT_AIRSPACE)
            _emit_log(log_event, "AirspaceManager", "创建默认空域资源")
        except Exception as e:
            _emit_log(log_event, "AirspaceManager", f"创建默认空域资源失败: {str(e)}", "error")

def setup_frequencies(env: PausableEnvironment, frequencies: List[Dict[str, Any]], log_event: Callable = None) -> None:
    """设置频率资源"""
    create_frequency = getattr(env.frequency_manager, "create_frequency", None)
    if not callable(create_frequency):
        if frequencies:
            raise ValueError("FrequencyManager does not support create_frequency for configured frequencies.")
        _emit_log(
            log_event,
            "FrequencyManager",
            "当前运行时未暴露 create_frequency，跳过默认频率注入。",
            "warning",
        )
        return

    for idx, freq_config in enumerate(frequencies):
        try:
            create_frequency(**freq_config)
            _emit_log(
                log_event,
                "FrequencyManager",
                f"创建频率资源 {freq_config.get('attributes', {}).get('purpose', f'频率{idx}')}",
            )
        except Exception as e:
            _emit_log(log_event, "FrequencyManager", f"创建频率资源失败: {str(e)}", "error")
    
    # 如果没有配置频率，添加默认频率
    if not frequencies:
        try:
            create_frequency(**DEFAULT_FREQUENCY)
            _emit_log(log_event, "FrequencyManager", "创建默认频率资源")
        except Exception as e:
            _emit_log(log_event, "FrequencyManager", f"创建默认频率资源失败: {str(e)}", "error")

def setup_landing_spots(env: PausableEnvironment, landing_spots: List[Dict[str, Any]], log_event: Callable = None) -> None:
    """设置着陆点资源"""
    for idx, landing_config in enumerate(landing_spots):
        try:
            env.landing_manager.create_landing_spot(**landing_config)
            _emit_log(
                log_event,
                "LandingManager",
                f"创建着陆点资源 {landing_config.get('attributes', {}).get('name', f'着陆点{idx}')}",
            )
        except Exception as e:
            _emit_log(log_event, "LandingManager", f"创建着陆点资源失败: {str(e)}", "error")
    
    # 如果没有配置着陆点，添加默认着陆点
    if not landing_spots:
        try:
            # 使用配置文件中的默认着陆点设置
            env.landing_manager.create_landing_spot(**DEFAULT_LANDING_SPOT)
            _emit_log(log_event, "LandingManager", "创建默认着陆点资源")
        except Exception as e:
            _emit_log(log_event, "LandingManager", f"创建默认着陆点资源失败: {str(e)}", "error")


def preflight_runtime_config(config: Dict[str, Any]) -> PreflightResult:
    checks: List[PreflightCheck] = []
    errors: List[str] = []
    warnings: List[str] = []
    captured_logs: List[Dict[str, Any]] = []

    def log_event(source: str, message: str, level: str = "info", extra_data: Optional[Dict[str, Any]] = None):
        payload = {
            "source": source,
            "message": message,
            "level": level,
            **(extra_data or {}),
        }
        captured_logs.append(payload)
        prefixed = f"{source}: {message}"
        if level == "error":
            errors.append(prefixed)
        elif level == "warning":
            warnings.append(prefixed)

    env = PausableEnvironment(
        initial_time=0,
        visual_interval=config.get("visual_interval", 10),
    )
    env.set_speed(float(config.get("simulation_speed", 1.0) or 1.0))

    resources_errors_before = len(errors)
    try:
        setup_environment_resources(env, config, log_event)
        traffic = config.get("traffic", {}) or {}
        if traffic.get("source") == "sumo":
            sumo_config = traffic.get("sumo_config", {}) or {}
            config_file = sumo_config.get("config_file")
            if not config_file:
                errors.append("TrafficDataProvider: SUMO traffic is enabled but sumo_config.config_file is missing.")
            else:
                resolved_path = str(config_file)
                if resolved_path.startswith("static/"):
                    resolved_path = resolved_path.replace("static/", "frontend/public/", 1)
                if not Path(resolved_path).is_absolute():
                    resolved_path = str((Path.cwd() / resolved_path).resolve())
                if not Path(resolved_path).exists():
                    errors.append(f"TrafficDataProvider: SUMO config file not found: {resolved_path}")
    except Exception as exc:
        errors.append(f"ResourceManager: {exc}")
    resources_errors_after = len(errors)
    checks.append(
        PreflightCheck(
            name="resources",
            status="error" if resources_errors_after > resources_errors_before else ("warning" if warnings else "pass"),
            message=(
                "Runtime resources are ready."
                if resources_errors_after == resources_errors_before
                else "Runtime resources contain blocking errors."
            ),
            details={
                "warnings": list(warnings),
            },
        )
    )

    proxy_errors, proxy_warnings = _validate_registry_proxies(config)
    errors.extend(proxy_errors)
    warnings.extend(proxy_warnings)
    checks.append(
        PreflightCheck(
            name="registry_proxies",
            status="error" if proxy_errors else ("warning" if proxy_warnings else "pass"),
            message=(
                "Dynamic registry proxies compiled successfully."
                if not proxy_errors
                else "Dynamic registry proxies failed to compile."
            ),
            details={
                "errors": proxy_errors,
                "warnings": proxy_warnings,
            },
        )
    )

    created_agents = 0
    agent_errors_before = len(errors)
    created_agent_map: Dict[str, Any] = {}
    for agent_config in config.get("agents", []) or []:
        try:
            agent = create_agent_from_config(env, agent_config, log_event, None)
        except Exception as exc:
            errors.append(
                f"AgentManager: Failed to instantiate agent {agent_config.get('id') or agent_config.get('name') or 'unknown'}: {exc}"
            )
            continue
        if agent is None:
            errors.append(f"AgentManager: Failed to instantiate agent {agent_config.get('id') or agent_config.get('name') or 'unknown'}.")
            continue
        created_agent_map[agent.id] = agent
        created_agents += 1
    checks.append(
        PreflightCheck(
            name="agents",
            status="error" if len(errors) > agent_errors_before else "pass",
            message=f"Instantiated {created_agents} / {len(config.get('agents', []) or [])} agents.",
            details={
                "created_agents": list(created_agent_map.keys()),
            },
        )
    )

    created_workflows = 0
    workflow_errors_before = len(errors)
    created_workflow_ids: List[str] = []
    for workflow_config in config.get("workflows", []) or []:
        agent_id = workflow_config.get("agent_id")
        agent = created_agent_map.get(agent_id)
        if agent is None:
            errors.append(
                f"WorkflowManager: Missing instantiated agent {agent_id} for workflow {workflow_config.get('id') or workflow_config.get('name') or 'unknown'}."
            )
            continue
        try:
            workflow = create_workflow_from_config(env, workflow_config, agent, log_event, None)
        except Exception as exc:
            errors.append(
                f"WorkflowManager: Failed to instantiate workflow {workflow_config.get('id') or workflow_config.get('name') or 'unknown'}: {exc}"
            )
            continue
        if workflow is None:
            errors.append(
                f"WorkflowManager: Failed to instantiate workflow {workflow_config.get('id') or workflow_config.get('name') or 'unknown'}."
            )
            continue
        created_workflow_ids.append(workflow_config.get("id") or getattr(workflow, "id", workflow_config.get("name", "workflow")))
        created_workflows += 1
    checks.append(
        PreflightCheck(
            name="workflows",
            status="error" if len(errors) > workflow_errors_before else "pass",
            message=f"Instantiated {created_workflows} / {len(config.get('workflows', []) or [])} workflows.",
            details={
                "created_workflows": created_workflow_ids,
            },
        )
    )

    return PreflightResult(
        is_ready=not errors,
        errors=errors,
        warnings=warnings,
        checks=checks,
    )

def create_agent_from_config(env: PausableEnvironment, agent_config: Dict[str, Any], 
                             log_event: Callable = None, data_service = None) -> Any:
    """从配置创建智能体
    
    Args:
        env: 仿真环境
        agent_config: 智能体配置
        log_event: 事件记录回调函数
        data_service: 数据服务，用于更新数据库
    
    Returns:
        Any: 创建的智能体对象，失败则返回None
    """
    if not env:
        logger.error("无法创建智能体：环境未初始化")
        return None
    
    requested_agent_type = agent_config.get("type", "drone")
    agent_definition_payload = agent_config.get("agent_definition")
    registry_ref = agent_config.get("definition_ref")
    proxy_agent_class = None
    proxy_defaults = {}
    if agent_definition_payload:
        definition = registry_service.validate_definition("agents", agent_definition_payload)
        if not definition.is_valid:
            raise ValueError(f"Invalid custom agent definition: {definition.errors}")
        proxy_agent_class = registry_service.compile_proxy(
            "agents",
            agent_definition_payload["id"],
            version=agent_definition_payload.get("version"),
            payload=agent_definition_payload,
        )
        env.agent_manager.register_agent_class(proxy_agent_class)
        proxy_defaults = dict(agent_definition_payload.get("default_properties", {}))
        agent_type = proxy_agent_class.__name__
    else:
        agent_type = canonical_agent_display_type(requested_agent_type) or "DroneAgent"
    agent_id = agent_config.get("id") or f"agent_{uuid.uuid4().hex[:8]}"
    agent_name = agent_config.get("name", f"智能体{agent_id}")
    position, battery, normalized_properties = _normalize_agent_properties(
        agent_config,
        proxy_defaults=proxy_defaults,
    )
    
    # 记录智能体创建开始
    if log_event:
        log_event("AgentManager", f"开始创建{agent_type}类型智能体: {agent_name}")
    
    try:
        # Resolve agent class: custom proxy > lookup from registered classes > DroneAgent fallback
        if proxy_agent_class is not None:
            agent_class = proxy_agent_class
        else:
            agent_class = _resolve_builtin_agent_class(requested_agent_type)
            if agent_class is None:
                if log_event:
                    log_event(
                        "AgentManager",
                        f"不支持的智能体类型: {requested_agent_type}",
                        "error"
                    )
                return None

        agent = env.create_agent(
            agent_class,
            agent_name,
            agent_id=agent_id,
            properties=normalized_properties,
        )

        component_names = agent_config.get("components") or ["MoveToComponent", "ChargingComponent"]
        if not component_names:
            component_names = ["MoveToComponent", "ChargingComponent"]
        for component_name in component_names:
            component_class = env.component_manager.get_component_class(component_name)
            if not component_class:
                continue
            agent.add_component(component_class(env, agent))

        for key, value in normalized_properties.items():
            agent.properties.setdefault(key, value)
            if key not in agent.state and value is not None:
                try:
                    agent.update_state(key, value)
                except Exception:
                    pass

        # 如果提供了数据服务，更新数据库
        if data_service:
            data_service.update_drone_state(
                drone_id=agent_id,
                position=position,
                battery_level=battery,
                status="idle",
                speed=0.0,
                sim_time=env.now
            )
            data_service.update_agent(
                agent_id=agent_id,
                name=agent_name,
                type_=agent_type,
                position=position,
                properties={
                    "battery": battery,
                    "components": component_names,
                    "registry_ref": registry_ref,
                    **agent_config.get("properties", {})
                }
            )

        if log_event:
            log_event(
                "AgentManager",
                f"成功创建{agent_type}类型智能体: {agent_name}，位置: {position}"
            )

        return agent
        
    except Exception as e:
        # 记录创建失败
        if log_event:
            log_event(
                "AgentManager", 
                f"创建智能体失败: {str(e)}", 
                "error"
            )
    
    return None

def create_workflow_from_config(env: PausableEnvironment, workflow_config: Dict[str, Any], 
                                agent: Any, log_event: Callable = None, data_service = None) -> Any:
    """从配置创建工作流
    
    Args:
        env: 仿真环境
        workflow_config: 工作流配置
        agent: 关联的智能体
        log_event: 事件记录回调函数
        data_service: 数据服务，用于更新数据库
    
    Returns:
        Any: 创建的工作流对象，失败则返回None
    """
    if not env or not agent:
        logger.error("无法创建工作流：环境或智能体未初始化")
        return None
    
    workflow_type = normalize_workflow_type(workflow_config.get("type", "inspection"))
    workflow_id = workflow_config.get("id") or f"workflow_{uuid.uuid4().hex[:8]}"
    workflow_name = workflow_config.get("name", f"{workflow_type}工作流")
    properties = workflow_config.get("properties", {}) or workflow_config.get("details", {}) or {}
    workflow_definition_payload = workflow_config.get("workflow_definition")
    task_definition_payloads = workflow_config.get("task_definitions", [])
    
    # 记录工作流创建开始
    if log_event:
        log_event(
            "WorkflowManager", 
            f"开始为智能体 {agent.id} 创建{workflow_type}类型工作流: {workflow_name}"
        )
    
    try:
        if workflow_definition_payload:
            for task_definition_payload in task_definition_payloads:
                task_validation = registry_service.validate_definition("tasks", task_definition_payload)
                if not task_validation.is_valid:
                    raise ValueError(f"Invalid custom task definition: {task_validation.errors}")
                task_proxy_class = registry_service.compile_proxy(
                    "tasks",
                    task_definition_payload["id"],
                    version=task_definition_payload.get("version"),
                    payload=task_definition_payload,
                )
                env.task_manager.register_task_class(task_proxy_class)

            workflow_validation = registry_service.validate_definition("workflows", workflow_definition_payload)
            if not workflow_validation.is_valid:
                raise ValueError(f"Invalid custom workflow definition: {workflow_validation.errors}")
            workflow_class = registry_service.compile_proxy(
                "workflows",
                workflow_definition_payload["id"],
                version=workflow_definition_payload.get("version"),
                payload=workflow_definition_payload,
            )
            workflow = env.create_workflow(
                workflow_class,
                name=workflow_name,
                owner=agent,
                initial_status=workflow_definition_payload.get("start_state") or "idle",
                properties=properties,
            )
            workflow.id = workflow_id
            workflow.name = workflow_name

            if data_service:
                data_service.update_workflow(
                    workflow_id=workflow_id,
                    name=workflow_name,
                    type_=workflow_type,
                    agent_id=agent.id,
                    status="pending",
                    details={
                        "registry_ref": workflow_config.get("definition_ref"),
                        "properties": properties,
                    },
                )

            if log_event:
                log_event(
                    "WorkflowManager",
                    f"成功创建自定义工作流: {workflow_name}"
                )
            return workflow

        if workflow_type == "inspection":
            # 巡检路径工作流
            waypoints = (
                workflow_config.get("waypoints")
                or properties.get("inspection_points")
                or properties.get("waypoints")
                or []
            )
            if not waypoints:
                # 默认路径
                waypoints = [
                    (10, 10, 100),
                    (500, 500, 150),
                    (10, 10, 100),
                    (10, 10, 0)
                ]
                if log_event:
                    log_event("WorkflowManager", "使用默认巡检路径点")
            
            workflow = create_inspection_workflow(env, agent, waypoints)
            workflow.id = workflow_id
            workflow.name = workflow_name
            
            # 如果提供了数据服务，更新数据库
            if data_service:
                data_service.update_workflow(
                    workflow_id=workflow_id,
                    name=workflow_name,
                    type_=workflow_type,
                    agent_id=agent.id,
                    status="pending",
                    details={"waypoints": waypoints}
                )
            
            # 记录成功创建
            if log_event:
                log_event(
                    "WorkflowManager", 
                    f"成功创建巡检工作流: {workflow_name}，路径点数量: {len(waypoints)}"
                )
            
            return workflow
        
        elif workflow_type == "charging":
            # 充电工作流
            battery_threshold = workflow_config.get(
                "battery_threshold",
                properties.get("battery_threshold", 30),
            )
            target_charge_level = workflow_config.get(
                "target_charge_level",
                workflow_config.get("target_level", properties.get("target_charge_level", 90)),
            )
            
            # 查找最近的充电站
            position3d = agent.get_state('position')
            nearest_charging_station = env.landing_manager.find_nearest_landing_spot(
                x=position3d[0],
                y=position3d[1],
                require_charging=True
            )
            
            if nearest_charging_station:
                charging_station_location = nearest_charging_station.location
                if log_event:
                    log_event(
                        "WorkflowManager", 
                        f"找到充电站位置: {charging_station_location}"
                    )
                
                workflow = create_charging_workflow(
                    env=env,
                    agent=agent,
                    charging_station=charging_station_location,
                    battery_threshold=battery_threshold,
                    target_charge_level=target_charge_level
                )
                workflow.id = workflow_id
                workflow.name = workflow_name
                
                # 如果提供了数据服务，更新数据库
                if data_service:
                    data_service.update_workflow(
                        workflow_id=workflow_id,
                        name=workflow_name,
                        type_=workflow_type,
                        agent_id=agent.id,
                        status="pending",
                        details={
                            "battery_threshold": battery_threshold,
                            "target_charge_level": target_charge_level,
                            "charging_station": charging_station_location
                        }
                    )
                
                # 记录成功创建
                if log_event:
                    log_event(
                        "WorkflowManager", 
                        f"成功创建充电工作流: {workflow_name}，阈值: {battery_threshold}%，目标: {target_charge_level}%"
                    )
                
                return workflow
            else:
                if log_event:
                    log_event(
                        "WorkflowManager", 
                        "未找到可用的充电站，无法创建充电工作流", 
                        "error"
                    )
        elif workflow_type == "logistics":
            pickup_location = properties.get("pickup_location", workflow_config.get("pickup_location", [0, 0, 0]))
            delivery_location = properties.get("delivery_location", workflow_config.get("delivery_location", [0, 0, 0]))
            payloads = properties.get("payloads", workflow_config.get("payloads", []))
            source_agent_id = properties.get("source_agent_id", workflow_config.get("source_agent_id", agent.id))
            target_agent_id = properties.get("target_agent_id", workflow_config.get("target_agent_id", agent.id))

            workflow = create_logistics_workflow(
                env=env,
                agent=agent,
                pickup_location=pickup_location,
                delivery_location=delivery_location,
                payloads=payloads,
                source_agent_id=source_agent_id,
                target_agent_id=target_agent_id,
            )
            workflow.id = workflow_id
            workflow.name = workflow_name

            if data_service:
                data_service.update_workflow(
                    workflow_id=workflow_id,
                    name=workflow_name,
                    type_=workflow_type,
                    agent_id=agent.id,
                    status="pending",
                    details={
                        "pickup_location": pickup_location,
                        "delivery_location": delivery_location,
                        "payloads": payloads,
                        "source_agent_id": source_agent_id,
                        "target_agent_id": target_agent_id,
                    },
                )

            if log_event:
                log_event(
                    "WorkflowManager",
                    f"成功创建物流工作流: {workflow_name}"
                )

            return workflow
        elif workflow_type in {"imageprocessing", "image_processing"}:
            sensing_locations = properties.get("sensing_locations", workflow_config.get("sensing_locations", []))
            image_resolution = properties.get("image_resolution", workflow_config.get("image_resolution", "1920x1080"))
            image_format = properties.get("image_format", workflow_config.get("image_format", "jpeg"))

            workflow = create_image_processing_workflow(
                env=env,
                agent=agent,
                sensing_locations=sensing_locations,
                image_resolution=image_resolution,
                image_format=image_format,
            )
            workflow.id = workflow_id
            workflow.name = workflow_name

            if data_service:
                data_service.update_workflow(
                    workflow_id=workflow_id,
                    name=workflow_name,
                    type_=workflow_type,
                    agent_id=agent.id,
                    status="pending",
                    details={
                        "sensing_locations": sensing_locations,
                        "image_resolution": image_resolution,
                        "image_format": image_format,
                    },
                )

            if log_event:
                log_event(
                    "WorkflowManager",
                    f"成功创建环境图像处理工作流: {workflow_name}"
                )

            return workflow
        else:
            if log_event:
                log_event(
                    "WorkflowManager", 
                    f"不支持的工作流类型: {workflow_type}", 
                    "error"
                )
            
    except Exception as e:
        # 记录创建失败
        if log_event:
            log_event(
                "WorkflowManager", 
                f"创建工作流失败: {str(e)}", 
                "error"
            )
    
    return None
